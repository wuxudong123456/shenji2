"""MCP Server: 违规表达式引擎 — 伪SQL解析+逐行扫描

工具列表:
  - execute_expression(expression, table, project_id)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.expression_engine import execute_expression as _execute


def execute_expression(expression: str, table: str = "data_contracts",
                       project_id: str = "") -> dict:
    """对数据工坊表执行违规表达式扫描。

    将伪SQL表达式解析为AST，对目标表的每一行求值，
    返回命中/未命中统计和详情。

    Args:
        expression: 伪SQL表达式（如 '采购方式="询价" AND 金额>1000000'）
        table: 目标数据表（data_contracts/finance/legal_docs/registers/credentials/general）
        project_id: 项目ID

    Returns:
        {success, total, hits, hit_rate, rows: [{row_id, matched, fields}], ast}
    """
    return _execute(expression, table, project_id)


TOOLS = {
    "execute_expression": execute_expression,
}

SERVER_NAME = "expression-mcp"
SERVER_DESCRIPTION = "违规表达式引擎 MCP Server — 伪SQL→AST→逐行扫描"
