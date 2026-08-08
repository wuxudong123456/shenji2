r"""Phase5 切片2 验收：P5-2 表统计 / P5-3 行查询双模式 / P5-4 强制 project_id

需 backend 运行 + M005 已 migrate（data_procurements/data_interviews 表存在）。
DB fixture 造 2 项目各插 data_contracts 行，HTTP + DataService 双层验：
  - ① 8表统计 项目级（GET /projects/<pid>/data → 8 表，data_contracts 仅本项目行数）
  - ② 8表统计 全局（GET /data/tables → 8 表，data_contracts 合计行数）
  - ③ 全局浏览行查询（GET /data/<table>/rows → 200，跨项目返回，硬 cap 200）
  - ④ 项目分析行查询（GET /projects/<pid>/data/<table>/rows → 200，仅本项目，跨项目隔离）
  - ⑤ 跨项目隔离（pidB 不含 A 的行）
  - ⑥ DataService 层强制 project_id：require_project=True + 空/None → ProjectIDRequiredError
  - ⑦ 表名非法 → 400（全局 + 项目级）
  - ⑧ data_procurements 新表端到端（budget_amount Decimal→float）

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice2.py [BASE_URL]
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import insert, execute  # noqa: E402
from services.data_service import (  # noqa: E402
    list_rows, ProjectIDRequiredError,
)

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def req(method, url):
    r = urllib.request.Request(url, method=method)
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
    global PASS, FAIL
    st, _ = req("GET", f"{BASE}/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase5 切片2：DataService 双模式 目标 {BASE}\n")

    pidA = "p5s2a_{}".format(uuid.uuid4().hex[:8])
    pidB = "p5s2b_{}".format(uuid.uuid4().hex[:8])
    for p in (pidA, pidB):
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
        execute(
            "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (p, "P5s2"), database="tt",
        )

    # data_contracts: A 2行(amount 100,200), B 1行(amount 999)
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no) "
        "VALUES (%s,%s,%s,%s,%s)",
        (pidA, "甲A", "乙A", 100, "C-A1"), database="tt",
    )
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no) "
        "VALUES (%s,%s,%s,%s,%s)",
        (pidA, "甲A2", "乙A2", 200, "C-A2"), database="tt",
    )
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no) "
        "VALUES (%s,%s,%s,%s,%s)",
        (pidB, "甲B", "乙B", 999, "C-B1"), database="tt",
    )
    # data_procurements: A 1行（新表端到端）
    insert(
        "INSERT INTO data_procurements (project_id,subject_name,supplier,budget_amount) "
        "VALUES (%s,%s,%s,%s)",
        (pidA, "采购标的A", "供应商A", 500000), database="tt",
    )

    try:
        # ═══ ① 8表统计 项目级 ═══
        print("── ① GET /projects/<pidA>/data（项目级 8 表统计）──")
        st, r = req("GET", f"{BASE}/api/audit/projects/{pidA}/data")
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        tables = r.get("tables", [])
        check("8 张表", len(tables) == 8, f"{len(tables)} 表: {[t['table'] for t in tables]}")
        check("含 data_procurements",
              any(t["table"] == "data_procurements" for t in tables),
              str([t["table"] for t in tables]))
        check("含 data_interviews",
              any(t["table"] == "data_interviews" for t in tables),
              str([t["table"] for t in tables]))
        dc = next((t for t in tables if t["table"] == "data_contracts"), {})
        check("① data_contracts 项目A = 2 行", dc.get("rows") == 2, str(dc))
        dproc = next((t for t in tables if t["table"] == "data_procurements"), {})
        check("① data_procurements 项目A = 1 行", dproc.get("rows") == 1, str(dproc))

        # ═══ ② 8表统计 全局 ═══
        print("\n── ② GET /data/tables（全局 8 表统计）──")
        st, r = req("GET", f"{BASE}/api/audit/data/tables")
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        gtables = r.get("tables", [])
        check("8 张表", len(gtables) == 8, f"{len(gtables)}")
        gdc = next((t for t in gtables if t["table"] == "data_contracts"), {})
        check("② data_contracts 全局 ≥ 3 行（A2+B1）", gdc.get("rows", 0) >= 3, str(gdc))

        # ═══ ③ 全局浏览行查询（跨项目）═══
        print("\n── ③ GET /data/data_contracts/rows（全局浏览）──")
        st, r = req("GET", f"{BASE}/api/audit/data/data_contracts/rows?per_page=200")
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        rows = r.get("rows", [])
        pids_in = {row.get("project_id") for row in rows}
        check("③ 全局返回含 A 和 B（跨项目）",
              pidA in pids_in and pidB in pids_in, str(pids_in))
        check("③ per_page 硬 cap ≤ 200",
              "per_page" in r and r["per_page"] <= 200, str(r.get("per_page")))

        # ═══ ④ 项目分析行查询（隔离）═══
        print("\n── ④ GET /projects/<pidA>/data/data_contracts/rows（项目隔离）──")
        st, r = req("GET", f"{BASE}/api/audit/projects/{pidA}/data/data_contracts/rows")
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        check("④ project_id 回显 = pidA", r.get("project_id") == pidA, str(r.get("project_id")))
        prows = r.get("rows", [])
        check("④ 仅返回 A 的行（2 条）",
              len(prows) == 2, f"{len(prows)} 行: {[rw.get('contract_no') for rw in prows]}")
        check("④ 全部行 project_id=pidA",
              all(row.get("project_id") == pidA for row in prows),
              str([rw.get("project_id") for rw in prows]))
        check("④ 不含 B 的 contract_no C-B1",
              all(row.get("contract_no") != "C-B1" for row in prows),
              str([rw.get("contract_no") for rw in prows]))

        # ═══ ⑤ 跨项目隔离（pidB 不含 A 的行）═══
        print("\n── ⑤ 跨项目隔离（pidB 不含 A 的行）──")
        st, r = req("GET", f"{BASE}/api/audit/projects/{pidB}/data/data_contracts/rows")
        check("HTTP 200", st == 200, f"{st}")
        brows = r.get("rows", [])
        check("⑤ pidB 仅 B 的行（1 条）", len(brows) == 1, f"{len(brows)} 行")
        check("⑤ 无 A 的 contract_no 串入",
              all(row.get("contract_no") not in ("C-A1", "C-A2") for row in brows),
              str([rw.get("contract_no") for rw in brows]))

        # ═══ ⑥ DataService 层强制 project_id（单元）═══
        print("\n── ⑥ DataService require_project 强制 ──")
        raised_none = False
        try:
            list_rows("data_contracts", project_id=None, require_project=True)
        except ProjectIDRequiredError:
            raised_none = True
        check("⑥ require_project + project_id=None → ProjectIDRequiredError", raised_none)
        raised_empty = False
        try:
            list_rows("data_contracts", project_id="", require_project=True)
        except ProjectIDRequiredError:
            raised_empty = True
        check("⑥ require_project + project_id='' → ProjectIDRequiredError", raised_empty)
        # require_project=False + None 不抛（全局浏览合法）
        try:
            list_rows("data_contracts", project_id=None, require_project=False, per_page=1)
            ok_global = True
        except Exception:
            ok_global = False
        check("⑥ require_project=False + None 不抛（全局浏览合法）", ok_global)

        # ═══ ⑦ 表名非法 → 400 ═══
        print("\n── ⑦ 表名非法 → 400 ──")
        st, r = req("GET", f"{BASE}/api/audit/data/data_evil/rows")
        check("⑦ 非法表全局 → 400", st == 400, f"{st} {str(r)[:120]}")
        st, r = req("GET", f"{BASE}/api/audit/projects/{pidA}/data/data_evil/rows")
        check("⑦ 非法表项目级 → 400", st == 400, f"{st} {str(r)[:120]}")

        # ═══ ⑧ data_procurements 新表端到端 ═══
        print("\n── ⑧ data_procurements 新表行查询 ──")
        st, r = req("GET", f"{BASE}/api/audit/projects/{pidA}/data/data_procurements/rows")
        check("⑧ HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        prows8 = r.get("rows", [])
        check("⑧ 返回 A 的采购行（1 条）", len(prows8) == 1, str(prows8)[:140])
        check("⑧ budget_amount=500000（Decimal→float）",
              prows8 and prows8[0].get("budget_amount") == 500000.0,
              str(prows8[0] if prows8 else {}))
    finally:
        try:
            for p in (pidA, pidB):
                execute("DELETE FROM data_contracts WHERE project_id=%s", (p,), database="tt")
            execute("DELETE FROM data_procurements WHERE project_id=%s", (pidA,), database="tt")
            for p in (pidA, pidB):
                execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
            print("\n[cleanup] 已删临时 data_contracts/data_procurements/projects")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片2 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
