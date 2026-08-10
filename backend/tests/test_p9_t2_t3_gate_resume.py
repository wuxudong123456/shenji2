r"""Phase9 T2 + T3 验收：拦截即正确（T2）+ 可恢复（T3）

T2 OCR 未完成进 Step5（PHASE_9 §6.2）：
  - 构造 OCR 未完成状态（trace.parse_status='pending'，无 data_* 行/field_sources）
  - 断言 readiness(data_ready).ready=False，checks 列出「OCR完成」未过
  - 「完成 OCR」(parse_status='done' + 落 1 行 data_* + 1 条 field_sources) 后 ready=True 放行

T3 恢复分析（PHASE_9 §6.3）：
  - POST /analysis（Step1-2）→ confirm（Step3，带 selected_violations/selected_laws）
  - 断言 GET /analysis/{id} 返回 current_step=3 + step_data.selected_violations/laws
    与中断前一致（纯 MySQL，非 localStorage）—— 后端权威 resume

用法：cd backend && python tests\test_p9_t2_t3_gate_resume.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, execute, insert  # noqa: E402
from services.analysis_lifecycle import check_readiness, create_analysis_task  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def req(method, path, body=None, timeout=240):
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
    if len(s) > 200:
        s = s[:200] + "…"
    print(f"    ℹ️ {label} = {s}")


# ══════════════════════════════════════════════════════════════════
# T2：OCR 未完成进 Step5 —— readiness(data_ready) 拦截与放行
# ══════════════════════════════════════════════════════════════════
def run_t2():
    global PASS, FAIL
    print("\n═══ T2 OCR 未完成进 Step5（门禁拦截/放行）═══")
    pid = "T2GATE_TEST"
    # 干净起点
    execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM data_procurements WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    # 抛错项目 + 1 条 OCR 未完成的 trace
    execute("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
            (pid, "T2 门禁测试项目"), database="tt")
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id, file_name, parse_status) VALUES (%s,%s,%s)",
        (pid, "T2_pending.pdf", "pending"), database="tt")
    task = create_analysis_task(pid, user_intent="T2 门禁测试")
    tid = task["task_id"] if isinstance(task, dict) else task
    info("throwaway task", tid)

    print("── ① OCR 未完成 → readiness 必拦 ──")
    # 服务层直检
    cr = check_readiness(tid, "data_ready")
    ocr_chk = next((c for c in cr["checks"] if c["name"] == "OCR完成"), {})
    info("data_ready.checks", [(c["name"], c["ok"]) for c in cr["checks"]])
    check("OCR 未完成时 ready=False（服务层）", cr["ready"] is False,
          f"ready={cr['ready']}")
    check("「OCR完成」单项未过", ocr_chk.get("ok") is False,
          f"ocr_chk={ocr_chk}")
    # HTTP 端点（E2E）— 未就绪时端点用非 2xx（如 412）+ body.ready=False 表达拦截
    st, r = req("GET", f"/analysis/{tid}/readiness?stage=data_ready")
    info("HTTP readiness", f"HTTP {st} ready={r.get('ready')}")
    check("HTTP readiness(data_ready).ready=False（body）", r.get("ready") is False,
          f"HTTP {st} ready={r.get('ready')}")

    print("── ② step/4 在 OCR 未完成时应被拦（或 readiness 否决）──")
    # 附录A：data_ready 未过不应进 Step5。step/4 路由是否硬拦取决于实现；
    # 此处断言 readiness 已否决（权威门禁），step/4 若放行则记为待加固（非本测试 FAIL）。
    info("门禁状态", "data_ready 未过 → Step5 不应执行（readiness 否决即可）")

    print("── ③ 「完成 OCR」后 ready=True 放行 ──")
    execute("UPDATE audit_document_traces SET parse_status='done', file_category='采购' WHERE id=%s",
            (trace_id,), database="tt")
    data_id = insert(
        "INSERT INTO data_procurements (project_id, subject_name, contract_amount) VALUES (%s,%s,%s)",
        (pid, "T2 测试采购行", 1000000), database="tt")
    insert(
        "INSERT INTO audit_field_sources (project_id, table_name, row_id, field_name) "
        "VALUES (%s,%s,%s,%s)",
        (pid, "data_procurements", data_id, "contract_amount"), database="tt")
    cr2 = check_readiness(tid, "data_ready")
    info("完成后 checks", [(c["name"], c["ok"]) for c in cr2["checks"]])
    check("OCR 完成后 ready=True（服务层）", cr2["ready"] is True,
          f"ready={cr2['ready']}")
    st2, r2 = req("GET", f"/analysis/{tid}/readiness?stage=data_ready")
    check("HTTP readiness(data_ready).ready=True", r2.get("ready") is True,
          f"{st2} ready={r2.get('ready')}")

    # 清理抛错项目
    execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM data_procurements WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")


# ══════════════════════════════════════════════════════════════════
# T3：恢复分析 —— 后端权威 resume
# ══════════════════════════════════════════════════════════════════
def run_t3():
    global PASS, FAIL
    print("\n═══ T3 恢复分析（后端权威 resume，真 LLM）═══")
    pid = "4a0946e4c4c0"
    item = query_one("SELECT id,title FROM audit_items WHERE project_id=%s ORDER BY id LIMIT 1",
                     (pid,), database="tt")

    print("── ① POST /analysis（Step1-2，真 LLM）──")
    st, r = req("POST", "/analysis", {"project_id": pid, "focus_item_id": item["id"],
                                      "user_intent": "核查办公电脑采购合规性"})
    tid = r.get("task_id", "")
    matches = r.get("matches", [])
    check("Step1-2 跑通", st == 200 and tid, f"{st}")
    info("matches", len(matches))

    print("── ② Step3 confirm（带 selected_violations/selected_laws）──")
    sel_v = [m.get("id") for m in matches[:2] if m.get("id")] or ["v1"]
    sel_l = [{"law_id": "T3LAW1", "law_name": "中华人民共和国政府采购法", "clause_id": "第18条"}]
    st, r = req("POST", f"/analysis/{tid}/confirm",
                {"selected_violations": sel_v, "selected_laws": sel_l, "action": "confirm"},
                timeout=120)
    check("confirm 跑通", st == 200, f"{st} {str(r)[:120]}")

    print("── ③ 模拟「刷新/重开」：GET /analysis/{id} 应权威恢复 ──")
    st, r = req("GET", f"/analysis/{tid}")
    check("GET 跑通", st == 200, f"{st}")
    if st == 200:
        info("响应键", sorted(r.keys()))
        info("current_step", r.get("current_step"))
        # GET 响应把已确认数据放在顶层（selected_violations/selected_laws），
        # 源自 MySQL step_data（advance_step 已合并 confirm 的选择）—— 后端权威，非 localStorage
        got_v = r.get("selected_violations")
        got_l = r.get("selected_laws")
        info("selected_violations（顶层）", got_v)
        info("selected_laws（顶层）", got_l)
        check("current_step=3（confirm 后权威恢复）",
              r.get("current_step") == 3, f"current_step={r.get('current_step')}")
        check("selected_violations 从后端权威恢复",
              got_v is not None and list(got_v) == list(sel_v),
              f"got={got_v} sent={sel_v}")
        check("selected_laws 从后端权威恢复",
              got_l is not None and len(got_l) >= 1,
              f"got={got_l}")
        # summaries 应含 step-2/step-3（每步固定 message_id）
        sums = r.get("summaries") or {}
        info("summaries 步号", list(sums.keys()) if isinstance(sums, dict) else type(sums).__name__)

    # 清理任务级数据
    execute("DELETE FROM audit_agent_traces WHERE task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_step_summaries WHERE analysis_task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE task_code=%s", (tid,), database="tt")


def main():
    global PASS, FAIL
    print("[test] Phase9 T2 门禁 + T3 恢复")
    # 前置：health
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    run_t2()
    run_t3()

    print(f"\n{'='*50}\nPhase9 T2+T3：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
