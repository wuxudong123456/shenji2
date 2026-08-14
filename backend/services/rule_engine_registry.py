"""统一审计规则调度：兼容行级表达式与采购跨文档确定性规则。"""
from __future__ import annotations

import json

from services.procurement_audit_rules import (
    evaluate_rule,
    load_project_facts,
    precheck_rule,
)


def normalize_rule_row(rule: dict | None) -> dict:
    row = dict(rule or {})
    row["executor_type"] = row.get("executor_type") or "expression"
    for key in ("threshold", "field_mapping"):
        value = row.get(key)
        if isinstance(value, str):
            try:
                row[key] = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                row[key] = {}
        elif not isinstance(value, dict):
            row[key] = {}
    return row


def precheck_violation_rule(rule: dict, project_id: str) -> dict:
    row = normalize_rule_row(rule)
    executor_type = row["executor_type"]
    if executor_type == "procurement_cross_doc":
        result = precheck_rule(row.get("executor_key") or "", load_project_facts(project_id))
        return {
            **result,
            "executor_type": executor_type,
            "executor_key": row.get("executor_key") or "",
            "table": "cross_document",
            "fields_missing": result.get("missing_roles", []),
        }
    if executor_type == "expression":
        from services.execution_planner import precheck_expression
        result = precheck_expression(row.get("expression") or "", project_id)
        return {**result, "executor_type": executor_type, "executor_key": ""}
    return {
        "verdict": "unsupported",
        "executor_type": executor_type,
        "executor_key": row.get("executor_key") or "",
        "table": "",
        "fields_missing": [],
        "missing_roles": [],
        "detail": f"未注册的规则执行器：{executor_type}",
    }


def execute_violation_rule(rule: dict, project_id: str) -> dict:
    row = normalize_rule_row(rule)
    executor_type = row["executor_type"]
    violation_id = row.get("violation_id")
    violation_name = row.get("violation_name") or row.get("violation_title") or ""

    if executor_type == "procurement_cross_doc":
        raw = evaluate_rule(
            row.get("executor_key") or "",
            load_project_facts(project_id),
            row.get("threshold") or {},
        )
        rows = raw.get("rows", [])
        evidence_refs = _collect_evidence(rows)
        return {
            "violation_id": violation_id,
            "violation_name": violation_name or raw.get("rule_name", ""),
            "rule_code": raw.get("rule_code") or row.get("executor_key") or "",
            "expression": row.get("expression") or "",
            "table": "cross_document",
            "executor_type": executor_type,
            "executable": bool(raw.get("success")),
            "status": raw.get("status", "completed"),
            "total": raw.get("total", 0),
            "hits": raw.get("hits", 0),
            "rows": rows,
            "finding_key": row.get("result_group_key") or raw.get("result_group_key") or "",
            "result_group_key": row.get("result_group_key") or raw.get("result_group_key") or "",
            "evidence_refs": evidence_refs,
            "error": "" if raw.get("success") else raw.get("reason", "执行失败"),
            "reason": raw.get("reason", ""),
            "judge_source": "deterministic",
            "judge_note": "确定性跨文档规则",
        }

    if executor_type == "expression":
        from services.execution_planner import detect_target_table
        from services.expression_engine import execute_expression
        expression = row.get("expression") or ""
        table = row.get("target_table") or detect_target_table(expression, project_id)
        scan = execute_expression(expression, table, project_id)
        return {
            "violation_id": violation_id,
            "violation_name": violation_name,
            "expression": expression,
            "table": table,
            "executor_type": executor_type,
            "executable": bool(scan.get("success")),
            "status": "completed" if scan.get("success") else "error",
            "total": scan.get("total", 0),
            "hits": scan.get("hits", 0),
            "rows": scan.get("rows", []),
            "finding_key": row.get("result_group_key") or "",
            "result_group_key": row.get("result_group_key") or "",
            "evidence_refs": _collect_evidence(scan.get("rows", [])),
            "error": scan.get("error", "") if not scan.get("success") else "",
            "reason": "",
            "judge_source": "rule",
            "judge_note": "",
        }

    return {
        "violation_id": violation_id,
        "violation_name": violation_name,
        "executor_type": executor_type,
        "executable": False,
        "status": "unsupported",
        "total": 0,
        "hits": 0,
        "rows": [],
        "finding_key": row.get("result_group_key") or "",
        "result_group_key": row.get("result_group_key") or "",
        "evidence_refs": [],
        "error": f"未注册的规则执行器：{executor_type}",
        "reason": f"未注册的规则执行器：{executor_type}",
        "judge_source": "rule",
        "judge_note": "",
    }


def _collect_evidence(rows: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for row in rows or []:
        candidates = row.get("evidence") if isinstance(row, dict) else None
        if not candidates and isinstance(row, dict):
            candidates = [row]
        for evidence in candidates or []:
            trace_id = evidence.get("document_trace_id")
            if not trace_id or trace_id in seen:
                continue
            seen.add(trace_id)
            out.append({
                "document_trace_id": trace_id,
                "doc_name": evidence.get("doc_name") or "",
                "page_number": evidence.get("page_number"),
                "position_anchor": evidence.get("position_anchor") or "",
            })
    return out
