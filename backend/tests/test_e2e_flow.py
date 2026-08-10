r"""Phase9 T1 端到端验收：七步全链路（真 LLM）

立项→事项(已有)→POST /analysis(Step1-2 真 LLM)→confirm(Step3)→step/4(Step5 真 LLM)
→suspicion/generate(Step6 真 LLM)→review CONFIRMED→documents/batch(Step7)。
断言 current_step 1→7 全程推进 + 各阶段落库（step_data/summaries/suspicions/文书）。

夹具：真实项目 4a0946e4c4c0（workspace + 6 事项 + 3 trace + 144 字段溯源），数据链 Phase2-7 已验证。
本 T1 聚焦"分析链 + Phase8 契约层"端到端通不通（最高信息量）。结束清理任务级数据，不动共享项目。

用法：cd backend && python tests\test_e2e_flow.py [BASE_URL]
"""
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query_one, execute  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5000"
PASS = 0
FAIL = 0

# 真实 LLM 链耗时，各步给足超时
TIMEOUT = {"slow": 240, "mid": 120, "fast": 60}


def req(method, path, body=None, timeout=60):
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


def main():
    global PASS, FAIL
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=8) as _h:
            _hc = _h.status
    except Exception as _e:
        _hc = None
        print(f"[fatal] backend 未就绪 (/api/health → {_e})")
    if _hc != 200:
        print(f"[fatal] backend 未就绪 (/api/health → {_hc})")
        sys.exit(2)
    print(f"[test] Phase9 T1 端到端全链路（真 LLM）目标 {BASE}\n")

    # ── 夹具：真实项目 4a0946e4c4c0 + 首个事项 ──
    pid = "4a0946e4c4c0"
    proj = query_one("SELECT id,name,setup_stage FROM audit_projects WHERE id=%s AND deleted=0",
                     (pid,), database="tt")
    if not proj:
        print(f"[fatal] 夹具项目 {pid} 不存在")
        sys.exit(2)
    item = query_one("SELECT id,title FROM audit_items WHERE project_id=%s ORDER BY id LIMIT 1",
                     (pid,), database="tt")
    check("夹具项目 + 事项就绪", proj and item, str(proj))
    print(f"    项目={proj['name'][:24]}…  focus_item={item['title'][:16] if item else '-'}\n")

    # ═══ Step1-2：POST /analysis（真 LLM：IntentAnalyzer + 3 并行 Agent）═══
    print("── Step1-2 POST /analysis（真 LLM，最长 4 分钟）──")
    st, r = req("POST", "/analysis", {
        "project_id": pid, "focus_item_id": item["id"] if item else None,
        "user_intent": "核查办公电脑采购合规性及财务收支",
    }, timeout=TIMEOUT["slow"])
    check("Step1-2 200", st == 200, f"{st} {str(r)[:160]}")
    tid = r.get("task_id", "")
    check("返回 task_id", bool(tid), str(r)[:120])
    cs = r.get("current_step")
    check("Step1-2 后 current_step=2", cs == 2, f"current_step={cs}")
    matches = r.get("matches", [])
    primary_laws = r.get("primary_laws", [])
    print(f"    matches={len(matches)}  primary_laws={len(primary_laws)}  readiness.entry.ready="
          f"{(r.get('readiness') or {}).get('entry', {}).get('ready')}")

    # ═══ Step3：confirm（人工确认违规模型 + 法规）═══
    print("── Step3 confirm ──")
    sel_v = [m.get("id") for m in matches[:2] if m.get("id")] or ["v1"]
    sel_l = primary_laws[:3] if primary_laws else []
    st, r = req("POST", f"/analysis/{tid}/confirm", {
        "selected_violations": sel_v, "selected_laws": sel_l, "action": "confirm",
    }, timeout=TIMEOUT["mid"])
    check("confirm 200", st == 200, f"{st} {str(r)[:160]}")
    check("confirm 后 current_step=3", r.get("current_step") == 3, f"current_step={r.get('current_step')}")
    check("confirm 返回 law_recommendations",
          isinstance(r.get("law_recommendations"), list), str(r)[:120])

    # ═══ readiness data_ready（夹具项目应有 trace+data+field_sources）═══
    print("── readiness data_ready ──")
    st, r = req("GET", f"/analysis/{tid}/readiness?stage=data_ready")
    check("data_ready 200", st == 200, f"{st}")
    check("data_ready ready=True（夹具数据完整）", r.get("ready") is True,
          str(r.get("missing_items")))

    # ═══ Step4→5：step/4（真 LLM：AuditAnalyzer + 表达式扫描）═══
    print("── Step4→5 step/4（真 LLM）──")
    st, r = req("POST", f"/analysis/{tid}/step/4", {"uploaded_files": []}, timeout=TIMEOUT["slow"])
    check("step/4 200", st == 200, f"{st} {str(r)[:160]}")
    check("step/4 后 current_step=5", r.get("current_step") == 5, f"current_step={r.get('current_step')}")
    ar = r.get("analysis_results", [])
    print(f"    analysis_results={len(ar)}  overall_assessment={bool(r.get('overall_assessment'))}")

    # ═══ Step6：suspicion/generate（真 LLM：SuspicionGenerator）═══
    print("── Step6 suspicion/generate（真 LLM）──")
    st, r = req("POST", "/suspicion/generate", {"task_id": tid}, timeout=TIMEOUT["mid"])
    check("suspicion/generate 200", st == 200, f"{st} {str(r)[:160]}")
    sid = r.get("suspicion_id")
    check("落库 project_suspicions（suspicion_id）", bool(sid), str(r)[:160])
    check("verify_status=MODEL_FOUND", r.get("verify_status") == "MODEL_FOUND", str(r.get("verify_status")))
    # current_step 应推进到 6
    st2, r2 = req("GET", f"/analysis/{tid}")
    check("Step6 后 current_step=6", r2.get("current_step") == 6, f"current_step={r2.get('current_step')}")

    # ═══ 疑点核实：MODEL_FOUND → CONFIRMED（五态流转）═══
    print("── 疑点核实 review→CONFIRMED ──")
    if sid:
        st, r = req("POST", f"/analysis/{tid}/suspicions/review",
                    {"suspicion_id": sid, "verify_status": "CONFIRMED",
                     "evidence": {"note": "T1 人工确认"}})
        check("review→CONFIRMED", st == 200 and
              (r.get("suspicion") or {}).get("verify_status") == "CONFIRMED", str(r)[:120])

    # ═══ Step7：documents/batch（四件套，读 CONFIRMED 疑点）═══
    print("── Step7 documents/batch ──")
    st, r = req("POST", "/documents/batch", {"task_id": tid}, timeout=TIMEOUT["fast"])
    check("documents/batch 200", st == 200, f"{st} {str(r)[:160]}")
    docs = r.get("documents") or {}
    check("四件套齐全", all(k in docs for k in ("evidence", "workpaper", "report", "review")),
          str(sorted(docs.keys())))
    check("Step7 后 current_step=7", r.get("current_step") == 7, f"current_step={r.get('current_step')}")

    # ═══ 终态：GET 权威状态（current_step=7 + summaries 覆盖）═══
    print("── 终态 GET /analysis/{id} ──")
    st, r = req("GET", f"/analysis/{tid}")
    check("终态 current_step=7", r.get("current_step") == 7, f"current_step={r.get('current_step')}")
    sums = r.get("summaries") or {}
    check("summaries 覆盖 step 2/3/5/6/7",
          all(str(s) in sums for s in (2, 3, 5, 6, 7)), f"keys={sorted(sums.keys())}")

    # ═══ DB 直查：各阶段落库完整（T1 §6.1 断言）═══
    print("── DB 落库完整性 ──")
    t = query_one("SELECT current_step,step_data FROM audit_analysis_tasks WHERE task_code=%s",
                  (tid,), database="tt")
    check("audit_analysis_tasks.current_step=7", (t or {}).get("current_step") == 7)
    sd = t.get("step_data") if t else None
    sd = json.loads(sd) if isinstance(sd, str) else (sd or {})
    check("step_data 含 matches/selected_laws/analysis_results",
          bool(sd.get("matches")) is not None and "selected_laws" in sd and "analysis_results" in sd,
          str(sorted(sd.keys()))[:120])
    sumn = query_one("SELECT COUNT(*) AS n FROM audit_step_summaries WHERE analysis_task_id=%s",
                     (tid,), database="tt")["n"]
    check(f"audit_step_summaries 落库（{sumn} 条）", sumn >= 5)
    trn = query_one("SELECT COUNT(*) AS n FROM audit_agent_traces WHERE task_id=%s",
                    (tid,), database="tt")["n"]
    check(f"audit_agent_traces 落库（{trn} 条，P8-11 溯源）", trn >= 1, f"traces={trn}")
    if sid:
        spr = query_one("SELECT verify_status,status FROM project_suspicions WHERE id=%s",
                        (sid,), database="tt")
        check("project_suspicions CONFIRMED+confirmed",
              (spr or {}).get("verify_status") == "CONFIRMED" and (spr or {}).get("status") == "confirmed")

    # ═══ 清理（任务级，不动共享项目 4a0946e4c4c0）═══
    if sid:
        execute("DELETE FROM project_suspicions WHERE id=%s", (sid,), database="tt")
    execute("DELETE FROM audit_agent_traces WHERE task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_step_summaries WHERE analysis_task_id=%s", (tid,), database="tt")
    execute("DELETE FROM audit_analysis_tasks WHERE task_code=%s", (tid,), database="tt")

    print(f"\n{'='*50}\nPhase9 T1 端到端：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
