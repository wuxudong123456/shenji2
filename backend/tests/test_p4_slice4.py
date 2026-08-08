r"""Phase4 切片4 验收：P4-5 字段→chunk 文本匹配 + P4-6 行→trace 引用

DB fixture（project+trace+2个真实chunk），构造 chunks_db 直调 _build_field_sources：
  - field_sources 覆盖列名（party_a/amount/sign_date）+ extra_fields->$.字段名（合同编号/采购方式）
  - 文本匹配（决策1）：值出现在 chunk.text → chunk_id 命中；不在 → NULL
  - 降级 chunks_db=[] → 全部 chunk_id=NULL（不伪造）
  - P4-6：link_data_row_to_document 落 data_row→trace 引用（get_refs 可查）

用法：cd backend && .venv\Scripts\python.exe tests\test_p4_slice4.py
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, query_one, insert, execute  # noqa: E402
from services.task_worker import _build_field_sources  # noqa: E402
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


CHUNK1_TEXT = "甲方A公司 合同金额100000元 签订日期2024-01-01"
CHUNK2_TEXT = "合同编号HT2024-001 附则条款"


def main():
    global PASS, FAIL
    print("[test] Phase4 切片4：P4-5/P4-6 字段匹配 + 行→trace 引用\n")

    pid = "p4s4_{}".format(uuid.uuid4().hex[:8])
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
        (pid, "P4s4"), database="tt",
    )
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status) "
        "VALUES (%s,%s,%s,%s,2,'done')",
        (pid, "fs.pdf", "bk", "p/fs.pdf"), database="tt",
    )
    c1 = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "ck1", "text", "[1]", CHUNK1_TEXT, 2), database="tt",
    )
    c2 = insert(
        "INSERT INTO audit_document_chunks (trace_id,project_id,chunk_id,chunk_type,page_nums,text,ocr_version,status) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,'active')",
        (trace_id, pid, "ck2", "text", "[2]", CHUNK2_TEXT, 2), database="tt",
    )
    chunks_db = [
        {"id": c1, "text": CHUNK1_TEXT, "page_nums": [1], "bbox": None},
        {"id": c2, "text": CHUNK2_TEXT, "page_nums": [2], "bbox": None},
    ]
    row_dict = {"party_a": "甲方A公司", "amount": "100000", "sign_date": "2024-01-01"}
    extra_fields = {"合同编号": "HT2024-001", "采购方式": "公开招标"}
    row_id_fake = 7777
    table = "data_contracts"

    try:
        # ═══ ① 正常匹配 ═══
        print("── ① _build_field_sources（5 字段：3 列名 + 2 extra）──")
        _build_field_sources(trace_id, pid, table, row_id_fake, row_dict, extra_fields, chunks_db)

        fs = query(
            "SELECT field_name, chunk_id, ocr_version FROM audit_field_sources "
            "WHERE table_name=%s AND row_id=%s ORDER BY id",
            (table, row_id_fake), database="tt",
        )
        check("落 5 条 field_source", len(fs) == 5, str(fs)[:160])
        fmap = {r["field_name"]: r for r in fs}

        check("① 覆盖列名 party_a", "party_a" in fmap, str(fmap)[:160])
        check("① 覆盖列名 amount", "amount" in fmap, str(fmap)[:160])
        check("① 覆盖列名 sign_date", "sign_date" in fmap, str(fmap)[:160])
        check("① 覆盖 extra_fields->$.合同编号",
              "extra_fields->$.合同编号" in fmap, str(fmap)[:160])
        check("① 覆盖 extra_fields->$.采购方式",
              "extra_fields->$.采购方式" in fmap, str(fmap)[:160])

        # 文本匹配命中
        check("① party_a 命中 chunk1", fmap.get("party_a", {}).get("chunk_id") == c1, str(fmap.get("party_a")))
        check("① amount 命中 chunk1", fmap.get("amount", {}).get("chunk_id") == c1, str(fmap.get("amount")))
        check("① sign_date 命中 chunk1", fmap.get("sign_date", {}).get("chunk_id") == c1, str(fmap.get("sign_date")))
        check("① 合同编号 命中 chunk2",
              fmap.get("extra_fields->$.合同编号", {}).get("chunk_id") == c2,
              str(fmap.get("extra_fields->$.合同编号")))
        check("① 采购方式 不在任何 chunk → NULL",
              fmap.get("extra_fields->$.采购方式", {}).get("chunk_id") is None,
              str(fmap.get("extra_fields->$.采购方式")))
        check("① ocr_version=2 对齐", all(r["ocr_version"] == 2 for r in fs), str(fs)[:160])

        # ═══ ② P4-6 行→trace 引用 ═══
        print("\n── ② P4-6 link_data_row_to_document（行→trace ref）──")
        refs = es.get_refs("data_row", row_id_fake)
        check("② data_row ref 1 条", len(refs) == 1, str(refs)[:140])
        check("② ref document_id=trace_id",
              refs and refs[0]["document_id"] == trace_id, str(refs)[:140])

        # ═══ ③ 降级：chunks_db=[] → 全 NULL ═══
        print("\n── ③ 降级 chunks_db=[] → 全 chunk_id=NULL ──")
        row_id_deg = 8888
        _build_field_sources(trace_id, pid, table, row_id_deg, row_dict, extra_fields, [])
        fdeg = query(
            "SELECT field_name, chunk_id FROM audit_field_sources "
            "WHERE table_name=%s AND row_id=%s",
            (table, row_id_deg), database="tt",
        )
        check("③ 降级仍落 5 条（不伪造但建来源行）", len(fdeg) == 5, str(fdeg)[:140])
        check("③ 降级全 chunk_id=NULL",
              all(r["chunk_id"] is None for r in fdeg), str(fdeg)[:140])

        # ═══ ④ 空值字段跳过 ═══
        print("\n── ④ 空值字段跳过 ──")
        row_id_empty = 9999
        _build_field_sources(
            trace_id, pid, table, row_id_empty,
            {"party_a": "", "amount": None, "note": "有效"},
            {"x": None, "y": ""},
            chunks_db,
        )
        femp = query(
            "SELECT field_name FROM audit_field_sources WHERE table_name=%s AND row_id=%s",
            (table, row_id_empty), database="tt",
        )
        names = {r["field_name"] for r in femp}
        check("④ 仅 'note' 一条（空值全跳）", names == {"note"}, str(names))
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
    print(f"切片4 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
