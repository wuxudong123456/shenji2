r"""Phase9 T8 并发编辑事项（乐观锁）验收

§6.8：两个会话同时编辑同一 audit_item → 乐观锁生效，后提交者收冲突提示，不静默覆盖。

机制（P1-7 乐观锁，audit_routes.py:665-675）：
  PUT /projects/<id>/items 收 expected_update_time；与 audit_projects.update_time
  不匹配 → 409「项目已被他人修改」+ current_update_time；check 在 DELETE+INSERT 之前。

本轮修复（用户选「修 Gap A+B」）：
  - Gap B（后端）：items-save 每次 successful save 都 bump update_time（原仅 stage 推进时），
    并在响应返回最新 update_time。→ items/workspace 阶段重编也能检出并发。
  - Gap A（前端）：projects.html saveItems 带 expected_update_time；409 时自动拉取最新事项
    + 刷新 token，提示用户核对后重存；api.js saveItems 增 expectedUpdateTime 形参。
  - Gap C（已知局限，未修）：token=秒精度 DATETIME（非 version INT），同秒并发不可区分；
    审计低并发场景可接受。测试用 time.sleep(1.2) 隔秒避开同秒碰撞。

本测试：
  ① 乐观锁正向（stage 推进 + 传 token，spec 主场景）：A 存→B 过期 token 409 不覆盖→B 刷新重存。
  ② items 阶段重编乐观锁（Gap B 已修）：C 重存 bump+返回 token→D 过期 token 409 不覆盖。
  ③ opt-in 兼容性：不传 token 仍兼容旧客户端（200）。

用法：cd backend && python tests\test_p9_t8_concurrency.py
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, query, execute, insert  # noqa: E402

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


def ut(pid):
    """读 audit_projects.update_time（乐观锁 token 源）"""
    r = query_one("SELECT update_time, setup_stage FROM audit_projects WHERE id=%s",
                  (pid,), database="tt")
    return r


def item_titles(pid):
    rows = query("SELECT title FROM audit_items WHERE project_id=%s ORDER BY id", (pid,), database="tt")
    return [r["title"] for r in rows]


def save_items(pid, items, expected=None):
    body = {"audit_items": items}
    if expected is not None:
        body["expected_update_time"] = expected
    return req("PUT", f"/projects/{pid}/items", body)


def iso(dt):
    return dt.isoformat() if hasattr(dt, "isoformat") else str(dt)


def main():
    global PASS, FAIL
    print("[test] Phase9 T8 并发编辑事项（乐观锁）\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    pid = "T8LOCK_TEST"
    # 干净起点
    execute("DELETE FROM audit_items WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    # 抛错项目：直接置于 target_scope（过前置阶段校验），便于观察首次保存推进 stage→items
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'target_scope')",
           (pid, "T8 并发编辑测试项目"), database="tt")

    # ══════════════════════════════════════════════════════════════════
    # ① 乐观锁逻辑正向（spec §6.8 主场景：stage 推进 + 传 token）
    # ══════════════════════════════════════════════════════════════════
    print("── ① 乐观锁正向（两会话并发，stage 推进 + 传 token）──")
    # 注：乐观锁 token = audit_projects.update_time（秒精度 DATETIME，非 version INT）。
    # 项目 INSERT 与 A 首存若同秒 → NOW() 与 insert 的 CURRENT_TIMESTAMP 同秒 → token 不变 →
    # 乐观锁无法检出（Gap C：秒精度脆弱）。此处显式隔秒，给乐观锁公平的触发条件。
    time.sleep(1.2)
    t0 = ut(pid)
    info("初始 update_time/setup_stage", f"{iso(t0['update_time'])} / {t0['setup_stage']}")
    tok0 = iso(t0["update_time"])

    print("  〔会话 A〕存〔A-事项〕expected=tok0")
    st, r = save_items(pid, [{"title": "A-事项"}], expected=tok0)
    info("A 响应", f"{st} {str(r)[:120]}")
    check("A 存成功（200）", st == 200 and r.get("success") is True, f"{st}")

    t1 = ut(pid)
    info("A 存后 update_time/setup_stage", f"{iso(t1['update_time'])} / {t1['setup_stage']}")
    check("A 存后 update_time 已 bump（乐观锁 token 已变）",
          t1["update_time"] != t0["update_time"], "未 bump→乐观锁无法检出后续并发")
    check("A 存后 stage 推进到 items", t1["setup_stage"] == "items", t1["setup_stage"])

    print("  〔会话 B〕用过期 tok0 存〔B-事项-应被拒〕")
    st, r = save_items(pid, [{"title": "B-事项-应被拒"}], expected=tok0)
    info("B 响应", f"{st} {str(r)[:160]}")
    check("B 过期 token 收 409（冲突提示）", st == 409, f"{st}")
    check("409 含「已被他人修改」提示",
          "修改" in (r.get("error") or "") or "他人" in (r.get("error") or ""),
          r.get("error"))
    check("409 返回 current_update_time（供前端刷新）",
          bool(r.get("current_update_time")), str(r.get("current_update_time")))

    titles = item_titles(pid)
    info("事项现状", titles)
    check("B 未覆盖（事项仍为 A 的，非静默覆盖）", titles == ["A-事项"], f"titles={titles}")

    print("  〔会话 B 刷新〕读最新 token 后重存〔B-事项-刷新后〕")
    tok1 = iso(t1["update_time"])
    st, r = save_items(pid, [{"title": "B-事项-刷新后"}], expected=tok1)
    info("B 刷新后响应", f"{st} {str(r)[:120]}")
    check("B 刷新 token 后重存成功", st == 200 and r.get("success") is True, f"{st}")
    check("刷新后事项为 B 的", item_titles(pid) == ["B-事项-刷新后"], str(item_titles(pid)))

    # ══════════════════════════════════════════════════════════════════
    # ② items 阶段重编乐观锁（Gap B 已修：每次存都 bump update_time + 返回新 token）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ② items 阶段重编乐观锁（Gap B 已修：每次存都 bump）──")
    time.sleep(1.2)  # 隔秒，确保 bump 可检出（秒精度 token，Gap C 已知局限）
    t2 = ut(pid)
    info("items 阶段 update_time", iso(t2["update_time"]))
    tok2 = iso(t2["update_time"])
    st, r = save_items(pid, [{"title": "C-事项"}], expected=tok2)  # stage 已 items，Gap B 修复后仍 bump
    info("C（items 阶段重存）响应", f"{st} {str(r)[:160]}")
    check("C items 阶段重存成功（200）", st == 200 and r.get("success") is True, f"{st}")
    check("响应返回最新 update_time（供前端刷 token，Gap A 修复）",
          bool(r.get("update_time")), str(r.get("update_time")))
    t3 = ut(pid)
    check("Gap B 修复：items 阶段重存也 bump update_time",
          t3["update_time"] != t2["update_time"], "未 bump")

    print("  〔会话 D〕用 items 阶段过期 tok2 存〔D-过期〕（应被乐观锁拦）")
    st, r = save_items(pid, [{"title": "D-过期"}], expected=tok2)
    info("D 响应", f"{st} {str(r)[:160]}")
    check("items 阶段乐观锁生效（D 过期→409）", st == 409, f"{st}")
    check("D 未覆盖（事项仍为 C 的）", item_titles(pid) == ["C-事项"], str(item_titles(pid)))

    # ══════════════════════════════════════════════════════════════════
    # ③ opt-in 兼容性（Gap A 已修：新前端发 token；后端仍兼容不传=跳过检查）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ③ opt-in 兼容性（不传 token 仍兼容旧客户端）──")
    st, r = save_items(pid, [{"title": "E-无token"}], expected=None)  # 不传 token
    info("E（不传 token）响应", f"{st} {str(r)[:120]}")
    check("opt-in：不传 token 时端点兼容旧客户端（200）", st == 200, f"{st}")
    info("Gap A 已修",
         "新前端（projects.html saveItems）已带 expected_update_time + 409 自动拉新；"
         "后端保留 opt-in 兼容旧客户端（不传则跳过检查）")

    # 清理抛错项目
    execute("DELETE FROM audit_items WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    print(f"\n{'='*50}\nPhase9 T8 并发编辑事项：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
