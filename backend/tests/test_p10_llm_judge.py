r"""Phase10 — 改动① LLM 语义降级验收

背景：Step⑤ 审核比对「规则语法对但值对不上」(病B)→ LLM 按语义再判一遍兜底。
架构（探索钉死，faithful-mode）：
  ✅ 降级只在 build_and_execute（execution_planner.py:91-116）planner 层；
     expression_engine.py 引擎层零 LLM 依赖（红线，test_p9_t6 守护）。
  ✅ 触发条件：success=False+error（语法错）或 success=True+hits=0+total>0（0 命中）；
     **排除 needs_review=True**（聚合 SQL 待人工确认，不能绕 Submit→Confirm 门禁）。
  ✅ judge_source 三态：rule（确定性）/ llm（非确定性，前端🤖徽章+文书脚注）/ manual（聚合待审）。
  ✅ LLM 不可用（health()=False）→ judge_violation_via_llm 返 judged=False，零副作用，保持原 scan。

三个测试（对应计划改动①-3）：
  ① test_judge_offline：LLM 死端点 → judged=False，不抛异常、零副作用（红线：不白屏/不雪崩）
  ② test_fallback：语法错表达式 + 命中数据 → patch LLM 判命中 → judge_source='llm'，hits>0，
     rows 带真实行字段（_build_llm_hit_rows 回查数据）—— planner 层集成逻辑
  ③ test_needs_review_not_bypassed：聚合 needs_review=True → judge_source='manual'，
     且 judge_violation_via_llm 根本未被调用（门禁红线：不绕人工确认）

测试②③测 planner 集成逻辑：用 monkey-patch 替 execute_expression / judge_violation_via_llm，
不依赖真实 LLM 在线或准确率（避免 flaky）。仿 test_p9_t6_llm_down.py 夹具+check 风格。

用法：cd backend && python tests\test_p10_llm_judge.py
"""
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.llm_semantic_judge import judge_violation_via_llm  # noqa: E402
from services.db import execute, insert, query_one  # noqa: E402
from services import execution_planner  # noqa: E402

BASE = "http://127.0.0.1:5000"
DEAD_LLM = "http://127.0.0.1:1/v1"  # 端口1无监听 = LLM停机（连接拒绝）
TEST_PID = "T10LLMJUDGE_TEST"
TEST_VID = None  # 夹具违规id（setup填）
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
    s = str(val)
    if len(s) > 220:
        s = s[:220] + "…"
    print(f"    ℹ️ {label} = {s}")


def _setup_fixture():
    """造测试项目 + 违规规则 + 命中数据行。返回夹具违规 id。"""
    # 清理可能的历史残留
    execute("DELETE FROM data_contracts WHERE project_id=%s", (TEST_PID,), database="tt")
    execute("DELETE FROM audit_violations WHERE violation_code=%s", ("T10-TEST",), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (TEST_PID,), database="tt")

    # 项目（workspace 阶段，与真实项目一致）
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (TEST_PID, "T10 LLM判定测试"), database="tt")
    # 违规：EXISTS 嵌套子查询——expression_engine 不支持 → 必报语法错（触发降级条件A）
    insert(("INSERT INTO audit_violations (violation_code, violation_title, expression_text, severity) "
            "VALUES (%s,%s,%s,'high')"),
           ("T10-TEST", "测试-大额采购应公开招标未招标",
            "EXISTS (SELECT 1 FROM data_contracts WHERE 采购方式 != '公开招标')"),
           database="tt")
    vid_row = query_one("SELECT id FROM audit_violations WHERE violation_code=%s",
                        ("T10-TEST",), database="tt")
    vid = vid_row["id"]
    # 数据行：1行合规（公开招标）+1行疑点（询价 440万，应公开招标）
    insert(("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
            "VALUES (%s,%s,%s,%s)"),
           (TEST_PID, "T10-合规行", 5000000, "公开招标"), database="tt")
    insert(("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
            "VALUES (%s,%s,%s,%s)"),
           (TEST_PID, "T10-疑点行", 4400000, "询价"), database="tt")
    return vid


def _teardown():
    execute("DELETE FROM data_contracts WHERE project_id=%s", (TEST_PID,), database="tt")
    execute("DELETE FROM audit_violations WHERE violation_code=%s", ("T10-TEST",), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (TEST_PID,), database="tt")


def main():
    global PASS, FAIL, TEST_VID
    print("[test] Phase10 改动① LLM 语义降级验收\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    TEST_VID = _setup_fixture()

    orig_llm = os.environ.get("LLM_API_BASE")
    try:
        # ══════════════════════════════════════════════════════════════════
        # ① LLM 死端点 → judged=False，零副作用（不抛/不雪崩）
        # ══════════════════════════════════════════════════════════════════
        print("── ① LLM 停机 → judge_violation_via_llm 零副作用 ──")
        os.environ["LLM_API_BASE"] = DEAD_LLM
        info("LLM_API_BASE", DEAD_LLM)

        raised = False
        r1 = None
        try:
            # 对着有数据的真实表调，验证离线时也不抛、不误判
            r1 = judge_violation_via_llm(
                "EXISTS (SELECT 1 FROM data_contracts WHERE 采购方式 != '公开招标')",
                "data_contracts", TEST_PID, "测试违规")
        except Exception as e:
            raised = True
            info("意外抛异常", str(e)[:200])
        check("① LLM 死端点不抛异常（降级而非崩溃）", not raised)
        check("① 返回 judged=False（LLM不可用→不判定）",
              isinstance(r1, dict) and r1.get("judged") is False,
              str(r1)[:160] if r1 else "None")
        check("① hits=0 零副作用（不误造命中）",
              isinstance(r1, dict) and r1.get("hits") == 0)
        info("① 红线", "expression_engine.py 引擎层零 call_llm 依赖（test_p9_t6 守护）；"
             "本函数仅在 build_and_execute planner 层调用，离线零副作用")

        # ══════════════════════════════════════════════════════════════════
        # ② 语法错表达式 + 命中数据 → patch LLM 判命中 → judge_source='llm'
        # ══════════════════════════════════════════════════════════════════
        print("\n── ② 语法错+命中数据 → LLM 降级判命中（planner 集成）──")
        # 先确认夹具表达式触发降级（条件A语法错 或 条件B零命中，二者其一即可）
        from services.expression_engine import execute_expression
        scan_pre = execute_expression(
            "EXISTS (SELECT 1 FROM data_contracts WHERE 采购方式 != '公开招标')",
            "data_contracts", TEST_PID)
        cond_a = scan_pre.get("success") is False and bool(scan_pre.get("error"))
        cond_b = (scan_pre.get("success") and scan_pre.get("hits", 0) == 0
                  and scan_pre.get("total", 0) > 0)
        info("② 夹具表达式引擎扫描", {"success": scan_pre.get("success"),
              "hits": scan_pre.get("hits"), "total": scan_pre.get("total"),
              "error": (scan_pre.get("error") or "")[:60],
              "降级条件": "A语法错" if cond_a else ("B零命中" if cond_b else "未触发")})
        check("② 夹具表达式触发降级条件（语法错或零命中）",
              cond_a or cond_b,
              f"success={scan_pre.get('success')} hits={scan_pre.get('hits')} total={scan_pre.get('total')}")

        # patch judge_violation_via_llm 返确定命中（测 planner 集成，不依赖真实LLM准确率）
        suspect_row = query_one(
            "SELECT id FROM data_contracts WHERE project_id=%s AND procurement_method=%s",
            (TEST_PID, "询价"), database="tt")
        fake_judge = {
            "judged": True, "hits": 1,
            "rows": [{"row_id": suspect_row["id"], "reason": "询价440万应公开招标"}],
            "judged_count": 2, "batches": 1, "circuit_tripped": False, "note": "",
        }
        original_judge = execution_planner.__dict__.get("judge_violation_via_llm")
        # build_and_execute 内部是延迟 import，patch 源模块
        import services.llm_semantic_judge as _judge_mod
        _judge_mod.judge_violation_via_llm = lambda *a, **k: fake_judge

        r2 = execution_planner.build_and_execute([str(TEST_VID)], TEST_PID)
        item = r2[0] if r2 else {}
        info("② build_and_execute 结果", {"judge_source": item.get("judge_source"),
              "hits": item.get("hits"), "table": item.get("table"),
              "executable": item.get("executable")})
        check("② judge_source='llm'（LLM降级判出命中）",
              item.get("judge_source") == "llm", item.get("judge_source"))
        check("② hits>0（判出疑点行）", (item.get("hits") or 0) > 0, f"hits={item.get('hits')}")
        check("② rows 带真实行字段（_build_llm_hit_rows 回查数据）",
              bool(item.get("rows")) and
              isinstance(item.get("rows", [{}])[0], dict) and
              "fields" in item.get("rows", [{}])[0],
              str((item.get("rows") or [{}])[0])[:160])
        llm_reason = ((item.get("rows", [{}])[0].get("fields") or {}).get("_llm_reason"))
        check("② rows 含 _llm_reason（LLM判定理由，前端🤖徽章依据）",
              bool(llm_reason), str(llm_reason)[:120])

        # 还原 patch
        if original_judge is not None:
            _judge_mod.judge_violation_via_llm = original_judge
        else:
            del _judge_mod.judge_violation_via_llm

        # ══════════════════════════════════════════════════════════════════
        # ③ 聚合 needs_review=True → judge_source='manual'，LLM未被调用
        # ══════════════════════════════════════════════════════════════════
        print("\n── ③ 聚合 needs_review=True → 不绕人工门禁（judge_source='manual'）──")
        # patch execute_expression 返 needs_review=True（模拟聚合SQL待人工确认）
        original_exec = execution_planner.execute_expression
        called_judge = {"count": 0}
        fake_aggregate_scan = {
            "success": False, "layer": "aggregate", "needs_review": True,
            "message": "聚合表达式已生成 SQL，待人工确认后执行",
        }

        def _fake_execute(expression, table, project_id, limit=2000):
            return fake_aggregate_scan

        def _spy_judge(*a, **k):
            called_judge["count"] += 1
            return {"judged": False, "hits": 0, "rows": []}

        execution_planner.execute_expression = _fake_execute
        _judge_mod.judge_violation_via_llm = _spy_judge

        r3 = execution_planner.build_and_execute([str(TEST_VID)], TEST_PID)
        item3 = r3[0] if r3 else {}
        info("③ build_and_execute 结果", {"judge_source": item3.get("judge_source"),
              "hits": item3.get("hits"), "needs_review路径": "execute_expression被patch返True"})
        check("③ judge_source='manual'（聚合待审，非rule非llm）",
              item3.get("judge_source") == "manual", item3.get("judge_source"))
        check("③ judge_violation_via_llm 零调用（不绕 Submit→Confirm 人工门禁）",
              called_judge["count"] == 0, f"调用次数={called_judge['count']}")
        info("③ 门禁红线", "needs_review=True 时只标 manual 走人工确认流程，"
             "绝不触发 LLM 自动判命中（防绕过审计法律责任门禁）")

        # 还原 patch
        execution_planner.execute_expression = original_exec
        if original_judge is not None:
            _judge_mod.judge_violation_via_llm = original_judge
        else:
            del _judge_mod.judge_violation_via_llm
    finally:
        # 恢复 env + 清理夹具（不污染后续测试/真实数据）
        if orig_llm is None:
            os.environ.pop("LLM_API_BASE", None)
        else:
            os.environ["LLM_API_BASE"] = orig_llm
        _teardown()

    print(f"\n{'='*50}\nPhase10 改动① LLM 语义降级：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
