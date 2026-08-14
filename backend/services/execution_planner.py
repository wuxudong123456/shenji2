"""Phase 2 — 执行计划生成器

按选中的违规模型批量生成执行计划（违规→表达式→表探测→执行→命中明细）。
表探测逻辑提取自 audit_analyzer._detect_target_table，作为公共函数供 expression/execute 端点复用。
"""
import re
from services.expression_engine import execute_expression
from services.db import query, get_columns


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
                    + judge_source(rule/llm/manual)/judge_note
    """
    from services.audit_item_rule_service import get_violation_rule
    from services.rule_engine_registry import execute_violation_rule
    from services.knowledge_service import get_violation_detail
    results = []
    for vid in violation_ids:
        try:
            configured_rule = get_violation_rule(vid)
            if configured_rule and configured_rule.get("executor_type") == "procurement_cross_doc":
                results.append(execute_violation_rule(configured_rule, project_id))
                continue

            v = get_violation_detail(vid)
            # 改动②-3:优先用清洗后表达式(normalized 为空则回退原值,列未建时 v.get 返回 None 自动回退)
            expr = (v.get("expression_normalized") or v.get("expression_text") or "") if v else ""
            vname = v.get("violation_title", "") if v else str(vid)

            if not expr:
                results.append({"violation_id": vid, "violation_name": vname,
                                "expression": "", "table": "", "executable": False,
                                "reason": "无表达式", "total": 0, "hits": 0, "rows": [],
                                "judge_source": "rule", "judge_note": ""})
                continue

            table = detect_target_table(expr, project_id)
            scan = execute_expression(expr, table, project_id)

            # ── 改动①:LLM 语义降级 ──
            # 规则语法错或 0 命中(且非聚合 needs_review 门禁)时,让 LLM 按语义再判一遍
            judge_source = "rule"
            judge_note = ""
            needs_review = scan.get("needs_review", False)
            if needs_review:
                judge_source = "manual"
            elif (scan.get("success") is False and scan.get("error")) or \
                 (scan.get("success") and scan.get("hits", 0) == 0 and scan.get("total", 0) > 0):
                from services.llm_semantic_judge import judge_violation_via_llm
                rd = v.get("required_data") if v else None
                judge = judge_violation_via_llm(expr, table, project_id, vname, rd)
                if judge.get("judged") and judge.get("hits", 0) > 0:
                    # LLM 判出命中 → 重写 scan 的 hits/rows(保留真实行数据供前端渲染)
                    scan = {
                        "success": True, "layer": "llm_judge",
                        "total": judge.get("judged_count", scan.get("total", 0)),
                        "hits": judge["hits"],
                        "rows": _build_llm_hit_rows(table, project_id, judge["rows"]),
                    }
                    judge_source = "llm"
                    judge_note = judge.get("note", "")
                else:
                    # LLM 未判出/失败 → 保持原 scan 不变,前端行为同现状
                    judge_note = judge.get("error", "") or judge.get("note", "")
            # ── 降级结束 ──

            results.append({
                "violation_id": vid, "violation_name": vname,
                "expression": expr, "table": table,
                "executable": scan.get("success", False),
                "total": scan.get("total", 0),
                "hits": scan.get("hits", 0),
                "rows": scan.get("rows", []),
                "error": scan.get("error", "") if not scan.get("success") else "",
                "judge_source": judge_source,
                "judge_note": judge_note,
            })
        except Exception as e:
            results.append({"violation_id": vid, "violation_name": "",
                            "expression": "", "table": "", "executable": False,
                            "reason": str(e), "total": 0, "hits": 0, "rows": [],
                            "judge_source": "rule", "judge_note": ""})
    return results


def _collect_ast_fields(ast, out=None) -> set:
    """递归收集 AST 中引用的字段名（仅取值字段，跳过字面量）

    比 detect_target_table 的 regex 更精确——regex 会误提引号里的字面量
    （如 RA-002 的 '询价采购'），导致假性 field_mismatch。
    """
    if out is None:
        out = set()
    if not isinstance(ast, dict):
        return out
    t = ast.get("type")
    if t in ("AND", "OR", "ARITH"):
        _collect_ast_fields(ast.get("left"), out)
        _collect_ast_fields(ast.get("right"), out)
    elif t in ("GT", "LT", "EQ", "NE", "GTE", "LTE", "BETWEEN",
               "IN", "NOT_IN", "IS_NULL", "IS_NOT_NULL"):
        f = ast.get("field")
        if f:
            out.add(str(f).split(".")[-1])
    elif t == "TRUTHY":
        v = ast.get("value")
        if v:
            out.add(str(v).split(".")[-1])
    elif t == "ARITH_CMP":
        _collect_ast_fields(ast.get("left"), out)
        _collect_ast_fields(ast.get("right"), out)
    elif t == "field":
        v = ast.get("value")
        if v:
            out.add(str(v).split(".")[-1])
    return out


def _resolve_field_to_col(table: str, field_name: str, cols) -> str | None:
    """字段名 → 目标表真实列名（对齐 engine._get_row_value 的解析语义）

    引擎取值时遍历全表别名找能落到当前行列上的列；预检须同样宽松，
    否则会把"引擎能解析"的字段误判为缺失。
    """
    from services.field_mapper import get_column_for_expr_field, FIELD_ALIAS_MAP
    # 1) 直接列名
    if field_name in cols:
        return field_name
    # 2) 目标表别名（精确+模糊）
    resolved = get_column_for_expr_field(table, field_name)
    if resolved and resolved in cols:
        return resolved
    # 3) 跨表别名兜底：任一表的别名解析到目标表存在的列（对齐引擎全表搜索）
    for table_aliases in FIELD_ALIAS_MAP.values():
        col = table_aliases.get(field_name)
        if col and col in cols:
            return col
    for table_aliases in FIELD_ALIAS_MAP.values():
        for ak, ac in table_aliases.items():
            if (ak in field_name or field_name in ak) and ac in cols:
                return ac
    return None


def precheck_expression(expression: str, project_id: str) -> dict:
    """轻量预检：判断违规表达式在当前项目数据上"能否命中"，零行扫描。

    供 Step② 推荐排序用——区分 hittable / syntax_error / field_mismatch /
    needs_llm / no_data。全程只做 parse + 字段存在性 + 层级 + COUNT，
    不调用 execute_expression 扫数据。

    Returns:
        {verdict, layer, table, fields_missing[], detail}
    """
    from services.expression_parser import parse_expression
    from services.expression_classifier import classify_expression

    expr = (expression or "").strip()
    if not expr:
        return {"verdict": "no_expr", "layer": "", "table": "",
                "fields_missing": [], "detail": "无表达式"}

    # 1) 语法
    try:
        ast = parse_expression(expr)
    except Exception as e:
        return {"verdict": "syntax_error", "layer": "", "table": "",
                "fields_missing": [], "detail": f"表达式语法错: {e}"}

    # 2) 层级（聚合/语义需 LLM，行级才可预判）
    layer = classify_expression(expr)
    if layer in ("aggregate", "semantic"):
        return {"verdict": "needs_llm", "layer": layer, "table": "",
                "fields_missing": [], "detail": "聚合/语义表达式，扫描时由LLM判断"}

    # 3) 目标表 + 数据存在性
    table = detect_target_table(expr, project_id)
    if not _table_has_data(table, project_id):
        return {"verdict": "no_data", "layer": layer, "table": table,
                "fields_missing": [], "detail": f"本项目无 {table} 数据"}

    # 4) 字段存在性
    cols = get_columns(table)
    missing = []
    if cols:  # 拿到列信息才校验；拿不到（information_schema 失败）则跳过，不误杀
        for f in _collect_ast_fields(ast):
            if not _resolve_field_to_col(table, f, cols):
                missing.append(f)

    if missing:
        return {"verdict": "field_mismatch", "layer": layer, "table": table,
                "fields_missing": missing,
                "detail": f"本项目 {table} 缺字段：{'、'.join(missing)}"}

    return {"verdict": "hittable", "layer": layer, "table": table,
            "fields_missing": [], "detail": "可命中"}


def _table_has_data(table: str, project_id: str) -> bool:
    """目标表在本项目是否有数据（复用 detect_target_table 回退段的 COUNT 范式）"""
    if not project_id or not table:
        return False
    try:
        from services.db import query_one
        row = query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id = %s",
            (project_id,), database="tt",
        )
        return bool(row and row.get("n", 0) > 0)
    except Exception:
        return True  # 查询失败时不因"无数据"误判，留给后续扫描


def _build_llm_hit_rows(table: str, project_id: str, llm_rows: list) -> list:
    """把 LLM 判定的 row_id 列表 → 带 fields 的命中行结构(对齐 execute_expression 的 rows 格式)

    LLM 只返回 row_id + reason,这里按 row_id 回查真实行数据填 fields,供前端渲染明细表。
    """
    if not llm_rows:
        return []
    rids = [r.get("row_id") for r in llm_rows if r.get("row_id") is not None]
    if not rids or not table.startswith("data_"):
        return []
    placeholders = ",".join(["%s"] * len(rids))
    try:
        rows = query(
            f"SELECT * FROM {table} WHERE project_id = %s AND id IN ({placeholders})",
            tuple([project_id] + rids), database="tt",
        )
    except Exception:
        return []
    # 按 LLM 判定顺序排列,附带 reason
    reason_map = {r.get("row_id"): r.get("reason", "") for r in llm_rows}
    rid_to_row = {r.get("id"): r for r in rows}
    result = []
    for rid in rids:
        row = rid_to_row.get(rid)
        if row:
            fields = {}
            for k, val in row.items():
                if isinstance(val, bytes):
                    continue
                fields[k] = str(val)[:200] if val is not None and len(str(val)) > 200 else val
            fields["_llm_reason"] = reason_map.get(rid, "")
            result.append({"row_id": rid, "matched": True, "fields": fields})
    return result[:500]
