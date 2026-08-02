"""API 请求日志中间件

自动记录每个 HTTP 请求的方法/路径/耗时/状态码。
不记录上传文件的请求体（含文件流，体积大且敏感）。

用法（在 app.py 中）:
    from middleware.request_logger import register_request_logger
    register_request_logger(app)
"""
import time
from flask import request

# 不记录请求体的路径（含文件流或大响应）
_SKIP_BODY_PATHS = ("/api/audit/projects/", "/api/files/", "/upload", "/download")


def register_request_logger(app):
    """注册请求日志钩子到 Flask app"""

    @app.before_request
    def _log_request_start():
        """请求开始：记录起始时间"""
        request._log_start_time = time.perf_counter()

    @app.after_request
    def _log_request_end(response):
        """请求结束：记录日志"""
        try:
            duration_ms = 0
            start = getattr(request, "_log_start_time", None)
            if start:
                duration_ms = int((time.perf_counter() - start) * 1000)

            # 获取客户端 IP（穿透代理）
            ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            if not ip:
                ip = request.remote_addr or ""

            path = request.path or ""

            # 对上传类接口不记录请求体
            detail = {"method": request.method, "path": path}
            if not any(p in path for p in _SKIP_BODY_PATHS):
                if request.method in ("POST", "PUT", "DELETE"):
                    try:
                        if request.is_json:
                            detail["body"] = request.get_json(silent=True)
                    except Exception:
                        pass

            # 跳过静态文件请求（CSS/JS/HTML）
            if path.endswith((".css", ".js", ".html", ".png", ".jpg", ".ico", ".woff2")):
                return response

            from services.audit_logger import log
            log(
                "request",
                action=f"{request.method} {path}",
                ip=ip,
                duration_ms=duration_ms,
                detail={
                    "method": request.method,
                    "path": path,
                    "status": response.status_code,
                },
            )
        except Exception:
            pass  # fail-silent

        return response
