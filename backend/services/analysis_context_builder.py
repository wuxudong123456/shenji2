"""分析上下文装配器（P8-10）— 按 task_id 从 DB 装配 AI 输入，禁 HTML

附录A §1/§2：AI 输入一律由服务端从 DB 装配（本服务），前端只传 task_id + 已确认 ID；
project_context 仅来自 DB 项目记录，AI 不得重写（§2）。

输出两部分：
  - 结构化（响应体 project_context/focus_item/confirmed_results/source_refs）
  - llm_text（纯文本，喂 Agent build_prompt，**不含 HTML**——§0 铁律）

复用：db.query_one（audit_projects/audit_items/audit_analysis_tasks）、
evidence_service.get_refs（source_refs）。task_id = audit_analysis_tasks.task_code。
"""
import json

from services.db import query_one

DATABASE = "tt"


def build(task_id, step=None):
    """装配某分析任务的完整上下文。

    Args:
        task_id: audit_analysis_tasks.task_code（16 位 hex）
        step: 可选，当前步骤号（仅用于 llm_text 标注，不影响取数）

    Returns:
        dict — {task, project_context, focus_item, confirmed_results, source_refs, llm_text}
        任务不存在返回 None。
    """
    task = query_one(
        "SELECT task_code, project_id, focus_item_id, analysis_target, analysis_scope, "
        "       current_step, step, status, step_data, title "
        "FROM audit_analysis_tasks WHERE task_code = %s",
        (task_id,), database=DATABASE,
    )
    if not task:
        return None

    project_id = task.get("project_id")
    project_context, focus_item = _load_project_and_item(
        project_id, task.get("focus_item_id"), task.get("analysis_target"),
        task.get("analysis_scope"),
    )

    confirmed_results = _loads_json(task.get("step_data")) or {}
    source_refs = []
    if project_id:
        # 该任务全部结论级 source_refs（analysis_hit/law_recommendation/suspicion）。
        # P8-4/6/8 各步落 ref 时 result_id 用 task_id，此处取任务级聚合。
        from services.db import query
        source_refs = query(
            "SELECT result_type, result_id, source_type, source_id, quote, relation "
            "FROM audit_source_refs WHERE project_id = %s "
            "AND result_id = %s ORDER BY id",
            (project_id, task_id), database=DATABASE,
        )

    return {
        "task": {
            "task_id": task["task_code"],
            "project_id": project_id,
            "focus_item_id": task.get("focus_item_id"),
            "analysis_target": task.get("analysis_target"),
            "analysis_scope": task.get("analysis_scope"),
            "current_step": task.get("current_step") or task.get("step") or 1,
            "status": task.get("status"),
            "title": task.get("title"),
        },
        "project_context": project_context,
        "focus_item": focus_item,
        "confirmed_results": confirmed_results,
        "source_refs": list(source_refs or []),
        "llm_text": _render_llm_text(project_context, focus_item, confirmed_results, step),
    }


def _load_project_and_item(project_id, focus_item_id=None,
                           analysis_target=None, analysis_scope=None):
    """读项目 + 聚焦事项。事项优先 focus_item_id，缺省取项目第一个事项（附录A §2）。"""
    if not project_id:
        return {}, None
    proj = query_one(
        "SELECT name, audit_type, target_level, audit_period, audited_unit, "
        "       objective, scope, setup_stage "
        "FROM audit_projects WHERE id = %s AND deleted = 0",
        (project_id,), database=DATABASE,
    )
    project_context = {
        "name": (proj or {}).get("name", "") or "",
        "audit_type": (proj or {}).get("audit_type", "") or "",
        "target_level": (proj or {}).get("target_level", "") or "",
        "audit_period": (proj or {}).get("audit_period", "") or "",
        "audited_unit": (proj or {}).get("audited_unit", "") or "",
        "objective": (proj or {}).get("objective", "") or "",
        "scope": (proj or {}).get("scope", "") or "",
    }

    focus_item = None
    item = None
    if focus_item_id:
        item = query_one(
            "SELECT id, title, subtitle, category, priority, "
            "       common_problems, required_materials, common_violations, "
            "       audit_methods, legal_bases "
            "FROM audit_items WHERE id = %s AND project_id = %s",
            (focus_item_id, project_id), database=DATABASE,
        )
    if not item:
        item = query_one(
            "SELECT id, title, subtitle, category, priority, "
            "       common_problems, required_materials, common_violations, "
            "       audit_methods, legal_bases "
            "FROM audit_items WHERE project_id = %s ORDER BY seq, id LIMIT 1",
            (project_id,), database=DATABASE,
        )
    if item:
        focus_item = {
            "item_id": item["id"],
            "title": item.get("title", ""),
            "category": item.get("category", ""),
            "priority": item.get("priority", ""),
            # 结构化指导字段（JSON 列，可能为 None/str/已被驱动解析）—— §0 三环之"装配"
            "common_problems": _loads_json(item.get("common_problems")) or [],
            "required_materials": _loads_json(item.get("required_materials")) or [],
            "common_violations": _loads_json(item.get("common_violations")) or [],
            "audit_methods": _loads_json(item.get("audit_methods")) or [],
            "legal_bases": _loads_json(item.get("legal_bases")) or [],
        }

    # analysis_target/scope 落库列优先（用户/路由显式设定），否则从项目/事项推导
    if analysis_target is None:
        analysis_target = project_context["audited_unit"] or (focus_item or {}).get("title", "")
    if analysis_scope is None:
        analysis_scope = project_context["scope"]

    # 透传给调用方（路由落 analysis_target/analysis_scope 时用）
    project_context["_analysis_target"] = analysis_target
    project_context["_analysis_scope"] = analysis_scope
    return project_context, focus_item


def _render_llm_text(project_context, focus_item, confirmed_results, step):
    """纯文本渲染上下文（喂 Agent build_prompt，禁 HTML）。"""
    lines = []
    if step:
        lines.append(f"【当前步骤】Step {step}")
    if project_context:
        lines.append("【项目背景】")
        lines.append(f"项目名称：{project_context.get('name', '')}")
        lines.append(f"审计类型：{project_context.get('audit_type', '')}")
        lines.append(f"单位层级：{project_context.get('target_level', '')}")
        lines.append(f"审计期间：{project_context.get('audit_period', '')}")
        lines.append(f"被审计单位：{project_context.get('audited_unit', '')}")
        if project_context.get("objective"):
            lines.append(f"审计目标：{project_context['objective']}")
        if project_context.get("scope"):
            lines.append(f"审计范围：{project_context['scope']}")
    if focus_item:
        lines.append("【聚焦事项】")
        lines.append(f"{focus_item.get('title', '')}"
                     f"（{focus_item.get('category', '')}/{focus_item.get('priority', '')}）")
        # 事项结构化指导：按 step 注入控制 token（step 未知/0 或末步 7=全注入）—— §0 三环之"渲染"
        # 不渲染则前两环白做，AI 仍只看到事项标题。
        try:
            sn = int(step) if step else 0
        except (TypeError, ValueError):
            sn = 0
        allf = sn in (0, 7)
        # 常见问题：各步通用（框定"要盯什么"），通常较短，恒注入
        cp = _fmt_list(focus_item.get("common_problems"))
        if cp:
            lines.append(f"常见问题：{cp}")
        # 所需资料：Step②推荐 / Step④资料
        if allf or sn in (2, 4):
            rm = _fmt_list(focus_item.get("required_materials"))
            if rm:
                lines.append(f"所需资料：{rm}")
        # 常见违规：Step②推荐 / Step⑤比对 / Step⑥疑点
        if allf or sn in (2, 5, 6):
            cv = _fmt_list(focus_item.get("common_violations"))
            if cv:
                lines.append(f"常见违规：{cv}")
        # 审计方法：Step⑤比对 / Step⑥疑点
        if allf or sn in (5, 6):
            am = _fmt_list(focus_item.get("audit_methods"))
            if am:
                lines.append(f"审计方法：{am}")
        # 法规依据：Step②推荐 / Step③法规
        if allf or sn in (2, 3):
            lb = _fmt_list(focus_item.get("legal_bases"))
            if lb:
                lines.append(f"法规依据：{lb}")
    if confirmed_results:
        lines.append("【已确认结果】")
        lines.append(json.dumps(confirmed_results, ensure_ascii=False, indent=2)[:2000])
    return "\n".join(lines)


def _fmt_list(v, limit=6):
    """把事项 JSON 字段渲染成紧凑一行文本（兼容 str/list/dict，截断防 token 爆炸）。

    common_problems/legal_bases/audit_methods 多为字符串列表；
    required_materials 可能是 {items:[{name,...}]} 结构——统一取可读名拼接。
    返回空串表示无内容（调用方据此跳过该行）。
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
    txt = "；".join(parts)
    if len(v) > limit:
        txt += f"（等共{len(v)}项）"
    return txt


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
