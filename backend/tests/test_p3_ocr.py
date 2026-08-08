r"""Phase3 端到端验收：真实 PDF 上传 → OntoSKU 解析 → 落库 → 重解析（P3-1/3/5/6/7/9/12）

需 backend + MinIO + MySQL + OntoSKU(189:5005) 真实可达。用仓库 data/test_contract_cn.pdf
（24KB 真实中文合同）做真实解析，覆盖执行包 §8 各断言：
  P3-1/P3-2：upload 响应 trace_id/task_id/ocr_status=pending；trace.parse_status 已初始化、
             parse_engine NULL；task.payload 非空、result 未完成
  P3-3/P3-5/P3-6/P3-7：轮询 completed → trace.parse_status=done/parsed_at/parse_engine∈三档/
             ocr_content 非空；ontosku 档 external_document_id+external_job_id 非空；
             ontosku_template 不再是引擎字符串（P3-7 bug 修复）
  P3-9：data_* 表命中行 + doc_type 非空
  P3-12：reparse → 新 task_id；轮询 completed → ocr_version 旧值+1

跑法：cd backend && .venv\Scripts\python.exe tests\test_p3_ocr.py [BASE_URL]
"""
import json
import sys
import os
import time
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, execute  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(REPO_ROOT, "data", "test_contract_cn.pdf")
TASK_TIMEOUT = 200  # OntoSKU 真实解析单文档留足时间

PASS = 0
FAIL = 0


def req(method, url, body=None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
                               headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def upload_file(pid, path):
    boundary = "----p3test" + uuid.uuid4().hex
    with open(path, "rb") as f:
        content = f.read()
    fname = os.path.basename(path)
    body = (
        ("--" + boundary + "\r\n").encode()
        + ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % fname).encode("utf-8")
        + ("Content-Type: application/pdf\r\n\r\n").encode() + content + b"\r\n"
        + ("--" + boundary + "--\r\n").encode()
    )
    r = urllib.request.Request(BASE + "/api/audit/projects/" + pid + "/upload", data=body, method="POST",
                               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
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


def finalize_chain(pid):
    req("PUT", f"{BASE}/api/audit/projects/{pid}/target-scope",
        {"scope": "P3范围", "extend_unit": "延伸", "audit_focus": ["重点"]})
    req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
        {"audit_items": [{"title": "事项1", "priority": "高"}]})
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    return st == 200 and r.get("project", {}).get("setup_stage") == "workspace"


def poll_task(task_id, timeout=TASK_TIMEOUT):
    """轮询任务到 completed/failed，返回 (status, result_dict, task_row)"""
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        st, r = req("GET", f"{BASE}/api/audit/tasks/{task_id}")
        if st == 200 and r.get("success"):
            t = r.get("task", {})
            status = t.get("status")
            if status in ("completed", "failed", "cancelled"):
                return status, t, t
            if status != last:
                last = status
                print(f"    … task {task_id} status={status} progress={t.get('progress')}")
        time.sleep(3)
    return "timeout", {}, {}


def main():
    global PASS, FAIL
    if not os.path.exists(FIXTURE):
        print(f"[fatal] 缺真实 PDF 夹具: {FIXTURE}")
        sys.exit(2)
    st, _ = req("GET", f"{BASE}/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase3 端到端（真实 PDF）目标 {BASE}\n      夹具 {FIXTURE}\n")

    project_name = "P3-ocr-e2e-{}".format(uuid.uuid4().hex[:6])
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": project_name, "audit_period": "2026-01-01至2026-06-30"})
    pid = r.get("project", {}).get("id", "")
    check("创建项目成功", bool(pid), str(r)[:120])
    if not finalize_chain(pid):
        check("finalize 推进 workspace", False, "finalize 失败")
        print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
        sys.exit(1 if FAIL else 0)
    check("finalize 推进 workspace", True)

    trace_id = None
    task1_id = None
    table_name = None
    row_id = None
    bucket = "audit-project-{}".format(pid)

    try:
        # ═══ P3-1/P3-2：上传真实 PDF ═══
        print("\n── P3-1/P3-2 上传 → trace+task（payload 列）──")
        st, r = upload_file(pid, FIXTURE)
        check("上传 → 200", st == 200 and r.get("success") is True, f"{st} {str(r)[:140]}")
        trace_id = r.get("trace_id")
        task1_id = r.get("task_id")
        check("响应含 trace_id", isinstance(trace_id, int), str(r)[:140])
        check("响应含 task_id", isinstance(task1_id, int), str(r)[:140])
        check("响应 ocr_status='pending'", r.get("ocr_status") == "pending", str(r)[:140])

        if trace_id:
            t0 = query_one(
                "SELECT parse_status, parse_engine FROM audit_document_traces WHERE id=%s",
                (trace_id,), database="tt",
            )
            # worker 可能已取走→running；关键是已初始化（非 NULL）且 engine 未落
            check("trace.parse_status 已初始化(pending/running)",
                  t0 and t0.get("parse_status") in ("pending", "running"), str(t0))
            check("trace.parse_engine 此时为 NULL", t0 and t0.get("parse_engine") is None, str(t0))

        if task1_id:
            tk0 = query_one("SELECT payload, result FROM audit_task_queue WHERE id=%s",
                            (task1_id,), database="tt")
            check("task.payload 非空（P3-2 入参列）",
                  tk0 and tk0.get("payload") is not None, str(tk0)[:140])
            # result 可能为 NULL（未完成）或已开始写——P3-2 前 result 应为空
            check("task.result 初始为空（未完成）",
                  tk0 and (tk0.get("result") is None), str(tk0)[:140])

        # ═══ P3-3/P3-5/P3-6/P3-7：轮询首次解析完成 ═══
        print(f"\n── 轮询首次解析 task={task1_id}（超时 {TASK_TIMEOUT}s）──")
        status, _, trow = poll_task(task1_id) if task1_id else ("noid", {}, {})
        check("首次解析 task=completed", status == "completed",
              f"status={status} err={str(trow.get('error_msg'))[:120]}")

        if status == "completed" and trace_id:
            tr = query_one(
                "SELECT parse_status, parse_engine, parsed_at, ocr_content, "
                "external_document_id, external_job_id, ontosku_template "
                "FROM audit_document_traces WHERE id=%s",
                (trace_id,), database="tt")
            check("P3-5 parse_status='done'", tr and tr.get("parse_status") == "done", str(tr)[:140])
            check("P3-5 parsed_at 非空", tr and tr.get("parsed_at") is not None, str(tr)[:140])
            check("P3-3/4 parse_engine∈三档",
                  tr and tr.get("parse_engine") in ("ontosku", "liteparse", "local-llm"),
                  str(tr)[:140])
            check("P3-6 ocr_content 非空",
                  tr and tr.get("ocr_content") and len(tr["ocr_content"]) > 0, str(tr)[:140])
            if tr and tr.get("parse_engine") == "ontosku":
                check("P3-3 external_document_id 非空",
                      tr.get("external_document_id"), str(tr)[:140])
                check("P3-3 external_job_id 非空",
                      tr.get("external_job_id"), str(tr)[:140])
            # P3-7：ontosku_template 不能是引擎字符串（原 bug）
            bug_vals = {"ontosku", "liteparse", "local-llm", "local-llm(fallback)"}
            check("P3-7 ontosku_template 非引擎字符串bug",
                  tr and tr.get("ontosku_template") not in bug_vals, str(tr)[:140])
            if tr and tr.get("ontosku_template"):
                check("P3-7 ontosku_template 形如模板（含 /）",
                      "/" in tr["ontosku_template"], str(tr.get("ontosku_template"))[:120])

            # ═══ P3-9：data_* 命中行 + doc_type ═══
            res = trow.get("result") or {}
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    res = {}
            table_name = res.get("table")
            row_id = res.get("row_id")
            check("complete_task.result 含 table", bool(table_name), str(res)[:140])
            check("complete_task.result 含 row_id", isinstance(row_id, int), str(res)[:140])
            if table_name and row_id:
                drow = query_one("SELECT doc_type, document_trace_id FROM %s WHERE id=%s"
                                 % (table_name, "%s"), (row_id,), database="tt")
                check("P3-9 data_* 命中行", drow is not None, f"{table_name}.{row_id}")
                check("P3-9 data_*.doc_type 非空",
                      drow and drow.get("doc_type"), str(drow)[:140])
                check("P3-9 document_trace_id 关联",
                      drow and drow.get("document_trace_id") == trace_id, str(drow)[:140])

        # ═══ P3-12：重解析（异步）→ ocr_version+1 ═══
        if status == "completed" and trace_id:
            print("\n── P3-12 重解析（异步）──")
            before = query_one("SELECT ocr_version FROM audit_document_traces WHERE id=%s",
                               (trace_id,), database="tt")
            old_v = before.get("ocr_version") if before else None
            st, r = req("POST", f"{BASE}/api/audit/documents/reparse", {"document_id": trace_id})
            check("reparse → 200", st == 200 and r.get("success") is True, f"{st} {str(r)[:140]}")
            check("reparse 返回 task_id（异步）", isinstance(r.get("task_id"), int), str(r)[:140])
            check("reparse 不再返 result（异步化）", "result" not in r, str(r)[:140])
            check("reparse 返回 ocr_version（旧值）",
                  r.get("ocr_version") == old_v, f"{r.get('ocr_version')} vs {old_v}")
            task2_id = r.get("task_id")
            if task2_id:
                print(f"    … 轮询重解析 task={task2_id}")
                status2, _, _ = poll_task(task2_id)
                check("重解析 task=completed", status2 == "completed", f"status={status2}")
                after = query_one("SELECT ocr_version FROM audit_document_traces WHERE id=%s",
                                  (trace_id,), database="tt")
                check("P3-12 ocr_version 旧值+1",
                      after and after.get("ocr_version") == (old_v or 0) + 1,
                      f"{old_v} → {after.get('ocr_version') if after else None}")
                # 清重解析产生的第二条 data 行（同 trace 第二次解析）
                if table_name and trace_id:
                    execute(f"DELETE FROM {table_name} WHERE document_trace_id=%s",
                            (trace_id,), database="tt")

    finally:
        # 收尾：删 trace / 首条 data 行 / 任务 / minio 对象 / 项目
        try:
            if trace_id:
                execute("DELETE FROM audit_document_traces WHERE id=%s", (trace_id,), database="tt")
            if table_name and trace_id:
                execute(f"DELETE FROM {table_name} WHERE document_trace_id=%s", (trace_id,), database="tt")
            for tid in (task1_id,):
                if tid:
                    execute("DELETE FROM audit_task_queue WHERE id=%s", (tid,), database="tt")
            execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
            from services.minio_client import delete_object, list_objects
            try:
                for obj in list_objects(bucket=bucket):
                    delete_object(obj.object_name, bucket=bucket)
            except Exception as e:
                print(f"[cleanup] minio 清理跳过: {e}")
            print("[cleanup] 已删临时 trace/data/task/project")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"端到端结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
