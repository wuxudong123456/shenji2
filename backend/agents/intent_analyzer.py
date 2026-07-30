"""意图分析专家 Agent — 方案B 子类

核心原则（纯 NLP 意图理解，不涉及事实判断）:
  步骤1 retrieve: 无（意图解析不需要查知识库，是纯文本理解）
  步骤2 invoke:   无（intent_analyzer 在 agents.yaml 中 mcp_tools 为空）
  步骤3 synthesize: LLM 把自然语言审计意图结构化为字段

特点:
  - 6 个 Agent 中唯一不需要工具调用的（纯脑力理解）
  - 但仍统一走 build_prompt(input_data, context) 接口，保持架构一致
  - 输出携带 trace_id / timing，与其他 Agent 溯源格式统一
"""
from agents.base import BaseAgent, AgentDefinition


class IntentAnalyzerAgent(BaseAgent):
    """意图分析专家 — 纯 LLM 意图结构化"""

    def build_prompt(self, input_data: dict, context: dict) -> str:
        intent = input_data.get("intent") or input_data.get("user_intent") or ""

        lines = [
            "## 审计意图解析任务",
            "",
            "请仔细阅读以下审计人员口述的审计意图，精准提取关键信息。",
            "严格按 System Prompt 中定义的 JSON 格式输出。",
            "",
            "## 审计意图原文",
            intent.strip() if intent else "（用户未输入意图，请输出 confidence=low 并在 missing_info 中提示补充）",
            "",
            "## 提取规则",
            "1. 只提取用户明确提到的信息，不编造、不推测",
            "2. 无法确定的字段返回 null",
            "3. target_level 从国家级/省级/市级/县级/乡级 中判断",
            "4. concerns 为审计关注点列表",
            "5. 若意图信息不足，confidence 设为 low 并填写 missing_info",
        ]
        return "\n".join(lines)
