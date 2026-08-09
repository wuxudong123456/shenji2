r"""Phase3 dedup 验收：_delete_trace_data_rows — reparse/insert 幂等不变式

不变式：一条 document_trace_id ≤ 一条 data_* 行。
_insert_into_data_table（task_worker.py:611）开头先 _delete_trace_data_rows(trace_id)
清旧，保证重抽（含跨表重分类的旧表残留）不再产生重复行；首次上传各表无旧行，
DELETE 为 noop。

本测直接打 DB（不经 HTTP / 不跑真实 OCR），隔离地验证去重函数本身：
  ① 预埋 2 行 data_general + 1 行 data_contracts 同 trace_id（模拟重抽前残留）
  ② _delete_trace_data_rows → 该 trace 在全部 8 张 data_* 表归零（含跨表）
  ③ delete-then-insert 净效果：再插 1 行 → 恰为 1 行
 ④ 再次预埋重复再清 → 仍归零（可重复调用，幂等）

用法：cd backend && .venv\Scripts\python.exe tests\test_p3_dedup.py
（需 MySQL 可连；不需 backend HTTP）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import insert, execute, query_one  # noqa: E402
from services.task_worker import _delete_trace_data_rows  # noqa: E402
from services.data_service import DATA_TABLES  # noqa: E402

PASS = 0
FAIL = 0
PID = "__p3_dedup__"


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def count_rows(trace_id):
    """该 trace_id 在所有 data_* 表的总行数"""
    total = 0
    for tbl in DATA_TABLES:
        row = query_one(f"SELECT COUNT(*) c FROM {tbl} WHERE document_trace_id = %s",
                        (trace_id,), database="tt")
        total += (row or {}).get("c", 0)
    return total


def seed_general(trace_id, doc_name):
    return insert(
        "INSERT INTO data_general (project_id, document_trace_id, doc_name, doc_type) "
        "VALUES (%s,%s,%s,%s)",
        (PID, trace_id, doc_name, "杂项"), database="tt",
    )


def seed_contract(trace_id, doc_name):
    return insert(
        "INSERT INTO data_contracts (project_id, document_trace_id, doc_name, doc_type) "
        "VALUES (%s,%s,%s,%s)",
        (PID, trace_id, doc_name, "合同"), database="tt",
    )


def main():
    print("[test] Phase3 dedup：_delete_trace_data_rows 幂等不变式\n")

    # 夹具：临时 project + trace（同 test_p3_slice9 范式，规避任何 FK）
    execute("DELETE FROM audit_projects WHERE id = %s", (PID,), database="tt")
    execute("INSERT INTO audit_projects (id,name,setup_stage,deleted) VALUES (%s,%s,'workspace',0)",
            (PID, "P3dedup"), database="tt")
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id,file_name,parse_status) VALUES (%s,%s,'done')",
        (PID, "dedup.pdf"), database="tt",
    )

    try:
        # 前置：清掉该 trace 残留（幂等，应 noop）
        _delete_trace_data_rows(trace_id)
        check("前置：清除后 0 行", count_rows(trace_id) == 0, f"got {count_rows(trace_id)}")

        # ① 预埋残留行（2 general + 1 contracts，模拟重抽前的旧 NULL 行 + 上一版行）
        print("── ① 预埋残留（2 general + 1 contracts）──")
        seed_general(trace_id, "stale_a")
        seed_general(trace_id, "stale_b")
        seed_contract(trace_id, "stale_contract")
        check("① 预埋后总行数=3", count_rows(trace_id) == 3, f"got {count_rows(trace_id)}")

        # ② _delete_trace_data_rows → 全 8 表归零（含跨表 contracts）
        print("── ② _delete_trace_data_rows → 归零（含跨表）──")
        _delete_trace_data_rows(trace_id)
        check("② 清除后总行数=0（跨表）", count_rows(trace_id) == 0, f"got {count_rows(trace_id)}")

        # ③ delete-then-insert 净效果：模拟 _insert_into_data_table 的"先清后插"
        print("── ③ delete-then-insert → 恰为 1 行 ──")
        _delete_trace_data_rows(trace_id)  # 模拟 insert 开头的清旧（此时 noop）
        seed_general(trace_id, "fresh")
        check("③ 插 1 行后总行数=1", count_rows(trace_id) == 1, f"got {count_rows(trace_id)}")

        # ④ 再次残留 → 再清 → 归零（可重复调用，幂等）
        print("── ④ 再次残留 → 再清 → 归零（可重复）──")
        seed_general(trace_id, "dup")
        seed_contract(trace_id, "dup_contract")
        check("④ 再残留后总行数=3", count_rows(trace_id) == 3, f"got {count_rows(trace_id)}")
        _delete_trace_data_rows(trace_id)
        check("④ 再清后总行数=0", count_rows(trace_id) == 0, f"got {count_rows(trace_id)}")

        # ⑤ 对不存在的 trace_id 调用不报错（noop 健壮性）
        print("── ⑤ 不存在的 trace_id → noop 不报错 ──")
        try:
            _delete_trace_data_rows(-900001234)
            check("⑤ 不存在 trace_id 不抛异常", True)
        except Exception as e:
            check("⑤ 不存在 trace_id 不抛异常", False, repr(e))
    finally:
        # 收尾：清 data 残留 + trace + project
        _delete_trace_data_rows(trace_id)
        execute("DELETE FROM audit_document_traces WHERE project_id = %s", (PID,), database="tt")
        execute("DELETE FROM audit_projects WHERE id = %s", (PID,), database="tt")
        print("\n[cleanup] 已删临时 project/trace/data 行")

    print(f"\n{'='*48}")
    print(f"dedup 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
