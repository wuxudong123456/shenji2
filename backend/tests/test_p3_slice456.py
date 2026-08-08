r"""Phase3 切片4+5+6 验收：P3-3(job_id) + P3-7(template_name/doc_type) + P3-9(doc_type 落 data_*)

三块确定性逻辑（不经真实 OntoSKU / OCR）：
  P3-7 客户端：ontosku_client._fetch_result_zip 从 sku_results.json 抽
              template_name(命中 audit/* key) + doc_type(_document_overview.document_type)
              + _normalize 透传/回落 sku_profile
  P3-3：_normalize 返回含 job_id
  P3-9：_insert_into_data_table 写 doc_type 列（真实 DB 临时行）

用法：cd backend && .venv\Scripts\python.exe tests\test_p3_slice456.py
"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import services.ontosku_client as oc  # noqa: E402
from services.db import insert, query_one, execute  # noqa: E402
import services.task_worker as tw  # noqa: E402

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


def _make_zip(sku_data, md="# doc\n正文内容足够长", chunks=None):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("full.md", md)
        z.writestr("sku_results.json", json.dumps(sku_data, ensure_ascii=False))
        z.writestr("chunks.json", json.dumps({"chunks": chunks or [{"id": 1}]}))
    return buf.getvalue()


class _FakeResp:
    def __init__(self, content):
        self.content = content
        self.status_code = 200


class _FakeReq:
    def __init__(self, content):
        self.content = content

    def get(self, url, timeout=None):
        return _FakeResp(self.content)


def main():
    print("[test] Phase3 切片4+5+6：P3-3/7 客户端 + P3-9 doc_type 落表\n")
    saved_req = oc.requests
    client = oc.OntoSKUClient()

    # ═══ P3-7：_fetch_result_zip 抽 template_name + doc_type ═══
    print("── P3-7 _fetch_result_zip：template_name + doc_type ──")

    # ① audit/* 命中 + _document_overview.document_type
    sku1 = {
        "general/base_model": {"model_result": {"fields": {"通用字段": "x"}}},
        "audit/历史档案类/卷宗": {"model_result": {"fields": {
            "_document_overview": {"document_type": "档案目录", "title": "卷宗"},
            "金额": "100万",
        }}},
    }
    oc.requests = _FakeReq(_make_zip(sku1))
    z = client._fetch_result_zip("http://fake/1")
    check("① template_name=命中的 audit/* key", z["template_name"] == "audit/历史档案类/卷宗", str(z.get("template_name")))
    check("① doc_type=_document_overview.document_type", z["doc_type"] == "档案目录", str(z.get("doc_type")))
    check("① audit 字段已合并（金额）", z["fields"].get("金额") == "100万", str(z["fields"]))

    # ② 仅 general（无 audit/*）+ 扁平 document_type
    sku2 = {"general/base_model": {"model_result": {"fields": {"document_type": "发票", "金额": "50"}}}}
    oc.requests = _FakeReq(_make_zip(sku2))
    z = client._fetch_result_zip("http://fake/2")
    check("② 无 audit/* → template_name=''", z["template_name"] == "", str(z.get("template_name")))
    check("② 扁平 document_type 兜底", z["doc_type"] == "发票", str(z.get("doc_type")))

    # ③ 空字段 → 均空
    sku3 = {"general/base_model": {"model_result": {"fields": {}}}}
    oc.requests = _FakeReq(_make_zip(sku3))
    z = client._fetch_result_zip("http://fake/3")
    check("③ 空字段 → template_name='' doc_type=''", z["template_name"] == "" and z["doc_type"] == "", str(z))

    # ④ ZIP 缺 sku_results.json → 不崩，返回空 template_name/doc_type
    oc.requests = _FakeReq(_make_zip({}) if False else b"not a zip")
    # requests.get 对非 zip：_fetch_result_zip 内 zipfile.ZipFile 抛异常 → 返回空结构
    # 用真 zip 但删 sku_results：手动构造
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("full.md", "x")
    oc.requests = _FakeReq(buf.getvalue())
    z = client._fetch_result_zip("http://fake/4")
    check("④ 无 sku_results.json → 不崩，template_name=''", z["template_name"] == "", str(z))

    # ═══ P3-3/P3-7：_normalize 透传 + sku_profile 回落 ═══
    print("\n── P3-3/P3-7 _normalize：job_id 透传 + sku_profile 回落 ──")
    oc.requests = _FakeReq(_make_zip(sku1))
    z = client._fetch_result_zip("http://fake/1")
    n = client._normalize({"document_id": "doc123"}, z, "doc123", "job456", sku_profile=None)
    check("normalize job_id 透传（P3-3）", n["job_id"] == "job456", str(n.get("job_id")))
    check("normalize document_id", n["document_id"] == "doc123", str(n.get("document_id")))
    check("normalize template_name 透传", n["template_name"] == "audit/历史档案类/卷宗", str(n.get("template_name")))
    check("normalize doc_type 透传", n["doc_type"] == "档案目录", str(n.get("doc_type")))

    # sku_profile 回落（zip 无 audit 模板时用调用方传入的 sku_profile）
    n2 = client._normalize({}, {"template_name": "", "doc_type": ""}, "d", "j",
                           sku_profile="audit/合同协议类/采购合同")
    check("sku_profile 回落 template_name", n2["template_name"] == "audit/合同协议类/采购合同", str(n2.get("template_name")))

    oc.requests = saved_req  # 还原 requests

    # ═══ P3-9：_insert_into_data_table 写 doc_type ═══
    print("\n── P3-9 _insert_into_data_table：doc_type 落 data_general ──")
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id, file_name, parse_status) "
        "VALUES (%s, %s, 'done')",
        ("__p3test__", "slice456_doc.pdf"), database="tt",
    )
    try:
        # ① 基本：doc_type 落公共列（row_dict 为空，仅公共列；中文字段进 extra_fields）
        row_id = tw._insert_into_data_table(
            "data_general", "__p3test__", trace_id, "slice456_doc.pdf",
            "raw text 内容", {}, {"供应商": "甲公司"},
            None, "档案目录",  # doc_type
        )
        check("data_general 写入成功（返回 row_id）", isinstance(row_id, int) and row_id > 0, str(row_id))
        drow = query_one(
            "SELECT doc_type, document_trace_id, doc_name FROM data_general WHERE id=%s",
            (row_id,), database="tt",
        )
        check("doc_type='档案目录' 已落", drow and drow["doc_type"] == "档案目录", str(drow))
        check("document_trace_id 关联正确", drow and drow["document_trace_id"] == trace_id, str(drow))

        # ② skip-set：row_dict 含 doc_type 键时不重复加列（显式参数胜，无 duplicate column 错）
        row_id_dup = tw._insert_into_data_table(
            "data_general", "__p3test__", trace_id, "dup.pdf",
            "raw", {"doc_type": "from_mapper"}, {}, None, "显式值",
        )
        check("row_dict 含 doc_type 不报 duplicate column", isinstance(row_id_dup, int), str(row_id_dup))
        drow_dup = query_one("SELECT doc_type FROM data_general WHERE id=%s", (row_id_dup,), database="tt")
        check("显式 doc_type 参数胜过 row_dict", drow_dup and drow_dup["doc_type"] == "显式值", str(drow_dup))

        # ③ doc_type=None → 列值 NULL 不报错
        row_id2 = tw._insert_into_data_table(
            "data_general", "__p3test__", trace_id, "nodoctype.pdf",
            "raw", {}, {}, None, None,
        )
        drow2 = query_one("SELECT doc_type FROM data_general WHERE id=%s", (row_id2,), database="tt")
        check("doc_type=None → 列值 NULL 不报错", drow2 and drow2["doc_type"] is None, str(drow2))
    finally:
        execute("DELETE FROM data_general WHERE project_id=%s", ("__p3test__",), database="tt")
        execute("DELETE FROM audit_document_traces WHERE project_id=%s", ("__p3test__",), database="tt")
        print("[cleanup] 已删 __p3test__ 临时 trace/data_general")

    print(f"\n{'='*48}")
    print(f"切片4+5+6 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
