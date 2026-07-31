"""Phase 4.4 — /api/audit/* 路由注册

全部端点与 frontend/js/api.js 中 AuditAPI 的调用签名一一对应。
参数名、字段名、返回格式均以前端为唯一标准。
"""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date
from decimal import Decimal
from flask import request, jsonify


def _clean_row(row: dict) -> dict:
    """将数据库返回的行转换为 JSON 可序列化格式"""
    if not row:
        return row
    result = {}
    for k, v in row.items():
        if isinstance(v, bytes):
            result[k] = int.from_bytes(v, "big") if len(v) <= 4 else None
        elif isinstance(v, (datetime, date)):
            result[k] = v.isoformat()
        elif isinstance(v, Decimal):
            result[k] = float(v)
        else:
            result[k] = v
    return result


def _clean_rows(rows: list) -> list:
    return [_clean_row(r) for r in rows] if rows else []

from services.db import query, query_one, execute, insert
from services.knowledge_service import (
    search_laws, count_laws, get_law_detail,
    list_potency_levels, list_timeliness_options,
    search_violations, count_violations, get_violation_detail,
    get_audititem_children, get_audititem_tree, search_audititems,
)
from services.regulation_graph import get_regulation_graph, get_law_clauses
from services.expression_engine import execute_expression
from services.template_service import list_templates as tmpl_list
from agents.registry import AgentRegistry


def register_audit_routes(app):
    """在 Flask app 上注册所有 /api/audit/* 路由"""

    # ═══════════════════════════════════════════════════════════
    #  项目管理
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects", methods=["GET"])
    def audit_projects_list():
        """GET /api/audit/projects — 项目列表"""
        rows = query(
            "SELECT id, name, description, audit_period, status, "
            "creator, create_time, update_time "
            "FROM audit_projects WHERE deleted = 0 ORDER BY create_time DESC",
            database="tt",
        )
        return jsonify({"success": True, "projects": _clean_rows(rows)})

    @app.route("/api/audit/projects", methods=["POST"])
    def audit_projects_create():
        """POST /api/audit/projects — 创建项目"""
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"success": False, "error": "项目名称不能为空"}), 400

        pid = str(uuid.uuid4()).replace("-", "")[:12]
        description = data.get("description", "")
        audit_period = data.get("audit_period", "")
        minio_bucket = f"audit-project-{pid}"

        insert(
            "INSERT INTO audit_projects (id, name, description, audit_period, "
            "minio_bucket, status, creator, create_time) "
            "VALUES (%s,%s,%s,%s, %s,'active','system',NOW())",
            (pid, name, description, audit_period, minio_bucket),
            database="tt",
        )

        # 尝试创建 MinIO bucket
        try:
            from services.minio_client import get_client
            client = get_client()
            if not client.bucket_exists(minio_bucket):
                client.make_bucket(minio_bucket)
        except Exception:
            pass  # MinIO 不可用时仍创建项目记录

        return jsonify({"success": True, "id": pid, "name": name, "bucket": minio_bucket})

    @app.route("/api/audit/projects/<project_id>", methods=["GET"])
    def audit_project_detail(project_id):
        """GET /api/audit/projects/<id> — 项目详情"""
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404
        return jsonify({"success": True, "project": _clean_row(dict(row))})

    @app.route("/api/audit/projects/<project_id>", methods=["DELETE"])
    def audit_project_delete(project_id):
        """DELETE /api/audit/projects/<id> — 软删除项目"""
        execute(
            "UPDATE audit_projects SET deleted = 1, status = 'archived' WHERE id = %s",
            (project_id,), database="tt",
        )
        return jsonify({"success": True, "message": "项目已删除"})

    # ═══════════════════════════════════════════════════════════
    #  文件管理
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects/<project_id>/upload", methods=["POST"])
    def audit_file_upload(project_id):
        """POST /api/audit/projects/<id>/upload — 上传文件+OCR解析"""
        if "file" not in request.files:
            return jsonify({"success": False, "error": "请选择文件"}), 400

        f = request.files["file"]
        filename = f.filename
        file_bytes = f.read()
        file_id = str(uuid.uuid4()).replace("-", "")[:12]

        # 确定 MinIO 路径
        bucket = f"audit-project-{project_id}"
        minio_path = f"{project_id}/raw/{file_id}/{filename}"

        # 存入 MinIO
        try:
            from services.minio_client import get_client
            client = get_client()
            if not client.bucket_exists(bucket):
                client.make_bucket(bucket)
            import io
            client.put_object(bucket, minio_path, io.BytesIO(file_bytes),
                              length=len(file_bytes),
                              content_type=f.content_type or "application/octet-stream")
        except Exception as e:
            return jsonify({"success": False, "error": f"文件存储失败: {e}"}), 500

        # 创建溯源记录
        trace_id = insert(
            "INSERT INTO audit_document_traces "
            "(project_id, file_name, minio_path, ocr_version, created_at) "
            "VALUES (%s,%s,%s,1,NOW())",
            (project_id, filename, minio_path), database="tt",
        )

        # OCR 解析（异步非阻塞——失败不中断）
        ocr_content = None
        try:
            import tempfile, os
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
            tmp.write(file_bytes)
            tmp.close()
            from services.ocr_client import OCREngine
            ocr_result = OCREngine.parse(tmp.name)
            if ocr_result.get("success"):
                from services.ocr_client import MinerUClient
                ocr_content = str(ocr_result.get("text", "") or str(ocr_result))
            os.unlink(tmp.name)
        except Exception:
            pass  # OCR 失败不阻塞上传

        return jsonify({
            "success": True,
            "file_id": file_id,
            "file_name": filename,
            "minio_bucket": bucket,
            "minio_path": minio_path,
            "trace_id": trace_id,
            "ocr_status": "completed" if ocr_content else "pending",
        })

    @app.route("/api/audit/projects/<project_id>/files", methods=["GET"])
    def audit_files_list(project_id):
        """GET /api/audit/projects/<id>/files — 文件列表"""
        rows = query(
            "SELECT id, file_name, minio_path, ocr_version, ocr_content IS NOT NULL AS ocr_done, "
            "created_at FROM audit_document_traces "
            "WHERE project_id = %s ORDER BY created_at DESC",
            (project_id,), database="tt",
        )
        files = []
        for r in rows:
            d = dict(r)
            d["ocr_done"] = bool(d.get("ocr_done"))
            files.append(d)
        return jsonify({"success": True, "project_id": project_id, "files": files})

    @app.route("/api/audit/documents/<int:doc_id>/trace", methods=["GET"])
    def audit_document_trace(doc_id):
        """GET /api/audit/documents/<id>/trace — 溯源锚点"""
        row = query_one(
            "SELECT * FROM audit_document_traces WHERE id = %s",
            (doc_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "溯源记录不存在"}), 404
        return jsonify({"success": True, "trace": dict(row)})

    @app.route("/api/audit/documents/reparse", methods=["POST"])
    def audit_document_reparse():
        """POST /api/audit/documents/reparse — 重新推理"""
        data = request.get_json() or {}
        doc_id = data.get("document_id")
        template_name = data.get("template_name", "")
        if not doc_id:
            return jsonify({"success": False, "error": "请提供 document_id"}), 400

        # 查原始 OCR 内容
        trace = query_one(
            "SELECT * FROM audit_document_traces WHERE id = %s",
            (doc_id,), database="tt",
        )
        if not trace:
            return jsonify({"success": False, "error": "溯源记录不存在"}), 404

        # 重新提取
        from services.extraction_service import extract_fields, auto_classify_and_extract
        markdown = trace.get("ocr_content") or ""
        if template_name:
            extract_result = extract_fields(template_name, markdown)
        else:
            extract_result = auto_classify_and_extract(markdown)

        return jsonify({"success": True, "document_id": doc_id, "result": extract_result})

    # ═══════════════════════════════════════════════════════════
    #  数据工坊
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects/<project_id>/data", methods=["GET"])
    def audit_data_tables(project_id):
        """GET /api/audit/projects/<id>/data — 6张数据表列表+行数"""
        tables = ["data_contracts", "data_finance", "data_legal_docs",
                  "data_registers", "data_credentials", "data_general"]
        result = []
        for t in tables:
            row = query_one(
                f"SELECT COUNT(*) AS n FROM {t} WHERE project_id = %s",
                (project_id,), database="tt",
            )
            result.append({
                "table": t,
                "label": t.replace("data_", ""),
                "rows": row["n"] if row else 0,
            })
        return jsonify({"success": True, "project_id": project_id, "tables": result})

    @app.route("/api/audit/data/<table_name>/rows", methods=["GET"])
    def audit_data_rows(table_name):
        """GET /api/audit/data/<table>/rows — 数据浏览+分页"""
        allowed = {"data_contracts", "data_finance", "data_legal_docs",
                   "data_registers", "data_credentials", "data_general"}
        if table_name not in allowed:
            return jsonify({"success": False, "error": f"不支持的表: {table_name}"}), 400

        project_id = request.args.get("project_id", "")
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 20, type=int)
        offset = (page - 1) * per_page

        where = "WHERE project_id = %s" if project_id else "WHERE 1=1"
        params = (project_id,) if project_id else ()

        rows = query(
            f"SELECT * FROM {table_name} {where} ORDER BY id DESC LIMIT %s OFFSET %s",
            (*params, per_page, offset), database="tt",
        )
        total = query_one(
            f"SELECT COUNT(*) AS n FROM {table_name} {where}",
            params, database="tt",
        )

        # 清理非JSON字段
        clean_rows = []
        for r in rows:
            d = dict(r)
            for k, v in d.items():
                if isinstance(v, (datetime,)):
                    d[k] = v.isoformat()
                elif isinstance(v, bytes):
                    d[k] = None
            clean_rows.append(d)

        return jsonify({
            "success": True,
            "table": table_name,
            "rows": clean_rows,
            "total": total["n"] if total else 0,
            "page": page,
            "per_page": per_page,
        })

    @app.route("/api/audit/data/query", methods=["POST"])
    def audit_data_query():
        """POST /api/audit/data/query — 智能问数（NL→伪SQL→执行）"""
        data = request.get_json() or {}
        question = data.get("question", "")
        project_id = data.get("project_id", "")

        if not question:
            return jsonify({"success": False, "error": "请输入查询问题"}), 400

        # 使用 LLM 生成伪SQL（从 prompts/query/nl2sql.txt 加载）
        from services.llm_client import call_llm
        prompt = ""
        try:
            from prompts import load_prompt
            prompt = load_prompt("query/nl2sql").format(question=question)
        except FileNotFoundError:
            prompt = (
                f"根据以下问题，生成伪SQL表达式用于查询数据工坊的6张表。\n"
                f"6张表: data_contracts(合同协议), data_finance(财务), data_legal_docs(法律文书), "
                f"data_registers(登记台账), data_credentials(资质证照), data_general(综合)。\n"
                f"支持的语法: > < = != >= <= AND OR BETWEEN LIKE\n"
                f"只返回伪SQL表达式，不要其他内容。\n\n"
                f"问题: {question}"
            )
        try:
            pseudo_sql = call_llm(prompt, max_tokens=512, temperature=0)
            pseudo_sql = pseudo_sql.strip().strip("`").strip("'").strip('"')
        except Exception as e:
            return jsonify({"success": False, "error": f"LLM调用失败: {e}"}), 500

        return jsonify({"success": True, "question": question, "pseudo_sql": pseudo_sql})

    # ═══════════════════════════════════════════════════════════
    #  知识工坊
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/knowledge/violations", methods=["GET"])
    def audit_knowledge_violations():
        """GET /api/audit/knowledge/violations — 违规行为检索"""
        q = request.args.get("q", "")
        severity = request.args.get("severity")
        is_reviewed = request.args.get("is_reviewed", type=int)
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        offset = (page - 1) * per_page

        rows = search_violations(q, severity=severity, is_reviewed=is_reviewed,
                                 limit=per_page, offset=offset)
        total = count_violations(q, severity=severity, is_reviewed=is_reviewed)

        return jsonify({
            "success": True,
            "violations": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
        })

    @app.route("/api/audit/knowledge/regulations", methods=["GET"])
    def audit_knowledge_regulations():
        """GET /api/audit/knowledge/regulations — 法规检索"""
        q = request.args.get("q", "")
        potency_level = request.args.get("potency_level")
        timeliness = request.args.get("timeliness")
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 50, type=int)
        offset = (page - 1) * per_page

        rows = search_laws(q, potency_level=potency_level, timeliness=timeliness,
                           limit=per_page, offset=offset)
        total = count_laws(q, potency_level=potency_level, timeliness=timeliness)

        return jsonify({
            "success": True,
            "regulations": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "filters": {
                "potency_levels": list_potency_levels(),
                "timeliness_options": list_timeliness_options(),
            },
        })

    @app.route("/api/audit/knowledge/regulation/<law_id>", methods=["GET"])
    def audit_regulation_detail(law_id):
        """GET /api/audit/knowledge/regulation/<law_id> — 法规详情（溯源用）
        返回发布机关/文号/施行日期/时效/效力级别/条款正文等。
        """
        detail = get_law_detail(law_id)
        if not detail:
            return jsonify({"success": False, "error": "法规不存在或已下线"}), 404
        return jsonify({"success": True, "law": detail})

    @app.route("/api/audit/knowledge/regulation/<law_id>/graph", methods=["GET"])
    def audit_regulation_graph(law_id):
        """GET /api/audit/knowledge/regulation/<id>/graph — 法规关系图"""
        graph = get_regulation_graph(law_id)
        if graph.get("error"):
            return jsonify({"success": False, "error": graph["error"]}), 404
        return jsonify({"success": True, "graph": graph})

    @app.route("/api/audit/knowledge/clauses/<law_id>", methods=["GET"])
    def audit_law_clauses(law_id):
        """GET /api/audit/knowledge/clauses/<law_id> — 条款分析"""
        clauses = get_law_clauses(law_id)
        return jsonify({"success": True, "law_id": law_id, "clauses": clauses,
                        "total": len(clauses)})

    # ═══════════════════════════════════════════════════════════
    #  表达式引擎 + 疑点
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/expression/execute", methods=["POST"])
    def audit_expression_execute():
        """POST /api/audit/expression/execute — 执行违规表达式"""
        data = request.get_json() or {}
        expression = data.get("expression", "")
        table = data.get("table", "data_contracts")
        project_id = data.get("project_id", "")

        if not expression:
            return jsonify({"success": False, "error": "请提供违规表达式"}), 400

        result = execute_expression(expression, table, project_id)
        return jsonify(result)

    @app.route("/api/audit/suspicion/generate", methods=["POST"])
    def audit_suspicion_generate():
        """POST /api/audit/suspicion/generate — 生成疑点报告"""
        data = request.get_json() or {}

        agent = AgentRegistry().create_agent("suspicion_generator")
        result = agent.run({
            "analysis_results": data.get("analysis_results", []),
            "overall_assessment": data.get("overall_assessment", ""),
            "domain": data.get("domain", ""),
            "audit_item": data.get("item", ""),
            "project_id": data.get("project_id", ""),
            "primary_laws": data.get("primary_laws", []),
            "selected_laws": data.get("selected_laws", []),
        })

        return jsonify(result)

    # ═══════════════════════════════════════════════════════════
    #  模板
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/templates", methods=["GET"])
    def audit_templates_list():
        """GET /api/audit/templates — 模板列表"""
        domain = request.args.get("domain")
        category = request.args.get("category")
        q = request.args.get("q")
        limit = request.args.get("limit", 50, type=int)

        from services.template_service import search_templates
        if q:
            rows = search_templates(q, limit=limit)
        else:
            rows = tmpl_list(domain=domain, category=category)

        return jsonify({"success": True, "templates": rows[:limit], "count": len(rows[:limit])})

    # ═══════════════════════════════════════════════════════════
    #  智能分析工作流（LangGraph 驱动）
    # ═══════════════════════════════════════════════════════════

    # 全局工作流实例（编译一次，复用）
    from workflow.graph import build_analysis_graph
    _analysis_graph = build_analysis_graph()

    def _graph_state_to_response(task_id: str, state: dict, snapshot) -> dict:
        """将 LangGraph 状态快照转为前端可用的 JSON 响应"""
        # 获取当前步骤信息
        next_nodes = list(snapshot.next) if snapshot.next else []
        current_step = state.get("current_step", 1)

        # 判断暂停位置（两个断点）
        if "step_3_confirm" in next_nodes:
            status = "awaiting_confirmation"          # 断点①：确认依据
        elif "step_5_analysis" in next_nodes:
            status = "awaiting_upload"                 # 断点②：等待上传资料
            current_step = 4                           # 前端展示为 Step4
        elif not next_nodes:
            status = "completed"                       # 工作流跑完
        else:
            status = "in_progress"

        return {
            "success": True,
            "task_id": task_id,
            "step": current_step,
            "status": status,
            "intent_result": state.get("intent_result", {}),
            "domain": state.get("domain", ""),
            "audit_item": state.get("audit_item", ""),
            "target_level": state.get("target_level", ""),
            "target_unit": state.get("target_unit", ""),
            "matches": state.get("matches", []),
            "primary_laws": state.get("primary_laws", []),
            "layer_advice": state.get("layer_advice", ""),
            "recommended_materials": state.get("recommended_materials", []),
            "confirmation_status": state.get("confirmation_status", "pending"),
            "next_nodes": next_nodes,
            "errors": state.get("errors", []),
        }

    @app.route("/api/audit/analysis", methods=["POST"])
    def audit_analysis_create():
        """POST /api/audit/analysis — 创建分析任务，启动 LangGraph 工作流

        工作流自动执行 Step①(意图分析) → Step②(三Agent并行推荐)，
        然后在 Step③ 人工确认断点处暂停，返回推荐结果供用户选择。
        """
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        user_intent = data.get("intent", "")

        if not user_intent:
            return jsonify({"success": False, "error": "请输入审计意图"}), 400

        task_id = str(uuid.uuid4()).replace("-", "")[:16]

        # 启动 LangGraph 工作流
        config = {"configurable": {"thread_id": task_id}}
        state = _analysis_graph.invoke({
            "task_id": task_id,
            "project_id": project_id,
            "session_id": task_id,
            "user_intent": user_intent,
        }, config)

        # 持久化到 MySQL
        insert(
            "INSERT INTO audit_analysis_tasks "
            "(task_code, project_id, title, step, step_data, agent_results, status, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'in_progress',NOW())",
            (task_id, project_id, user_intent[:500],
             state.get("current_step", 2),
             json.dumps({"intent_result": state.get("intent_result", {})}, ensure_ascii=False),
             json.dumps({
                 "intent_analyzer": state.get("intent_result", {}),
                 "matches": state.get("matches", []),
                 "primary_laws": state.get("primary_laws", []),
                 "recommended_materials": state.get("recommended_materials", []),
             }, ensure_ascii=False)),
            database="tt",
        )

        # 构造响应
        snapshot = _analysis_graph.get_state(config)
        return jsonify(_graph_state_to_response(task_id, state, snapshot))

    @app.route("/api/audit/analysis/<task_id>", methods=["GET"])
    def audit_analysis_status(task_id):
        """GET /api/audit/analysis/<id> — 查询分析任务状态

        优先从 LangGraph 状态快照获取（实时），
        回退到 MySQL 持久化记录。
        """
        config = {"configurable": {"thread_id": task_id}}
        snapshot = _analysis_graph.get_state(config)

        if snapshot and snapshot.values:
            state = snapshot.values
            return jsonify(_graph_state_to_response(task_id, state, snapshot))

        # 回退：查 MySQL
        row = query_one(
            "SELECT * FROM audit_analysis_tasks WHERE task_code = %s",
            (task_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "任务不存在"}), 404
        return jsonify({"success": True, "task": dict(row)})

    @app.route("/api/audit/analysis/<task_id>/step/<int:step_num>", methods=["POST"])
    def audit_analysis_step(task_id, step_num):
        """POST /api/audit/analysis/<id>/step/<n> — 推进工作流到指定步骤

        step=4: 确认文件上传完成 → 继续执行 Step⑤+⑥
        其他步骤由 LangGraph 自动推进，前端只需调 confirm 端点。
        """
        config = {"configurable": {"thread_id": task_id}}
        snapshot = _analysis_graph.get_state(config)

        if not snapshot or not snapshot.values:
            return jsonify({"success": False, "error": "任务不存在或状态已过期"}), 404

        state = snapshot.values

        if step_num == 4:
            # Step④: 标记文件已上传，继续执行后续步骤
            data = request.get_json() or {}
            uploaded_files = data.get("uploaded_files", [])

            _analysis_graph.update_state(config, {
                "uploaded_files": uploaded_files,
                "current_step": 4,
            }, as_node="step_4_upload")

            # 继续执行 Step⑤ + Step⑥
            final_state = _analysis_graph.invoke(None, config)

            # 持久化最终结果
            execute(
                "UPDATE audit_analysis_tasks SET step = 6, status = 'completed', "
                "step_data = JSON_MERGE_PATCH(COALESCE(step_data,'{}'), %s), "
                "agent_results = JSON_MERGE_PATCH(COALESCE(agent_results,'{}'), %s), "
                "result = %s WHERE task_code = %s",
                (json.dumps({"uploaded_files": uploaded_files}, ensure_ascii=False),
                 json.dumps({
                     "audit_analyzer": final_state.get("analysis_results", []),
                     "suspicion_generator": final_state.get("suspicion_report", {}),
                 }, ensure_ascii=False),
                 json.dumps(final_state.get("suspicion_report", {}), ensure_ascii=False),
                 task_id),
                database="tt",
            )

            return jsonify({
                "success": True,
                "task_id": task_id,
                "step": final_state.get("current_step", 6),
                "status": "completed",
                "analysis_results": final_state.get("analysis_results", []),
                "overall_assessment": final_state.get("overall_assessment", ""),
                "suspicion_report": final_state.get("suspicion_report", {}),
            })

        # 对于其他步骤，返回当前状态（工作流由 confirm 端点驱动）
        return jsonify(_graph_state_to_response(task_id, state, snapshot))

    @app.route("/api/audit/analysis/<task_id>/confirm", methods=["POST"])
    def audit_analysis_confirm(task_id):
        """POST /api/audit/analysis/<id>/confirm — 人工确认并继续工作流

        用户在 Step③ 选择违规模型和法规依据后，调用此端点：
          - 注入用户选择到工作流状态
          - 确认通过 → 继续执行 Step④⑤⑥
          - 确认拒绝 → 结束工作流
        """
        data = request.get_json() or {}
        config = {"configurable": {"thread_id": task_id}}

        snapshot = _analysis_graph.get_state(config)
        if not snapshot or not snapshot.values:
            return jsonify({"success": False, "error": "任务不存在或状态已过期"}), 404

        selected_violations = data.get("selected_violations", [])
        selected_laws = data.get("selected_laws", [])
        custom_regulations = data.get("custom_regulations", [])
        action = data.get("action", "confirm")  # confirm / reject

        # 注入用户确认数据（as_node 指向确认节点，消除并行后的歧义更新）
        _analysis_graph.update_state(config, {
            "selected_violations": selected_violations,
            "selected_laws": selected_laws,
            "custom_regulations": custom_regulations,
            "confirmation_status": "confirmed" if action == "confirm" else "rejected",
            "current_step": 3,
        }, as_node="step_3_confirm")

        # 持久化确认记录
        execute(
            "UPDATE audit_analysis_tasks SET step = 3, step_data = JSON_MERGE_PATCH("
            "COALESCE(step_data,'{}'), %s) WHERE task_code = %s",
            (json.dumps({
                "selected_violations": selected_violations,
                "selected_laws": selected_laws,
                "custom_regulations": custom_regulations,
                "action": action,
                "confirmed_at": datetime.now().isoformat(),
            }, ensure_ascii=False), task_id),
            database="tt",
        )

        if action == "reject":
            return jsonify({
                "success": True,
                "task_id": task_id,
                "step": 3,
                "status": "rejected",
                "message": "分析已取消，用户拒绝AI推荐",
            })

        # 确认通过：继续工作流（执行 Step④）
        # Step④ 是文件上传等待节点，不阻塞
        state = _analysis_graph.invoke(None, config)

        # 检查是否到达 Step④ 等待点
        new_snapshot = _analysis_graph.get_state(config)
        return jsonify(_graph_state_to_response(task_id, state, new_snapshot))

    # ═══════════════════════════════════════════════════════════
    #  工作区（资料工坊兼容）
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/workspace/projects", methods=["GET"])
    def audit_workspace_projects():
        """GET /api/audit/workspace/projects — 合并 MySQL + MinIO 项目列表"""
        # MySQL 项目
        db_projects = query(
            "SELECT id, name, description, status, create_time FROM audit_projects WHERE deleted = 0 ORDER BY create_time DESC",
            database="tt",
        )
        result = []
        for p in db_projects:
            result.append({"id": p["id"], "name": p["name"], "type": "db", "status": p.get("status", "")})
        # MinIO 项目
        try:
            from services.minio_client import list_folders, get_client
            folders = list_folders()
            for f in folders:
                has_id = any(p["name"] == f for p in result)
                if not has_id:
                    result.append({"id": f, "name": f, "type": "minio", "status": "active"})
        except Exception:
            pass
        return jsonify({"success": True, "projects": result})

    @app.route("/api/audit/workspace/files", methods=["GET"])
    def audit_workspace_files():
        """GET /api/audit/workspace/files?project=<name> — 列出 MinIO 中的文件"""
        project = request.args.get("project", "")
        if not project:
            return jsonify({"success": False, "error": "请提供 project 参数"}), 400
        try:
            from services.minio_client import list_objects
            prefix = project + "/"
            objects = list_objects(prefix)
            files = []
            for obj in objects:
                name = obj["name"].replace(prefix, "", 1)
                if "/" in name:
                    continue
                files.append({"name": name, "size": obj["size"], "last_modified": obj["last_modified"]})
            return jsonify({"success": True, "project": project, "files": files})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/audit/workspace/download", methods=["GET"])
    def audit_workspace_download():
        """GET /api/audit/workspace/download?project=<name>&file=<filename> — MinIO 预签名下载"""
        project = request.args.get("project", "")
        filename = request.args.get("file", "")
        if not project or not filename:
            return jsonify({"success": False, "error": "缺少参数"}), 400
        try:
            from services.minio_client import get_presigned_url
            url = get_presigned_url(f"{project}/{filename}", expires=3600)
            return jsonify({"success": True, "url": url})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/audit/workspace/delete", methods=["DELETE"])
    def audit_workspace_delete():
        """DELETE /api/audit/workspace/delete?project=<name>&file=<filename>"""
        project = request.args.get("project", "")
        filename = request.args.get("file", "")
        try:
            from services.minio_client import delete_object
            delete_object(f"{project}/{filename}")
            return jsonify({"success": True, "message": f"{filename} 已删除"})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    # ═══════════════════════════════════════════════════════════
    #  AI 对话
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/chat", methods=["POST"])
    def audit_chat():
        """POST /api/chat — AI对话（法规问答/智能分析）"""
        data = request.get_json() or {}
        message = data.get("message", "")
        session_id = data.get("session_id", str(uuid.uuid4())[:16])

        if not message:
            return jsonify({"success": False, "error": "请输入消息"}), 400

        # 使用 IntentAnalyzer 作为通用对话 Agent
        agent = AgentRegistry().create_agent("intent_analyzer")
        result = agent.run({"intent": message})

        # 保存对话记录
        insert(
            "INSERT INTO audit_conversations (session_id, page, title, created_at) "
            "VALUES (%s,'chat',%s,NOW())",
            (session_id, message[:500]), database="tt",
        )

        return jsonify({
            "success": True,
            "session_id": session_id,
            "reply": result.get("output", {}),
            "raw": result.get("raw_response", {}),
        })

    @app.route("/api/chat/history", methods=["GET"])
    def audit_chat_history():
        """GET /api/chat/history — 对话历史"""
        session_id = request.args.get("session_id", "")
        if not session_id:
            return jsonify({"success": False, "error": "请提供 session_id"}), 400
        rows = query(
            "SELECT * FROM audit_conversations WHERE session_id = %s ORDER BY created_at DESC LIMIT 50",
            (session_id,), database="tt",
        )
        return jsonify({"success": True, "session_id": session_id, "history": rows})
