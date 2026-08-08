r"""Phase3 切片9 验收：P3-12 reparse 异步重写 + 选路统一

测 reparse 路由契约（不经真实 OCR 完成，只验路由逻辑/响应 shape/前置校验）：
  - happy：workspace 项目 + trace → 200 {success,document_id,task_id,ocr_version,message}，
            且不再同步返 "result"（异步化，§6.12）
  - 409：basic 阶段项目的 trace → "项目未完成资料空间创建"
  - 404：不存在的 document_id
  - 400：缺 document_id

用法：cd backend && .venv\Scripts\python.exe tests\test_p3_slice9.py [BASE_URL]
"""
import json
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def req(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def main():
    # 健康检查
    st, _ = req("GET", f"{BASE}/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase3 切片9：P3-12 reparse 异步重写 目标 {BASE}\n")

    sys.path.insert(0, __import__("os").path.abspath(
        __import__("os").path.join(__import__("os").path.dirname(__file__), "..")))
    from services.db import insert, execute  # noqa: E402

    PID_WS = "__p3_ws__"
    PID_BASIC = "__p3_basic__"
    # DB 夹具：workspace 项目 + 其 trace；basic 项目 + 其 trace
    execute("DELETE FROM audit_projects WHERE id IN (%s,%s)", (PID_WS, PID_BASIC), database="tt")
    execute("INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (PID_WS, "P3reparse_WS"), database="tt")
    execute("INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'basic',0)",
            (PID_BASIC, "P3reparse_BASIC"), database="tt")
    t_ws = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,1,'done')",
        (PID_WS, "ws.pdf", "audit-project-bogus", "ws/ws.pdf"), database="tt",
    )
    t_basic = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,parse_status) VALUES (%s,%s,'done')",
        (PID_BASIC, "basic.pdf"), database="tt",
    )
    created_tasks = []

    try:
        # ① happy：workspace trace → 200，异步 shape（无 result）
        print("── ① reparse(workspace trace) → 200 异步 shape ──")
        st, r = req("POST", f"{BASE}/api/audit/documents/reparse", {"document_id": t_ws})
        check("HTTP 200", st == 200, f"{st} {str(r)[:120]}")
        check("success=True", r.get("success") is True, str(r)[:120])
        check("document_id 回传", r.get("document_id") == t_ws, str(r)[:120])
        check("返回 task_id（异步）", isinstance(r.get("task_id"), int), str(r)[:120])
        check("返回 ocr_version", "ocr_version" in r, str(r)[:120])
        check("返回 message", isinstance(r.get("message"), str), str(r)[:120])
        check("不再同步返 result（异步化）", "result" not in r, str(r)[:120])
        if r.get("task_id"):
            created_tasks.append(r["task_id"])

        # ② 带指定 template_name → sku_profile 透传（仍 200）
        print("\n── ② reparse 带 template_name → 200 ──")
        st, r = req("POST", f"{BASE}/api/audit/documents/reparse",
                    {"document_id": t_ws, "template_name": "audit/历史档案类/卷宗"})
        check("带 template_name HTTP 200", st == 200, f"{st} {str(r)[:120]}")
        if r.get("task_id"):
            created_tasks.append(r["task_id"])

        # ③ basic 阶段 → 409
        print("\n── ③ reparse(basic 项目 trace) → 409 ──")
        st, r = req("POST", f"{BASE}/api/audit/documents/reparse", {"document_id": t_basic})
        check("HTTP 409", st == 409, f"{st} {str(r)[:120]}")
        check("错误含资料空间", "资料空间" in r.get("error", ""), str(r)[:120])
        check("返回 setup_stage=basic", r.get("setup_stage") == "basic", str(r)[:120])

        # ④ 不存在 document_id → 404
        print("\n── ④ reparse(不存在) → 404 ──")
        st, r = req("POST", f"{BASE}/api/audit/documents/reparse", {"document_id": 9999999})
        check("HTTP 404", st == 404, f"{st} {str(r)[:120]}")

        # ⑤ 缺 document_id → 400
        print("\n── ⑤ reparse(缺 document_id) → 400 ──")
        st, r = req("POST", f"{BASE}/api/audit/documents/reparse", {})
        check("HTTP 400", st == 400, f"{st} {str(r)[:120]}")
    finally:
        # 收尾：删任务（避免后台重试噪音）+ 临时 trace + 临时 project
        if created_tasks:
            execute(
                "DELETE FROM audit_task_queue WHERE id IN (%s)" % ",".join(["%s"] * len(created_tasks)),
                tuple(created_tasks), database="tt",
            )
        execute("DELETE FROM audit_document_traces WHERE project_id IN (%s,%s)",
                (PID_WS, PID_BASIC), database="tt")
        execute("DELETE FROM audit_projects WHERE id IN (%s,%s)", (PID_WS, PID_BASIC), database="tt")
        print("\n[cleanup] 已删临时 project/trace/task")

    print(f"\n{'='*48}")
    print(f"切片9 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
