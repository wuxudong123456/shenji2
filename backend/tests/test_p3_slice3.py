r"""Phase3 切片3+8 验收：P3-5 parse_status 状态机 + P3-10 指数退避 re-submit

直接测 task_manager / task_worker 模块逻辑（不经 HTTP / OCR），确定性可断言：
  - _set_trace_parse_status：running/failed/done(parsed_at) 转写 + None 安全
  - fail_task 终态路径：max_retries=1 一次失败 → 返回 False、status=failed、无重投
  - fail_task 重试路径（P3-10 全生命周期）：max_retries=3 连续 3 次失败
        退避 1s/2s → 两次 re-submit → 第三次终态 failed
        （time.sleep 与 submit_task 打桩捕获，不真睡、不真跑 OCR）

用法：cd backend && .venv\Scripts\python.exe tests\test_p3_slice3.py
"""
import os
import sys

# 确保能 import services.*（与 migrate.py 同一约定：cwd=backend）
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.db import insert, query_one, execute  # noqa: E402
import services.task_manager as tm  # noqa: E402
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


def main():
    print("[test] Phase3 切片3+8：P3-5 状态机 + P3-10 退避 re-submit\n")

    # ── 夹具：临时 trace（仅 project_id 必填）──
    trace_id = insert(
        "INSERT INTO audit_document_traces (project_id, file_name, parse_status) "
        "VALUES (%s, %s, 'pending')",
        ("__p3test__", "slice3_probe.pdf"), database="tt",
    )
    print(f"[setup] trace_id={trace_id}")

    # ═══ P3-5：_set_trace_parse_status ═══
    print("\n── P3-5 _set_trace_parse_status ──")

    tw._set_trace_parse_status(trace_id, "running")
    row = query_one("SELECT parse_status, parsed_at FROM audit_document_traces WHERE id=%s",
                    (trace_id,), database="tt")
    check("running：parse_status='running'", row["parse_status"] == "running", str(row))
    check("running：parsed_at 仍 NULL", row["parsed_at"] is None, str(row))

    tw._set_trace_parse_status(trace_id, "failed")
    row = query_one("SELECT parse_status FROM audit_document_traces WHERE id=%s",
                    (trace_id,), database="tt")
    check("failed：parse_status='failed'", row["parse_status"] == "failed", str(row))

    tw._set_trace_parse_status(trace_id, "done", parsed_at=True)
    row = query_one("SELECT parse_status, parsed_at FROM audit_document_traces WHERE id=%s",
                    (trace_id,), database="tt")
    check("done+parsed_at：parse_status='done'", row["parse_status"] == "done", str(row))
    check("done+parsed_at：parsed_at 非空", row["parsed_at"] is not None, str(row))

    # None 安全（不应抛异常、不应改库）
    try:
        tw._set_trace_parse_status(None, "running")
        check("None trace_id 安全无异常", True)
    except Exception as e:
        check("None trace_id 安全无异常", False, repr(e))

    # ═══ P3-5 failed 同步 + _fail_with_trace ═══
    print("\n── P3-5 _fail_with_trace（终态→failed 同步）──")
    # 用打桩的 submit_task / sleep，避免真跑 OCR / 真睡
    submitted, sleeps = [], []
    real_time = tm.time

    class _FakeTime:
        def sleep(self, s):
            sleeps.append(s)

    tm.time = _FakeTime()
    tw.submit_task = lambda tid: submitted.append(tid)
    try:
        # 终态任务：max_retries=1 → 一次失败即终态
        term = tm.create_task("slice3_term", "ocr", project_id="__p3test__", max_retries=1)
        term_id = term["task"]["id"]
        # _fail_with_trace 是 void（不返回重试标志）；其效果由下方 status/trace 断言覆盖
        tw._fail_with_trace(term_id, trace_id, "终态探针")
        check("终态无 re-submit", len(submitted) == 0, str(submitted))
        check("终态无 sleep", len(sleeps) == 0, str(sleeps))
        trow = query_one("SELECT status, retry_count FROM audit_task_queue WHERE id=%s",
                         (term_id,), database="tt")
        check("终态 status='failed'", trow["status"] == "failed", str(trow))
        check("终态 retry_count=1", trow["retry_count"] == 1, str(trow))
        # trace 被同步为 failed
        prow = query_one("SELECT parse_status FROM audit_document_traces WHERE id=%s",
                         (trace_id,), database="tt")
        check("终态同步 trace.parse_status='failed'", prow["parse_status"] == "failed", str(prow))

        # ═══ P3-10：重试全生命周期（max_retries=3，连败 3 次）═══
        print("\n── P3-10 fail_task 指数退避 + re-submit 全生命周期 ──")
        rt = tm.create_task("slice3_retry", "ocr", project_id="__p3test__", max_retries=3)
        rt_id = rt["task"]["id"]
        submitted.clear()
        sleeps.clear()

        r1 = tm.fail_task(rt_id, "第1次失败")
        r2 = tm.fail_task(rt_id, "第2次失败")
        r3 = tm.fail_task(rt_id, "第3次失败")

        check("重试1 返回 True（回 pending）", r1 is True)
        check("重试2 返回 True（回 pending）", r2 is True)
        check("重试3 返回 False（终态）", r3 is False)
        check("退避序列 = [1, 2]（2^(retry-1)）", sleeps == [1, 2], str(sleeps))
        check("re-submit 序列长度 = 2（仅前两次重投）", len(submitted) == 2, str(submitted))
        check("re-submit 均为同一 task_id", all(x == rt_id for x in submitted), str(submitted))
        frow = query_one("SELECT status, retry_count FROM audit_task_queue WHERE id=%s",
                         (rt_id,), database="tt")
        check("生命周期末 status='failed'", frow["status"] == "failed", str(frow))
        check("生命周期末 retry_count=3", frow["retry_count"] == 3, str(frow))
    finally:
        tm.time = real_time  # 还原 time 模块
        # 还原 tw.submit_task（删打桩属性，回落真实函数）
        try:
            del tw.submit_task
        except AttributeError:
            pass

    # ═══ 收尾：删临时数据 ═══
    execute("DELETE FROM audit_task_queue WHERE project_id=%s", ("__p3test__",), database="tt")
    execute("DELETE FROM audit_document_traces WHERE project_id=%s", ("__p3test__",), database="tt")
    print("\n[cleanup] 已删 __p3test__ 临时 trace/task")

    print(f"\n{'='*48}")
    print(f"切片3+8 结果：PASS={PASS}  FAIL={FAIL}")
    print(f"{'='*48}")
    sys.exit(0 if FAIL == 0 else 1)


if __name__ == "__main__":
    main()
