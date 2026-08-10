"""Phase 7 (P7-7) — 反填 audit_engine_rules：违规模型 → 分析规则映射

数据流程:
  遍历 audit_violations（含 expression_text）→
  target_table = 中文场探测（见下）→
  field_mapping = expression 引用字段 → field_mapper 映射 → JSON →
  写入 tt.audit_engine_rules(violation_id, target_table, expression, field_mapping, threshold=NULL)

注意:
  - threshold 留 NULL（独立阈值规则走 threshold_rules.yaml + threshold_service，不在此重复，
    避免与 audit_engine_rules.threshold 混淆）。
  - expression 缺省引用 violation.expression_text（PHASE_7 §6.7）。
  - 产出供 Phase8 P8-6 确定性取 target_table（禁用 audit_analyzer._detect_target_table 运行时猜表）。

target_table 探测说明（与 backfill_item_methods.py 共用逻辑，用户确认改用中文场探测）:
  原 §6.7 指定 execution_planner.detect_target_table(expr, "")，但其 TABLE_SIGNATURES 是英文
  列名，而 2225 条表达式全用中文场 → 0% 命中、全回退 data_contracts。故改用：对每张 data_* 表
  统计表达式字段经 field_mapper 可映射的「不同列数」，取最高分表（复用既有 FIELD_ALIAS_MAP，
  不造数）。detect_target_table 运行时路径留待 P8-6 处理（本 Phase 不动 analyzer/planner）。

用法:
    cd backend && python data/backfill_engine_rules.py          # 试运行
    cd backend && python data/backfill_engine_rules.py --run    # 正式反填
"""
import sys
import json
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import query, query_one, insert
from services.field_mapper import get_column_for_expr_field, FIELD_ALIAS_MAP

# 字段提取正则 — 复用 execution_planner.detect_target_table 的 field_pattern（planner.py:23）
_FIELD_RE = re.compile(r'([a-zA-Z_一-鿿][a-zA-Z0-9_一-鿿]*)')
_SQL_KW = {
    "and", "or", "not", "between", "null", "true", "false", "like", "in", "is", "as",
    "select", "from", "where", "group", "order", "by", "having", "limit", "join", "on",
    "case", "when", "then", "else", "end", "distinct", "asc", "desc",
    "count", "sum", "avg", "max", "min",
}
_EN_COL_RE = re.compile(r'^[a-z][a-z0-9_]*$')


def _extract_clean_fields(expression: str) -> list[str]:
    """提取表达式标识符（复用 execution_planner field_pattern），排除 SQL 关键词，去重保序"""
    fields = []
    seen = set()
    for m in _FIELD_RE.finditer(expression or ""):
        f = m.group(1)
        if f.lower() in _SQL_KW or f in seen:
            continue
        seen.add(f)
        fields.append(f)
    return fields


def _signature_target_table(expression: str) -> tuple[str, bool]:
    """中文场探测目标表：对每张 data_* 表，统计表达式字段经 field_mapper 可映射的「不同列数」，
    取最高分表。全无可映射字段→回退 data_contracts（detect_target_table 既有默认）。
    Returns (table, matched): matched=True 至少一字段映射成功，False 回退 data_contracts。
    """
    fields = _extract_clean_fields(expression)
    if not fields:
        return ("data_contracts", False)
    best_table, best_score = None, 0
    for table in FIELD_ALIAS_MAP:
        mapped_cols = set()
        for f in fields:
            col = get_column_for_expr_field(table, f)
            if col:
                mapped_cols.add(col)
        if len(mapped_cols) > best_score:
            best_score = len(mapped_cols)
            best_table = table
    if best_score >= 1:
        return (best_table, True)
    return ("data_contracts", False)


def _resolve_fields(expression: str, target_table: str) -> list[tuple[str, str]]:
    """解析 expression 引用字段 → [(原字段名, 表列名), ...]（去重保序）"""
    resolved = []
    for f in _extract_clean_fields(expression):
        col = get_column_for_expr_field(target_table, f)
        if col:
            resolved.append((f, col))
        elif _EN_COL_RE.match(f):
            resolved.append((f, f))
    return resolved


def backfill_engine_rules(dry_run: bool = True) -> dict:
    """执行反填"""
    rows = query(
        "SELECT id, expression_text FROM audit_violations "
        "WHERE expression_text IS NOT NULL AND TRIM(expression_text) <> '' AND deleted = b'0'",
        database="tt",
    )
    total = len(rows)
    print(f"含表达式 violation 总数: {total}")
    print()

    backfilled = 0
    skipped = 0
    sig_dist = {}        # 中文场探测命中表分布
    fallback_count = 0   # 无任何字段映射→回退 data_contracts 数
    preview = []

    for r in rows:
        vid = r["id"]
        expr = r["expression_text"]
        table, matched = _signature_target_table(expr)
        if matched:
            sig_dist[table] = sig_dist.get(table, 0) + 1
        else:
            fallback_count += 1
        field_mapping = {cn: col for cn, col in _resolve_fields(expr, table)}

        if dry_run:
            if len(preview) < 10:
                preview.append({"violation_id": vid, "target_table": table,
                                "matched": matched, "field_mapping": field_mapping})
            continue

        existing = query_one(
            "SELECT id FROM audit_engine_rules WHERE violation_id = %s LIMIT 1",
            (vid,), database="tt",
        )
        if existing:
            skipped += 1
            continue
        insert(
            "INSERT INTO audit_engine_rules (violation_id, target_table, expression, field_mapping, threshold) "
            "VALUES (%s, %s, %s, %s, NULL)",
            (vid, table, expr,
             json.dumps(field_mapping, ensure_ascii=False) if field_mapping else None),
            database="tt",
        )
        backfilled += 1
        if (backfilled + skipped) % 200 == 0 and (backfilled + skipped) > 0:
            print(f"  进度: 反填 {backfilled}, 跳过 {skipped}")

    print()
    print(f"中文场探测命中表分布: {sig_dist}")
    print(f"回退 data_contracts 数（无字段映射）: {fallback_count}")
    if dry_run:
        print("=== 试运行模式（不写库）— 前 10 条预览 ===")
        for p in preview:
            print(f"  vid={p['violation_id']} table={p['target_table']} matched={p['matched']} mapping={p['field_mapping']}")
        print(f"\n确认无误后执行: python data/backfill_engine_rules.py --run")
    else:
        print(f"反填完成: {backfilled} 条新增, {skipped} 条跳过(已存在)")

    return {
        "dry_run": dry_run, "total": total, "backfilled": backfilled,
        "skipped": skipped, "sig_dist": sig_dist, "fallback_count": fallback_count,
    }


if __name__ == "__main__":
    dry_run = "--run" not in sys.argv
    backfill_engine_rules(dry_run=dry_run)
