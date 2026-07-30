"""AuditWorkbench — Agent 多智能体系统

Phase 3: 6个AI审计智能体，按7步分析向导编排。
每个Agent封装LLM调用、Prompt模板渲染、输出Schema验证。
"""
from agents.base import BaseAgent
from agents.registry import AgentRegistry

__all__ = ["BaseAgent", "AgentRegistry"]
