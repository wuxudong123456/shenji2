r"""Phase5 切片4 验收：P5-6 大字段裁剪 + 游标分页 + 超时保护

需 backend 运行。DB fixture 造 pidA 5 行 + pidB 2 行（均含 raw_text），HTTP 验：
  - ① 默认裁剪：GET rows → 行无 raw_text 键（LARGE_FIELDS 剥离）
  - ② ?fields=raw_text → raw_text 显式取回
  - ③ 游标翻页连续性：per_page=2，after 逐页取 → 5 行不重不漏，next_cursor 正确（到尾 None）
  - ④ 游标+隔离：游标页只含 pidA 行，total 稳定=5（不含 pidB）
  - ⑤ 游标模式 page=None；OFFSET 模式 page=1

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice4.py [BASE_URL]
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
    print(f"[test] Phase5 切片4：P5-6 裁剪+游标 目标 {BASE}\n")

    pidA = "p5s4a_{}".format(uuid.uuid4().hex[:8])
    pidB = "p5s4b_{}".format(uuid.uuid4().hex[:8])
    for p in (pidA, pidB):
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
        execute(
            "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (p, "P5s4"), database="tt",
        )

    # pidA 5 行 + pidB 2 行，均含 raw_text
    for i in range(1, 6):
        insert(
            "INSERT INTO data_contracts (project_id,party_a,amount,contract_no,raw_text) "
            "VALUES (%s,%s,%s,%s,%s)",
            (pidA, f"甲{i}", i * 10, f"K-{i}", f"大段原文{i}" * 50), database="tt",
        )
    for i in range(1, 3):
        insert(
            "INSERT INTO data_contracts (project_id,party_a,amount,contract_no,raw_text) "
            "VALUES (%s,%s,%s,%s,%s)",
            (pidB, f"乙{i}", i * 10, f"B-{i}", "B项目原文" * 50), database="tt",
        )

    base = f"/api/audit/projects/{pidA}/data/data_contracts/rows"
    try:
        # ═══ ① 默认裁剪 raw_text ═══
        print("── ① 默认裁剪（无 raw_text 键）──")
        st, r = get(base, {"per_page": 3})
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        rows = r.get("rows", [])
        check("① 返回 3 行", len(rows) == 3, f"{len(rows)}")
        check("① 行均无 raw_text 键", all("raw_text" not in row for row in rows),
              str([list(row.keys())[:3] for row in rows]))
        check("① 仍有 contract_no（非大字段保留）", all("contract_no" in row for row in rows), "")

        # ═══ ② ?fields=raw_text 显式取回 ═══
        print("\n── ② ?fields=raw_text 取回 ──")
        st, r = get(base, {"per_page": 2, "fields": "raw_text"})
        rows = r.get("rows", [])
        check("② 行含 raw_text 键", all("raw_text" in row for row in rows), str([("raw_text" in row) for row in rows]))
        check("② raw_text 非空", all(row.get("raw_text") for row in rows), "")

        # ═══ ③ 游标翻页连续性 ═══
        print("\n── ③ 游标翻页（per_page=2，5 行不重不漏）──")
        seen = []
        after = None
        pages = 0
        next_cursor = None
        while True:
            params = {"per_page": 2}
            if after is not None:
                params["after"] = after
            st, r = get(base, params)
            rows = r.get("rows", [])
            pages += 1
            seen.extend(row.get("contract_no") for row in rows)
            # 游标模式 page=None
            if after is not None:
                check(f"③ 第{pages}页 page=None（游标模式）", r.get("page") is None, str(r.get("page")))
            next_cursor = r.get("next_cursor")
            if next_cursor is None or pages > 10:
                break
            after = next_cursor
        check(f"③ 游标翻 {pages} 页取尽", pages >= 3, f"pages={pages}")
        check("③ 5 行不重不漏", sorted(seen) == ["K-1", "K-2", "K-3", "K-4", "K-5"], str(sorted(seen)))
        check("③ 末页 next_cursor=None", next_cursor is None, str(next_cursor))

        # ═══ ④ 游标+隔离（不含 pidB）═══
        print("\n── ④ 游标+隔离（仅 pidA）──")
        check("④ 无 B- 行串入", all(not c.startswith("B-") for c in seen), str(seen))
        # total 稳定（每页都=5，不含 pidB 的 2 行）
        st, r = get(base, {"per_page": 2})
        check("④ total=5（pidA 全量，不含 pidB）", r.get("total") == 5, str(r.get("total")))

        # ═══ ⑤ OFFSET 模式 page=1 ═══
        print("\n── ⑤ OFFSET 模式 page=1 ──")
        st, r = get(base, {"per_page": 2, "page": 1})
        check("⑤ OFFSET 模式 page=1", r.get("page") == 1, str(r.get("page")))
        check("⑤ OFFSET 模式也有 next_cursor（可切入游标）", "next_cursor" in r, str(list(r.keys())))
    finally:
        try:
            for p in (pidA, pidB):
                execute("DELETE FROM data_contracts WHERE project_id=%s", (p,), database="tt")
                execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
            print("\n[cleanup] 已删临时 data_contracts/projects")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片4 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
