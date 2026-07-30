"""Phase 1.3 — 从 YAML 模板提取 violations[] 导入 tt.audit_violations

用法:
    cd backend && python data/import_violations.py          # 试运行(不写库)
    cd backend && python data/import_violations.py --run    # 正式导入
"""
import sys
import yaml
import json
from pathlib import Path
from datetime import datetime

# 将 backend/ 加入路径以支持直接运行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import query, query_one, execute, insert

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "profiles"
BATCH_ID = datetime.now().strftime("%Y%m%d-%H%M")


def _pid_from_name(name: str) -> str:
    """取模板路径最后两段作为来源标识: audit/合同协议类/买卖合同 -> 合同协议类/买卖合同"""
    parts = name.split("/")
    if len(parts) >= 3:
        return f"{parts[-2]}/{parts[-1]}"
    return name


def _resolve_audititem(audit_item_text: str) -> str | None:
    """尝试将 YAML 的 audit_item 文本匹配到 sys_audititem_SLFF.id

    策略: 精确子串匹配（YAML文本 in SLFF.name 或 SLFF.name in YAML文本）
    """
    if not audit_item_text:
        return None
    # 优先精确子串
    row = query_one(
        "SELECT id, name FROM sys_audititem_SLFF WHERE name LIKE %s LIMIT 1",
        (f"%{audit_item_text}%",),
        database="audit_law",
    )
    if row:
        return row["id"]
    # 反向匹配
    row = query_one(
        "SELECT id, name FROM sys_audititem_SLFF WHERE %s LIKE CONCAT('%%', name, '%%') LIMIT 1",
        (audit_item_text,),
        database="audit_law",
    )
    return row["id"] if row else None


def _regulation_json(raw_regulation) -> str | None:
    """规范化 regulation 字段为 JSON 字符串"""
    if not raw_regulation:
        return None
    # YAML 中 regulation 是一个 JSON 字符串（不是 dict）
    if isinstance(raw_regulation, str):
        try:
            parsed = json.loads(raw_regulation)
            return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            return json.dumps([{"raw": raw_regulation}], ensure_ascii=False)
    # 如果已经被 YAML parser 解析为 list
    if isinstance(raw_regulation, list):
        return json.dumps(raw_regulation, ensure_ascii=False)
    return json.dumps([{"raw": str(raw_regulation)}], ensure_ascii=False)


def load_all_violations() -> list[dict]:
    """遍历所有 YAML 模板，提取 violations 列表"""
    all_violations = []
    stats = {"templates": 0, "with_violations": 0, "without_violations": 0, "errors": 0}

    for yaml_file in TEMPLATES_DIR.rglob("*.yaml"):
        stats["templates"] += 1
        try:
            with open(yaml_file, "r", encoding="utf-8") as f:
                tmpl = yaml.safe_load(f)
        except Exception:
            stats["errors"] += 1
            continue

        if not tmpl or not isinstance(tmpl, dict):
            stats["errors"] += 1
            continue

        violations = tmpl.get("violations", [])
        if not violations:
            stats["without_violations"] += 1
            continue

        stats["with_violations"] += 1
        template_name = tmpl.get("name", "")
        domain = tmpl.get("domain", "")
        tc = tmpl.get("template_class", {})
        category = tc.get("category", "")
        category_path = f"{domain}/{category}/{template_name.split('/')[-1] if template_name else ''}"

        for v in violations:
            all_violations.append({
                "template_name": template_name,
                "domain": domain,
                "category": category,
                "category_path": category_path,
                "expression": v.get("expression", ""),
                "description": v.get("description", ""),
                "audit_item": v.get("audit_item", ""),
                "suspicion": v.get("suspicion", ""),
                "regulation": v.get("regulation", ""),
            })

    return all_violations, stats


def import_violations(dry_run: bool = True) -> dict:
    """导入违规行为到 tt.audit_violations"""
    violations, stats = load_all_violations()
    print(f"扫描模板: {stats['templates']} 个")
    print(f"含 violations: {stats['with_violations']} 个")
    print(f"无 violations: {stats['without_violations']} 个")
    print(f"解析错误: {stats['errors']} 个")
    print(f"违规行为条目: {len(violations)} 条")
    print()

    if dry_run:
        print("=== 试运行模式 (不写库) ===")
        print("前 5 条示例:")
        for i, v in enumerate(violations[:5]):
            print(f"--- #{i+1} ---")
            print(f"  template:        {v['template_name']}")
            print(f"  audit_item:      {v['audit_item']}")
            print(f"  suspicion:       {v['suspicion'][:80]}")
            print(f"  expression:      {v['expression'][:100]}")
            print(f"  description:     {v['description'][:100]}")
            print()

        # 统计 audit_item 分布
        from collections import Counter
        items = Counter(v["audit_item"] for v in violations if v["audit_item"])
        print(f"不同 audit_item 类型: {len(items)}")
        print("Top 10 audit_item:")
        for name, cnt in items.most_common(10):
            print(f"  {cnt:4d}  {name}")

        # 匹配率预估
        matched = 0
        for v in violations:
            if _resolve_audititem(v["audit_item"]):
                matched += 1
        print(f"\naudititem 匹配率: {matched}/{len(violations)} ({100*matched//max(len(violations),1)}%)")

        return {"dry_run": True, "total": len(violations), **stats}

    # ── 正式导入 ──
    print("=== 正式导入 ===")
    inserted = 0
    skipped = 0
    errors = 0
    matched_items = 0

    for idx, v in enumerate(violations):
        # 检查重复: 相同 expression_text 视为重
        existing = query_one(
            "SELECT id FROM audit_violations WHERE expression_text = %s AND deleted = b'0'",
            (v["expression"],),
            database="tt",
        )
        if existing:
            skipped += 1
            continue

        # 尝试匹配审计事项
        audititem_id = _resolve_audititem(v["audit_item"])
        if audititem_id:
            matched_items += 1

        # 生成 violation_code
        seq = inserted + 1
        violation_code = f"VIO-{seq:04d}"

        try:
            insert(
                """INSERT INTO audit_violations
                   (violation_code, violation_title, audititem_id, category_path,
                    severity, expression_text, description,
                    source_file, author, import_batch,
                    is_reviewed, review_status, creator, create_time)
                   VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s, NOW())""",
                (
                    violation_code,
                    v["suspicion"][:500] if v["suspicion"] else "未命名违规行为",
                    audititem_id,
                    v["category_path"][:500],
                    "medium",
                    v["expression"],
                    v["description"],
                    v["template_name"][:255],
                    "",
                    BATCH_ID,
                    0,
                    "pending",
                    "system",
                ),
                database="tt",
            )
            inserted += 1
        except Exception as e:
            errors += 1
            if errors <= 5:
                print(f"  错误 #{errors}: {v['template_name']} — {e}")

        if (inserted + skipped + errors) % 200 == 0:
            print(f"  进度: {inserted + skipped + errors}/{len(violations)} (导入 {inserted}, 跳过 {skipped}, 错误 {errors})")

    print()
    print(f"导入完成: {inserted} 条新增, {skipped} 条跳过(重复), {errors} 条错误")
    print(f"audititem 匹配: {matched_items}/{inserted}")

    return {
        "dry_run": False,
        "total": len(violations),
        "inserted": inserted,
        "skipped": skipped,
        "errors": errors,
        "matched_audititems": matched_items,
        "batch": BATCH_ID,
        **stats,
    }


if __name__ == "__main__":
    dry_run = "--run" not in sys.argv
    result = import_violations(dry_run=dry_run)
    if dry_run:
        print(f"\n确认无误后执行: python data/import_violations.py --run")
