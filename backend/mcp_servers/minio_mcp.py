"""MCP Server: MinIO 文件操作 — 项目文件查询 + 溯源锚点

工具列表:
  - list_project_files(project_id)
  - get_document_trace(doc_id)
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import query, query_one


def list_project_files(project_id: str) -> dict:
    """列出项目下的所有文件和解析状态。

    Args:
        project_id: 项目ID

    Returns:
        {project_id, files: [{id, file_name, ocr_done, created_at}]}
    """
    rows = query(
        "SELECT id, file_name, minio_path, ocr_content IS NOT NULL AS ocr_done, "
        "ontosku_template, created_at "
        "FROM audit_document_traces WHERE project_id = %s ORDER BY created_at DESC LIMIT 100",
        (project_id,), database="tt",
    )
    files = []
    for r in rows:
        d = dict(r)
        d["ocr_done"] = bool(d.get("ocr_done"))
        files.append(d)
    return {"project_id": project_id, "files": files, "total": len(files)}


def get_document_trace(doc_id: int) -> dict:
    """获取文档溯源锚点信息。

    Args:
        doc_id: 文档溯源记录ID

    Returns:
        {trace: {id, file_name, minio_path, ocr_content(截断), page_number, ...}}
    """
    row = query_one(
        "SELECT id, project_id, file_name, minio_path, ocr_version, "
        "LEFT(ocr_content, 5000) AS ocr_preview, page_number, "
        "position_anchor, ontosku_template, created_at "
        "FROM audit_document_traces WHERE id = %s",
        (doc_id,), database="tt",
    )
    if not row:
        return {"error": f"溯源记录不存在: {doc_id}"}
    return {"trace": dict(row)}


TOOLS = {
    "list_project_files": list_project_files,
    "get_document_trace": get_document_trace,
}

SERVER_NAME = "minio-mcp"
SERVER_DESCRIPTION = "MinIO 文件操作 MCP Server — 项目文件列表+溯源锚点"
