"""Phase 5 — 后台任务管理器

提供任务生命周期管理: 创建→排队→执行→成功/失败/取消
所有状态真实持久化到 MySQL，支持进度查询和日志记录。

任务状态流转:
  pending → processing → completed
                       → failed → (retry) → processing
                       → cancelled
"""
import json
from datetime import datetime
from services.db import query, query_one, execute, insert

VALID_TYPES = {"ocr", "extract", "analysis", "export", "archive"}
VALID_STATUSES = {"pending", "processing", "completed", "failed", "cancelled"}


def create_task(task_name: str, task_type: str, project_id: str = "",
                max_retries: int = 3) -> dict:
    """创建后台任务，状态为 pending，立即返回

    Args:
        task_name: 任务名称（对应前端 name 字段）
        task_type: ocr / extract / analysis / export / archive
        project_id: 关联项目ID（可选）
        max_retries: 最大重试次数

    Returns:
        {id, task_name, task_type, status, progress, created_at}
    """
    if task_type not in VALID_TYPES:
        return {"success": False, "error": f"不支持的任务类型: {task_type}，支持: {VALID_TYPES}"}
    if not task_name:
        return {"success": False, "error": "任务名称不能为空"}

    task_id = insert(
        "INSERT INTO audit_task_queue (task_name, task_type, status, progress, "
        "project_id, max_retries) VALUES (%s,%s,'pending',0,%s,%s)",
        (task_name, task_type, project_id, max_retries),
        database="tt",
    )
    return get_task(task_id)


def get_task(task_id: int) -> dict | None:
    """查询单个任务"""
    row = query_one(
        "SELECT id, task_name, task_type, status, progress, project_id, "
        "result, error_msg, retry_count, max_retries, "
        "created_at, started_at, completed_at FROM audit_task_queue WHERE id = %s",
        (task_id,), database="tt",
    )
    if not row:
        return None
    d = _clean_task(row)
    return {"success": True, "task": d}


def list_tasks(project_id: str = "", status: str = "", task_type: str = "",
               limit: int = 50, offset: int = 0) -> dict:
    """查询任务列表

    Args:
        project_id: 按项目筛选
        status: pending/processing/completed/failed/cancelled
        task_type: ocr/extract/analysis/export/archive
        limit: 每页条数
        offset: 偏移量

    Returns:
        {success, tasks: [...], total, processing, completed, failed}
    """
    clauses = ["1=1"]
    params = []

    if project_id:
        clauses.append("project_id = %s")
        params.append(project_id)
    if status and status in VALID_STATUSES:
        clauses.append("status = %s")
        params.append(status)
    if task_type and task_type in VALID_TYPES:
        clauses.append("task_type = %s")
        params.append(task_type)

    where = " AND ".join(clauses)

    rows = query(
        f"SELECT id, task_name, task_type, status, progress, project_id, "
        f"error_msg, created_at, started_at, completed_at "
        f"FROM audit_task_queue WHERE {where} "
        f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset), database="tt",
    )

    # 统计
    stats_row = query_one(
        f"SELECT COUNT(*) AS total, "
        f"SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) AS processing, "
        f"SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) AS completed, "
        f"SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed "
        f"FROM audit_task_queue WHERE {where}",
        tuple(params), database="tt",
    )

    return {
        "success": True,
        "tasks": [_clean_task(r) for r in rows],
        "total": stats_row["total"] if stats_row else 0,
        "processing": int(stats_row.get("processing") or 0),
        "completed": int(stats_row.get("completed") or 0),
        "failed": int(stats_row.get("failed") or 0),
    }


def start_task(task_id: int) -> bool:
    """标记任务为 processing 状态"""
    return execute(
        "UPDATE audit_task_queue SET status='processing', started_at=NOW() "
        "WHERE id = %s AND status IN ('pending','failed')",
        (task_id,), database="tt",
    ) > 0


def update_progress(task_id: int, progress: int) -> bool:
    """更新任务进度 (0-100)

    Q1.3 修复：已取消的任务不再更新进度（避免 worker 覆写 cancelled 状态）
    """
    progress = max(0, min(100, progress))
    return execute(
        "UPDATE audit_task_queue SET progress = %s WHERE id = %s "
        "AND status NOT IN ('cancelled')",
        (progress, task_id), database="tt",
    ) > 0


def complete_task(task_id: int, result: dict = None) -> bool:
    """标记任务为 completed

    Q1.3 修复：已取消的任务不覆写为 completed（仅 pending/processing 可完成）
    """
    return execute(
        "UPDATE audit_task_queue SET status='completed', progress=100, "
        "result=%s, completed_at=NOW() WHERE id = %s "
        "AND status IN ('pending', 'processing')",
        (json.dumps(result, ensure_ascii=False) if result else None, task_id),
        database="tt",
    ) > 0


def recover_stuck_tasks() -> int:
    """Q1.3 新增：进程重启时恢复卡住的任务

    把状态为 'processing' 的任务改回 'pending'（进程重启后这些任务的 worker 已丢失）。
    应在应用启动时调用一次。

    Returns:
        恢复的任务数量
    """
    try:
        affected = execute(
            "UPDATE audit_task_queue SET status='pending', progress=0, "
            "error_msg='进程重启，任务重新排队' "
            "WHERE status='processing'",
            database="tt",
        )
        if affected > 0:
            print(f"[task_manager] 恢复 {affected} 个卡住的任务")
        return affected
    except Exception:
        return 0


def fail_task(task_id: int, error_msg: str) -> bool:
    """标记任务为 failed，自动递增重试次数"""
    execute(
        "UPDATE audit_task_queue SET status='failed', error_msg=%s, "
        "retry_count = retry_count + 1 WHERE id = %s",
        (error_msg[:2000] if error_msg else "未知错误", task_id),
        database="tt",
    )

    # 检查是否需要自动重试
    row = query_one(
        "SELECT retry_count, max_retries FROM audit_task_queue WHERE id = %s",
        (task_id,), database="tt",
    )
    if row and row["retry_count"] < row["max_retries"]:
        execute(
            "UPDATE audit_task_queue SET status='pending', progress=0, "
            "error_msg=CONCAT('重试中(第', retry_count, '次): ', error_msg) WHERE id = %s",
            (task_id,), database="tt",
        )
        return True  # 已自动重试

    # 重试耗尽，保持 failed 状态
    execute(
        "UPDATE audit_task_queue SET status='failed' WHERE id = %s",
        (task_id,), database="tt",
    )
    return False


def cancel_task(task_id: int) -> bool:
    """取消任务（仅 pending/processing 状态可取消）"""
    return execute(
        "UPDATE audit_task_queue SET status='cancelled', completed_at=NOW() "
        "WHERE id = %s AND status IN ('pending','processing')",
        (task_id,), database="tt",
    ) > 0


def retry_task(task_id: int) -> bool:
    """手动重试失败任务"""
    return execute(
        "UPDATE audit_task_queue SET status='pending', progress=0, error_msg=NULL "
        "WHERE id = %s AND status IN ('failed','cancelled')",
        (task_id,), database="tt",
    ) > 0


def _clean_task(row: dict) -> dict:
    """清理数据库行，转换时间字段"""
    d = dict(row)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif isinstance(v, bytes):
            d[k] = None
    # 解析 JSON 结果
    if d.get("result") and isinstance(d["result"], str):
        try:
            d["result"] = json.loads(d["result"])
        except json.JSONDecodeError:
            pass
    return d
