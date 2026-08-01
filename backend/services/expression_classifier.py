"""Q2.2 — 表达式分级器：判断表达式属于哪一层

三层分级:
  - row:      行级表达式（仅比较+AND/OR+IN+算术+BETWEEN），现有 parser+engine 能处理
  - aggregate: 聚合表达式（含 SUM/COUNT/MAX/MIN/AVG/GROUP BY/HAVING），需 SQL 翻译
  - semantic:  语义表达式（含 SAME_*/DATE_DIFF/工作日/EXISTS 等业务函数），需 UDF
"""
import re

# 聚合函数 + SQL 聚合语法
_AGGREGATE_PATTERNS = [
    r"\bSUM\s*\(", r"\bCOUNT\s*\(", r"\bMAX\s*\(", r"\bMIN\s*\(",
    r"\bAVG\s*\(", r"\bGROUP\s+BY\b", r"\bHAVING\b",
]

# 语义函数（需要 Python UDF 实现）
_SEMANTIC_PATTERNS = [
    r"SAME_DEPT_AND_SUPPLIER_GROUP", r"SAME_CATEGORY_OR_SIMILAR",
    r"DATE_DIFF", r"CURRENT_DATE", r"工作日",
    r"\bEXISTS\b", r"NOT\s+EXISTS",
]

# 复杂语法（中文语法、时间窗、嵌套SELECT）—— 归到语义层（需LLM辅助）
_COMPLEX_PATTERNS = [
    r"\bSELECT\b.*\bFROM\b",   # 嵌套 SELECT
    r"最近\d+个?月",            # 时间窗
    r"的\s*.*\s*[>=<]",        # 中文 "的" 语法
]


def classify_expression(expression: str) -> str:
    """判断表达式属于哪一层

    Returns:
        'row' | 'aggregate' | 'semantic'
    """
    if not expression:
        return "row"

    expr = expression.strip()

    # 1. 优先判断语义层（含业务函数/复杂语法）
    for pattern in _SEMANTIC_PATTERNS:
        if re.search(pattern, expr, re.IGNORECASE):
            return "semantic"
    for pattern in _COMPLEX_PATTERNS:
        if re.search(pattern, expr):
            return "semantic"

    # 2. 判断聚合层
    for pattern in _AGGREGATE_PATTERNS:
        if re.search(pattern, expr, re.IGNORECASE):
            return "aggregate"

    # 3. 默认行级
    return "row"


def classify_batch(expressions: list[str]) -> dict[str, list[str]]:
    """批量分级

    Returns:
        {'row': [...], 'aggregate': [...], 'semantic': [...]}
    """
    result = {"row": [], "aggregate": [], "semantic": []}
    for expr in expressions:
        result[classify_expression(expr)].append(expr)
    return result
