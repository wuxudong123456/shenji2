r"""Phase4 切片5 验收：P4-8 trace 查询接口 + P4-10 推导

需 backend 运行。DB fixture 插 chunks/field_sources/source_refs，HTTP GET /traces/ 验：
  - happy：data_row 行 → refs[] + field_sources[]，chunk.text/page_nums 透传，has_page=True，expired=False
  - 无页码：chunk page_nums=NULL → field_source has_page=False（待人工核实，决策6）
  - chunk_id=NULL：降级字段 → chunk=None，has_page=False
  - P4-10：chunk→superseded → 对应 field_source + ref expired=True（留痕不删）
  - ?table= 过滤；404 不存在 result

用法：cd backend && .venv\Scripts\python.exe tests\test_p4_slice5.py [BASE_URL]
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import insert, execute  # noqa: E402
from services import evidence_service as es  # noqa: E402

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
    print(f"[test] Phase4 切片5：P4-8 trace 查询接口 目标 {BASE}\n")

    pid = "p4s5_{}".format(uuid.uuid4().hex[:8])
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
        (pid, "P4s5"), database="tt",
    )
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,2,'done')",
        (pid, "tr.pdf", "bk", "p/tr.pdf"), database="tt",
    )
    # chunkA：有页码 active；chunkB：无页码 active；chunkC：有页码（后续标 superseded）
    cA = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,bbox,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "cA", "text", "[1]", "[10,20,100,200]", "甲方A 金额100", 2), database="tt",
    )
    cB = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "cB", "text", None, "无页码切片", 2), database="tt",
    )
    cC = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "cC", "text", "[3]", "将失效的切片", 2), database="tt",
    )
    row_id = 5555
    table = "data_contracts"
    # field_sources：party_a→cA(有页码)；note→cB(无页码)；other→NULL(降级)；stale→cC(后续superseded)
    for fn, cid in [("party_a", cA), ("note", cB), ("other", None), ("stale", cC)]:
        insert(
            "INSERT INTO audit_field_sources (project_id,table_name,row_id,field_name,chunk_id,ocr_version) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (pid, table, row_id, fn, cid, 2), database="tt",
        )
    # source_ref：document_chunk → cA（后续 cA 不动，cC superseded 测另一路）
    es.add_ref(pid, "data_row", row_id, "document_chunk", cA,
               document_id=trace_id, quote="甲方A", page_number=1)

    try:
        # ═══ ① happy：完整溯源链 ═══
        print("── ① GET /traces/data_row/5555（happy）──")
        st, r = req("GET", f"{BASE}/api/audit/traces/data_row/{row_id}?table={table}")
        check("HTTP 200", st == 200, f"{st} {str(r)[:140]}")
        check("success=True", r.get("success") is True, str(r)[:140])
        check("refs 1 条", len(r.get("refs", [])) == 1, str(r.get("refs"))[:140])
        check("field_sources 4 条", len(r.get("field_sources", [])) == 4, str(r.get("field_sources"))[:200])

        fs = {f["field_name"]: f for f in r.get("field_sources", [])}
        check("① party_a.chunk_id=cA", fs.get("party_a", {}).get("chunk_id") == cA, str(fs.get("party_a")))
        check("① party_a.chunk.text 透传",
              fs.get("party_a", {}).get("chunk", {}).get("text") == "甲方A 金额100", str(fs.get("party_a")))
        check("① party_a.has_page=True（page_nums=[1]）",
              fs.get("party_a", {}).get("has_page") is True, str(fs.get("party_a")))
        check("① party_a.expired=False",
              fs.get("party_a", {}).get("expired") is False, str(fs.get("party_a")))
        check("① note.has_page=False（page_nums=NULL）",
              fs.get("note", {}).get("has_page") is False, str(fs.get("note")))
        check("① other.chunk=None（chunk_id=NULL 降级）",
              fs.get("other", {}).get("chunk") is None, str(fs.get("other")))
        check("① other.has_page=False", fs.get("other", {}).get("has_page") is False, str(fs.get("other")))
        check("① ref.quote 落库",
              r["refs"][0].get("quote") == "甲方A", str(r["refs"][0]))
        check("① ref.expired=False（cA active）",
              r["refs"][0].get("expired") is False, str(r["refs"][0]))

        # ═══ ② P4-10：cC→superseded → stale 字段 + 其 ref expired ═══
        print("\n── ② cC→superseded → expired 推导（留痕不删）──")
        execute("UPDATE audit_document_chunks SET status='superseded' WHERE id=%s", (cC,), database="tt")
        st, r = req("GET", f"{BASE}/api/audit/traces/data_row/{row_id}?table={table}")
        fs2 = {f["field_name"]: f for f in r.get("field_sources", [])}
        check("② stale.expired=True（cC superseded）",
              fs2.get("stale", {}).get("expired") is True, str(fs2.get("stale")))
        check("② stale 仍在结果里（留痕不删）", "stale" in fs2, str(list(fs2.keys())))
        check("② party_a.expired 仍 False（cA 未动）",
              fs2.get("party_a", {}).get("expired") is False, str(fs2.get("party_a")))

        # ③ ref 推导过期（cA 标 superseded）
        print("\n── ③ cA→superseded → ref expired ──")
        execute("UPDATE audit_document_chunks SET status='superseded' WHERE id=%s", (cA,), database="tt")
        st, r = req("GET", f"{BASE}/api/audit/traces/data_row/{row_id}?table={table}")
        check("③ ref.expired=True（cA superseded）",
              r["refs"][0].get("expired") is True, str(r["refs"][0]))
        check("③ party_a.expired=True（cA superseded）",
              any(f.get("field_name") == "party_a" and f.get("expired") for f in r.get("field_sources", [])),
              str(r.get("field_sources"))[:160])

        # ═══ ④ ?table= 过滤 + 不带 table ═══
        print("\n── ④ table 过滤 ──")
        st, r = req("GET", f"{BASE}/api/audit/traces/data_row/{row_id}?table=data_finance")
        check("④ 异表过滤 → field_sources 空（404 或 空）",
              st == 404 or len(r.get("field_sources", [])) == 0, f"{st} {str(r)[:140]}")

        # ═══ ⑤ 404 不存在 result ═══
        print("\n── ⑤ 404 不存在 result ──")
        st, r = req("GET", f"{BASE}/api/audit/traces/data_row/9999999?table=data_contracts")
        check("⑤ HTTP 404", st == 404, f"{st} {str(r)[:120]}")
    finally:
        try:
            execute("DELETE FROM audit_field_sources WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_source_refs WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_document_chunks WHERE trace_id=%s", (trace_id,), database="tt")
            execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
            print("\n[cleanup] 已删临时 project/trace/chunk/field_sources/refs")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片5 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
