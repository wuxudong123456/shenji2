r"""Phase4 切片3 验收：P4-7 EvidenceService（add_ref/get_refs/link_data_row_to_document）

DB fixture（project + trace + chunk 行），直调 evidence_service：
  - add_ref 写 audit_source_refs 往返（quote/page_number/bbox 落库）
  - get_refs 按 result 查；document_chunk 类 source 推导 expired（active→False）
  - chunk.status='superseded' → get_refs 同一 ref expired=True（P4-10 留痕不删）
  - link_data_row_to_document：data_row 行 → document_chunk 引用（带 chunk_id）；
    无 chunk_id 退化为 data_row→data_row 行级引用

用法：cd backend && .venv\Scripts\python.exe tests\test_p4_slice3.py
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, query, insert, execute  # noqa: E402
from services import evidence_service as es  # noqa: E402

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def _loads(v):
    if v is None:
        return None
    if isinstance(v, (list, dict)):
        return v
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def main():
    global PASS, FAIL
    print("[test] Phase4 切片3：P4-7 EvidenceService\n")

    pid = "p4s3_{}".format(uuid.uuid4().hex[:8])
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
        (pid, "P4s3"), database="tt",
    )
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,2,'done')",
        (pid, "ev.pdf", "bk", "p/ev.pdf"), database="tt",
    )
    # 一个 active chunk 行（document_chunk source 引用目标）
    chunk_id = insert(
        "INSERT INTO audit_document_chunks "
        "(trace_id,project_id,chunk_id,chunk_type,page_nums,bbox,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "ck1", "text", "[1]", "[10,20,100,200]", "原文片段", 2),
        database="tt",
    )

    try:
        # ═══ ① add_ref 往返 ═══
        print("── ① add_ref（document ← document_chunk）──")
        ref_id = es.add_ref(
            project_id=pid, result_type="document", result_id=trace_id,
            source_type="document_chunk", source_id=chunk_id,
            document_id=trace_id, file_name="ev.pdf", page_number=1,
            bbox=[10, 20, 100, 200], quote="原文片段", relation="supports",
        )
        check("add_ref 返回 id(int)", isinstance(ref_id, int) and ref_id > 0, str(ref_id))
        row = query_one(
            "SELECT result_type,result_id,source_type,source_id,document_id,"
            "file_name,page_number,bbox,quote,relation FROM audit_source_refs WHERE id=%s",
            (ref_id,), database="tt",
        )
        check("① result_type=document", row and row["result_type"] == "document", str(row)[:120])
        check("① result_id=str(trace_id)", row and row["result_id"] == str(trace_id), str(row)[:120])
        check("① source_type=document_chunk", row and row["source_type"] == "document_chunk", str(row)[:120])
        check("① source_id=str(chunk_id)", row and row["source_id"] == str(chunk_id), str(row)[:120])
        check("① document_id=trace_id", row and row["document_id"] == trace_id, str(row)[:120])
        check("① page_number=1", row and row["page_number"] == 1, str(row)[:120])
        check("① bbox JSON", _loads(row["bbox"]) == [10, 20, 100, 200], str(row)[:120])
        check("① quote 落库", row and row["quote"] == "原文片段", str(row)[:120])

        # ═══ ② get_refs + expired 推导（active → expired=False）═══
        print("\n── ② get_refs（chunk active → expired=False）──")
        refs = es.get_refs("document", trace_id)
        check("get_refs 返回 1 条", len(refs) == 1, str(refs)[:120])
        check("② expired=False（chunk active）", refs[0].get("expired") is False, str(refs[0])[:140])

        # ═══ ③ P4-10：chunk→superseded → 同一 ref expired=True（留痕不删）═══
        print("\n── ③ chunk→superseded → expired=True（P4-10 推导）──")
        execute("UPDATE audit_document_chunks SET status='superseded' WHERE id=%s",
                (chunk_id,), database="tt")
        refs2 = es.get_refs("document", trace_id)
        check("③ ref 行未删（留痕）", len(refs2) == 1, str(refs2)[:120])
        check("③ expired=True（chunk superseded）", refs2[0].get("expired") is True, str(refs2[0])[:140])

        # ═══ ④ link_data_row_to_document（带 chunk_id）═══
        print("\n── ④ link_data_row_to_document（data_row ← document_chunk）──")
        # 复原 chunk active 并加第二个 chunk 供行引用
        execute("UPDATE audit_document_chunks SET status='active' WHERE id=%s", (chunk_id,), database="tt")
        row_id_fake = 1234
        lid = es.link_data_row_to_document(
            pid, "data_contracts", row_id_fake, trace_id,
            chunk_id=chunk_id, quote="金额100元", page_number=1,
        )
        check("link 返回 id(int)", isinstance(lid, int) and lid > 0, str(lid))
        drefs = es.get_refs("data_row", row_id_fake)
        check("④ data_row ref 1 条", len(drefs) == 1, str(drefs)[:120])
        check("④ source_type=document_chunk", drefs[0]["source_type"] == "document_chunk", str(drefs[0])[:140])
        check("④ source_id=str(chunk_id)", drefs[0]["source_id"] == str(chunk_id), str(drefs[0])[:140])
        check("④ document_id=trace_id", drefs[0]["document_id"] == trace_id, str(drefs[0])[:140])
        check("④ quote 落库", drefs[0]["quote"] == "金额100元", str(drefs[0])[:140])

        # ═══ ⑤ link 无 chunk_id → 退化为行级引用（source=data_row）═══
        print("\n── ⑤ link 无 chunk_id → 行级引用 ──")
        row_id_fake2 = 5678
        lid2 = es.link_data_row_to_document(pid, "data_contracts", row_id_fake2, trace_id)
        drefs2 = es.get_refs("data_row", row_id_fake2)
        check("⑤ 无 chunk ref 1 条", len(drefs2) == 1, str(drefs2)[:120])
        check("⑤ source_type=data_row（退化）", drefs2[0]["source_type"] == "data_row", str(drefs2[0])[:140])
        check("⑤ document_id=trace_id 仍锚定", drefs2[0]["document_id"] == trace_id, str(drefs2[0])[:140])

        # ═══ ⑥ get_refs 不存在 result → [] ═══
        print("\n── ⑥ get_refs 空结果 ──")
        check("⑥ 不存在 result → []", es.get_refs("data_row", 9999999) == [])
    finally:
        try:
            execute("DELETE FROM audit_source_refs WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_document_chunks WHERE trace_id=%s", (trace_id,), database="tt")
            execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
            print("\n[cleanup] 已删临时 project/trace/chunk/refs")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片3 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
