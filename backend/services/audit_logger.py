"""统一审计日志服务

四类日志统一入口:
  - request:   API 请求(method/path/耗时/状态码)
  - operation: 用户操作(确认/修改/删除，含前后值)
  - llm_call:  LLM 调用(prompt + response + 耗时)
  - db_write:  数据库写操作(INSERT/UPDATE/DELETE)

设计原则:
  - fail-silent: 日志失败绝不抛异常，不影响业务
  - 异步可选: 高频日志用线程池写入
  - 单一入口: 所有日志走 log()
"""
import os
import json
import threading
from datetime import datetime
from services.db import insert

# 日志总开关（.env 中 LOG_ENABLED 控制，默认开）
_ENABLED = os.environ.get("LOG_ENABLED", "true").lower() == "true"

# 异步写日志线程池（高频场景用，低频场景直接同步）
_log_lock = threading.Lock()


def log(log_type: str, action: str, **kwargs):
    """统一日志入口

    Args:
        log_type: request / operation / llm_call / db_write / trace
        action:   动作描述（如 'create_project', 'intent_analyzer'）
        **kwargs: 可选字段
            user:        操作人
            target_type: 对象类型(project/violation/law)
            target_id:   对象ID
            ip:          IP地址
            detail:      详情(dict 会转 JSON)
            duration_ms: 耗时毫秒

    Returns:
        int: 日志记录ID（失败返回 None）

    Example:
        >>> log("operation", "confirm_violation", user="张三",
        ...      target_type="violation", target_id="VIO-0001",
        ...      detail={"before": "medium", "after": "high"})
    """
    if not _ENABLED:
        return None

    try:
        detail = kwargs.get("detail")
        if detail is not None and not isinstance(detail, str):
            # 截断超长内容，避免日志表膨胀
            detail_str = json.dumps(detail, ensure_ascii=False, default=str)
            if len(detail_str) > 8000:
                detail_str = detail_str[:8000] + "...[truncated]"
            detail = detail_str

        log_id = insert(
            "INSERT INTO audit_logs (log_type, action, user, target_type, target_id, "
            "ip_address, detail, duration_ms, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())",
            (log_type, action,
             kwargs.get("user", ""),
             kwargs.get("target_type", ""),
             kwargs.get("target_id", ""),
             kwargs.get("ip", ""),
             detail,
             kwargs.get("duration_ms")),
            database="tt",
        )
        return log_id
    except Exception:
        # fail-silent: 日志失败不影响业务
        return None


def log_llm_call(agent_id: str, prompt: str, response: dict,
                 duration_ms: int = None, trace_id: str = ""):
    """记录 LLM 调用（推理溯源用）

    Args:
        agent_id: Agent 标识
        prompt:   发送给 LLM 的 prompt（截断到 3000 字符）
        response: LLM 返回结果
        duration_ms: 耗时
        trace_id: 追溯ID（关联分析任务）
    """
    return log(
        "llm_call",
        action=f"agent_{agent_id}",
        target_id=trace_id,
        duration_ms=duration_ms,
        detail={
            "agent": agent_id,
            "prompt": (prompt or "")[:3000],
            "response": response,
        },
    )


def log_operation(user: str, action: str, target_type: str = "",
                  target_id: str = "", before=None, after=None, ip: str = ""):
    """记录用户操作（审计追责 + 修正留痕用）

    Args:
        user:       操作人
        action:     动作（如 confirm_violation / modify_field）
        target_type: 对象类型
        target_id:  对象ID
        before:     修改前的值（修正留痕）
        after:      修改后的值（修正留痕）
        ip:         请求IP
    """
    detail = {}
    if before is not None or after is not None:
        detail["before"] = before
        detail["after"] = after
    return log(
        "operation",
        action=action,
        user=user,
        target_type=target_type,
        target_id=target_id,
        ip=ip,
        detail=detail if detail else None,
    )


def log_db_write(sql: str, params=None, affected: int = 0):
    """记录数据库写操作

    Args:
        sql:      SQL 语句（截断到 500 字符）
        params:   参数
        affected: 影响行数
    """
    # 只记录写操作，跳过 SELECT
    sql_upper = (sql or "").strip().upper()
    if not sql_upper.startswith(("INSERT", "UPDATE", "DELETE")):
        return None

    return log(
        "db_write",
        action=sql_upper.split()[0],  # INSERT / UPDATE / DELETE
        detail={
            "sql": (sql or "")[:500],
            "params": str(params)[:500] if params else None,
            "affected_rows": affected,
        },
    )
