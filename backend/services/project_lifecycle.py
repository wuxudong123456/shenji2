"""项目生命周期服务 — 阶段矩阵 + 字段白名单 + allowed_actions 计算 + 阶段校验

四阶段：basic → target_scope → items → workspace。
"active" 只在 audit_projects.status 出现（workspace 完成后 draft 转 active，见 PHASE_1 §3）。
"""
import json

# ── 阶段定义 ──
STAGES = ["basic", "target_scope", "items", "workspace"]

# 各阶段允许写入的字段白名单（越阶段字段由路由层用本服务过滤，不报错、只忽略）
STAGE_FIELDS = {
    "basic": [
        "name", "project_code", "description", "audit_type", "audit_method",
        "target_level", "audited_unit", "leader", "auditor", "objective",
        "audit_period", "amount",
        "business_start_date", "business_end_date",  # 决策13 方案A
        "start_date", "entry_date",                  # 决策4 纳入（报告文号留文书阶段）
    ],
    "target_scope": [
        "scope", "target_unit", "extend_unit", "audit_focus",  # 决策4 确认（列名=audit_focus）
    ],
    "items": [],  # 审计事项走专用 /items 接口，不走字段更新
}

# 各阶段可执行动作（前端据此禁用/启用 Tab 与按钮）
STAGE_ACTIONS = {
    "basic": ["save_basic"],
    "target_scope": ["save_basic", "save_target_scope"],
    "items": ["save_basic", "save_target_scope", "save_items", "split_items"],
    "workspace": ["save_basic", "finalize", "upload", "analysis"],
}


def stage_index(stage):
    """阶段排名（basic=0）。未知阶段返回 -1。"""
    try:
        return STAGES.index(stage)
    except ValueError:
        return -1


def allowed_actions(setup_stage):
    """返回某阶段可执行动作列表"""
    return STAGE_ACTIONS.get(setup_stage or "basic", ["save_basic"])


def filter_fields(stage, data):
    """只保留某阶段及之前所有阶段白名单内的字段。

    basic 字段（name/audit_type/period 等）在所有阶段可编辑；
    越阶段字段（如 basic 阶段提交 scope）被忽略，兼容旧前端。
    """
    if not isinstance(data, dict):
        return {}
    idx = stage_index(stage)
    allowed = set()
    for s in STAGES[:idx + 1]:
        allowed.update(STAGE_FIELDS.get(s, []))
    return {k: v for k, v in data.items() if k in allowed}


def required_fields_for(target_stage):
    """推进到 target_stage 所需的项目级字段（audit_items 为行数，由路由层提供）"""
    req = []
    if stage_index(target_stage) >= stage_index("target_scope"):
        req.append("scope")
    if stage_index(target_stage) >= stage_index("items"):
        req.append("audit_items")
    return req


def missing_for(project, target_stage, item_count=0):
    """返回推进到 target_stage 还缺的字段（用于 409 + missing_fields）"""
    missing = []
    project = project or {}
    for field in required_fields_for(target_stage):
        if field == "audit_items":
            if item_count <= 0:
                missing.append("audit_items")
        elif not project.get(field):
            missing.append(field)
    return missing


def check_stage(project, min_stage, item_count=0):
    """校验项目是否至少处于 min_stage。

    Returns:
        (ok: bool, missing: list[str])
        未达标时 ok=False，并返回推进所需的缺失字段清单。
    """
    cur = stage_index((project or {}).get("setup_stage") or "basic")
    if cur < stage_index(min_stage):
        return False, missing_for(project, min_stage, item_count)
    return True, []


def enrich(dto):
    """给项目 DTO 附加 setup_stage / allowed_actions / missing_fields

    missing_fields 仅字段级（scope 等），不含 audit_items —— audit_items 缺失由
    推进端点用 check_stage(item_count) 权威校验后返回。
    """
    stage = dto.get("setup_stage") or "basic"
    dto["setup_stage"] = stage
    dto["allowed_actions"] = allowed_actions(stage)
    idx = stage_index(stage)
    next_stage = STAGES[idx + 1] if 0 <= idx < len(STAGES) - 1 else None
    if next_stage:
        req = [f for f in required_fields_for(next_stage) if f != "audit_items"]
        dto["missing_fields"] = [f for f in req if not (dto.get(f))]
    else:
        dto["missing_fields"] = []
    return dto


if __name__ == "__main__":
    # 简单自测
    print("STAGES:", STAGES)
    print("allowed_actions(basic):", allowed_actions("basic"))
    print("allowed_actions(workspace):", allowed_actions("workspace"))
    p = {"setup_stage": "basic", "scope": ""}
    print("check to items:", check_stage(p, "items", item_count=0))
    p2 = {"setup_stage": "target_scope", "scope": "范围"}
    print("check to items:", check_stage(p2, "items", item_count=2))
