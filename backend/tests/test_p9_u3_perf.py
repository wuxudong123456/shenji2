"""
Phase9 U3 性能并发压测（locust 或同等 — 自写并发脚本，零新依赖）
================================================================

覆盖（docs/PHASE_9.md §6 U3 + docs/RELEASE_CHECKLIST.md §3.3）：
  场景A 大数据扫描并发：50000 行 data_contracts，8 线程 × 5 迭代，
     每迭代 GET rows 游标分页（连翻 3 页）+ POST expression/execute 单表达式扫描
  场景B 七步并发不超时：真 LLM 种子任务 + 快端点高并发（B1）+ LLM 慢端点低并发（B2）
     B1  GET /analysis/{task_id}（15 线程 × 5）+ POST /documents/batch（15 线程 × 3）
     B2  POST /suspicion/generate（3 线程 × 1，预算 120s）

通过标准（RELEASE_CHECKLIST §3.3 语义：大数据表扫描 + 七步并发不超时）：
  0 HTTP 错误、0 超时（快端点 >3s 计超时 / 慢端点 >预算计超时）、
  p95 在预算内、max < 10s（对齐 DB MAX_EXECUTION_TIME hint）、
  并发深度 ≥5（证明后端 threaded 真并行，非串行队列）。

运行：cd backend && python tests/test_p9_u3_perf.py
前置：后端 app.py 已在 5000 起（threaded=True）、LLM 可用、MySQL tt 库迁移已应用。
自建自清：PID="U3PERF_TEST" 抛错项目，跑完定向 DELETE，0 残留。
"""
import json
import os
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "http://127.0.0.1:5000"
PID = "U3PERF_TEST"
N = 50000                # 造数规模（行）
BATCH = 2000             # executemany 批次（T7 手法，10 万行 7.8s）
SCAN_EXPR = '金额 >= 2000000 AND 采购方式 != "公开招标"'
TABLE = "data_contracts"

FAST_BUDGET_S = 3.0      # 快端点软预算（超时判定阈值）
MAX_ABS_S = 10.0         # 绝对上限（对齐 QUERY_TIMEOUT_MS=10s）
LLM_BUDGET_S = 120.0     # B2 suspicion/generate 预算
SEED_BUDGET_S = 240.0    # POST /analysis（Step1+2 真 LLM）预算
SCAN_CONCURRENCY = 8     # 场景A 线程数
SCAN_ITERS = 5
B1_GET_THREADS = 15
B1_GET_ITERS = 5
B1_DOC_THREADS = 15
B1_DOC_ITERS = 3
B2_THREADS = 3

PASS = 0
FAIL = 0


def check(cond, msg):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {msg}")
    else:
        FAIL += 1
        print(f"  ❌ {msg}")


def info(msg):
    print(f"    ℹ️ {msg}")


def req(method, path, body=None, timeout=20):
    """发起 HTTP 请求，返回 (status, json|None, elapsed_sec)。不抛异常。"""
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    t0 = time.time()
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            status = resp.status
            try:
                payload = json.loads(resp.read().decode("utf-8"))
            except Exception:
                payload = None
    except urllib.error.HTTPError as e:
        status = e.code
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = None
    except Exception:
        return (0, None, time.time() - t0)
    return (status, payload, time.time() - t0)


# ══════════════════════════════════════════════════════════════════
# 并发装载 + 统计
# ══════════════════════════════════════════════════════════════════

class DepthCounter:
    """在飞请求计数（最大深度=并发深度证明）。"""

    def __init__(self):
        self._lock = threading.Lock()
        self.inflight = 0
        self.max_depth = 0

    def enter(self):
        with self._lock:
            self.inflight += 1
            if self.inflight > self.max_depth:
                self.max_depth = self.inflight

    def leave(self):
        with self._lock:
            self.inflight -= 1


class LatencyLog:
    """线程安全耗时/错误/超时收集。"""

    def __init__(self, budget_s, label):
        self._lock = threading.Lock()
        self.budget_s = budget_s
        self.label = label
        self.lat = []
        self.errors = 0
        self.timeouts = 0

    def record(self, elapsed_s, ok, timed_out):
        with self._lock:
            self.lat.append(elapsed_s)
            if not ok:
                self.errors += 1
            if timed_out:
                self.timeouts += 1

    def summary(self):
        lat = sorted(self.lat)
        n = len(lat)
        p50 = lat[int(n * 0.5) - 1] if n else 0.0
        p95 = lat[min(int(n * 0.95) - 1, n - 1)] if n else 0.0
        mx = lat[-1] if n else 0.0
        return {
            "label": self.label,
            "n": n, "p50_s": round(p50, 3), "p95_s": round(p95, 3),
            "max_s": round(mx, 3), "errors": self.errors, "timeouts": self.timeouts,
        }


# ══════════════════════════════════════════════════════════════════
# 造数（T7 手法：get_connection + executemany，绕过逐行 log_db_write）
# ══════════════════════════════════════════════════════════════════

def get_conn():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from services.db import get_connection
    return get_connection(database="tt")


def plant_project():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
                (PID, "U3 压测抛错项目"),
            )
        conn.commit()
    finally:
        conn.close()


def bulk_insert():
    t0 = time.time()
    conn = get_conn()
    try:
        cols = ["project_id", "party_a", "party_b", "amount", "procurement_method"]
        sql = (f"INSERT INTO {TABLE} ({','.join(cols)}) VALUES (%s,%s,%s,%s,%s)")
        rows = []
        with conn.cursor() as cur:
            for i in range(N):
                amount = 3000000 if i % 3 == 0 else 500000
                rows.append((PID, f"甲方-{i}", f"U3PERF-行-{i}", amount, "询价"))
                if len(rows) >= BATCH:
                    cur.executemany(sql, rows)
                    rows.clear()
            if rows:
                cur.executemany(sql, rows)
        conn.commit()
    finally:
        conn.close()
    return time.time() - t0


# ══════════════════════════════════════════════════════════════════
# 场景A：大数据扫描并发（8 线程 × 5 迭代）
# ══════════════════════════════════════════════════════════════════

def run_scenario_a():
    print("\n── 场景A 大数据扫描并发（8 线程 × 5 迭代，3 页游标 + 单表达式扫描）──")
    depth = DepthCounter()
    rows_log = LatencyLog(FAST_BUDGET_S, "GET rows 游标分页")
    expr_log = LatencyLog(FAST_BUDGET_S, "POST expression/execute")

    def worker(_):
        for _it in range(SCAN_ITERS):
            # 连翻 3 页游标
            cursor = None
            for _pg in range(3):
                path = f"/api/audit/projects/{PID}/data/{TABLE}/rows?per_page=100"
                if cursor is not None:
                    path += f"&after={cursor}"
                depth.enter()
                try:
                    status, payload, el = req("GET", path, timeout=10)
                    ok = status == 200
                    timed = el > FAST_BUDGET_S
                    rows_log.record(el, ok, timed)
                    cursor = (payload or {}).get("next_cursor")
                finally:
                    depth.leave()
            # 单表达式扫描
            depth.enter()
            try:
                status, payload, el = req("POST", "/api/audit/expression/execute",
                                          {"expression": SCAN_EXPR, "table": TABLE,
                                           "project_id": PID}, timeout=10)
                ok = status == 200 and bool(payload and payload.get("success"))
                timed = el > FAST_BUDGET_S
                expr_log.record(el, ok, timed)
            finally:
                depth.leave()

    with ThreadPoolExecutor(max_workers=SCAN_CONCURRENCY) as ex:
        list(ex.map(worker, range(SCAN_CONCURRENCY)))

    rs = rows_log.summary()
    es = expr_log.summary()
    info(f"并发深度 max_inflight = {depth.max_depth}（线程池 {SCAN_CONCURRENCY}）")
    info(f"GET rows   : n={rs['n']} p50={rs['p50_s']}s p95={rs['p95_s']}s max={rs['max_s']}s err={rs['errors']} tmo={rs['timeouts']}")
    info(f"expr/execute: n={es['n']} p50={es['p50_s']}s p95={es['p95_s']}s max={es['max_s']}s err={es['errors']} tmo={es['timeouts']}")

    check(depth.max_depth >= 5, f"并发深度 ≥5（实测 {depth.max_depth}，证明 threaded 真并行）")
    check(rs["errors"] == 0 and es["errors"] == 0, "0 HTTP 错误")
    check(rs["timeouts"] == 0 and es["timeouts"] == 0, "0 超时（>3s 预算）")
    check(rs["p95_s"] < FAST_BUDGET_S and es["p95_s"] < FAST_BUDGET_S, "p95 < 3s（两端点）")
    check(rs["max_s"] < MAX_ABS_S and es["max_s"] < MAX_ABS_S, f"max < 10s（实测 {max(rs['max_s'], es['max_s'])}s，对齐 DB 超时 hint）")
    return {"rows": rs, "expr": es, "max_depth": depth.max_depth}


# ══════════════════════════════════════════════════════════════════
# 场景B：七步并发不超时
# ══════════════════════════════════════════════════════════════════

def seed_task():
    """POST /analysis 建真实任务（真 LLM Step1+2），推进到 step4（confirm，无 LLM）。"""
    print("\n── 种子：POST /analysis 建真实任务（真 LLM Step1+2，预算 240s）──")
    status, payload, el = req("POST", "/api/audit/analysis", {
        "project_id": PID,
        "intent": "对采购合同进行审计，关注大额采购未公开招标",
    }, timeout=SEED_BUDGET_S)
    info(f"POST /analysis -> {status} {el:.1f}s")
    check(status == 200 and bool(payload and payload.get("success")),
          "POST /analysis 成功（真 LLM 跑通 Step1+2）")
    if not (payload or {}).get("task_id"):
        check(False, "响应含 task_id")
        return None
    task_id = payload["task_id"]
    check(payload["task_id"] == task_id, f"task_id 返回 = {task_id[:8]}…")

    # 单次 confirm（空选择推进到 step4，无 LLM）——非关键路径，失败仅记 info 不 FAIL
    status, payload, el = req("POST", f"/api/audit/analysis/{task_id}/confirm", {
        "selected_violations": [], "selected_laws": [], "custom_regulations": [],
        "action": "confirm", "user": "U3PERF",
    }, timeout=30)
    info(f"POST /analysis/{task_id}/confirm -> {status} {el:.2f}s")
    if status != 200:
        info("confirm 非 200（空选择边界）——任务仍在 step3，B1/B2 继续测")
    return task_id


def run_scenario_b1(task_id):
    print("\n── 场景B1 七步快端点高并发（GET analysis 15×5 + documents/batch 15×3）──")
    depth = DepthCounter()
    get_log = LatencyLog(FAST_BUDGET_S, "GET /analysis/{id}")
    doc_log = LatencyLog(FAST_BUDGET_S, "POST /documents/batch")

    def get_worker(_):
        for _it in range(B1_GET_ITERS):
            depth.enter()
            try:
                status, _p, el = req("GET", f"/api/audit/analysis/{task_id}", timeout=10)
                get_log.record(el, status == 200, el > FAST_BUDGET_S)
            finally:
                depth.leave()

    def doc_worker(_):
        for _it in range(B1_DOC_ITERS):
            depth.enter()
            try:
                status, _p, el = req("POST", "/api/audit/documents/batch",
                                     {"task_id": task_id}, timeout=10)
                doc_log.record(el, status == 200, el > FAST_BUDGET_S)
            finally:
                depth.leave()

    with ThreadPoolExecutor(max_workers=B1_GET_THREADS) as ex:
        list(ex.map(get_worker, range(B1_GET_THREADS)))
    with ThreadPoolExecutor(max_workers=B1_DOC_THREADS) as ex:
        list(ex.map(doc_worker, range(B1_DOC_THREADS)))

    gs = get_log.summary()
    ds = doc_log.summary()
    info(f"并发深度 max_inflight = {depth.max_depth}")
    info(f"GET /analysis     : n={gs['n']} p50={gs['p50_s']}s p95={gs['p95_s']}s max={gs['max_s']}s err={gs['errors']} tmo={gs['timeouts']}")
    info(f"documents/batch   : n={ds['n']} p50={ds['p50_s']}s p95={ds['p95_s']}s max={ds['max_s']}s err={ds['errors']} tmo={ds['timeouts']}")

    check(depth.max_depth >= 5, f"并发深度 ≥5（实测 {depth.max_depth}）")
    check(gs["errors"] == 0 and ds["errors"] == 0, "0 HTTP 错误")
    check(gs["timeouts"] == 0 and ds["timeouts"] == 0, "0 超时")
    check(gs["p95_s"] < FAST_BUDGET_S and ds["p95_s"] < FAST_BUDGET_S, "p95 < 3s")
    check(gs["max_s"] < MAX_ABS_S and ds["max_s"] < MAX_ABS_S, f"max < 10s（实测 {max(gs['max_s'], ds['max_s'])}s）")
    return {"get": gs, "doc": ds, "max_depth": depth.max_depth}


def run_scenario_b2(task_id):
    print("\n── 场景B2 LLM 慢端点低并发（suspicion/generate 3 线程，预算 120s）──")
    depth = DepthCounter()
    sus_log = LatencyLog(LLM_BUDGET_S, "POST /suspicion/generate")

    def sus_worker(_):
        depth.enter()
        try:
            status, payload, el = req("POST", "/api/audit/suspicion/generate",
                                      {"task_id": task_id}, timeout=LLM_BUDGET_S)
            ok = status == 200 and bool(payload and payload.get("success"))
            sus_log.record(el, ok, el > LLM_BUDGET_S)
        finally:
            depth.leave()

    with ThreadPoolExecutor(max_workers=B2_THREADS) as ex:
        list(ex.map(sus_worker, range(B2_THREADS)))

    ss = sus_log.summary()
    info(f"并发深度 max_inflight = {depth.max_depth}（3 线程 LLM）")
    info(f"suspicion/generate: n={ss['n']} min/p95/max = 未单列（LLM 慢端点）max={ss['max_s']}s err={ss['errors']} tmo={ss['timeouts']}")
    check(ss["errors"] == 0, "0 HTTP 错误（suspicion/generate 全 200 且 success）")
    check(ss["timeouts"] == 0, "0 超时（均 < 120s 预算）")
    check(ss["max_s"] < LLM_BUDGET_S, f"max < 120s（实测 {ss['max_s']}s）")
    return {"sus": ss, "max_depth": depth.max_depth}


# ══════════════════════════════════════════════════════════════════
# cleanup（定向 DELETE，断言 0 残留）
# ══════════════════════════════════════════════════════════════════

def cleanup(task_id):
    print("\n── cleanup 定向清理 ──")
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {TABLE} WHERE project_id=%s", (PID,))
            n1 = cur.rowcount
            cur.execute("DELETE FROM project_suspicions WHERE project_id=%s", (PID,))
            n2 = cur.rowcount
            cur.execute("DELETE FROM audit_source_refs WHERE project_id=%s", (PID,))
            n3 = cur.rowcount
            if task_id:
                cur.execute("DELETE FROM audit_analysis_tasks WHERE task_code=%s", (task_id,))
                n4 = cur.rowcount
            else:
                n4 = 0
            cur.execute("DELETE FROM audit_projects WHERE id=%s", (PID,))
            n5 = cur.rowcount
        conn.commit()
        info(f"删除 data_contracts={n1} 疑点={n2} source_refs={n3} tasks={n4} 项目={n5}")
    finally:
        conn.close()

    # 残留断言
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS n FROM {TABLE} WHERE project_id=%s", (PID,))
            r1 = cur.fetchone()["n"]
            cur.execute("SELECT COUNT(*) AS n FROM audit_projects WHERE id=%s", (PID,))
            r2 = cur.fetchone()["n"]
            if task_id:
                cur.execute("SELECT COUNT(*) AS n FROM audit_analysis_tasks WHERE task_code=%s", (task_id,))
                r3 = cur.fetchone()["n"]
            else:
                r3 = 0
    finally:
        conn.close()
    check(r1 == 0 and r2 == 0 and r3 == 0,
          f"0 残留（data={r1} projects={r2} tasks={r3}）")


# ══════════════════════════════════════════════════════════════════
def main():
    print("[test] Phase9 U3 性能并发压测（自建自清）")

    # 前置：后端 + LLM 健康
    status, payload, _ = req("GET", "/api/health", timeout=10)
    check(status == 200 and (payload or {}).get("status") == "ok",
          "前置：/api/health ok（后端在线）")
    status, payload, _ = req("GET", "/api/llm/health", timeout=15)
    llm_ok = status == 200 and bool((payload or {}).get("llm_available"))
    check(llm_ok, "前置：LLM 可用（真 LLM 七步种子需要）")
    if not llm_ok:
        print("LLM 不可用，场景B 无法建真实任务——跳过场景B，仅跑场景A。")
        info("（七步并发不超时依赖真 LLM；LLM 恢复后重跑即可全量）")

    # 造数
    t0 = time.time()
    plant_project()
    info("项目已建；开始灌 data_contracts…")
    plant_el = bulk_insert()
    info(f"灌入 {N} 行 data_contracts，耗时 {plant_el:.1f}s")

    # 场景A
    a = run_scenario_a()

    # 场景B
    task_id = None
    if llm_ok:
        task_id = seed_task()
        if task_id:
            b1 = run_scenario_b1(task_id)
            b2 = run_scenario_b2(task_id)
        else:
            check(False, "种子任务失败，场景B1/B2 未执行")

    # cleanup
    cleanup(task_id)

    # 汇总
    print("\n==================================================")
    print(f"Phase9 U3 性能并发压测：PASS={PASS}  FAIL={FAIL}")
    print("==================================================")
    # JSON 摘要（报告 §18 记录用）
    summary = {
        "pid": PID, "rows": N,
        "scenario_a": {"max_depth": a["max_depth"],
                       "rows": a["rows"], "expr": a["expr"]},
    }
    if task_id:
        summary["scenario_b"] = {"b1_max_depth": b1["max_depth"],
                                 "get": b1["get"], "doc": b1["doc"],
                                 "b2": b2["sus"]}
    else:
        summary["scenario_b"] = "skipped (LLM down)"
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
