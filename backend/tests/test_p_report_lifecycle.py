"""报告段状态机端到端测试 — report_stage 推进 + 前置校验 + 乐观锁 + 交付物

仿 test_p9_t8_concurrency.py 风格：urllib 打运行中的后端(:5000)，check 三重断言
（HTTP 状态 + 响应体 + DB 后置条件）。后端须先启动：cd backend && python app.py

聚焦状态机逻辑：交付物用 DB 直插满足前置条件（绕过 MinIO 上传）。
"""
import sys
import os
import json
import urllib.request
import urllib.error

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND)
from services.db import query, query_one, execute, insert  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  ✅ " + name)
    else:
        FAIL += 1
        print("  ❌ " + name + "  " + str(detail))


def req(method, path, body=None, timeout=30):
    url = "{}{}{}".format(BASE, "/api/audit", path)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json;charset=utf-8"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"_exc": str(e)[:300]}


def stage_of(pid):
    r = query_one(
        "SELECT report_stage, status, setup_stage, update_time "
        "FROM audit_projects WHERE id=%s", (pid,), database="tt")
    return r or {}


def transition(pid, to, expected=None):
    body = {"to": to}
    if expected is not None:
        body["expected_update_time"] = expected
    return req("POST", "/projects/{}/report-transition".format(pid), body)


def add_deliverable(pid, dtype="report", status="draft"):
    """DB 直插交付物（测试聚焦状态机，绕过 MinIO 上传）"""
    return insert(
        "INSERT INTO audit_deliverables (project_id, deliverable_type, version, "
        "deliverable_no, title, minio_path, status, created_by) "
        "VALUES (%s,%s,1,%s,%s,%s,%s,'test')",
        (pid, dtype, "测试文号-" + dtype, dtype + "标题",
         "test/{}/{}.pdf".format(pid, dtype), status),
        database="tt",
    )


def post_upload(pid, dtype, fname, content, version=None):
    """multipart 上传交付物（覆盖 #2 version 自增 + #3 minio_bucket + #5 上传路径）

    手工拼 multipart（标准库无 helper）；version 显式传则随请求带，否则后端 MAX+1。
    """
    boundary = "----RLCTestBoundary"
    parts = ["--" + boundary + "\r\n"
             'Content-Disposition: form-data; name="deliverable_type"\r\n\r\n'
             + dtype + "\r\n"]
    if version is not None:
        parts.append("--" + boundary + "\r\n"
                     'Content-Disposition: form-data; name="version"\r\n\r\n'
                     + str(version) + "\r\n")
    parts.append("--" + boundary + "\r\n"
                 'Content-Disposition: form-data; name="file"; filename="' + fname + '"\r\n'
                 "Content-Type: text/plain\r\n\r\n"
                 + content + "\r\n")
    parts.append("--" + boundary + "--\r\n")
    body = "".join(parts).encode("utf-8")
    url = "{}/api/audit/projects/{}/deliverables".format(BASE, pid)
    r = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return None, {"_exc": str(e)[:300]}


def make_project(pid, name, status="active", setup_stage="workspace"):
    execute("DELETE FROM audit_deliverables WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    insert(
        "INSERT INTO audit_projects (id, name, status, setup_stage, minio_bucket, "
        "deleted, create_time) VALUES (%s,%s,%s,%s,%s,0,NOW())",
        (pid, name, status, setup_stage, "audit-project-" + pid), database="tt",
    )


def main():
    # 健康检查
    try:
        urllib.request.urlopen(BASE + "/api/health", timeout=5)
    except Exception:
        print("⚠ 后端未运行，请先 cd backend && python app.py")
        sys.exit(2)

    pid = "RLC_TEST01"
    make_project(pid, "状态机测试项目")

    # ── 完整链路 NULL→drafting→reviewing→issued→filed ──
    print("\n[1] NULL→drafting（active+workspace 项目合法推进）")
    st, r = transition(pid, "drafting")
    check("HTTP 200", st == 200, "{} {}".format(st, r))
    check("DB report_stage=drafting", stage_of(pid).get("report_stage") == "drafting",
          stage_of(pid))

    print("\n[2] drafting→filed 非法跳跃被拒")
    st, r = transition(pid, "filed")
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    check("DB 仍 drafting", stage_of(pid).get("report_stage") == "drafting")

    print("\n[3] drafting→reviewing 无 report 交付物 → 409")
    st, r = transition(pid, "reviewing")
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    check("missing 含 report_deliverable",
          "report_deliverable" in (r.get("missing") or []), r)

    print("\n[4] 加 report 交付物后 drafting→reviewing 成功")
    add_deliverable(pid, "report", "draft")
    st, r = transition(pid, "reviewing")
    check("HTTP 200", st == 200, "{} {}".format(st, r))
    check("DB report_stage=reviewing", stage_of(pid).get("report_stage") == "reviewing")

    print("\n[5] reviewing→issued 无 adopted report → 409")
    st, r = transition(pid, "issued")
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    check("missing 含 adopted_report",
          "adopted_report" in (r.get("missing") or []), r)

    print("\n[6] 加 adopted report 后 reviewing→issued 成功")
    add_deliverable(pid, "report", "adopted")
    st, r = transition(pid, "issued")
    check("HTTP 200", st == 200, "{} {}".format(st, r))
    check("DB report_stage=issued", stage_of(pid).get("report_stage") == "issued")

    print("\n[7] issued→filed 无 archive_no → 409")
    st, r = transition(pid, "filed")
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    check("missing 含 archive_no", "archive_no" in (r.get("missing") or []), r)

    print("\n[8] report-meta 填 archive_no 后 issued→filed 成功（终态）")
    st, r = req("PUT", "/projects/{}/report-meta".format(pid), {"archive_no": "档2026-001"})
    check("report-meta HTTP 200", st == 200, "{} {}".format(st, r))
    st, r = transition(pid, "filed")
    check("HTTP 200", st == 200, "{} {}".format(st, r))
    check("DB report_stage=filed（终态）", stage_of(pid).get("report_stage") == "filed")

    # ── 非 active 项目被拒 ──
    print("\n[9] 非 active 项目 NULL→drafting 被拒")
    pid2 = "RLC_TEST02"
    make_project(pid2, "未激活项目", status="draft", setup_stage="basic")
    st, r = transition(pid2, "drafting")
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    check("missing 含 status=active", "status=active" in (r.get("missing") or []), r)
    check("missing 含 setup_stage=workspace",
          "setup_stage=workspace" in (r.get("missing") or []), r)

    # ── 乐观锁 ──
    print("\n[10] 乐观锁：错误 token 推进被拒 + DB 未变")
    pid3 = "RLC_TEST03"
    make_project(pid3, "乐观锁测试项目")
    transition(pid3, "drafting")  # 先到 drafting
    add_deliverable(pid3, "report", "draft")  # 满足 reviewing 前置
    st, r = transition(pid3, "reviewing", expected="故意错的token-2020-01-01T00:00:00")
    check("错误 token HTTP 409", st == 409, "{} {}".format(st, r))
    check("错误信息含「修改」", "修改" in (r.get("error") or ""), r)
    check("DB 未变仍 drafting", stage_of(pid3).get("report_stage") == "drafting")

    # ── 交付物列表 ──
    print("\n[11] GET 交付物列表（type=report）")
    st, r = req("GET", "/projects/{}/deliverables?type=report".format(pid))
    check("HTTP 200", st == 200, "{}".format(st))
    check("返回 ≥2 份 report", len(r.get("deliverables") or []) >= 2, r)

    print("\n[12] 非法目标阶段 → 400")
    st, r = transition(pid, "nonsense")
    check("HTTP 400", st == 400, "{} {}".format(st, r))

    # ── report-meta 台账字段端点（修 #1：报告台账字段可写入）──
    print("\n[13] report-meta:白名单更新 + 非白名单字段忽略")
    pid4 = "RLC_TEST04"
    make_project(pid4, "台账字段测试项目")
    st, r = req("PUT", "/projects/{}/report-meta".format(pid4),
                {"archive_no": "档2026-001", "review_deadline": "2026-12-31",
                 "name": "改名应被忽略"})
    check("HTTP 200", st == 200, "{} {}".format(st, r))
    m = query_one("SELECT archive_no, review_deadline, name FROM audit_projects WHERE id=%s",
                  (pid4,), database="tt")
    check("archive_no 已落库", m.get("archive_no") == "档2026-001", m)
    check("review_deadline 已落库", str(m.get("review_deadline")) == "2026-12-31", m)
    check("name 未被改（白名单外）", m.get("name") == "台账字段测试项目", m)

    print("\n[14] report-meta 乐观锁:错误 token 被拒 + DB 未变")
    st, r = req("PUT", "/projects/{}/report-meta".format(pid4),
                {"archive_no": "档2026-999", "expected_update_time": "错token"})
    check("HTTP 409", st == 409, "{} {}".format(st, r))
    m2 = query_one("SELECT archive_no FROM audit_projects WHERE id=%s", (pid4,), database="tt")
    check("archive_no 未变", m2.get("archive_no") == "档2026-001", m2)

    print("\n[15] report-meta 无白名单字段 → 400")
    st, r = req("PUT", "/projects/{}/report-meta".format(pid4), {"foo": "bar"})
    check("HTTP 400", st == 400, "{} {}".format(st, r))

    # ── 上传 round-trip（#2 version 自增 + #3 minio_bucket 落库/返回 + #5 上传路径）──
    print("\n[16] 上传 round-trip:version 自增 + minio_bucket 落库/返回(#2 #3 #5)")
    pid5 = "rlctest05"  # MinIO bucket 名禁大写/下划线，触 MinIO 的用例 pid 须小写
    make_project(pid5, "上传测试项目")
    try:
        from services.minio_client import get_client
        cli = get_client()
        bkt5 = "audit-project-" + pid5
        if not cli.bucket_exists(bkt5):
            cli.make_bucket(bkt5)

        st, r = post_upload(pid5, "report", "report_v1.txt", "报告正文 v1")
        check("上传1 HTTP 200", st == 200, "{} {}".format(st, r))
        st, r = post_upload(pid5, "report", "report_v2.txt", "报告正文 v2")
        check("上传2 HTTP 200", st == 200, "{} {}".format(st, r))
        check("响应 minio_bucket = audit-project-{pid}",
              r.get("minio_bucket") == bkt5, r)

        delivs = query(
            "SELECT version, minio_bucket, minio_path FROM audit_deliverables "
            "WHERE project_id=%s ORDER BY version", (pid5,), database="tt")
        check("version 自增 [1,2]",
              [d["version"] for d in delivs] == [1, 2], delivs)
        check("minio_bucket 落库",
              all(d.get("minio_bucket") == bkt5 for d in delivs), delivs)
        check("minio_path 含 deliverables/report/",
              all("deliverables/report/" in (d.get("minio_path") or "") for d in delivs),
              delivs)

        st, r = req("GET", "/projects/{}/deliverables?type=report".format(pid5))
        check("列表 HTTP 200", st == 200, st)
        items = r.get("deliverables") or []
        check("列表 ≥2 份且带 minio_bucket",
              len(items) >= 2 and all("minio_bucket" in it for it in items), items)

        # 前端显式 version 覆盖 MAX+1（#2 旁路）
        st, r = post_upload(pid5, "decision", "dec.txt", "决定书正文", version=5)
        check("显式 version=5 HTTP 200", st == 200, "{} {}".format(st, r))
        dv = query_one(
            "SELECT version FROM audit_deliverables WHERE project_id=%s "
            "AND deliverable_type='decision'", (pid5,), database="tt")
        check("显式 version 生效(=5)", dv.get("version") == 5, dv)

        # 清理 MinIO（DB 由收尾统一删；recursive=True 展开含路径前缀的对象）
        try:
            for o in cli.list_objects(bkt5, recursive=True):
                cli.remove_object(bkt5, o.object_name)
            cli.remove_bucket(bkt5)
        except Exception as ce:
            print("  (清理 bucket 警告: {})".format(ce))
    except Exception as e:
        check("上传测试环境就绪(MinIO)", False, "异常: {}".format(e))

    # 收尾
    for p in (pid, pid2, pid3, pid4, pid5):
        execute("DELETE FROM audit_deliverables WHERE project_id=%s", (p,), database="tt")
        execute("DELETE FROM audit_projects WHERE id=%s", (p,), database="tt")

    print("\n" + "=" * 40)
    print("报告段状态机测试 PASS={} FAIL={}".format(PASS, FAIL))
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
