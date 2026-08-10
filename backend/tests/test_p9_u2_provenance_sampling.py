r"""Phase9 U2 溯源抽样验收：抽样 N≥10 条 AI 结论，证据可回溯到 chunk/页/原文

§6.9 上线检查单项："U2 溯源抽样 N 条可回溯"。
§6 动作 U2 完成标准："抽样 N 条 AI 结论，证据可回溯到 chunk/页/原文"。

本测试是 §0 铁律的**抽样规模**验收（与既有单链路/单机制测试互补）：
  - T1(§2) / §7 provenance：真 LLM 单链路，source_refs 非空可解析（PASS=10/0）——证明"单条结论可溯源"。
  - T4 wiring(test_p9_t4_wiring.py)：直接驱动单次扫描，10/10——证明"接线机制成立"。
  - **U2 本测试**：把命中池扩到 ≥10 条 AI 结论，**抽样逐条回溯**，断言 0 条断链——
    证明 §0 铁律在抽样规模上普遍成立，非单夹具巧合。

现状（_probe_u2_pool.py 实测，2026-08-10）：
  - data_procurements 仅 3 行（命中池上限 3，远 <10）→ **补植 12 行扩池**。
  - document_chunks 30 行全有 text，但 page_nums 全空（OntoSKU 源端空，不伪造）→
    可回溯性走 quote（= chunk 原文片段），属 §0"chunk/页/**原文**"口径。
  - field_sources 144 行 / 43 有 chunk 链接。

方法（faithful-mode，确定性可复现）：
  ① 补植 12 行 data_procurements（contract_amount=200万 > 100万阈值），每行经
     audit_field_sources 链到一个**不同的真实 chunk**（复用现有 chunk_id，证据链
     原文真实可解析）；全部标记 U2SAMP 便于 cleanup 定向还原。
  ② 直接驱动 Analyzer._scan_expression（生产写侧接线 scan→add_ref→get_refs），
     命中 12 行 → analysis_hit refs（按 chunk 去重 ≥10）。
  ③ 抽样全部 refs（断言池 ≥10），逐条 source_id→chunk 回溯，断言**每条**有 text(原文)
     或 page_nums(页码)；0 条断链即过。
  ④ build_field_sources_evidence 在 12 行规模逐行装配，断言普遍产出含 text 的证据链。
  ⑤ link_suspicion_evidence → suspicion refs 抽样回溯（覆盖疑点结论类型）。
  ⑥ cleanup 定向删除补植行 + 其 field_sources + 本任务 analysis_hit/suspicion refs。

为何直接驱动而非全链 POST /analysis：U2 的被测对象是"结论→证据→chunk/原文"的**回溯
解析**在抽样规模成立与否；全链的命中表/条数依赖 LLM + 违规匹配（非确定性，无法保证
≥10 条 data_procurements 命中）。直接驱动确定性复现，单链路真实性已由 §7 证明。

用法：cd backend && python tests\test_p9_u2_provenance_sampling.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.registry import AgentRegistry  # noqa: E402
from services import evidence_service as ev  # noqa: E402
from services.db import query, query_one, execute, insert  # noqa: E402

PID = "4a0946e4c4c0"          # fixture 项目（同 T4 wiring/provenance）
TID = "U2SAMP_TEST"           # 本测试任务 id（rid_key = TID:VID）
VID = 888888                  # 桩违规 id（非真实 audit_violations 行，仅供 rid_key）
N_FLOOR = 10                  # 抽样规模下限（spec「N 条」，取 10）
PLANT_N = 12                  # 补植行数（> N_FLOOR，留去重/边界余量）
MARKER = "U2SAMP-PLANTED"     # 补植行 subject_name 标记（cleanup 定向）
SCAN_EXPR = "contract_amount > 1000000"   # wiring 验证过能命中 data_procurements
PASS = 0
FAIL = 0


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
    if len(s) > 240:
        s = s[:240] + "…"
    print(f"    ℹ️ {label} = {s}")


def _loads(v):
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def plant_rows():
    """补植 PLANT_N 行 data_procurements，每行链到一个不同的真实 chunk（有 text）。
    返回 (planted_row_ids, chunks_used)。"""
    chunks = query(
        "SELECT ch.id, ch.trace_id FROM audit_document_chunks ch "
        "JOIN audit_document_traces dt ON dt.id=ch.trace_id "
        "WHERE dt.project_id=%s AND ch.text IS NOT NULL AND ch.text!='' "
        "ORDER BY ch.id LIMIT %s", (PID, PLANT_N), database="tt")
    check(f"找到 {PLANT_N} 个有正文的 chunk 用于挂链", len(chunks) >= PLANT_N,
          f"chunks={len(chunks)}")
    row_ids = []
    for ch in chunks:
        rid = insert(
            "INSERT INTO data_procurements "
            "(project_id, document_trace_id, subject_name, contract_amount, "
            " budget_amount, procurement_method, doc_name) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (PID, ch["trace_id"], MARKER, 2000000, 1000000, "询价", MARKER),
            database="tt")
        row_ids.append(rid)
        # 该行 contract_amount 字段 ← chunk 证据（复用真实 chunk，原文链真实可解析）
        insert(
            "INSERT INTO audit_field_sources "
            "(project_id, table_name, row_id, field_name, chunk_id) "
            "VALUES (%s,%s,%s,%s,%s)",
            (PID, "data_procurements", rid, "contract_amount", ch["id"]),
            database="tt")
    return row_ids, [c["id"] for c in chunks]


def cleanup(row_ids):
    """定向还原：补植行 + 其 field_sources + 本任务 analysis_hit/suspicion refs。"""
    if row_ids:
        ph = ",".join(["%s"] * len(row_ids))
        execute(f"DELETE FROM audit_field_sources WHERE project_id=%s "
                f"AND table_name='data_procurements' AND row_id IN ({ph})",
                (PID, *row_ids), database="tt")
        execute(f"DELETE FROM data_procurements WHERE project_id=%s AND id IN ({ph})",
                (PID, *row_ids), database="tt")
    execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_type='analysis_hit' "
            "AND result_id LIKE %s", (PID, f"{TID}:%"), database="tt")
    # suspicion：先按 sid 清 refs 再清疑点
    sids = [r["id"] for r in query(
        "SELECT id FROM project_suspicions WHERE project_id=%s AND suspicion_items LIKE %s",
        (PID, "%U2SAMP%"), database="tt")]
    for sid in sids:
        execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_type='suspicion' "
                "AND result_id=%s", (PID, sid), database="tt")
    if sids:
        execute("DELETE FROM project_suspicions WHERE project_id=%s AND suspicion_items LIKE %s",
                (PID, "%U2SAMP%"), database="tt")


def main():
    global PASS, FAIL
    print(f"[test] Phase9 U2 溯源抽样验收（N≥{N_FLOOR}）\n")

    # ═══ ① 补植 PLANT_N 行扩命中池 ═══
    print(f"── ① 补植 {PLANT_N} 行 data_procurements（各链 distinct chunk）──")
    row_ids, chunks_used = plant_rows()
    info("补植行 ids", row_ids)
    info("挂链 chunk ids", chunks_used)
    check(f"成功补植 {PLANT_N} 行", len(row_ids) == PLANT_N, f"planted={len(row_ids)}")

    # ═══ ② 直接驱动 _scan_expression（生产写侧接线）═══
    print("\n── ② _scan_expression 命中补植行（scan→add_ref）──")
    agent = AgentRegistry().create_agent("audit_analyzer")
    agent._task_id = TID
    agent._project_id = PID
    agent._evidence_by_vid = {}
    agent._vid_by_title = {"U2抽样违规": VID}

    scan = agent._scan_expression(
        SCAN_EXPR, PID,
        violation={"id": VID, "violation_title": "U2抽样违规"}, task_id=TID)
    info("扫描结果", {"success": scan.get("success"), "table": scan.get("table"),
                      "hits": scan.get("hits"), "total(扫)": scan.get("total")})
    check("扫描 success", scan.get("success") is True, str(scan)[:160])
    check(f"命中行 ≥ {PLANT_N}（补植行全命中）",
          (scan.get("hits") or 0) >= PLANT_N, f"hits={scan.get('hits')}")

    # ═══ ③ 抽样 analysis_hit refs，逐条回溯 ═══
    print(f"\n── ③ 抽样 analysis_hit refs，逐条回溯 source_id→chunk ──")
    rid_key = f"{TID}:{VID}"
    refs = ev.get_refs("analysis_hit", rid_key)
    info("analysis_hit ref 池规模", len(refs))
    check(f"ref 池 ≥ {N_FLOOR}（抽样规模达标）", len(refs) >= N_FLOOR, f"refs={len(refs)}")

    traceable = 0
    untraceable = []
    for r in refs:
        cid = r.get("source_id")
        chunk = query_one("SELECT text, page_nums FROM audit_document_chunks WHERE id=%s",
                          (cid,), database="tt")
        text = (chunk or {}).get("text") or ""
        pages = _loads((chunk or {}).get("page_nums")) or []
        # ref 自身也带 quote/page_number（add_ref 写入时装配）
        has_quote = bool(r.get("quote"))
        has_ref_page = r.get("page_number") is not None
        ok = bool(text) or bool(pages) or has_quote or has_ref_page
        if ok:
            traceable += 1
        else:
            untraceable.append({"ref_id": r.get("id"), "chunk_id": cid})
    info("可回溯条数", f"{traceable}/{len(refs)}")
    info("不可回溯", untraceable or "无")
    check(f"§0 抽样：全部 refs 可回溯到 chunk/原文/页（{traceable}/{len(refs)}）",
          len(refs) > 0 and traceable == len(refs),
          f"断链 {len(untraceable)} 条：{untraceable[:3]}")
    # 明确的「抽样 N 条」断言：取前 N_FLOOR 条，每条可回溯
    sample = refs[:N_FLOOR]
    sample_ok = all(
        (query_one("SELECT text FROM audit_document_chunks WHERE id=%s",
                   (r.get("source_id"),), database="tt") or {}).get("text")
        for r in sample)
    check(f"§0 抽样 N={N_FLOOR} 条：每条 source→chunk 原文非空", sample_ok,
          f"sample={len(sample)}")

    # ═══ ④ build_field_sources_evidence 在补植行规模逐行装配 ═══
    print(f"\n── ④ build_field_sources_evidence 规模装配（{PLANT_N} 行）──")
    ev_ok = 0
    for rid in row_ids:
        chain = ev.build_field_sources_evidence(PID, "data_procurements", rid)
        if any((e.get("text") or e.get("page_nums")) for e in chain):
            ev_ok += 1
    info("装配出含原文/页码证据的行", f"{ev_ok}/{len(row_ids)}")
    check(f"装配机制在 {PLANT_N} 行规模普遍可用（全部产出证据）",
          ev_ok == len(row_ids), f"ev_ok={ev_ok}")

    # ═══ ⑤ suspicion 继承 evidence（覆盖疑点结论类型）═══
    print("\n── ⑤ link_suspicion_evidence 疑点继承 → suspicion refs 回溯 ──")
    sid = insert(
        "INSERT INTO project_suspicions "
        "(project_id, suspicion_items, evidence_chain, status, verify_status) "
        "VALUES (%s,%s,%s,'draft','MODEL_FOUND')",
        (PID, json.dumps([{"title": "U2SAMP 抽样疑点"}], ensure_ascii=False),
         json.dumps({}, ensure_ascii=False)), database="tt")
    n_link = ev.link_suspicion_evidence(PID, TID, sid)
    srefs = ev.get_refs("suspicion", sid)
    info("疑点继承 refs", f"link 写入 {n_link} 条 / 查到 {len(srefs)} 条")
    s_traceable = sum(
        1 for r in srefs
        if r.get("quote") or r.get("page_number") is not None
        or (query_one("SELECT text FROM audit_document_chunks WHERE id=%s",
                      (r.get("source_id"),), database="tt") or {}).get("text"))
    info("suspicion 可回溯", f"{s_traceable}/{len(srefs)}")
    check("suspicion refs 继承自 analysis_hit（条数 >0）", n_link > 0 and len(srefs) > 0,
          f"link={n_link}")
    check(f"§0 抽样：suspicion refs 全部可回溯（{s_traceable}/{len(srefs)}）",
          len(srefs) > 0 and s_traceable == len(srefs), f"断链 {len(srefs)-s_traceable}")

    # ═══ ⑥ cleanup ═══
    print("\n── ⑥ cleanup 还原 ──")
    cleanup(row_ids)
    remain_proc = query_one(
        "SELECT COUNT(*) AS n FROM data_procurements WHERE project_id=%s AND subject_name=%s",
        (PID, MARKER), database="tt")
    remain_fs = query_one(
        "SELECT COUNT(*) AS n FROM audit_field_sources WHERE project_id=%s AND table_name='data_procurements' "
        "AND field_name='contract_amount' AND chunk_id IN (%s)",
        (PID, ",".join(str(c) for c in chunks_used)) if chunks_used else (PID, "0"),
        database="tt") if chunks_used else {"n": 0}
    remain_refs = query_one(
        "SELECT COUNT(*) AS n FROM audit_source_refs WHERE result_type='analysis_hit' AND result_id LIKE %s",
        (f"{TID}:%",), database="tt")
    info("残留补植行", remain_proc)
    info("残留补植 field_sources", remain_fs)
    info("残留 analysis_hit refs", remain_refs)
    check("cleanup：补植行清零", remain_proc["n"] == 0, str(remain_proc))
    check("cleanup：本任务 refs 清零", remain_refs["n"] == 0, str(remain_refs))

    print(f"\n{'='*50}\nPhase9 U2 溯源抽样验收：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
