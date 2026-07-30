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
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from workflow.state import AnalysisState
from agents.registry import AgentRegistry

_registry = AgentRegistry()


# ── 节点函数 ──

def _node_intent_analyzer(state: AnalysisState) -> dict:
    """Step①: 意图分析"""
    agent = _registry.create_agent("intent_analyzer")
    result = agent.run({"intent": state.get("user_intent", "")})

    if not result["success"]:
        return {"errors": [f"IntentAnalyzer失败: {result.get('error')}"], "current_step": 1}

    out = result["output"]
    return {
        "intent_result": out,
        "domain": out.get("domain", ""),
        "audit_item": out.get("item", ""),
        "audit_period": out.get("period", ""),
        "target_level": out.get("target_level", ""),
        "target_unit": out.get("target_unit", ""),
        "current_step": 1,
    }


def _node_violation_matcher(state: AnalysisState) -> dict:
    """Step②-A: 违规模型匹配"""
    agent = _registry.create_agent("violation_matcher")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "target_level": state.get("target_level", ""),
        "target_unit": state.get("target_unit", ""),
    })

    if not result["success"]:
        return {"errors": [f"ViolationMatcher失败: {result.get('error')}"], "current_step": 2}

    out = result["output"]
    violations = out.get("matches", [])
    # 资料推荐由 DataAdvisor 负责，此处不写 recommended_materials（避免并行写冲突+数据类型混乱）
    return {
        "matches": violations,
        "current_step": 2,
    }


def _node_data_advisor(state: AnalysisState) -> dict:
    """Step②-B: 资料顾问"""
    agent = _registry.create_agent("data_advisor")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "matches": state.get("matches", []),
    })

    if not result["success"]:
        return {"errors": [f"DataAdvisor失败: {result.get('error')}"], "current_step": 2}

    out = result["output"]
    return {
        "recommended_materials": out.get("materials", []),
        "current_step": 2,
    }


def _node_regulation_advisor(state: AnalysisState) -> dict:
    """Step②-C: 法规顾问"""
    agent = _registry.create_agent("regulation_advisor")
    result = agent.run({
        "domain": state.get("domain", ""),
        "item": state.get("audit_item", ""),
        "target_level": state.get("target_level", ""),
        "target_unit": state.get("target_unit", ""),
    })

    if not result["success"]:
        return {"errors": [f"RegulationAdvisor失败: {result.get('error')}"], "current_step": 2}

    out = result["output"]
    return {
        "primary_laws": out.get("primary_laws", []),
        "layer_advice": out.get("layer_advice", ""),
        "current_step": 2,
    }


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
        "matches": state.get("matches", []),
        "primary_laws": state.get("primary_laws", []),
        "uploaded_files": state.get("uploaded_files", []),
        "selected_violations": state.get("selected_violations", []),
        "selected_laws": state.get("selected_laws", []),
    })

    if not result["success"]:
        return {"errors": [f"AuditAnalyzer失败: {result.get('error')}"], "current_step": 5}

    out = result["output"]
    return {
        "analysis_results": out.get("analysis_results", []),
        "overall_assessment": out.get("overall_assessment", ""),
        "current_step": 5,
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
    })

    if not result["success"]:
        return {"errors": [f"SuspicionGenerator失败: {result.get('error')}"], "current_step": 6}

    out = result["output"]
    return {
        "suspicion_report": out.get("suspicion_report", {}),
        "current_step": 6,
        "completed_at": "",  # 后续由路由层填入时间戳
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

    # Step① → Step② (三个Agent并行 → 汇总到 step_3)
    workflow.add_edge("step_1_intent", "step_2_violations")
    workflow.add_edge("step_1_intent", "step_2_data_advice")
    workflow.add_edge("step_1_intent", "step_2_regulations")

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
        checkpointer=MemorySaver(),
        interrupt_before=["step_3_confirm", "step_5_analysis"],
    )
