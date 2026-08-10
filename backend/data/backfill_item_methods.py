"""Phase 7 (P7-6) — 反填 audit_item_methods：违规模型 → 审计方法 → 数据字段要求

数据流程:
  遍历 audit_violations（含 expression_text）→
  解析 expression 引用字段（复用 execution_planner 字段提取正则，PHASE_7 §6.6 允许）→
  经 field_mapper.get_column_for_expr_field(table, field) 映射成 data_* 表列名 →
  写入 tt.audit_item_methods(violation_id, method_name='', method_desc='', data_requirements=JSON数组)

注意:
  - method_name/method_desc 本轮留空（YAML violations[] 只有 expression/description/
    audit_item/suspicion/regulation 五字段，无方法名数据源，用户确认不造数）。
  - data_requirements 由 expression 派生（用户确认）= 表达式引用字段的表列名清单。
  - target_table 用于字段映射（决定查哪张表的别名表）。

target_table 探测说明（与 backfill_engine_rules.py 共用逻辑，用户确认改用中文场探测）:
  原 §6.7 指定 execution_planner.detect_target_table(expr, "")，但其 TABLE_SIGNATURES 是英文
  列名，而 2225 条表达式全用中文场 → 0% 命中、全回退 data_contracts。故改用：对每张 data_* 表
  统计表达式字段经 field_mapper 可映射的「不同列数」，取最高分表（复用既有 FIELD_ALIAS_MAP，
  不造数）。detect_target_table 运行时路径留待 P8-6 处理（本 Phase 不动 analyzer/planner）。

用法:
    cd backend && python data/backfill_item_methods.py          # 试运行
    cd backend && python data/backfill_item_methods.py --run    # 正式反填
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
# SQL 关键词/聚合函数过滤（execution_planner 仅排 8 个，此处适度扩展以产出干净列清单）
_SQL_KW = {
    "and", "or", "not", "between", "null", "true", "false", "like", "in", "is", "as",
    "select", "from", "where", "group", "order", "by", "having", "limit", "join", "on",
    "case", "when", "then", "else", "end", "distinct", "asc", "desc",
    "count", "sum", "avg", "max", "min",
}
_EN_COL_RE = re.compile(r'^[a-z][a-z0-9_]*$')  # 英文蛇形列名判定


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


def _signature_target_table(expression: str) -> str:
    """中文场探测目标表：对每张 data_* 表，统计表达式字段经 field_mapper 可映射的「不同列数」，
    取最高分表。全无可映射字段→回退 data_contracts（detect_target_table 既有默认）。
    """
    fields = _extract_clean_fields(expression)
    if not fields:
        return "data_contracts"
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
    return best_table if best_score >= 1 else "data_contracts"


def _resolve_fields(expression: str, target_table: str) -> list[tuple[str, str]]:
    """解析 expression 引用字段 → [(原字段名, 表列名), ...]

    中文场经 field_mapper 映射成英文列；已是英文蛇形列名原样保留；
    未映射中文/SQL 噪声/数字等略去。
    """
    resolved = []
    for f in _extract_clean_fields(expression):
        col = get_column_for_expr_field(target_table, f)
        if col:
            resolved.append((f, col))
        elif _EN_COL_RE.match(f):
            resolved.append((f, f))  # 已是英文列名（别名表无英文键，原样保留）
        # 其余（未映射中文/噪声）略去
    return resolved


def backfill_item_methods(dry_run: bool = True) -> dict:
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
    nonempty_req = 0
    table_dist = {}
    preview = []

    for r in rows:
        vid = r["id"]
        expr = r["expression_text"]
        target_table = _signature_target_table(expr)
        table_dist[target_table] = table_dist.get(target_table, 0) + 1
        reqs = sorted({col for _, col in _resolve_fields(expr, target_table)})
        if reqs:
            nonempty_req += 1

        if dry_run:
            if len(preview) < 10:
                preview.append({"violation_id": vid, "target_table": target_table,
                                "data_requirements": reqs})
            continue

        # 去重：已存在则跳过（幂等，仿 migrate_violation_law_refs.py 范式）
        existing = query_one(
            "SELECT id FROM audit_item_methods WHERE violation_id = %s LIMIT 1",
            (vid,), database="tt",
        )
        if existing:
            skipped += 1
            continue
        insert(
            "INSERT INTO audit_item_methods (violation_id, method_name, method_desc, data_requirements) "
            "VALUES (%s, '', '', %s)",
            (vid, json.dumps(reqs, ensure_ascii=False) if reqs else None),
            database="tt",
        )
        backfilled += 1
        if (backfilled + skipped) % 200 == 0 and (backfilled + skipped) > 0:
            print(f"  进度: 反填 {backfilled}, 跳过 {skipped}")

    print()
    print(f"target_table 分布: {table_dist}")
    if dry_run:
        print("=== 试运行模式（不写库）— 前 10 条预览 ===")
        for p in preview:
            print(f"  vid={p['violation_id']} table={p['target_table']} reqs={p['data_requirements']}")
        print(f"\ndata_requirements 非空率: {nonempty_req}/{total} ({100*nonempty_req//max(total,1)}%)")
        print("确认无误后执行: python data/backfill_item_methods.py --run")
    else:
        print(f"反填完成: {backfilled} 条新增, {skipped} 条跳过(已存在)")
        print(f"data_requirements 非空率: {nonempty_req}/{total} ({100*nonempty_req//max(total,1)}%)")

    return {
        "dry_run": dry_run, "total": total, "backfilled": backfilled,
        "skipped": skipped, "nonempty_req": nonempty_req, "table_dist": table_dist,
    }


if __name__ == "__main__":
    dry_run = "--run" not in sys.argv
    backfill_item_methods(dry_run=dry_run)
