r"""P2-7 files 列表 manifest 化 + 过滤单测（PHASE_2）

验证（需 backend + MinIO + MySQL 运行）：
  1. GET /files 数据源为 manifest，含 audit_year/category/subcategory/size 新字段
  2. ?category=text 只返 text 类；?category=image 只返 image 类
  3. ?year=2026 返回文件；?year=2099（不匹配）返回空
  4. ocr_done 与 trace join（OCR 未完成 → False）

跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_files_list.py [BASE_URL]
"""
import json
import sys
import os
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.minio_client import delete_object  # noqa: E402
from services.workspace_service import build_manifest_path  # noqa: E402

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


def main():
    global PASS, FAIL
    print(f"[test] P2-7 files 列表 manifest 化 目标 {BASE}\n")

    project_name = "P2-files-list-{}".format(uuid.uuid4().hex[:6])
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": project_name, "audit_period": "2026-01-01至2026-06-30"})
    pid = r.get("project", {}).get("id", "")
    check("创建项目成功", bool(pid))
    ok = finalize_chain(pid)
    check("finalize 推进 workspace", ok)
    if not ok:
        print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
        return 1
    bucket = "audit-project-{}".format(pid)

    st, rp = upload(pid, "报告.pdf", b"%PDF-1.4 fake pdf", "application/pdf")
    st, ri = upload(pid, "截图.png", b"\x89PNG fake png", "image/png")
    check("上传 .pdf + .png 成功", rp.get("success") and ri.get("success"))

    paths = [rp.get("minio_path"), ri.get("minio_path")]

    # ① 全量列表（manifest 源）
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files")
    files = r.get("files", []) if r.get("success") else []
    check("全量列表含 2 条", len(files) == 2, str(files)[:120])
    if files:
        f0 = files[0]
        check("file 含 audit_year=2026", f0.get("audit_year") == "2026", str(f0))
        check("file 含 category 字段", "category" in f0)
        check("file 含 subcategory 字段", "subcategory" in f0)
        check("file 含 size 字段", "size" in f0)
        check("file 含 ocr_done（join）", "ocr_done" in f0)
        check("OCR 未完成 ocr_done=False", f0.get("ocr_done") is False)

    # ② category 过滤
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?category=text")
    ft = r.get("files", [])
    check("?category=text 只返 text 类", len(ft) == 1 and ft[0].get("category") == "text", str(ft)[:120])
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?category=image")
    fi = r.get("files", [])
    check("?category=image 只返 image 类", len(fi) == 1 and fi[0].get("category") == "image", str(fi)[:120])
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?category=video")
    fv = r.get("files", [])
    check("?category=video（无）→ 空", len(fv) == 0)

    # ③ year 过滤
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?year=2026")
    check("?year=2026 返回 2 条", len(r.get("files", [])) == 2)
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?year=2099")
    check("?year=2099（不匹配）→ 空", len(r.get("files", [])) == 0)
    st, r = get(f"{BASE}/api/audit/projects/{pid}/files?year=2026&category=image")
    check("?year=2026&category=image → 1 条", len(r.get("files", [])) == 1)

    # ④ 不存在项目 → 404
    st, r = get(f"{BASE}/api/audit/projects/no-such-proj/files")
    check("不存在项目 → 404", st == 404)

    # 清理
    for p in paths:
        try:
            delete_object(p, bucket=bucket)
        except Exception:
            pass
    try:
        delete_object(build_manifest_path("2026", pid, project_name), bucket=bucket)
    except Exception:
        pass

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
