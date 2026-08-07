r"""P2-5 年度项目树 + P2-10 年度隔离单测（PHASE_2）

验证（需 backend + MinIO + MySQL 运行）：
  1. GET /workspace/tree?year=2026 只返 2026 年项目，counts 按 category 汇总正确
  2. ?year=2025 只返 2025 年项目（P2-10 不串年度）
  3. 无 year → 返回全部 workspace 项目
  4. projects[] 含 counts/text+image+... 五类、files[]、safe_name

跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_tree.py [BASE_URL]
"""
import json
import sys
import os
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
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def get(url):
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


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


def make_project(name, period):
    st, r = req("POST", f"{BASE}/api/audit/projects", {"name": name, "audit_period": period})
    pid = r.get("project", {}).get("id", "")
    if pid and finalize_chain(pid):
        return pid
    return None


def main():
    global PASS, FAIL
    print(f"[test] P2-5 年度项目树 目标 {BASE}\n")

    tag = uuid.uuid4().hex[:6]
    # 项目 A：2026，传 2 text + 1 image
    pidA = make_project("P2-tree-A-{}".format(tag), "2026-01-01至2026-06-30")
    # 项目 B：2025
    pidB = make_project("P2-tree-B-{}".format(tag), "2025年度预算执行")
    check("创建并 finalize 项目 A(2026) + B(2025)", bool(pidA) and bool(pidB))
    if not (pidA and pidB):
        print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
        return 1

    upload(pidA, "a1.pdf", b"%PDF a1", "application/pdf")
    upload(pidA, "a2.docx", b"PK a2 docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    upload(pidA, "img.png", b"\x89PNG img", "image/png")
    upload(pidB, "b1.xlsx", b"PK b1 xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # ① year=2026 → 只含 A，counts.text=2 image=1
    st, r = get(f"{BASE}/api/audit/workspace/tree?year=2026")
    projs = r.get("projects", [])
    pids_2026 = {p.get("project_id") for p in projs}
    check("?year=2026 含项目 A", pidA in pids_2026, str(pids_2026))
    check("?year=2026 不含项目 B(2025)（P2-10 不串年度）", pidB not in pids_2026, str(pids_2026))
    nodeA = next((p for p in projs if p["project_id"] == pidA), None)
    if nodeA:
        c = nodeA.get("counts", {})
        check("A counts.text=2", c.get("text") == 2, str(c))
        check("A counts.image=1", c.get("image") == 1, str(c))
        check("A counts.audio/video/other=0", c.get("audio") == 0 and c.get("video") == 0 and c.get("other") == 0)
        check("A files[] 含 3 条", len(nodeA.get("files", [])) == 3, str(len(nodeA.get("files", []))))
        check("A 含 safe_name", bool(nodeA.get("safe_name")))
        check("A audit_year=2026", nodeA.get("audit_year") == "2026")

    # ② year=2025 → 只含 B
    st, r = get(f"{BASE}/api/audit/workspace/tree?year=2025")
    pids_2025 = {p.get("project_id") for p in r.get("projects", [])}
    check("?year=2025 含项目 B", pidB in pids_2025, str(pids_2025))
    check("?year=2025 不含项目 A(2026)", pidA not in pids_2025, str(pids_2025))
    nodeB = next((p for p in r.get("projects", []) if p["project_id"] == pidB), None)
    if nodeB:
        check("B counts.text=1（excel 归 text）", nodeB.get("counts", {}).get("text") == 1, str(nodeB.get("counts")))

    # ③ 无 year → 全部 workspace 项目（含 A 和 B）
    st, r = get(f"{BASE}/api/audit/workspace/tree")
    pids_all = {p.get("project_id") for p in r.get("projects", [])}
    check("无 year 含 A 和 B", pidA in pids_all and pidB in pids_all, str(pids_all))

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
