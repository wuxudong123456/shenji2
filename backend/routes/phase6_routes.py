"""Phase 6 — 增强功能 API 路由

包含:
  - FAISS 向量语义搜索
  - 案例库 CRUD + 三向关联
  - 文书生成
"""
import json
from flask import request, jsonify, send_file
from services.db import query, query_one, insert, execute
from services.document_service import generate_document, batch_generate
from services.document_export_service import export_single, export_all


def _get_vector_store():
    """延迟导入 vector_store（torch/sentence_transformers 可能加载失败）"""
    try:
        from services.vector_store import get_vector_store as _gvs
        return _gvs()
    except Exception as e:
        raise RuntimeError(f"FAISS 向量检索不可用（依赖加载失败）: {e}")


def register_phase6_routes(app):

    # ═══════════════════════════════════════════════════════
    #  FAISS 向量语义搜索
    # ═══════════════════════════════════════════════════════

    @app.route("/api/audit/search/laws", methods=["GET"])
    def phase6_semantic_search_laws():
        """GET /api/audit/search/laws?q=招标投标合规&top_k=10"""
        q = request.args.get("q", "")
        top_k = request.args.get("top_k", 10, type=int)
        if not q:
            return jsonify({"success": False, "error": "请输入搜索词"}), 400
        try:
            store = _get_vector_store()
            results = store.search_laws(q, top_k=top_k)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 503
        return jsonify({"success": True, "query": q, "results": results, "total": len(results)})

    @app.route("/api/audit/search/violations", methods=["GET"])
    def phase6_semantic_search_violations():
        """GET /api/audit/search/violations?q=围标串标&top_k=10"""
        q = request.args.get("q", "")
        top_k = request.args.get("top_k", 10, type=int)
        if not q:
            return jsonify({"success": False, "error": "请输入搜索词"}), 400
        try:
            store = _get_vector_store()
            results = store.search_violations(q, top_k=top_k)
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 503
        return jsonify({"success": True, "query": q, "results": results, "total": len(results)})

    # ═══════════════════════════════════════════════════════
    #  案例库
    # ═══════════════════════════════════════════════════════

    @app.route("/api/audit/cases", methods=["GET"])
    def phase6_cases_list():
        """GET /api/audit/cases — 案例列表
        附带每个案例的关联违规名(violation_names) + 关联法规名(law_names)，
        以及 domains 领域列表（供下拉框）。"""
        q = request.args.get("q", "")
        domain = request.args.get("domain", "")
        limit = min(request.args.get("limit", 20, type=int), 200)
        offset = request.args.get("offset", 0, type=int)

        clauses = ["1=1"]; params = []
        if q:
            clauses.append("(c.title LIKE %s OR c.case_summary LIKE %s)")
            params.extend([f"%{q}%", f"%{q}%"])
        if domain:
            clauses.append("c.domain = %s"); params.append(domain)

        where = " AND ".join(clauses)
        # 关联违规名/法规名（先分页再聚合：内层先取分页行，外层仅对这几行 JOIN 聚合，
        # 避免 GROUP BY 全量聚合导致冷启动慢；law_id 字符集已对齐，无需 COLLATE）
        rows = query(
            f"SELECT c.id, c.title, c.domain, c.case_summary, c.involved_amount, c.source, c.created_at, "
            f"GROUP_CONCAT(DISTINCT v.violation_title SEPARATOR '、') AS violation_names, "
            f"GROUP_CONCAT(DISTINCT l.title SEPARATOR '、') AS law_names "
            f"FROM (SELECT c.id, c.title, c.domain, c.case_summary, c.involved_amount, c.source, c.created_at "
            f"      FROM audit_cases c WHERE {where} "
            f"      ORDER BY c.created_at DESC LIMIT %s OFFSET %s) c "
            f"LEFT JOIN audit_case_violations cv ON cv.case_id = c.id "
            f"LEFT JOIN audit_violations v ON cv.violation_id = v.id "
            f"LEFT JOIN audit_case_law_refs cl ON cl.case_id = c.id "
            f"LEFT JOIN audit_law.sys_core_law_allaudit l ON cl.law_id = l.id "
            f"GROUP BY c.id ORDER BY MAX(c.created_at) DESC",
            tuple(params) + (limit, offset), database="tt",
        )
        total = query_one(
            f"SELECT COUNT(*) AS n FROM audit_cases c WHERE {where}",
            tuple(params), database="tt",
        )
        # 领域列表（供下拉框）
        domains = query(
            "SELECT DISTINCT domain FROM audit_cases "
            "WHERE domain IS NOT NULL AND domain != '' ORDER BY domain",
            database="tt",
        )
        return jsonify({
            "success": True, "cases": [dict(r) for r in rows],
            "total": total["n"] if total else 0,
            "domains": [r["domain"] for r in domains],
        })

    @app.route("/api/audit/cases/<int:case_id>", methods=["GET"])
    def phase6_case_detail(case_id):
        """GET /api/audit/cases/<id> — 案例详情 + 三向关联"""
        row = query_one("SELECT * FROM audit_cases WHERE id = %s", (case_id,), database="tt")
        if not row:
            return jsonify({"success": False, "error": "案例不存在"}), 404

        # 关联违规
        violations = query(
            "SELECT v.id, v.violation_title, v.severity, v.expression_text "
            "FROM audit_case_violations cv JOIN audit_violations v ON cv.violation_id = v.id "
            "WHERE cv.case_id = %s", (case_id,), database="tt",
        )

        # 关联法规
        # 注: audit_case_law_refs 在 tt 库，sys_core_law_allaudit 在 audit_law 库，
        # 需全限定表名 + COLLATE 解决跨库字符集不一致。
        laws = query(
            "SELECT l.id, l.title, l.potency_level "
            "FROM tt.audit_case_law_refs cl JOIN audit_law.sys_core_law_allaudit l "
            "ON cl.law_id COLLATE utf8mb4_0900_ai_ci = l.id "
            "WHERE cl.case_id = %s", (case_id,), database="tt",
        )

        # 同类案例（同领域）
        domain = row.get("domain", "")
        similar = []
        if domain:
            similar = query(
                "SELECT id, title FROM audit_cases WHERE domain = %s AND id != %s LIMIT 5",
                (domain, case_id), database="tt",
            )

        return jsonify({
            "success": True,
            "case": dict(row),
            "violations": [dict(v) for v in violations],
            "laws": [dict(l) for l in laws],
            "similar_cases": [dict(s) for s in similar],
        })

    @app.route("/api/audit/cases", methods=["POST"])
    def phase6_case_create():
        """POST /api/audit/cases — 创建案例"""
        data = request.get_json() or {}
        title = data.get("title", "")
        if not title:
            return jsonify({"success": False, "error": "案例标题不能为空"}), 400

        case_id = insert(
            "INSERT INTO audit_cases (title, domain, case_summary, audit_method, "
            "involved_amount, audit_finding, audit_impact, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (title, data.get("domain", ""), data.get("case_summary", ""),
             data.get("audit_method", ""), data.get("involved_amount"),
             data.get("audit_finding", ""), data.get("audit_impact", ""),
             data.get("source", "")),
            database="tt",
        )

        # 关联违规
        for vid in data.get("violation_ids", []):
            try:
                insert(
                    "INSERT IGNORE INTO audit_case_violations (case_id, violation_id) VALUES (%s,%s)",
                    (case_id, vid), database="tt",
                )
            except Exception:
                pass

        # 关联法规
        for lid in data.get("law_ids", []):
            try:
                insert(
                    "INSERT IGNORE INTO audit_case_law_refs (case_id, law_id) VALUES (%s,%s)",
                    (case_id, lid), database="tt",
                )
            except Exception:
                pass

        return jsonify({"success": True, "id": case_id, "title": title})

    # ═══════════════════════════════════════════════════════
    #  文书生成
    # ═══════════════════════════════════════════════════════

    @app.route("/api/audit/documents/generate", methods=["POST"])
    def phase6_document_generate():
        """POST /api/audit/documents/generate — 生成单个文书"""
        data = request.get_json() or {}
        doc_type = data.get("type", "evidence")
        result = generate_document(doc_type, data.get("context", {}))
        return jsonify(result)

    @app.route("/api/audit/documents/batch", methods=["POST"])
    def phase6_document_batch_generate():
        """POST /api/audit/documents/batch — 批量生成四件套（P8-8 Step7）

        body: {task_id} 或兼容旧 {context:{...}}。
        有 task_id 时由 AnalysisContextBuilder 装配上下文（project_context 仅 DB、已确认疑点 +
        证据链继承、source_refs），替代前端 _buildDocContext；evidence_complete 门禁随响应返回。
        """
        data = request.get_json() or {}
        task_id = data.get("task_id")

        if task_id:
            # P8-10: 后端按 task_id 构建上下文（替前端 _buildDocContext）
            from services import analysis_context_builder as acb
            from services import analysis_lifecycle as alc
            from services import evidence_service
            ctx_obj = acb.build(task_id, step=7) or {}
            pc = ctx_obj.get("project_context", {})
            sd = ctx_obj.get("confirmed_results", {}) or {}
            project_id = ctx_obj["task"].get("project_id", "")
            # 已确认疑点 + 证据链（P8-8: 报告读 CONFIRMED 疑点，无来源禁入文书）
            suspicions = evidence_service.get_confirmed_suspicion_evidence(project_id, task_id)
            analysis_results = sd.get("analysis_results", [])
            context = {
                "project_title": pc.get("name", ""),
                "audit_period": pc.get("audit_period", ""),
                "audited_unit": pc.get("audited_unit", ""),
                "domain": pc.get("audit_type", ""),
                "item": (ctx_obj.get("focus_item") or {}).get("title", ""),
                "analysis_summary": sd.get("overall_assessment", ""),
                "analysis_results": analysis_results,
                "selected_laws": sd.get("selected_laws", []),
                "suspicions": suspicions,
                "laws": sd.get("law_recommendations", []),
            }
            ev_ready = alc.check_readiness(task_id, "evidence_complete")
            result = batch_generate(context)
            result["task_id"] = task_id
            result["readiness"] = {"evidence_complete": ev_ready}
            return jsonify(result)

        result = batch_generate(data.get("context", {}))
        return jsonify(result)

    @app.route("/api/audit/documents/export", methods=["POST"])
    def phase6_document_export():
        """POST /api/audit/documents/export — 导出文书为 Word(.docx)
        body: {context: {...}, doc_type?: "evidence"|"workpaper"|"report"|"review"}
        - 给定 doc_type → 单文书 .docx
        - 缺省 doc_type → 四件套 zip
        """
        data = request.get_json() or {}
        context = data.get("context", {})
        doc_type = data.get("doc_type")  # 可为 None → 导出全部
        try:
            if doc_type:
                buf, filename = export_single(doc_type, context)
                mimetype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            else:
                buf, filename = export_all(context)
                mimetype = "application/zip"
            return send_file(buf, as_attachment=True, download_name=filename, mimetype=mimetype)
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except Exception as e:  # noqa: BLE001
            return jsonify({"success": False, "error": f"导出失败: {e}"}), 500
