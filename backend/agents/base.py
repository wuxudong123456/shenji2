"""Agent 基类 — 知识优先 + 显式工具调用 + LLM 格式化（方案 B）

执行模型（三段式）:
  步骤1: retrieve  — 查询自有知识库（确定性检索，不依赖 LLM）
  步骤2: invoke    — 显式调用 MCP 工具（代码决定调用哪个，不是 LLM 决定）
  步骤3: synthesize— LLM 仅做结果整理与格式化，不做自主推理判断

每个 Agent 的输出都携带完整溯源信息:
  - trace_id          本次执行的唯一标识
  - source_knowledge  引用了哪些知识库数据（法规/违规 ID 等）
  - tool_call_records 调用了哪些工具、参数、结果、状态
  - timing            各阶段耗时

子类只需覆盖 build_prompt（步骤3 的数据组装），框架自动处理工具调用、
LLM 调用、输出验证、溯源记录与异常处理。

Usage:
    agent = BaseAgent(definition)
    result = agent.run({"intent": "审计某市教育局2026年采购合规性"})
    # result 含 output / trace_id / source_knowledge / tool_call_records
"""
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional, Callable

from services.llm_client import call_llm_json


@dataclass
class AgentDefinition:
    """Agent 定义 — 从 agents.yaml + 数据库加载"""
    agent_id: str                                  # 唯一标识: intent_analyzer
    name: str                                      # 中文名: 意图分析专家
    description: str = ""                          # 角色描述
    model: str = "deepseek-v4-flash"               # LLM 模型
    temperature: float = 0.1                       # 温度参数
    max_tokens: int = 4096                         # 最大输出 token
    system_prompt: str = ""                        # 系统提示词（角色设定）
    output_schema: dict = field(default_factory=dict)        # 输出 JSON Schema
    mcp_tools: list[str] = field(default_factory=list)       # 可用的 MCP 工具白名单
    knowledge_base_ids: list[str] = field(default_factory=list)  # 绑定的知识库标识


# ── 工具调用超时（秒）—— invoke_tool 的安全阀 ──
_TOOL_TIMEOUT_SECONDS = 60


class BaseAgent:
    """审计智能体基类 — 所有 6 个 Agent 的父类

    子类约定:
      - 覆盖 build_prompt(input_data, context) 组装步骤3的 Prompt
      - 在 build_prompt 中通过 self.invoke_tool() 显式调用所需工具
      - 通过 self.add_knowledge_source() 登记引用的知识数据
      - 不要直接调用 call_llm_json，由基类统一调度
    """

    def __init__(self, definition: AgentDefinition):
        self.defn = definition
        # 运行态：每次 run() 重置
        self._tool_logs: list[dict] = []
        self._knowledge_sources: list[dict] = []
        self._last_raw_response: Optional[dict] = None

    # ────────────────────────────────────────────────────────────
    #  核心执行：三段式
    # ────────────────────────────────────────────────────────────

    def run(self, input_data: dict, context: Optional[dict] = None) -> dict:
        """核心执行方法

        Args:
            input_data: 上游输入数据（来自 AnalysisState 或用户输入）
            context: 执行上下文 {task_id, step, upstream_trace_ids} 用于溯源串联

        Returns:
            {
                "success": bool,
                "agent": "intent_analyzer",
                "trace_id": "trace-xxxx",
                "output": {...},                    # LLM 结构化输出
                "source_knowledge": [...],          # 引用的知识来源
                "tool_call_records": [...],         # 工具调用记录
                "timing": {...},                    # 各阶段耗时
                "model": "deepseek-v4-flash",
            }
        """
        ctx = context or {}
        trace_id = ctx.get("trace_id") or f"trace-{uuid.uuid4().hex[:12]}"
        self._reset_runtime()
        self._input_snapshot = input_data  # 供 _persist_trace 落 input_summary

        timing = {"total_ms": 0}
        t_start = time.perf_counter()

        # 步骤1 + 步骤2 + 步骤3 的数据组装（在 build_prompt 内完成工具调用与知识检索）
        try:
            t_prompt = time.perf_counter()
            user_prompt = self.build_prompt(input_data, ctx)
            timing["prompt_build_ms"] = int((time.perf_counter() - t_prompt) * 1000)
        except Exception as e:
            return self._failure(trace_id, ctx, f"Prompt 组装失败: {e}", timing, t_start)

        if not user_prompt or not user_prompt.strip():
            # 无需 LLM 的 Agent（纯检索型）可在此直接返回已收集的结果
            return self._success_no_llm(trace_id, ctx, timing, t_start)

        # 步骤3：LLM 仅做格式化
        try:
            t_llm = time.perf_counter()
            raw = call_llm_json(
                prompt=user_prompt,
                system_prompt=self.defn.system_prompt,
                model=self.defn.model,
                max_tokens=self.defn.max_tokens,
                temperature=self.defn.temperature,
            )
            timing["llm_ms"] = int((time.perf_counter() - t_llm) * 1000)
            self._last_raw_response = raw

            # 记录 LLM 调用日志（推理溯源）
            try:
                from services.audit_logger import log_llm_call
                log_llm_call(self.defn.agent_id, user_prompt, raw,
                             duration_ms=timing["llm_ms"], trace_id=trace_id)
            except Exception:
                pass
        except Exception as e:
            return self._failure(trace_id, ctx, f"LLM 调用异常: {e}", timing, t_start)

        if "error" in raw:
            return self._failure(trace_id, ctx,
                                 f"LLM 返回错误: {raw.get('error')}", timing, t_start,
                                 raw=raw)

        # 输出验证 + 自动修正
        validation = self.validate_output(raw)
        if not validation["valid"]:
            raw = self._auto_fix_output(raw, validation)
            validation = self.validate_output(raw)

        timing["total_ms"] = int((time.perf_counter() - t_start) * 1000)
        result = {
            "success": validation["valid"],
            "agent": self.defn.agent_id,
            "trace_id": trace_id,
            "output": raw,
            "validation_errors": validation.get("errors", []),
            "source_knowledge": self._knowledge_sources,
            "tool_call_records": self._tool_logs,
            "timing": timing,
            "context": ctx,
            "model": self.defn.model,
        }
        self._persist_trace(result)
        return result

    # ────────────────────────────────────────────────────────────
    #  子类钩子：build_prompt
    # ────────────────────────────────────────────────────────────

    def build_prompt(self, input_data: dict, context: dict) -> str:
        """组装步骤3 的 User Prompt — 子类覆盖此方法

        约定:
          - 在此方法内通过 self.invoke_tool() 显式调用所需工具（步骤2）
          - 通过 self.add_knowledge_source() 登记引用的知识（步骤1）
          - 返回拼装好的 Prompt 字符串；返回空串表示无需 LLM
        """
        lines = ["请根据以下输入信息，按要求输出结构化 JSON。", ""]
        lines.append("## 输入信息")
        lines.append(json.dumps(input_data, ensure_ascii=False, indent=2))
        lines.append("")
        lines.append("## 要求")
        lines.append("严格按 System Prompt 中定义的 JSON 格式输出，不得添加额外文字。")
        return "\n".join(lines)

    # ────────────────────────────────────────────────────────────
    #  工具调用：invoke_tool（步骤2 的实现）
    # ────────────────────────────────────────────────────────────

    def invoke_tool(self, tool_name: str, params: dict,
                    timeout: int = _TOOL_TIMEOUT_SECONDS) -> dict:
        """显式调用 MCP 工具 — 代码决定调用，不是 LLM 决定

        具备: 权限校验 / 参数校验 / 异常捕获 / 超时处理 / 结果记录 / 失败回传

        Args:
            tool_name: "knowledge-mcp.search_laws" 格式（server.tool）
            params: 工具参数 dict
            timeout: 超时秒数

        Returns:
            {"success": bool, "result": ..., "error": str|None}
            无论成功失败都返回，便于 build_prompt 内容错处理
        """
        record = {
            "tool": tool_name,
            "params": _safe_serialize(params),
            "success": False,
            "result": None,
            "error": None,
            "duration_ms": 0,
        }
        t0 = time.perf_counter()

        # 1. 权限校验：工具必须在白名单内
        if tool_name not in self.defn.mcp_tools:
            record["error"] = (f"权限拒绝: Agent '{self.defn.agent_id}' "
                               f"未授权工具 '{tool_name}'")
            record["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            self._tool_logs.append(record)
            return {"success": False, "result": None, "error": record["error"]}

        # 2. 解析工具函数
        try:
            from mcp_servers import resolve_tool
            fn = resolve_tool(tool_name)
            if not fn or not callable(fn):
                raise ValueError(f"工具未注册或不可调用: {tool_name}")
        except Exception as e:
            record["error"] = f"工具解析失败: {e}"
            record["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            self._tool_logs.append(record)
            return {"success": False, "result": None, "error": record["error"]}

        # 3. 参数校验 + 执行（含超时）
        if not isinstance(params, dict):
            record["error"] = f"参数必须为 dict, 实际 {type(params).__name__}"
            record["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            self._tool_logs.append(record)
            return {"success": False, "result": None, "error": record["error"]}

        try:
            result = self._call_with_timeout(fn, params, timeout)
            record["success"] = True
            record["result"] = _safe_serialize(result)
        except TimeoutError:
            record["error"] = f"工具执行超时 ({timeout}s): {tool_name}"
        except Exception as e:
            record["error"] = f"工具执行失败: {e}"

        record["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        self._tool_logs.append(record)
        return {"success": record["success"],
                "result": result if record["success"] else None,
                "error": record["error"]}

    def _call_with_timeout(self, fn: Callable, params: dict, timeout: int):
        """带超时地调用工具函数（同步工具用线程兜底）"""
        if timeout <= 0:
            return fn(**params)

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(fn, **params)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                raise TimeoutError(f"超过 {timeout}s")

    # ────────────────────────────────────────────────────────────
    #  知识来源登记（步骤1 的溯源标记）
    # ────────────────────────────────────────────────────────────

    def add_knowledge_source(self, source: str, item_type: str,
                             item_id, snippet: str = "") -> None:
        """登记一条知识来源，供溯源链使用

        Args:
            source: 来源标识（如 "audit_law.sys_core_law_allaudit"）
            item_type: 数据类型（"law" / "violation" / "case" / "data_row"）
            item_id: 数据 ID
            snippet: 摘要文本（法规标题/违规名称等）
        """
        self._knowledge_sources.append({
            "source": source,
            "type": item_type,
            "id": item_id,
            "snippet": snippet[:200] if snippet else "",
        })

    # ────────────────────────────────────────────────────────────
    #  输出验证
    # ────────────────────────────────────────────────────────────

    def validate_output(self, output: dict) -> dict:
        """验证输出是否符合 output_schema（轻量：required + enum）"""
        schema = self.defn.output_schema
        if not schema:
            return {"valid": True, "errors": []}

        errors = []
        for req in schema.get("required", []):
            if req not in output:
                errors.append(f"缺少必填字段: {req}")

        for fname, prop in schema.get("properties", {}).items():
            if fname in output and "enum" in prop:
                if output[fname] not in prop["enum"]:
                    errors.append(
                        f"字段 {fname} 值 '{output[fname]}' 不在允许范围 {prop['enum']}"
                    )
        return {"valid": len(errors) == 0, "errors": errors}

    def _auto_fix_output(self, output: dict, validation: dict) -> dict:
        """自动修正常见 LLM 输出字段名偏差"""
        schema = self.defn.output_schema
        if not schema:
            return output
        aliases = {
            "suspicion_report": ["suspicion_points", "findings", "report", "items"],
            "matches": ["violations", "results", "violation_matches"],
            "analysis_results": ["results", "analysis", "findings"],
            "primary_laws": ["laws", "regulations", "recommendations"],
        }
        for req in schema.get("required", []):
            if req not in output:
                for alias in aliases.get(req, []):
                    if alias in output:
                        val = output.pop(alias)
                        if isinstance(val, list) and req == "suspicion_report":
                            output[req] = {
                                "report_title": "审计疑点报告",
                                "total_suspicions": len(val),
                                "high_risk_count": 0,
                                "medium_risk_count": len(val),
                                "low_risk_count": 0,
                                "items": val,
                            }
                        else:
                            output[req] = val
                        break
        return output

    # ────────────────────────────────────────────────────────────
    #  内部工具
    # ────────────────────────────────────────────────────────────

    def _reset_runtime(self) -> None:
        self._tool_logs = []
        self._knowledge_sources = []
        self._last_raw_response = None
        self._input_snapshot = None

    def _failure(self, trace_id, ctx, error, timing, t_start, raw=None) -> dict:
        timing["total_ms"] = int((time.perf_counter() - t_start) * 1000)
        result = {
            "success": False,
            "agent": self.defn.agent_id,
            "trace_id": trace_id,
            "error": error,
            "output": raw or {},
            "source_knowledge": self._knowledge_sources,
            "tool_call_records": self._tool_logs,
            "timing": timing,
            "context": ctx,
            "model": self.defn.model,
        }
        self._persist_trace(result)
        return result

    def _success_no_llm(self, trace_id, ctx, timing, t_start) -> dict:
        """无需 LLM 的纯检索型 Agent 的成功返回"""
        timing["total_ms"] = int((time.perf_counter() - t_start) * 1000)
        result = {
            "success": True,
            "agent": self.defn.agent_id,
            "trace_id": trace_id,
            "output": {},
            "source_knowledge": self._knowledge_sources,
            "tool_call_records": self._tool_logs,
            "timing": timing,
            "context": ctx,
            "model": "none",
        }
        self._persist_trace(result)
        return result

    # ────────────────────────────────────────────────────────────
    #  溯源落库（P8-11）：把本次执行写入 audit_agent_traces
    # ────────────────────────────────────────────────────────────

    def _persist_trace(self, result: dict) -> None:
        """把本次 Agent 执行写入 audit_agent_traces（best-effort）。

        从 result + self._input_snapshot + ctx 提取全列：
          trace_id/task_id/project_id/agent_id/agent_name/step/node_name/
          upstream_trace_ids/input_summary/output_summary/knowledge_sources/
          tool_call_records/llm_raw_response/validation_errors/duration_ms/
          status/error_message/model。
        整体 try/except——落库失败只 log，绝不影响 run() 返回值（溯源不阻塞业务）。
        task_id/project_id/step/node_name 来自 context（graph 节点装配，见 graph.py）。
        """
        try:
            ctx = result.get("context") or {}
            timing = result.get("timing") or {}
            from services.db import insert
            insert(
                "INSERT INTO audit_agent_traces "
                "(trace_id, task_id, project_id, agent_id, agent_name, "
                " step, node_name, upstream_trace_ids, input_summary, output_summary, "
                " knowledge_sources, tool_call_records, llm_raw_response, "
                " validation_errors, duration_ms, status, error_message, model) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    result.get("trace_id"),
                    ctx.get("task_id"),
                    ctx.get("project_id"),
                    self.defn.agent_id,
                    self.defn.name,
                    ctx.get("step"),
                    ctx.get("node_name"),
                    json.dumps(ctx.get("upstream_trace_ids") or [], ensure_ascii=False),
                    json.dumps(_safe_serialize(self._input_snapshot, max_str=4000),
                               ensure_ascii=False),
                    json.dumps(_safe_serialize(result.get("output"), max_str=4000),
                               ensure_ascii=False),
                    json.dumps(result.get("source_knowledge") or [], ensure_ascii=False),
                    json.dumps(result.get("tool_call_records") or [], ensure_ascii=False),
                    json.dumps(self._last_raw_response, ensure_ascii=False)
                        if self._last_raw_response is not None else None,
                    json.dumps(result.get("validation_errors") or [], ensure_ascii=False)
                        if result.get("validation_errors") else None,
                    timing.get("total_ms", 0),
                    "success" if result.get("success") else "failed",
                    result.get("error"),
                    result.get("model"),
                ),
                database="tt",
            )
        except Exception as e:
            # 溯源落库失败不阻塞业务流程，仅记录到 stdout（best-effort）
            print(f"[base._persist_trace] 落库失败(best-effort忽略): {e}")

    @property
    def last_raw_response(self) -> Optional[dict]:
        """获取最后一次 LLM 原始响应（用于溯源）"""
        return self._last_raw_response


# ── 序列化助手：确保溯源记录可 JSON 化 ──

# ── 事项字段渲染助手（agent prompt 用）──

def fmt_list(v, limit=8):
    """把聚焦事项的 JSON 字段渲染成紧凑一行文本（兼容 str/list/dict，截断防 token 爆炸）。

    供 Step②/⑤ Agent 的 build_prompt 把 focus_item 的 common_violations/
    required_materials/legal_bases/audit_methods/common_problems 拼进 prompt。
    元素为 dict 时取 name/title/首个字符串值；返回空串表示无内容。
    """
    if not v:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        v = v.get("items") or v.get("list") or list(v.values())
    if not isinstance(v, list):
        return str(v)
    parts = []
    for it in v[:limit]:
        if isinstance(it, str):
            s = it.strip()
        elif isinstance(it, dict):
            s = (it.get("name") or it.get("title")
                 or next((x for x in it.values() if isinstance(x, str)), ""))
        else:
            s = str(it)
        if s:
            parts.append(s)
    return "；".join(parts)


def elt_text(v):
    """取单个事项字段元素的可读文本（str 直取；dict 取 name/title/首字符串）。"""
    if not v:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, dict):
        return (v.get("name") or v.get("title")
                or next((x for x in v.values() if isinstance(x, str)), ""))
    return str(v)


def _safe_serialize(obj, max_str: int = 4000) -> object:
    """把任意对象转为可 JSON 序列化的形式（截断超长字符串/列表）"""
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return obj[:max_str] if len(obj) > max_str else obj
    if isinstance(obj, dict):
        return {str(k): _safe_serialize(v, max_str) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        if len(obj) > 50:
            return [_safe_serialize(x, max_str) for x in obj[:50]] + \
                   [{"_truncated": f"共{len(obj)}项，已截断"}]
        return [_safe_serialize(x, max_str) for x in obj]
    # 其他对象转字符串
    try:
        s = str(obj)
        return s[:max_str] if len(s) > max_str else s
    except Exception:
        return "<unserializable>"
