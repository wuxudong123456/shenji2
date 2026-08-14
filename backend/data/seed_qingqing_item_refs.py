#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""灌清清(3bf1fcf4fafb)的事项↔违规规则映射 + 补 common_violations 检索种子

幂等：桥表靠 UNIQUE(item_id,violation_id)+INSERT IGNORE；common_violations 用
JSON_ARRAY_APPEND 追加后再 JSON 去重（多次跑不重复堆积）。
依据：连库实测命中的两条线索——RA-002(询价440万>200万) + 化整为零(三批审批表合计418.8万)。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.db import query, execute

PID = "3bf1fcf4fafb"

# 事项 seq→id（实测：清清 audit_items seq0-5 = id 182-187）
ITEMS = {
    "seq0": 182,  # 采购决策与计划审批合规性
    "seq1": 183,  # 采购方式选择与程序执行审计
    "seq2": 184,  # 供应商选择与资质审查审计
    "seq3": 185,  # 合同签订与执行审计
    "seq4": 186,  # 供货验收与资产登记审计
    "seq5": 187,  # 资金支付与账务处理审计
}

# 映射（依据实测命中线索）
REFS = [
    # (item_id, violation_id, is_primary, match_reason)
    (ITEMS["seq1"], 2,     1, "清清采购计划id=58询价采购440万>200万公开招标限额，命中此规则"),
    (ITEMS["seq1"], 10807, 0, "三批审批表id=62/63/64合计418.8万拆分询价，化整为零规避招标"),
    (ITEMS["seq2"], 10031, 1, "询价采购程序合规性核查（政府采购及招投标程序不规范）"),
    (ITEMS["seq0"], 10716, 0, "采购决策审批环节是否存在拆分项目规避招标核查"),
]

print("=== 0. 校验违规规则 id 真实存在 ===")
vids = sorted(set(r[1] for r in REFS))
rows = query(
    "SELECT id, violation_title FROM audit_violations WHERE id IN (%s)" % ",".join(["%s"] * len(vids)),
    tuple(vids), database="tt",
)
found = {r["id"]: r["violation_title"] for r in rows}
missing = [v for v in vids if v not in found]
if missing:
    print(f"  [错误] 这些 violation_id 不存在: {missing}")
    sys.exit(1)
for v in vids:
    print(f"  id={v}  {found[v]}")

print("\n=== 1. 灌桥表（INSERT IGNORE 幂等）===")
for item_id, vid, is_primary, reason in REFS:
    n = execute(
        "INSERT IGNORE INTO audit_item_violation_refs "
        "(item_id, violation_id, project_id, is_primary, match_reason) "
        "VALUES (%s,%s,%s,%s,%s)",
        (item_id, vid, PID, is_primary, reason), database="tt",
    )
    print(f"  item={item_id} → violation={vid} (is_primary={is_primary})  {'新增' if n else '已存在跳过'}")

print("\n=== 2. 补 common_violations 检索种子（seq1/seq2）===")
# seq1 采购方式：补真实命中的违规标题文本，作 ViolationMatcher LLM 检索种子
SEED = {
    ITEMS["seq1"]: ["大额询价采购应公开招标未招标", "化整为零拆分项目规避公开招标"],
    ITEMS["seq2"]: ["政府采购程序不规范"],
}
for item_id, seeds in SEED.items():
    # JSON_MERGE_PATCH 会用新数组整体替换；先读出旧的再合并去重
    row = query("SELECT common_violations FROM audit_items WHERE id=%s", (item_id,), database="tt")
    if not row:
        continue
    import json
    old = row[0]["common_violations"]
    old_list = json.loads(old) if isinstance(old, str) and old else (old or [])
    if not isinstance(old_list, list):
        old_list = []
    merged = old_list + [s for s in seeds if s not in old_list]
    execute(
        "UPDATE audit_items SET common_violations=%s WHERE id=%s",
        (json.dumps(merged, ensure_ascii=False), item_id), database="tt",
    )
    print(f"  item={item_id} common_violations → {merged}")

print("\n=== 3. 校验结果 ===")
rows = query(
    "SELECT r.item_id, i.title AS item_title, r.violation_id, v.violation_title, "
    "r.is_primary, r.match_reason "
    "FROM audit_item_violation_refs r "
    "JOIN audit_items i ON r.item_id=i.id "
    "JOIN audit_violations v ON r.violation_id=v.id "
    "WHERE r.project_id=%s ORDER BY r.item_id, r.is_primary DESC", (PID,), database="tt")
for r in rows:
    flag = "★主" if r["is_primary"] else " 辅"
    print(f"  [{flag}] {r['item_title'][:14]}  →  {r['violation_title']}")
print(f"\n  桥表清清映射共 {len(rows)} 行")
