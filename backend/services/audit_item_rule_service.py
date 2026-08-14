"""审计事项与可信执行规则的读取服务。"""
from __future__ import annotations

import json

from services.db import query, query_one


def get_item_rules(project_id: str, item_id: int) -> list[dict]:
    """返回指定项目事项绑定的规则，主规则优先。"""
    rows = query(
        "SELECT r.item_id, r.is_primary, r.match_reason, "
        "v.id AS violation_id, v.violation_code, v.violation_title, v.severity, "
        "v.description, v.required_data, "
        "er.target_table, er.expression, er.field_mapping, er.threshold, "
        "er.executor_type, er.executor_key, er.rule_version, er.result_group_key "
        "FROM audit_item_violation_refs r "
        "JOIN audit_violations v ON v.id = r.violation_id "
        "LEFT JOIN audit_engine_rules er ON er.violation_id = v.id "
        "WHERE r.project_id = %s AND r.item_id = %s AND COALESCE(v.deleted, 0) = 0 "
        "ORDER BY r.is_primary DESC, v.id",
        (project_id, item_id),
        database="tt",
    )
    return [_normalize(row) for row in rows]


def get_violation_rule(violation_id: int | str) -> dict | None:
    row = query_one(
        "SELECT v.id AS violation_id, v.violation_code, v.violation_title, v.severity, "
        "v.description, v.expression_text, er.target_table, er.expression, "
        "er.field_mapping, er.threshold, er.executor_type, er.executor_key, "
        "er.rule_version, er.result_group_key "
        "FROM audit_violations v "
        "LEFT JOIN audit_engine_rules er ON er.violation_id = v.id "
        "WHERE v.id = %s AND COALESCE(v.deleted, 0) = 0 "
        "ORDER BY er.id DESC LIMIT 1",
        (violation_id,),
        database="tt",
    )
    if not row:
        return None
    normalized = _normalize(row)
    normalized["expression"] = normalized.get("expression") or normalized.get("expression_text") or ""
    return normalized


def _normalize(row: dict) -> dict:
    result = dict(row)
    for key in ("required_data", "field_mapping", "threshold"):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                result[key] = [] if key == "required_data" else {}
    result["is_primary"] = bool(result.get("is_primary"))
    result["executor_type"] = result.get("executor_type") or "expression"
    return result
