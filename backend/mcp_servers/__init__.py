"""MCP Servers 注册中心 — OpenSquilla 发现入口

每个 MCP Server 模块暴露:
  - TOOLS: dict[str, callable] — 工具名称 → 函数映射
  - SERVER_NAME: str — MCP Server 唯一标识
  - SERVER_DESCRIPTION: str — 描述文本

用法:
    from mcp_servers import list_servers, get_server_tools
    servers = list_servers()
    # → [{"name": "knowledge-mcp", "tools": {...}}, ...]
"""
from . import knowledge_mcp, vector_mcp, minio_mcp, expression_mcp

_SERVERS = [knowledge_mcp, vector_mcp, minio_mcp, expression_mcp]


def list_servers() -> list[dict]:
    """列出所有已注册的 MCP Server"""
    return [
        {
            "name": m.SERVER_NAME,
            "description": m.SERVER_DESCRIPTION,
            "tools": list(m.TOOLS.keys()),
            "tool_count": len(m.TOOLS),
        }
        for m in _SERVERS
    ]


def get_server(name: str):
    """按名称获取 MCP Server 模块"""
    for m in _SERVERS:
        if m.SERVER_NAME == name:
            return m
    return None


def get_tool(server_name: str, tool_name: str):
    """获取指定 MCP Server 的指定工具函数

    Args:
        server_name: MCP Server 名称（如 "knowledge-mcp"）
        tool_name: 工具名称（如 "search_laws"）

    Returns:
        callable 或 None
    """
    server = get_server(server_name)
    if server and tool_name in server.TOOLS:
        return server.TOOLS[tool_name]
    return None


def resolve_tool(full_name: str):
    """从 "server_name.tool_name" 格式解析并返回工具函数

    Args:
        full_name: 如 "knowledge-mcp.search_violations"

    Returns:
        callable 或 None
    """
    if "." not in full_name:
        return None
    server_name, tool_name = full_name.split(".", 1)
    return get_tool(server_name, tool_name)
