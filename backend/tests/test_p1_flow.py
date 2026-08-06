"""P1-4..P1-9 流程验收脚本（Phase 1：target-scope / items 保存 / finalize 幂等）

用法：cd backend && .venv\Scripts\python.exe tests\test_p1_flow.py [BASE_URL]
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
    print(f"[test] P1-4..P1-9 流程验收 目标 {BASE}\n")

    # ① 创建项目 → draft/basic
    st, r = req("POST", f"{BASE}/api/audit/projects",
                {"name": "P1流程验收", "audit_period": "2026-01-01至2026-06-30"})
    pid = r.get("project", {}).get("id", "")
    check("创建项目 draft+basic", st == 200 and r["project"]["status"] == "draft"
          and r["project"]["setup_stage"] == "basic", str(r)[:120])

    # ② target-scope → 推进到 target_scope
    st, r = req("PUT", f"{BASE}/api/audit/projects/{pid}/target-scope",
                {"scope": "范围A", "extend_unit": "延伸B", "audit_focus": ["重点1"]})
    check("target-scope 推进+持久化", st == 200 and r["project"]["setup_stage"] == "target_scope"
          and r["project"]["scope"] == "范围A" and r["project"]["extend_unit"] == "延伸B", str(r)[:120])

    # ③ 保存事项 → 推进到 items
    st, r = req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
                {"audit_items": [{"title": "事项1", "priority": "高"}]})
    check("保存事项推进 items", st == 200 and r.get("count") == 1, str(r)[:120])

    # ④ 乐观锁：错误 expected_update_time → 409
    st, r = req("PUT", f"{BASE}/api/audit/projects/{pid}/items",
                {"audit_items": [{"title": "事项1"}], "expected_update_time": "1999-01-01T00:00:00"})
    check("乐观锁 409", st == 409, f"st={st}")

    # ⑤ finalize → active/workspace
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    check("finalize 激活", st == 200 and r["project"]["status"] == "active"
          and r["project"]["setup_stage"] == "workspace", str(r)[:150])

    # ⑥ finalize 幂等
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid}/workspace/finalize", {})
    check("finalize 幂等", st == 200 and r.get("message") == "已激活，幂等返回", str(r)[:120])

    # ⑦ 未完成前置 finalize → 409
    st, r = req("POST", f"{BASE}/api/audit/projects", {"name": "P1流程验收-无事项"})
    pid2 = r.get("project", {}).get("id", "")
    st, r = req("POST", f"{BASE}/api/audit/projects/{pid2}/workspace/finalize", {})
    check("未完成前置 finalize 409", st == 409, f"st={st}")

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
