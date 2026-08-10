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
    print("\n── ③ analysis_results 的 source_refs ──")
    if ares:
        info("analysis_results[0] 全部键", sorted(ares[0].keys()))
        has_sr = [a for a in ares if a.get("source_refs")]
        has_ev = [a for a in ares if a.get("evidence") or a.get("evidence_chain")]
        info("带 source_refs 的 hit 数", len(has_sr))
        info("带 evidence 的 hit 数", len(has_ev))
        check("analysis_results 各 hit 带 source_refs（§0 铁律）", len(has_sr) == len(ares) and len(ares) > 0,
              f"{len(has_sr)}/{len(ares)}")
        check("analysis_results 各 hit 带 evidence", len(has_ev) > 0, f"{len(has_ev)}/{len(ares)}")
    else:
        check("analysis_results 非空", False, "无 analysis_results，无法判定 source_refs")
        print("    (跳过 source_refs 检查)")

    # ═══ ④ audit_source_refs 结论级引用计数（analysis_hit/suspicion）═══
    print("\n── ④ audit_source_refs 结论级引用 ──")
    by_type = query(
        "SELECT result_type, COUNT(*) AS n FROM audit_source_refs "
        "WHERE project_id=%s GROUP BY result_type", (pid,), database="tt")
    info("source_refs by result_type（全项目历史）", {r["result_type"]: r["n"] for r in by_type})
    concl = sum(r["n"] for r in by_type if r["result_type"] in
                ("analysis_hit", "suspicion", "law_recommendation"))
    check("有结论级 source_refs（analysis_hit/suspicion/law_rec）", concl > 0,
          f"结论级={concl}")

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
        # 疑点级 source_refs（result_type=suspicion）
        srefs = ev.get_refs("suspicion", sid)
        info("suspicion source_refs 条数", len(srefs))
        check("suspicion 带 source_refs（§0 铁律）", len(srefs) > 0, f"refs={len(srefs)}")

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

    print(f"\n{'='*50}\nPhase9 T4 溯源穿透：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
