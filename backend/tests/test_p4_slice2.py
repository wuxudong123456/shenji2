r"""Phase4 切片2 验收：P4-3/P4-4/P4-10 chunk 落库 + 双写 + 失效

直调 _persist_chunks（DB fixture，合成 ontosku chunks，不依赖真实 OCR/backend HTTP）：
  - 首次落库：audit_document_chunks 有 active 行，page_nums(JSON)/bbox(JSON)/ocr_version 正确
  - 双写：_persist_chunks 不碰 trace.position_anchor（由 _run_ocr_task 的 UPDATE 单独写）
  - 重解析失效（P4-10）：第二次调（ocr_version+1）→ 旧行 superseded、新行 active
  - 降级/空：raw_chunks=[] → 返回 [] 不插行

用法：cd backend && .venv\Scripts\python.exe tests\test_p4_slice2.py
"""
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, query_one, insert, execute  # noqa: E402
from services.task_worker import _persist_chunks  # noqa: E402

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
    """DB 取回的 JSON 列可能是 str/已解析，统一成 python 对象。"""
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


CHUNKS_V1 = [
    {"chunk_id": "c1", "type": "text", "page_nums": [1], "bbox": [10, 20, 100, 200],
     "text": "甲方A公司 金额100元", "section_path": "一、当事人"},
    {"chunk_id": "c2", "type": "table", "page_nums": [2, 3], "bbox": None,
     "text": "明细表", "section_path": None},
]
CHUNKS_V2 = [
    {"chunk_id": "c3", "type": "text", "page_nums": [1], "bbox": [5, 5, 50, 50],
     "text": "重解析后内容", "section_path": "新章节"},
]


def main():
    global PASS, FAIL
    print("[test] Phase4 切片2：P4-3/P4-4/P4-10 chunk 落库+双写+失效\n")

    pid = "p4s2_{}".format(uuid.uuid4().hex[:8])
    # fixture：project + trace（ocr_version=2 模拟首次解析后 UPDATE 已 +1；
    # position_anchor 占位模拟 _run_ocr_task 双写写入的旧值）
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    execute(
        "INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
        (pid, "P4s2"), database="tt",
    )
    trace_id = insert(
        "INSERT INTO audit_document_traces "
        "(project_id,file_name,minio_bucket,minio_path,ocr_version,parse_status,parse_engine,position_anchor) "
        "VALUES (%s,%s,%s,%s,%s,'done','ontosku',%s)",
        (pid, "fixture.pdf", "bk", "p/fixture.pdf", 2, json.dumps({"old": True})),
        database="tt",
    )

    try:
        # ═══ ① 首次落库（P4-3/P4-4）═══
        print("── ① 首次 _persist_chunks（v1, 2 chunks）──")
        returned = _persist_chunks(trace_id, pid, CHUNKS_V1, "ontosku")
        check("返回 2 条 chunk 行", len(returned) == 2, str(returned)[:120])
        check("返回行含 id(int)", all(isinstance(r.get("id"), int) for r in returned),
              str(returned)[:120])
        check("返回行含 text", all(r.get("text") for r in returned), str(returned)[:120])

        rows = query(
            "SELECT chunk_id,chunk_type,page_nums,bbox,ocr_version,status,text "
            "FROM audit_document_chunks WHERE trace_id=%s ORDER BY id",
            (trace_id,), database="tt",
        )
        check("DB 落 2 active 行", len(rows) == 2, str(rows)[:140])
        check("① status 全 active", all(r["status"] == "active" for r in rows), str(rows)[:140])
        check("① ocr_version=2", all(r["ocr_version"] == 2 for r in rows), str(rows)[:140])
        check("① chunk_id 透传", rows[0]["chunk_id"] == "c1", str(rows[0]))
        check("① chunk_type 透传", rows[0]["chunk_type"] == "text", str(rows[0]))
        check("① page_nums=[1]", _loads(rows[0]["page_nums"]) == [1], str(rows[0]))
        check("① bbox=[10,20,100,200]", _loads(rows[0]["bbox"]) == [10, 20, 100, 200], str(rows[0]))
        check("① page_nums=[2,3]", _loads(rows[1]["page_nums"]) == [2, 3], str(rows[1]))
        # bbox None → 列存 NULL
        check("① 空 bbox 存 NULL", rows[1]["bbox"] is None, str(rows[1]))

        # ═══ ② 双写：position_anchor 未被 _persist_chunks 改动 ═══
        print("\n── ② 双写：position_anchor 不被 _persist_chunks 触碰 ──")
        tr = query_one("SELECT position_anchor FROM audit_document_traces WHERE id=%s",
                       (trace_id,), database="tt")
        check("position_anchor 保持原值（双写由 UPDATE 负责）",
              _loads(tr.get("position_anchor")) == {"old": True}, str(tr)[:120])

        # ═══ ③ 重解析失效（P4-10）═══
        print("\n── ③ 重解析 _persist_chunks（v2, ocr_version+1→3）──")
        execute("UPDATE audit_document_traces SET ocr_version=3 WHERE id=%s",
                (trace_id,), database="tt")
        returned2 = _persist_chunks(trace_id, pid, CHUNKS_V2, "ontosku")
        check("v2 返回 1 条", len(returned2) == 1, str(returned2)[:120])

        all_rows = query(
            "SELECT chunk_id,ocr_version,status FROM audit_document_chunks "
            "WHERE trace_id=%s ORDER BY id",
            (trace_id,), database="tt",
        )
        v1_rows = [r for r in all_rows if r["chunk_id"] in ("c1", "c2")]
        v2_rows = [r for r in all_rows if r["chunk_id"] == "c3"]
        check("③ v1 旧行全 superseded（留痕不删）",
              all(r["status"] == "superseded" for r in v1_rows), str(v1_rows))
        check("③ v2 新行 active", len(v2_rows) == 1 and v2_rows[0]["status"] == "active",
              str(v2_rows))
        check("③ v2 新行 ocr_version=3", v2_rows and v2_rows[0]["ocr_version"] == 3, str(v2_rows))
        check("③ 旧行未删（留痕）", len(all_rows) == 3, f"应有3行实有{len(all_rows)}")

        # ═══ ④ 降级/空 chunks → [] 不插行 ═══
        print("\n── ④ 空 chunks → [] 不插行 ──")
        # 新 trace 模拟降级（chunks 天然空）
        tid2 = insert(
            "INSERT INTO audit_document_traces (project_id,file_name,ocr_version,parse_status,parse_engine) "
            "VALUES (%s,%s,2,'done','liteparse')",
            (pid, "degraded.pdf"), database="tt",
        )
        ret_empty = _persist_chunks(tid2, pid, [], "ontosku")
        check("空 chunks 返回 []", ret_empty == [], str(ret_empty)[:80])
        cnt = query_one("SELECT COUNT(*) c FROM audit_document_chunks WHERE trace_id=%s",
                        (tid2,), database="tt")
        check("空 chunks 不插行", cnt and cnt["c"] == 0, str(cnt))
        check("④ 降级 engine 直接返 []",
              _persist_chunks(tid2, pid, CHUNKS_V1, "liteparse") == [])
    finally:
        # 收尾
        try:
            execute("DELETE FROM audit_document_chunks WHERE trace_id IN "
                    "(SELECT id FROM audit_document_traces WHERE project_id=%s)",
                    (pid,), database="tt")
            execute("DELETE FROM audit_document_traces WHERE project_id=%s", (pid,), database="tt")
            execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
            print("\n[cleanup] 已删临时 project/trace/chunks")
        except Exception as e:
            print(f"[cleanup] 异常: {e}")

    print(f"\n{'='*48}")
    print(f"切片2 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
