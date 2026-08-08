r"""Phase5 切片3 验收：P5-5 字段筛选（白名单列防注入）

需 backend 运行。DB fixture 造 2 项目插 data_contracts 行（不同 party_a/amount/sign_date），
HTTP + DataService 双层验：
  - ① 等于筛选 ?party_a=甲A → 仅命中行（+ 跨项目隔离：不含 pidB 同名行）
  - ② 金额范围 ?amount_min=&amount_max= → 区间内行
  - ③ 日期范围 ?date_from=&date_to= → 区间内行
  - ④ 组合筛选 ?party_a=&amount_min= → 交集
  - ⑤ 非白名单列 ?非白名单=foo → 静默忽略（返回全部，不报错）
  - ⑥ 注入串 ?party_a=' OR '1'='1 → 参数化字面匹配 → 0 行（无扩展）
  - ⑦ parse_query_filters 单元：白名单/非白名单/金额解析失败忽略

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice3.py [BASE_URL]
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlencode

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import insert, execute  # noqa: E402
from services.data_service import parse_query_filters  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def get(path, params=None):
    url = f"{BASE}{path}"
    if params:
        url += "?" + urlencode(params)
    r = urllib.request.Request(url, method="GET")
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
    print(f"[test] Phase5 切片3：P5-5 字段筛选 目标 {BASE}\n")

    pidA = "p5s3a_{}".format(uuid.uuid4().hex[:8])
    pidB = "p5s3b_{}".format(uuid.uuid4().hex[:8])
    for p in (pidA, pidB):
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
        execute(
            "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (p, "P5s3"), database="tt",
        )

    # pidA: 3 行（甲A×2 不同金额/日期；乙B×1 大金额）；pidB: 1 行（与 C-2 同名同额，验隔离）
    rows_spec = [
        (pidA, "甲A", 100,  "2024-01-10", "C-1"),
        (pidA, "甲A", 200,  "2024-06-15", "C-2"),
        (pidA, "乙B", 999,  "2024-12-20", "C-3"),
        (pidB, "甲A", 200,  "2024-06-15", "C-4"),
    ]
    for pid, pa, amt, sd, no in rows_spec:
        insert(
            "INSERT INTO data_contracts (project_id,party_a,party_b,amount,sign_date,contract_no) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (pid, pa, "乙", amt, sd, no), database="tt",
        )

    base = f"/api/audit/projects/{pidA}/data/data_contracts/rows"
    try:
        # ═══ ① 等于筛选 ═══
        print("── ① ?party_a=甲A（等于筛选 + 跨项目隔离）──")
        st, r = get(base, {"party_a": "甲A"})
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("① 命中 C-1/C-2（甲A）", set(nos) == {"C-1", "C-2"}, str(nos))
        check("① 不含 pidB 的 C-4（隔离）", "C-4" not in nos, str(nos))
        check("① 不含乙B 的 C-3", "C-3" not in nos, str(nos))

        # ═══ ② 金额范围 ═══
        print("\n── ② ?amount_min=150&amount_max=250（金额范围）──")
        st, r = get(base, {"amount_min": 150, "amount_max": 250})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("② 仅 C-2（amount=200 落在 [150,250]）", set(nos) == {"C-2"}, str(nos))

        # ② 仅下界
        st, r = get(base, {"amount_min": 150})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("② amount_min=150 → C-2/C-3（200,999 均 ≥150）", set(nos) == {"C-2", "C-3"}, str(nos))

        # ═══ ③ 日期范围 ═══
        print("\n── ③ ?date_from=2024-06-01&date_to=2024-12-31（日期范围）──")
        st, r = get(base, {"date_from": "2024-06-01", "date_to": "2024-12-31"})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("③ C-2/C-3（sign_date 06-15/12-20 落在区间）", set(nos) == {"C-2", "C-3"}, str(nos))

        # ═══ ④ 组合筛选（交集）═══
        print("\n── ④ ?party_a=甲A&amount_min=150（组合交集）──")
        st, r = get(base, {"party_a": "甲A", "amount_min": 150})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("④ 仅 C-2（甲A 且 amount≥150）", set(nos) == {"C-2"}, str(nos))

        # ═══ ⑤ 非白名单列忽略 ═══
        print("\n── ⑤ ?非白名单列=foo（静默忽略）──")
        st, r = get(base, {"raw_text": "foo", "id": "1", "不存在的列": "bar"})
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("⑤ 非白名单忽略 → 返回项目全部 3 行", set(nos) == {"C-1", "C-2", "C-3"}, str(nos))

        # ═══ ⑥ 注入串（参数化字面匹配）═══
        print("\n── ⑥ ?party_a=' OR '1'='1（注入隔离）──")
        st, r = get(base, {"party_a": "' OR '1'='1"})
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("⑥ 注入串字面匹配 → 0 行（未扩展结果集）", len(nos) == 0, str(nos))

        # ═══ ⑦ parse_query_filters 单元 ═══
        print("\n── ⑦ parse_query_filters 单元 ──")
        flt = parse_query_filters("data_contracts", {
            "party_a": "甲A",          # 白名单 → eq
            "raw_text": "foo",         # 非白名单 → 忽略
            "amount_min": "150",       # → float
            "amount_max": "abc",       # 非数字 → 忽略
            "date_from": "2024-01-01", # → 直传
            "page": "1",               # 分页键 → 忽略
        })
        check("⑦ eq 含 party_a", flt["eq"].get("party_a") == "甲A", str(flt))
        check("⑦ eq 不含 raw_text", "raw_text" not in flt["eq"], str(flt))
        check("⑦ amount_min=150.0", flt.get("amount_min") == 150.0, str(flt))
        check("⑦ amount_max 非数字 → 缺失", "amount_max" not in flt, str(flt))
        check("⑦ date_from 直传", flt.get("date_from") == "2024-01-01", str(flt))
        check("⑦ page 被忽略", "page" not in flt and "page" not in flt["eq"], str(flt))
    finally:
        try:
            for p in (pidA, pidB):
                execute("DELETE FROM data_contracts WHERE project_id=%s", (p,), database="tt")
                execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
            print("\n[cleanup] 已删临时 data_contracts/projects")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片3 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
