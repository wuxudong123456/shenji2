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

from services.data_service import (
    list_table_counts, list_rows, parse_query_filters,
    quality_check, missing_check, ProjectIDRequiredError,
)


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


def _json_loads(v):
    """DB 取回的 JSON 列（str/已解析）统一成 python 对象，非法/NULL 原样返回。"""
    if v is None or isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _project_to_dto(row: dict | None) -> dict:
    """项目 DTO：DB 行 → 统一字段 + 旧别名兼容（前端 title/unit/domain/level）

    P1.2: 立项全字段（project_code/audited_unit/audit_type/target_level/...）是新权威字段，
    同时保留旧别名让前端老代码（读 title/unit/domain/level）不破。
    P1.x: setup_stage 随行返回，缺失时默认 basic。
    """
    d = _clean_row(dict(row)) if row else {}
    d["title"] = d.get("name", "")
    d["unit"] = d.get("audited_unit", "") or ""
    d["domain"] = d.get("audit_type", "") or ""
    d["level"] = d.get("target_level", "") or ""
    d["setup_stage"] = d.get("setup_stage") or "basic"
    rlc.enrich(d)  # 报告段：加 report_allowed_actions（report_stage 透传，None→["start_report"]）
    return d


def _stage_enrich(dto: dict) -> dict:
    """附加 allowed_actions / missing_fields（供前端切换 Tab/按钮）"""
    return plc.enrich(dto)

from services.db import query, query_one, execute, insert
from services import project_lifecycle as plc
from services import report_lifecycle as rlc
from services.knowledge_service import (
    search_laws, count_laws, get_law_detail,
    list_potency_levels, list_timeliness_options,
    search_violations, count_violations, get_violation_detail,
    get_laws_for_violations, list_violation_categories,
    get_audititem_children, get_audititem_tree, search_audititems,
)
from services.regulation_graph import get_regulation_graph, get_law_clauses
from services.expression_engine import execute_expression
from services.template_service import list_templates as tmpl_list
from services import analysis_lifecycle as alc
from services import analysis_context_builder as acb
from services import evidence_service
from agents.registry import AgentRegistry


# ═══════════════════════════════════════════════════════════
#  审计事项（audit_items）辅助函数
# ═══════════════════════════════════════════════════════════
_JSON_ITEM_FIELDS = (
    "common_problems", "required_materials", "common_violations",
    "audit_methods", "legal_bases",
)


def _parse_json_col(v):
    """JSON 列在 pymysql 中以 str/bytes 返回，解析为 list/dict；None 或已解析则原样返回。"""
    if v is None or isinstance(v, (list, dict)):
        return v
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("utf-8", errors="ignore")
    try:
        return json.loads(v)
    except Exception:
        return v


def _norm_priority(v) -> str:
    """priority 归一为 高/中/低，非法值落回 中。"""
    v = (str(v or "")).strip()
    return v if v in ("高", "中", "低") else "中"


def _item_to_dto(row: dict) -> dict:
    """audit_items 行 → 前端 item dict（JSON 列解析为 list）。"""
    d = _clean_row(dict(row)) if row else {}
    for k in _JSON_ITEM_FIELDS:
        d[k] = _parse_json_col(d.get(k)) or []
    tasks = _parse_json_col(d.get("tasks"))
    d["tasks"] = tasks if isinstance(tasks, list) else []
    return d


def _normalize_items(items) -> list:
    """规整 LLM 返回的 audit_items：丢弃无标题项；priority 归一；数组字段补 []。"""
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = (str(it.get("title", "") or "")).strip()
        if not title:
            continue
        clean = {
            "title": title,
            "subtitle": (str(it.get("subtitle", "") or "")).strip(),
            "category": (str(it.get("category", "") or "")).strip(),
            "priority": _norm_priority(it.get("priority", "")),
        }
        for f in _JSON_ITEM_FIELDS:
            v = it.get(f)
            clean[f] = v if isinstance(v, list) else []
        tasks = it.get("tasks")
        clean["tasks"] = tasks if isinstance(tasks, list) else []
        out.append(clean)
    return out


def _ensure_audit_items_table():
    """启动时幂等建表 audit_items（schema.sql 是参考文档非执行脚本，故在此兜底）。"""
    try:
        execute(
            "CREATE TABLE IF NOT EXISTS tt.audit_items ("
            "id INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',"
            "project_id VARCHAR(32) NOT NULL COMMENT '关联项目ID',"
            "seq INT DEFAULT 0 COMMENT '展示顺序',"
            "title VARCHAR(200) NOT NULL COMMENT '事项名称',"
            "subtitle VARCHAR(500) COMMENT '一句话描述',"
            "category VARCHAR(100) COMMENT '分类',"
            "priority VARCHAR(20) COMMENT '高/中/低',"
            "common_problems JSON COMMENT '常见问题表现',"
            "required_materials JSON COMMENT '审计所需资料',"
            "common_violations JSON COMMENT '常见违规行为',"
            "audit_methods JSON COMMENT '常用审计方法',"
            "legal_bases JSON COMMENT '审计依据',"
            "tasks JSON COMMENT '任务分解',"
            "create_time DATETIME DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',"
            "update_time DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',"
            "INDEX idx_project (project_id)"
            ") COMMENT '审计事项'",
            database="tt",
        )
    except Exception:
        pass  # 建表失败不阻塞路由注册；运行时端点会自然报错暴露问题


def register_audit_routes(app):
    """在 Flask app 上注册所有 /api/audit/* 路由"""

    # 启动时幂等建表（audit_items），schema.sql 仅为参考文档
    _ensure_audit_items_table()

    # ═══════════════════════════════════════════════════════════
    #  项目管理
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects", methods=["GET"])
    def audit_projects_list():
        """GET /api/audit/projects — 项目列表（P1.2: 返回立项全字段）"""
        rows = query(
            "SELECT * FROM audit_projects WHERE deleted = 0 ORDER BY create_time DESC",
            database="tt",
        )
        return jsonify({"success": True, "projects": [_project_to_dto(r) for r in rows]})

    @app.route("/api/audit/projects", methods=["POST"])
    def audit_projects_create():
        """POST /api/audit/projects — 创建项目（P1.2 兼容立项全字段；P1-1/2/3: 只存基础信息、draft、不建 bucket）

        兼容旧格式：越阶段字段（scope/target_unit/items 等）被忽略，不报错。
        bucket 延迟到 finalize 创建（PHASE_1 P1-8），此处仅预生成名称返回。
        """
        data = request.get_json() or {}
        # P1-3: 基础字段白名单过滤（越阶段字段丢弃）
        basic = plc.filter_fields("basic", data)
        name = (basic.get("name") or "").strip()
        if not name:
            return jsonify({"success": False, "error": "项目名称不能为空"}), 400

        pid = str(uuid.uuid4()).replace("-", "")[:12]
        minio_bucket = f"audit-project-{pid}"
        # P1-2: status='draft'，不建 bucket（P1-1）
        insert(
            "INSERT INTO audit_projects (id, name, description, audit_period, "
            "minio_bucket, status, creator, create_time, "
            "project_code, audited_unit, audit_type, audit_method, target_level, "
            "leader, auditor, objective, scope, amount, "
            "business_start_date, business_end_date, start_date, entry_date, setup_stage) "
            "VALUES (%s,%s,%s,%s, %s,'draft','system',NOW(), %s,%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s,%s,'basic')",
            (pid, basic.get("name", ""), basic.get("description", ""), basic.get("audit_period", ""),
             minio_bucket,
             basic.get("project_code", ""), basic.get("audited_unit", ""),
             basic.get("audit_type", ""), basic.get("audit_method", ""),
             basic.get("target_level", ""), basic.get("leader", ""), basic.get("auditor", ""),
             basic.get("objective", ""), basic.get("scope", ""), basic.get("amount") or None,
             basic.get("business_start_date") or None, basic.get("business_end_date") or None,
             basic.get("start_date") or None, basic.get("entry_date") or None),
            database="tt",
        )

        row = query_one("SELECT * FROM audit_projects WHERE id = %s", (pid,), database="tt")
        dto = _stage_enrich(_project_to_dto(row))
        # 兼容旧格式：仍返回 bucket（预生成名，未实际创建）
        return jsonify({"success": True, "project": dto, "bucket": minio_bucket})

    @app.route("/api/audit/projects/<project_id>", methods=["GET"])
    def audit_project_detail(project_id):
        """GET /api/audit/projects/<id> — 项目详情（P1.2: 返回立项全字段+旧别名+审计事项）"""
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404
        dto = _project_to_dto(row)
        # 附带已落库的审计事项
        try:
            _ensure_audit_items_table()
            item_rows = query(
                "SELECT * FROM audit_items WHERE project_id = %s ORDER BY seq",
                (project_id,), database="tt",
            )
            dto["audit_items"] = [_item_to_dto(r) for r in item_rows]
        except Exception:
            dto["audit_items"] = []
        return jsonify({"success": True, "project": dto})

    @app.route("/api/audit/projects/<project_id>", methods=["PUT"])
    def audit_project_update(project_id):
        """PUT /api/audit/projects/<id> — 更新立项信息（P1.2 兼容 + P1-3: 按当前 setup_stage 白名单过滤）

        越阶段字段（如 basic 阶段提交 scope）被忽略，不报错（兼容旧前端灰度）。
        focus 为 JSON 列，list/dict 入参序列化。
        """
        data = request.get_json() or {}
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        stage = row.get("setup_stage") or "basic"
        updates = plc.filter_fields(stage, data)
        if "audit_focus" in updates and isinstance(updates["audit_focus"], (list, dict)):
            updates["audit_focus"] = json.dumps(updates["audit_focus"], ensure_ascii=False)
        if not updates:
            return jsonify({"success": False, "error": "没有可更新字段"}), 400
        set_clauses = ", ".join(f"{k} = %s" for k in updates)
        params = list(updates.values()) + [project_id]
        execute(
            f"UPDATE audit_projects SET {set_clauses}, update_time = NOW() "
            f"WHERE id = %s AND deleted = 0",
            params, database="tt",
        )
        row = query_one("SELECT * FROM audit_projects WHERE id = %s", (project_id,), database="tt")
        return jsonify({"success": True, "project": _stage_enrich(_project_to_dto(row))})

    @app.route("/api/audit/projects/<project_id>/target-scope", methods=["PUT"])
    def audit_project_target_scope(project_id):
        """PUT /api/audit/projects/<id>/target-scope — 保存审计对象和范围（P1-4）

        校验：项目存在；setup_stage ≤ target_scope（推进到 items 后不允许回改）；scope 必填。
        从 basic 首次保存时推进 setup_stage 到 target_scope。
        持久化 scope/target_unit/extend_unit/focus（决策 4 确认）。
        """
        data = request.get_json() or {}
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        stage = row.get("setup_stage") or "basic"
        if plc.stage_index(stage) > plc.stage_index("target_scope"):
            return jsonify({
                "success": False,
                "error": "当前阶段不允许修改对象范围",
                "setup_stage": stage,
            }), 409

        fields = plc.filter_fields("target_scope", data)
        scope = (fields.get("scope") or "").strip()
        if not scope:
            return jsonify({
                "success": False,
                "error": "审计范围必填",
                "missing_fields": ["scope"],
            }), 409
        # focus 为 JSON 列，list/dict 入参序列化
        if "audit_focus" in fields and isinstance(fields["audit_focus"], (list, dict)):
            fields["audit_focus"] = json.dumps(fields["audit_focus"], ensure_ascii=False)

        set_clauses = ", ".join(f"{k} = %s" for k in fields)
        params = list(fields.values())
        execute(
            f"UPDATE audit_projects SET {set_clauses}, setup_stage = 'target_scope', "
            f"update_time = NOW() WHERE id = %s AND deleted = 0",
            params + [project_id], database="tt",
        )
        row = query_one("SELECT * FROM audit_projects WHERE id = %s", (project_id,), database="tt")
        return jsonify({"success": True, "project": _stage_enrich(_project_to_dto(row))})

    @app.route("/api/audit/projects/<project_id>/workspace/finalize", methods=["POST"])
    def audit_project_finalize(project_id):
        """POST /api/audit/projects/<id>/workspace/finalize — 创建资料空间并激活项目（P1-8/9）

        校验：setup_stage ≥ items 且 ≥1 项已确认审计事项（check_stage，P1-5 复用）。
        幂等：已激活（status=active 且 workspace_created_at 非空）直接返回，不重复建 bucket。
        bucket：预生成名或已有 minio_bucket；MinIO 失败则不进入 active。
        """
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        # P1-9 幂等：已激活直接返回
        if row.get("status") == "active" and row.get("workspace_created_at"):
            return jsonify({
                "success": True,
                "project": _stage_enrich(_project_to_dto(row)),
                "message": "已激活，幂等返回",
            })

        # 阶段检查（P1-5）：至少 items 阶段且事项数 ≥1
        _ensure_audit_items_table()
        cnt = query_one(
            "SELECT COUNT(*) AS n FROM audit_items WHERE project_id = %s",
            (project_id,), database="tt",
        )
        item_count = cnt["n"] if cnt else 0
        ok, missing = plc.check_stage(row, "items", item_count)
        if not ok:
            return jsonify({
                "success": False,
                "error": "前置阶段未完成，不能创建资料空间",
                "setup_stage": row.get("setup_stage") or "basic",
                "missing_fields": missing,
            }), 409

        # 创建 bucket（幂等）
        minio_bucket = row.get("minio_bucket") or f"audit-project-{project_id}"
        try:
            from services.minio_client import get_client
            client = get_client()
            if not client.bucket_exists(minio_bucket):
                client.make_bucket(minio_bucket)
        except Exception as e:
            return jsonify({"success": False, "error": f"资料空间创建失败: {e}"}), 500

        execute(
            "UPDATE audit_projects SET status = 'active', setup_stage = 'workspace', "
            "workspace_created_at = NOW(), update_time = NOW() "
            "WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        row = query_one("SELECT * FROM audit_projects WHERE id = %s", (project_id,), database="tt")

        # P2-3 §6.6：生成首版 workspace-manifest.json（幂等；失败不阻断 finalize，§7 兜底重建）
        try:
            from services.workspace_service import init_first_manifest, derive_audit_year
            audit_year, _ = derive_audit_year(
                row.get("audit_period"),
                row.get("create_time") or row.get("workspace_created_at"),
            )
            init_first_manifest(project_id, row.get("name") or "", audit_year, minio_bucket)
        except Exception as e:
            print("[finalize] manifest 首版初始化失败（不阻断）: %s" % e)

        return jsonify({
            "success": True,
            "project": _stage_enrich(_project_to_dto(row)),
            "minio_bucket": minio_bucket,
        })

    @app.route("/api/audit/projects/migrate-stages", methods=["POST"])
    def audit_projects_migrate_stages():
        """POST /api/audit/projects/migrate-stages — 存量项目阶段推断（P1-10，决策 5）

        只扫描 setup_stage=basic 的项目（已有阶段不动）。推断规则（优先级高到低）：
          - MinIO bucket 实际存在 → workspace
          - 有 audit_items → items
          - 有 scope → target_scope
          - 否则 → basic
        返回待人工确认清单，**不改库**。
        """
        rows = query("SELECT * FROM audit_projects WHERE deleted = 0", database="tt")
        _ensure_audit_items_table()
        from services.minio_client import get_client
        client = get_client()
        candidates = []
        for p in rows:
            cur = p.get("setup_stage") or "basic"
            if cur != "basic":
                continue  # 已有阶段的不动
            pid = p["id"]
            bucket = p.get("minio_bucket") or f"audit-project-{pid}"
            bucket_exists = False
            try:
                bucket_exists = client.bucket_exists(bucket)
            except Exception:
                pass
            if bucket_exists:
                inferred = "workspace"
            else:
                cnt = query_one(
                    "SELECT COUNT(*) AS n FROM audit_items WHERE project_id = %s",
                    (pid,), database="tt",
                )
                if cnt and cnt["n"] > 0:
                    inferred = "items"
                elif (p.get("scope") or "").strip():
                    inferred = "target_scope"
                else:
                    inferred = "basic"
            candidates.append({
                "id": pid,
                "name": p.get("name", ""),
                "status": p.get("status", ""),
                "current_stage": cur,
                "inferred_stage": inferred,
            })
        return jsonify({"success": True, "candidates": candidates, "count": len(candidates)})

    @app.route("/api/audit/projects/migrate-stages/confirm", methods=["POST"])
    def audit_projects_migrate_confirm():
        """POST /api/audit/projects/migrate-stages/confirm — 批量确认推断结果（P1-10）

        body: {"updates":[{"id":"...","setup_stage":"items"}, ...]}
        推断为 workspace 的项目，若原 status 非 active 则一并激活并记录 workspace_created_at。
        """
        data = request.get_json() or {}
        updates = data.get("updates") or []
        applied = 0
        for u in updates:
            pid = u.get("id")
            stage = u.get("setup_stage")
            if not pid or stage not in plc.STAGES:
                continue
            execute(
                "UPDATE audit_projects SET setup_stage = %s, update_time = NOW() "
                "WHERE id = %s AND deleted = 0",
                (stage, pid), database="tt",
            )
            if stage == "workspace":
                execute(
                    "UPDATE audit_projects SET status = 'active', workspace_created_at = NOW() "
                    "WHERE id = %s AND deleted = 0 AND status != 'active'",
                    (pid,), database="tt",
                )
            applied += 1
        return jsonify({"success": True, "applied": applied})

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
        """POST /api/audit/projects/extract-info — AI 从文本提取项目立项基本信息

        供 projects.html 的"AI辅助立项"使用。
        本功能【只负责审计立项基本信息】——不提取审计对象、审计范围、审计事项
        （它们在后续阶段分别处理）。严格基于文本，提取不到的字段留空（不编造）。
        """
        data = request.get_json() or {}
        text = (data.get("text") or "").strip()
        if not text:
            return jsonify({"success": False, "error": "请提供文档内容"}), 400

        from services.llm_client import call_llm_json

        system_prompt = (
            "你是审计项目立项助手。从用户提供的文档内容中提取【审计立项基本信息】。"
            "严格基于文本内容，提取不到的字段留空字符串，不要编造。"
            "只提取立项基本信息，不要推断审计对象、审计范围或审计事项——它们由后续独立功能处理。"
        )
        prompt = (
            "请从以下审计文档内容中提取【审计立项基本信息】，返回 JSON（提取不到的字段留空字符串）：\n"
            "只提取以下字段，不要返回 audit_items / scope / target_unit / extend_unit：\n"
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
            '  "business_start": "业务发生期间起始 YYYY-MM-DD（如可提取）",\n'
            '  "business_end": "业务发生期间结束 YYYY-MM-DD（如可提取）"\n'
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

    @app.route("/api/audit/projects/split-audit-items", methods=["POST"])
    def audit_project_split_items():
        """POST /api/audit/projects/split-audit-items — AI 根据项目信息拆分审计事项

        供 projects.html Tab3「AI 拆分审计事项」按钮使用：读立项表单信息，
        用 LLM 生成 3-6 个可独立执行的审计事项，每项含完整核查指引
        （常见问题/所需资料/常见违规/审计方法/审计依据/任务分解）。
        """
        data = request.get_json() or {}
        project_name = (data.get("project_name") or data.get("name") or "").strip()
        if not project_name:
            return jsonify({"success": False, "error": "请提供项目名称"}), 400

        from services.llm_client import call_llm_json

        audit_type = (str(data.get("audit_type") or "")).strip()
        target_unit = (str(data.get("target_unit") or "")).strip()
        scope = (str(data.get("scope") or "")).strip()
        objective = (str(data.get("objective") or "")).strip()
        amount = (str(data.get("amount") or "")).strip()

        system_prompt = (
            "你是资深政府审计专家。根据审计项目信息，将审计内容拆分为 3-6 个可独立执行的审计事项，"
            "每个事项给出完整的核查指引。事项必须与该项目的实际业务（审计类型/对象/范围）直接相关，"
            "紧扣项目主题，不要套用无关领域的模板，不要泛泛而谈。"
        )
        prompt = (
            "根据以下审计项目信息，拆分出 3-6 个审计事项，返回 JSON：\n"
            "{\n"
            '  "audit_items": [\n'
            "    {\n"
            '      "title": "事项名称（如：采购方式合规性审计）",\n'
            '      "subtitle": "一句话描述核查内容与重点",\n'
            '      "category": "分类（与项目审计类型一致）",\n'
            '      "priority": "高 或 中 或 低",\n'
            '      "common_problems": ["常见问题表现1", "常见问题表现2"],\n'
            '      "required_materials": ["审计所需资料1", "审计所需资料2"],\n'
            '      "common_violations": ["常见违规行为1", "常见违规行为2"],\n'
            '      "audit_methods": ["审计方法：简要说明（如：合同比对法：比对合同金额与招标门槛）"],\n'
            '      "legal_bases": ["相关法规依据（如：《招标投标法》第4条 — 禁止化整为零）"],\n'
            '      "tasks": [{"name": "审计任务名称", "plan": "计划时间（如第1周）", "status": "待启动"}]\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "项目信息：\n"
            "项目名称：" + project_name + "\n"
            "审计类型：" + (audit_type or "未指定") + "\n"
            "审计对象：" + (target_unit or "未指定") + "\n"
            "审计范围：" + (scope or "未指定") + "\n"
            "审计目标：" + (objective or "未指定") + "\n"
            "涉及金额：" + (amount or "未指定") + " 万元\n"
        )

        result = call_llm_json(prompt, system_prompt=system_prompt, temperature=0.2, timeout=120)
        if "error" in result:
            return jsonify({"success": False, "error": result["error"]}), 503
        items = _normalize_items(result.get("audit_items", []))
        return jsonify({"success": True, "audit_items": items})

    @app.route("/api/audit/projects/<project_id>/items", methods=["GET"])
    def audit_project_items_list(project_id):
        """GET /api/audit/projects/<id>/items — 项目下所有审计事项（按顺序）"""
        _ensure_audit_items_table()
        rows = query(
            "SELECT * FROM audit_items WHERE project_id = %s ORDER BY seq",
            (project_id,), database="tt",
        )
        return jsonify({"success": True, "audit_items": [_item_to_dto(r) for r in rows]})

    @app.route("/api/audit/projects/<project_id>/items", methods=["PUT"])
    def audit_project_items_save(project_id):
        """PUT /api/audit/projects/<id>/items — 全量替换项目的审计事项（落库）"""
        data = request.get_json() or {}
        items = data.get("audit_items")
        if not isinstance(items, list):
            return jsonify({"success": False, "error": "audit_items 必须为数组"}), 400

        _ensure_audit_items_table()
        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        # P1-5 前置阶段校验：保存事项前必须先完成 target_scope（scope 必填），
        # 不允许 basic 跳过对象和范围直存事项（§2 任何客户端不能跳过前序阶段）。
        cur_stage = row.get("setup_stage") or "basic"
        if plc.stage_index(cur_stage) < plc.stage_index("target_scope"):
            return jsonify({
                "success": False,
                "error": "前置阶段未完成，请先完成对象和范围",
                "setup_stage": cur_stage,
                "missing_fields": plc.missing_for(row, "target_scope"),
            }), 409

        # P1-7 乐观锁：可选 expected_update_time，不匹配 → 409（并发覆盖防护；旧前端不传则跳过）
        expected = data.get("expected_update_time")
        if expected:
            cur_ut = row.get("update_time")
            cur_str = cur_ut.isoformat() if hasattr(cur_ut, "isoformat") else str(cur_ut or "")
            if cur_str and expected != cur_str:
                return jsonify({
                    "success": False,
                    "error": "项目已被他人修改，请刷新后重试",
                    "current_update_time": cur_str,
                }), 409

        # 全量替换：先删后插
        execute("DELETE FROM audit_items WHERE project_id = %s", (project_id,), database="tt")
        for seq, it in enumerate(items):
            it = it if isinstance(it, dict) else {}
            title = (str(it.get("title", "") or "")).strip() or "未命名事项"
            insert(
                "INSERT INTO audit_items (project_id, seq, title, subtitle, category, priority, "
                "common_problems, required_materials, common_violations, audit_methods, "
                "legal_bases, tasks) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (project_id, seq, title,
                 (str(it.get("subtitle", "") or "")).strip(),
                 (str(it.get("category", "") or "")).strip(),
                 _norm_priority(it.get("priority", "")),
                 json.dumps(it.get("common_problems") or [], ensure_ascii=False),
                 json.dumps(it.get("required_materials") or [], ensure_ascii=False),
                 json.dumps(it.get("common_violations") or [], ensure_ascii=False),
                 json.dumps(it.get("audit_methods") or [], ensure_ascii=False),
                 json.dumps(it.get("legal_bases") or [], ensure_ascii=False),
                 json.dumps(it.get("tasks") or [], ensure_ascii=False)),
                database="tt",
            )
        # P1-7：保存事项后推进 setup_stage 到 items（仅向前；finalize 负责最终校验）
        if items:
            cur_stage = row.get("setup_stage") or "basic"
            if plc.stage_index(cur_stage) < plc.stage_index("items"):
                execute(
                    "UPDATE audit_projects SET setup_stage = 'items', update_time = NOW() "
                    "WHERE id = %s AND deleted = 0",
                    (project_id,), database="tt",
                )
            else:
                # T8(Gap B)：items/workspace 阶段重存也 bump update_time，
                # 使乐观锁（expected_update_time）在所有阶段都能检出并发冲突
                execute(
                    "UPDATE audit_projects SET update_time = NOW() "
                    "WHERE id = %s AND deleted = 0",
                    (project_id,), database="tt",
                )
        # T8：返回最新 update_time 供前端刷新乐观锁 token（每次成功存后 token 必变）
        ut_row = query_one(
            "SELECT update_time FROM audit_projects WHERE id = %s", (project_id,), database="tt",
        )
        ut_val = ut_row["update_time"] if ut_row else None
        ut_str = ut_val.isoformat() if hasattr(ut_val, "isoformat") else (str(ut_val) if ut_val else "")
        return jsonify({"success": True, "count": len(items), "update_time": ut_str})

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
        # P2-2 前置校验：项目须已 finalize（setup_stage=workspace 且资料空间桶已建）
        proj = query_one(
            "SELECT setup_stage, minio_bucket, name, audit_period, create_time "
            "FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not proj:
            return jsonify({"success": False, "error": "项目不存在"}), 404
        if (proj.get("setup_stage") or "") != "workspace":
            return jsonify({
                "success": False,
                "error": "请先完成立项四阶段并创建资料空间",
                "setup_stage": proj.get("setup_stage") or "basic",
            }), 409
        bucket = proj.get("minio_bucket") or f"audit-project-{project_id}"
        from services.minio_client import get_client
        if not get_client().bucket_exists(bucket):
            return jsonify({
                "success": False,
                "error": "资料空间未就绪，请先 finalize 创建资料空间",
            }), 409

        if "file" not in request.files:
            return jsonify({"success": False, "error": "请选择文件"}), 400

        f = request.files["file"]
        filename = f.filename
        file_bytes = f.read()
        file_id = str(uuid.uuid4()).replace("-", "")[:12]

        # P2-6 年度派生 + 分类 + 安全名（§6.1，规则集中在 workspace_service）
        from services.workspace_service import (
            derive_audit_year, classify_file, compute_safe_name,
            build_file_prefix, build_manifest_path,
            load_manifest, save_manifest, init_first_manifest,
            append_file_to_manifest, build_file_entry,
            update_manifest_atomic,
        )
        audit_year, _ = derive_audit_year(proj.get("audit_period"), proj.get("create_time"))
        category, subcategory = classify_file(filename, f.content_type)
        safe_name = compute_safe_name(proj.get("name") or "")
        # object_key 叶子 = {file_id}.{filename}（保留原名，§3.3 示例口径）
        leaf = "{}.{}".format(file_id, filename)
        cat_part = "{}/{}/{}".format(category, subcategory, leaf) if subcategory else "{}/{}".format(category, leaf)
        minio_path = build_file_prefix(audit_year, project_id, safe_name) + cat_part

        # 1. 计算 MD5
        import hashlib
        file_md5 = hashlib.md5(file_bytes).hexdigest()

        # 2. 去重检查：同项目同 MD5 的文件已存在则返回已有记录
        existing = query_one(
            "SELECT id, file_name, ocr_content IS NOT NULL AS ocr_done "
            "FROM audit_document_traces WHERE project_id = %s AND file_md5 = %s "
            "AND deleted_at IS NULL ORDER BY id DESC LIMIT 1",
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

        # 3. 存入 MinIO（minio_path 由 P2-6 按 §3.1 前缀构造；bucket 由 finalize 创建）
        try:
            client = get_client()
            import io
            client.put_object(bucket, minio_path, io.BytesIO(file_bytes),
                              length=len(file_bytes),
                              content_type=f.content_type or "application/octet-stream")
        except Exception as e:
            return jsonify({"success": False, "error": f"文件存储失败: {e}"}), 500

        # 4. 创建溯源记录（含 MD5 + P2-6 资料空间列）
        trace_id = insert(
            "INSERT INTO audit_document_traces "
            "(project_id, file_name, file_md5, minio_path, ocr_version, created_at, "
            "audit_year, file_category, file_subcategory, minio_bucket, file_size, parse_status) "
            "VALUES (%s,%s,%s,%s,1,NOW(),%s,%s,%s,%s,%s,'pending')",
            (project_id, filename, file_md5, minio_path,
             audit_year, category, subcategory, bucket, len(file_bytes)),
            database="tt",
        )

        # P2-6 §6.1 manifest 增量追加（upload 为唯一合法变更点之一，§7；失败不阻断上传）
        # 并发保护：多文件并发上传时 load→append→save 是读-改-写，后写覆盖先写会丢条目
        # （lost update）。改用 per-project 写锁的原子更新。
        try:
            mpath = build_manifest_path(audit_year, project_id, safe_name)
            _entry = build_file_entry(
                trace_id=trace_id, file_name=filename, object_key=minio_path,
                category=category, subcategory=subcategory,
                size=len(file_bytes), md5=file_md5,
                content_type=f.content_type or "application/octet-stream",
            )

            def _append(m):
                if m is None:
                    m = init_first_manifest(project_id, proj.get("name") or "", audit_year, bucket)
                append_file_to_manifest(m, _entry)
                return m

            update_manifest_atomic(project_id, bucket, mpath, _append)
        except Exception as e:
            print("[upload] manifest 增量写入失败（不阻断上传）: %s" % e)

        # 5. 提交异步 OCR 任务（P3-2：入参走 payload 列，不再塞 result；sku_profile 管道打通）
        from services.task_manager import create_task
        from services.task_worker import submit_task
        task_result = create_task(
            task_name=filename,
            task_type="ocr",
            project_id=project_id,
            payload={
                "trace_id": trace_id,
                "minio_bucket": bucket,
                "minio_path": minio_path,
                "filename": filename,
                "project_id": project_id,
                "sku_profile": None,  # 预留：前端指定模板 profile（P3-2 打通管道，当前 None）
            },
        )
        task_id = task_result.get("task", {}).get("id")

        if task_id:
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

    # ═══════════════════════════════════════════════════════════
    #  报告管理（报告段状态机 report_stage + 交付物 audit_deliverables）
    #  报告段状态走独立列 report_stage，不共用 status（status='archived' 是软删除）。
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects/<project_id>/report-transition", methods=["POST"])
    def audit_project_report_transition(project_id):
        """POST /api/audit/projects/<id>/report-transition — 推进报告段状态机

        body: {"to": "drafting", "expected_update_time"?: "..."}
        校验顺序：① 目标阶段合法 ② 项目存在 ③ can_transition（防跨级/回退）
                  ④ check_prerequisites（drafting 要 active+workspace；reviewing 要 report 交付物；
                     issued 要 adopted report；filed 要 archive_no）⑤ 乐观锁 token。
        成功：UPDATE report_stage + report_stage_changed_at + bump update_time（与三存端点一致）。
        """
        data = request.get_json() or {}
        target = data.get("to")
        if target not in rlc.REPORT_STAGES:
            return jsonify({
                "success": False, "error": "非法目标阶段: {}".format(target),
                "report_stages": rlc.REPORT_STAGES,
            }), 400

        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        cur = row.get("report_stage")  # None = 未启动报告段

        # ③ 合法转换（TRANSITIONS 白名单，防 drafting→filed 跨级）
        if not rlc.can_transition(cur, target):
            return jsonify({
                "success": False,
                "error": "不允许从 {} 推进到 {}".format(cur, target),
                "report_stage": cur,
                "allowed_next": rlc.TRANSITIONS.get(cur, []),
            }), 409

        # ④ 前置条件（reviewing/issued 需查交付物；drafting/filed 只看项目行）
        delivs = []
        if target in ("reviewing", "issued"):
            delivs = query(
                "SELECT deliverable_type, status FROM audit_deliverables "
                "WHERE project_id = %s",
                (project_id,), database="tt",
            )
        ok, missing = rlc.check_prerequisites(row, target, delivs)
        if not ok:
            return jsonify({
                "success": False,
                "error": "前置条件未满足",
                "report_stage": cur,
                "missing": missing,
            }), 409

        # ⑤ 乐观锁（可选 expected_update_time，复用 items-save 机制）
        expected = data.get("expected_update_time")
        if expected:
            cur_ut = row.get("update_time")
            cur_str = cur_ut.isoformat() if hasattr(cur_ut, "isoformat") else str(cur_ut or "")
            if cur_str and expected != cur_str:
                return jsonify({
                    "success": False,
                    "error": "项目已被他人修改，请刷新后重试",
                    "current_update_time": cur_str,
                }), 409

        execute(
            "UPDATE audit_projects SET report_stage = %s, "
            "report_stage_changed_at = NOW(), update_time = NOW() "
            "WHERE id = %s AND deleted = 0",
            (target, project_id), database="tt",
        )
        ut_row = query_one(
            "SELECT update_time, report_stage FROM audit_projects WHERE id = %s",
            (project_id,), database="tt",
        )
        ut_val = ut_row["update_time"] if ut_row else None
        ut_str = ut_val.isoformat() if hasattr(ut_val, "isoformat") else (str(ut_val) if ut_val else "")
        return jsonify({
            "success": True,
            "report_stage": target,
            "update_time": ut_str,
            "report_allowed_actions": rlc.allowed_actions(target),
        })

    @app.route("/api/audit/projects/<project_id>/report-meta", methods=["PUT"])
    def audit_project_report_meta(project_id):
        """PUT /api/audit/projects/<id>/report-meta — 更新报告台账字段（项目级）

        白名单：review_deadline / archive_no / archive_date（报告段项目级字段；
        文书属性归 audit_deliverables，不在此）。空串视作清空（NULL）。
        带乐观锁 expected_update_time（复用 items-save 机制）。bump update_time。
        解决"issued→filed 需 archive_no 但无端点可填"的缺口。
        """
        data = request.get_json() or {}
        allowed = ("review_deadline", "archive_no", "archive_date")
        updates = {}
        for k in allowed:
            if k in data:
                v = data[k]
                if isinstance(v, str):
                    v = v.strip() or None
                updates[k] = v
        if not updates:
            return jsonify({
                "success": False,
                "error": "没有可更新字段（允许: review_deadline/archive_no/archive_date）",
            }), 400

        row = query_one(
            "SELECT * FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not row:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        # 乐观锁（可选 expected_update_time）
        expected = data.get("expected_update_time")
        if expected:
            cur_ut = row.get("update_time")
            cur_str = cur_ut.isoformat() if hasattr(cur_ut, "isoformat") else str(cur_ut or "")
            if cur_str and expected != cur_str:
                return jsonify({
                    "success": False,
                    "error": "项目已被他人修改，请刷新后重试",
                    "current_update_time": cur_str,
                }), 409

        set_clauses = ", ".join("{} = %s".format(k) for k in updates)
        params = list(updates.values()) + [project_id]
        try:
            execute(
                "UPDATE audit_projects SET {}, update_time = NOW() "
                "WHERE id = %s AND deleted = 0".format(set_clauses),
                params, database="tt",
            )
        except Exception as e:
            return jsonify({
                "success": False,
                "error": "更新失败（检查日期格式 YYYY-MM-DD）: {}".format(e),
            }), 400

        row2 = query_one(
            "SELECT review_deadline, archive_no, archive_date, update_time "
            "FROM audit_projects WHERE id = %s",
            (project_id,), database="tt",
        )
        dto = _clean_row(row2) if row2 else {}
        return jsonify({
            "success": True,
            "update_time": dto.get("update_time"),
            "report_meta": {k: dto.get(k) for k in ("review_deadline", "archive_no", "archive_date")},
        })

    @app.route("/api/audit/projects/<project_id>/deliverables", methods=["GET"])
    def audit_deliverables_list(project_id):
        """GET /api/audit/projects/<id>/deliverables — 交付物列表（带版本）

        query: type（可选，过滤 deliverable_type，如 report/decision）。
        """
        dtype = (request.args.get("type") or "").strip() or None
        if dtype:
            rows = query(
                "SELECT * FROM audit_deliverables WHERE project_id = %s "
                "AND deliverable_type = %s ORDER BY version DESC, id DESC",
                (project_id, dtype), database="tt",
            )
        else:
            rows = query(
                "SELECT * FROM audit_deliverables WHERE project_id = %s "
                "ORDER BY deliverable_type, version DESC, id DESC",
                (project_id,), database="tt",
            )
        return jsonify({"success": True, "deliverables": [_clean_row(r) for r in rows]})

    @app.route("/api/audit/projects/<project_id>/deliverables", methods=["POST"])
    def audit_deliverable_create(project_id):
        """POST /api/audit/projects/<id>/deliverables — 上传交付物（multipart）

        form: deliverable_type（必填，report/decision/review_feedback/rectification_report）
              version?/deliverable_no?/title?/issue_date?/status?
        file: 正文文件（必填）。存项目 bucket（audit-project-{pid}），object_key =
              build_file_prefix(...) + deliverables/{type}/{file_id}.{filename}。
        跳过 manifest/OCR/trace（那些是被审计资料专属，交付物不需要）。
        """
        proj = query_one(
            "SELECT name, audit_period, create_time, minio_bucket "
            "FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not proj:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        dtype = (request.form.get("deliverable_type") or "").strip()
        if dtype not in ("report", "decision", "review_feedback", "rectification_report"):
            return jsonify({"success": False, "error": "deliverable_type 非法"}), 400
        if "file" not in request.files:
            return jsonify({"success": False, "error": "请选择文件"}), 400

        bucket = proj.get("minio_bucket") or "audit-project-{}".format(project_id)
        from services.minio_client import upload_file, get_client
        try:
            if not get_client().bucket_exists(bucket):
                return jsonify({"success": False, "error": "资料空间未就绪，请先 finalize"}), 409
        except Exception as e:
            return jsonify({"success": False, "error": "存储检查失败: {}".format(e)}), 500

        f = request.files["file"]
        filename = f.filename
        file_bytes = f.read()
        file_id = str(uuid.uuid4()).replace("-", "")[:12]

        from services.workspace_service import (
            derive_audit_year, compute_safe_name, build_file_prefix,
        )
        audit_year, _ = derive_audit_year(proj.get("audit_period"), proj.get("create_time"))
        safe_name = compute_safe_name(proj.get("name") or "")
        leaf = "{}.{}".format(file_id, filename)
        object_key = "{}deliverables/{}/{}".format(
            build_file_prefix(audit_year, project_id, safe_name), dtype, leaf)

        try:
            upload_file(file_bytes, object_key,
                        content_type=f.content_type or "application/octet-stream",
                        bucket=bucket)
        except Exception as e:
            return jsonify({"success": False, "error": "文件存储失败: {}".format(e)}), 500

        # version：前端显式传则用，否则同 type 内 MAX(version)+1
        # （并发上传同 type 罕见，接受读-写间隙；如需强一致可加 project+type+version 唯一索引）
        if (request.form.get("version") or "").isdigit():
            version = int(request.form.get("version"))
        else:
            maxv = query_one(
                "SELECT MAX(version) AS m FROM audit_deliverables "
                "WHERE project_id = %s AND deliverable_type = %s",
                (project_id, dtype), database="tt")
            version = (maxv["m"] or 0) + 1 if maxv else 1
        deliverable_no = (request.form.get("deliverable_no") or "").strip() or None
        title = (request.form.get("title") or "").strip() or None
        issue_date = (request.form.get("issue_date") or "").strip() or None
        dstatus = (request.form.get("status") or "draft").strip()

        deliv_id = insert(
            "INSERT INTO audit_deliverables "
            "(project_id, deliverable_type, version, deliverable_no, title, issue_date, "
            "minio_path, minio_bucket, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (project_id, dtype, version, deliverable_no, title, issue_date,
             object_key, bucket, dstatus, "system"),
            database="tt",
        )
        return jsonify({
            "success": True,
            "deliverable_id": deliv_id,
            "minio_bucket": bucket,
            "minio_path": object_key,
            "message": "交付物已上传",
        })

    @app.route("/api/audit/projects/<project_id>/files", methods=["GET"])
    def audit_files_list(project_id):
        """GET /api/audit/projects/<id}/files — 文件列表（P2-7，manifest 单一事实源）

        query: year / category（可选过滤）；默认返回全部未删文件。
        数据源：workspace-manifest.json；ocr_done 与 trace 表 join（§6.2）。
        响应增加：audit_year / category / subcategory / size / deleted。
        """
        year_q = (request.args.get("year") or "").strip() or None
        category_q = (request.args.get("category") or "").strip() or None

        proj = query_one(
            "SELECT name, audit_period, create_time FROM audit_projects "
            "WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not proj:
            return jsonify({"success": False, "error": "项目不存在"}), 404

        from services.workspace_service import (
            derive_audit_year, compute_safe_name, build_manifest_path, load_manifest,
        )
        audit_year, _ = derive_audit_year(proj.get("audit_period"), proj.get("create_time"))
        bucket = "audit-project-{}".format(project_id)
        mpath = build_manifest_path(audit_year, project_id, compute_safe_name(proj.get("name") or ""))
        manifest = load_manifest(bucket, mpath)
        raw_files = (manifest or {}).get("files", [])

        out, trace_ids = [], []
        # 单项目 manifest 只有一个年度；year 不匹配 → 整体空
        if year_q and year_q != audit_year:
            out = []
        else:
            for f in raw_files:
                if f.get("deleted"):
                    continue  # 默认过滤软删（§6.2）
                if category_q and f.get("category") != category_q:
                    continue
                out.append({
                    "trace_id": f.get("trace_id"),
                    "file_name": f.get("file_name"),
                    "minio_path": f.get("object_key"),
                    "audit_year": audit_year,
                    "category": f.get("category"),
                    "subcategory": f.get("subcategory"),
                    "size": f.get("size"),
                    "deleted": False,
                    "uploaded_at": f.get("uploaded_at"),
                })
                if f.get("trace_id") is not None:
                    trace_ids.append(f["trace_id"])

        # join ocr_done（trace 表）
        ocr_map = {}
        if trace_ids:
            placeholders = ",".join(["%s"] * len(trace_ids))
            rows = query(
                "SELECT id, ocr_content IS NOT NULL AS ocr_done FROM audit_document_traces "
                "WHERE id IN (%s)" % placeholders,
                tuple(trace_ids), database="tt",
            )
            ocr_map = {r["id"]: bool(r["ocr_done"]) for r in rows}
        for f in out:
            f["ocr_done"] = ocr_map.get(f.get("trace_id"), False)

        return jsonify({
            "success": True, "project_id": project_id,
            "year": audit_year, "category": category_q,
            "files": out,
        })

    @app.route("/api/audit/workspace/tree", methods=["GET"])
    def audit_workspace_tree():
        """GET /api/audit/workspace/tree?year= — 年度项目树（P2-5/P2-10，§6.3）

        读所有 setup_stage=workspace 项目，按 audit_year 过滤（不串年度，P2-10），
        返回 manifest 汇总的年度—项目—类型—文件树。manifest 缺失 → 回退 trace 对账、
        重建首版 manifest 并告警（不静默，§6.3/§7）。
        """
        year_q = (request.args.get("year") or "").strip() or None

        projects = query(
            "SELECT id, name, audit_period, create_time, minio_bucket "
            "FROM audit_projects WHERE deleted = 0 AND setup_stage = 'workspace' "
            "ORDER BY create_time DESC",
            (), database="tt",
        )
        from services.workspace_service import (
            derive_audit_year, compute_safe_name, build_manifest_path,
            load_manifest, init_first_manifest, save_manifest,
            append_file_to_manifest, build_file_entry,
            update_manifest_atomic,
        )
        cats = ["text", "image", "audio", "video", "other"]
        out = []
        for p in projects:
            audit_year, _ = derive_audit_year(p.get("audit_period"), p.get("create_time"))
            if year_q and year_q != audit_year:
                continue  # P2-10：年度树不串年度
            pid = p["id"]
            bucket = p.get("minio_bucket") or "audit-project-{}".format(pid)
            mpath = build_manifest_path(audit_year, pid, compute_safe_name(p.get("name") or ""))
            # 单项目异常不应炸全树：manifest 读/重建（含 MinIO 写）失败 → 跳过并告警，
            # 该项目以空文件列表呈现（如 bucket 名非法等历史残留）。
            try:
                manifest = load_manifest(bucket, mpath)
            except Exception as e:
                print("[tree] WARN 项目 %s load_manifest 失败，跳过: %s" % (pid, e))
                out.append({
                    "project_id": pid, "project_name": p.get("name"),
                    "safe_name": compute_safe_name(p.get("name") or ""),
                    "audit_year": audit_year,
                    "counts": {c: 0 for c in cats}, "files": [],
                })
                continue
            counts = {c: 0 for c in cats}
            files_out = []
            if manifest:
                for f in manifest.get("files", []):
                    if f.get("deleted"):
                        continue
                    cat = f.get("category") or "other"
                    counts[cat] = counts.get(cat, 0) + 1
                    files_out.append({
                        "trace_id": f.get("trace_id"),
                        "file_name": f.get("file_name"),
                        "category": f.get("category"),
                        "subcategory": f.get("subcategory"),
                        "size": f.get("size"),
                        "uploaded_at": f.get("uploaded_at"),
                    })
            else:
                # §6.3/§7 兜底：manifest 缺失 → trace 对账重建首版并告警（不静默）
                traces = query(
                    "SELECT id, file_name, file_category, file_subcategory, file_size, "
                    "minio_path, created_at FROM audit_document_traces "
                    "WHERE project_id = %s AND deleted_at IS NULL ORDER BY id",
                    (pid,), database="tt",
                )
                if traces:
                    print("[tree] WARN 项目 %s manifest 缺失，回退 trace 对账重建" % pid)
                    try:
                        _traces = traces

                        def _rebuild(m):
                            if m is None:
                                m = init_first_manifest(pid, p.get("name") or "", audit_year, bucket)
                            exist_ids = {f.get("trace_id") for f in m.get("files", [])}
                            for t in _traces:
                                if t["id"] in exist_ids:
                                    continue  # 已存在不重复追加
                                cat = t.get("file_category") or "other"
                                counts[cat] = counts.get(cat, 0) + 1
                                files_out.append({
                                    "trace_id": t["id"],
                                    "file_name": t.get("file_name"),
                                    "category": cat,
                                    "subcategory": t.get("file_subcategory"),
                                    "size": t.get("file_size"),
                                    "uploaded_at": str(t["created_at"]) if t.get("created_at") else None,
                                })
                                append_file_to_manifest(m, build_file_entry(
                                    trace_id=t["id"], file_name=t.get("file_name") or "",
                                    object_key=t.get("minio_path") or "", category=cat,
                                    subcategory=t.get("file_subcategory"),
                                    size=t.get("file_size"), legacy_raw=True,
                                ))
                            return m

                        update_manifest_atomic(pid, bucket, mpath, _rebuild,
                                               fallback_manifest=init_first_manifest(
                                                   pid, p.get("name") or "", audit_year, bucket))
                    except Exception as e:
                        # 重建写 MinIO 失败（如 bucket 名非法）：不炸全树，告警并以已装配文件列表返回
                        print("[tree] WARN 项目 %s manifest 重建失败，跳过 MinIO 写: %s" % (pid, e))
            out.append({
                "project_id": pid,
                "project_name": p.get("name"),
                "safe_name": compute_safe_name(p.get("name") or ""),
                "audit_year": audit_year,
                "counts": counts,
                "files": files_out,
            })
        return jsonify({"success": True, "year": year_q, "projects": out})

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
        """POST /api/audit/documents/reparse — 重新解析（P3-12：异步重跑 OCR+提取）

        重写为异步 OCR 任务（原实现仅同步重提取、不重 OCR/不写库/不 ocr_version+1，名实不符）：
          1. 校验 trace 存在 + 关联项目 setup_stage=workspace（OCR 类操作统一前置，§6 约定）
          2. 从 trace 读 minio 信息，入队 ocr 任务（payload 与 upload 同形态 + is_reparse=True）
          3. 立即返回 task_id（不再同步返 extract_result）
        worker 跑完后 trace.ocr_version 自然 +1、各列覆盖写（决策3；旧版本 Phase 4 标 superseded）。
        """
        data = request.get_json() or {}
        doc_id = data.get("document_id")
        template_name = data.get("template_name", "")
        if not doc_id:
            return jsonify({"success": False, "error": "请提供 document_id"}), 400

        # 1. 查 trace（document_id 即 trace.id）
        trace = query_one(
            "SELECT id, project_id, file_name, minio_bucket, minio_path, ocr_version "
            "FROM audit_document_traces WHERE id = %s",
            (doc_id,), database="tt",
        )
        if not trace:
            return jsonify({"success": False, "error": "溯源记录不存在"}), 404

        # 2. 校验项目 setup_stage=workspace（OCR 类操作统一前置，§6 约定）
        project_id = trace.get("project_id") or ""
        proj = query_one(
            "SELECT setup_stage FROM audit_projects WHERE id = %s AND deleted = 0",
            (project_id,), database="tt",
        )
        if not proj:
            return jsonify({"success": False, "error": "关联项目不存在"}), 404
        if (proj.get("setup_stage") or "") != "workspace":
            return jsonify({
                "success": False,
                "error": "项目未完成资料空间创建，不能重新解析",
                "setup_stage": proj.get("setup_stage") or "basic",
            }), 409

        # 3. 从 trace 读 minio 信息，入队异步 OCR 任务（payload 与 upload 同形态 + is_reparse 标记）
        from services.task_manager import create_task
        from services.task_worker import submit_task
        task_result = create_task(
            task_name=f"reparse:{trace.get('file_name') or doc_id}",
            task_type="ocr",
            project_id=project_id,
            payload={
                "trace_id": trace["id"],
                "minio_bucket": trace.get("minio_bucket"),
                "minio_path": trace.get("minio_path"),
                "filename": trace.get("file_name") or "",
                "project_id": project_id,
                "sku_profile": template_name or None,  # 前端可指定模板 profile
                "is_reparse": True,  # 决策3：worker 覆盖写 + ocr_version+1（旧版本 Phase 4 标 superseded）
            },
        )
        task_id = task_result.get("task", {}).get("id")
        if task_id:
            submit_task(task_id)

        return jsonify({
            "success": True,
            "document_id": doc_id,
            "task_id": task_id,
            "ocr_version": trace.get("ocr_version") or 1,
            "message": "重新解析已入队，完成后 ocr_version 递增" if task_id else "入队失败",
        })

    @app.route("/api/audit/traces/<result_type>/<result_id>", methods=["GET"])
    def audit_trace_provenance(result_type, result_id):
        """GET /api/audit/traces/<result_type>/<result_id> — 完整溯源链（P4-8）

        聚合 audit_source_refs（EvidenceService.get_refs，含 expired 推导）+
        audit_field_sources（JOIN audit_document_chunks 取原文/页码/坐标/status）。
        可选 ?table= 限定数据表（data_row 行 id 跨表可能重复）。

        P4-10 推导（留痕不删）：
          - chunk.status='superseded' → refs/field_sources 的 expired=True（证据已过期，待复核）；
          - chunk page_nums 空 → has_page=False（无精确页码，待人工核实，决策6）。
        本 Phase result_type∈{document,data_row}；其余类型（AI 结论类）有数据即返，无则空。
        无任何溯源数据 → 404。
        """
        from services import evidence_service as es
        table = request.args.get("table")

        refs = es.get_refs(result_type, result_id)
        refs_out = [{
            "source_type": r.get("source_type"),
            "source_id": r.get("source_id"),
            "document_id": r.get("document_id"),
            "file_name": r.get("file_name"),
            "page_number": r.get("page_number"),
            "bbox": _json_loads(r.get("bbox")),
            "quote": r.get("quote"),
            "relation": r.get("relation"),
            "expired": r.get("expired"),
        } for r in refs]

        field_sources_out = []
        if result_type == "data_row":
            if table:
                fs_rows = query(
                    "SELECT fs.table_name, fs.row_id, fs.field_name, fs.chunk_id, fs.ocr_version, "
                    "c.text AS chunk_text, c.page_nums AS chunk_page_nums, "
                    "c.bbox AS chunk_bbox, c.status AS chunk_status "
                    "FROM audit_field_sources fs "
                    "LEFT JOIN audit_document_chunks c ON fs.chunk_id = c.id "
                    "WHERE fs.row_id = %s AND fs.table_name = %s ORDER BY fs.id",
                    (result_id, table), database="tt",
                )
            else:
                fs_rows = query(
                    "SELECT fs.table_name, fs.row_id, fs.field_name, fs.chunk_id, fs.ocr_version, "
                    "c.text AS chunk_text, c.page_nums AS chunk_page_nums, "
                    "c.bbox AS chunk_bbox, c.status AS chunk_status "
                    "FROM audit_field_sources fs "
                    "LEFT JOIN audit_document_chunks c ON fs.chunk_id = c.id "
                    "WHERE fs.row_id = %s ORDER BY fs.id",
                    (result_id,), database="tt",
                )
            for fr in fs_rows:
                page_nums = _json_loads(fr.get("chunk_page_nums"))
                bbox = _json_loads(fr.get("chunk_bbox"))
                status = fr.get("chunk_status")
                chunk_id = fr.get("chunk_id")
                field_sources_out.append({
                    "table_name": fr.get("table_name"),
                    "row_id": fr.get("row_id"),
                    "field_name": fr.get("field_name"),
                    "chunk_id": chunk_id,
                    "ocr_version": fr.get("ocr_version"),
                    "chunk": {
                        "text": fr.get("chunk_text"),
                        "page_nums": page_nums,
                        "bbox": bbox,
                        "status": status,
                    } if chunk_id else None,
                    "expired": (status == "superseded"),
                    "has_page": bool(page_nums),
                })

        if not refs_out and not field_sources_out:
            return jsonify({
                "success": False, "error": "未找到该结果的溯源数据",
                "result_type": result_type, "result_id": result_id,
            }), 404

        return jsonify({
            "success": True,
            "result_type": result_type,
            "result_id": result_id,
            "refs": refs_out,
            "field_sources": field_sources_out,
        })

    # ═══════════════════════════════════════════════════════════
    #  数据工坊
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/projects/<project_id>/data", methods=["GET"])
    def audit_data_tables(project_id):
        """GET /api/audit/projects/<id>/data — 8张数据表项目级行数（P5-2）"""
        tables = list_table_counts(project_id)
        return jsonify({"success": True, "project_id": project_id, "tables": tables})

    @app.route("/api/audit/data/tables", methods=["GET"])
    def audit_data_tables_global():
        """GET /api/audit/data/tables — 8张数据表全库行数（全局浏览，P5-2）"""
        tables = list_table_counts(None)
        return jsonify({"success": True, "tables": tables})

    @app.route("/api/audit/data/<table_name>/rows", methods=["GET"])
    def audit_data_rows(table_name):
        """GET /api/audit/data/<table>/rows — 全局浏览模式（无 project_id，硬 cap 200，P5-3）

        全局浏览：不按项目过滤，per_page 硬 cap 200（DataService 兜底）。
        项目级行查询见 /projects/<id>/data/<table>/rows（项目分析模式）。
        """
        try:
            args = request.args
            filters = parse_query_filters(table_name, args.to_dict())
            fields_list = [f.strip() for f in args.get("fields", "").split(",") if f.strip()] or None
            result = list_rows(
                table_name, project_id=None, filters=filters,
                page=args.get("page", 1, type=int),
                per_page=args.get("per_page", 20, type=int),
                after=args.get("after", type=int),
                fields=fields_list,
            )
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        return jsonify({"success": True, "table": table_name, **result})

    @app.route("/api/audit/projects/<project_id>/data/<table_name>/rows", methods=["GET"])
    def audit_data_rows_project(project_id, table_name):
        """GET /api/audit/projects/<id>/data/<table>/rows — 项目分析模式（project_id 强制，P5-3/P5-4）

        project_id 路径参数强制非空；空/伪造 → DataService 拒绝（ProjectIDRequiredError→400）。
        DataService 内部附加 WHERE project_id=%s，调用方/LLM 无法绕过跨项目隔离。
        """
        try:
            args = request.args
            filters = parse_query_filters(table_name, args.to_dict())
            fields_list = [f.strip() for f in args.get("fields", "").split(",") if f.strip()] or None
            result = list_rows(
                table_name, project_id=project_id, require_project=True, filters=filters,
                page=args.get("page", 1, type=int),
                per_page=args.get("per_page", 20, type=int),
                after=args.get("after", type=int),
                fields=fields_list,
            )
        except ProjectIDRequiredError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        except ValueError as e:
            return jsonify({"success": False, "error": str(e)}), 400
        return jsonify({"success": True, "table": table_name, "project_id": project_id, **result})

    @app.route("/api/audit/projects/<project_id>/data/quality", methods=["GET"])
    def audit_data_quality(project_id):
        """GET /projects/<id>/data/quality — 数据质量报告（P5-7，项目分析模式）

        每表空值率 + 金额列 min/max + 金额单位异常软告警（决策11：元）。
        """
        report = quality_check(project_id)
        return jsonify({"success": True, **report})

    @app.route("/api/audit/projects/<project_id>/data/missing", methods=["GET"])
    def audit_data_missing(project_id):
        """GET /projects/<id>/data/missing — 关键业务列缺失清单（P5-8，项目分析模式）

        DB 仅 project_id NOT NULL；关键列应用层定义（DataService.KEY_COLS）。
        """
        report = missing_check(project_id)
        return jsonify({"success": True, **report})

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
        """GET /api/audit/knowledge/violations — 违规行为检索
        参数: q/severity/is_reviewed/category/page/per_page
        返回附带 categories（审计事项分类列表，供下拉框）"""
        q = request.args.get("q", "")
        severity = request.args.get("severity")
        is_reviewed = request.args.get("is_reviewed", type=int)
        category = request.args.get("category", "")
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 50, type=int), 200)
        offset = (page - 1) * per_page

        rows = search_violations(q, severity=severity, is_reviewed=is_reviewed,
                                 category=category or None, limit=per_page, offset=offset)
        total = count_violations(q, severity=severity, is_reviewed=is_reviewed,
                                 category=category or None)
        categories = list_violation_categories()

        return jsonify({
            "success": True,
            "violations": rows,
            "total": total,
            "page": page,
            "per_page": per_page,
            "categories": categories,
        })

    @app.route("/api/audit/knowledge/violations/laws", methods=["GET"])
    def audit_knowledge_violation_laws():
        """GET /api/audit/knowledge/violations/laws?violation_ids=1,2,3
        按多个违规 id 批量查关联法规（去重，按被引违规数降序）。
        供 AW 第三步「审计依据」按所选违规带出对应法规——替代旧的全库前 N 条无匹配兜底。"""
        raw = request.args.get("violation_ids", "")
        ids = [s.strip() for s in raw.split(",") if s.strip().isdigit()]
        laws = get_laws_for_violations(ids)
        return jsonify({"success": True, "laws": laws, "total": len(laws)})

    @app.route("/api/audit/knowledge/violations/<int:violation_id>", methods=["GET"])
    def audit_knowledge_violation_detail(violation_id):
        """GET /api/audit/knowledge/violations/<id> — 违规行为详情
        （含 audit_procedure/required_data + 关联法规 laws 数组）
        关联法规从 audit_violation_law_refs 关联 audit_law 法规库，
        未匹配 law_id 的法规仅返回 law_title。"""
        detail = get_violation_detail(violation_id)
        if not detail:
            return jsonify({"success": False, "error": "违规行为不存在"}), 404

        # 关联法规（跨库 JOIN + COLLATE 解决字符集不一致）
        laws = query(
            "SELECT vl.law_id, vl.law_title, vl.clause_ref, l.title, l.potency_level "
            "FROM tt.audit_violation_law_refs vl "
            "LEFT JOIN audit_law.sys_core_law_allaudit l "
            "ON vl.law_id COLLATE utf8mb4_0900_ai_ci = l.id "
            "WHERE vl.violation_id = %s ORDER BY vl.id",
            (violation_id,), database="tt",
        )
        law_list = []
        for r in laws:
            title = r.get("title") or r.get("law_title") or ""
            if title and not title.startswith("《"):
                title = f"《{title}》"
            law_list.append({
                "law_id": r.get("law_id"),
                "title": title,
                "potency_level": r.get("potency_level") or "",
                "clause_ref": r.get("clause_ref") or "",
                "matched": bool(r.get("title")),
            })
        return jsonify({"success": True, "violation": detail, "laws": law_list})

    @app.route("/api/audit/knowledge/regulations", methods=["GET"])
    def audit_knowledge_regulations():
        """GET /api/audit/knowledge/regulations — 法规检索"""
        q = request.args.get("q", "")
        potency_level = request.args.get("potency_level")
        timeliness = request.args.get("timeliness")
        page = request.args.get("page", 1, type=int)
        per_page = min(request.args.get("per_page", 50, type=int), 200)
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
        """POST /api/audit/expression/execute — 执行违规表达式（P2.1: 支持violation_ids批量+table自动探测）"""
        data = request.get_json() or {}
        project_id = data.get("project_id", "")

        # P2.1: 批量模式（violation_ids → 每个违规取表达式+探测表+执行+命中明细）
        violation_ids = data.get("violation_ids", [])
        if violation_ids:
            from services.execution_planner import build_and_execute
            results = build_and_execute(violation_ids, project_id)
            return jsonify({"success": True, "results": results})

        # 兼容旧模式（单 expression）
        expression = data.get("expression", "")
        table = data.get("table", "")
        if not expression:
            return jsonify({"success": False, "error": "请提供违规表达式或 violation_ids"}), 400
        # P2.1: table 为空时自动探测
        if not table:
            from services.execution_planner import detect_target_table
            table = detect_target_table(expression, project_id)
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
        """POST /api/audit/suspicion/generate — Step6 疑点生成（附录A §7）

        P8-7: 收 task_id，由 ContextBuilder 装配 Step5命中+Step3法规；Agent 仅组织疑点；
        结果落 project_suspicions（verify_status=MODEL_FOUND，待人工核实流转）。
        body: {task_id} 或兼容旧 {project_id, analysis_results, ...}
        """
        data = request.get_json() or {}
        task_id = data.get("task_id")

        project_id = data.get("project_id", "")
        analysis_results = data.get("analysis_results", [])
        selected_laws = data.get("selected_laws", [])
        primary_laws = data.get("primary_laws", [])

        # 有 task_id 则从 DB 权威装配上下文（P8-10），否则用 body（旧前端兼容）
        if task_id:
            ctx = acb.build(task_id, step=6) or {}
            sd = ctx.get("confirmed_results", {}) or {}
            project_id = project_id or ctx["task"].get("project_id", "")
            analysis_results = analysis_results or sd.get("analysis_results", [])
            selected_laws = selected_laws or sd.get("selected_laws", [])
            primary_laws = primary_laws or sd.get("primary_laws", [])

        agent = AgentRegistry().create_agent("suspicion_generator")
        result = agent.run({
            "analysis_results": analysis_results,
            "overall_assessment": data.get("overall_assessment", ""),
            "domain": data.get("domain", ""),
            "audit_item": data.get("item", ""),
            "project_id": project_id,
            "primary_laws": primary_laws,
            "selected_laws": selected_laws,
        }, context={"task_id": task_id, "project_id": project_id, "step": 6,
                    "node_name": "suspicion_endpoint"})

        suspicion_report = result.get("output", {}).get("suspicion_report", {}) \
            if result.get("success") else {}

        # P8-7: 落库 project_suspicions（MODEL_FOUND，待人工核实）+ 推进 current_step=6
        suspicion_id = None
        if project_id and suspicion_report:
            items = suspicion_report.get("items", [])
            # analysis_id 列为 INT（关联 audit_analysis_tasks.id 自增主键），
            # 需由 task_code(task_id) 反查数值 id；无 task_id 时留空
            analysis_id_num = None
            if task_id:
                row = query_one("SELECT id FROM audit_analysis_tasks WHERE task_code = %s",
                                (task_id,), database="tt")
                analysis_id_num = (row or {}).get("id")
            suspicion_id = insert(
                "INSERT INTO project_suspicions "
                "(project_id, analysis_id, suspicion_items, evidence_chain, status, verify_status) "
                "VALUES (%s,%s,%s,%s,'draft','MODEL_FOUND')",
                (project_id, analysis_id_num,
                 json.dumps(items, ensure_ascii=False),
                 json.dumps({"analysis_results": analysis_results,
                             "selected_laws": selected_laws}, ensure_ascii=False)),
                database="tt",
            )
            # P9-T4: 疑点继承本任务分析命中证据（result_type=suspicion，可溯源到文档 chunk）
            if task_id and suspicion_id:
                try:
                    evidence_service.link_suspicion_evidence(project_id, task_id, suspicion_id)
                except Exception:
                    pass
        if task_id:
            alc.advance_step(task_id, to_step=6, step_data_patch={"suspicion_report": suspicion_report},
                             summary_content=f"Step6 疑点生成：{suspicion_report.get('total_suspicions', 0)} 条疑点（待人工核实）",
                             summary_structured={"suspicion_report": suspicion_report,
                                                 "suspicion_id": suspicion_id})

        return jsonify({**result, "task_id": task_id, "suspicion_id": suspicion_id,
                        "suspicion_report": suspicion_report,
                        "verify_status": "MODEL_FOUND" if suspicion_id else None,
                        "next": "POST /analysis/{id}/suspicions/review 五态核实流转"})

    # ═══════════════════════════════════════════════════════════
    #  模板
    # ═══════════════════════════════════════════════════════════

    @app.route("/api/audit/templates", methods=["GET"])
    def audit_templates_list():
        """GET /api/audit/templates — 模板列表"""
        domain = request.args.get("domain")
        category = request.args.get("category")
        q = request.args.get("q")
        limit = min(request.args.get("limit", 50, type=int), 200)

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
            "current_step": current_step,   # P8 Q1: 权威步骤（与 GET 一致）
            "step": current_step,           # 旧前端兼容别名
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

    def _enrich_candidates(matches: list) -> list:
        """P8-3: 给违规模型候选补 engine_rule + audit_methods + match_score（确定性，非 AI 打分）。

        读 Phase7 audit_engine_rules（target_table/expression）+ audit_item_methods（data_requirements）。
        match_score 规则排序：命中表达式/方法数据的优先（有 engine_rule→100，否则按 id 稳定次序递减）。
        """
        if not matches:
            return matches
        # 批量取 engine_rule + audit_methods（按 violation_id）
        vids = []
        for m in matches:
            vid = m.get("violation_id") or m.get("id")
            if isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit()):
                vids.append(int(vid))
        rule_map, method_map = {}, {}
        if vids:
            ph = ",".join(["%s"] * len(vids))
            for r in query(f"SELECT violation_id, target_table, expression FROM "
                           f"audit_engine_rules WHERE violation_id IN ({ph})", tuple(vids), database="tt"):
                rule_map[r["violation_id"]] = {"target_table": r.get("target_table"),
                                               "expression": r.get("expression")}
            for r in query(f"SELECT violation_id, data_requirements FROM "
                           f"audit_item_methods WHERE violation_id IN ({ph})", tuple(vids), database="tt"):
                method_map[r["violation_id"]] = {"data_requirements": _json_loads(r.get("data_requirements"))}
        enriched = []
        for i, m in enumerate(matches):
            vid = m.get("violation_id") or m.get("id")
            vid_int = int(vid) if (isinstance(vid, int) or (isinstance(vid, str) and vid.isdigit())) else None
            has_rule = vid_int in rule_map
            m2 = dict(m)
            m2["engine_rule"] = rule_map.get(vid_int, {})
            m2["audit_methods"] = method_map.get(vid_int, {})
            # 确定性打分：有规则 100，否则按原顺序递减（保留 LLM relevance 时不覆盖）
            if not m2.get("match") and not m2.get("match_score"):
                m2["match_score"] = 100 if has_rule else max(10, 90 - i * 10)
            enriched.append(m2)
        return enriched

    def _build_law_recommendations(laws, project_id, task_id):
        """P8-4: 构造 law_recommendations（附录A §4）。

        逐法规查条款（regulation_graph.get_law_clauses），无条款/无原文 → confirm_status="待人工核实"
        （禁入文书）；落 source_refs 到 audit_source_refs。
        laws: [law_id_str] 或 [{law_id, law, clause, ...}]
        """
        recs = []
        for law in (laws or []):
            law_id = law.get("law_id") if isinstance(law, dict) else law
            law_title = law.get("law") or law.get("law_title", "") if isinstance(law, dict) else ""
            if not law_id:
                continue
            try:
                clauses = get_law_clauses(str(law_id)) or []
            except Exception:
                clauses = []
            clause = clauses[0] if clauses else {}
            clause_text = clause.get("clause_text") or clause.get("text") or ""
            has_evidence = bool(clause_text)
            # 落 source_ref（法规→条款）
            if project_id and has_evidence:
                try:
                    evidence_service.add_ref(project_id, "law_recommendation", task_id,
                                             "law_clause", clause.get("clause_id") or law_id,
                                             quote=clause_text[:500])
                except Exception:
                    pass
            recs.append({
                "law_id": str(law_id),
                "law_title": law_title,
                "clause_id": clause.get("clause_id") or "",
                "clause_no": clause.get("clause_no") or clause.get("no") or "",
                "clause_text": clause_text,
                "source_refs": [f"law_clause:{clause.get('clause_id') or law_id}"] if has_evidence else [],
                "confirm_status": "已确认" if has_evidence else "待人工核实",
            })
        return recs

    @app.route("/api/audit/analysis/<task_id>/readiness", methods=["GET"])
    def audit_analysis_readiness(task_id):
        """GET /analysis/{id}/readiness?stage=entry|data_ready|evidence_complete — 三道控制层检查（附录A §9）"""
        stage = request.args.get("stage", "entry")
        result = alc.check_readiness(task_id, stage)
        code = 200 if result.get("ready") else 412
        return jsonify({"success": result.get("ready", False),
                        "task_id": task_id, "stage": stage, **result}), code

    @app.route("/api/audit/analysis/<task_id>/suspicions/review", methods=["POST"])
    def audit_suspicion_review(task_id):
        """POST /analysis/{id}/suspicions/review — 疑点五态核实流转（附录A §7）

        body: {suspicion_id, verify_status, evidence?, reviewer?, note?}
        verify_status ∈ {MODEL_FOUND, WAIT_CONFIRM, CONFIRMED, REJECTED, NEED_MORE_EVIDENCE}
        流转：MODEL_FOUND→WAIT_CONFIRM→{CONFIRMED|REJECTED|NEED_MORE_EVIDENCE}，NEED_MORE_EVIDENCE→WAIT_CONFIRM
        """
        data = request.get_json() or {}
        suspicion_id = data.get("suspicion_id")
        new_status = (data.get("verify_status") or "").strip()
        allowed = {"MODEL_FOUND", "WAIT_CONFIRM", "CONFIRMED", "REJECTED", "NEED_MORE_EVIDENCE"}
        if not suspicion_id or new_status not in allowed:
            return jsonify({"success": False,
                            "error": "需 suspicion_id + verify_status(五态之一)"}), 400

        row = query_one("SELECT id, project_id, verify_status FROM project_suspicions WHERE id = %s",
                        (suspicion_id,), database="tt")
        if not row:
            return jsonify({"success": False, "error": "疑点不存在"}), 404

        # 合并证据/备注到 evidence_chain（JSON_MERGE_PATCH）
        patch = {}
        if data.get("evidence"):
            patch["review_evidence"] = data.get("evidence")
        if data.get("note"):
            patch["review_note"] = data.get("note")
        patch_json = json.dumps({"review": patch, "reviewer": data.get("reviewer") or "system",
                                 "at": datetime.now().isoformat()}, ensure_ascii=False)
        # status 列与 verify_status 保持同步：CONFIRMED→confirmed / REJECTED→rejected
        sync_status = ("confirmed" if new_status == "CONFIRMED"
                       else "rejected" if new_status == "REJECTED" else row.get("status") or "draft")
        execute(
            "UPDATE project_suspicions SET verify_status = %s, status = %s, "
            "evidence_chain = JSON_MERGE_PATCH(COALESCE(evidence_chain,'{}'), %s) WHERE id = %s",
            (new_status, sync_status, patch_json, suspicion_id),
            database="tt",
        )
        updated = query_one("SELECT id, verify_status, status FROM project_suspicions WHERE id = %s",
                            (suspicion_id,), database="tt")
        return jsonify({"success": True, "suspicion": dict(updated) if updated else {}})

    @app.route("/api/audit/analysis", methods=["POST"])
    def audit_analysis_create():
        """POST /api/audit/analysis — 创建分析任务，启动 LangGraph 工作流

        工作流自动执行 Step①(意图分析) → Step②(三Agent并行推荐)，
        然后在 Step③ 人工确认断点处暂停，返回推荐结果供用户选择。
        """
        data = request.get_json() or {}
        project_id = data.get("project_id", "")
        # P8-2: 附录A body = {project_id, focus_item_id?, user_intent?}；intent 作旧前端别名
        focus_item_id = data.get("focus_item_id")
        user_intent = data.get("user_intent") or data.get("intent", "")

        if not project_id:
            return jsonify({"success": False, "error": "缺少 project_id"}), 400
        if not user_intent:
            return jsonify({"success": False, "error": "请输入审计意图"}), 400

        # P3.4: 清理同 project_id 的旧未完成任务（防僵尸堆积）
        execute(
            "UPDATE audit_analysis_tasks SET status = 'cancelled' "
            "WHERE project_id = %s AND status IN ('in_progress','awaiting_confirmation','awaiting_upload')",
            (project_id,), database="tt",
        )

        # P8-2: 建任务（current_step=1）—— analysis_target/scope 由 ContextBuilder 推导后回填
        task_id = alc.create_analysis_task(project_id, focus_item_id=focus_item_id,
                                            user_intent=user_intent)

        # P8-10: ContextBuilder 从 DB 装配 project_context（禁 HTML）+ 推导 target/scope
        ctx = acb.build(task_id, step=1)
        pc = (ctx or {}).get("project_context", {})
        analysis_target = pc.pop("_analysis_target", None)
        analysis_scope = pc.pop("_analysis_scope", None)
        if analysis_target or analysis_scope:
            execute(
                "UPDATE audit_analysis_tasks SET analysis_target = %s, analysis_scope = %s "
                "WHERE task_code = %s",
                (analysis_target, analysis_scope, task_id), database="tt",
            )
        project_context = {
            "domain": pc.get("audit_type", ""),
            "audit_item": ((ctx or {}).get("focus_item") or {}).get("title", "") or pc.get("name", ""),
            "audit_period": pc.get("audit_period", ""),
            "target_level": pc.get("target_level", ""),
            "target_unit": pc.get("audited_unit", ""),
            # P9-立项匹配: objective/scope 已由 ContextBuilder 读出，此处接通（此前被丢弃，导致 ViolationMatcher 看不到立项详情）
            "objective": pc.get("objective", ""),
            "scope": pc.get("scope", ""),
        }

        # 启动 LangGraph 工作流（Step① + Step②，在 Step③ 断点暂停）
        config = {"configurable": {"thread_id": task_id}}
        state = _analysis_graph.invoke({
            "task_id": task_id,
            "project_id": project_id,
            "session_id": task_id,
            "user_intent": user_intent,
            **project_context,  # P1.4: 注入 DB 项目上下文
            "focus_item": (ctx or {}).get("focus_item") or {},  # 事项级指导（common_violations/required_materials 等）
        }, config)

        # 持久化 Step1-2 结果到 step_data（current_step 由 graph 算，权威落 MySQL）
        matches_enriched = _enrich_candidates(state.get("matches", []))
        to_step = state.get("current_step", 2)
        alc.advance_step(task_id, to_step=to_step,
                         step_data_patch={
                             "intent_result": state.get("intent_result", {}),
                             "matches": matches_enriched,
                             "primary_laws": state.get("primary_laws", []),
                             "recommended_materials": state.get("recommended_materials", []),
                         },
                         # P8 §8: 本步正式总结（固定 message_id=step-{N}-summary）
                         summary_content=(
                             state.get("intent_result", {}).get("summary")
                             or f"Step1-2 完成：意图分析 + 违规模型匹配 {len(matches_enriched)} 条、"
                                f"法规候选 {len(state.get('primary_laws', []))} 条"
                         ),
                         summary_structured={
                             "matches": len(matches_enriched),
                             "primary_laws": len(state.get("primary_laws", [])),
                             "recommended_materials": len(state.get("recommended_materials", [])),
                         })

        # P8-1: entry 门禁（附录A §9）— 当前前端仍自推进（C6 前），门禁结果随响应返回，
        # 不硬阻断 create；项目缺失才 400。C6 前端化后由前端调 /readiness 显式门禁。
        entry = alc.check_readiness(task_id, "entry")
        if not (ctx or {}).get("project_context", {}).get("name"):
            return jsonify({"success": False, "error": "项目不存在或已删",
                            "task_id": task_id}), 400

        snapshot = _analysis_graph.get_state(config)
        resp = _graph_state_to_response(task_id, state, snapshot)
        resp["matches"] = matches_enriched
        resp["project_context"] = pc
        resp["focus_item"] = (ctx or {}).get("focus_item")
        resp["readiness"] = {"entry": entry}
        return jsonify(resp)

    @app.route("/api/audit/analysis/<task_id>", methods=["GET"])
    def audit_analysis_status(task_id):
        """GET /api/audit/analysis/<id> — 查询分析任务权威状态（P8 Q1：MySQL 唯一权威源）

        current_step/step_data/summaries 均从 MySQL 读；LangGraph 快照仅作 in-flight 兜底
        （兼容旧任务或 Step1-2 刚 invoke 尚未落 step_data 的极短窗口）。
        """
        auth = alc.get_authoritative_state(task_id)
        if not auth:
            # 兜底：查 graph（旧任务 / 极短窗口）
            config = {"configurable": {"thread_id": task_id}}
            snapshot = _analysis_graph.get_state(config)
            if snapshot and snapshot.values:
                return jsonify(_graph_state_to_response(task_id, snapshot.values, snapshot))
            return jsonify({"success": False, "error": "任务不存在"}), 404

        sd = auth.get("step_data", {})
        return jsonify({
            "success": True,
            "task_id": auth["task_id"],
            "project_id": auth.get("project_id"),
            "current_step": auth["current_step"],   # P8 Q1: 权威步骤
            "step": auth["current_step"],           # 旧前端兼容别名
            "status": auth.get("status"),
            "intent_result": sd.get("intent_result", {}),
            "domain": sd.get("intent_result", {}).get("domain", ""),
            "audit_item": sd.get("audit_item", ""),
            "matches": sd.get("matches", []),
            "primary_laws": sd.get("primary_laws", []),
            "layer_advice": sd.get("layer_advice", ""),
            "recommended_materials": sd.get("recommended_materials", []),
            "analysis_results": (auth.get("agent_results") or {}).get("audit_analyzer", []),
            "suspicion_report": auth.get("result") or {},
            "selected_violations": sd.get("selected_violations", []),
            "selected_laws": sd.get("selected_laws", []),
            "summaries": auth.get("summaries", {}),
            "focus_item_id": auth.get("focus_item_id"),
            "analysis_target": auth.get("analysis_target"),
            "analysis_scope": auth.get("analysis_scope"),
        })

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
            # Step④: 标记文件已上传，继续执行 Step⑤（P8 Q2: Step6 疑点已移出 graph）
            data = request.get_json() or {}
            uploaded_files = data.get("uploaded_files", [])

            # P8-5: data_ready 门禁（附录A §9）— 当前随响应返回，不硬阻断（C6 前）；
            # 真无文件/无数据时 graph 跑空，analysis_results 为空，前端据 readiness 提示。
            data_ready = alc.check_readiness(task_id, "data_ready")

            _analysis_graph.update_state(config, {
                "uploaded_files": uploaded_files,
                "current_step": 4,
            }, as_node="step_4_upload")

            # 继续执行 Step⑤（step5→END，疑点走独立端点）
            final_state = _analysis_graph.invoke(None, config)

            # P9-T4: 命中行证据（field_sources→chunk→audit_source_refs）由 AuditAnalyzer
            # 在扫描期写库 + validate_output 注入 analysis_result.source_refs（确定性，vid/table
            # 已知处接线）。此处 analysis_results 已带 source_refs，直接持久化即可。
            analysis_results = final_state.get("analysis_results", [])

            # 持久化 Step5 结果（current_step=5 权威）+ 写 Step5 正式总结
            alc.advance_step(task_id, to_step=5, step_data_patch={
                "uploaded_files": uploaded_files,
                "analysis_results": analysis_results,
                "overall_assessment": final_state.get("overall_assessment", ""),
            }, summary_content=final_state.get("overall_assessment", ""),
               summary_structured={"analysis_results": analysis_results})
            # agent_results 单独合并（GET 读 agent_results.audit_analyzer）
            execute(
                "UPDATE audit_analysis_tasks SET agent_results = JSON_MERGE_PATCH("
                "COALESCE(agent_results,'{}'), %s) WHERE task_code = %s",
                (json.dumps({"audit_analyzer": analysis_results}, ensure_ascii=False), task_id),
                database="tt",
            )

            return jsonify({
                "success": True,
                "task_id": task_id,
                "current_step": 5,                 # P8 Q1: 权威步骤
                "step": 5,                         # 旧前端兼容
                "status": "step5_done",
                "analysis_results": analysis_results,
                "overall_assessment": final_state.get("overall_assessment", ""),
                "readiness": {"data_ready": data_ready},
                "next": "POST /suspicion/generate 生成疑点（Step6）",
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

        # P8-4: 构造 law_recommendations（附录A §4）— 逐法规查条款，无条款/无原文→待人工核实
        project_id = snapshot.values.get("project_id", "")
        law_recs = _build_law_recommendations(selected_laws or snapshot.values.get("primary_laws", []),
                                              project_id, task_id)

        # 注入用户确认数据（as_node 指向确认节点，消除并行后的歧义更新）
        _analysis_graph.update_state(config, {
            "selected_violations": selected_violations,
            "selected_laws": selected_laws,
            "custom_regulations": custom_regulations,
            "confirmation_status": "confirmed" if action == "confirm" else "rejected",
            "current_step": 3,
        }, as_node="step_3_confirm")

        # 持久化确认记录（current_step=3 权威 + law_recommendations + Step3 总结）
        alc.advance_step(task_id, to_step=3, step_data_patch={
            "selected_violations": selected_violations,
            "selected_laws": selected_laws,
            "custom_regulations": custom_regulations,
            "law_recommendations": law_recs,
            "action": action,
            "confirmed_at": datetime.now().isoformat(),
        }, summary_content=f"Step3 法规确认：{len(selected_laws)} 部法规，"
           f"{'通过' if action == 'confirm' else '拒绝'}",
           summary_structured={"law_recommendations": law_recs,
                               "selected_violations": selected_violations})

        if action == "reject":
            return jsonify({
                "success": True,
                "task_id": task_id,
                "current_step": 3,
                "step": 3,
                "status": "rejected",
                "law_recommendations": law_recs,
                "message": "分析已取消，用户拒绝AI推荐",
            })

        # 确认通过：继续工作流（执行 Step④ 上传等待节点，不阻塞）
        state = _analysis_graph.invoke(None, config)
        new_snapshot = _analysis_graph.get_state(config)
        resp = _graph_state_to_response(task_id, state, new_snapshot)
        resp["current_step"] = 3
        resp["step"] = 3
        resp["law_recommendations"] = law_recs
        return jsonify(resp)

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
        """GET /api/audit/workspace/download — 预签名下载（P2-8/P2-10 §6.4）

        新逻辑：?project_id=<pid>&file=<object_key>（每项目 bucket + 跨项目 pid 校验，不匹配 403）。
        旧逻辑（§6.8 deprecated）：?project=<name>&file=<filename>（单桶，灰度至 Phase 5）。
        """
        project_id = (request.args.get("project_id") or "").strip()
        object_key = (request.args.get("file") or "").strip()
        # 新逻辑（带 project_id）
        if project_id:
            if not object_key:
                return jsonify({"success": False, "error": "缺少 file 参数"}), 400
            proj = query_one(
                "SELECT minio_bucket FROM audit_projects WHERE id = %s AND deleted = 0",
                (project_id,), database="tt",
            )
            if not proj:
                return jsonify({"success": False, "error": "项目不存在"}), 404
            bucket = proj.get("minio_bucket") or "audit-project-{}".format(project_id)
            # P2-10 跨项目校验：object_key 前缀 pid 须 == project_id
            from services.workspace_service import parse_pid_from_key
            if parse_pid_from_key(object_key) != project_id:
                return jsonify({"success": False, "error": "无权访问该文件（跨项目）"}), 403
            try:
                from services.minio_client import get_presigned_url
                url = get_presigned_url(object_key, bucket=bucket, expires=3600)
                return jsonify({"success": True, "url": url, "bucket": bucket})
            except Exception as e:
                return jsonify({"success": False, "error": str(e)}), 500
        # 旧逻辑（deprecated，按 MinIO 文件夹名寻址，§6.8 灰度保留）
        project = request.args.get("project", "")
        filename = request.args.get("file", "")
        if not project or not filename:
            return jsonify({"success": False, "error": "缺少参数（需 project_id 或 project+file）"}), 400
        try:
            from services.minio_client import get_presigned_url
            url = get_presigned_url("{}/{}".format(project, filename), expires=3600)
            return jsonify({"success": True, "url": url})
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/audit/workspace/delete", methods=["DELETE"])
    def audit_workspace_delete():
        """DELETE /api/audit/workspace/delete — 删除文件（P2-9/P2-10 §6.5）

        新逻辑：?project_id=<pid>&file=<object_key> → 软删（trace.deleted_at=NOW()
        + manifest.files[].deleted=true，MinIO 对象留原位 §3.5，可恢复）。
        旧逻辑（§6.8 deprecated）：?project=<name>&file=<filename> → 物理删（单桶，灰度）。
        """
        project_id = (request.args.get("project_id") or "").strip()
        object_key = (request.args.get("file") or "").strip()
        # 新逻辑（软删）
        if project_id:
            if not object_key:
                return jsonify({"success": False, "error": "缺少 file 参数"}), 400
            proj = query_one(
                "SELECT name, audit_period, create_time, minio_bucket "
                "FROM audit_projects WHERE id = %s AND deleted = 0",
                (project_id,), database="tt",
            )
            if not proj:
                return jsonify({"success": False, "error": "项目不存在"}), 404
            bucket = proj.get("minio_bucket") or "audit-project-{}".format(project_id)
            from services.workspace_service import (
                parse_pid_from_key, derive_audit_year, compute_safe_name,
                build_manifest_path, mark_file_deleted, update_manifest_atomic,
            )
            # P2-10 跨项目校验
            if parse_pid_from_key(object_key) != project_id:
                return jsonify({"success": False, "error": "无权删除该文件（跨项目）"}), 403
            # 软删 trace（§3.5 留痕）
            trace = query_one(
                "SELECT id FROM audit_document_traces "
                "WHERE project_id = %s AND minio_path = %s AND deleted_at IS NULL",
                (project_id, object_key), database="tt",
            )
            trace_id = None
            if trace:
                execute("UPDATE audit_document_traces SET deleted_at = NOW() WHERE id = %s",
                        (trace["id"],), database="tt")
                trace_id = trace["id"]
            # 软删 manifest（增量更新，并发写保护）
            audit_year, _ = derive_audit_year(proj.get("audit_period"), proj.get("create_time"))
            mpath = build_manifest_path(audit_year, project_id, compute_safe_name(proj.get("name") or ""))
            _marked = {"v": False}

            def _del(m):
                if m:
                    _marked["v"] = mark_file_deleted(m, object_key=object_key)
                return m

            update_manifest_atomic(project_id, bucket, mpath, _del)
            marked = _marked["v"]
            return jsonify({
                "success": True, "soft_deleted": True, "trace_id": trace_id,
                "manifest_marked": marked,
                "message": "文件已软删（对象保留原位，可恢复）",
            })
        # 旧逻辑（deprecated 物理删，§6.8 灰度保留）
        project = request.args.get("project", "")
        filename = request.args.get("file", "")
        try:
            from services.minio_client import delete_object
            delete_object("{}/{}".format(project, filename))
            return jsonify({"success": True, "message": "{} 已删除".format(filename)})
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
