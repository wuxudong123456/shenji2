r"""Phase9 T6 LLM 停机降级验收

§3.5 / §6.6：LLM 停机时（/api/llm/health 不可用），降级路径提示「非 AI 推理」
      （规则结果仍可用），不白屏/不抛 500。§7：「降级路径分散在各 Phase | T6 需逐一
      验证各 Phase 降级点不白屏」——本测试逐一验证各降级点。

降级架构（代码核查，faithful-mode）：
  ✅ LLM 客户端降级：call_llm_json（llm_client.py:79-101）任何异常 → 返回 {"error":...}
     dict，不 raise（连接拒绝/超时/非 200/JSON 解析失败统一兜底）。
  ✅ Agent 降级：base.py:133-136 `if "error" in raw: return self._failure(...)`；_failure
     （:348-363）返回结构化 {success:False, error:"LLM 返回错误..."}，不抛异常（=不 500）。
     _persist_trace best-effort（:394 整体 try/except，落库失败只 log）。
  ✅ Step5 规则扫描 LLM 无关：audit_analyzer._scan_expression 走 invoke_tool(
     "expression-mcp.execute_expression")，grep 确认 audit_analyzer.py 零 call_llm import
     ——expression_engine.execute_expression 纯 DB+Python，无 LLM。LLM 停机时规则命中仍产出。
  ✅ Step7 文书降级：document_export_service._fallback_report（:204-221）report 占位
     「（AI 推理暂不可用，已回退到分析摘要）」；_safe_batch_generate（:224-245）整体 try/except，
     逐项降级，"导出永不因 LLM 而整批失败"。

LLM 停机模拟：本测试进程内 os.environ["LLM_API_BASE"]=死端口（127.0.0.1:1），直接调用
call_llm_json / Agent.run（同进程，env 即时生效）。不影响旁路运行的后端进程（隔离）。

本测试（4 降级点，对应 §6.6 断言）：
  ① call_llm_json 死端点 → error dict（不抛）——LLM 客户端层不白屏。
  ② execute_expression 命中（Step5 规则结果可用，LLM 无关）——§6.6「规则可执行步骤仍出结果」。
  ③ _fallback_report 占位「AI 推理暂不可用」——§6.6「LLM 步骤降级提示非 AI 推理」。
  ④ Agent.run() 死端点 → 结构化 success=False（不抛=不 500）——§6.6「不白屏/不 500」。

用法：cd backend && python tests\test_p9_t6_llm_down.py
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.llm_client import call_llm_json, health as llm_health  # noqa: E402
from services.expression_engine import execute_expression  # noqa: E402
from services.document_export_service import _fallback_report  # noqa: E402
from services.db import execute, insert  # noqa: E402
from agents.base import BaseAgent, AgentDefinition  # noqa: E402

BASE = "http://127.0.0.1:5000"
DEAD_LLM = "http://127.0.0.1:1/v1"  # 端口 1 无监听 = LLM 停机（连接拒绝）
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
    if len(s) > 220:
        s = s[:220] + "…"
    print(f"    ℹ️ {label} = {s}")


class _StubAgent(BaseAgent):
    """桩 Agent：build_prompt 返回非空串强制走 LLM 路径，隔离 Agent 依赖。"""
    def build_prompt(self, input_data, context):
        return "ping"


def main():
    global PASS, FAIL
    print("[test] Phase9 T6 LLM 停机降级（不白屏/不 500）\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)
    info("LLM 健康基线", f"health={llm_health()}（基线，后续进程内切死端点模拟停机）")

    orig_llm = os.environ.get("LLM_API_BASE")
    try:
        # ══════════════════════════════════════════════════════════════════
        # Block A：LLM 停机模拟（进程内切死端点）—— ① 客户端 + ④ Agent
        # ══════════════════════════════════════════════════════════════════
        os.environ["LLM_API_BASE"] = DEAD_LLM
        print("── Block A：LLM 停机模拟（LLM_API_BASE=%s）──" % DEAD_LLM)

        # ── ① call_llm_json 死端点 → error dict（不抛）──
        print("  〔① LLM 客户端降级：死端点不抛异常〕")
        raised = False
        r1 = None
        try:
            r1 = call_llm_json(prompt="ping", system_prompt="x", timeout=8)
        except Exception as e:
            raised = True
            info("意外抛异常", str(e)[:200])
        check("① call_llm_json 死端点不抛异常（降级而非崩溃）", not raised)
        check("① 返回 error dict（结构化错误，非白屏）",
              isinstance(r1, dict) and "error" in r1, str(r1)[:160] if r1 else "None")

        # ── ④ Agent.run() 死端点 → 结构化 success=False（不 500/不抛）──
        print("  〔④ Agent 降级：LLM 失败→结构化 failure，不 500〕")
        stub = _StubAgent(AgentDefinition(agent_id="t6_stub", name="T6桩Agent"))
        raised = False
        res = None
        try:
            res = stub.run({"x": 1}, context={"task_id": "T6LLMDOWN_STUB"})
        except Exception as e:
            raised = True
            info("意外抛异常", str(e)[:200])
        check("④ Agent.run() LLM 停机不抛异常（=HTTP 不 500）", not raised)
        check("④ 返回结构化 success=False（非崩溃）",
              isinstance(res, dict) and res.get("success") is False, str(res)[:160] if res else "None")
        check("④ failure 含 LLM 错误提示",
              "LLM" in (res.get("error") or "") if res else False,
              res.get("error") if res else "")
        # 清理桩 trace（_persist_trace best-effort 可能落了一行）
        try:
            execute("DELETE FROM audit_agent_traces WHERE task_id=%s",
                    ("T6LLMDOWN_STUB",), database="tt")
        except Exception:
            pass
    finally:
        # 恢复 env（不污染后续进程/其他测试）
        if orig_llm is None:
            os.environ.pop("LLM_API_BASE", None)
        else:
            os.environ["LLM_API_BASE"] = orig_llm

    # ══════════════════════════════════════════════════════════════════
    # ② Step5 规则扫描 LLM 无关（execute_expression 命中）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ② Step5 规则扫描 LLM 无关（规则结果可用）──")
    pid = "T6LLMDOWN_TEST"
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (pid, "T6 LLM停机测试"), database="tt")
    insert("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
           "VALUES (%s,%s,%s,%s)", (pid, "T6-合规行", 8000000, "公开招标"), database="tt")
    insert("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
           "VALUES (%s,%s,%s,%s)", (pid, "T6-疑点行", 3000000, "询价"), database="tt")
    # expression_engine 纯 DB+Python，无 LLM —— LLM 停机仍能扫出命中
    scan = execute_expression('金额 >= 2000000 AND 采购方式 != "公开招标"', "data_contracts", pid)
    info("扫描结果（LLM 无关）", {"success": scan.get("success"), "hits": scan.get("hits")})
    check("② execute_expression success（纯规则，不依赖 LLM）", scan.get("success") is True)
    check("② 规则命中疑点行（询价 300万，≥200万）", scan.get("hits") == 1,
          f"hits={scan.get('hits')}")
    info("② 静态确认",
         "audit_analyzer._scan_expression 走 invoke_tool('expression-mcp.execute_expression')，"
         "audit_analyzer.py grep 零 call_llm import——扫描路径 LLM 无关，停机时规则结果仍可用")

    # ══════════════════════════════════════════════════════════════════
    # ③ Step7 文书降级（_fallback_report 两级回退）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ③ Step7 文书降级（_fallback_report：有摘要回退摘要 / 无摘要占位非 AI 推理）──")
    # ③a 有 analysis_summary → 回退到摘要（优雅降级，用可得非 LLM 数据）
    ctx_a = {
        "project_title": "T6测试项目",
        "analysis_summary": "T6 分析摘要：发现 1 项采购程序疑点。",
        "suspicions": [{"title": "T6-询价超限额", "severity": "high"}],
    }
    doc_a = _fallback_report(ctx_a)
    info("③a 有摘要→降级 report", {"summary": (doc_a.get("summary") or "")[:60]})
    check("③a 有 analysis_summary → summary 用摘要（优雅降级，不丢可得数据）",
          doc_a.get("summary") == ctx_a["analysis_summary"], (doc_a.get("summary") or "")[:60])
    check("③a 降级 report 仍带 suspicions + recommendations",
          bool(doc_a.get("suspicions")) and bool(doc_a.get("recommendations")))
    # ③b 无 analysis_summary → 占位「AI 推理暂不可用」（§3.5 降级提示「非 AI 推理」）
    ctx_b = {"project_title": "T6测试项目", "suspicions": [{"title": "T6-询价超限额"}]}
    doc_b = _fallback_report(ctx_b)
    info("③b 无摘要→降级 report", {"summary": doc_b.get("summary")})
    check("③b 无 analysis_summary → summary 占位「AI 推理暂不可用」（§3.5 非 AI 推理提示）",
          "AI 推理暂不可用" in (doc_b.get("summary") or ""), doc_b.get("summary"))
    info("③ _safe_batch_generate",
         "document_export_service._safe_batch_generate(:224) 整体 try/except 包 batch_generate，"
         "report 失败→_fallback_report 占位；逐项降级，导出永不因 LLM 整批失败（不白屏）")

    # 清理
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    print(f"\n{'='*50}\nPhase9 T6 LLM 停机降级：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
