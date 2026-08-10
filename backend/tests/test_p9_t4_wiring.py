r"""Phase9 T4 溯源接线单元验证（直接驱动 Analyzer 真实代码路径）

E2E（test_p9_t4_provenance.py）受上游限制（表达式引擎原不支持 data_procurements
+ Step2 匹配多落收费域），scan 难命中 data_procurements，故 source_refs 不触发。
本测试【直接驱动 Analyzer._scan_expression】用一条命中 data_procurements 的表达式，
证明 P9-T4 写侧接线（scan→add_ref→get_refs→validate_output 注入→link_suspicion）
在真实命中时全程贯通。§0 铁律机制成立。

用法：cd backend && python tests\test_p9_t4_wiring.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agents.registry import AgentRegistry  # noqa: E402
from services import evidence_service as ev  # noqa: E402
from services.db import query_one, execute, insert  # noqa: E402

PID = "4a0946e4c4c0"
TID = "T4WIRE_TEST"
VID = 999999
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


def cleanup():
    execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_id LIKE %s",
            (PID, f"{TID}:%"), database="tt")
    # 怀疑是 T4WIRE 残留：先取本次创建的 sid 再按 sid 清（避免 LIKE %% 转义 + 子查询）
    sids = [r["id"] for r in __import__("services.db", fromlist=["query"]).query(
        "SELECT id FROM project_suspicions WHERE project_id=%s AND suspicion_items LIKE %s",
        (PID, "%T4WIRE%"), database="tt")]
    for sid in sids:
        execute("DELETE FROM audit_source_refs WHERE project_id=%s AND result_type='suspicion' "
                "AND result_id=%s", (PID, sid), database="tt")
    if sids:
        execute("DELETE FROM project_suspicions WHERE project_id=%s AND suspicion_items LIKE %s",
                (PID, "%T4WIRE%"), database="tt")


def main():
    global PASS, FAIL
    print(f"[test] Phase9 T4 溯源接线单元验证（直接驱动 Analyzer）\n")
    cleanup()

    agent = AgentRegistry().create_agent("audit_analyzer")
    # 复刻 build_prompt 的接线上下文初始化
    agent._task_id = TID
    agent._project_id = PID
    agent._evidence_by_vid = {}
    agent._vid_by_title = {"测试采购违规": VID}

    # ① _scan_expression：命中 data_procurements → 写 audit_source_refs + 暂存证据
    print("── ① _scan_expression 命中 data_procurements ──")
    scan = agent._scan_expression(
        "contract_amount > 1000000", PID,
        violation={"id": VID, "violation_title": "测试采购违规"}, task_id=TID)
    check("扫描成功", scan.get("success"), str(scan)[:120])
    check("命中行 > 0", (scan.get("hits") or 0) > 0, f"hits={scan.get('hits')}")
    rid_key = f"{TID}:{VID}"
    ref_rows = ev.get_refs("analysis_hit", rid_key)
    check(f"audit_source_refs 写入 analysis_hit（{len(ref_rows)} 条）", len(ref_rows) > 0)
    # 证据可解析到 chunk 页码/原文
    resolvable = [r for r in ref_rows if r.get("page_number") or r.get("quote")]
    check("analysis_hit 引用可解析到页码/原文", len(resolvable) > 0,
          f"{len(resolvable)}/{len(ref_rows)}")
    check("证据按 vid 暂存（_evidence_by_vid）",
          len(agent._evidence_by_vid.get(VID, [])) > 0,
          f"vid={VID} stash={len(agent._evidence_by_vid.get(VID, []))}")

    # ② validate_output：注入 analysis_result.source_refs
    print("── ② validate_output 注入 source_refs ──")
    out = {"analysis_results": [{"violation_model": "测试采购违规", "scan_summary": {}}]}
    agent.validate_output(out)
    sr = out["analysis_results"][0].get("source_refs")
    check("analysis_result 注入 source_refs", isinstance(sr, list) and len(sr) > 0,
          f"source_refs={len(sr) if sr else 0}")
    check("source_refs 含 chunk_id", sr and any(x.get("chunk_id") for x in sr), str(sr)[:120])

    # ③ link_suspicion_evidence：疑点继承同批 chunk 证据
    print("── ③ link_suspicion_evidence 疑点继承 ──")
    sid = insert(
        "INSERT INTO project_suspicions "
        "(project_id, suspicion_items, evidence_chain, status, verify_status) "
        "VALUES (%s,%s,%s,'draft','MODEL_FOUND')",
        (PID, json.dumps([{"title": "T4WIRE 测试疑点"}], ensure_ascii=False),
         json.dumps({}, ensure_ascii=False)), database="tt",
    )
    n = ev.link_suspicion_evidence(PID, TID, sid)
    check(f"link_suspicion_evidence 复制 {n} 条引用", n > 0, f"n={n}")
    srefs = ev.get_refs("suspicion", sid)
    check("audit_source_refs 有 suspicion 引用", len(srefs) > 0, f"refs={len(srefs)}")
    check("suspicion 引用可解析到页码/原文",
          any(r.get("page_number") or r.get("quote") for r in srefs))

    cleanup()
    print(f"\n{'='*50}\nPhase9 T4 接线单元：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
