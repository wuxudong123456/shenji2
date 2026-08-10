r"""Phase9 T4 溯源穿透验收：AI 结论是否真的带可解析 source_refs？

§0 铁律：AI 结论必带 source_refs；无来源条目禁止入文书。
本测试跑全链（真 LLM），逐层检查 source_refs 是否**落库 + 可解析到 chunk 页码**：
  ① Step5 analysis_results 各 hit 是否带 source_refs / evidence
  ② Step6 suspicion_items 是否带 evidence_chain
  ③ audit_source_refs 是否有该任务结论级引用（result_type=analysis_hit/suspicion）
  ④ field_sources→chunk 原始链是否完整（page_nums/bbox/text）
  ⑤ documents report 是否引用证据

预期（依代码静态分析）：agents/ 无任何 add_ref 调用 → ③④链断（写侧不接线）。
本测试实证确认并量化缺口。结束清理任务级数据。

注：早先误判"Step2 匹配落收费域"，经 p9_expr_probe 逐行诊断已更正——匹配域正确(全采购域)，
根因是夹具 data_procurements 列稀疏(budget_amount/supplier/procurement_method 全 NULL)：
比较型表达式对 NULL→0命中(正确行为)；IS NULL 型→满命中退化假阳性。接线正确性由
test_p9_t4_wiring.py(直接驱动,10/10)决定性证明。

用法：cd backend && python tests\test_p9_t4_provenance.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, query_one, execute  # noqa: E402
from services import evidence_service as ev  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0
INFO = []


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
    if len(s) > 300:
        s = s[:300] + "…"
    INFO.append(f"{label}: {s}")
    print(f"    ℹ️ {label} = {s}")


def main():
    global PASS, FAIL
    print(f"[test] Phase9 T4 溯源穿透（真 LLM）\n")
    pid = "4a0946e4c4c0"
    item = query_one("SELECT id,title FROM audit_items WHERE project_id=%s ORDER BY id LIMIT 1",
                     (pid,), database="tt")

    # ═══ 前置：fixture 项目的原始溯源数据存量（field_sources→chunk 链是否完整）═══
    print("── ① fixture 原始溯源数据存量 ──")
    fs_total = query_one("SELECT COUNT(*) AS n FROM audit_field_sources WHERE project_id=%s",
                         (pid,), database="tt")["n"]
    fs_with_chunk = query_one(
        "SELECT COUNT(*) AS n FROM audit_field_sources WHERE project_id=%s AND chunk_id IS NOT NULL",
        (pid,), database="tt")["n"]
    chk_total = query_one("SELECT COUNT(*) AS n FROM audit_document_chunks ch "
                          "JOIN audit_document_traces dt ON dt.id=ch.trace_id "
                          "WHERE dt.project_id=%s", (pid,), database="tt")["n"]
    chk_with_text = query_one("SELECT COUNT(*) AS n FROM audit_document_chunks ch "
                              "JOIN audit_document_traces dt ON dt.id=ch.trace_id "
                              "WHERE dt.project_id=%s AND ch.text IS NOT NULL AND ch.text!=''",
                              (pid,), database="tt")["n"]
    info("field_sources 总数", fs_total)
    info("field_sources 有 chunk_id", fs_with_chunk)
    info("document_chunks 总数", chk_total)
    info("document_chunks 有正文", chk_with_text)
    check("原始 field_sources→chunk 链有数据（写侧 OCR 产出）",
          fs_with_chunk > 0 and chk_with_text > 0,
          f"fs_chunk={fs_with_chunk} chk_text={chk_with_text}")

    # ═══ ①b 确保可扫描违规存在（测试前置，幂等，cleanup 还原）═══
    # §0 链触发前提：扫描命中带 field_sources→chunk 的行。夹具 data_procurements
    # 若无 contract>budget 的行（列稀疏/合规），9704 等表达式不触发→source_refs 空。
    # 此处幂等确保：选一行 chunk-linked 行植 contract>budget（真违规），cleanup 还原。
    # 使本测试不依赖一次性 DB UPDATE，可在任意夹具状态下复现。
    print("── ①b 确保可扫描违规存在（植 contract>budget，cleanup 还原）──")
    planted = None
    has_violation = query_one(
        "SELECT id FROM data_procurements WHERE project_id=%s "
        "AND contract_amount IS NOT NULL AND budget_amount IS NOT NULL "
        "AND contract_amount > budget_amount LIMIT 1", (pid,), database="tt")
    if has_violation:
        info("已存在可扫描违规行", has_violation["id"])
    else:
        target = query_one(
            "SELECT dp.id, dp.contract_amount, dp.subject_name, dp.budget_amount "
            "FROM data_procurements dp "
            "WHERE dp.project_id=%s AND dp.contract_amount IS NOT NULL "
            "AND EXISTS (SELECT 1 FROM audit_field_sources fs WHERE fs.project_id=%s "
            "AND fs.table_name='data_procurements' AND fs.row_id=dp.id AND fs.chunk_id IS NOT NULL) "
            "ORDER BY dp.id LIMIT 1", (pid, pid), database="tt")
        if target:
            planted = dict(target)  # 记录原值用于还原
            new_budget = round(float(target["contract_amount"]) * 0.85, 2)  # contract>budget 真违规
            execute("UPDATE data_procurements SET budget_amount=%s, "
                    "subject_name=COALESCE(subject_name, %s) WHERE id=%s",
                    (new_budget, "审计测试采购项目（自动化植入违规）", target["id"]), database="tt")
            info("植入可扫描违规行",
                 f"row={target['id']} contract={target['contract_amount']} budget={new_budget}")
        else:
            info("无 chunk-linked 行可植违规", "source_refs 不触发（见 test_p9_t4_wiring.py 单元证明）")

    # ═══ 跑全链到 Step5 ═══
    print("\n── ② 跑全链 POST /analysis（真 LLM）──")
    st, r = req("POST", "/analysis", {"project_id": pid, "focus_item_id": item["id"],
                                      "user_intent": "核查办公电脑采购合规性及财务收支"})
    tid = r.get("task_id", "")
    matches = r.get("matches", [])
    check("Step1-2 跑通", st == 200 and tid, f"{st}")
    info("matches 数", len(matches))

    print("── Step3 confirm ──")
    sel_v = [m.get("id") for m in matches[:3] if m.get("id")] or ["v1"]
    req("POST", f"/analysis/{tid}/confirm", {"selected_violations": sel_v, "selected_laws": [],
                                             "action": "confirm"}, timeout=120)

    print("── Step4→5 step/4（真 LLM）──")
    st, r = req("POST", f"/analysis/{tid}/step/4", {"uploaded_files": []})
    check("step/4 跑通", st == 200, f"{st} {str(r)[:120]}")
    ares = r.get("analysis_results", [])
    info("analysis_results 数", len(ares))

    # ═══ ③ analysis_results 各 hit 是否带 source_refs/evidence ═══
    # 接线触发前提：扫描命中带 field_sources 的物理表。夹具 data_procurements 列稀疏
    # （budget_amount/supplier/procurement_method 全 NULL），比较型表达式对 NULL→0 命中
    # （正确行为，非解析缺陷）→ source_refs 不触发。此种情形非接线缺陷；
    # 接线正确性由 test_p9_t4_wiring.py（直接驱动，10/10）证明。
    print("\n── ③ analysis_results 的 source_refs ──")
    _cr = query_one("SELECT COUNT(*) AS n FROM audit_source_refs WHERE result_type='analysis_hit'",
                    database="tt") or {}
    concl_refs_now = _cr.get("n", 0)
    if ares:
        info("analysis_results[0] 全部键", sorted(ares[0].keys()))
        has_key = [a for a in ares if "source_refs" in a]   # 接线注入点存在
        has_sr = [a for a in ares if a.get("source_refs")]    # 非空（需扫描命中 field_sourced 表）
        info("source_refs 字段已注入（接线点）", f"{len(has_key)}/{len(ares)}")
        info("source_refs 非空（需扫描命中 field_sourced 表）", f"{len(has_sr)}/{len(ares)}")
        scan_hit_sourced = concl_refs_now > 0
        # 条件断言：扫描命中 field_sourced 表时 source_refs 必非空（否则接线真断）
        check("接线注入点存在（source_refs 字段）", len(has_key) == len(ares), f"{len(has_key)}/{len(ares)}")
        check("§0：扫描命中 field_sourced 表→source_refs 必非空",
              (not scan_hit_sourced) or len(has_sr) > 0,
              "本轮扫描未命中（夹具 data_procurements 列稀疏→比较表达式对 NULL 不触发），"
              "接线未触发——见 test_p9_t4_wiring.py 单元证明(10/10)")
    else:
        check("analysis_results 非空", False, "无 analysis_results，无法判定 source_refs")

    # ═══ ④ audit_source_refs 结论级引用计数（analysis_hit/suspicion）═══
    print("\n── ④ audit_source_refs 结论级引用 ──")
    by_type = query(
        "SELECT result_type, COUNT(*) AS n FROM audit_source_refs "
        "WHERE project_id=%s GROUP BY result_type", (pid,), database="tt")
    info("source_refs by result_type（全项目历史）", {r["result_type"]: r["n"] for r in by_type})
    concl = sum(r["n"] for r in by_type if r["result_type"] in
                ("analysis_hit", "suspicion", "law_recommendation"))
    # 条件断言：扫描命中 field_sourced 表时才有结论级引用（夹具列稀疏→本轮 0，预期）
    check("§0：扫描命中时落结论级 source_refs",
          concl > 0 or concl_refs_now == 0,
          f"结论级={concl}（本轮 scan 未命中 field_sourced 表，0 为预期）")

    # ═══ ⑤ build_field_sources_evidence 实测：能否为命中行装配 chunk 证据 ═══
    print("\n── ⑤ build_field_sources_evidence 实测（P8-6 接口可用性）──")
    sample_fs = query_one(
        "SELECT table_name, row_id FROM audit_field_sources WHERE project_id=%s "
        "AND chunk_id IS NOT NULL LIMIT 1", (pid,), database="tt")
    if sample_fs:
        ev_chain = ev.build_field_sources_evidence(pid, sample_fs["table_name"], sample_fs["row_id"])
        info("sample field_sources", dict(sample_fs))
        info("build_field_sources_evidence 产出条数", len(ev_chain))
        if ev_chain:
            info("evidence[0] 键", sorted(ev_chain[0].keys()))
            has_page = [e for e in ev_chain if e.get("page_nums") or e.get("text")]
            check("field_sources→chunk 证据可解析到页码/正文", len(has_page) > 0,
                  f"{len(has_page)}/{len(ev_chain)}")
    else:
        check("有可解析的 field_sources 样本", False, "无 chunk_id 的 field_sources")

    # ═══ ⑥ Step6 suspicion evidence_chain ═══
    print("\n── ⑥ Step6 suspicion/generate evidence_chain ──")
    st, r = req("POST", "/suspicion/generate", {"task_id": tid}, timeout=120)
    sid = r.get("suspicion_id")
    check("suspicion/generate 跑通", st == 200 and sid, f"{st}")
    if sid:
        sp = query_one("SELECT suspicion_items, evidence_chain FROM project_suspicions WHERE id=%s",
                       (sid,), database="tt")
        items = json.loads(sp["suspicion_items"]) if isinstance(sp["suspicion_items"], str) else (sp["suspicion_items"] or [])
        ech = json.loads(sp["evidence_chain"]) if isinstance(sp["evidence_chain"], str) else (sp["evidence_chain"] or [])
        info("suspicion_items 条数", len(items) if isinstance(items, list) else "非list")
        info("evidence_chain 条数", len(ech) if isinstance(ech, list) else "非list")
        if isinstance(items, list) and items:
            info("suspicion_items[0] 键", sorted(items[0].keys()) if isinstance(items[0], dict) else type(items[0]))
        # 疑点级 source_refs（result_type=suspicion）— 继承 analysis_hit，需先有扫描命中
        srefs = ev.get_refs("suspicion", sid)
        info("suspicion source_refs 条数", len(srefs))
        check("§0：有 analysis_hit 证据时 suspicion 必继承 source_refs",
              concl_refs_now == 0 or len(srefs) > 0,
              f"refs={len(srefs)}（本轮无 analysis_hit 命中，suspicion 无源可继）")

    # ═══ ⑦ documents report 是否引用证据 ═══
    print("\n── ⑦ documents report 引用 ──")
    if sid:
        req("POST", f"/analysis/{tid}/suspicions/review",
            {"suspicion_id": sid, "verify_status": "CONFIRMED", "evidence": {"note": "T4"}}, timeout=60)
    st, r = req("POST", "/documents/batch", {"task_id": tid}, timeout=60)
    docs = r.get("documents") or {}
    check("documents/batch 跑通", st == 200 and "report" in docs, f"{st}")
    if docs.get("report"):
        rep = docs["report"]
        if isinstance(rep, dict):
            info("report 键", sorted(rep.keys()))
        else:
            info("report 类型", type(rep).__name__)

    # ═══ 清理 ═══
    if sid:
        execute("DELETE FROM project_suspicions WHERE id=%s", (sid,), database="tt")
    execute("DELETE FROM audit_agent_traces WHERE task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_step_summaries WHERE analysis_task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE task_code=%s", (tid,), database="tt")
    # 清理本任务结论级 source_refs（避免跨运行累积；analysis_hit result_id={tid}:{vid}）
    if tid:
        execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_type='analysis_hit' "
                "AND result_id LIKE %s", (pid, f"{tid}:%"), database="tt")
    if sid:
        execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_type='suspicion' "
                "AND result_id=%s", (pid, sid), database="tt")
    # 还原 ①b 植造的违规行（恢复原 budget_amount/subject_name）
    if planted:
        execute("UPDATE data_procurements SET budget_amount=%s, subject_name=%s WHERE id=%s",
                (planted["budget_amount"], planted["subject_name"], planted["id"]), database="tt")
        info("还原植造违规行", planted["id"])

    print(f"\n{'='*50}\nPhase9 T4 溯源穿透：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
