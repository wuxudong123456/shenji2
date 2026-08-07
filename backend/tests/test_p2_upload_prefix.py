r"""P2-6 upload 前缀/新列/分类/manifest 增量单测（PHASE_2）

验证（需 backend + MinIO + MySQL 运行）：
  1. 上传 .pdf → minio_path 走 §3.1 前缀（2026/{pid}-{safe_name}/text/pdf/...）+ 叶子保留原名
  2. trace 落新列 audit_year/file_category/file_subcategory/minio_bucket/file_size
  3. manifest files[] 增量含该文件
  4. 上传 .png → category=image，minio_path 走 /image/（无 subcategory 子目录）

跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_upload_prefix.py [BASE_URL]
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
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
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
        {"scope": "P2范围", "extend_unit": "延伸", "audit_focus": ["重点"]})
    req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
        {"audit_items": [{"title": "事项1", "priority": "高"}]})
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    return st == 200 and r.get("project", {}).get("setup_stage") == "workspace"


def main():
    global PASS, FAIL
    print(f"[test] P2-6 upload 前缀/新列/分类/manifest 增量 目标 {BASE}\n")

    project_name = "P2-upload-prefix-{}".format(uuid.uuid4().hex[:6])
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": project_name, "audit_period": "2026-01-01至2026-06-30"})
    pid = r.get("project", {}).get("id", "")
    check("创建项目成功", bool(pid), str(r)[:120])
    ok = finalize_chain(pid)
    check("finalize 推进 workspace", ok)
    if not ok:
        print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
        return 1
    bucket = "audit-project-{}".format(pid)
    safe = compute_safe_name(project_name)
    prefix = "2026/{}-{}/".format(pid, safe)

    # ① 上传 .pdf
    st, r = upload(pid, "采购合同.pdf", b"%PDF-1.4 fake pdf content for p2", "application/pdf")
    check("上传 .pdf → 200", st == 200 and r.get("success") is True, f"st={st} {str(r)[:120]}")
    mpath_pdf = r.get("minio_path", "")
    check(".pdf minio_path 以 2026/{pid}-{safe}/ 前缀开头", mpath_pdf.startswith(prefix), mpath_pdf)
    after_pdf = mpath_pdf[len(prefix):].split("/") if mpath_pdf.startswith(prefix) else []
    check(".pdf 前缀后为 text/pdf/{file_id}.原名",
          len(after_pdf) == 3 and after_pdf[0] == "text" and after_pdf[1] == "pdf"
          and after_pdf[2].endswith(".采购合同.pdf"), mpath_pdf)
    trace_id_pdf = r.get("trace_id")

    # trace 新列
    if trace_id_pdf:
        t = query_one(
            "SELECT audit_year, file_category, file_subcategory, minio_bucket, file_size "
            "FROM audit_document_traces WHERE id = %s",
            (trace_id_pdf,), database="tt",
        )
        check("trace.audit_year=2026", t and t.get("audit_year") == "2026", str(t))
        check("trace.file_category=text", t and t.get("file_category") == "text")
        check("trace.file_subcategory=pdf", t and t.get("file_subcategory") == "pdf")
        check("trace.minio_bucket 已落", t and t.get("minio_bucket") == bucket)
        check("trace.file_size 已落 >0", t and t.get("file_size") and t["file_size"] > 0)

    # ② 上传 .png（image，无 subcategory）
    st, r2 = upload(pid, "现场照片.png", b"\x89PNG\r\n\x1a\n fake png content for p2", "image/png")
    check("上传 .png → 200", st == 200 and r2.get("success") is True, f"st={st} {str(r2)[:120]}")
    mpath_png = r2.get("minio_path", "")
    after_png = mpath_png[len(prefix):].split("/") if mpath_png.startswith(prefix) else []
    check(".png 前缀后为 image/{file_id}.原名（无 subcategory 子目录）",
          len(after_png) == 2 and after_png[0] == "image" and after_png[1].endswith(".现场照片.png"),
          mpath_png)
    t2 = None
    if r2.get("trace_id"):
        t2 = query_one("SELECT file_category, file_subcategory FROM audit_document_traces WHERE id = %s",
                       (r2["trace_id"],), database="tt")
    check(".png trace.file_category=image", t2 and t2.get("file_category") == "image")
    check(".png trace.file_subcategory 为 None", t2 and t2.get("file_subcategory") is None)

    # ③ manifest files[] 增量含两条
    mpath = build_manifest_path("2026", pid, safe)
    m = load_manifest(bucket, mpath)
    check("manifest files[] 含 ≥2 条", m and len(m.get("files", [])) >= 2,
          str(m.get("files") if m else None)[:120])
    if m:
        names = [f.get("file_name") for f in m["files"]]
        check("manifest 含 采购合同.pdf", "采购合同.pdf" in names)
        check("manifest 含 现场照片.png", "现场照片.png" in names)
        pdf_entry = next((f for f in m["files"] if f.get("file_name") == "采购合同.pdf"), None)
        check("manifest pdf entry category=text/subcategory=pdf",
              pdf_entry and pdf_entry.get("category") == "text" and pdf_entry.get("subcategory") == "pdf")
        check("manifest pdf entry object_key 与 trace minio_path 一致",
              pdf_entry and pdf_entry.get("object_key") == mpath_pdf)

    # 清理：删 manifest + 两个对象
    for p in [mpath_pdf, mpath_png, mpath]:
        try:
            delete_object(p, bucket=bucket)
        except Exception:
            pass

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
