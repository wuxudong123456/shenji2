"""分析任务生命周期服务（P8-1/P8-2/P8-5）— readiness 三道门禁 + 任务推进 + 权威状态

附录A §9 三道控制层检查：
  entry            Step1 前：项目完成/对象范围完成/事项完成/空间存在/权限正确
  data_ready       Step5 前：文件存在/OCR完成/分类完成/结构化完成/字段完整/进入data_*/trace存在
  evidence_complete Step7 前：疑点已确认/数据证据存在/文档引用存在/法规存在

Q1 决策：current_step 为 audit_analysis_tasks 唯一权威源（1-7）；本服务负责其推进与读取。
复用：project_lifecycle.check_stage（entry 门禁）、db.*、evidence_service。
task_id = audit_analysis_tasks.task_code。
"""
import json

from services.db import query, query_one, execute, insert
from services import project_lifecycle

DATABASE = "tt"

# 八张通用数据表（migrate_phase5_data_tables + 历史）— data_ready 统计结构化数据用
_DATA_TABLES = [
    "data_contracts", "data_general", "data_registers", "data_legal_docs",
    "data_finance", "data_credentials", "data_procurements", "data_interviews",
]


# ── readiness 三道门禁 ──────────────────────────────────────────────

def check_readiness(task_id, stage):
    """检查某任务在某 stage 是否就绪。

    Args:
        task_id: audit_analysis_tasks.task_code
        stage: 'entry' | 'data_ready' | 'evidence_complete'

    Returns:
        {ready: bool, checks: [{name, ok, detail}], missing_items: [name]}
    """
    task = query_one(
        "SELECT task_code, project_id, current_step, step, step_data "
        "FROM audit_analysis_tasks WHERE task_code = %s",
        (task_id,), database=DATABASE,
    )
    if not task:
        return {"ready": False, "checks": [],
                "missing_items": ["task_not_found"]}

    project_id = task.get("project_id")
    if stage == "entry":
        checks = _check_entry(project_id)
    elif stage == "data_ready":
        checks = _check_data_ready(project_id)
    elif stage == "evidence_complete":
        checks = _check_evidence_complete(project_id, task)
    else:
        return {"ready": False, "checks": [],
                "missing_items": [f"unknown_stage:{stage}"]}

    missing = [c["name"] for c in checks if not c["ok"]]
    return {"ready": len(missing) == 0, "checks": checks, "missing_items": missing}


def _check_entry(project_id):
    """entry 门禁 5 项（附录A §9 entry）— 复用 project_lifecycle.check_stage。"""
    project = query_one(
        "SELECT name, scope, audited_unit, setup_stage, status "
        "FROM audit_projects WHERE id = %s AND deleted = 0",
        (project_id,), database=DATABASE,
    ) if project_id else None
    item_count = _item_count(project_id)

    checks = []
    # ① 项目完成：setup_stage 至少 'items'，且前置必填完整
    ok_proj, miss = project_lifecycle.check_stage(project, "items", item_count)
    checks.append({"name": "项目完成", "ok": ok_proj,
                   "detail": "setup_stage≥items" if ok_proj else f"缺 {miss}"})
    # ② 对象范围完成：scope + audited_unit 已填
    ok_scope = bool(project) and bool(project.get("scope")) and bool(project.get("audited_unit"))
    checks.append({"name": "对象范围完成", "ok": ok_scope,
                   "detail": "" if ok_scope else "scope/audited_unit 未填"})
    # ③ 事项完成：audit_items ≥ 1
    checks.append({"name": "事项完成", "ok": item_count > 0,
                   "detail": f"{item_count} 个事项" if item_count else "无审计事项"})
    # ④ 空间存在：setup_stage == 'workspace'（资料空间已创建）
    has_workspace = bool(project) and project.get("setup_stage") == "workspace"
    checks.append({"name": "空间存在", "ok": has_workspace,
                   "detail": project.get("setup_stage") if project else "无项目"})
    # ⑤ 权限正确：项目存在且未删（真实鉴权由路由层/网关，此处校验可达性）
    checks.append({"name": "权限正确", "ok": bool(project),
                   "detail": "" if project else "项目不存在或已删"})
    return checks


def _check_data_ready(project_id):
    """data_ready 门禁 7 项（附录A §5）。"""
    checks = []
    if not project_id:
        return [{"name": n, "ok": False, "detail": "无项目"} for n in
                ["文件存在", "OCR完成", "分类完成", "结构化完成", "字段完整", "进入data_*", "trace存在"]]

    traces = query(
        "SELECT id, parse_status, file_category, file_subcategory, file_name "
        "FROM audit_document_traces WHERE project_id = %s AND deleted_at IS NULL",
        (project_id,), database=DATABASE,
    )
    trace_n = len(traces)
    ocr_done = sum(1 for t in traces if t.get("parse_status") == "done")
    classified = sum(1 for t in traces if t.get("file_category") or t.get("file_subcategory"))
    data_n = _data_row_count(project_id)
    field_n = query_one(
        "SELECT COUNT(*) AS n FROM audit_field_sources WHERE project_id = %s",
        (project_id,), database=DATABASE,
    )
    field_n = (field_n or {}).get("n", 0)

    checks.append({"name": "文件存在", "ok": trace_n > 0, "detail": f"{trace_n} 个文件"})
    checks.append({"name": "OCR完成", "ok": ocr_done > 0, "detail": f"{ocr_done}/{trace_n} 已解析"})
    checks.append({"name": "分类完成", "ok": classified > 0, "detail": f"{classified} 已分类"})
    checks.append({"name": "结构化完成", "ok": data_n > 0, "detail": f"{data_n} 行数据"})
    checks.append({"name": "字段完整", "ok": field_n > 0, "detail": f"{field_n} 字段溯源"})
    checks.append({"name": "进入data_*", "ok": data_n > 0, "detail": f"{data_n} 行"})
    checks.append({"name": "trace存在", "ok": trace_n > 0, "detail": f"{trace_n} 条 trace"})
    return checks


def _check_evidence_complete(project_id, task):
    """evidence_complete 门禁 4 项（附录A §9）— 疑点已确认/数据证据/文档引用/法规。"""
    checks = []
    if not project_id:
        return [{"name": n, "ok": False, "detail": "无项目"} for n in
                ["疑点已确认", "数据证据存在", "文档引用存在", "法规存在"]]

    susp_confirmed = query_one(
        "SELECT COUNT(*) AS n FROM project_suspicions "
        "WHERE project_id = %s AND verify_status = 'CONFIRMED'",
        (project_id,), database=DATABASE,
    )
    susp_n = (susp_confirmed or {}).get("n", 0)

    data_ev = query_one(
        "SELECT COUNT(*) AS n FROM audit_source_refs "
        "WHERE project_id = %s AND result_type IN ('analysis_hit','data_row')",
        (project_id,), database=DATABASE,
    )
    doc_ev = query_one(
        "SELECT COUNT(*) AS n FROM audit_source_refs "
        "WHERE project_id = %s AND source_type = 'document_chunk'",
        (project_id,), database=DATABASE,
    )

    step_data = _loads_json(task.get("step_data")) or {}
    selected_laws = step_data.get("selected_laws") or []
    has_laws = len(selected_laws) > 0

    checks.append({"name": "疑点已确认", "ok": susp_n > 0, "detail": f"{susp_n} 条 CONFIRMED"})
    checks.append({"name": "数据证据存在", "ok": (data_ev or {}).get("n", 0) > 0,
                   "detail": f"{(data_ev or {}).get('n', 0)} 条"})
    checks.append({"name": "文档引用存在", "ok": (doc_ev or {}).get("n", 0) > 0,
                   "detail": f"{(doc_ev or {}).get('n', 0)} 条"})
    checks.append({"name": "法规存在", "ok": has_laws, "detail": f"{len(selected_laws)} 部法规"})
    return checks


# ── 任务推进 ────────────────────────────────────────────────────────

def create_analysis_task(project_id, focus_item_id=None, user_intent=None,
                         analysis_target=None, analysis_scope=None):
    """创建分析任务（附录A §2）。current_step=1。

    Returns:
        task_id（task_code，16 位 hex）
    """
    import uuid
    task_id = str(uuid.uuid4()).replace("-", "")[:16]
    title = (user_intent or "")[:500] or "智能分析任务"
    insert(
        "INSERT INTO audit_analysis_tasks "
        "(task_code, project_id, title, step, current_step, status, "
        " focus_item_id, analysis_target, analysis_scope, created_at) "
        "VALUES (%s,%s,%s,%s,%s,'in_progress',%s,%s,%s,NOW())",
        (task_id, project_id, title, 1, 1,
         focus_item_id, analysis_target, analysis_scope),
        database=DATABASE,
    )
    return task_id


def advance_step(task_id, to_step, step_data_patch=None,
                 summary_content=None, summary_structured=None, summary_source_refs=None):
    """推进 current_step + 合并 step_data + UPSERT 本步正式总结（audit_step_summaries）。

    Args:
        task_id: task_code
        to_step: 目标步骤 1-7
        step_data_patch: dict，JSON_MERGE_PATCH 合并入 step_data
        summary_*: 本步 audit_step_summaries（content/structured/source_refs）

    Returns:
        dict — 更新后的权威状态（get_authoritative_state）
    """
    patch_json = json.dumps(step_data_patch or {}, ensure_ascii=False)
    execute(
        "UPDATE audit_analysis_tasks "
        "SET current_step = %s, step = %s, "
        "    step_data = JSON_MERGE_PATCH(COALESCE(step_data,'{}'), %s) "
        "WHERE task_code = %s",
        (to_step, to_step, patch_json, task_id),
        database=DATABASE,
    )

    if summary_content is not None or summary_structured is not None:
        _upsert_step_summary(task_id, to_step, summary_content,
                             summary_structured, summary_source_refs)
    return get_authoritative_state(task_id)


def _upsert_step_summary(task_id, step_no, content, structured, source_refs):
    """UPSERT audit_step_summaries（UNIQUE task+step → 覆盖，version+1）。"""
    msg_id = f"step-{step_no}-summary"
    execute(
        "INSERT INTO audit_step_summaries "
        "(analysis_task_id, step_no, message_id, content, structured, source_refs) "
        "VALUES (%s,%s,%s,%s,%s,%s) "
        "ON DUPLICATE KEY UPDATE "
        " content = VALUES(content), structured = VALUES(structured), "
        " source_refs = VALUES(source_refs), version = version + 1",
        (task_id, step_no, msg_id,
         content,
         json.dumps(structured, ensure_ascii=False) if structured is not None else None,
         json.dumps(source_refs, ensure_ascii=False) if source_refs is not None else None),
        database=DATABASE,
    )


def get_authoritative_state(task_id):
    """纯 MySQL 读任务权威状态（Q1：响应只从 MySQL 派生）。

    Returns:
        dict 或 None（任务不存在）
    """
    task = query_one(
        "SELECT task_code, project_id, focus_item_id, analysis_target, analysis_scope, "
        "       current_step, step, status, step_data, agent_results, result, title "
        "FROM audit_analysis_tasks WHERE task_code = %s",
        (task_id,), database=DATABASE,
    )
    if not task:
        return None
    summaries = query(
        "SELECT step_no, message_id, content, structured, source_refs, version "
        "FROM audit_step_summaries WHERE analysis_task_id = %s ORDER BY step_no",
        (task_id,), database=DATABASE,
    )
    return {
        "task_id": task["task_code"],
        "project_id": task.get("project_id"),
        "current_step": task.get("current_step") or task.get("step") or 1,
        "status": task.get("status"),
        "title": task.get("title"),
        "focus_item_id": task.get("focus_item_id"),
        "analysis_target": task.get("analysis_target"),
        "analysis_scope": task.get("analysis_scope"),
        "step_data": _loads_json(task.get("step_data")) or {},
        "agent_results": _loads_json(task.get("agent_results")) or {},
        "result": _loads_json(task.get("result")),
        "summaries": {
            s["step_no"]: {
                "message_id": s.get("message_id"),
                "content": s.get("content"),
                "structured": _loads_json(s.get("structured")),
                "source_refs": _loads_json(s.get("source_refs")),
                "version": s.get("version"),
            }
            for s in summaries
        },
    }


# ── 小工具 ──────────────────────────────────────────────────────────

def _item_count(project_id):
    if not project_id:
        return 0
    r = query_one(
        "SELECT COUNT(*) AS n FROM audit_items WHERE project_id = %s",
        (project_id,), database=DATABASE,
    )
    return (r or {}).get("n", 0)


def _data_row_count(project_id):
    """八张 data_* 表按 project_id 合计行数。"""
    if not project_id:
        return 0
    unions = " UNION ALL ".join(
        f"(SELECT COUNT(*) AS n FROM {t} WHERE project_id = %s)" for t in _DATA_TABLES
    )
    rows = query(unions, tuple([project_id] * len(_DATA_TABLES)), database=DATABASE)
    return sum((r.get("n") or 0) for r in rows)


def _loads_json(v):
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return {}
    return {}


if __name__ == "__main__":
    # 简单自测：check_readiness 对不存在任务应返回 task_not_found
    r = check_readiness("nonexistent_task_id_xyz", "entry")
    print("entry (no task):", r)
