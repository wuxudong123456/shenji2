"""Phase 4.2 — 违规表达式执行引擎: AST → 逐行求值

对数据工坊6张表（data_contracts/finance/legal_docs/registers/credentials/general）
的每一行执行表达式AST，返回命中/未命中结果。

用法:
    from services.expression_engine import execute_expression
    result = execute_expression(expression, "data_contracts", "project-id")
    # → {total, hits, hit_rate, rows: [{id, fields, matched}]}
"""
from services.db import query
from services.expression_parser import parse_expression


def _eval_ast(ast: dict, row: dict) -> bool:
    """对一行数据执行AST求值

    Args:
        ast: parse_expression() 返回的AST节点
        row: 数据行字典 {列名: 值}

    Returns:
        bool: 是否命中（违规条件成立）
    """
    t = ast.get("type", "?")

    # 逻辑节点
    if t == "AND":
        return _eval_ast(ast["left"], row) and _eval_ast(ast["right"], row)
    if t == "OR":
        return _eval_ast(ast["left"], row) or _eval_ast(ast["right"], row)

    # 比较节点: 从 row 中取字段值，与 ast.value 比较
    if t in ("GT", "LT", "EQ", "NE", "GTE", "LTE"):
        field_name = ast["field"]
        target = ast["value"]

        # 处理复合字段名（如 "领料单号.数量"）
        if "." in field_name:
            field_name = field_name.split(".")[-1]

        # 裸字字段引用：target 是非数字字符串且能解析到本行列时，视为字段引用
        # （使 field=field / field>field 生效，如 合同金额>预算金额、合同项目名称=预算项目名称；
        #  带引号字面量如 '公开招标' 与不匹配任何列的裸字仍按字面量处理，安全）
        if isinstance(target, str):
            try:
                float(target)
            except (ValueError, TypeError):
                ref_val = _get_row_value(row, target)
                if ref_val is not None:
                    target = ref_val

        # 取字段值（支持英文列名 + 中文别名，如 金额→amount）
        row_value = _get_row_value(row, field_name)

        if row_value is None:
            return False  # 字段不存在，默认不命中

        # 类型统一：数字比较
        if isinstance(target, (int, float)) and row_value is not None:
            try:
                row_value = float(row_value) if row_value is not None else 0
            except (ValueError, TypeError):
                pass

        try:
            if t == "GT":
                return float(row_value) > float(target) if row_value is not None else False
            if t == "LT":
                return float(row_value) < float(target) if row_value is not None else False
            if t == "GTE":
                return float(row_value) >= float(target) if row_value is not None else False
            if t == "LTE":
                return float(row_value) <= float(target) if row_value is not None else False
            if t == "EQ":
                if target is None:
                    return row_value is None
                return str(row_value).strip() == str(target).strip()
            if t == "NE":
                if target is None:
                    return row_value is not None
                return str(row_value).strip() != str(target).strip()
        except (ValueError, TypeError):
            return False

    # BETWEEN 节点
    if t == "BETWEEN":
        field_name = ast["field"]
        if "." in field_name:
            field_name = field_name.split(".")[-1]

        row_value = _get_row_value(row, field_name)

        if row_value is None:
            return False
        try:
            rv = float(row_value)
            lo = float(ast["low"])
            hi = float(ast["high"])
            return lo <= rv <= hi
        except (ValueError, TypeError):
            return False

    # TRUTHY: 字段存在且非空/非0
    if t == "TRUTHY":
        val = ast.get("value", "")
        v = _get_row_value(row, val)
        return v is not None and v != "" and v != 0

    # Q2.1 新增：IN / NOT IN
    if t in ("IN", "NOT_IN"):
        field_name = ast["field"]
        if "." in str(field_name):
            field_name = str(field_name).split(".")[-1]
        row_value = _get_row_value(row, field_name)
        if row_value is None:
            return False
        in_list = any(str(row_value).strip() == str(v).strip() for v in ast["values"])
        # 也尝试数值比较
        if not in_list:
            for v in ast["values"]:
                try:
                    if float(row_value) == float(v):
                        in_list = True
                        break
                except (ValueError, TypeError):
                    pass
        return in_list if t == "IN" else (not in_list)

    # P1-1: IS NULL / IS NOT NULL（中文"为空"/"不为空"）
    if t == "IS_NULL":
        val = _get_row_value(row, ast["field"])
        return val is None or val == "" or str(val).strip() in ("未提供", "无", "null", "None")
    if t == "IS_NOT_NULL":
        val = _get_row_value(row, ast["field"])
        return val is not None and val != "" and str(val).strip() not in ("未提供", "无", "null", "None")

    # Q2.1 新增：算术比较 (金额/合同金额) > 0.03
    if t == "ARITH_CMP":
        left_val = _eval_arith(ast["left"], row)
        right_val = _eval_arith(ast["right"], row)
        # 任一侧字段缺失 → 不可比较，判不命中（SQL 三值逻辑；
        # 防 NULL 预算被当 0 致 `合同金额 > 预算金额*1.0` 假阳性）
        if left_val is _MISSING or right_val is _MISSING:
            return False
        op = ast["op"]
        try:
            if op == "GT":
                return left_val > right_val
            if op == "LT":
                return left_val < right_val
            if op == "GTE":
                return left_val >= right_val
            if op == "LTE":
                return left_val <= right_val
            if op == "EQ":
                return left_val == right_val
            if op == "NE":
                return left_val != right_val
        except (ValueError, TypeError):
            return False

    # 字面量
    if t == "literal":
        return bool(ast.get("value", False))

    return False


def _get_row_value(row: dict, field_name: str):
    """从行中取字段值（不区分大小写，处理 表.字段 格式，支持中文别名）"""
    if "." in str(field_name):
        field_name = str(field_name).split(".")[-1]
    fn = str(field_name)
    # 1) 直接列名匹配（英文列名，不区分大小写）
    for k, v in row.items():
        if k.lower() == fn.lower():
            return v
    # 2) 中文别名 → 英文列名：遍历 field_mapper 全量别名表（覆盖所有 data_* 表的所有别名）
    try:
        from services.field_mapper import FIELD_ALIAS_MAP
        # 精确
        for table_aliases in FIELD_ALIAS_MAP.values():
            col = table_aliases.get(fn)
            if col and col in row:
                return row[col]
        # 模糊（别名键互为子串）
        for table_aliases in FIELD_ALIAS_MAP.values():
            for ak, ac in table_aliases.items():
                if (ak in fn or fn in ak) and ac in row:
                    return row[ac]
    except Exception:
        pass
    return None


def _col_to_cn(col: str) -> str:
    """英文列名→中文字段名（反向映射，用于表达式字段对齐）"""
    # 简单的内置反向映射
    REVERSE = {
        "party_a": "甲方", "party_b": "乙方", "amount": "金额",
        "procurement_method": "采购方式", "sign_date": "签订日期",
        "account_name": "账户名称", "debit_amount": "借方金额", "credit_amount": "贷方金额",
        "voucher_no": "凭证号", "quantity": "数量",
    }
    return REVERSE.get(col, col)


# 算术求值的"字段缺失"哨兵——区别于真实数值 0
# 字段为 None/"" 时返回它，ARITH_CMP 见到即判不命中（SQL 三值逻辑），
# 避免 `合同金额 > 预算金额*1.0` 在预算金额缺失时把 NULL 当 0，
# 误判成"合同金额 > 0"恒真（假阳性）。与 _eval_ast 普通 GT/LT 节点
# (L56-57 "字段为空→False") 行为对齐。
_MISSING = object()


def _eval_arith(node: dict, row: dict):
    """递归求算术表达式节点的值

    字段为空(None/"")时返回 _MISSING（而非 0）并沿 ARITH 链传播；
    ARITH_CMP 收到 _MISSING 即判不命中。literal 解析失败仍返回 0（常量非数据）。
    """
    t = node.get("type")
    if t == "ARITH":
        left = _eval_arith(node["left"], row)
        right = _eval_arith(node["right"], row)
        if left is _MISSING or right is _MISSING:
            return _MISSING  # NULL 传播：任一操作数缺失 → 结果不可定
        op = node["op"]
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right if right != 0 else 0
    if t == "field":
        val = _get_row_value(row, node["value"])
        if val is None or val == "":
            return _MISSING
        try:
            return float(val)
        except (ValueError, TypeError):
            return _MISSING
    if t == "literal":
        try:
            return float(node.get("value", 0))
        except (ValueError, TypeError):
            return 0
    return 0


def execute_expression(expression: str, table: str, project_id: str,
                       limit: int = 2000) -> dict:
    """对指定数据表执行违规表达式（Q2.4 三层分级调度）

    自动判断表达式层级:
      - row:        逐行求值（现有 AST 引擎，补强后支持 IN/算术）
      - aggregate:  LLM 生成 SQL + 缓存，MySQL 执行聚合查询
      - semantic:   LLM 生成 SQL + 语义函数 UDF 二次过滤

    Args:
        expression: 伪SQL表达式字符串
        table: 目标表名（data_contracts/finance/... 或简写）
        project_id: 项目ID
        limit: 行级模式的最大扫描行数

    Returns:
        {
            success, total, hits, hit_rate, rows,
            ast, layer,    # layer: row/aggregate/semantic
            sql_cache_status  # aggregate/semantic 层的缓存状态
        }
    """
    # 标准化表名
    allowed_tables = {
        "data_contracts", "data_finance", "data_legal_docs",
        "data_registers", "data_credentials", "data_general", "data_procurements",
        "contracts", "finance", "legal_docs", "registers", "credentials", "general",
        "procurements",
    }
    if table not in allowed_tables:
        return {"success": False, "error": f"不支持的表: {table}"}
    if not table.startswith("data_"):
        table = f"data_{table}"

    # Q2.4 分级
    from services.expression_classifier import classify_expression
    layer = classify_expression(expression)

    if layer == "row":
        return _execute_row(expression, table, project_id, limit)
    elif layer == "aggregate":
        return _execute_aggregate(expression, table, project_id)
    else:  # semantic
        return _execute_semantic(expression, table, project_id)


# ── 第 1 层：行级求值 ──

def _execute_row(expression: str, table: str, project_id: str,
                 limit: int = 2000) -> dict:
    """行级表达式：AST 解析 → 逐行求值"""
    try:
        ast = parse_expression(expression)
    except SyntaxError as e:
        return {"success": False, "error": f"表达式语法错误: {e}", "layer": "row"}

    # project_id 可选：为空时扫全库（数据工坊全局视图），非空时按项目过滤
    if project_id:
        rows = query(
            f"SELECT * FROM {table} WHERE project_id = %s LIMIT %s",
            (project_id, limit), database="tt"
        )
    else:
        rows = query(
            f"SELECT * FROM {table} LIMIT %s",
            (limit,), database="tt"
        )

    hit_rows = []
    for row in rows:
        if _eval_ast(ast, row):
            hit_rows.append({
                "row_id": row.get("id"),
                "matched": True,
                "fields": _clean_row(row),
            })

    total = len(rows)
    hits = len(hit_rows)
    return {
        "success": True,
        "layer": "row",
        "total": total,
        "hits": hits,
        "hit_rate": round(hits / total, 4) if total > 0 else 0,
        "rows": hit_rows[:500],
        "ast": ast,
    }


# ── 第 2 层：聚合表达式（LLM 生成 SQL + 缓存）──

def _execute_aggregate(expression: str, table: str, project_id: str) -> dict:
    """聚合表达式：LLM 生成 SQL → MySQL 执行"""
    from services.sql_generator import get_or_generate_sql

    sql_result = get_or_generate_sql(expression, table)
    if not sql_result.get("sql"):
        return {
            "success": False,
            "layer": "aggregate",
            "error": "无法生成 SQL（LLM 翻译失败或表达式过于复杂）",
            "sql_cache_status": "failed",
        }

    sql = sql_result["sql"]
    status = sql_result["status"]

    # Q2.2 安全：未经人工确认的 SQL 不执行（Submit→Confirm→Execute 原则）
    # generated_pending 状态返回 needs_review，前端弹确认框，approve 后才执行
    if status != "cached":
        return {
            "success": False,
            "layer": "aggregate",
            "needs_review": True,
            "message": "聚合表达式已生成 SQL，待人工确认后执行",
            "sql": sql,
            "sql_cache_status": status,
            "sql_cache_id": sql_result.get("id"),
        }

    # SQL 安全校验：只允许只读 SELECT，拒绝任何写操作（防 LLM 产出危险语句）
    if not _is_safe_select(sql):
        return {
            "success": False,
            "layer": "aggregate",
            "error": "生成的 SQL 未通过安全校验（仅允许只读 SELECT）",
            "sql": sql,
            "sql_cache_status": status,
        }

    # 执行 SQL（参数化 project_id）
    try:
        # 替换占位符 :project_id → 实际值（用参数化查询）
        if ":project_id" in sql:
            sql_with_params = sql.replace(":project_id", "%s")
            rows = query(sql_with_params, (project_id,), database="tt")
        else:
            rows = query(sql, database="tt")

        # 更新缓存表的执行记录
        if sql_result.get("id"):
            from services.db import execute as _execute
            try:
                _execute(
                    "UPDATE audit_expression_sql SET last_executed_at=NOW(), "
                    "hit_count=hit_count+%s WHERE id=%s",
                    (len(rows), sql_result["id"]), database="tt",
                )
            except Exception:
                pass

        hit_rows = [{"row_id": r.get("id"), "matched": True, "fields": _clean_row(r)}
                    for r in rows[:500]]
        return {
            "success": True,
            "layer": "aggregate",
            "total": len(rows),   # 聚合层 total=hits（命中即返回的分组）
            "hits": len(rows),
            "hit_rate": 1.0 if rows else 0.0,
            "rows": hit_rows,
            "sql": sql,
            "sql_cache_status": status,
            "sql_cache_id": sql_result.get("id"),
            "note": "聚合层：返回的即命中分组（SQL 已人工确认）",
        }
    except Exception as e:
        # 记录执行错误
        if sql_result.get("id"):
            from services.db import execute as _execute
            try:
                _execute(
                    "UPDATE audit_expression_sql SET error_msg=%s WHERE id=%s",
                    (str(e)[:2000], sql_result["id"]), database="tt",
                )
            except Exception:
                pass
        return {
            "success": False,
            "layer": "aggregate",
            "error": f"SQL 执行失败: {e}",
            "sql": sql,
            "sql_cache_status": status,
        }


def _is_safe_select(sql: str) -> bool:
    """校验 LLM 生成的 SQL 是否为安全的只读查询

    最后一道防线，防止 LLM 产出的 SQL 含写操作或非 SELECT 语句：
      - 必须以 SELECT 或 WITH(CTE) 开头（跳过前导注释）
      - 不得含写操作关键字（词边界匹配，updated_at/create_time 等列名不会误判）
    """
    import re
    s = sql.strip().lower()
    # 跳过前导注释，定位首个真实语句
    while s.startswith("--") or s.startswith("/*"):
        if s.startswith("--"):
            nl = s.find("\n")
            s = s[nl + 1:].strip() if nl >= 0 else ""
        else:
            end = s.find("*/")
            s = s[end + 2:].strip() if end >= 0 else ""
    if not (s.startswith("select") or s.startswith("with ")):
        return False
    if re.search(r"\b(insert|update|delete|drop|alter|truncate|"
                 r"create|grant|replace|rename|load\s+data)\b", s):
        return False
    return True


# ── 第 3 层：语义表达式（SQL + UDF 二次过滤）──

def _execute_semantic(expression: str, table: str, project_id: str) -> dict:
    """语义表达式：先尝试 SQL 翻译，再用 UDF 二次过滤

    策略:
      1. 同聚合层，LLM 生成 SQL（语义函数转占位符 TRUE）
      2. SQL 执行得到候选行
      3. 用 semantic_functions 对候选行做 Python 二次过滤
    """
    from services.sql_generator import get_or_generate_sql
    from services import semantic_functions as sf

    sql_result = get_or_generate_sql(expression, table)

    # 如果 LLM 能翻译（即使含语义函数占位符），先执行 SQL 取候选集
    candidate_rows = []
    sql_used = None
    if sql_result.get("sql"):
        sql = sql_result["sql"]
        sql_used = sql
        try:
            if ":project_id" in sql:
                rows = query(sql.replace(":project_id", "%s"), (project_id,), database="tt")
            else:
                rows = query(sql, database="tt")
            candidate_rows = list(rows)
        except Exception:
            # SQL 执行失败，降级为全表扫描
            candidate_rows = query(
                f"SELECT * FROM {table} WHERE project_id = %s LIMIT 2000",
                (project_id,), database="tt"
            )
    else:
        # 无法翻译，全表扫描后全靠 UDF 判断
        candidate_rows = query(
            f"SELECT * FROM {table} WHERE project_id = %s LIMIT 2000",
            (project_id,), database="tt"
        )

    # UDF 二次过滤
    hit_rows = []
    for row in candidate_rows:
        if _eval_semantic(expression, row, sf):
            hit_rows.append({
                "row_id": row.get("id"),
                "matched": True,
                "fields": _clean_row(row),
            })

    total = len(candidate_rows)
    hits = len(hit_rows)
    return {
        "success": True,
        "layer": "semantic",
        "total": total,
        "hits": hits,
        "hit_rate": round(hits / total, 4) if total > 0 else 0,
        "rows": hit_rows[:500],
        "sql": sql_used,
        "sql_cache_status": sql_result.get("status"),
        "note": "语义层：SQL候选集 + UDF二次过滤",
    }


def _eval_semantic(expression: str, row: dict, sf_module) -> bool:
    """对单行执行语义判定

    简化策略：检测表达式里调用的语义函数，对行里的字段求值。
    对于复杂嵌套表达式，这里做保守判定（默认不命中，避免误报）。
    """
    expr_upper = expression.upper()

    # SAME_DEPT_AND_SUPPLIER_GROUP(采购单位, 供应商)
    if "SAME_DEPT_AND_SUPPLIER_GROUP" in expr_upper:
        buyer = (row.get("party_a") or row.get("采购单位") or
                 _get_row_value(row, "采购单位") or "")
        supplier = (row.get("party_b") or row.get("供应商") or
                    _get_row_value(row, "供应商") or "")
        if buyer and supplier:
            return sf_module.same_dept_and_supplier_group(buyer, supplier)

    # SAME_CATEGORY_OR_SIMILAR
    if "SAME_CATEGORY_OR_SIMILAR" in expr_upper:
        cat1 = _get_row_value(row, "采购品目") or row.get("category") or ""
        cat2 = _get_row_value(row, "品目") or cat1
        if cat1:
            return sf_module.same_category_or_similar(str(cat1), str(cat2))

    # DATE_DIFF / 工作日
    if "DATE_DIFF_WORKDAY" in expr_upper or "工作日" in expression:
        # 通用日期差检测：找行里的两个日期字段
        d1 = row.get("sign_date") or row.get("voucher_date") or row.get("doc_date")
        d2 = row.get("effective_date") or row.get("register_date") or row.get("expire_date")
        if d1 and d2:
            diff = sf_module.date_diff_workday(d1, d2)
            # 简化：如果工作日差 > 5，视为可疑（具体阈值应由表达式指定，这里保守）
            return diff > 5

    # 含 EXISTS/嵌套SELECT 等无法在行级判断的，保守返回 False
    return False


def _clean_row(row: dict) -> dict:
    """清理行数据用于 JSON 输出"""
    clean = {}
    for k, v in row.items():
        if isinstance(v, bytes):
            continue
        clean[k] = str(v)[:200] if v is not None and len(str(v)) > 200 else v
    return clean
