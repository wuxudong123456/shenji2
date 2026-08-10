r"""Phase8 契约层端到端验收：七步分析引擎契约（附录A v1）

验证对象 = 契约层（M008 三表 + readiness 三道 + current_step 权威推进 + 疑点五态 +
trace 落库 + 文书证据继承 + 固定 message_id）。**不依赖 LLM**——直接走 service/
路由的 DB 契约，LLM 驱动的 graph 端到端属 P8-12 质量评测范畴。

需 backend 运行 + M008 已 migrate。隔离 fixture（p8e2e_*），结束清理。

用法：cd backend && python tests\test_p8_seven_step.py [BASE_URL]
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, query_one, insert, execute  # noqa: E402
from services import analysis_lifecycle as alc  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def _req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json;charset=utf-8"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def main():
    global PASS, FAIL
    st, _ = _req("GET", "/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase8 契约层：七步分析引擎 目标 {BASE}\n")

    # ── 隔离 fixture：项目 + 2 事项 ──
    pid = "p8e2e_" + uuid.uuid4().hex[:8]
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,audit_type,target_level,audit_period,"
        "audited_unit,objective,scope,setup_stage,deleted) "
        "VALUES (%s,%s,'预算执行审计','市级','2025年度','P8测试单位','核查合规','P8范围','workspace',0)",
        (pid, "P8契约层E2E"), database="tt",
    )
    for n in (1, 2):
        execute(
            "INSERT INTO audit_items (project_id,title,category,priority,seq) "
            "VALUES (%s,%s,'支出合规','高',%s)",
            (pid, f"P8事项{n}", n), database="tt",
        )
    item = query_one("SELECT id FROM audit_items WHERE project_id=%s ORDER BY id LIMIT 1",
                     (pid,), database="tt")
    focus_item_id = item["id"]

    # ═══ M008：三表 + 三列存在 ═══
    print("── M008 schema ──")
    for t in ("project_suspicions", "audit_agent_traces", "audit_step_summaries"):
        r = query_one("SELECT COUNT(*) AS n FROM information_schema.tables "
                      "WHERE table_schema='tt' AND table_name=%s", (t,), database="tt")
        check(f"表 {t} 存在", (r or {}).get("n") == 1)
    for col in ("focus_item_id", "analysis_target", "analysis_scope", "current_step"):
        r = query_one("SELECT COUNT(*) AS n FROM information_schema.columns "
                      "WHERE table_schema='tt' AND table_name='audit_analysis_tasks' AND column_name=%s",
                      (col,), database="tt")
        check(f"列 analysis_tasks.{col} 存在", (r or {}).get("n") == 1)

    # ═══ create_analysis_task（附录A §2：current_step=1）═══
    print("── create_analysis_task ──")
    tid = alc.create_analysis_task(pid, focus_item_id=focus_item_id,
                                   user_intent="P8契约测试", analysis_target="P8单位",
                                   analysis_scope="P8范围")
    check("task_code 16位hex", isinstance(tid, str) and len(tid) == 16, tid)
    trow = query_one("SELECT current_step,step,focus_item_id,analysis_target FROM "
                     "audit_analysis_tasks WHERE task_code=%s", (tid,), database="tt")
    check("current_step=1", (trow or {}).get("current_step") == 1, str(trow))
    check("step别名=1", (trow or {}).get("step") == 1)
    check("focus_item_id 回填", (trow or {}).get("focus_item_id") == focus_item_id)
    check("analysis_target 回填", (trow or {}).get("analysis_target") == "P8单位")

    # ═══ readiness 三道（附录A §9）═══
    print("── readiness entry/data_ready/evidence_complete ──")
    st, r = _req("GET", f"/api/audit/analysis/{tid}/readiness?stage=entry")
    check("entry 200", st == 200, str(st))
    check("entry 5项检查", isinstance(r.get("checks"), list) and len(r["checks"]) == 5,
          str(len(r.get("checks") or [])))
    check("entry ready=True（fixture 完整）", r.get("ready") is True, str(r.get("missing_items")))

    st, r = _req("GET", f"/api/audit/analysis/{tid}/readiness?stage=data_ready")
    check("data_ready 7项检查", isinstance(r.get("checks"), list) and len(r["checks"]) == 7,
          str(len(r.get("checks") or [])))
    check("data_ready 初始 not ready（无文件）", r.get("ready") is False)

    # 造 data_ready 所需：trace(done+分类) + data_contracts 行 + field_sources
    tr = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,"
        "ocr_version,parse_status,file_category,file_subcategory) "
        "VALUES (%s,%s,'bk','p/e2e.pdf',2,'done','合同','data_contracts')",
        (pid, "e2e.pdf"), database="tt",
    )
    dc_id = insert(
        "INSERT INTO data_contracts (project_id,document_trace_id,doc_name,amount) "
        "VALUES (%s,%s,%s,%s)", (pid, tr, "P8合同", 100000), database="tt",
    )
    insert("INSERT INTO audit_field_sources (project_id,table_name,row_id,field_name) "
           "VALUES (%s,%s,%s,%s)", (pid, "data_contracts", dc_id, "amount"), database="tt")
    st, r = _req("GET", f"/api/audit/analysis/{tid}/readiness?stage=data_ready")
    check("data_ready 造数后 ready=True", r.get("ready") is True, str(r.get("missing_items")))

    # ═══ 七步 current_step 权威推进 + summaries UPSERT + 固定 message_id ═══
    print("── advance_step 1→7 + summaries ──")
    for step in range(2, 8):
        alc.advance_step(tid, to_step=step,
                         step_data_patch={f"step{step}_data": "ok"},
                         summary_content=f"第{step}步总结",
                         summary_structured={"step": step},
                         summary_source_refs=[{"type": "test"}])
    auth = alc.get_authoritative_state(tid)
    check("推进后 current_step=7", auth.get("current_step") == 7, str(auth.get("current_step")))
    check("step_data 七步合并齐全",
          all(auth["step_data"].get(f"step{s}_data") == "ok" for s in range(2, 8)),
          str(auth.get("step_data")))
    check("summaries 覆盖 step 2-7",
          all(s in (auth.get("summaries") or {}) for s in range(2, 8)),
          str(sorted((auth.get("summaries") or {}).keys())))
    # 固定 message_id（附录A §8：step-N-summary）
    for s in range(2, 8):
        mid = (auth["summaries"].get(s) or {}).get("message_id")
        check(f"step{s} message_id=step-{s}-summary", mid == f"step-{s}-summary", str(mid))

    # ═══ 疑点五态核实流转（附录A §7）═══
    print("── suspicions/review 五态流转 ──")
    aid_num = (query_one("SELECT id FROM audit_analysis_tasks WHERE task_code=%s",
                         (tid,), database="tt") or {}).get("id")
    sid = insert(
        "INSERT INTO project_suspicions (project_id,analysis_id,suspicion_items,status,verify_status) "
        "VALUES (%s,%s,%s,'draft','MODEL_FOUND')",
        (pid, aid_num, json.dumps([{"x": 1}])), database="tt",
    )
    # MODEL_FOUND → WAIT_CONFIRM
    st, r = _req("POST", f"/api/audit/analysis/{tid}/suspicions/review",
                 {"suspicion_id": sid, "verify_status": "WAIT_CONFIRM", "reviewer": "tester"})
    check("→WAIT_CONFIRM 200", st == 200 and r.get("suspicion", {}).get("verify_status") == "WAIT_CONFIRM",
          str(st) + str(r)[:80])
    # WAIT_CONFIRM → CONFIRMED（status 同步）
    st, r = _req("POST", f"/api/audit/analysis/{tid}/suspicions/review",
                 {"suspicion_id": sid, "verify_status": "CONFIRMED", "evidence": {"doc": "证据1"}})
    check("→CONFIRMED", r.get("suspicion", {}).get("verify_status") == "CONFIRMED", str(r)[:80])
    check("CONFIRMED 同步 status=confirmed", r.get("suspicion", {}).get("status") == "confirmed")
    # evidence_chain 合并
    ec = query_one("SELECT evidence_chain FROM project_suspicions WHERE id=%s", (sid,), database="tt")
    ec_json = json.dumps(ec.get("evidence_chain") or {}, ensure_ascii=False)
    check("evidence_chain 含 review 证据", "证据1" in ec_json or "review" in ec_json, ec_json[:120])
    # 非法态 → 400
    st, r = _req("POST", f"/api/audit/analysis/{tid}/suspicions/review",
                 {"suspicion_id": sid, "verify_status": "BOGUS"})
    check("非法态 →400", st == 400, str(st))

    # ═══ trace 落库（P8-11：_persist_trace 全列可写）═══
    print("── trace 落库（audit_agent_traces 契约）──")
    trace_id = "tr_" + uuid.uuid4().hex[:12]
    insert(
        "INSERT INTO audit_agent_traces "
        "(trace_id, task_id, project_id, agent_id, agent_name, step, node_name, "
        " upstream_trace_ids, input_summary, output_summary, knowledge_sources, "
        " tool_call_records, llm_raw_response, validation_errors, duration_ms, status, model) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (trace_id, tid, pid, 1, "IntentAnalyzer", 1, "step_1_intent",
         json.dumps([]), json.dumps({"intent": "测试"}), json.dumps({"domain": "采购"}),
         json.dumps([]), json.dumps([]), None, None, 123, "success", "deepseek-v4-flash"),
        database="tt",
    )
    tr_row = query_one("SELECT task_id,project_id,step,node_name,status FROM "
                       "audit_agent_traces WHERE trace_id=%s", (trace_id,), database="tt")
    check("trace task_id 关联", (tr_row or {}).get("task_id") == tid, str(tr_row))
    check("trace step/node 落库", (tr_row or {}).get("step") == 1 and tr_row.get("node_name") == "step_1_intent")
    check("trace status=success", (tr_row or {}).get("status") == "success")

    # ═══ readiness evidence_complete（疑点已确认 + 法规）═══
    print("── readiness evidence_complete ──")
    # 选定法规写入 step_data
    alc.advance_step(tid, to_step=7, step_data_patch={"selected_laws": [{"law_id": "L1"}]})
    st, r = _req("GET", f"/api/audit/analysis/{tid}/readiness?stage=evidence_complete")
    check("evidence_complete 4项检查", isinstance(r.get("checks"), list) and len(r["checks"]) == 4,
          str(len(r.get("checks") or [])))
    check("evidence_complete 疑点已确认 ok",
          any(c["name"] == "疑点已确认" and c["ok"] for c in r.get("checks", [])),
          str(r.get("checks")))
    check("evidence_complete 法规存在 ok",
          any(c["name"] == "法规存在" and c["ok"] for c in r.get("checks", [])))

    # ═══ 文书证据继承（P8-8：报告读 CONFIRMED 疑点，无来源禁入）═══
    print("── documents/batch task_id 证据继承 ──")
    st, r = _req("POST", "/api/audit/documents/batch", {"task_id": tid})
    check("documents/batch 200", st == 200, str(st) + str(r)[:80])
    check("documents/batch success", r.get("success") is True)
    docs = r.get("documents") or {}
    check("四件套齐全", all(k in docs for k in ("evidence", "workpaper", "report", "review")),
          str(sorted(docs.keys())))
    check("含 readiness.evidence_complete", "readiness" in r and "evidence_complete" in r["readiness"])
    # report 应含 CONFIRMED 疑点（前面 review 到 CONFIRMED）
    rep = docs.get("report") or {}
    check("report 继承 CONFIRMED 疑点", rep.get("total_suspicions", 0) >= 1,
          "total=" + str(rep.get("total_suspicions")))

    # ═══ GET 权威状态（Q1：纯 MySQL 读）═══
    print("── GET /analysis/{id} 权威状态 ──")
    st, r = _req("GET", f"/api/audit/analysis/{tid}")
    check("GET 200", st == 200, str(st))
    check("GET current_step=7（权威）", r.get("current_step") == 7, str(r.get("current_step")))
    check("GET step别名=7", r.get("step") == 7)
    check("GET summaries 非空", isinstance(r.get("summaries"), dict) and len(r["summaries"]) >= 6)

    # ═══ 清理 ═══
    execute("DELETE FROM project_suspicions WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_agent_traces WHERE task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_step_summaries WHERE analysis_task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE task_code=%s", (tid,), database="tt")
    execute("DELETE FROM audit_items WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    print(f"\n{'='*48}\nPhase8 契约层：PASS={PASS}  FAIL={FAIL}\n{'='*48}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
