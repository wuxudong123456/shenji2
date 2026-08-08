r"""Phase5 端到端验收：数据工坊全链（单项目 统计→行→筛选→游标→质量→缺失→溯源→全局）

需 backend 运行 + M005 已 migrate。一个项目造多表数据 + trace/chunk/field_sources，
走完数据工坊读侧全链，验证 Phase 5 整体可隔离地读。

用法：cd backend && .venv\Scripts\python.exe tests\test_p5_data.py [BASE_URL]
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


def main():
    global PASS, FAIL
    st, _ = get("/api/health")
    if st != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {st})")
        sys.exit(2)
    print(f"[test] Phase5 端到端：数据工坊全链 目标 {BASE}\n")

    pid = "p5e2e_{}".format(uuid.uuid4().hex[:8])
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
        (pid, "P5e2e"), database="tt",
    )

    # ── fixture：trace + 2 chunk + data_contracts×2 + data_procurements×1 + field_sources/ref ──
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,2,'done')",
        (pid, "e2e.pdf", "bk", "p/e2e.pdf"), database="tt",
    )
    c1 = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "c1", "text", "[1]", "建设局 合同金额300万", 2), database="tt",
    )
    c2 = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "c2", "text", "[2]", "建工集团", 2), database="tt",
    )
    row1 = insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no,sign_date,document_trace_id,raw_text) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (pid, "建设局", None, 3000000, "HT-001", "2024-05-01", trace_id, "原文" * 40), database="tt",
    )
    row2 = insert(
        "INSERT INTO data_contracts (project_id,party_a,party_b,amount,contract_no,sign_date,document_trace_id,raw_text) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (pid, "教育局", "建工集团", 500, None, "2024-06-01", trace_id, "原文" * 40), database="tt",
    )
    insert(
        "INSERT INTO data_procurements (project_id,subject_name,supplier,budget_amount,contract_amount,bid_date) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pid, "电脑采购", None, 8, 6, "2024-07-01"), database="tt",
    )
    insert(
        "INSERT INTO audit_field_sources (project_id,table_name,row_id,field_name,chunk_id,ocr_version) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pid, "data_contracts", row1, "party_a", c1, 2), database="tt",
    )
    insert(
        "INSERT INTO audit_field_sources (project_id,table_name,row_id,field_name,chunk_id,ocr_version) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (pid, "data_contracts", row2, "party_b", c2, 2), database="tt",
    )
    es.add_ref(pid, "data_row", row1, "document_chunk", c1, document_id=trace_id, quote="建设局", page_number=1)

    base = f"/api/audit/projects/{pid}/data/data_contracts/rows"
    try:
        # ═══ ① 表统计 ═══
        print("── ① 表统计 GET /projects/<pid>/data ──")
        st, r = get(f"/api/audit/projects/{pid}/data")
        check("HTTP 200", st == 200, f"{st}")
        tmap = {t["table"]: t["rows"] for t in r.get("tables", [])}
        check("8 张表", len(tmap) == 8, str(list(tmap)))
        check("① data_contracts=2", tmap.get("data_contracts") == 2, str(tmap))
        check("① data_procurements=1", tmap.get("data_procurements") == 1, str(tmap))

        # ═══ ② 行查询（项目模式）+ 裁剪 ═══
        print("\n── ② 行查询 + raw_text 裁剪 ──")
        st, r = get(base)
        check("HTTP 200", st == 200, f"{st}")
        check("② 返回 2 行", len(r.get("rows", [])) == 2, str(len(r.get("rows", []))))
        check("② 行无 raw_text", all("raw_text" not in row for row in r.get("rows", [])), "")

        # ═══ ③ 等于筛选 ═══
        print("\n── ③ 筛选 ?party_a=建设局 ──")
        st, r = get(base, {"party_a": "建设局"})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("③ 仅 HT-001", set(nos) == {"HT-001"}, str(nos))

        # ═══ ④ 金额范围筛选 ═══
        print("\n── ④ 金额范围 ?amount_min=1000000 ──")
        st, r = get(base, {"amount_min": 1000000})
        nos = [row.get("contract_no") for row in r.get("rows", [])]
        check("④ 仅 HT-001（3M≥1M，500<1M）", set(nos) == {"HT-001"}, str(nos))

        # ═══ ⑤ 游标翻页 ═══
        print("\n── ⑤ 游标翻页 per_page=1 ──")
        seen = []
        after = None
        for _ in range(5):
            params = {"per_page": 1}
            if after is not None:
                params["after"] = after
            st, r = get(base, params)
            rows = r.get("rows", [])
            if not rows:
                break
            seen.extend(row.get("contract_no") for row in rows)
            after = r.get("next_cursor")
            if after is None:
                break
        check("⑤ 游标取尽共 2 条（row2 contract_no=None 故按数量校验）", len(seen) == 2, f"seen={seen}")

        # ═══ ⑥ 质量检查 ═══
        print("\n── ⑥ 质量检查 GET /quality ──")
        st, r = get(f"/api/audit/projects/{pid}/data/quality")
        check("HTTP 200", st == 200, f"{st}")
        dc = next((t for t in r.get("tables", []) if t["table"] == "data_contracts"), {})
        nullmap = {n["col"]: n["null"] for n in dc.get("nulls", [])}
        check("⑥ party_b 缺 1", nullmap.get("party_b") == 1, str(nullmap))
        check("⑥ contract_no 缺 1", nullmap.get("contract_no") == 1, str(nullmap))
        dproc = next((t for t in r.get("tables", []) if t["table"] == "data_procurements"), {})
        pwarns = [w for a in dproc.get("amounts", []) for w in a.get("warnings", [])]
        check("⑥ procurements 触发 max<10 告警", any("疑似应为万元单位" in w for w in pwarns), str(pwarns))

        # ═══ ⑦ 缺失清单 ═══
        print("\n── ⑦ 缺失清单 GET /missing ──")
        st, r = get(f"/api/audit/projects/{pid}/data/missing")
        check("HTTP 200", st == 200, f"{st}")
        dcmiss = next((t for t in r.get("tables", []) if t["table"] == "data_contracts"), {})
        mcols = {x["col"] for x in dcmiss.get("missing", [])}
        check("⑦ missing 含 party_b/contract_no",
              {"party_b", "contract_no"} <= mcols, str(mcols))

        # ═══ ⑧ 行溯源 ═══
        print("\n── ⑧ 行溯源 GET /traces/data_row/<row1> ──")
        st, r = get(f"/api/audit/traces/data_row/{row1}", {"table": "data_contracts"})
        check("HTTP 200", st == 200, f"{st} {str(r)[:120]}")
        check("⑧ field_sources 非空", len(r.get("field_sources", [])) >= 1, str(r.get("field_sources"))[:120])
        check("⑧ chunk 文本透传",
              any(f.get("chunk", {}).get("text") == "建设局 合同金额300万" for f in r.get("field_sources", [])),
              str(r.get("field_sources"))[:160])

        # ═══ ⑨ 全局浏览 ═══
        print("\n── ⑨ 全局浏览 GET /data/tables + /data/<table>/rows ──")
        st, r = get("/api/audit/data/tables")
        check("⑨ 全局 8 表", len(r.get("tables", [])) == 8, str(len(r.get("tables", []))))
        st, r = get("/api/audit/data/data_contracts/rows", {"per_page": 200})
        check("⑨ 全局行查询 200", st == 200, f"{st}")
        check("⑨ 全局行无 raw_text", all("raw_text" not in row for row in r.get("rows", [])), "")
        check("⑨ 全局含本项目行",
              any(row.get("project_id") == pid for row in r.get("rows", [])),
              str([row.get("project_id") for row in r.get("rows", [])[:5]]))
    finally:
        try:
            execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_source_refs WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_document_chunks WHERE trace_id=%s", (trace_id,), database="tt")
            execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM data_procurements WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
            print("\n[cleanup] 已删临时 e2e 全量数据")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"Phase5 端到端 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
