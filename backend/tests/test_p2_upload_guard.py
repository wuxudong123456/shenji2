r"""P2-2 upload 前置校验单测（PHASE_2）

验证：
  1. 未 finalize 项目 upload → 409（setup_stage != workspace）
  2. 完整立项+finalize 后 upload → 200（桶由 finalize 建，删二次 make_bucket 后仍可传）
  3. 不存在项目 upload → 404

需 backend 运行（含 P2-2 改造）。跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_upload_guard.py [BASE_URL]
"""
import json
import sys
import uuid
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
        with urllib.request.urlopen(r, timeout=20) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def upload(pid):
    """multipart 上传 test.txt，返回 (status, body)"""
    boundary = "----p2test" + uuid.uuid4().hex
    content = b"hello p2 test"
    body = (
        ("--" + boundary + "\r\n").encode()
        + b'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
        + b"Content-Type: text/plain\r\n\r\n" + content + b"\r\n"
        + ("--" + boundary + "--\r\n").encode()
    )
    r = urllib.request.Request(
        BASE + "/api/audit/projects/" + pid + "/upload", data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
    )
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
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
    """推进项目到 finalize（workspace）：target-scope → items → finalize"""
    req("PUT", f"{BASE}/api/audit/projects/{pid}/target-scope",
        {"scope": "P2范围", "extend_unit": "延伸", "audit_focus": ["重点"]})
    req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
        {"audit_items": [{"title": "事项1", "priority": "高"}]})
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    return st == 200 and r.get("project", {}).get("setup_stage") == "workspace"


def main():
    print(f"[test] P2-2 upload 前置校验 目标 {BASE}\n")

    # ① 未 finalize 项目 upload → 409
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": "P2-upload-guard-未finalize", "audit_period": "2026-01-01至2026-06-30"})
    pid1 = r.get("project", {}).get("id", "")
    st1, r1 = upload(pid1)
    check("未 finalize 项目 upload → 409", st1 == 409, f"st={st1} body={str(r1)[:120]}")
    check("409 含 setup_stage 字段", "setup_stage" in r1, str(r1)[:120])

    # ② 完整立项+finalize 后 upload → 200（删二次建桶后仍可传，桶由 finalize 建）
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": "P2-upload-guard-已finalize", "audit_period": "2026-01-01至2026-06-30"})
    pid2 = r.get("project", {}).get("id", "")
    ok = finalize_chain(pid2)
    check("finalize 推进到 workspace", ok, "finalize 失败")
    if ok:
        st2, r2 = upload(pid2)
        check("已 finalize 项目 upload → 200", st2 == 200 and r2.get("success") is True,
              f"st={st2} body={str(r2)[:150]}")

    # ③ 不存在项目 upload → 404
    st3, r3 = upload("nonexistent-project-xyz-p2")
    check("不存在项目 upload → 404", st3 == 404, f"st={st3}")

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
