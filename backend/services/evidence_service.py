"""证据引用服务 — 统一溯源契约（PHASE_4 §6.6 P4-7）

任何「结论」（result）与支撑它的「证据」（source）之间，统一用 audit_source_refs 记录引用。
本 Phase 只落「结构化数据→文档」类引用：
  result_type ∈ {document, data_row}，source_type ∈ {document_chunk, data_row}。
AI 结论类引用（analysis_hit/suspicion/law_recommendation）由 Phase 7/8 写，本服务不越界。

失效推导（P4-10，§3.3）：document_chunk 类 source 若其 chunk.status='superseded' →
查询时标 expired=True（证据已过期，待复核），留痕不删。
"""
import json

from services.db import insert, query


def add_ref(project_id: str, result_type: str, result_id, source_type: str, source_id,
            document_id=None, file_name=None, page_number=None, bbox=None,
            quote=None, relation="supports") -> int:
    """写一条统一证据引用，返回新行 id。

    result_id/source_id 统一转 str（audit_source_refs 列为 VARCHAR）。
    bbox 为 list/dict 时序列化为 JSON；page_number 为 int 或 None。
    """
    bbox_json = json.dumps(bbox) if bbox is not None else None
    return insert(
        "INSERT INTO audit_source_refs "
        "(project_id, result_type, result_id, source_type, source_id, "
        "document_id, file_name, page_number, bbox, quote, relation) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (project_id, result_type, str(result_id), source_type, str(source_id),
         document_id, file_name, page_number, bbox_json, quote, relation),
        database="tt",
    )


def get_refs(result_type: str, result_id) -> list:
    """按 result 查全部证据引用，并为 document_chunk 类 source 推导过期标记（P4-10）。

    返回 [{id,project_id,result_type,result_id,source_type,source_id,document_id,
           file_name,page_number,bbox,quote,relation,created_at,expired}]。
    expired=True 表示该证据引用的 chunk 已 superseded（待复核，留痕不删）。
    """
    rows = query(
        "SELECT id, project_id, result_type, result_id, source_type, source_id, "
        "document_id, file_name, page_number, bbox, quote, relation, created_at "
        "FROM audit_source_refs "
        "WHERE result_type=%s AND result_id=%s ORDER BY id",
        (result_type, str(result_id)), database="tt",
    )

    # 为 document_chunk 类 source 批量查 chunk.status → 推导 expired
    chunk_ids = [r["source_id"] for r in rows
                 if r.get("source_type") == "document_chunk" and r.get("source_id")]
    status_map = {}
    if chunk_ids:
        ph = ",".join(["%s"] * len(chunk_ids))
        cs = query(
            f"SELECT id, status FROM audit_document_chunks WHERE id IN ({ph})",
            tuple(chunk_ids), database="tt",
        )
        status_map = {str(c["id"]): c["status"] for c in cs}

    for r in rows:
        r["expired"] = (
            r.get("source_type") == "document_chunk"
            and status_map.get(str(r.get("source_id"))) == "superseded"
        )
    return list(rows)


def link_data_row_to_document(project_id: str, table_name: str, row_id: int,
                              trace_id: int, chunk_id=None, quote=None,
                              page_number=None) -> int:
    """落「数据行→文档 chunk」引用（P4-6/P4-7 衔接）。

    result=data_row(row_id) ← source=document_chunk(chunk_id) 或 data_row(row_id)，
    document_id 指向 trace（行→trace→文档链路）。table_name 用于上下文记录（结果侧
    table_name 由 audit_field_sources 持有，ref 侧靠 result_id + 查询时 ?table= 限定）。
    返回新 ref id。
    """
    if chunk_id:
        source_type, source_id = "document_chunk", chunk_id
    else:
        # 无具体 chunk 时退化为「行→文档」级引用（source 指回行自身，document_id 锚 trace）
        source_type, source_id = "data_row", row_id
    return add_ref(
        project_id=project_id,
        result_type="data_row",
        result_id=row_id,
        source_type=source_type,
        source_id=source_id,
        document_id=trace_id,
        quote=quote,
        page_number=page_number,
    )


# ── Phase8 契约层扩展（P8-6/P8-8）──────────────────────────────────
# 上半部分（add_ref/get_refs/link_data_row_to_document）落 document/data_row 类引用；
# 以下两法补 P8-6（命中行 field_sources→chunk 证据链）与 P8-8（已确认疑点证据装配），
# 复用上半的 get_refs 取疑点 source_refs，不越界改写既有逻辑。

def _loads_json(v):
    """安全解析 JSON 列（DictCursor 下 JSON 列可能返回 str 或已解析对象）。"""
    if v is None:
        return None
    if isinstance(v, (dict, list)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def build_field_sources_evidence(project_id: str, table_name: str, row_id: int) -> list:
    """为某命中数据行装配字段来源证据链（audit_field_sources → audit_document_chunks）。

    P8-6 Step5 命中行 evidence.field_sources→chunk：逐字段连到原文切片，
    让每个比对命中可溯源到文档页码/坐标/原文片段。

    Returns:
        [{field_name, chunk_id, page_nums, bbox, text, file_name, ocr_version}]
    """
    if not project_id or not table_name or row_id is None:
        return []
    rows = query(
        "SELECT fs.field_name, fs.chunk_id, fs.ocr_version, "
        "       dc.text, dc.page_nums, dc.bbox, dc.trace_id, "
        "       dt.file_name "
        "FROM audit_field_sources fs "
        "LEFT JOIN audit_document_chunks dc ON dc.id = fs.chunk_id "
        "LEFT JOIN audit_document_traces dt ON dt.id = dc.trace_id "
        "WHERE fs.project_id = %s AND fs.table_name = %s AND fs.row_id = %s",
        (project_id, table_name, row_id), database="tt",
    )
    return [{
        "field_name": r.get("field_name"),
        "chunk_id": r.get("chunk_id"),
        "page_nums": _loads_json(r.get("page_nums")),
        "bbox": _loads_json(r.get("bbox")),
        "text": (r.get("text") or "")[:500],  # 截断，避免长正文撑爆响应
        "file_name": r.get("file_name"),
        "ocr_version": r.get("ocr_version"),
    } for r in rows]


def get_confirmed_suspicion_evidence(project_id: str, analysis_id=None) -> list:
    """取已确认疑点（verify_status=CONFIRMED）的证据链（P8-8 文书前置 evidence_complete）。

    Returns:
        [{id, violation_id, suspicion_items, evidence_chain, verify_status, status, source_refs}]
        verify_status/status 透传，供下游 _build_report_template 复核 CONFIRMED（不二次查库）。
    """
    sql = ("SELECT id, violation_id, suspicion_items, evidence_chain, verify_status, status "
           "FROM project_suspicions WHERE project_id = %s AND verify_status = 'CONFIRMED'")
    params = [project_id]
    if analysis_id:
        sql += " AND analysis_id = %s"; params.append(analysis_id)
    rows = query(sql, tuple(params), database="tt")
    return [{
        "id": r["id"],
        "violation_id": r.get("violation_id"),
        "suspicion_items": _loads_json(r.get("suspicion_items")),
        "evidence_chain": _loads_json(r.get("evidence_chain")),
        "verify_status": r.get("verify_status"),
        "status": r.get("status"),
        "source_refs": get_refs("suspicion", r["id"]),
    } for r in rows]

