"""报告生命周期服务 — 报告段状态机（drafting → reviewing → issued → filed）

与 project_lifecycle.py（立项向导 setup_stage）结构对称，但专管 active 之后的
"报告段"。报告段状态走独立列 report_stage，**不共用 status**——因为
status='archived' 已被软删除占用（audit_routes.py 删除路由 SET deleted=1,
status='archived'）。NULL 表示"未启动报告段"（项目尚在实施段 status=active）。

三字段分工（各管一段，互不干扰）：
  setup_stage   立项向导 (basic→…→workspace)
  status        实施段 + 软删除 (draft/active/archived)
  report_stage  报告段 (NULL→drafting→reviewing→issued→filed)  ← 本模块
"""
import json  # noqa: F401  （与 project_lifecycle 对齐保留，便于后续扩展 JSON 字段）

# ── 阶段定义 ──
REPORT_STAGES = ["drafting", "reviewing", "issued", "filed"]

# 合法流转白名单（cur=None 表示"未启动"，可推进到 drafting）
TRANSITIONS = {
    None:        ["drafting"],
    "drafting":  ["reviewing"],
    "reviewing": ["issued"],
    "issued":    ["filed"],
}

# 各阶段可执行动作（前端据此启用/禁用按钮；后端校验同源）
STAGE_ACTIONS = {
    None:        ["start_report"],
    "drafting":  ["edit_report", "submit_review"],
    "reviewing": ["adopt_and_issue"],
    "issued":    ["archive"],
    "filed":     [],
}


def stage_index(stage):
    """阶段排名（drafting=0）。None（未启动）与未知阶段均返回 -1。"""
    if stage is None:
        return -1
    try:
        return REPORT_STAGES.index(stage)
    except ValueError:
        return -1


def can_transition(cur, nxt):
    """是否允许 cur→nxt 流转。cur=None 表示当前未启动报告段。"""
    return nxt in TRANSITIONS.get(cur, [])


def allowed_actions(report_stage):
    """返回某阶段可执行动作列表"""
    return STAGE_ACTIONS.get(report_stage, [])


def check_prerequisites(project, target_stage, deliverables=None):
    """推进到 target_stage 的前置条件校验。

    Args:
        project:      audit_projects 行（dict）
        target_stage: 目标阶段
        deliverables: 该项目的交付物列表（路由层查好传入）；
                      drafting→reviewing / reviewing→issued 需要据此判断

    Returns:
        (ok: bool, missing: list[str])  missing 用人类可读标识说明缺什么
    """
    project = project or {}
    deliverables = deliverables or []
    missing = []

    if target_stage == "drafting":
        # NULL→drafting：必须已进实施段（status=active 且 setup_stage=workspace）
        if (project.get("status") or "") != "active":
            missing.append("status=active")
        if (project.get("setup_stage") or "") != "workspace":
            missing.append("setup_stage=workspace")

    elif target_stage == "reviewing":
        # drafting→reviewing：至少 1 份 type=report 的交付物（报告草稿）
        reports = [d for d in deliverables if d.get("deliverable_type") == "report"]
        if not reports:
            missing.append("report_deliverable")

    elif target_stage == "issued":
        # reviewing→issued：存在 type=report 且 status=adopted（已采纳/定稿）的交付物
        adopted = [
            d for d in deliverables
            if d.get("deliverable_type") == "report"
            and (d.get("status") or "") == "adopted"
        ]
        if not adopted:
            missing.append("adopted_report")

    elif target_stage == "filed":
        # issued→filed：归档号已填（审计档案按项目归档，archive_no 是项目级属性）
        if not (project.get("archive_no") or "").strip():
            missing.append("archive_no")

    return (len(missing) == 0), missing


def enrich(dto):
    """给项目 DTO 附加 report_stage 相关字段（供前端切换按钮；后端路由可选调用）"""
    stage = dto.get("report_stage")  # None 保留原样
    dto["report_stage"] = stage
    dto["report_allowed_actions"] = allowed_actions(stage)
    return dto


if __name__ == "__main__":
    # 简单自测
    print("REPORT_STAGES:", REPORT_STAGES)
    print("can_transition(None,'drafting'):", can_transition(None, "drafting"))
    print("can_transition('drafting','reviewing'):", can_transition("drafting", "reviewing"))
    print("can_transition('drafting','filed'):", can_transition("drafting", "filed"))  # False 非法跳跃
    p_active = {"status": "active", "setup_stage": "workspace"}
    print("check to drafting (active):", check_prerequisites(p_active, "drafting"))
    p_not_active = {"status": "draft", "setup_stage": "basic"}
    print("check to drafting (draft):", check_prerequisites(p_not_active, "drafting"))
    print("check to reviewing (无交付物):", check_prerequisites(p_active, "reviewing", []))
    print("check to filed (无归档号):", check_prerequisites({"archive_no": ""}, "filed"))
