"""MCP Server: 审计知识库 — 法规检索 + 违规查询 + 法规关系图

暴露为 OpenSquilla MCP Server，供 Agent 运行时通过 function-calling 调用。

工具列表:
  - search_laws(query, potency_level, timeliness, limit)
  - get_law_detail(law_id)
  - get_regulation_graph(law_id)
  - search_violations(query, severity, limit)
  - get_violation_detail(violation_id)
"""
import sys
from pathlib import Path

# 确保 backend/ 在路径中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.knowledge_service import (
    search_laws as _search_laws,
    count_laws as _count_laws,
    get_law_detail as _get_law_detail,
    search_violations as _search_violations,
    count_violations as _count_violations,
    get_violation_detail as _get_violation_detail,
)
from services.regulation_graph import get_regulation_graph as _get_regulation_graph


def search_laws(query: str = "", potency_level: str = None,
                timeliness: str = None, limit: int = 20) -> dict:
    """法规全文检索。按关键词搜索法规库，支持效力级别和时效性筛选。

    Args:
        query: 搜索关键词（匹配法规标题和正文）
        potency_level: 效力级别（法律/行政法规/部门规章/地方法规/司法解释等）
        timeliness: 时效性（现行有效/失效/已修改等）
        limit: 返回结果数量上限

    Returns:
        {total, laws: [{id, title, potency_level, timeliness, snippet}]}
    """
    results = _search_laws(query, potency_level=potency_level,
                           timeliness=timeliness, limit=limit)
    total = _count_laws(query, potency_level=potency_level, timeliness=timeliness)
    return {"total": total, "laws": results}


def get_law_detail(law_id: str) -> dict:
    """获取法规全文详情，包含标题、正文、效力级别、时效性等。

    Args:
        law_id: 法规ID（sys_core_law_allaudit.id）

    Returns:
        法规详情字典，或 {error: "..."}
    """
    result = _get_law_detail(law_id)
    if not result:
        return {"error": f"法规不存在: {law_id}"}
    return result


def get_regulation_graph(law_id: str) -> dict:
    """获取法规完整关系图：上位法链、下位法、相关法、历史版本。

    Args:
        law_id: 中心法规ID

    Returns:
        {center, superior_chain, inferior, related, history_versions, total_relations}
    """
    return _get_regulation_graph(law_id)


def search_violations(query: str = "", severity: str = None, limit: int = 20) -> dict:
    """违规行为检索。按关键词搜索违规模型库。

    Args:
        query: 搜索关键词（匹配违规名称和描述）
        severity: 严重程度（high/medium/low）
        limit: 返回结果数量上限

    Returns:
        {total, violations: [{id, violation_title, severity, expression_text, description}]}
    """
    results = _search_violations(query, severity=severity, limit=limit)
    total = _count_violations(query, severity=severity)
    return {"total": total, "violations": results}


def get_violation_detail(violation_id: int) -> dict:
    """获取违规行为详情，包含违规表达式、描述、关联法规等。

    Args:
        violation_id: 违规行为ID（audit_violations.id）

    Returns:
        违规详情字典，或 {error: "..."}
    """
    result = _get_violation_detail(violation_id)
    if not result:
        return {"error": f"违规行为不存在: {violation_id}"}
    return result


# ── MCP Server 注册入口（OpenSquilla 发现） ──
# 工具清单: 名称 → 函数映射
TOOLS = {
    "search_laws": search_laws,
    "get_law_detail": get_law_detail,
    "get_regulation_graph": get_regulation_graph,
    "search_violations": search_violations,
    "get_violation_detail": get_violation_detail,
}

SERVER_NAME = "knowledge-mcp"
SERVER_DESCRIPTION = "审计知识库 MCP Server — 法规检索、违规查询、法规关系图"
