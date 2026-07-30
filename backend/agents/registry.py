"""Agent 注册表 — 从 agents.yaml + audit_agents 表加载所有 Agent 定义

提供:
  - 按 agent_id 获取定义
  - 列出所有 Agent（YAML + DB 合并，DB 优先）
  - 创建 Agent 实例（自动查找子类）
  - DB 持久化（save / delete）

Usage:
    registry = AgentRegistry()
    agent = registry.create_agent("intent_analyzer")
    result = agent.run({"intent": "..."})
"""
import json
import yaml
import importlib
from pathlib import Path
from typing import Optional

from agents.base import AgentDefinition, BaseAgent


# ── Agent ID → 子类的映射 ──
# 键: agent_id（与 agents.yaml 中的 key 一致）
# 值: "module.ClassName" 的导入路径
_SUBCLASS_MAP: dict[str, str] = {
    "audit_analyzer": "agents.audit_analyzer.AuditAnalyzerAgent",
}


class AgentRegistry:
    """Agent 注册中心

    单例模式。数据来源优先级: DB > YAML > 默认值
    首次访问时从 YAML + MySQL 懒加载全部定义。
    """

    _instance: Optional["AgentRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
            cls._instance._definitions: dict[str, AgentDefinition] = {}
        return cls._instance

    # ── 加载逻辑 ──

    def _load(self):
        """从 YAML + MySQL 加载所有 Agent 定义（DB 覆盖 YAML 同名字段）"""
        if self._loaded:
            return

        # 1. 先从 YAML 加载
        yaml_path = Path(__file__).parent / "agents.yaml"
        if yaml_path.exists():
            with open(yaml_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            agents_data = data.get("agents", {})
            for agent_id, cfg in agents_data.items():
                self._definitions[agent_id] = AgentDefinition(
                    agent_id=agent_id,
                    name=cfg.get("name", agent_id),
                    description=cfg.get("description", ""),
                    model=cfg.get("model", "deepseek-v4-flash"),
                    temperature=cfg.get("temperature", 0.1),
                    max_tokens=cfg.get("max_tokens", 4096),
                    system_prompt=cfg.get("system_prompt", ""),
                    output_schema=cfg.get("output_schema", {}),
                    mcp_tools=cfg.get("mcp_tools", []),
                )

        # 2. 从 DB 加载（覆盖 YAML 中的同名字段）
        db_agents = self.load_from_db()
        for db_def in db_agents:
            if db_def.agent_id in self._definitions:
                # 合并: DB 字段覆盖 YAML（保留 YAML 中的 output_schema 和 temperature）
                existing = self._definitions[db_def.agent_id]
                if db_def.name:
                    existing.name = db_def.name
                if db_def.description:
                    existing.description = db_def.description
                if db_def.model:
                    existing.model = db_def.model
                if db_def.system_prompt:
                    existing.system_prompt = db_def.system_prompt
                if db_def.mcp_tools:
                    existing.mcp_tools = db_def.mcp_tools
            else:
                # DB 独有的 Agent（纯 DB 定义）
                self._definitions[db_def.agent_id] = db_def

        self._loaded = True

    def reload(self):
        """强制重新加载（YAML 或 DB 变更后使用）"""
        self._loaded = False
        self._definitions.clear()
        self._load()

    # ── DB 持久化 ──

    def load_from_db(self) -> list[AgentDefinition]:
        """从 audit_agents 表加载 Agent 定义

        Returns:
            AgentDefinition 列表（仅 is_active=1 的记录）
        """
        try:
            from services.db import query
            rows = query(
                "SELECT name, display_name, role, system_prompt, model, tools, is_active "
                "FROM audit_agents WHERE is_active = 1 ORDER BY id",
                database="tt",
            )
        except Exception:
            return []

        definitions = []
        for r in rows:
            name = r.get("name", "")
            if not name:
                continue
            tools = r.get("tools")
            if isinstance(tools, str):
                try:
                    tools = json.loads(tools)
                except json.JSONDecodeError:
                    tools = []
            elif tools is None:
                tools = []

            definitions.append(AgentDefinition(
                agent_id=name,
                name=r.get("display_name", name),
                description=r.get("role", ""),
                model=r.get("model", "deepseek-v4-flash"),
                temperature=0.1,
                max_tokens=4096,
                system_prompt=r.get("system_prompt", ""),
                output_schema={},
                mcp_tools=tools if isinstance(tools, list) else [],
            ))
        return definitions

    def save_to_db(self, agent_id: str) -> bool:
        """将当前 Agent 定义持久化到 audit_agents 表（UPSERT）"""
        definition = self.get_definition(agent_id)
        if not definition:
            return False

        try:
            from services.db import execute, query_one

            tools_json = json.dumps(definition.mcp_tools, ensure_ascii=False)

            existing = query_one(
                "SELECT id FROM audit_agents WHERE name = %s",
                (agent_id,), database="tt",
            )

            if existing:
                execute(
                    "UPDATE audit_agents SET display_name=%s, role=%s, system_prompt=%s, "
                    "model=%s, tools=%s, updated_at=NOW() WHERE name=%s",
                    (definition.name, definition.description, definition.system_prompt,
                     definition.model, tools_json, agent_id),
                    database="tt",
                )
            else:
                from services.db import insert
                insert(
                    "INSERT INTO audit_agents (name, display_name, role, system_prompt, "
                    "model, tools, is_active, is_system, created_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,1,1,NOW())",
                    (agent_id, definition.name, definition.description,
                     definition.system_prompt, definition.model, tools_json),
                    database="tt",
                )
            return True
        except Exception:
            return False

    def delete_from_db(self, agent_id: str) -> bool:
        """从 audit_agents 表软删除 Agent（设置 is_active=0）

        仅删除 DB 中的记录；YAML 定义的 Agent 不受影响。
        """
        try:
            from services.db import execute
            affected = execute(
                "UPDATE audit_agents SET is_active = 0, updated_at = NOW() WHERE name = %s",
                (agent_id,), database="tt",
            )
            return affected > 0
        except Exception:
            return False

    # ── 查询 ──

    def get_definition(self, agent_id: str) -> Optional[AgentDefinition]:
        """获取 Agent 定义"""
        self._load()
        return self._definitions.get(agent_id)

    def list_agents(self) -> list[dict]:
        """列出所有 Agent（摘要信息，含来源标记）"""
        self._load()
        # 标记每个 Agent 的来源（YAML / DB / both）
        yaml_ids = self._get_yaml_agent_ids()
        result = []
        for aid, d in self._definitions.items():
            source = "both" if aid in yaml_ids else "db"
            if aid in yaml_ids and not self._has_db_record(aid):
                source = "yaml"
            result.append({
                "agent_id": aid,
                "name": d.name,
                "description": d.description,
                "model": d.model,
                "mcp_tools": d.mcp_tools,
                "source": source,
            })
        return result

    def _get_yaml_agent_ids(self) -> set[str]:
        """获取 YAML 中定义的 Agent ID 集合"""
        yaml_path = Path(__file__).parent / "agents.yaml"
        if not yaml_path.exists():
            return set()
        with open(yaml_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return set(data.get("agents", {}).keys())

    def _has_db_record(self, agent_id: str) -> bool:
        """检查 audit_agents 表中是否有此 Agent 的记录"""
        try:
            from services.db import query_one
            row = query_one(
                "SELECT id FROM audit_agents WHERE name = %s AND is_active = 1",
                (agent_id,), database="tt",
            )
            return row is not None
        except Exception:
            return False

    # ── 实例化 ──

    def create_agent(self, agent_id: str) -> BaseAgent:
        """创建 Agent 实例

        优先使用子类（如果 _SUBCLASS_MAP 中有映射），
        否则使用通用 BaseAgent。

        Raises:
            ValueError: Agent 不存在
        """
        definition = self.get_definition(agent_id)
        if not definition:
            available = list(self._definitions.keys())
            raise ValueError(f"Agent 不存在: {agent_id}，可用: {available}")

        # 查找是否有专用子类
        subclass = self._find_subclass(agent_id)
        if subclass:
            return subclass(definition)
        return BaseAgent(definition)

    def _find_subclass(self, agent_id: str):
        """根据 agent_id 查找对应的 Agent 子类

        查找顺序:
          1. _SUBCLASS_MAP 显式映射
          2. 约定命名: agents.{agent_id}.{PascalCase}Agent

        Returns:
            BaseAgent 子类，或 None
        """
        # 1. 显式映射
        if agent_id in _SUBCLASS_MAP:
            module_path, class_name = _SUBCLASS_MAP[agent_id].rsplit(".", 1)
            try:
                mod = importlib.import_module(module_path)
                return getattr(mod, class_name)
            except (ImportError, AttributeError):
                pass

        # 2. 约定命名: audit_analyzer → agents.audit_analyzer.AuditAnalyzerAgent
        # snake_case → PascalCase: audit_analyzer → AuditAnalyzer
        parts = agent_id.split("_")
        pascal = "".join(p.capitalize() for p in parts)
        class_name = f"{pascal}Agent"
        module_path = f"agents.{agent_id}"
        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, class_name, None)
        except ImportError:
            pass

        return None

    # ── OpenSquilla 适配 ──

    def _resolve_mcp_tools(self, mcp_tool_names: list) -> list[dict]:
        """将 mcp_tools 声明转换为 OpenSquilla 可用的工具 Schema

        Args:
            mcp_tool_names: ["knowledge-mcp.search_violations", ...]

        Returns:
            [{"server": "knowledge-mcp", "tool": "search_violations", "fn": <callable>}, ...]
        """
        from mcp_servers import resolve_tool
        resolved = []
        for name in mcp_tool_names:
            fn = resolve_tool(name)
            if fn:
                server_name, tool_name = name.split(".", 1)
                resolved.append({
                    "server": server_name,
                    "tool": tool_name,
                    "fn": fn,
                })
        return resolved

    def export_for_opensquilla(self) -> dict:
        """导出所有 Agent 定义为 OpenSquilla 网关可消费的格式

        Returns:
            {"agents": [{"id": ..., "name": ..., "tools": [...], ...}, ...]}
        """
        self._load()
        agents_config = []
        for agent_id, d in self._definitions.items():
            resolved_tools = self._resolve_mcp_tools(d.mcp_tools)
            agents_config.append({
                "id": agent_id,
                "name": d.name,
                "description": d.description,
                "model": d.model,
                "temperature": d.temperature,
                "max_tokens": d.max_tokens,
                "system_prompt": d.system_prompt,
                "tools": [
                    {"server": t["server"], "tool": t["tool"]}
                    for t in resolved_tools
                ],
            })
        return {"agents": agents_config}

    @property
    def agent_ids(self) -> list[str]:
        """所有 Agent ID 列表"""
        self._load()
        return list(self._definitions.keys())
