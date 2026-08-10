r"""Phase9 T7 大数据表扫描验收

§6.7：data_* 灌入大批量行（如 10 万+），跑 Step5 + 数据工坊查询；
      断言：游标分页 + 超时保护生效，不超时（Phase 5 P5-6）。

机制（代码核查，faithful-mode）：
  ✅ 超时保护：data_service.list_rows 的 SELECT/COUNT 带 `/*+ MAX_EXECUTION_TIME(10000) */`
     hint（data_service.py:50 QUERY_TIMEOUT_MS=10000 / :201 hint），MySQL 超 10s 自杀查询。
  ✅ 游标分页：list_rows(after=<id>) → `WHERE id<%s ORDER BY id DESC LIMIT per_page`
     （:193-196），避开 OFFSET 越翻越慢；next_cursor=满页末行 id（:239）。路由透传
     after/per_page（audit_routes.py:1248-1254）。
  ✅ Step5 扫描行级有 LIMIT cap：expression_engine._execute_row `SELECT * FROM {table}
     WHERE project_id=%s LIMIT %s`（:292-295，默认 limit=2000），大表扫描不爆。
  ✅ 隔离索引：data_contracts INDEX idx_project(project_id)（schema.sql:357），
     WHERE project_id=%s 走索引，10 万级单项目查询快。

本测试（N=100000 行，抛错项目 T7LARGE_TEST 全程自建自清）：
  ① 大表查询不超时：HTTP GET rows per_page=100 → 200/rows=100/total=100000，计时 < 阈值。
  ② 游标分页深翻页：after=next_cursor 连翻 5+ 页，每页 100 行、游标推进；深页计时快
     （cursor WHERE id<%s 走 PK 索引，无 OFFSET 全表扫）。
  ③ Step5 扫描大表完成：execute_expression on 10 万行 → success，hits>0（LIMIT 2000 cap）。

用法：cd backend && python tests\test_p9_t7_large_scan.py
（首次跑含 10 万行批量插入，约 30-60s；可改 N 调规模）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import get_connection, execute, insert, query_one  # noqa: E402
from services.expression_engine import execute_expression  # noqa: E402

BASE = "http://127.0.0.1:5000"
N = 100000              # 灌入行数（spec「10 万+」）
BATCH = 2000            # executemany 批大小
TIMEOUT_BUDGET_MS = 3000   # 单次查询软预算（远 < MAX_EXECUTION_TIME=10s 即证明不超时）
PASS = 0
FAIL = 0


def req(method, path, body=None, timeout=120):
    url = f"{BASE}/api/audit{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json;charset=utf-8"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"_exc": str(e)[:300]}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def info(label, val):
    s = json.dumps(val, ensure_ascii=False, default=str)
    if len(s) > 220:
        s = s[:220] + "…"
    print(f"    ℹ️ {label} = {s}")


def bulk_insert(pid):
    """批量插入 N 行 data_contracts（get_connection + executemany，绕开逐行 log 开销）。
    金额分布：i%3==0 → 300万 询价（命中 ≥200万阈值），其余 → 50万 询价（不命中）。"""
    sql = ("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
           "VALUES (%s,%s,%s,%s)")
    conn = get_connection(database="tt")
    inserted = 0
    t0 = time.perf_counter()
    try:
        with conn.cursor() as cur:
            batch = []
            for i in range(N):
                amt = 3000000 if i % 3 == 0 else 500000
                batch.append((pid, f"T7-行-{i}", amt, "询价"))
                if len(batch) >= BATCH:
                    cur.executemany(sql, batch)
                    conn.commit()
                    inserted += len(batch)
                    batch = []
                    if inserted % 20000 == 0:
                        print(f"    … 已插入 {inserted}/{N}（{time.perf_counter()-t0:.1f}s）")
            if batch:
                cur.executemany(sql, batch)
                conn.commit()
                inserted += len(batch)
    finally:
        conn.close()
    return inserted, time.perf_counter() - t0


def main():
    global PASS, FAIL
    print(f"[test] Phase9 T7 大数据表扫描（N={N}）\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    pid = "T7LARGE_TEST"
    # 干净起点
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (pid, "T7 大数据扫描测试"), database="tt")

    # ── 灌入 N 行 ──
    print(f"── 灌入 {N} 行 data_contracts（批量 executemany）──")
    cnt, ins_s = bulk_insert(pid)
    info("插入完成", f"{cnt} 行 / {ins_s:.1f}s")
    check(f"成功灌入 {N} 行", cnt == N, f"cnt={cnt}")
    real_total = query_one("SELECT COUNT(*) AS n FROM data_contracts WHERE project_id=%s",
                           (pid,), database="tt")
    info("DB 直查 total", real_total)
    check("DB 行数 = N", real_total and real_total["n"] == N, str(real_total))

    # ══════════════════════════════════════════════════════════════════
    # ① 大表查询不超时（HTTP + MAX_EXECUTION_TIME hint）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ① 大表查询不超时（per_page=100）──")
    t0 = time.perf_counter()
    st, r1 = req("GET", f"/projects/{pid}/data/data_contracts/rows?per_page=100")
    ms1 = (time.perf_counter() - t0) * 1000
    info("首页响应", f"{st} rows={len(r1.get('rows', [])) if st == 200 else 'err'} "
                    f"total={r1.get('total')} next_cursor={r1.get('next_cursor')} ({ms1:.0f}ms)")
    check("① 首页 200", st == 200, f"{st}")
    check("① 首页 rows=100（per_page 生效）", st == 200 and len(r1.get("rows", [])) == 100,
          f"rows={len(r1.get('rows', [])) if st == 200 else 'err'}")
    check("① total=100000（全量计数正确）", r1.get("total") == N, r1.get("total"))
    check(f"① 首页查询 {ms1:.0f}ms < {TIMEOUT_BUDGET_MS}ms（远未触 10s 超时）",
          ms1 < TIMEOUT_BUDGET_MS, f"{ms1:.0f}ms")
    check("① 首页满页 → next_cursor 非空（可切入游标翻页）",
          r1.get("next_cursor") is not None, r1.get("next_cursor"))

    # ══════════════════════════════════════════════════════════════════
    # ② 游标分页深翻页（cursor WHERE id<%s，避开 OFFSET 越翻越慢）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ② 游标分页深翻页（after=next_cursor 连翻）──")
    cursor = r1.get("next_cursor")
    cursors_seen = set()
    pages_ok = 0
    deep_ms = None
    for p in range(2, 8):  # 翻到第 7 页（~700 行深处）
        if cursor is None:
            break
        cursors_seen.add(cursor)
        t_p = time.perf_counter()
        st, rp = req("GET", f"/projects/{pid}/data/data_contracts/rows?per_page=100&after={cursor}")
        ms_p = (time.perf_counter() - t_p) * 1000
        if st != 200 or len(rp.get("rows", [])) == 0:
            info(f"第{p}页", f"{st} rows={len(rp.get('rows', [])) if st == 200 else 'err'}"); break
        # 游标推进：新页所有 id < cursor（ORDER BY id DESC）
        new_ids = [row.get("id") for row in rp.get("rows", [])]
        advancing = all(i < cursor for i in new_ids if i is not None)
        check(f"② 第{p}页 100 行且 id < 游标（游标推进正确）",
              len(new_ids) == 100 and advancing, f"min_id={min(new_ids) if new_ids else None} vs cursor={cursor}")
        pages_ok += 1
        deep_ms = ms_p
        cursor = rp.get("next_cursor")
    check("② 游标连翻 ≥5 页", pages_ok >= 5, f"pages_ok={pages_ok}")
    check(f"② 深页查询 {deep_ms:.0f}ms < {TIMEOUT_BUDGET_MS}ms（cursor 走 PK 索引不超时）",
          deep_ms is not None and deep_ms < TIMEOUT_BUDGET_MS, f"{deep_ms}ms")
    # 游标每页都不同（未停滞）
    check("② 每页游标推进（无停滞）", len(cursors_seen) >= 5, f"distinct cursors={len(cursors_seen)}")

    # ══════════════════════════════════════════════════════════════════
    # ③ Step5 扫描大表完成（LIMIT 2000 cap，不爆不超时）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ③ Step5 扫描大表（execute_expression，行级 LIMIT 2000 cap）──")
    t0 = time.perf_counter()
    scan = execute_expression('金额 >= 2000000 AND 采购方式 != "公开招标"',
                              "data_contracts", pid)
    ms_scan = (time.perf_counter() - t0) * 1000
    info("Step5 扫描", {"success": scan.get("success"), "total(扫)": scan.get("total"),
                        "hits": scan.get("hits"), "ms": round(ms_scan)})
    check("③ Step5 扫描 success（大表不崩）", scan.get("success") is True, str(scan)[:120])
    check("③ Step5 行级 LIMIT 2000 cap 生效（total(扫) ≤ 2000）",
          (scan.get("total") or 0) <= 2000, f"total={scan.get('total')}")
    check("③ Step5 命中疑点行（300万 询价，hits>0）", (scan.get("hits") or 0) > 0,
          f"hits={scan.get('hits')}")
    check(f"③ Step5 扫描 {ms_scan:.0f}ms < {TIMEOUT_BUDGET_MS}ms（不超时）",
          ms_scan < TIMEOUT_BUDGET_MS, f"{ms_scan}ms")
    info("③ 设计说明",
         "expression_engine._execute_row 行级 LIMIT cap=2000（默认）使大表扫描有界；"
         "data_service.list_rows 走 MAX_EXECUTION_TIME(10s) hint + 游标分页——双保护。")

    # 清理（10 万行 + 项目）
    t0 = time.perf_counter()
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    info("清理耗时", f"{time.perf_counter()-t0:.1f}s")

    print(f"\n{'='*50}\nPhase9 T7 大数据表扫描：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
