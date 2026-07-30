"""Phase 3 — 从 YAML 模板拆解违规↔法规关联，写入 audit_violation_law_refs

数据流程:
  YAML violations[].regulation (JSON) → 解析法规引用 →
  匹配 sys_core_law_allaudit.id → 写入 tt.audit_violation_law_refs

用法:
    cd backend && python data/migrate_violation_law_refs.py          # 试运行
    cd backend && python data/migrate_violation_law_refs.py --run    # 正式导入
"""
import sys
import json
import yaml
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import query, query_one, execute, insert
from collections import defaultdict

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "profiles"
BATCH_ID = datetime.now().strftime("%Y%m%d-%H%M")


def _parse_regulation(raw_regulation) -> list[dict]:
    """解析 YAML 中的 regulation 字段为法规引用列表

    regulation 字段可能是:
      - JSON 字符串: '[{"law": "招标投标法", "clause": "第4条"}]'
      - Python list（已被 YAML parser 解析）
      - 纯文本字符串
      - None

    Returns:
        [{"law_title": "中华人民共和国招标投标法", "clause": "第4条"}, ...]
    """
    if not raw_regulation:
        return []

    # 如果是字符串，尝试 JSON 解析
    if isinstance(raw_regulation, str):
        try:
            parsed = json.loads(raw_regulation)
            if isinstance(parsed, list):
                return _normalize_regulation_items(parsed)
            if isinstance(parsed, dict):
                return _normalize_regulation_items([parsed])
            return [{"law_title": str(raw_regulation), "clause": ""}]
        except json.JSONDecodeError:
            return [{"law_title": str(raw_regulation), "clause": ""}]

    # 已经是 list/dict
    if isinstance(raw_regulation, list):
        return _normalize_regulation_items(raw_regulation)
    if isinstance(raw_regulation, dict):
        return _normalize_regulation_items([raw_regulation])

    return []


def _normalize_regulation_items(items: list) -> list[dict]:
    """规范化法规引用条目，提取 law_title 和 clause"""
    result = []
    for item in items:
        if isinstance(item, str):
            result.append({"law_title": item, "clause": ""})
        elif isinstance(item, dict):
            result.append({
                "law_title": item.get("law", item.get("law_title", item.get("name", ""))),
                "clause": item.get("clause", item.get("article", item.get("条", ""))),
            })
    return [r for r in result if r["law_title"]]


def _match_law_id(law_title: str) -> str | None:
    """根据法规名称匹配 sys_core_law_allaudit.id

    策略:
      1. 精确匹配 title = law_title
      2. 子串匹配 title LIKE '%law_title%'
      3. 用书名号提取后匹配: 《招标投标法》→ 匹配含"招标投标法"的 title
    """
    if not law_title:
        return None

    # 去掉书名号和前后空格
    clean = law_title.strip().strip("《").strip("》").strip()

    # 精确匹配
    row = query_one(
        "SELECT id, title FROM sys_core_law_allaudit WHERE title = %s AND status = 1 LIMIT 1",
        (clean,), database="audit_law",
    )
    if row:
        return row["id"]

    # 子串匹配
    row = query_one(
        "SELECT id, title FROM sys_core_law_allaudit WHERE title LIKE %s AND status = 1 LIMIT 1",
        (f"%{clean}%",), database="audit_law",
    )
    if row:
        return row["id"]

    # 反向子串（law_title 包含在数据库 title 中）
    row = query_one(
        "SELECT id, title FROM sys_core_law_allaudit WHERE %s LIKE CONCAT('%%', title, '%%') AND status = 1 LIMIT 1",
        (clean,), database="audit_law",
    )
    if row:
        return row["id"]

    # 用书名号内容匹配：《中华人民共和国招标投标法》→ 招标投标法
    import re
    bracketed = re.findall(r'《([^》]+)》', law_title)
    if bracketed:
        for b in bracketed:
            row = query_one(
                "SELECT id, title FROM sys_core_law_allaudit WHERE title LIKE %s AND status = 1 LIMIT 1",
                (f"%{b}%",), database="audit_law",
            )
            if row:
                return row["id"]

    return None


def load_violation_regulations() -> list[dict]:
    """遍历 YAML 模板，提取每个 violation 的 regulation 引用"""
    mappings = []
    stats = {"templates": 0, "violations": 0, "regulations_found": 0}

    for yaml_file in TEMPLATES_DIR.rglob("*.yaml"):
        stats["templates"] += 1
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                tmpl = yaml.safe_load(f)
        except Exception:
            continue

        if not tmpl or not isinstance(tmpl, dict):
            continue

        violations = tmpl.get("violations", [])
        if not violations:
            continue

        template_name = tmpl.get("name", "")
        for v in violations:
            stats["violations"] += 1
            expression = v.get("expression", "")
            regulation = v.get("regulation")
            refs = _parse_regulation(regulation)

            if refs:
                stats["regulations_found"] += len(refs)

            mappings.append({
                "template_name": template_name,
                "expression": expression,
                "suspicion": v.get("suspicion", ""),
                "regulation_refs": refs,
            })

    return mappings, stats


def migrate_violation_law_refs(dry_run: bool = True) -> dict:
    """执行迁移"""
    mappings, stats = load_violation_regulations()
    print(f"扫描模板: {stats['templates']} 个")
    print(f"违规条目: {stats['violations']} 条")
    print(f"含法规引用: {stats['regulations_found']} 条")
    print()

    if dry_run:
        print("=== 试运行模式 (不写库) ===")
        # 统计法规匹配率
        all_refs = []
        for m in mappings:
            all_refs.extend(m["regulation_refs"])

        unique_laws = set(r["law_title"] for r in all_refs)
        print(f"涉及法规种类: {len(unique_laws)}")
        print()

        matched = 0
        unmatched = []
        for law_title in sorted(unique_laws)[:30]:
            law_id = _match_law_id(law_title)
            if law_id:
                matched += 1
            else:
                unmatched.append(law_title)

        print(f"法规匹配率: {matched}/{len(unique_laws)} ({100*matched//max(len(unique_laws),1)}%)")
        if unmatched:
            print(f"\n未匹配的法规 ({len(unmatched)} 条):")
            for u in unmatched[:10]:
                print(f"  - {u}")
        print(f"\n确认无误后执行: python data/migrate_violation_law_refs.py --run")
        return {"dry_run": True, **stats}

    # ── 正式导入 ──
    print("=== 正式导入 ===")
    inserted = 0
    skipped = 0
    not_found = 0

    for m in mappings:
        if not m["regulation_refs"]:
            continue

        # 查找对应的 violation 记录
        violation_row = query_one(
            "SELECT id, violation_title FROM audit_violations "
            "WHERE expression_text = %s AND source_file = %s AND deleted = b'0'",
            (m["expression"], m["template_name"]), database="tt",
        )
        if not violation_row:
            # 尝试只用 expression_text 匹配
            violation_row = query_one(
                "SELECT id, violation_title FROM audit_violations "
                "WHERE expression_text = %s AND deleted = b'0' LIMIT 1",
                (m["expression"],), database="tt",
            )
        if not violation_row:
            not_found += 1
            continue

        violation_id = violation_row["id"]

        for ref in m["regulation_refs"]:
            law_id = _match_law_id(ref["law_title"])
            if not law_id:
                not_found += 1
                continue

            # 检查去重
            existing = query_one(
                "SELECT id FROM audit_violation_law_refs WHERE violation_id = %s AND law_id = %s",
                (violation_id, law_id), database="tt",
            )
            if existing:
                skipped += 1
                continue

            try:
                insert(
                    "INSERT INTO audit_violation_law_refs (violation_id, law_id, law_title, clause_ref) "
                    "VALUES (%s, %s, %s, %s)",
                    (violation_id, law_id, ref["law_title"], ref["clause"]),
                    database="tt",
                )
                inserted += 1
            except Exception as e:
                print(f"  错误: violation_id={violation_id}, law_id={law_id} — {e}")

        if (inserted + skipped) % 200 == 0 and (inserted + skipped) > 0:
            print(f"  进度: 导入 {inserted}, 跳过 {skipped}, 未匹配 {not_found}")

    print()
    print(f"导入完成: {inserted} 条新增, {skipped} 条跳过(重复), {not_found} 条未匹配")
    return {
        "dry_run": False,
        "inserted": inserted,
        "skipped": skipped,
        "not_found": not_found,
        "batch": BATCH_ID,
        **stats,
    }


if __name__ == "__main__":
    dry_run = "--run" not in sys.argv
    result = migrate_violation_law_refs(dry_run=dry_run)
    if dry_run:
        print(f"\n确认无误后执行: python data/migrate_violation_law_refs.py --run")
