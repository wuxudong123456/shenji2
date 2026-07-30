"""Agent 基类 — 封装 LLM 调用 + 输出验证

每个 Agent 的生命周期:
  1. 从 AgentRegistry 加载定义
  2. 渲染 System Prompt + User Prompt
  3. 调用 LLM (call_llm_json)
  4. 验证输出符合 output_schema
  5. 返回结构化结果
"""
import json
from dataclasses import dataclass, field
from typing import Optional
from services.llm_client import call_llm_json


@dataclass
class AgentDefinition:
    """Agent 定义 — 从 agents.yaml 加载"""
    agent_id: str                          # 唯一标识: intent_analyzer
    name: str                              # 中文名: 意图分析专家
    description: str = ""                  # 角色描述
    model: str = "deepseek-v4-flash"       # LLM 模型
    temperature: float = 0.1               # 温度参数
    max_tokens: int = 4096                 # 最大输出 token
    system_prompt: str = ""                # 系统提示词
    output_schema: dict = field(default_factory=dict)   # 输出 JSON Schema
    mcp_tools: list[str] = field(default_factory=list)  # 可用的 MCP 工具列表


class BaseAgent:
    """Agent 基类 — 所有审计智能体的父类

    子类只需覆盖 prompt_builder 方法来自定义 Prompt 组装逻辑，
    框架自动处理 LLM 调用和输出验证。

    Usage:
        agent = BaseAgent(definition)
        result = agent.run({"intent": "审计某市教育局2026年采购合规性"})
    """

    def __init__(self, definition: AgentDefinition):
        self.defn = definition
        self._last_raw_response: Optional[dict] = None

    def run(self, input_data: dict) -> dict:
        """核心执行方法

        Args:
            input_data: 上游输入数据（来自 AnalysisState 或用户输入）

        Returns:
            {
                "success": True/False,
                "agent": "intent_analyzer",
                "output": {...},        # 结构化输出（符合 output_schema）
                "raw_response": {...},  # LLM 原始响应（用于溯源）
                "model": "deepseek-v4-flash",
                "tokens_used": 1234,    # 估算 token
            }
        """
        # 1. 构建 User Prompt
        user_prompt = self.build_prompt(input_data)

        # 2. 调用 LLM
        raw = call_llm_json(
            prompt=user_prompt,
            system_prompt=self.defn.system_prompt,
            model=self.defn.model,
            max_tokens=self.defn.max_tokens,
            temperature=self.defn.temperature,
        )
        self._last_raw_response = raw

        # 3. 检查 LLM 错误
        if "error" in raw:
            return {
                "success": False,
                "agent": self.defn.agent_id,
                "error": raw.get("error", "LLM调用失败"),
                "raw_response": raw,
                "model": self.defn.model,
            }

        # 4. 输出验证 + 自动修正
        validation = self.validate_output(raw)
        if not validation["valid"]:
            raw = self._auto_fix_output(raw, validation)
            validation = self.validate_output(raw)

        return {
            "success": validation["valid"],
            "agent": self.defn.agent_id,
            "output": raw,
            "validation_errors": validation.get("errors", []),
            "raw_response": raw,
            "model": self.defn.model,
        }

    def build_prompt(self, input_data: dict) -> str:
        """构建 User Prompt — 子类可覆盖

        默认：将 input_data 序列化为 JSON，附带简洁指令。
        """
        lines = ["请根据以下输入信息，按要求输出结构化 JSON。", ""]
        lines.append("## 输入信息")
        lines.append(json.dumps(input_data, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("## 要求")
        lines.append("严格按 System Prompt 中定义的 JSON 格式输出，不得添加额外文字。")
        return "\n".join(lines)

    def validate_output(self, output: dict) -> dict:
        """验证输出是否符合 output_schema

        当前为轻量验证（检查 required 字段是否存在），
        未来可升级为完整的 JSON Schema 验证（jsonschema 库）。
        """
        schema = self.defn.output_schema
        if not schema:
            return {"valid": True, "errors": []}

        errors = []

        # 检查 required 字段
        required = schema.get("required", [])
        for field in required:
            if field not in output:
                errors.append(f"缺少必填字段: {field}")

        # 检查 enum 约束
        properties = schema.get("properties", {})
        for field, prop in properties.items():
            if field in output and "enum" in prop:
                if output[field] not in prop["enum"]:
                    errors.append(
                        f"字段 {field} 值 '{output[field]}' 不在允许范围 {prop['enum']} 内"
                    )

        return {"valid": len(errors) == 0, "errors": errors}

    def _auto_fix_output(self, output: dict, validation: dict) -> dict:
        """自动修正常见 LLM 输出格式错误

        例如 LLM 返回 suspicion_points 但 Schema 要求 suspicion_report。
        """
        schema = self.defn.output_schema
        if not schema:
            return output
        required = schema.get("required", [])

        for field in required:
            if field not in output:
                aliases = {
                    "suspicion_report": ["suspicion_points", "findings", "report", "items"],
                    "matches": ["violations", "results", "violation_matches"],
                    "analysis_results": ["results", "analysis", "findings"],
                    "primary_laws": ["laws", "regulations", "recommendations"],
                }
                for alias in aliases.get(field, []):
                    if alias in output:
                        val = output.pop(alias)
                        # 如果别名值是数组但期望是对象，包装
                        if isinstance(val, list) and field == "suspicion_report":
                            output[field] = {
                                "report_title": "审计疑点报告",
                                "total_suspicions": len(val),
                                "high_risk_count": 0,
                                "medium_risk_count": len(val),
                                "low_risk_count": 0,
                                "items": val,
                            }
                        elif isinstance(val, list) and field == "analysis_results":
                            output[field] = val
                        else:
                            output[field] = val
                        break

        return output

    @property
    def last_raw_response(self) -> Optional[dict]:
        """获取最后一次 LLM 原始响应（用于溯源）"""
        return self._last_raw_response
