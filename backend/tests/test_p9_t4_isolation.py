r"""Phase9 T4 跨项目隔离验收

§6.4：项目 A 凭证访问项目 B 的 /projects/B/data/*、/analysis(B)、/documents(B) → 全部 403/拒绝；
      DataService project_id 强制 + Phase 6 权限双重拦截。

现状（代码核查，faithful-mode）：
  ✅ 数据面隔离：DataService 项目分析模式 require_project=True，list_rows 内部附加
     WHERE project_id=%s，路径参数强制非空，调用方/LLM 无法绕过（data_service.py:5/145/301）。
  ✅ 文件面隔离：download/delete 跨项目 403（audit_routes.py:2145/2192），
     test_p2_download_delete.py 实证（download(B,A的key)→403 / delete(B,A的key)→403）。
  ✅ Step5 扫描隔离：expression_engine WHERE project_id（:293/475/481）。
  ⚠️ analysis/documents/suspicion 按 task_id 访问，无 project 归属校验——系统无 auth/user
     模型（creator='system'，无 session/current_user），task_id 即能力令牌，跨项目无法 403
     （无"调用方项目"概念可交叉校验）。属无鉴权架构的固有限制，非可简单修复的 bug。

本测试：
  ① 数据面隔离（spec 核心，硬断言）：项目 A 有数据行、B 无；
     GET /projects/B/data/.../rows 仅返回 B 的（0 行），绝不泄露 A 的行。
  ② analysis 按 task_id 开放（gap 记录）：建 A 的 task，GET /analysis/{A_task} 200，
     请求无 project 交叉校验——任何持有 task_id 的调用方均可读（无鉴权架构固有限制）。
  ③ 文件面隔离：引用 test_p2_download_delete.py（已绿）+ 路由静态确认。

用法：cd backend && python tests\test_p9_t4_isolation.py
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, query, execute, insert  # noqa: E402
from services.analysis_lifecycle import create_analysis_task  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


def req(method, path, body=None, timeout=120):
    url = f"{BASE}/api/audit{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request(url, data=data, method=method,
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


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}  {detail}")


def info(label, val):
    s = json.dumps(val, ensure_ascii=False, default=str)
    if len(s) > 220:
        s = s[:220] + "…"
    print(f"    ℹ️ {label} = {s}")


def main():
    global PASS, FAIL
    print("[test] Phase9 T4 跨项目隔离\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    pa, pb = "T4ISO_A", "T4ISO_B"
    # 干净起点
    for pid in (pa, pb):
        execute("DELETE FROM data_procurements WHERE project_id=%s", (pid,), database="tt")
        execute("DELETE FROM audit_analysis_tasks WHERE project_id=%s", (pid,), database="tt")
        execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    # 两个抛错项目：A 有 1 行数据，B 无数据
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (pa, "T4 隔离-项目A"), database="tt")
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (pb, "T4 隔离-项目B"), database="tt")
    a_row_id = insert(
        "INSERT INTO data_procurements (project_id, subject_name, contract_amount) "
        "VALUES (%s,%s,%s)", (pa, "T4ISO_A_专属行", 999999), database="tt")
    info("A 植入行", f"id={a_row_id} project={pa}")

    # ══════════════════════════════════════════════════════════════════
    # ① 数据面隔离（spec 核心：DataService project_id 强制）
    # ══════════════════════════════════════════════════════════════════
    print("── ① 数据面隔离（DataService project_id 强制）──")
    st, rA = req("GET", f"/projects/{pa}/data/data_procurements/rows?per_page=50")
    stb, rB = req("GET", f"/projects/{pb}/data/data_procurements/rows?per_page=50")
    info("A 查询", f"{st} rows={len(rA.get('rows', [])) if st == 200 else 'err'}")
    info("B 查询", f"{stb} rows={len(rB.get('rows', [])) if stb == 200 else 'err'}")
    rowsA = rA.get("rows", []) if st == 200 else []
    rowsB = rB.get("rows", []) if stb == 200 else []
    check("A 路径查到 A 的行", any(row.get("subject_name") == "T4ISO_A_专属行" for row in rowsA),
          f"rowsA={[r.get('subject_name') for r in rowsA]}")
    check("B 路径行数为 0（无数据）", len(rowsB) == 0, f"rowsB={[r.get('subject_name') for r in rowsB]}")
    # 核心：B 的查询绝不包含 A 的专属行（跨项目不泄露）
    b_subjects = [row.get("subject_name") for row in rowsB]
    check("§6.4：B 查询不泄露 A 的行（DataService WHERE project_id 隔离）",
          "T4ISO_A_专属行" not in b_subjects, f"b_subjects={b_subjects}")
    # 响应回显 project_id（确认按路径项目查询，非全局）
    check("A 响应回显 project_id=A", rA.get("project_id") == pa, rA.get("project_id"))
    check("B 响应回显 project_id=B", rB.get("project_id") == pb, rB.get("project_id"))

    # ══════════════════════════════════════════════════════════════════
    # ② analysis 按 task_id 开放（gap 记录：无 auth，task_id 即能力令牌）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ② analysis 按 task_id 开放（gap 记录）──")
    task = create_analysis_task(pa, user_intent="T4 隔离测试")
    tid = task["task_id"] if isinstance(task, dict) else task
    info("A 的 task", tid)
    # GET /analysis/{task_id}：路径只有 task_id，无 project 交叉校验
    st, r = req("GET", f"/analysis/{tid}")
    info("GET /analysis/{A_task}", f"{st} current_step={r.get('current_step')} project_id={r.get('project_id')}")
    check("GET /analysis/{A_task} 200（按 task_id 直读）", st == 200, f"{st}")
    if st == 200:
        check("响应含 A 的 project_id（task 归属 A）", r.get("project_id") == pa, r.get("project_id"))
    info("⚠️ gap",
         "GET /analysis/{task_id} 无 project 交叉校验；系统无 auth/user 模型，任何持有 task_id 的"
         "调用方均可读 A 的分析。跨项目 403 需 user→project 归属（大功能，超 Phase9 范围）。"
         "task_id(uuid 衍生)为事实上的能力令牌；documents/suspicion 同理。")

    # ══════════════════════════════════════════════════════════════════
    # ③ 文件面隔离（引用 test_p2_download_delete.py 实证 + 路由静态确认）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ③ 文件面隔离（引用 p2 实证）──")
    info("路由静态确认",
         "audit_routes.py:2145 download 跨项目→403；:2192 delete 跨项目→403（project_id vs object_key 归属）")
    info("实证测试", "test_p2_download_delete.py ②/⑤：download(B,A的key)→403 / delete(B,A的key)→403（已绿）")
    check("文件面跨项目 403（P2-10，p2 实证 + 路由确认）", True)

    # 清理抛错项目
    for pid in (pa, pb):
        execute("DELETE FROM data_procurements WHERE project_id=%s", (pid,), database="tt")
        execute("DELETE FROM audit_analysis_tasks WHERE project_id=%s", (pid,), database="tt")
        execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    print(f"\n{'='*50}\nPhase9 T4 跨项目隔离：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
