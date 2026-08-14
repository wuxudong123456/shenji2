#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段0 — 违规表达式规则体检(只读,不写库)

遍历 audit_violations 全部含表达式规则,用 parse_expression 做语法层诊断(毫秒级),
统计真实报错分布,为改动②(规则清洗)校准清洗清单。

关键优化(初版教训):不调 execute_expression 真跑——那是执行层,对空项目会扫全库8表,
2228条 × 全库 = 卡死。语法对错 parse_expression 一步就能判定,几秒跑完全部。

为何必做:expression_parser 已支持 !=/||/中文括号(token 定义见 expression_parser.py:27-31,
_preprocess_chinese:324 也预处理了中文括号)。盲目替换这些 = 无效 diff。
真实报错类别(中文引号/LIKE未实现/字段名含全角括号/未闭合括号)必须靠本脚本实测才知道。

用法(仓库根目录):
    python backend/data/diagnose_violation_expressions.py          # 语法层诊断(秒级)
    python backend/data/diagnose_violation_expressions.py --json    # 额外导出 JSON 报告到 TEMP

输出指标:
  - total / syntax_ok / syntax_error_count
  - by_error_type{}: 按错误信息前缀分类(清洗清单依据)
  - by_layer{}: 表达式层级(row/aggregate/semantic)分布
  - 失败样本(含 expr 片段 + 具体错误)
"""
import sys
import os
import json
import re
from collections import Counter

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from services.db import query  # noqa: E402
from services.expression_parser import parse_expression  # noqa: E402
from services.expression_classifier import classify_expression  # noqa: E402


def _classify_error(error_msg: str) -> str:
    """把 parse_expression 的 SyntaxError 信息归到清洗策略可用的类别"""
    if not error_msg:
        return "unknown"
    e = error_msg
    if "无法识别的字符" in e:
        m = re.search(r"无法识别的字符:\s*(.)", e)
        ch = m.group(1) if m else ""
        if ch in ("“", "”"):
            return "中文引号"
        if ch == "。":
            return "中文句号"
        if ch in ("​", "‌", "‍", "﻿"):
            return "零宽字符"
        return f"无法识别字符({repr(ch)})"
    if "多余的 token" in e:
        m = re.search(r"多余的 token:\s*\(([^,]+),", e)
        tok = m.group(1).strip() if m else "?"
        return f"多余token({tok})"
    if "意外的 EOF" in e or "未闭合" in e:
        return "未闭合括号"
    return "其他语法错"


def diagnose(export_json: bool = False) -> dict:
    rows = query(
        "SELECT id, violation_code, violation_title, expression_text, category_path "
        "FROM audit_violations "
        "WHERE expression_text IS NOT NULL AND TRIM(expression_text) <> '' AND deleted = b'0'",
        database="tt",
    )
    total = len(rows)
    print("=" * 64)
    print(f"违规表达式体检(语法层):共 {total} 条规则(只读,不写库)")
    print("=" * 64)

    stats = Counter()
    by_error_type = Counter()
    by_layer = Counter()
    failures = []

    for i, r in enumerate(rows, 1):
        expr = r["expression_text"]

        # 表达式层级(纯正则,快)
        layer = classify_expression(expr)
        by_layer[layer] += 1

        # 语法层诊断:直接 parse,不真跑执行(parse_expression 内部已含 _preprocess_chinese)
        try:
            parse_expression(expr)
            stats["syntax_ok"] += 1
        except SyntaxError as e:
            stats["syntax_error"] += 1
            category = _classify_error(str(e))
            by_error_type[category] += 1
            if len(failures) < 30:
                failures.append({
                    "id": r["id"], "code": r["violation_code"],
                    "title": (r["violation_title"] or "")[:20],
                    "category": category, "error": str(e)[:80],
                    "expr": expr[:70],
                })
        except Exception as e:
            stats["syntax_error"] += 1
            category = f"解析异常({type(e).__name__})"
            by_error_type[category] += 1
            if len(failures) < 30:
                failures.append({
                    "id": r["id"], "code": r["violation_code"],
                    "title": (r["violation_title"] or "")[:20],
                    "category": category, "error": str(e)[:80],
                    "expr": expr[:70],
                })

    # ── 报告 ──
    ok = stats["syntax_ok"]
    err = stats["syntax_error"]
    print()
    print("─" * 64)
    print("【语法层诊断结果】")
    print(f"  语法正确(可 parse) : {ok:>5}  ({100*ok//max(total,1)}%)")
    print(f"  语法错误(扫不动)   : {err:>5}  ({100*err//max(total,1)}%)  ← 病A主体,改动②清洗目标")

    print()
    print("【语法错细分(改动②清洗清单依据)】")
    for cat, n in by_error_type.most_common():
        print(f"  {cat:<24} {n:>5} 条")

    print()
    print("【表达式层级分布(决定走哪层执行)】")
    for layer_name, n in by_layer.most_common():
        print(f"  {layer_name:<12} {n:>5}")

    print()
    print(f"【失败样本(前 {len(failures)} 条)】")
    for f in failures:
        print(f"  id={f['id']} [{f['category']}] {f['expr']}")
        print(f"      错误: {f['error']}")

    print()
    print("─" * 64)
    print("【清洗策略指引(基于实测)】")
    if by_error_type.get("中文引号"):
        print(f"  ✦ 中文引号 {by_error_type['中文引号']} 条 → 机械替换 \"\"'' → '\"'(安全,parser不认)")
    if by_error_type.get("中文句号"):
        print(f"  ✦ 中文句号 {by_error_type['中文句号']} 条 → 去除或改半角(安全)")
    if by_error_type.get("零宽字符"):
        print(f"  ✦ 零宽字符 {by_error_type['零宽字符']} 条 → 清除(安全)")
    for cat, n in by_error_type.most_common():
        if cat.startswith("多余token"):
            print(f"  ✦ {cat} {n} 条 → 查该token parser是否支持(可能需人工)")
        if cat.startswith("无法识别字符") and "中文" not in cat:
            print(f"  ✦ {cat} {n} 条 → 查特殊字符(可能需人工)")
    if by_error_type.get("未闭合括号"):
        print(f"  ✦ 未闭合括号 {by_error_type['未闭合括号']} 条 → 需人工补(自动补有歧义风险)")
    if by_error_type.get("其他语法错"):
        print(f"  ✦ 其他语法错 {by_error_type['其他语法错']} 条 → 需人工逐条看")

    report = {
        "total": total,
        "syntax_ok": ok,
        "syntax_error": err,
        "by_error_type": dict(by_error_type),
        "by_layer": dict(by_layer),
        "failures_sample": failures,
    }

    if export_json:
        out = os.path.join(os.environ.get("TEMP", "/tmp"), "diagnose_violations_report.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告已导出: {out}")

    return report


if __name__ == "__main__":
    export = "--json" in sys.argv
    diagnose(export_json=export)
