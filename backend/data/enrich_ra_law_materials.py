#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""方案③：补全 RA-001/RA-002（id 1/2，import_golden_case 灌的样本规则）的两条腿：
  ① required_data（审计所需资料，平铺数组格式，与 GP-* 批次一致）——修复 Step② 资料面板 0 类；
  ② audit_violation_law_refs 法规引用——修复 Step③ 选中它们时 0 部法。

红线（同 enrich_procurement_law_refs.py）：
  - law_id 必须在 audit_law.sys_core_law_allaudit 真实存在且「现行有效」；
  - 条款号（第N条）必须能在该法规 content 正文里查到，否则跳过+警告（绝不编造条款）；
  - 只动 violation_id IN (1,2)；
  - 幂等：law refs 已存在的 (violation_id, law_id) 不重复插；required_data 已非空不覆盖；
  - 可回滚：--rollback 恢复 required_data=NULL + 删除本脚本插入的法规行（基线本就是 0 行）。

用法：
  python data/enrich_ra_law_materials.py --dry-run    # 看会写什么（含条款校验）
  python data/enrich_ra_law_materials.py --apply      # 执行
  python data/enrich_ra_law_materials.py --verify     # 查现状
  python data/enrich_ra_law_materials.py --rollback   # 撤销本脚本全部写入
"""
import sys, os, re, json, argparse
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, get_connection

# ---------- 待补数据（law_id 均来自 A 计划已人工核实的现行有效行；条款由脚本再校验一遍） ----------
MATERIALS = {
    1: ["采购申请审批表", "采购合同或订单", "验收单", "发票", "付款凭证", "记账凭证"],
    2: ["采购预算批复或采购计划", "采购方式审批材料", "采购合同", "询价单或招标文件", "验收单", "发票", "付款凭证"],
}
# violation_id -> [(law_id, law_title, clause_ref), ...]
LAW_REFS = {
    # RA-001 应实行未实行政府采购（直接采购）：范围界定条款
    1: [("a00000235823", "中华人民共和国政府采购法（2014修正）", "第二条（政府采购范围：财政性资金采购集中采购目录内或限额标准以上货物、工程和服务）")],
    # RA-002 达到公开招标限额标准未按规定方式采购（大额询价）：方式选择+禁止拆分规避
    2: [("a00000235823", "中华人民共和国政府采购法（2014修正）", "第二十七条（公开招标数额标准；例外方式须经设区的市级以上财政部门批准）"),
        ("a00000301932", "中华人民共和国政府采购法实施条例", "第二十八条（不得化整为零规避公开招标）")],
}


def _clause_marker(clause_ref):
    m = re.search(r"第[一二三四五六七八九十百零]+条", clause_ref or "")
    return m.group(0) if m else None


def _load_law_pool():
    lids = {lid for refs in LAW_REFS.values() for (lid, _, _) in refs}
    placeholders = ",".join(["%s"] * len(lids))
    rows = query(
        f"SELECT id, title, timeliness, CHAR_LENGTH(content) AS clen, content "
        f"FROM sys_core_law_allaudit WHERE id IN ({placeholders})",
        tuple(lids), database="audit_law")
    return {r["id"]: r for r in rows}


def _plan(law_pool):
    """校验法规存在+现行有效+条款在正文。返回 (will_insert, skipped)。"""
    will, skipped = [], []
    for vid, refs in LAW_REFS.items():
        for law_id, law_title, clause_ref in refs:
            law = law_pool.get(law_id)
            if not law:
                skipped.append((vid, law_id, clause_ref, "law_id 不存在于 audit_law（跳过，绝不编造）"))
                continue
            if law["timeliness"] and law["timeliness"] not in ("现行有效", "现行", "有效", "部分有效"):
                skipped.append((vid, law_id, clause_ref, f"时效={law['timeliness']}（非现行，跳过）"))
                continue
            marker = _clause_marker(clause_ref)
            if marker and law.get("content") and marker not in law["content"]:
                skipped.append((vid, law_id, clause_ref, f"条款 {marker} 不在法规正文（跳过，绝不编造条款）"))
                continue
            will.append((vid, law_id, law["title"], clause_ref))
    return will, skipped


def _current_state():
    rd = {r["id"]: r["required_data"] for r in query(
        "SELECT id, required_data FROM audit_violations WHERE id IN (1,2)", (), database="tt")}
    lr = {(r["violation_id"], r["law_id"]): (r["clause_ref"] or "") for r in query(
        "SELECT violation_id, law_id, clause_ref FROM audit_violation_law_refs WHERE violation_id IN (1,2)",
        (), database="tt")}
    return rd, lr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    args = ap.parse_args()

    rd_now, lr_now = _current_state()
    law_pool = _load_law_pool()

    if args.verify:
        print("=== 现状（RA-001/RA-002） ===")
        for vid in (1, 2):
            print(f"  vid={vid} required_data={rd_now.get(vid)!r}")
            for (v, lid), clause in lr_now.items():
                if v == vid:
                    print(f"    law={lid} clause={clause!r}")
        return

    if args.rollback:
        with get_connection(database="tt") as conn:
            cur = conn.cursor()
            n1 = cur.execute("UPDATE audit_violations SET required_data=NULL WHERE id IN (1,2)", ())
            n2 = 0
            for vid, refs in LAW_REFS.items():
                for lid, _, _ in refs:
                    n2 += cur.execute(
                        "DELETE FROM audit_violation_law_refs WHERE violation_id=%s AND law_id=%s", (vid, lid))
            conn.commit()
        print(f"回滚完成：required_data 置空影响 {n1} 行，删除法规引用 {n2} 行")
        return

    will, skipped = _plan(law_pool)
    print("=== 计划写入 ===")
    for vid in (1, 2):
        has_rd = rd_now.get(vid) not in (None, "", "[]", "null")
        print(f"  vid={vid} required_data: {'跳过(已有)' if has_rd else '写入 ' + json.dumps(MATERIALS[vid], ensure_ascii=False)}")
    for vid, law_id, title, clause in will:
        exists = (vid, law_id) in lr_now
        print(f"  法规 vid={vid} {law_id} {title[:24]}… clause={clause[:30]}… {'跳过(已有)' if exists else '插入'}")
    for vid, law_id, clause, why in skipped:
        print(f"  ⚠️ 跳过 vid={vid} {law_id} {clause[:20]}… → {why}")

    if args.apply:
        with get_connection(database="tt") as conn:
            cur = conn.cursor()
            n_rd, n_lr = 0, 0
            for vid in (1, 2):
                if rd_now.get(vid) in (None, "", "[]", "null"):
                    n_rd += cur.execute("UPDATE audit_violations SET required_data=%s WHERE id=%s",
                                        (json.dumps(MATERIALS[vid], ensure_ascii=False), vid))
            for vid, law_id, title, clause in will:
                if (vid, law_id) not in lr_now:
                    n_lr += cur.execute(
                        "INSERT INTO audit_violation_law_refs (violation_id, law_id, law_title, clause_ref) "
                        "VALUES (%s,%s,%s,%s)", (vid, law_id, title, clause))
            conn.commit()
        print(f"=== 已执行：required_data 更新 {n_rd} 行，法规引用插入 {n_lr} 行（跳过 {len(skipped)} 项） ===")
    else:
        print("=== dry-run 结束（未写库；加 --apply 执行） ===")


if __name__ == "__main__":
    main()
