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

        # 不区分大小写匹配列名
        row_value = None
        for k, v in row.items():
            if k.lower() == field_name.lower():
                row_value = v
                break

        if row_value is None:
            return False  # 列不存在，默认不命中

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

        row_value = None
        for k, v in row.items():
            if k.lower() == field_name.lower():
                row_value = v
                break

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
        if "." in str(val):
            val = str(val).split(".")[-1]
        for k, v in row.items():
            if k.lower() == str(val).lower():
                return v is not None and v != "" and v != 0
        return False

    # 字面量
    if t == "literal":
        return bool(ast.get("value", False))

    return False


def execute_expression(expression: str, table: str, project_id: str,
                       limit: int = 2000) -> dict:
    """对指定数据表执行违规表达式

    Args:
        expression: 伪SQL表达式字符串
        table: 目标表名（data_contracts/finance/legal_docs/registers/credentials/general）
        project_id: 项目ID
        limit: 最大扫描行数（默认2000，防止全表扫描过大）

    Returns:
        {
            "success": True/False,
            "total": 1250,           # 扫描总行数
            "hits": 89,              # 命中行数
            "hit_rate": 0.071,       # 命中率
            "rows": [{               # 命中行详情（最多500条）
                "row_id": 5,
                "matched": true,
                "fields": {列名: 值, ...}
            }],
            "ast": {...},            # 解析后的AST（前端可视化用）
            "error": "..."           # 仅失败时
        }
    """
    # 1. 解析表达式
    try:
        ast = parse_expression(expression)
    except SyntaxError as e:
        return {"success": False, "error": f"表达式语法错误: {e}"}

    # 2. 校验表名
    allowed_tables = {
        "data_contracts", "data_finance", "data_legal_docs",
        "data_registers", "data_credentials", "data_general",
        "contracts", "finance", "legal_docs", "registers", "credentials", "general",
    }
    if table not in allowed_tables:
        return {"success": False, "error": f"不支持的表: {table}"}

    # 标准化表名（允许简写）
    if not table.startswith("data_"):
        table = f"data_{table}"

    # 3. 查询数据行
    rows = query(
        f"SELECT * FROM {table} WHERE project_id = %s LIMIT %s",
        (project_id, limit), database="tt"
    )

    # 4. 逐行求值
    hit_rows = []
    for row in rows:
        matched = _eval_ast(ast, row)
        if matched:
            # 清理 JSON 字段，控制输出大小
            clean = {}
            for k, v in row.items():
                if isinstance(v, bytes):
                    continue
                clean[k] = str(v)[:200] if v is not None and len(str(v)) > 200 else v
            hit_rows.append({
                "row_id": row.get("id"),
                "matched": True,
                "fields": clean,
            })

    total = len(rows)
    hits = len(hit_rows)

    return {
        "success": True,
        "total": total,
        "hits": hits,
        "hit_rate": round(hits / total, 4) if total > 0 else 0,
        "rows": hit_rows[:500],  # 最多返回500条详情
        "ast": ast,               # 前端渲染表达式树用
    }
