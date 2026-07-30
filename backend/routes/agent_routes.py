"""Agent 管理 API 路由 — GET/POST/PUT/DELETE /api/audit/agents

对应 DESIGN.md §3.2 Agent 管理 (AG-01~06) 和 audit_agents 表。
"""
import json
from flask import request, jsonify
from agents.registry import AgentRegistry


def register_agent_routes(app):

    @app.route("/api/audit/agents", methods=["GET"])
    def audit_agents_list():
        """GET /api/audit/agents — Agent 列表（YAML + DB 合并）"""
        registry = AgentRegistry()
        agents = registry.list_agents()
        return jsonify({"success": True, "agents": agents, "total": len(agents)})

    @app.route("/api/audit/agents", methods=["POST"])
    def audit_agents_create():
        """POST /api/audit/agents — 创建/注册 Agent 到 DB

        Body: { name, display_name, role, system_prompt, model, tools, icon, color }
        写入 audit_agents 表，设置 is_active=1, is_system=0
        """
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        display_name = data.get("display_name", "").strip()

        if not name:
            return jsonify({"success": False, "error": "Agent name 不能为空"}), 400
        if not display_name:
            display_name = name

        try:
            from services.db import insert, query_one

            # 检查是否已存在
            existing = query_one(
                "SELECT id FROM audit_agents WHERE name = %s",
                (name,), database="tt",
            )
            if existing:
                return jsonify({"success": False, "error": f"Agent '{name}' 已存在，请使用 PUT 更新"}), 409

            tools = data.get("tools", [])
            tools_json = json.dumps(tools, ensure_ascii=False) if tools else None

            agent_id = insert(
                "INSERT INTO audit_agents (name, display_name, role, system_prompt, "
                "model, tools, icon, color, is_active, is_system, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,0,NOW())",
                (
                    name,
                    display_name,
                    data.get("role", ""),
                    data.get("system_prompt", ""),
                    data.get("model", "deepseek-v4-flash"),
                    tools_json,
                    data.get("icon", "bi-robot"),
                    data.get("color", "#1a3a5c"),
                ),
                database="tt",
            )

            # 刷新 Registry
            AgentRegistry().reload()

            return jsonify({
                "success": True,
                "id": agent_id,
                "name": name,
                "display_name": display_name,
                "message": f"Agent '{display_name}' 已创建",
            })
        except Exception as e:
            return jsonify({"success": False, "error": f"创建失败: {e}"}), 500

    @app.route("/api/audit/agents/<agent_name>", methods=["PUT"])
    def audit_agents_update(agent_name):
        """PUT /api/audit/agents/<name> — 更新 Agent 配置

        Body 中提供的字段会被更新；未提供的字段保持不变。
        """
        data = request.get_json() or {}
        if not data:
            return jsonify({"success": False, "error": "请提供要更新的字段"}), 400

        try:
            from services.db import query_one, execute

            existing = query_one(
                "SELECT id FROM audit_agents WHERE name = %s AND is_active = 1",
                (agent_name,), database="tt",
            )
            if not existing:
                return jsonify({"success": False, "error": f"Agent '{agent_name}' 不存在"}), 404

            # 构建 SET 子句
            setters = []
            params = []
            field_map = {
                "display_name": "display_name",
                "role": "role",
                "system_prompt": "system_prompt",
                "model": "model",
                "icon": "icon",
                "color": "color",
                "is_active": "is_active",
            }
            for json_field, db_col in field_map.items():
                if json_field in data:
                    setters.append(f"{db_col} = %s")
                    params.append(data[json_field])

            if "tools" in data:
                setters.append("tools = %s")
                params.append(json.dumps(data["tools"], ensure_ascii=False))

            if not setters:
                return jsonify({"success": False, "error": "没有可更新的字段"}), 400

            setters.append("updated_at = NOW()")
            params.append(agent_name)

            execute(
                f"UPDATE audit_agents SET {', '.join(setters)} WHERE name = %s",
                tuple(params), database="tt",
            )

            # 刷新 Registry
            AgentRegistry().reload()

            return jsonify({"success": True, "name": agent_name, "message": "Agent 已更新"})
        except Exception as e:
            return jsonify({"success": False, "error": f"更新失败: {e}"}), 500

    @app.route("/api/audit/agents/<agent_name>", methods=["DELETE"])
    def audit_agents_delete(agent_name):
        """DELETE /api/audit/agents/<name> — 删除 Agent（软删除，设置 is_active=0）

        仅能删除 DB 中的 Agent；YAML 定义的 Agent 不受影响。
        """
        registry = AgentRegistry()
        ok = registry.delete_from_db(agent_name)
        if not ok:
            return jsonify({
                "success": False,
                "error": f"Agent '{agent_name}' 不存在或无法删除（系统预置 Agent 受保护）",
            }), 404

        registry.reload()
        return jsonify({"success": True, "name": agent_name, "message": "Agent 已删除"})

    @app.route("/api/audit/agents/<agent_name>/reset", methods=["POST"])
    def audit_agents_reset(agent_name):
        """POST /api/audit/agents/<name>/reset — 重置 Agent 为 YAML 默认配置

        删除 DB 中的自定义配置，恢复到 YAML 定义。
        """
        registry = AgentRegistry()
        ok = registry.delete_from_db(agent_name)
        registry.reload()

        definition = registry.get_definition(agent_name)
        if not definition:
            return jsonify({"success": False, "error": f"Agent '{agent_name}' 不存在"}), 404

        return jsonify({
            "success": True,
            "name": agent_name,
            "message": f"Agent '{definition.name}' 已重置为默认配置",
        })
