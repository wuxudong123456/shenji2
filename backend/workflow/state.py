"""Phase 4.3 — LangGraph 共享状态定义

AnalysisState 在工作流6个节点+2个人工确认断点之间传递。
遵循"引用而非全量"原则：Agent间传递ID引用，下游通过Service实时查询。
"""
from typing import TypedDict, Optional, Annotated
from operator import add


def _last(prev, new):
    """后写覆盖 reducer — 用于并行节点同写同一键（如 current_step）。
    并行批中各节点写相同值，取最后一个即可；None 保留前值。"""
    return new if new is not None else prev


class AnalysisState(TypedDict, total=False):
    """审计分析工作流共享状态

    字段分组：
      - 输入: 用户输入+意图解析结果
      - Step1: IntentAnalyzer 输出
      - Step2: 并行Agent (ViolationMatcher ∥ DataAdvisor ∥ RegulationAdvisor) 输出
      - Step3: 人工确认后的依据选择
      - Step4: 文件上传+OCR结果
      - Step5: AuditAnalyzer 分析结果
      - Step6: SuspicionGenerator 疑点报告
    """

    # ── 基础标识 ──
    task_id: str                          # 分析任务ID (audit_analysis_tasks.id)
    project_id: str                       # 关联项目ID
    session_id: str                       # 对话session_id

    # ── Step①: 意图输入 ──
    user_intent: str                      # 用户输入的原始审计意图
    intent_result: dict                   # IntentAnalyzer 结构化输出
    domain: str                           # 审计领域
    audit_item: str                       # 审计事项
    audit_period: str                     # 审计期间
    target_level: str                     # 被审计对象层级
    target_unit: str                      # 被审计单位

    # ── Step②: Agent并行推荐 ──
    # 违规匹配
    matches: Annotated[list[dict], add]   # ViolationMatcher 匹配的违规模型列表
    recommended_materials: Annotated[list[dict], add]  # DataAdvisor 推荐的资料清单（并行合并）
    # 法规推荐
    primary_laws: Annotated[list[dict], add]  # RegulationAdvisor 推荐的主法列表
    layer_advice: str                     # 法规层级适用建议

    # ── Step③: 人工确认 ──
    selected_violations: list[str]        # 已确认的违规模型ID列表
    selected_laws: list[str]              # 已确认的法规ID列表
    custom_regulations: list[dict]        # 用户自定义补充法规
    confirmation_status: str              # pending / confirmed / rejected

    # ── Step④: 资料分析 ──
    uploaded_files: list[dict]            # 上传文件列表 [{name, minio_path, trace_id}]
    ocr_results: list[dict]               # OCR解析结果
    extracted_tables: list[str]           # 数据工坊中生成的表名列表

    # ── Step⑤: 智能分析 ──
    analysis_results: list[dict]          # AuditAnalyzer 逐模型分析结果
    overall_assessment: str               # 整体评估

    # ── Step⑥: 疑点报告 ──
    suspicion_report: dict                # SuspicionGenerator 生成的疑点报告

    # ── 元数据 ──
    current_step: Annotated[int, _last]   # 当前步骤 (1-6) — 并行节点用_last合并
    errors: Annotated[list[str], add]     # 累积错误信息
    completed_at: str                     # 完成时间
