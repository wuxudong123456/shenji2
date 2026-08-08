r"""Phase5 切片6 验收：P5-9 行溯源（复用 Phase4）+ P5-10 跨项目隔离

需 backend 运行。
P5-9（零代码，复用 Phase4 /traces/data_row）：data_* 行带 document_trace_id，
GET /traces/data_row/<row_id>?table= 返回 行→trace→chunk 链。本切片只加测试覆盖。
P5-10（DataService 隔离）：项目分析查询强制 WHERE project_id，跨项目不串。

验：
  - ① P5-9：真实 data_contracts 行（document_trace_id）→ /traces/data_row 返回
        refs + field_sources，chunk 文本透传，has_page=True
  - ② P5-9：chunk→superseded → 对应 field_source expired=True（留痕不删，复用 P4-10）
  - ③ P5-10：跨项目隔离——两项目各插 party_a=同甲 同值行，pidA 查询只返回 pidA 行
  - ④ P5-10：重叠筛选值下仍隔离（?party_a=同甲 在 pidA 不返回 pidB 行）
  - ⑤ P5-10：全局浏览不暴露 raw_text（大字段裁剪，复用 P5-6）

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_slice6.py [BASE_URL]
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
from services import evidence_service as es  # noqa: E402

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
    print(f"[test] Phase5 切片6：P5-9 溯源 + P5-10 隔离 目标 {BASE}\n")

    pidA = "p5s6a_{}".format(uuid.uuid4().hex[:8])
    pidB = "p5s6b_{}".format(uuid.uuid4().hex[:8])
    for p in (pidA, pidB):
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
        execute(
            "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (p, "P5s6"), database="tt",
        )

    # ── P5-9 溯源 fixture：trace + chunk + data_contracts 行（带 document_trace_id）──
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,2,'done')",
        (pidA, "src.pdf", "bk", "p/src.pdf"), database="tt",
    )
    cA = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,bbox,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pidA, "cA6", "text", "[1]", "[10,20,100,200]", "溯源甲方 金额一百万", 2), database="tt",
    )
    data_row_id = insert(
        "INSERT INTO data_contracts (project_id,party_a,amount,document_trace_id) "
        "VALUES (%s,%s,%s,%s)",
        (pidA, "溯源甲方", 1000000, trace_id), database="tt",
    )
    insert(
        "INSERT INTO audit_field_sources (project_id,table_name,row_id,field_name,chunk_id,ocr_version) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pidA, "data_contracts", data_row_id, "party_a", cA, 2), database="tt",
    )
    es.add_ref(pidA, "data_row", data_row_id, "document_chunk", cA,
               document_id=trace_id, quote="溯源甲方", page_number=1)

    # ── P5-10 隔离 fixture：两项目同 party_a=同甲 同 amount ──
    ridAX = insert(
        "INSERT INTO data_contracts (project_id,party_a,amount,contract_no,raw_text) "
        "VALUES (%s,%s,%s,%s,%s)",
        (pidA, "同甲", 100, "AX", "A项目原文" * 30), database="tt",
    )
    ridBX = insert(
        "INSERT INTO data_contracts (project_id,party_a,amount,contract_no,raw_text) "
        "VALUES (%s,%s,%s,%s,%s)",
        (pidB, "同甲", 100, "BX", "B项目原文" * 30), database="tt",
    )

    try:
        # ═══ ① P5-9 行溯源链 ═══
        print("── ① P5-9 /traces/data_row 行→trace→chunk ──")
        st, r = get(f"/api/audit/traces/data_row/{data_row_id}", {"table": "data_contracts"})
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        check("① refs 1 条", len(r.get("refs", [])) == 1, str(r.get("refs"))[:140])
        check("① field_sources 1 条", len(r.get("field_sources", [])) == 1, str(r.get("field_sources"))[:160])
        fs = r.get("field_sources", [{}])[0]
        check("① party_a.chunk_id=cA", fs.get("chunk_id") == cA, str(fs))
        check("① chunk 文本透传", fs.get("chunk", {}).get("text") == "溯源甲方 金额一百万", str(fs))
        check("① has_page=True（page_nums=[1]）", fs.get("has_page") is True, str(fs))
        check("① expired=False（cA active）", fs.get("expired") is False, str(fs))

        # ═══ ② P5-9 chunk→superseded → expired ═══
        print("\n── ② cA→superseded → field_source expired ──")
        execute("UPDATE audit_document_chunks SET status='superseded' WHERE id=%s", (cA,), database="tt")
        st, r = get(f"/api/audit/traces/data_row/{data_row_id}", {"table": "data_contracts"})
        fs2 = r.get("field_sources", [{}])[0]
        check("② party_a.expired=True（cA superseded）", fs2.get("expired") is True, str(fs2))
        check("② 仍在结果（留痕不删）", fs2.get("field_name") == "party_a", str(fs2))

        # ═══ ③ P5-10 跨项目隔离 ═══
        print("\n── ③ P5-10 跨项目隔离（同 party_a 不串）──")
        st, r = get(f"/api/audit/projects/{pidA}/data/data_contracts/rows")
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("③ pidA 含 AX", "AX" in nos, str(nos))
        check("③ pidA 不含 BX（隔离）", "BX" not in nos, str(nos))
        st, r = get(f"/api/audit/projects/{pidB}/data/data_contracts/rows")
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("③ pidB 含 BX", "BX" in nos, str(nos))
        check("③ pidB 不含 AX（隔离）", "AX" not in nos, str(nos))

        # ═══ ④ P5-10 重叠筛选值下仍隔离 ═══
        print("\n── ④ P5-10 重叠筛选 ?party_a=同甲 仍隔离 ──")
        st, r = get(f"/api/audit/projects/{pidA}/data/data_contracts/rows", {"party_a": "同甲"})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("④ pidA+同甲 → 仅 AX（不含 BX）", set(nos) == {"AX"}, str(nos))

        # ═══ ⑤ P5-10 全局浏览不暴露 raw_text ═══
        print("\n── ⑤ P5-10 全局浏览大字段裁剪 ──")
        st, r = get("/api/audit/data/data_contracts/rows", {"per_page": 50})
        check("HTTP 200", st == 200, f"{st}")
        check("⑤ 全局行无 raw_text 键",
              all("raw_text" not in row for row in r.get("rows", [])),
              str([("raw_text" in row) for row in r.get("rows", [])[:5]]))
    finally:
        try:
            execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pidA,), database="tt")
            execute("DELETE FROM audit_source_refs WHERE project_id=%s", (pidA,), database="tt")
            execute("DELETE FROM audit_document_chunks WHERE trace_id=%s", (trace_id,), database="tt")
            execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pidA,), database="tt")
            for p in (pidA, pidB):
                execute("DELETE FROM data_contracts WHERE project_id=%s", (p,), database="tt")
                execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")
            print("\n[cleanup] 已删临时 trace/chunk/field_sources/refs/data/projects")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片6 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
