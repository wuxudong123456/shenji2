"""Phase 4.3 — LangGraph 审计分析工作流图

工作流结构:
  Step① IntentAnalyzer (串行)
    → Step② {ViolationMatcher ∥ DataAdvisor ∥ RegulationAdvisor} (并行)
    → Step③ 人工确认断点 (interrupt)
    → Step④ 文件上传+OCR处理
    → Step⑤ AuditAnalyzer (串行)
    → Step⑥ SuspicionGenerator (串行)
    → END

用法:
    from workflow import build_analysis_graph
    graph = build_analysis_graph()
    config = {"configurable": {"thread_id": task_id}}
    state = graph.invoke({"user_intent": "..."}, config)
"""
from typing import Literal
import sqlite3
from pathlib import Path
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from workflow.state import AnalysisState
from agents.registry import AgentRegistry

_registry = AgentRegistry()

# P2-1: 持久化 checkpointer（进程重启不丢分析任务状态）
_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "langgraph_checkpoints.db"
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
_checkpoint_conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)


# ── P8-11 溯源上下文装配 ──
def _trace_ctx(state, step, node_name):
    """构建 Agent.run 的 context，供 BaseAgent._persist_trace 关联 task/project/step/node。

    upstream_trace_ids 取 state 累积链（各节点成功后 append 自身 trace_id），
    best-effort：并行节点间竞态下取到的是已落库的子集，不影响溯源可用性。
    """
    return {
        "task_id": state.get("task_id"),
        "project_id": state.get("project_id"),
        "step": step,
        "node_name": node_name,
        "upstream_trace_ids": state.get("trace_ids", []),
    }


# ── 节点函数 ──

def _node_intent_analyzer(state: AnalysisState) -> dict:
    """Step①: 意图分析（P1.6: 守卫式补全，不空串覆盖 P1.4 注入的 DB 值）"""
    agent = _registry.create_agent("intent_analyzer")
    result = agent.run({"intent": state.get("user_intent", "")},
                       context=_trace_ctx(state, 1, "step_1_intent"))

    if not result["success"]:
        return {"errors": [f"IntentAnalyzer失败: {result.get('error')}"],
                "current_step": 1, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    # P1.6 守卫: LLM 抽到非空才写，空串/None 不覆盖 P1.4 注入的 DB 上下文
    updates = {"intent_result": out, "current_step": 1, "trace_ids": [result["trace_id"]]}
    for llm_key, state_key in [("domain", "domain"), ("item", "audit_item"),
                                ("period", "audit_period"), ("target_level", "target_level"),
                                ("target_unit", "target_unit"), ("concerns", "concerns")]:
        val = out.get(llm_key)
        if val:  # 非空才覆盖（空串/None/空列表保留 DB 值）
            updates[state_key] = val
    return updates


def _node_violation_matcher(state: AnalysisState) -> dict:
    """Step②-A: 违规模型匹配（P1.6: 传concerns + 转换为前端格式）"""
    agent = _registry.create_agent("violation_matcher")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "target_level": state.get("target_level", ""),
        "target_unit": state.get("target_unit", ""),
        "concerns": state.get("concerns", []),  # P1.6: 贯通 concerns（提升召回质量）
    }, context=_trace_ctx(state, 2, "step_2_violations"))

    if not result["success"]:
        return {"errors": [f"ViolationMatcher失败: {result.get('error')}"],
                "current_step": 2, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    raw_matches = out.get("matches", [])
    # P1.6 转换层: LLM 输出 → 前端 violationDB 期望格式（id/name/risk/match/symptom）
    risk_map = {"high": "高", "medium": "中", "low": "低"}
    matches = []
    for i, m in enumerate(raw_matches):
        matches.append({
            "id": m.get("violation_id") or m.get("id") or f"v{i+1}",
            "name": m.get("violation_title", ""),
            "risk": risk_map.get(m.get("risk_level", ""), "中"),
            "match": round(m.get("relevance_score", 0) * 100),
            "symptom": m.get("match_reason", ""),
            "materials": [],
            "regulations": [],
            "key_checkpoints": m.get("key_checkpoints", []),
        })
    return {"matches": matches, "current_step": 2, "trace_ids": [result["trace_id"]]}


def _node_data_advisor(state: AnalysisState) -> dict:
    """Step②-B: 资料顾问"""
    agent = _registry.create_agent("data_advisor")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "matches": state.get("matches", []),
    }, context=_trace_ctx(state, 2, "step_2_data_advice"))

    if not result["success"]:
        return {"errors": [f"DataAdvisor失败: {result.get('error')}"],
                "current_step": 2, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    return {
        "recommended_materials": out.get("materials", []),
        "current_step": 2,
        "trace_ids": [result["trace_id"]],
    }


def _node_regulation_advisor(state: AnalysisState) -> dict:
    """Step②-C: 法规顾问（P1.6: 传matches + 转换为前端格式）"""
    agent = _registry.create_agent("regulation_advisor")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "target_level": state.get("target_level", ""),
        "target_unit": state.get("target_unit", ""),
        "matches": state.get("matches", []),  # P1.6: 传 matches（关联违规模型，P1.7拓扑修正后生效）
    }, context=_trace_ctx(state, 2, "step_2_regulations"))

    if not result["success"]:
        return {"errors": [f"RegulationAdvisor失败: {result.get('error')}"],
                "current_step": 2, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    raw_laws = out.get("primary_laws", [])
    # P1.6 转换层: LLM 输出 → 前端 renderS3 期望格式（law/clause/type/rec）
    laws = []
    for l in raw_laws:
        laws.append({
            "law_id": l.get("law_id", ""),
            "law": l.get("law_title", ""),
            "clause": (l.get("applicable_clauses") or [""])[0],
            "type": l.get("layer_suggestion", "主依据"),
            "rec": True,
        })
    return {"primary_laws": laws, "layer_advice": out.get("layer_advice", ""),
            "current_step": 2, "trace_ids": [result["trace_id"]]}


def _node_human_confirm(state: AnalysisState) -> dict:
    """Step③: 人工确认断点节点

    此节点在 interrupt_before 之前执行，整理确认面板所需的全部信息。
    确认后状态通过 update_state 注入 selected_violations / selected_laws。
    """
    return {"confirmation_status": "pending", "current_step": 3}


def _node_document_processing(state: AnalysisState) -> dict:
    """Step④: 文件上传+OCR处理

    此阶段由外部操作触发（前端上传文件→OCR→入库），
    工作流在此等待所有文件处理完成后继续。
    """
    return {"current_step": 4}


def _node_audit_analyzer(state: AnalysisState) -> dict:
    """Step⑤: 智能分析"""
    agent = _registry.create_agent("audit_analyzer")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "project_id": state.get("project_id", ""),  # P1.6: 补 project_id（step_5 跑空根因修复）
        "matches": state.get("matches", []),
        "primary_laws": state.get("primary_laws", []),
        "uploaded_files": state.get("uploaded_files", []),
        "selected_violations": state.get("selected_violations", []),
        "selected_laws": state.get("selected_laws", []),
    }, context=_trace_ctx(state, 5, "step_5_analysis"))

    if not result["success"]:
        return {"errors": [f"AuditAnalyzer失败: {result.get('error')}"],
                "current_step": 5, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    return {
        "analysis_results": out.get("analysis_results", []),
        "overall_assessment": out.get("overall_assessment", ""),
        "current_step": 5,
        "trace_ids": [result["trace_id"]],
    }


def _node_suspicion_generator(state: AnalysisState) -> dict:
    """Step⑥: 疑点报告生成"""
    agent = _registry.create_agent("suspicion_generator")
    result = agent.run({
        "analysis_results": state.get("analysis_results", []),
        "overall_assessment": state.get("overall_assessment", ""),
        "domain": state.get("domain", ""),
        "audit_item": state.get("audit_item", ""),
        "primary_laws": state.get("primary_laws", []),
    }, context=_trace_ctx(state, 6, "step_6_suspicion"))

    if not result["success"]:
        return {"errors": [f"SuspicionGenerator失败: {result.get('error')}"],
                "current_step": 6, "trace_ids": [result.get("trace_id")]}

    out = result["output"]
    return {
        "suspicion_report": out.get("suspicion_report", {}),
        "current_step": 6,
        "completed_at": "",  # 后续由路由层填入时间戳
        "trace_ids": [result["trace_id"]],
    }


# ── 路由函数 ──

def _route_after_confirm(state: AnalysisState) -> Literal["step_4_upload", "END"]:
    """确认后的路由: 确认通过→继续，拒绝→结束"""
    if state.get("confirmation_status") == "rejected":
        return "END"
    return "step_4_upload"


# ── 图构建 ──

def build_analysis_graph():
    """构建并编译审计分析工作流图

    Returns:
        编译后的 LangGraph StateGraph（含 MemorySaver checkpointer）
    """
    workflow = StateGraph(AnalysisState)

    # 注册节点
    workflow.add_node("step_1_intent", _node_intent_analyzer)
    workflow.add_node("step_2_violations", _node_violation_matcher)
    workflow.add_node("step_2_data_advice", _node_data_advisor)
    workflow.add_node("step_2_regulations", _node_regulation_advisor)
    workflow.add_node("step_3_confirm", _node_human_confirm)
    workflow.add_node("step_4_upload", _node_document_processing)
    workflow.add_node("step_5_analysis", _node_audit_analyzer)
    workflow.add_node("step_6_suspicion", _node_suspicion_generator)

    # 定义边（流程）
    workflow.set_entry_point("step_1_intent")

    # Step① → Step② (P1.7 方案A: ViolationMatcher串行前置 → DataAdvisor/RegulationAdvisor并行读matches)
    workflow.add_edge("step_1_intent", "step_2_violations")
    workflow.add_edge("step_2_violations", "step_2_data_advice")
    workflow.add_edge("step_2_violations", "step_2_regulations")

    # Step② 三个并行节点 → Step③
    workflow.add_edge("step_2_violations", "step_3_confirm")
    workflow.add_edge("step_2_data_advice", "step_3_confirm")
    workflow.add_edge("step_2_regulations", "step_3_confirm")

    # Step③ → Step④ (含条件路由)
    workflow.add_conditional_edges("step_3_confirm", _route_after_confirm, {
        "step_4_upload": "step_4_upload",
        "END": END,
    })

    # Step④ → Step⑤ → Step⑥ → END
    workflow.add_edge("step_4_upload", "step_5_analysis")
    workflow.add_edge("step_5_analysis", "step_6_suspicion")
    workflow.add_edge("step_6_suspicion", END)

    # 编译: 两个人工断点
    #   ① step_3_confirm 前 — 等待用户确认违规模型+法规依据
    #   ② step_5_analysis 前 — 等待用户上传审计资料（取证后再分析）
    return workflow.compile(
        checkpointer=SqliteSaver(_checkpoint_conn),
        interrupt_before=["step_3_confirm", "step_5_analysis"],
    )
