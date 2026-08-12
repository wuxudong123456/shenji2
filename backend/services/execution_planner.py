"""Phase 2 — 执行计划生成器

按选中的违规模型批量生成执行计划（违规→表达式→表探测→执行→命中明细）。
表探测逻辑提取自 audit_analyzer._detect_target_table，作为公共函数供 expression/execute 端点复用。
"""
import re
from services.expression_engine import execute_expression


def detect_target_table(expression: str, project_id: str) -> str:
    """根据表达式字段自动探测目标数据表（提取自 audit_analyzer._detect_target_table）

    策略: 字段签名匹配 → 回退查有数据的表 → 最终回退 data_contracts
    """
    TABLE_SIGNATURES = {
        "data_contracts": ["contract_no", "party_a", "party_b", "procurement_method", "sign_date", "amount"],
        "data_finance": ["account_no", "debit_amount", "credit_amount", "voucher_no", "bank_name"],
        "data_legal_docs": ["case_no", "issuing_body", "legal_basis", "verdict"],
        "data_registers": ["register_type", "item_name", "quantity", "responsible_person"],
        "data_credentials": ["cert_type", "cert_no", "holder", "expire_date"],
        # 以下 3 表原缺失（写侧 task_worker:813 八表齐全，扫侧只列 5 表）
        # → 扫描器永远路由不到有数据的 data_general/data_procurements，total=0 误报"数据不足"
        # 字段名逐字照搬 schema.sql 各表真实列名
        "data_procurements": ["procurement_method", "subject_name", "supplier", "budget_amount", "contract_amount", "bid_date", "sign_date"],
        "data_general": ["category", "title", "summary", "issuing_body", "doc_date"],
        "data_interviews": ["interviewee", "interview_date", "location", "transcript"],
    }

    field_pattern = re.compile(r'([a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)')
    fields_in_expr = set()
    for match in field_pattern.finditer(expression):
        field = match.group(1)
        if field.upper() not in ("AND", "OR", "BETWEEN", "NULL", "TRUE", "FALSE", "LIKE", "IN"):
            fields_in_expr.add(field.lower())

    if not fields_in_expr:
        return "data_contracts"

    best_table, best_score = None, 0
    for table, signatures in TABLE_SIGNATURES.items():
        score = sum(1 for s in signatures if s.lower() in fields_in_expr)
        if score > best_score:
            best_score = score
            best_table = table

    if best_score >= 1:
        return best_table

    # 回退：查项目中有数据的表
    from services.db import query_one
    for table in TABLE_SIGNATURES:
        row = query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = %s",
            (project_id,), database="tt",
        )
        if row and row.get("n", 0) > 0:
            return table

    return "data_contracts"


def build_and_execute(violation_ids: list, project_id: str) -> list:
    """按违规 ID 批量生成执行计划并执行

    每个违规: 从知识库取 expression_text → 探测表 → execute_expression → 命中明细

    Returns:
        list[dict]: 每项含 violation_id/violation_name/expression/table/executable/total/hits/rows
    """
    from services.knowledge_service import get_violation_detail
    results = []
    for vid in violation_ids:
        try:
            v = get_violation_detail(vid)
            expr = v.get("expression_text", "") if v else ""
            vname = v.get("violation_title", "") if v else str(vid)

            if not expr:
                results.append({"violation_id": vid, "violation_name": vname,
                                "expression": "", "table": "", "executable": False,
                                "reason": "无表达式", "total": 0, "hits": 0, "rows": []})
                continue

            table = detect_target_table(expr, project_id)
            scan = execute_expression(expr, table, project_id)
            results.append({
                "violation_id": vid, "violation_name": vname,
                "expression": expr, "table": table,
                "executable": scan.get("success", False),
                "total": scan.get("total", 0),
                "hits": scan.get("hits", 0),
                "rows": scan.get("rows", []),
                "error": scan.get("error", "") if not scan.get("success") else "",
            })
        except Exception as e:
            results.append({"violation_id": vid, "violation_name": "",
                            "expression": "", "table": "", "executable": False,
                            "reason": str(e), "total": 0, "hits": 0, "rows": []})
    return results
