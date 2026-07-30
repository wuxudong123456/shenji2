"""MCP Server: FAISS 语义搜索 — 法规 + 违规向量检索

工具列表:
  - semantic_search_laws(query, top_k)
  - semantic_search_violations(query, top_k)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.vector_store import get_vector_store


def semantic_search_laws(query: str, top_k: int = 10) -> dict:
    """法规语义搜索（FAISS 向量检索）。

    与关键词搜索互补：适合用自然语言描述搜索意图的场景。
    例如: "招标过程中如何防止围标串标" → 返回相关法规

    Args:
        query: 自然语言搜索词
        top_k: 返回结果数量

    Returns:
        {query, results: [{id, title, potency_level, similarity}]}
    """
    store = get_vector_store()
    results = store.search_laws(query, top_k=top_k)
    return {"query": query, "results": results, "total": len(results)}


def semantic_search_violations(query: str, top_k: int = 10) -> dict:
    """违规行为语义搜索（FAISS 向量检索）。

    用自然语言描述审计发现，返回最匹配的违规模型。
    例如: "把一个大项目拆成几个小项目分别招标" → "化整为零规避公开招标"

    Args:
        query: 自然语言描述
        top_k: 返回结果数量

    Returns:
        {query, results: [{id, violation_title, severity, similarity}]}
    """
    store = get_vector_store()
    results = store.search_violations(query, top_k=top_k)
    return {"query": query, "results": results, "total": len(results)}


TOOLS = {
    "semantic_search_laws": semantic_search_laws,
    "semantic_search_violations": semantic_search_violations,
}

SERVER_NAME = "vector-mcp"
SERVER_DESCRIPTION = "FAISS 语义搜索 MCP Server — 法规+违规向量检索"
