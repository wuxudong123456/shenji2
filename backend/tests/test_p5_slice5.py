r"""Phase5 切片5 验收：P5-7 质量检查 + P5-8 缺失字段

需 backend 运行。DB fixture 造 pidA（contracts 3 行含缺失/大额/小额 + procurements 2 行小额）
+ pidB（空项目），HTTP + DataService 双层验：
  - ① GET /quality 结构：8 表 + unit 含「元」
  - ② data_contracts 空值率：party_b 缺 2、amount 缺 0
  - ③ data_contracts 金额统计 min/max/count
  - ④ 金额单位告警：amount max>1e9 → 「疑似万元/亿元混入」
  - ⑤ 金额单位告警：procurements.budget_amount max<10 → 「疑似应为万元单位」
  - ⑥ GET /missing：data_contracts 缺失清单含 party_b/contract_no/sign_date
  - ⑦ 空项目（pidB）→ 各表 total=0

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice5.py [BASE_URL]
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import insert, execute  # noqa: E402
from services.data_service import quality_check, missing_check  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def get(path):
    r = urllib.request.Request(f"{BASE}{path}", method="GET")
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
    st, _ = get("/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase5 切片5：P5-7/P5-8 质量+缺失 目标 {BASE}\n")

    pidA = "p5s5a_{}".format(uuid.uuid4().hex[:8])
    pidB = "p5s5b_{}".format(uuid.uuid4().hex[:8])
    for p in (pidA, pidB):
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
        execute(
            "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (p, "P5s5"), database="tt",
        )

    # data_contracts: R1 全、R2 party_b缺+大额、R3 多缺+小额
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no,sign_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "甲A", "乙A", 5000000, "C-1", "2024-01-01"), database="tt",
    )
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no,sign_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "甲B", None, 2000000000, "C-2", "2024-02-02"), database="tt",
    )
    insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no,sign_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "甲C", None, 5, None, None), database="tt",
    )
    # data_procurements: P1/P2 小额（max<10 触发告警）
    insert(
        "INSERT INTO data_procurements (project_id,subject_name,supplier,budget_amount,contract_amount,bid_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "标的1", "供1", 3, 2, "2024-03-01"), database="tt",
    )
    insert(
        "INSERT INTO data_procurements (project_id,subject_name,supplier,budget_amount,contract_amount,bid_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "标的2", "供2", 5, None, None), database="tt",
    )

    try:
        # DataService 直接调用（精确断言）
        q = quality_check(pidA)
        qmap = {t["table"]: t for t in q["tables"]}
        m = missing_check(pidA)
        mmap = {t["table"]: t for t in m["tables"]}

        # ═══ ① quality 结构 ═══
        print("── ① quality 结构（8 表 + unit 元）──")
        check("① 返回 8 表", len(q["tables"]) == 8, str(len(q["tables"])))
        check("① unit 含「元」", "元" in q.get("unit", ""), q.get("unit"))

        # ═══ ② data_contracts 空值率 ═══
        print("\n── ② data_contracts 空值率 ──")
        dc = qmap["data_contracts"]
        check("② contracts total=3", dc.get("total") == 3, str(dc.get("total")))
        nullmap = {n["col"]: n["null"] for n in dc.get("nulls", [])}
        check("② party_b 缺 2", nullmap.get("party_b") == 2, str(nullmap))
        check("② amount 缺 0", nullmap.get("amount") == 0, str(nullmap))
        check("② contract_no 缺 1", nullmap.get("contract_no") == 1, str(nullmap))
        check("② sign_date 缺 1", nullmap.get("sign_date") == 1, str(nullmap))

        # ═══ ③ 金额统计 ═══
        print("\n── ③ data_contracts 金额统计 ──")
        amt = {a["col"]: a for a in dc.get("amounts", [])}
        check("③ amount.min=5", amt.get("amount", {}).get("min") == 5.0, str(amt.get("amount")))
        check("③ amount.max=2e9", amt.get("amount", {}).get("max") == 2000000000.0, str(amt.get("amount")))
        check("③ amount.count=3", amt.get("amount", {}).get("count") == 3, str(amt.get("amount")))
        check("③ amount.unit=元", amt.get("amount", {}).get("unit") == "元", str(amt.get("amount")))

        # ═══ ④ 金额单位告警 max>1e9 ═══
        print("\n── ④ 金额告警 max>1e9 ──")
        warns = amt.get("amount", {}).get("warnings", [])
        check("④ amount 触发「疑似万元/亿元混入」",
              any("疑似万元/亿元混入" in w for w in warns), str(warns))

        # ═══ ⑤ 金额单位告警 max<10 ═══
        print("\n── ⑤ 金额告警 max<10 ──")
        dproc = qmap["data_procurements"]
        pamt = {a["col"]: a for a in dproc.get("amounts", [])}
        pwarns = pamt.get("budget_amount", {}).get("warnings", [])
        check("⑤ budget_amount(max=5)<10 触发「疑似应为万元单位」",
              any("疑似应为万元单位" in w for w in pwarns), str(pwarns))

        # ═══ ⑥ missing 清单 ═══
        print("\n── ⑥ data_contracts 缺失清单 ──")
        dcmiss = {x["col"] for x in mmap["data_contracts"].get("missing", [])}
        check("⑥ missing 含 party_b", "party_b" in dcmiss, str(dcmiss))
        check("⑥ missing 含 contract_no", "contract_no" in dcmiss, str(dcmiss))
        check("⑥ missing 含 sign_date", "sign_date" in dcmiss, str(dcmiss))
        check("⑥ missing 不含 amount（无缺失）", "amount" not in dcmiss, str(dcmiss))

        # ═══ ⑦ HTTP 路由 ═══
        print("\n── ⑦ HTTP GET /quality + /missing ──")
        st, r = get(f"/api/audit/projects/{pidA}/data/quality")
        check("⑦ /quality HTTP 200", st == 200, f"{st} {str(r)[:120]}")
        check("⑦ /quality success=True", r.get("success") is True, str(r)[:120])
        check("⑦ /quality 8 表", len(r.get("tables", [])) == 8, str(len(r.get("tables", []))))
        st, r = get(f"/api/audit/projects/{pidA}/data/missing")
        check("⑦ /missing HTTP 200", st == 200, f"{st} {str(r)[:120]}")
        check("⑦ /missing success=True", r.get("success") is True, str(r)[:120])

        # ═══ ⑧ 空项目（pidB）═══
        print("\n── ⑧ 空项目 quality（pidB）──")
        qb = quality_check(pidB)
        check("⑧ 空项目各表 total=0", all(t["total"] == 0 for t in qb["tables"]),
              str([(t["table"], t["total"]) for t in qb["tables"]]))
    finally:
        try:
            execute("DELETE FROM data_contracts WHERE project_id=%s", (pidA,), database="tt")
            execute("DELETE FROM data_procurements WHERE project_id=%s", (pidA,), database="tt")
            for p in (pidA, pidB):
                execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
            print("\n[cleanup] 已删临时 data_*/projects")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片5 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
