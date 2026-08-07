r"""P2-3 §6.6 finalize 生成首版 manifest 端到端单测（PHASE_2）

验证 finalize 成功后在项目 bucket 写入 workspace-manifest.json（首版空 files[]）。
需 backend 运行（含 §6.6 manifest 接入）。跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_finalize_manifest.py [BASE_URL]
"""
import json
import sys
import os
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.minio_client import get_client, delete_object  # noqa: E402
from services.workspace_service import (  # noqa: E402
    build_manifest_path, compute_safe_name, load_manifest,
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
    return st, r


def main():
    print(f"[test] P2-3 §6.6 finalize 生成首版 manifest 目标 {BASE}\n")

    project_name = "P2-finalize-manifest-{}".format(uuid.uuid4().hex[:6])
    audit_period = "2026-01-01至2026-06-30"
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": project_name, "audit_period": audit_period})
    pid = r.get("project", {}).get("id", "")
    check("创建项目成功", bool(pid), str(r)[:120])

    st, r = finalize_chain(pid)
    ok = st == 200 and r.get("project", {}).get("setup_stage") == "workspace"
    check("finalize 推进到 workspace", ok, f"st={st} body={str(r)[:150]}")
    bucket = "audit-project-{}".format(pid)
    check("finalize 响应含 minio_bucket", r.get("minio_bucket") == bucket)

    # 定位 manifest：audit_period 取首年 2026
    safe = compute_safe_name(project_name)
    mpath = build_manifest_path("2026", pid, safe)
    m = load_manifest(bucket, mpath)
    check("finalize 写入 workspace-manifest.json", m is not None, f"path={mpath}")
    if m:
        check("manifest manifest_version=1", m.get("manifest_version") == 1)
        check("manifest project_id 匹配", m.get("project_id") == pid)
        check("manifest audit_year=2026", m.get("audit_year") == "2026")
        check("manifest 首版 files 为空", m.get("files") == [])
        check("manifest safe_name 匹配", m.get("safe_name") == safe)
        check("manifest bucket 匹配", m.get("bucket") == bucket)

    # 清理 manifest 对象（项目/桶留待后续清理流程）
    if m:
        try:
            delete_object(mpath, bucket=bucket)
        except Exception:
            pass

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
