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

    @app.route("/api/audit/projects/extract-info", methods=["POST"])
    def audit_project_extract_info():
        """POST /api/audit/projects/extract-info — AI 从文本提取项目立项信息

        供 projects.html 的"AI综合分析"使用（原 aiExtract 是前端写死的假数据）。
        用 LLM 从用户粘贴的文档内容提取项目基本信息 + 审计事项，严格基于文本，
        提取不到的字段留空（不编造）。
        """
        data = request.get_json() or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"success": False, "error": "请提供文档内容"}), 400

        from services.llm_client import call_llm_json

        system_prompt = (
            "你是审计项目立项助手。从用户提供的文档内容中提取项目立项信息。"
            "严格基于文本内容，提取不到的字段留空字符串，不要编造。"
            "审计事项根据项目类型合理推断 3-5 个核查方向。"
        )
        prompt = (
            "请从以下审计文档内容中提取项目立项信息，返回 JSON（提取不到的字段留空字符串）：\n"
            "{\n"
            '  "project_code": "项目编号，如审通〔2026〕001号",\n'
            '  "project_name": "项目名称",\n'
            '  "audited_unit": "被审计单位",\n'
            '  "audit_type": "审计类型（预算执行审计/专项审计调查/经济责任审计/固定资产投资审计/绩效审计/资源环境审计 之一）",\n'
            '  "audit_method": "审计方式（就地审计/送达审计/联网审计 之一）",\n'
            '  "audit_period": "审计期间，如 2026-01 至 2026-06",\n'
            '  "entry_date": "进点日期 YYYY-MM-DD",\n'
            '  "amount": "涉及金额（万元，纯数字字符串）",\n'
            '  "level": "单位层级（省级/市级/县级 之一）",\n'
            '  "target_unit": "审计对象",\n'
            '  "extend_unit": "延伸审计单位",\n'
            '  "scope": "审计范围描述",\n'
            '  "audit_items": ["审计事项1：核查方向", "审计事项2", "审计事项3"]\n'
            "}\n\n文档内容：\n" + text[:3000]
        )

        result = call_llm_json(prompt, system_prompt=system_prompt, temperature=0.1, timeout=120)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 503
        return jsonify({"success": True, "extracted": result})

    @app.route("/api/audit/projects/infer-concerns", methods=["POST"])
    def audit_project_infer_concerns():
        """POST /api/audit/projects/infer-concerns — AI 推断审计关注业务环节

        供 analysis-wiz 第一步使用：项目无 concerns 时，根据项目名/类型推断 3-5 个关注环节，
        替代写死的"招标投标\\n采购方式..."默认。
        """
        data = request.get_json() or {}
        project_name = (data.get("project_name") or "").strip()
        domain = (data.get("domain") or "").strip()
        if not project_name:
            return jsonify({"success": False, "error": "请提供项目名称"}), 400

        from services.llm_client import call_llm_json
        system_prompt = "你是审计专家。根据审计项目信息，推断 3-5 个应重点关注的业务环节，环节须与该项目类型直接相关。"
        prompt = (
            "根据以下审计项目，推断 3-5 个应重点关注的业务环节，返回 JSON：\n"
            '{"concerns": ["环节1", "环节2", "环节3"]}\n\n'
            "项目名称：" + project_name + "\n审计类型：" + (domain or "未指定") + "\n"
            "要求：环节须与该项目类型相关（如采购审计关注招标/采购方式/供应商/资金；农业农村审计关注补贴发放/资金流向等），不要泛泛而谈。"
        )
        result = call_llm_json(prompt, system_prompt=system_prompt, temperature=0.2, timeout=60)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 503
        return jsonify({"success": True, "concerns": result.get("concerns", [])})

    # ═══════════════════════════════════════════════════════════
    #  文件管理
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects/<project_id>/upload", methods=["POST"])
    def audit_file_upload(project_id):
        """POST /api/audit/projects/<id>/upload — 上传文件，异步触发 OCR+提取

        改造说明（Q1.1）:
          原实现同步跑 OCR 阻塞请求；现改为：
          1. 算 MD5 去重（同项目同文件不重复处理）
          2. 存 MinIO + 建溯源记录
          3. 提交异步任务（task_worker 后台跑 OCR+提取）
          4. 立即返回 task_id，前端轮询进度
        """
        if "file" not in request.files:
            return jsonify({"success": False, "error": "请选择文件"}), 400

        f = request.files["file"]
        filename = f.filename
        file_bytes = f.read()
        file_id = str(uuid.uuid4()).replace("-", "")[:12]

        # 1. 计算 MD5
        import hashlib
        file_md5 = hashlib.md5(file_bytes).hexdigest()

        # 2. 去重检查：同项目同 MD5 的文件已存在则返回已有记录
        existing = query_one(
            "SELECT id, file_name, ocr_content IS NOT NULL AS ocr_done "
            "FROM audit_document_traces WHERE project_id = %s AND file_md5 = %s "
            "ORDER BY id DESC LIMIT 1",
            (project_id, file_md5), database="tt",
        )
        if existing:
            return jsonify({
                "success": True,
                "deduplicated": True,
                "file_name": existing["file_name"],
                "trace_id": existing["id"],
                "ocr_status": "completed" if existing.get("ocr_done") else "pending",
                "message": "文件已存在，跳过重复处理",
            })

        # 3. 存入 MinIO
        bucket = f"audit-project-{project_id}"
        minio_path = f"{project_id}/raw/{file_id}/{filename}"
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

        # 4. 创建溯源记录（含 MD5）
        trace_id = insert(
            "INSERT INTO audit_document_traces "
            "(project_id, file_name, file_md5, minio_path, ocr_version, created_at) "
            "VALUES (%s,%s,%s,%s,1,NOW())",
            (project_id, filename, file_md5, minio_path), database="tt",
        )

        # 5. 提交异步 OCR 任务（task_worker 后台处理）
        from services.task_manager import create_task
        from services.task_worker import submit_task
        import json as _json
        task_result = create_task(
            task_name=filename,
            task_type="ocr",
            project_id=project_id,
        )
        task_id = task_result.get("task", {}).get("id")

        # 把 trace_id + minio 信息塞进 task 的 result 字段（task_worker 读取）
        if task_id:
            execute(
                "UPDATE audit_task_queue SET result = %s WHERE id = %s",
                (_json.dumps({
                    "trace_id": trace_id,
                    "minio_bucket": bucket,
                    "minio_path": minio_path,
                    "filename": filename,
                    "project_id": project_id,
                }, ensure_ascii=False), task_id),
                database="tt",
            )
            submit_task(task_id)

        return jsonify({
            "success": True,
            "file_id": file_id,
            "file_name": filename,
            "minio_bucket": bucket,
            "minio_path": minio_path,
            "trace_id": trace_id,
            "task_id": task_id,
            "ocr_status": "pending",
            "message": "文件已上传，OCR+提取正在后台处理",
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

    @app.route("/api/audit/data/tables", methods=["GET"])
    def audit_data_tables_global():
        """GET /api/audit/data/tables — 6张数据表的全库行数（全局视图，不按项目过滤）"""
        tables = ["data_contracts", "data_finance", "data_legal_docs",
                  "data_registers", "data_credentials", "data_general"]
        result = []
        for t in tables:
            row = query_one(f"SELECT COUNT(*) AS n FROM {t}", (), database="tt")
            result.append({
                "table": t,
                "label": t.replace("data_", ""),
                "rows": row["n"] if row else 0,
            })
        return jsonify({"success": True, "tables": result})

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
                f"把问题转成【行级筛选表达式】（只输出WHERE后面的条件，禁止SELECT/FROM/WHERE/分号/表名）。\n"
                f"字段用中文名，字符串用双引号，支持 > < = != >= <= AND OR BETWEEN LIKE。\n"
                f"示例：金额超过100万且询价 → 金额 > 1000000 AND 采购方式 = \"询价\"\n"
                f"只输出表达式本身。\n\n问题: {question}"
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

    @app.route("/api/audit/threshold/check", methods=["POST"])
    def audit_threshold_check():
        """POST /api/audit/threshold/check — 阈值规则批量扫描（③阈值对照）"""
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        table = data.get("table", "data_contracts")
        from services.threshold_service import check_thresholds
        result = check_thresholds(project_id, table)
        return jsonify(result)

    @app.route("/api/audit/threshold-table", methods=["POST"])
    def audit_threshold_table():
        """POST /api/audit/threshold-table — 业务阈值×法规条款对照表（P2-3 动态生成）

        Body: { violation_titles: ["应公开招标未招标"], target_level: "市级" }
        从违规模型的表达式提取阈值，从描述提取法规，动态组装对照表。
        """
        data = request.get_json() or {}
        from services.threshold_extractor import build_threshold_table
        result = build_threshold_table(
            violation_titles=data.get("violation_titles", []),
            violation_ids=data.get("violation_ids"),
            target_level=data.get("target_level", ""),
        )
        return jsonify({"success": True, **result})

    # ── 聚合表达式 SQL 人工确认（Submit→Confirm→Execute）──
    @app.route("/api/audit/expression-sql/pending", methods=["GET"])
    def audit_expression_sql_pending():
        """GET /api/audit/expression-sql/pending — 列出待确认的 LLM 生成 SQL"""
        from services.sql_generator import list_pending_sql
        return jsonify({"success": True, "items": list_pending_sql()})

    @app.route("/api/audit/expression-sql/<int:cid>/approve", methods=["POST"])
    def audit_expression_sql_approve(cid):
        """POST /api/audit/expression-sql/<id>/approve — 人工批准 SQL（批准后方可执行）"""
        from services.sql_generator import approve_sql
        reviewer = (request.get_json(silent=True) or {}).get("reviewer", "admin")
        ok = approve_sql(cid, reviewer)
        return jsonify({"success": ok, "id": cid,
                        "review_status": "approved" if ok else "error"})

    @app.route("/api/audit/expression-sql/<int:cid>/reject", methods=["POST"])
    def audit_expression_sql_reject(cid):
        """POST /api/audit/expression-sql/<id>/reject — 人工拒绝 SQL"""
        from services.sql_generator import reject_sql
        reviewer = (request.get_json(silent=True) or {}).get("reviewer", "admin")
        ok = reject_sql(cid, reviewer)
        return jsonify({"success": ok, "id": cid,
                        "review_status": "rejected" if ok else "error"})

    @app.route("/api/audit/expression-sql/auto-approve", methods=["POST"])
    def audit_expression_sql_auto_approve():
        """POST /api/audit/expression-sql/auto-approve — 自动批准安全的聚合 SQL（P2-2）

        对所有 pending SQL 做安全检查（只读 SELECT + 聚合 + project_id 过滤），
        通过的自动批准，不通过的跳过留待人工审核。
        """
        from services.sql_generator import auto_approve_safe_sql
        result = auto_approve_safe_sql()
        return jsonify({"success": True, **result})

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

        # 记录用户确认操作（审计留痕）
        try:
            from services.audit_logger import log_operation
            ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or ""
            log_operation(
                user=data.get("user", "system"),
                action=f"{action}_analysis",
                target_type="analysis_task",
                target_id=task_id,
                before={"violations": snapshot.values.get("matches", [])},
                after={"violations": selected_violations, "laws": selected_laws},
                ip=ip,
            )
        except Exception:
            pass

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
