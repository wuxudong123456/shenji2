r"""Phase9 T5 金额边界验收

§6.5：data_* 金额字段以「元」存（决策 11）；构造万/元混入场景，断言：阈值比对
      （如 ≥200万公开招标）正确，不因单位差万倍误判。

现状（代码核查，faithful-mode）：
  ✅ 万/亿→元归一（field_mapper._cast_value，:200-226）：NUMERIC_COLS 含 amount/
     budget_amount/contract_amount 等；值含「亿」→×1e8、「万」→×1e4，剥离逗号/非数字后
     float×mult。map_extracted_fields(:181) 调 _cast_value，即 ingest 入口即归一。
     test_p5_slice1.py:123-126 实证「100万元→1000000.0」「200万→2000000.0」。
  ✅ 阈值表达式用裸「元」字面量（threshold_rules.yaml:8 `金额 >= 2000000 AND 采购方式 != "公开招标"`，
     TR001 应公开招标未招标，货物≥200万）；表达式引擎用 plain float() 比对（无万处理）——
     因 data_* 存的已是元，扫元值 vs 元阈值，一致，无万倍差。
  ⚠️ 隐式单位 gap（固有，advisory）：列头是「金额(万元)」而值为裸「200」时，_cast_value
     只见值串不见列头 → 不归一（存 200 而非 2000000）→ 漏过阈值（假阴性）。field_mapper 无法
     无歧义判定列头单位（强归一风险误伤真实小额）。data_service 有 advisory 告警
     (AMOUNT_TOO_LARGE=1e9 / AMOUNT_TOO_SMALL=10，:73-74/334-336) 作缓解。属数据质量固有限制。

本测试：
  ① 万/亿→元归一（map_extracted_fields 公共 ingest API，含 _cast_value）：
     "200万"/"200万元"/"1.5亿"/纯"2000000"/逗号"1,234,567"/非数"面议"。
  ② 阈值比对无万倍误判（execute_expression on data_contracts，TR001 ≥200万公开招标）：
     200万(询价)命中 / 50万(询价)不命中 / 300万(公开招标)不命中 / 250万(磋商)命中。
     若有万倍差：200万误存为 200 → 漏；或阈值误为 200 → 50万(=500000)假命中。实际 A/D 命中、B/C 不命中 → 边界正确。
  ③ 隐式单位 gap 记录：裸"200"(无万后缀) → 200.0（不归一，漏阈值，advisory 缓解）。

用法：cd backend && python tests\test_p9_t5_amount.py
"""
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.field_mapper import map_extracted_fields  # noqa: E402
from services.expression_engine import execute_expression  # noqa: E402
from services.db import execute, insert, query  # noqa: E402

BASE = "http://127.0.0.1:5000"
PASS = 0
FAIL = 0


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


def amt_of(raw):
    """走公共 ingest 归一：map_extracted_fields → amount 值"""
    row, _ = map_extracted_fields("data_contracts", {"金额": raw})
    return row.get("amount")


def main():
    global PASS, FAIL
    print("[test] Phase9 T5 金额边界（万→元归一 + 阈值无万倍误判）\n")
    try:
        with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as resp:
            if resp.status != 200:
                print("后端不可用，退出"); sys.exit(2)
    except Exception:
        print("后端不可用，退出"); sys.exit(2)

    # ══════════════════════════════════════════════════════════════════
    # ① 万/亿→元归一（map_extracted_fields 公共 ingest API）
    # ══════════════════════════════════════════════════════════════════
    print("── ① 万/亿→元归一（map_extracted_fields → _cast_value）──")
    cases = [
        ("200万", 2000000.0),       # 万 → ×1e4
        ("200万元", 2000000.0),     # 万元 → ×1e4（元 被正则剥离）
        ("1.5亿", 150000000.0),     # 亿 → ×1e8
        ("2000000", 2000000.0),     # 纯元，无单位 → ×1
        ("1,234,567", 1234567.0),   # 逗号千分位 → 剥离后 ×1
    ]
    for raw, expect in cases:
        got = amt_of(raw)
        check(f"「{raw}」→ {expect:g}", got == expect, f"got={got}")
    # 非数值 → None（不落异常值）
    got_none = amt_of("面议")
    check("「面议」(非数值) → None", got_none is None, f"got={got_none}")

    # ══════════════════════════════════════════════════════════════════
    # ② 阈值比对无万倍误判（execute_expression，TR001 ≥200万公开招标）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ② 阈值比对无万倍误判（TR001 ≥200万 且 非公开招标）──")
    pid = "T5AMT_TEST"
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")
    insert("INSERT INTO audit_projects (id, name, setup_stage) VALUES (%s,%s,'workspace')",
           (pid, "T5 金额边界测试"), database="tt")
    # 四行：A=200万(询价) D=250万(磋商) 应命中；B=50万(询价) C=300万(公开招标) 不命中
    rows = [
        ("A-200万-询价",      2000000, "询价"),       # =200万 阈值边界，询价 → 命中
        ("B-50万-询价",        500000, "询价"),       # <200万 → 不命中
        ("C-300万-公开招标",  3000000, "公开招标"),   # ≥200万 但公开招标 → 不命中
        ("D-250万-磋商",      2500000, "竞争性磋商"),  # ≥200万 非公开招标 → 命中
    ]
    for name, amount, method in rows:
        insert("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
               "VALUES (%s,%s,%s,%s)", (pid, name, amount, method), database="tt")
    info("植入行(元)", [{"name": n, "amount": a, "method": m} for n, a, m in rows])

    expr_tr001 = '金额 >= 2000000 AND 采购方式 != "公开招标"'
    scan = execute_expression(expr_tr001, "data_contracts", pid)
    info("TR001 扫描", {"success": scan.get("success"), "total(扫)": scan.get("total"),
                        "hits(命中)": scan.get("hits")})
    check("TR001 扫描 success", scan.get("success") is True, str(scan)[:120])
    hit_fields = [r.get("fields", {}) for r in scan.get("rows", [])]
    hit_names = sorted(f.get("party_b") for f in hit_fields if f.get("party_b"))
    check("TR001 命中 2 行（A 200万询价 + D 250万磋商）", scan.get("hits") == 2,
          f"hits={scan.get('hits')} names={hit_names}")
    check("命中含 A（200万 = 阈值边界，≥含等）", "A-200万-询价" in hit_names, str(hit_names))
    check("命中含 D（250万 磋商）", "D-250万-磋商" in hit_names, str(hit_names))
    check("未误命中 B（50万 < 200万，无万倍假命中）", "B-50万-询价" not in hit_names, str(hit_names))
    check("未误命中 C（300万 公开招标=合规）", "C-300万-公开招标" not in hit_names, str(hit_names))
    # 核心断言：无万倍误判——若 200万 误存为 200，A 漏；若阈值误为 200，B(500000)假命中。
    # 实际 A 命中 + B 不命中 = 边界（200万=2000000元 vs 2000000元阈值）精确比对。
    check("§6.5 核心：阈值比对无万倍误判（200万命中、50万不命中）",
          "A-200万-询价" in hit_names and "B-50万-询价" not in hit_names, str(hit_names))

    # 阈值边界补充：TR004 ≥500万（600万命中、其余不命中）
    scan_tr004 = execute_expression("金额 >= 5000000", "data_contracts", pid)
    info("TR004(≥500万) 扫描", {"hits": scan_tr004.get("hits")})
    # 现有四行最高 300万 < 500万 → 全不命中
    check("TR004(≥500万) 现有行全不命中（最高 300万）", scan_tr004.get("hits") == 0,
          f"hits={scan_tr004.get('hits')}")
    # 补一行 600万验证上界
    insert("INSERT INTO data_contracts (project_id, party_b, amount, procurement_method) "
           "VALUES (%s,%s,%s,%s)", (pid, "E-600万-公开招标", 6000000, "公开招标"), database="tt")
    scan_tr004b = execute_expression("金额 >= 5000000", "data_contracts", pid)
    check("TR004 补 600万后命中 1 行（上界正确）", scan_tr004b.get("hits") == 1,
          f"hits={scan_tr004b.get('hits')}")

    # ══════════════════════════════════════════════════════════════════
    # ③ 隐式单位 gap 记录（裸数无万后缀 → 不归一，漏阈值，advisory 缓解）
    # ══════════════════════════════════════════════════════════════════
    print("\n── ③ 隐式单位 gap 记录（裸『200』不归一）──")
    bare = amt_of("200")
    info("裸『200』(无万后缀) 归一结果", bare)
    check("裸『200』不归一（存 200 而非 2000000）——固有 gap", bare == 200.0, f"got={bare}")
    info("⚠️ gap 说明",
         "列头「金额(万元)」+ 值「200」：_cast_value 只见值串不见列头 → 存 200 → 漏过 ≥2000000 "
         "阈值（假阴性）。field_mapper 无法无歧义判定列头单位（强归一风险误伤真实小额）。"
         "data_service advisory 告警(AMOUNT_TOO_LARGE/SMALL) 作缓解——属数据质量固有限制，非 bug。")

    # 清理抛错项目
    execute("DELETE FROM data_contracts WHERE project_id=%s", (pid,), database="tt")
    execute("DELETE FROM audit_projects WHERE id=%s", (pid,), database="tt")

    print(f"\n{'='*50}\nPhase9 T5 金额边界：PASS={PASS}  FAIL={FAIL}\n{'='*50}")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
