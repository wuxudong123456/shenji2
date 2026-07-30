"""Phase 5 — 后台任务 API 路由

端点:
  GET    /api/audit/tasks         — 任务列表（含统计）
  POST   /api/audit/tasks         — 创建任务 + 提交异步执行
  GET    /api/audit/tasks/<id>    — 查询单个任务
  POST   /api/audit/tasks/<id>/cancel  — 取消任务
  POST   /api/audit/tasks/<id>/retry   — 重试失败任务
"""
from flask import request, jsonify
from services.task_manager import (
    create_task, get_task, list_tasks, cancel_task, retry_task,
)
from services.task_worker import submit_task


def register_task_routes(app):

    @app.route("/api/audit/tasks", methods=["GET"])
    def audit_tasks_list():
        """GET /api/audit/tasks — 任务列表 + 统计

        Query params:
            project_id  — 按项目筛选
            status      — pending/processing/completed/failed/cancelled
            type        — ocr/extract/analysis/export/archive
        """
        result = list_tasks(
            project_id=request.args.get("project_id", ""),
            status=request.args.get("status", ""),
            task_type=request.args.get("type", ""),
            limit=request.args.get("limit", 50, type=int),
            offset=request.args.get("offset", 0, type=int),
        )
        return jsonify(result)

    @app.route("/api/audit/tasks", methods=["POST"])
    def audit_tasks_create():
        """POST /api/audit/tasks — 创建后台任务并异步执行

        Body: { name, type, project_id }
        立即返回 task 对象，实际执行由后台 Worker 负责。
        """
        data = request.get_json() or {}
        task_name = data.get("name", "")
        task_type = data.get("type", "ocr")
        project_id = data.get("project_id", "")

        # 创建任务
        result = create_task(task_name, task_type, project_id)
        if not result.get("success") and result.get("error"):
            return jsonify(result), 400

        task_id = result["task"]["id"]

        # 如果有额外数据，注入到任务记录
        if data.get("result"):
            from services.db import execute
            import json
            execute(
                "UPDATE audit_task_queue SET result = %s WHERE id = %s",
                (json.dumps(data["result"], ensure_ascii=False), task_id),
                database="tt",
            )

        # 提交到后台线程池异步执行
        submit_task(task_id)

        # 重新查询（获取 updated 状态）
        updated = get_task(task_id)
        return jsonify(updated or result)

    @app.route("/api/audit/tasks/<int:task_id>", methods=["GET"])
    def audit_task_detail(task_id):
        """GET /api/audit/tasks/<id> — 查询单个任务状态和进度"""
        result = get_task(task_id)
        if not result:
            return jsonify({"success": False, "error": "任务不存在"}), 404
        return jsonify(result)

    @app.route("/api/audit/tasks/<int:task_id>/cancel", methods=["POST"])
    def audit_task_cancel(task_id):
        """POST /api/audit/tasks/<id>/cancel — 取消任务"""
        from services.task_worker import cancel_running_task
        cancelled = cancel_task(task_id)
        if not cancelled:
            return jsonify({"success": False, "error": "任务不存在或无法取消（仅 pending/processing 可取消）"}), 400
        cancel_running_task(task_id)
        return jsonify({"success": True, "message": "任务已取消", "task_id": task_id})

    @app.route("/api/audit/tasks/<int:task_id>/retry", methods=["POST"])
    def audit_task_retry(task_id):
        """POST /api/audit/tasks/<id>/retry — 重试失败任务"""
        ok = retry_task(task_id)
        if not ok:
            return jsonify({"success": False, "error": "任务不存在或不可重试（仅 failed/cancelled 可重试）"}), 400
        submit_task(task_id)
        return jsonify({"success": True, "message": "已重新提交", "task_id": task_id})
