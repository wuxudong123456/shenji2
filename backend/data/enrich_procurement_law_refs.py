#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""丰富清清采购 7 条 GP-* 规则的法律依据（每规则从 1 部 → 多部真实法规）。

红线：
  - law_id 必须在 audit_law.sys_core_law_allaudit 真实存在且「现行有效」；
  - 条款号（第N条）必须能在该法规 content 正文里查到，否则跳过+警告（绝不编造条款）；
  - 只动 qingyue-procurement-rules-v1 批次的 7 条规则（violation_id 10994~11000）；
  - 幂等：已存在的 (violation_id, law_id, clause_ref) 不重复插入；
  - 可回滚：--rollback 按 ADDITIONAL_LAW_REFS 的精确三元组删除（不碰原有 7 行）。

用法：
  python data/enrich_procurement_law_refs.py --dry-run     # 看会插哪些（含条款校验）
  python data/enrich_procurement_law_refs.py --apply       # 执行插入
  python data/enrich_procurement_law_refs.py --verify      # 查现状（每规则几部法）
  python data/enrich_procurement_law_refs.py --rollback    # 删除本脚本新增的行
"""
import sys, os, re, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, get_connection

PROJECT_ID = "3bf1fcf4fafb"
IMPORT_BATCH = "qingyue-procurement-rules-v1"

# 基线：seed_procurement_audit_rules 灌入的原始 7 行主依据（rollback 恢复目标）。
# (violation_id, law_id, law_title, clause_ref)
BASELINE = [
    (10994, "a00000301932", "中华人民共和国政府采购法实施条例", "第二十八条"),
    (10995, "a00000301932", "中华人民共和国政府采购法实施条例", "第二十八条"),
    (10996, "a00000235823", "中华人民共和国政府采购法（2014修正）", "第三条（公平竞争、诚实信用原则）"),
    (10997, "a00000235823", "中华人民共和国政府采购法（2014修正）", "第四十六条"),
    (10998, "a00000235823", "中华人民共和国政府采购法（2014修正）", "第四十九条"),
    (10999, "a00013104445", "财政部关于印发《政府采购需求管理办法》的通知", "履约验收管理要求"),
    (11000, "a00014361389", "中华人民共和国会计法（2024修正）", "会计资料真实、完整要求"),
]

# 每条 GP-* 规则【追加】的法律依据（law_id 均已人工确认为 audit_law 现行有效行）。
# 结构: violation_id -> [(law_id, law_title, clause_ref), ...]
ADDITIONAL_LAW_REFS = {
    10994: [  # GP-PLAN-001 / F01 拆分采购规避公开招标
        ("a00000235823", "中华人民共和国政府采购法（2014修正）", "第二十七条"),
        ("a00001875252", "中华人民共和国招标投标法（2017修正）", "第三条"),
    ],
    10995: [  # GP-METHOD-001 / F01 拆分采购规避公开招标（采购方式层）
        ("a00000235823", "中华人民共和国政府采购法（2014修正）", "第二十七条"),
        ("a00001875252", "中华人民共和国招标投标法（2017修正）", "第三条"),
    ],
    10996: [  # GP-SUPPLIER-001 / F06 供应商共用联系方式（串通）
        ("a00000235823", "中华人民共和国政府采购法（2014修正）", "第七十七条"),
        ("a00000301932", "中华人民共和国政府采购法实施条例", "第七十四条"),
        ("a00001875252", "中华人民共和国招标投标法（2017修正）", "第五十三条"),
    ],
    10997: [  # GP-CONTRACT-001 / F02 合同倒签
        ("a00000301932", "中华人民共和国政府采购法实施条例", "第四十三条"),
    ],
    10998: [  # GP-CONTRACT-002 / F03 超比例追加
        ("a00000301932", "中华人民共和国政府采购法实施条例", "第四十五条"),
    ],
    10999: [  # GP-ACCEPT-001 / F05 先验收后履约
        ("a00000235823", "中华人民共和国政府采购法（2014修正）", "第四十一条"),
        ("a00000301932", "中华人民共和国政府采购法实施条例", "第四十二条"),
    ],
    11000: [  # GP-FINANCE-001 / F04 重复发票
        ("a00000238616", "财政违法行为处罚处分条例（2011修订）", "第十六条"),
        ("a00000235823", "中华人民共和国政府采购法（2014修正）", "第七十六条"),
    ],
}

CN_NUM = {"一":1,"二":2,"三":3,"四":4,"五":5,"六":6,"七":7,"八":8,"九":9,"十":10}


def _clause_marker(clause_ref):
    """从 '第二十七条（...）' 提取 '第二十七条'；无第N条返回 None。"""
    m = re.search(r"第[一二三四五六七八九十百零]+条", clause_ref or "")
    return m.group(0) if m else None


def _load_law_pool():
    """预取每部候选法规的 (title, timeliness, content)；返回 {law_id: row}。"""
    lids = {lid for refs in ADDITIONAL_LAW_REFS.values() for (lid, _, _) in refs}
    if not lids:
        return {}
    placeholders = ",".join(["%s"] * len(lids))
    rows = query(
        f"SELECT id, title, timeliness, potency_level, CHAR_LENGTH(content) AS clen, content "
        f"FROM sys_core_law_allaudit WHERE id IN ({placeholders})",
        tuple(lids), database="audit_law")
    return {r["id"]: r for r in rows}


def _plan(law_pool):
    """校验每个待插三元组：法规存在+现行有效+条款在正文。返回 (will_insert, skipped)。"""
    will, skipped = [], []
    for vid, refs in ADDITIONAL_LAW_REFS.items():
        for law_id, law_title, clause_ref in refs:
            law = law_pool.get(law_id)
            if not law:
                skipped.append((vid, law_id, clause_ref, "law_id 不存在于 audit_law（跳过，绝不编造）"))
                continue
            if law["timeliness"] and law["timeliness"] not in ("现行有效", "现行", "有效", "部分有效"):
                skipped.append((vid, law_id, clause_ref, f"时效={law['timeliness']}（非现行，跳过）"))
                continue
            marker = _clause_marker(clause_ref)
            if marker and law.get("content"):
                if marker not in law["content"]:
                    skipped.append((vid, law_id, clause_ref, f"条款 {marker} 不在法规正文（跳过，绝不编造条款）"))
                    continue
            will.append((vid, law_id, law["title"], clause_ref))
    return will, skipped


def _existing_map():
    """现有 {(violation_id, law_id): clause_ref}（uk_violation_law 保证键唯一）。"""
    rows = query(
        "SELECT violation_id, law_id, clause_ref FROM tt.audit_violation_law_refs "
        "WHERE violation_id BETWEEN 10994 AND 11000", (), database="tt")
    return {(r["violation_id"], r["law_id"]): (r["clause_ref"] or "") for r in rows}


def _classify(law_pool, existing):
    """对每个待加三元组分类，返回 (inserts, merges, idempotent, skipped)。
    inserts: [(vid, law_id, title, clause)] 新法新行
    merges:  [(vid, law_id, new_clause)] 同法已挂→把 new_clause 追加进现有 clause_ref
    """
    will, skipped = _plan(law_pool)
    inserts, merges, idempotent = [], [], []
    for vid, law_id, title, clause in will:
        cur = existing.get((vid, law_id))
        if cur is None:
            inserts.append((vid, law_id, title, clause))
        else:
            marker = _clause_marker(clause)
            if marker and marker in cur:
                idempotent.append((vid, law_id, clause))
            else:
                merged = (cur + "、" + clause) if cur else clause
                merges.append((vid, law_id, merged))
    return inserts, merges, idempotent, skipped


def cmd_dry_run():
    law_pool = _load_law_pool()
    existing = _existing_map()
    inserts, merges, idempotent, skipped = _classify(law_pool, existing)
    print(f"=== DRY-RUN: 法律依据丰富化（采购 GP-* 规则）===\n")
    print(f"候选法规池 {len(law_pool)} 部（已查 audit_law）:")
    for lid, law in law_pool.items():
        print(f"  {lid}  {law['title'][:40]}  [{law['timeliness']}]  正文{law['clen']}字")
    if skipped:
        print(f"\n⚠ {len(skipped)} 行未通过校验已跳过（绝不编造）:")
        for v, l, c, reason in skipped:
            print(f"    vid={v} {l} {c}: {reason}")
    print(f"\n+ 新增行（INSERT）{len(inserts)}:")
    for v, l, t, c in inserts:
        print(f"    vid={v} {l} 《{t[:24]}》 {c}")
    print(f"↔ 合并条款（UPDATE 同法已挂行）{len(merges)}:")
    for v, l, c in merges:
        print(f"    vid={v} {l} → clause_ref 追加为「{c}」")
    print(f"⊙ 幂等跳过 {len(idempotent)}（条款已在现有 clause_ref 中）")
    print(f"\n净变更：{len(inserts)} 插 + {len(merges)} 合并。用 --apply 执行。")


def cmd_apply():
    law_pool = _load_law_pool()
    existing = _existing_map()
    inserts, merges, idempotent, skipped = _classify(law_pool, existing)
    if skipped:
        print(f"⚠ {len(skipped)} 行未通过校验已跳过（见 --dry-run）")
    if not inserts and not merges:
        print("无可变更行（已全部存在/合并或未通过校验）。")
        _print_counts()
        return
    con = get_connection()
    try:
        cur = con.cursor()
        for vid, law_id, title, clause in inserts:
            cur.execute(
                "INSERT INTO tt.audit_violation_law_refs (violation_id, law_id, law_title, clause_ref) "
                "VALUES (%s,%s,%s,%s)", (vid, law_id, title, clause))
        for vid, law_id, merged in merges:
            cur.execute(
                "UPDATE tt.audit_violation_law_refs SET clause_ref=%s "
                "WHERE violation_id=%s AND law_id=%s", (merged, vid, law_id))
        con.commit()
    finally:
        con.close()
    print(f"✓ 已插入 {len(inserts)} 行 + 合并 {len(merges)} 条款。")
    _print_counts()


def cmd_verify():
    _print_counts()


def _print_counts():
    rows = query(
        "SELECT r.violation_id, v.violation_code, COUNT(*) AS law_cnt, "
        "GROUP_CONCAT(CONCAT(law_title,'(',clause_ref,')') SEPARATOR ' | ') AS refs "
        "FROM audit_violation_law_refs r JOIN audit_engine_rules e ON e.violation_id=r.violation_id "
        "JOIN audit_violations v ON v.id=r.violation_id "
        "WHERE e.executor_type='procurement_cross_doc' "
        "GROUP BY r.violation_id, v.violation_code ORDER BY r.violation_id", (), database="tt")
    print("\n=== 现状：每条 GP-* 规则的法律依据 ===")
    for r in rows:
        print(f"  vid={r['violation_id']} {r['violation_code']}: {r['law_cnt']} 部法")
        print(f"      {r['refs']}")


def cmd_rollback():
    """恢复到基线（原始 7 行主依据）：删 10994~11000 全部 ref → 重灌 BASELINE。
    幂等：已是基线态则无变化。"""
    con = get_connection()
    try:
        cur = con.cursor()
        cur.execute("DELETE FROM tt.audit_violation_law_refs "
                    "WHERE violation_id BETWEEN 10994 AND 11000")
        for vid, law_id, title, clause in BASELINE:
            cur.execute(
                "INSERT INTO tt.audit_violation_law_refs "
                "(violation_id, law_id, law_title, clause_ref) VALUES (%s,%s,%s,%s)",
                (vid, law_id, title, clause))
        con.commit()
    finally:
        con.close()
    print(f"✓ 回滚到基线（原始 {len(BASELINE)} 行主依据）。本脚本的新增/合并已撤销。")
    _print_counts()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--verify", action="store_true")
    g.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.dry_run:
        cmd_dry_run()
    elif a.apply:
        cmd_apply()
    elif a.verify:
        cmd_verify()
    else:
        cmd_rollback()
