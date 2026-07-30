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

def _run_ocr_task(task_id: int, task_data: dict):
    """OCR 文件解析任务"""
    start_task(task_id)
    update_progress(task_id, 10)

    project_id = task_data.get("project_id", "")
    file_name = task_data.get("task_name", "")

    # 从文件记录中获取 MinIO 路径
    from services.db import query_one
    trace = query_one(
        "SELECT * FROM audit_document_traces WHERE project_id = %s AND file_name = %s "
        "ORDER BY id DESC LIMIT 1",
        (project_id, file_name), database="tt",
    )
    if not trace or not trace.get("minio_path"):
        fail_task(task_id, f"找不到文件记录: {file_name}")
        return

    update_progress(task_id, 30)

    # 从 MinIO 下载文件
    try:
        from services.minio_client import download_file
        from config import Config
        file_bytes = download_file(trace["minio_path"])
    except Exception as e:
        fail_task(task_id, f"从MinIO下载失败: {e}")
        return

    update_progress(task_id, 50)

    # OCR 解析
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(file_bytes)
        tmp.close()
        from services.ocr_client import OCREngine
        ocr_result = OCREngine.parse(tmp.name)
        os.unlink(tmp.name)
    except Exception as e:
        fail_task(task_id, f"OCR解析失败: {e}")
        return

    update_progress(task_id, 80)

    # 更新溯源记录
    if ocr_result.get("success"):
        ocr_text = str(ocr_result.get("text", "") or str(ocr_result))
        from services.db import execute
        execute(
            "UPDATE audit_document_traces SET ocr_content = %s, ocr_version = 1 WHERE id = %s",
            (ocr_text[:50000], trace["id"]), database="tt",
        )
        complete_task(task_id, {
            "trace_id": trace["id"],
            "ocr_engine": ocr_result.get("engine", "unknown"),
            "text_length": len(ocr_text),
        })
    else:
        fail_task(task_id, f"OCR返回失败: {ocr_result.get('error', '未知')}")


def _run_extract_task(task_id: int, task_data: dict):
    """知识抽取任务: OCR文本 → 模板匹配 → LLM字段提取 → 数据工坊入库"""
    start_task(task_id)
    update_progress(task_id, 10)

    project_id = task_data.get("project_id", "")
    trace_id = task_data.get("result", {}).get("trace_id") if isinstance(task_data.get("result"), dict) else None

    # 查溯源记录
    if trace_id:
        from services.db import query_one
        trace = query_one("SELECT * FROM audit_document_traces WHERE id = %s", (trace_id,), database="tt")
    else:
        trace = None

    if not trace or not trace.get("ocr_content"):
        fail_task(task_id, "无OCR内容可提取，请先完成文件解析")
        return

    update_progress(task_id, 30)

    # 文档分类
    from services.extraction_service import classify_document
    try:
        classify_result = classify_document(trace["ocr_content"])
    except Exception as e:
        fail_task(task_id, f"文档分类失败: {e}")
        return

    update_progress(task_id, 50)

    if not classify_result.get("success") or not classify_result.get("matches"):
        fail_task(task_id, "无法匹配到合适的提取模板")
        return

    # 用最佳模板提取
    best_template = classify_result["matches"][0]["name"]
    from services.extraction_service import extract_fields
    try:
        extract_result = extract_fields(best_template, trace["ocr_content"])
    except Exception as e:
        fail_task(task_id, f"字段提取失败: {e}")
        return

    update_progress(task_id, 80)

    # 写入数据工坊（根据分类结果选择目标表）
    table_name = _map_category_to_table(classify_result.get("classification", {}).get("category", ""))
    if extract_result.get("success"):
        from services.db import insert
        fields_data = {f["name"]: f["value"] for f in extract_result.get("fields", [])}
        row_id = insert(
            f"INSERT INTO {table_name} (project_id, document_trace_id, template_name, doc_name, extra_fields, raw_text) "
            f"VALUES (%s,%s,%s,%s,%s,%s)",
            (project_id, trace_id, best_template, trace.get("file_name", ""),
             json.dumps(fields_data, ensure_ascii=False), trace["ocr_content"][:10000]),
            database="tt",
        )
        complete_task(task_id, {
            "table": table_name, "row_id": row_id, "template": best_template,
            "fields_count": len(extract_result.get("fields", [])),
        })
    else:
        fail_task(task_id, extract_result.get("error", "提取失败"))


def _run_analysis_task(task_id: int, task_data: dict):
    """AI 分析任务: 调用 Agent 执行违规分析"""
    start_task(task_id)
    update_progress(task_id, 20)

    from agents.registry import AgentRegistry
    agent = AgentRegistry().create_agent("audit_analyzer")
    result = agent.run({
        "domain": task_data.get("result", {}).get("domain", ""),
        "item": task_data.get("result", {}).get("item", ""),
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
