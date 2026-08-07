r"""P2-8 下载 + P2-9 软删 + P2-10 跨项目拦截单测（PHASE_2）

验证（需 backend + MinIO + MySQL 运行）：
  1. download?project_id=A&file=<A的key> → 200，url 指向 audit-project-A 桶
  2. download?project_id=B&file=<A的key> → 403（跨项目拦截，P2-10）
  3. delete?project_id=A&file=<A的key> → 200 soft_deleted:true（软删，P2-9）
  4. 软删后 /files 列表过滤掉该文件；trace.deleted_at 已设；manifest.deleted=true
  5. delete?project_id=B&file=<A的key> → 403（跨项目拦截）

跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_download_delete.py [BASE_URL]
"""
import json
import sys
import os
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.db import query_one  # noqa: E402
from services.minio_client import delete_object  # noqa: E402
from services.workspace_service import (  # noqa: E402
    compute_safe_name, build_manifest_path, load_manifest,
)

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
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


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def delete(url):
    r = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def upload(pid, filename, content, ctype):
    boundary = "----p2test" + uuid.uuid4().hex
    body = (
        ("--" + boundary + "\r\n").encode()
        + ('Content-Disposition: form-data; name="file"; filename="%s"\r\n' % filename).encode("utf-8")
        + ("Content-Type: %s\r\n\r\n" % ctype).encode() + content + b"\r\n"
        + ("--" + boundary + "--\r\n").encode()
    )
    r = urllib.request.Request(BASE + "/api/audit/projects/" + pid + "/upload", data=body, method="POST",
                               headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    with urllib.request.urlopen(r, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


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
        {"scope": "P2范围", "extend_unit": "延伸", "audit_focus": ["重点"]})
    req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
        {"audit_items": [{"title": "事项1", "priority": "高"}]})
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    return st == 200 and r.get("project", {}).get("setup_stage") == "workspace"


def make_project(name):
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": name, "audit_period": "2026-01-01至2026-06-30"})
    pid = r.get("project", {}).get("id", "")
    if pid and finalize_chain(pid):
        return pid
    return None


def main():
    global PASS, FAIL
    print(f"[test] P2-8/9/10 下载/软删/跨项目拦截 目标 {BASE}\n")

    tag = uuid.uuid4().hex[:6]
    pidA = make_project("P2-dl-A-{}".format(tag))
    pidB = make_project("P2-dl-B-{}".format(tag))
    check("创建并 finalize 项目 A + B", bool(pidA) and bool(pidB))
    if not (pidA and pidB):
        print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
        return 1

    st, rp = upload(pidA, "合同.pdf", b"%PDF dl test", "application/pdf")
    keyA = rp.get("minio_path", "")
    check("项目 A 上传成功", rp.get("success") and bool(keyA), str(rp)[:120])

    # object_key 含中文，URL 须 percent-encode（后端 Flask 自动解码）
    from urllib.parse import quote
    keyA_q = quote(keyA, safe="")

    # ① download project_id=A → 200，url 指向 A 桶
    st, r = get(f"{BASE}/api/audit/workspace/download?project_id={pidA}&file={keyA_q}")
    check("download(A, A的key) → 200", st == 200 and r.get("success") is True, f"st={st} {str(r)[:120]}")
    url = r.get("url", "")
    check("url 指向 audit-project-A 桶", "audit-project-{}".format(pidA) in url, url[:80])

    # ② download project_id=B & file=A的key → 403（跨项目）
    st, r = get(f"{BASE}/api/audit/workspace/download?project_id={pidB}&file={keyA_q}")
    check("download(B, A的key) → 403 跨项目拦截", st == 403, f"st={st} {str(r)[:120]}")

    # ③ delete project_id=A → 软删 200
    st, r = delete(f"{BASE}/api/audit/workspace/delete?project_id={pidA}&file={keyA_q}")
    check("delete(A, A的key) → 200", st == 200 and r.get("success") is True, f"st={st} {str(r)[:150]}")
    check("软删响应 soft_deleted=true", r.get("soft_deleted") is True)
    trace_id = r.get("trace_id")

    # ④ 软删后 /files 过滤掉；trace.deleted_at 已设；manifest.deleted=true
    st, r = get(f"{BASE}/api/audit/projects/{pidA}/files")
    names = [f.get("file_name") for f in r.get("files", [])]
    check("软删后 /files 不含该文件", "合同.pdf" not in names, str(names))
    if trace_id:
        t = query_one("SELECT deleted_at FROM audit_document_traces WHERE id = %s",
                      (trace_id,), database="tt")
        check("trace.deleted_at 已设", t and t.get("deleted_at") is not None, str(t))
    mpath = build_manifest_path("2026", pidA, compute_safe_name("P2-dl-A-{}".format(tag)))
    m = load_manifest("audit-project-{}".format(pidA), mpath)
    entry = next((f for f in (m or {}).get("files", []) if f.get("object_key") == keyA), None)
    check("manifest 该文件 deleted=true", entry and entry.get("deleted") is True, str(entry))

    # ⑤ delete project_id=B & file=A的key → 403（跨项目）
    st, r = delete(f"{BASE}/api/audit/workspace/delete?project_id={pidB}&file={keyA_q}")
    check("delete(B, A的key) → 403 跨项目拦截", st == 403, f"st={st} {str(r)[:120]}")

    # ⑥ 不存在项目 download → 404
    st, r = get(f"{BASE}/api/audit/workspace/download?project_id=no-such&file={keyA_q}")
    check("download 不存在项目 → 404", st == 404)

    # 清理
    try:
        delete_object(keyA, bucket="audit-project-{}".format(pidA))
        delete_object(mpath, bucket="audit-project-{}".format(pidA))
    except Exception:
        pass

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
