"""Phase 5 — 后台任务异步 Worker

基于 ThreadPoolExecutor 的轻量级异步任务执行引擎。
所有耗时操作（OCR / LLM提取 / AI分析 / 导出）在此异步执行。

架构:
  POST /api/audit/tasks → 创建任务 → 提交到线程池 → 立即返回
  Worker 线程 → 更新进度 → 执行 → success/fail → 写入结果
  前端轮询 GET /api/audit/tasks/<id> → 获取实时状态
"""
import json
import io
import tempfile
import os
import traceback
from concurrent.futures import ThreadPoolExecutor, Future
from datetime import datetime

from services.task_manager import (
    start_task, update_progress, complete_task, fail_task, get_task,
)

# 全局线程池（最多5个并发任务）
_executor = ThreadPoolExecutor(max_workers=5, thread_name_prefix="audit-task-")
_active_futures: dict[int, Future] = {}


def submit_task(task_id: int):
    """提交任务到后台线程池异步执行

    根据 task_type 分派到不同的处理函数：
      ocr       → _run_ocr_task
      extract   → _run_extract_task
      analysis  → _run_analysis_task
      export    → _run_export_task
      archive   → _run_archive_task
    """
    task = get_task(task_id)
    if not task or not task.get("success"):
        return

    task_data = task["task"]
    task_type = task_data["task_type"]

    handlers = {
        "ocr": _run_ocr_task,
        "extract": _run_extract_task,
        "analysis": _run_analysis_task,
        "export": _run_export_task,
        "archive": _run_archive_task,
    }

    handler = handlers.get(task_type)
    if not handler:
        fail_task(task_id, f"不支持的任务类型: {task_type}")
        return

    future = _executor.submit(_safe_run, task_id, handler, task_data)
    _active_futures[task_id] = future


def _safe_run(task_id: int, handler, task_data: dict):
    """包裹执行：捕获所有异常，确保不会静默失败"""
    try:
        handler(task_id, task_data)
    except Exception as e:
        trace = traceback.format_exc()
        fail_task(task_id, f"{e}\n{trace[-500:]}")
    finally:
        _active_futures.pop(task_id, None)


def cancel_running_task(task_id: int) -> bool:
    """取消正在运行的任务"""
    future = _active_futures.get(task_id)
    if future and not future.done():
        return future.cancel()
    return False


# ── 任务处理函数 ──


def _set_trace_parse_status(trace_id, status, parsed_at=False):
    """同步 trace.parse_status（P3-5 状态机）。

    pending(建 trace, P3-1) → running(worker 取走) → done(完成, P3-3) / failed(终态)。
    parsed_at=True 时一并写 parsed_at=NOW()（done 用）。
    """
    if not trace_id:
        return
    from services.db import execute
    if parsed_at:
        execute(
            "UPDATE audit_document_traces SET parse_status=%s, parsed_at=NOW() WHERE id=%s",
            (status, trace_id), database="tt",
        )
    else:
        execute(
            "UPDATE audit_document_traces SET parse_status=%s WHERE id=%s",
            (status, trace_id), database="tt",
        )


def _fail_with_trace(task_id, trace_id, error_msg):
    """fail_task + 终态时同步 trace.parse_status='failed'（P3-5）。

    fail_task 返回 True=已回 pending 待重试（trace 保持 running，用户视角仍在跑），
    False=重试耗尽终态 failed。trace_id 无效时只 fail_task（不写 trace）。
    """
    retried = fail_task(task_id, error_msg)
    if not retried and trace_id:
        _set_trace_parse_status(trace_id, "failed")


def _run_ocr_task(task_id: int, task_data: dict):
    """OCR + 字段提取任务（Q1.2 → P3 加固：三档选路唯一入口 + 字段映射入表）

    【唯一选路点 §3.1】OntoSKU 主 → LiteParse 降级 → 本地 LLM 兜底，
        全程写 trace.parse_engine ∈ {ontosku, liteparse, local-llm}；
        OCREngine.get_engine() 不再承担选路（P3-12，仅取客户端）。

    流程:
      1. 从 task.payload 读 trace_id/minio 信息（P3-2，payload 优先 result 兜底兼容在途任务）
      2. trace.parse_status→running（P3-5）；从 MinIO 下载文件
      3. OntoSKU 提取（含 sku_profile 匹配）；失败→_fallback_local_extract 三档降级（P3-4）
      4. 字段映射：中文字段 → data_xxx 表英文列（field_mapper）
      5. 落库（P3-3/5done/7/9 合并 UPDATE：external_document_id/external_job_id/parse_engine/
         parse_status='done'/parsed_at/ontosku_template 真值）+ 写 data_*(doc_type)
      失败：_fail_with_trace → fail_task 退避重试（P3-10）；终态→trace.parse_status=failed（P3-5）
    """
    start_task(task_id)
    update_progress(task_id, 10)

    # 1. 从 task.payload 读入参（P3-2：payload 优先，result 过渡兜底兼容在途任务）
    task_payload = task_data.get("payload") or task_data.get("result") or {}
    if isinstance(task_payload, str):
        try:
            task_payload = json.loads(task_payload)
        except json.JSONDecodeError:
            task_payload = {}
    trace_id = task_payload.get("trace_id")
    minio_bucket = task_payload.get("minio_bucket")
    minio_path = task_payload.get("minio_path")
    project_id = task_payload.get("project_id") or task_data.get("project_id", "")
    filename = task_payload.get("filename") or task_data.get("task_name", "")

    if not trace_id:
        fail_task(task_id, "task 缺少 trace_id（payload.trace_id）")
        return

    from services.db import query_one, execute, insert
    trace = query_one(
        "SELECT * FROM audit_document_traces WHERE id = %s",
        (trace_id,), database="tt",
    )
    if not trace:
        fail_task(task_id, f"溯源记录不存在: {trace_id}")
        return

    # P3-5：trace 加载成功 → parse_status='running'
    _set_trace_parse_status(trace_id, "running")
    update_progress(task_id, 20)

    # 2. 从 MinIO 下载文件（从 task_payload 取项目 bucket，不能用默认 bucket）
    path_to_fetch = minio_path or trace.get("minio_path")
    try:
        from services.minio_client import download_file
        file_bytes = download_file(path_to_fetch, bucket=minio_bucket)
    except Exception as e:
        _fail_with_trace(task_id, trace_id, f"从MinIO下载失败(bucket={minio_bucket}): {e}")
        return

    update_progress(task_id, 30)

    # 3. 调 OntoSKU 提取（双引擎：OntoSKU 优先，失败降级本地 LLM）
    ocr_result = None
    extract_engine = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=_guess_suffix(filename))
        tmp.write(file_bytes)
        tmp.close()
        try:
            # 首选：OntoSKU（带 sku_profile，由分类预判或留空让服务端自动匹配）
            from services.ontosku_client import get_client as get_ontosku
            from services.ontosku_client import OntoSKUError
            sku_profile = task_payload.get("sku_profile")  # 可由前端指定
            try:
                ontosku_result = get_ontosku().extract(tmp.name, sku_profile=sku_profile)
                ocr_result = {
                    "success": True,
                    "engine": "ontosku",
                    "text": ontosku_result.get("markdown", ""),
                    "fields": ontosku_result.get("fields", {}),
                    "document_id": ontosku_result.get("document_id", ""),
                    "job_id": ontosku_result.get("job_id", ""),  # P3-3
                    "chunks": ontosku_result.get("chunks", []),
                    "template_name": ontosku_result.get("template_name", ""),  # P3-7
                    "doc_type": ontosku_result.get("doc_type", ""),  # P3-7
                }
                extract_engine = "ontosku"
            except OntoSKUError as oe:
                # OntoSKU 失败 → 三档降级（P3-4 §6.4）：LiteParse → LLM 兜底
                update_progress(task_id, 40)
                ocr_result = _fallback_local_extract(tmp.name, trace.get("ocr_content") or "")
                extract_engine = ocr_result.get("engine", "local-llm")  # liteparse 或 local-llm
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
    except Exception as e:
        _fail_with_trace(task_id, trace_id, f"提取阶段异常: {e}")
        return

    if not ocr_result or not ocr_result.get("success"):
        _fail_with_trace(task_id, trace_id, f"提取失败: {ocr_result.get('error', '未知') if ocr_result else '无结果'}")
        return

    update_progress(task_id, 70)

    # 4. 文档分类（确定写入哪张 data_xxx 表）
    ocr_text = ocr_result.get("text", "") or ocr_result.get("markdown", "")
    fields = ocr_result.get("fields", {}) or {}

    # 补抽：从 OCR 文本 regex 捞 LLM 可能漏掉的关键审计字段（采购方式/金额/合同号/供应商/日期）
    from services.field_mapper import enrich_fields_from_text
    fields = enrich_fields_from_text(fields, ocr_text)

    category = _classify_for_table(ocr_text, filename)
    table_name = _map_category_to_table(category)

    # P3-7/9：ontosku_template（命中的 audit/* 模板，仅 OntoSKU 路径有）+ doc_type
    #   doc_type 决策2：OntoSKU document_type 优先，无则分类 category 兜底，皆无 NULL
    template_name = ocr_result.get("template_name") or None
    doc_type = ocr_result.get("doc_type") or category or None

    # 5. 字段映射：中文字段 → 表英文列
    from services.field_mapper import map_extracted_fields
    row_dict, extra_fields = map_extracted_fields(table_name, fields)

    # 溯源 chunks（JSON 序列化）
    chunks_json = json.dumps(ocr_result.get("chunks", []),
                             ensure_ascii=False) if ocr_result.get("chunks") else None

    # 6. 更新溯源记录（P3-3/5done/7 合并落库）
    #   external_document_id/external_job_id/parse_engine（P3-3）
    #   parse_status='done' + parsed_at（P3-5 done）
    #   ontosku_template=真值 template_name（P3-7，修原 extract_engine 误塞语义 bug）
    execute(
        "UPDATE audit_document_traces SET "
        "ocr_content = %s, ocr_version = ocr_version + 1, "
        "external_document_id = %s, external_job_id = %s, "
        "parse_engine = %s, parse_status = 'done', parsed_at = NOW(), "
        "ontosku_template = %s, extracted_fields = %s, position_anchor = %s "
        "WHERE id = %s",
        (ocr_text[:50000],
         ocr_result.get("document_id") or None,
         ocr_result.get("job_id") or None,
         extract_engine,
         template_name,
         json.dumps({"mapped": row_dict, "extra": extra_fields}, ensure_ascii=False),
         chunks_json, trace_id),
        database="tt",
    )

    # P4-3/P4-4/P4-10：chunks 逐条落 audit_document_chunks（双写 position_anchor 兼容；
    # 废旧旧版本；返回 chunks_db 供 P4-5 字段匹配）
    chunks_db = _persist_chunks(trace_id, project_id, ocr_result.get("chunks", []), extract_engine)

    update_progress(task_id, 85)

    # 7. 写入数据工坊表（P3-9：传 doc_type）
    row_id = _insert_into_data_table(
        table_name, project_id, trace_id, filename, ocr_text,
        row_dict, extra_fields, task_payload.get("template_name"),
        doc_type,
    )

    # P4-5/P4-6：字段→chunk 文本匹配溯源 + 行→trace 证据引用
    if row_id:
        _build_field_sources(trace_id, project_id, table_name, row_id,
                             row_dict, extra_fields, chunks_db)

    update_progress(task_id, 100)
    complete_task(task_id, {
        "trace_id": trace_id,
        "table": table_name,
        "row_id": row_id,
        "engine": extract_engine,
        "fields_mapped": len(row_dict),
        "fields_extra": len(extra_fields),
        "text_length": len(ocr_text),
        "chunks_count": len(chunks_db),
    })


def _fallback_local_extract(file_path: str, existing_ocr: str = "") -> dict:
    r"""OntoSKU 失败后的降级（P3-4 §6.4 三档后两档）：LiteParse → 本地 LLM 兜底。

    OntoSKU 已在调用方失败，本函数负责：
      ① LiteParse OCR 成功且文本非空（len(strip)≥10）→ engine='liteparse'
      ② LiteParse 失败或空文本（扫描件，<10）→ 本地 LLM 兜底抽取 → engine='local-llm'（决策 10）

    字段提取统一走 LLM（auto_classify_and_extract），引擎标签反映【文本来源】档位；
    三档产物归一化为 {text/markdown, fields} 后走同一落库路径（§6.4）。

    注意：必须显式用 LiteParseClient，不能用 OCREngine.parse()——
    OCREngine 受 OCR_ENGINE=mineru 配置控制会回调 OntoSKU（主路径已失败），形成死循环。
    """
    lp_text = ""
    engine = "local-llm"  # 默认兜底档；LiteParse 命中实质文本时升级为 liteparse

    # ① LiteParse 档
    try:
        from services.ocr_client import LiteParseClient
        ocr = LiteParseClient().parse(file_path)
        if ocr.get("success"):
            lp_text = ocr.get("text") or ocr.get("markdown") or ""
            if len(lp_text.strip()) >= 10:  # 实质文本（扫描件常为空白/残渣）→ liteparse 档
                engine = "liteparse"
    except Exception:
        pass  # LiteParse 不可用 → 落 LLM 兜底档

    # ② LLM 兜底档：LiteParse 失败/空文本时，在现有文本上尽力抽取
    #   liteparse 档用 LiteParse 实质文本；local-llm 档：LiteParse 有非空白残渣则用之，
    #   否则（纯空白/扫描件）回落历史 ocr_content
    if engine == "liteparse":
        text = lp_text
    else:
        text = lp_text if lp_text.strip() else existing_ocr
    try:
        from services.extraction_service import auto_classify_and_extract
        extract = auto_classify_and_extract(text)
        fields = {f["name"]: f["value"] for f in extract.get("fields", [])} \
            if extract.get("success") else {}
    except Exception as e:
        return {"success": False, "engine": engine, "error": f"LLM兜底抽取失败: {e}"}

    return {
        "success": True,
        "engine": engine,
        "text": text,
        "fields": fields,
        "document_id": "",
        "chunks": [],
    }


def _normalize_chunks(raw_chunks, engine: str) -> list:
    """P4-2 — OntoSKU chunks 归一化为统一最小字段（纯函数，无副作用）

    把各引擎的 chunks 统一为 {chunk_id, chunk_type, page_nums, bbox, text, section_path}，
    供 P4-3 逐条落 audit_document_chunks、P4-5 文本匹配 chunk。

    - **防御性兼容键名**（K2 §4 chunks.json 真实结构待校准）：
      chunk_id←chunk_id/id；chunk_type←type/chunk_type；page_nums←page_nums/pages/page；
      bbox←bbox/bounding_box/coords；text←text/content/markdown；section_path←section_path/section/heading。
    - **降级路径不伪造（§3.4）**：engine∈{liteparse,local-llm}（无可靠页码/坐标）或输入空 → 返回 []。
      （_fallback_local_extract 本就返回 chunks:[]，此处 engine 守卫为二次防御。）
    """
    if not raw_chunks or engine in ("liteparse", "local-llm"):
        return []
    if not isinstance(raw_chunks, list):
        return []

    normed = []
    for idx, raw in enumerate(raw_chunks):
        if not isinstance(raw, dict):
            continue
        normed.append({
            "chunk_id": _first_key(raw, ("chunk_id", "id")) or f"chunk-{idx}",
            "chunk_type": _first_key(raw, ("type", "chunk_type")) or "text",
            "page_nums": _coerce_page_nums(
                _first_key(raw, ("page_nums", "pages", "page", "page_number"))),
            "bbox": _coerce_bbox(
                _first_key(raw, ("bbox", "bounding_box", "coords", "box"))),
            "text": _first_key(raw, ("text", "content", "markdown")) or "",
            "section_path": _first_key(raw, ("section_path", "section", "heading")),
        })
    return normed


def _first_key(d: dict, keys) :
    """取 dict 中第一个存在的键值（键名兼容用）。"""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _coerce_page_nums(val) -> list:
    """页码归一为 list[int]：int→[int]；list→逐项 int；"3-5"→[3,4,5]；其它→[]（不伪造）。"""
    if val is None:
        return []
    if isinstance(val, (list, tuple)):
        out = []
        for v in val:
            try:
                out.append(int(v))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(val, int):
        return [val]
    if isinstance(val, str):
        s = val.strip()
        if "-" in s and s.count("-") == 1:
            lo, hi = s.split("-", 1)
            try:
                return list(range(int(lo), int(hi) + 1))
            except ValueError:
                pass
        try:
            return [int(s)]
        except ValueError:
            return []
    return []


def _coerce_bbox(val):
    """坐标归一为 [x0,y0,x1,y1]（数字四元组）或 None（无法解析不伪造）。"""
    if val is None:
        return None
    if isinstance(val, (list, tuple)) and len(val) == 4:
        try:
            return [float(val[0]), float(val[1]), float(val[2]), float(val[3])]
        except (TypeError, ValueError):
            return None
    if isinstance(val, dict):
        # 兼容 {x0,y0,x1,y1} 与 {left,top,right,bottom} 与 {x,y,w,h}
        try:
            if "x0" in val:
                return [float(val["x0"]), float(val["y0"]),
                        float(val["x1"]), float(val["y1"])]
            if "left" in val:
                return [float(val["left"]), float(val["top"]),
                        float(val["right"]), float(val["bottom"])]
            if {"x", "y", "w", "h"} <= set(val):
                x, y, w, h = float(val["x"]), float(val["y"]), float(val["w"]), float(val["h"])
                return [x, y, x + w, y + h]
        except (TypeError, ValueError, KeyError):
            return None
    return None


def _persist_chunks(trace_id: int, project_id: str, raw_chunks, engine: str) -> list:
    """P4-3/P4-4/P4-10 — chunks 逐条落 audit_document_chunks（在 trace UPDATE 之后调用）

    - **P4-10 废旧**：先 UPDATE status='superseded' WHERE trace_id AND status='active'
      （幂等：首次解析 no-op；reparse 自动废旧旧版本，留痕不删，§3.3）。
    - 归一化（_normalize_chunks）→ 取当前 ocr_version（UPDATE 已 +1）→ 逐条 INSERT active 行，
      page_nums/bbox 为 JSON 或 NULL（空/降级不伪造，§3.4）。
    - **双写**：trace.position_anchor 仍由调用方 UPDATE 写（现状前端在读），本函数只追加新表行。
    - 返回插入的 chunk 行 [{id,text,page_nums,bbox}]，供 P4-5 字段文本匹配。
    降级/空 chunks → 返回 []（不插行）。
    """
    from services.db import execute, insert, query_one

    # P4-10：废旧旧版本（幂等）
    execute(
        "UPDATE audit_document_chunks SET status='superseded' "
        "WHERE trace_id=%s AND status='active'",
        (trace_id,), database="tt",
    )

    normed = _normalize_chunks(raw_chunks, engine)
    if not normed:
        return []

    ver_row = query_one(
        "SELECT ocr_version FROM audit_document_traces WHERE id=%s",
        (trace_id,), database="tt",
    )
    ocr_version = (ver_row or {}).get("ocr_version") or 1

    chunks_db = []
    for c in normed:
        page_nums_json = json.dumps(c["page_nums"]) if c["page_nums"] else None
        bbox_json = json.dumps(c["bbox"]) if c["bbox"] else None
        cid = insert(
            "INSERT INTO audit_document_chunks "
            "(trace_id, project_id, chunk_id, chunk_type, page_nums, bbox, "
            "text, section_path, ocr_version, status) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'active')",
            (trace_id, project_id, c["chunk_id"], c["chunk_type"],
             page_nums_json, bbox_json, c["text"], c["section_path"], ocr_version),
            database="tt",
        )
        chunks_db.append({
            "id": cid, "text": c["text"],
            "page_nums": c["page_nums"], "bbox": c["bbox"],
        })
    return chunks_db


def _match_field_chunk(value, chunks_db):
    """P4-5 文本匹配兜底（决策1）：字段值→str→在 chunks_db 文本里找包含，首个命中→chunk_id。

    OntoSKU 字段级溯源结构待 K2 校准，本 Phase 用文本包含兜底；精度有限（短值易误命中），
    命中不到返回 None（chunk_id=NULL，标待人工核实）。chunks_db 为空（降级）→ 恒 None。
    """
    if value is None or not chunks_db:
        return None
    s = str(value).strip()
    if not s:
        return None
    for c in chunks_db:
        if s in (c.get("text") or ""):
            return c["id"]
    return None


def _build_field_sources(trace_id, project_id, table_name, row_id,
                         row_dict, extra_fields, chunks_db):
    """P4-5/P4-6 — 字段→chunk 溯源 + 行→trace 证据引用（_insert_into_data_table 之后调用）

    - audit_field_sources：每个非空字段建一行
        · row_dict key → field_name=列名
        · extra_fields key → field_name='extra_fields->$.字段名'（K5 覆盖未映射字段）
      chunk_id 由 _match_field_chunk 文本匹配（决策1）；命中不到 NULL。
    - P4-6：调 EvidenceService.link_data_row_to_document 落 data_row→trace 引用；
      trace_id 缺失打 warning 不静默。
    降级 chunks_db=[] → 全部 chunk_id=NULL（不伪造）。
    """
    from services.db import insert

    ocr_version = None
    ver_row = None
    if trace_id:
        from services.db import query_one
        ver_row = query_one("SELECT ocr_version FROM audit_document_traces WHERE id=%s",
                            (trace_id,), database="tt")
    ocr_version = (ver_row or {}).get("ocr_version")

    sources = []
    # ① row_dict：列名
    for col, val in (row_dict or {}).items():
        if col in ("project_id", "document_trace_id", "template_name", "doc_name", "doc_type"):
            continue
        if val in (None, "", 0):
            # 0 视为无有效值跳过（金额 0 多为占位）；非空字符串/数字才建来源
            continue
        sources.append((col, _match_field_chunk(val, chunks_db)))
    # ② extra_fields：extra_fields->$.字段名（K5）
    for fname, val in (extra_fields or {}).items():
        if val in (None, ""):
            continue
        field_name = "extra_fields->$.%s" % fname
        sources.append((field_name, _match_field_chunk(val, chunks_db)))

    for field_name, chunk_id in sources:
        insert(
            "INSERT INTO audit_field_sources "
            "(project_id, table_name, row_id, field_name, chunk_id, ocr_version) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (project_id, table_name, row_id, field_name, chunk_id, ocr_version),
            database="tt",
        )

    # P4-6：行→trace 证据引用（document_id 锚 trace，建立 row→trace→document 链路）
    if trace_id:
        from services.evidence_service import link_data_row_to_document
        link_data_row_to_document(project_id, table_name, row_id, trace_id)
    else:
        print(f"[warn] P4-6 data 行 {table_name}.{row_id} 缺 trace_id，未建行→文档引用")


def _classify_for_table(ocr_text: str, filename: str) -> str:
    """轻量分类（决定写入哪张 data_xxx 表）

    优先用文件名/内容关键词快速判断，避免每次都调 LLM。
    """
    text = (filename + " " + ocr_text[:2000])
    KEYWORD_CATEGORY = [
        ("合同", "合同协议类"), ("采购", "合同协议类"), ("招标", "合同协议类"),
        ("发票", "财务票据类"), ("凭证", "财务凭证类"), ("账簿", "财务账簿类"),
        ("银行", "财务凭证类"), ("流水", "财务凭证类"),
        ("判决", "法律文书类"), ("裁定", "法律文书类"), ("处罚", "法律文书类"),
        ("登记", "登记台账类"), ("台账", "登记台账类"), ("名册", "登记台账类"),
        ("执照", "资质证照类"), ("资质", "资质证照类"), ("证书", "资质证照类"),
        ("许可证", "资质证照类"),
    ]
    for kw, cat in KEYWORD_CATEGORY:
        if kw in text:
            return cat
    return "其他杂项类"  # → data_general


def _insert_into_data_table(table_name: str, project_id: str, trace_id: int,
                            filename: str, ocr_text: str,
                            row_dict: dict, extra_fields: dict,
                            template_name: str = None,
                            doc_type: str = None) -> int:
    """把映射后的字段写入对应的 data_xxx 表（P3-9 补 doc_type 列）"""
    from services.db import insert
    extra_json = json.dumps(extra_fields, ensure_ascii=False) if extra_fields else None
    tmpl = template_name or row_dict.get("template_name") or ""

    # 公共列（doc_type P3-9 新增；六表均有该列 schema.sql:133/160/186/210/233/257）
    cols = ["project_id", "document_trace_id", "template_name", "doc_name", "doc_type",
            "extra_fields", "raw_text"]
    vals = [project_id, trace_id, tmpl, filename, doc_type, extra_json, ocr_text[:10000]]

    # 动态追加映射到的列（只 INSERT 有值的列）
    for col, val in row_dict.items():
        if col in ("project_id", "document_trace_id", "template_name", "doc_name", "doc_type"):
            continue  # 已在公共列
        cols.append(col)
        vals.append(val)

    placeholders = ",".join(["%s"] * len(cols))
    col_names = ",".join(cols)
    return insert(
        f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})",
        tuple(vals), database="tt",
    )


def _guess_suffix(filename: str) -> str:
    """根据文件名推断临时文件后缀"""
    import os as _os
    _, ext = _os.path.splitext(filename or "")
    return ext or ".pdf"


# 保留旧的 _run_extract_task 作为独立任务（前端手动触发重新提取时用）
def _run_extract_task(task_id: int, task_data: dict):
    """独立提取任务：对已 OCR 的文档重新提取（切换模板时用）"""
    start_task(task_id)
    update_progress(task_id, 10)

    task_payload = task_data.get("result") or {}
    if isinstance(task_payload, str):
        try:
            task_payload = json.loads(task_payload)
        except json.JSONDecodeError:
            task_payload = {}
    trace_id = task_payload.get("trace_id")
    project_id = task_payload.get("project_id") or task_data.get("project_id", "")
    template_name = task_payload.get("template_name")

    from services.db import query_one
    trace = query_one(
        "SELECT * FROM audit_document_traces WHERE id = %s",
        (trace_id,), database="tt",
    ) if trace_id else None

    if not trace or not trace.get("ocr_content"):
        fail_task(task_id, "无OCR内容可提取，请先完成文件解析")
        return

    update_progress(task_id, 40)

    # 用本地 LLM 重新提取
    from services.extraction_service import extract_fields, auto_classify_and_extract
    try:
        if template_name:
            extract_result = extract_fields(template_name, trace["ocr_content"])
        else:
            extract_result = auto_classify_and_extract(trace["ocr_content"])
    except Exception as e:
        fail_task(task_id, f"字段提取失败: {e}")
        return

    update_progress(task_id, 80)

    if not extract_result.get("success"):
        fail_task(task_id, extract_result.get("error", "提取失败"))
        return

    # 字段映射 + 入表
    category = _classify_for_table(trace["ocr_content"], trace.get("file_name", ""))
    table_name = _map_category_to_table(category)
    fields_data = {f["name"]: f["value"] for f in extract_result.get("fields", [])}
    # 补抽：从 OCR 文本 regex 捞 LLM 漏掉的关键审计字段
    from services.field_mapper import enrich_fields_from_text
    fields_data = enrich_fields_from_text(fields_data, trace.get("ocr_content", ""))
    from services.field_mapper import map_extracted_fields
    row_dict, extra_fields = map_extracted_fields(table_name, fields_data)

    row_id = _insert_into_data_table(
        table_name, project_id, trace_id, trace.get("file_name", ""),
        trace["ocr_content"], row_dict, extra_fields, template_name,
    )

    complete_task(task_id, {
        "table": table_name, "row_id": row_id, "template": template_name,
        "fields_count": len(extract_result.get("fields", [])),
    })


def _run_analysis_task(task_id: int, task_data: dict):
    """AI 分析任务: 调用 Agent 执行违规分析"""
    start_task(task_id)
    update_progress(task_id, 20)

    from agents.registry import AgentRegistry
    agent = AgentRegistry().create_agent("audit_analyzer")
    # 防御: _clean_task 清空 result 时保留键、值为 None，.get 默认值不生效 → None.get() 崩
    task_result = task_data.get("result") or {}
    result = agent.run({
        "domain": task_result.get("domain", ""),
        "item": task_result.get("item", ""),
        "project_id": task_data.get("project_id", ""),
    })

    update_progress(task_id, 80)

    if result.get("success"):
        complete_task(task_id, result.get("output", {}))
    else:
        fail_task(task_id, result.get("error", "AI分析失败"))


def _run_export_task(task_id: int, task_data: dict):
    """导出任务: 生成报告文件存入 MinIO"""
    start_task(task_id)
    update_progress(task_id, 30)

    # 生成 JSON 格式报告
    project_id = task_data.get("project_id", "")
    report_content = json.dumps(task_data.get("result", {}), ensure_ascii=False, indent=2)
    file_name = f"export_{task_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    minio_path = f"{project_id}/exports/{file_name}"

    try:
        from services.minio_client import get_client
        from config import Config
        client = get_client()
        client.put_object(
            Config.MINIO_BUCKET, minio_path,
            io.BytesIO(report_content.encode("utf-8")),
            length=len(report_content.encode("utf-8")),
            content_type="application/json",
        )
        complete_task(task_id, {"minio_path": minio_path, "file_name": file_name})
    except Exception as e:
        fail_task(task_id, f"导出文件存储失败: {e}")


def _run_archive_task(task_id: int, task_data: dict):
    """归档任务: 标记项目为已归档"""
    start_task(task_id)
    update_progress(task_id, 30)

    project_id = task_data.get("project_id", "")
    if not project_id:
        fail_task(task_id, "缺少项目ID")
        return

    try:
        from services.db import execute
        execute(
            "UPDATE audit_projects SET status = 'archived' WHERE id = %s",
            (project_id,), database="tt",
        )
        update_progress(task_id, 100)
        complete_task(task_id, {"archived": True, "project_id": project_id})
    except Exception as e:
        fail_task(task_id, f"归档失败: {e}")


def _map_category_to_table(category: str) -> str:
    """文档分类 → 数据工坊表名映射"""
    mapping = {
        "合同协议类": "data_contracts",
        "财务凭证类": "data_finance",
        "财务票据类": "data_finance",
        "财务账簿类": "data_finance",
        "业务单据类": "data_credentials",
        "法律文书类": "data_legal_docs",
        "审查报告类": "data_legal_docs",
        "规章制度类": "data_legal_docs",
        "登记台账类": "data_registers",
        "清单名册类": "data_registers",
        "记录留痕类": "data_registers",
        "资质证照类": "data_credentials",
        "影像图件类": "data_credentials",
        "数据表格类": "data_general",
        "数据信息类": "data_general",
        "政策文件类": "data_general",
        "历史档案类": "data_general",
        "资料材料类": "data_general",
    }
    return mapping.get(category, "data_general")


def shutdown():
    """关闭线程池，等待所有任务完成"""
    _executor.shutdown(wait=True)
