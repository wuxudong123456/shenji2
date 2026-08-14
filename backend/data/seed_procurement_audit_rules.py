#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为清岳采购案例幂等创建7条确定性规则并绑定6个审计事项。"""
from __future__ import annotations

import argparse
import json
import os
import sys


BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from services.db import execute, insert, query, query_one  # noqa: E402


DEFAULT_PROJECT = "3bf1fcf4fafb"
IMPORT_BATCH = "qingyue-procurement-rules-v1"

RULES = (
    {"code": "GP-PLAN-001", "title": "同一年度同类货物拆分采购规避公开招标",
     "item_seq": 0, "primary": 1, "finding": "F01_SPLIT_TENDER",
     "required": ["年度采购计划", "各批采购资料"],
     "threshold": {"public_tender_threshold": 4000000},
     "laws": [("a00000301932", "中华人民共和国政府采购法实施条例", "第二十八条")]},
    {"code": "GP-METHOD-001", "title": "采购方式未按年度累计规模适用公开招标",
     "item_seq": 1, "primary": 1, "finding": "F01_SPLIT_TENDER",
     "required": ["年度采购计划", "采购审批资料"],
     "threshold": {"public_tender_threshold": 4000000},
     "laws": [("a00000301932", "中华人民共和国政府采购法实施条例", "第二十八条")]},
    {"code": "GP-SUPPLIER-001", "title": "不同响应供应商使用相同联系电话或电子邮箱",
     "item_seq": 2, "primary": 1, "finding": "F06_SHARED_CONTACT",
     "required": ["供应商报价函", "供应商资格资料"], "threshold": {},
     "laws": [("a00000235823", "中华人民共和国政府采购法（2014修正）", "第三条（公平竞争、诚实信用原则）")]},
    {"code": "GP-CONTRACT-001", "title": "采购合同签订日晚于送货日期",
     "item_seq": 3, "primary": 1, "finding": "F02_SIGN_AFTER_DELIVERY",
     "required": ["采购合同", "送货清单"], "threshold": {},
     "laws": [("a00000235823", "中华人民共和国政府采购法（2014修正）", "第四十六条") ]},
    {"code": "GP-CONTRACT-002", "title": "合同追加金额超过原合同金额百分之十",
     "item_seq": 3, "primary": 0, "finding": "F03_ADDITION_OVER_10_PERCENT",
     "required": ["采购合同", "追加款付款申请"],
     "threshold": {"max_addition_ratio": 0.10},
     "laws": [("a00000235823", "中华人民共和国政府采购法（2014修正）", "第四十九条") ]},
    {"code": "GP-ACCEPT-001", "title": "项目验收日期早于送货或安装完成日期",
     "item_seq": 4, "primary": 1, "finding": "F05_ACCEPT_BEFORE_PERFORMANCE",
     "required": ["送货清单", "安装调试记录", "验收报告"], "threshold": {},
     "laws": [("a00013104445", "财政部关于印发《政府采购需求管理办法》的通知", "履约验收管理要求") ]},
    {"code": "GP-FINANCE-001", "title": "同一发票在不同付款或记账凭证中重复列支",
     "item_seq": 5, "primary": 1, "finding": "F04_DUPLICATE_INVOICE",
     "required": ["付款申请", "记账凭证", "发票"], "threshold": {},
     "laws": [("a00014361389", "中华人民共和国会计法（2024修正）", "会计资料真实、完整要求") ]},
)


def build_plan(project_id: str) -> dict:
    items = query(
        "SELECT id, seq, title FROM audit_items WHERE project_id=%s ORDER BY seq, id",
        (project_id,), database="tt",
    )
    by_seq = {int(row["seq"]): row for row in items}
    missing = [rule["item_seq"] for rule in RULES if rule["item_seq"] not in by_seq]
    return {"project_id": project_id, "items": by_seq, "missing_item_seqs": sorted(set(missing)),
            "rules": list(RULES)}


def apply_plan(plan: dict) -> list[dict]:
    if plan["missing_item_seqs"]:
        raise RuntimeError(f"项目缺少事项序号：{plan['missing_item_seqs']}")
    applied = []
    for rule in plan["rules"]:
        violation = query_one(
            "SELECT id FROM audit_violations WHERE violation_code=%s ORDER BY id LIMIT 1",
            (rule["code"],), database="tt",
        )
        required_json = json.dumps(rule["required"], ensure_ascii=False)
        description = "系统确定性检查命中仅表示需进一步核实，不直接替代审计定性。"
        if violation:
            violation_id = violation["id"]
            execute(
                "UPDATE audit_violations SET violation_title=%s, severity=%s, required_data=%s, "
                "description=%s, source_file=%s, import_batch=%s, is_reviewed=1, "
                "review_status='approved', deleted=0 WHERE id=%s",
                (rule["title"], "high", required_json, description,
                 "清岳区采购测试案例/预设疑点及法规依据", IMPORT_BATCH, violation_id),
                database="tt",
            )
        else:
            violation_id = insert(
                "INSERT INTO audit_violations "
                "(violation_code, violation_title, severity, expression_text, required_data, "
                "description, source_file, import_batch, is_reviewed, review_status, creator, deleted) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,'approved','system',0)",
                (rule["code"], rule["title"], "high", f"RULE:{rule['code']}", required_json,
                 description, "清岳区采购测试案例/预设疑点及法规依据", IMPORT_BATCH),
                database="tt",
            )

        engine = query_one(
            "SELECT id FROM audit_engine_rules WHERE violation_id=%s ORDER BY id LIMIT 1",
            (violation_id,), database="tt",
        )
        engine_values = (
            "cross_document", f"RULE:{rule['code']}", "{}",
            json.dumps(rule["threshold"], ensure_ascii=False),
            "procurement_cross_doc", rule["code"], "1.0", rule["finding"],
        )
        if engine:
            execute(
                "UPDATE audit_engine_rules SET target_table=%s, expression=%s, field_mapping=%s, "
                "threshold=%s, executor_type=%s, executor_key=%s, rule_version=%s, "
                "result_group_key=%s WHERE id=%s",
                (*engine_values, engine["id"]), database="tt",
            )
        else:
            insert(
                "INSERT INTO audit_engine_rules "
                "(violation_id,target_table,expression,field_mapping,threshold,executor_type,"
                "executor_key,rule_version,result_group_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (violation_id, *engine_values), database="tt",
            )

        item = plan["items"][rule["item_seq"]]
        # 唯一键不含 project_id，先只处理本项目事项自身的旧绑定，再 upsert 新绑定。
        execute(
            "INSERT INTO audit_item_violation_refs "
            "(item_id,violation_id,project_id,is_primary,match_reason) VALUES (%s,%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE project_id=VALUES(project_id), is_primary=VALUES(is_primary), "
            "match_reason=VALUES(match_reason)",
            (item["id"], violation_id, plan["project_id"], rule["primary"],
             f"{IMPORT_BATCH}：与事项{item['seq'] + 1}的案例事实和预设疑点直接对应"),
            database="tt",
        )

        for law_id, law_title, clause_ref in rule["laws"]:
            law = query_one(
                "SELECT id FROM audit_law.sys_core_law_allaudit WHERE id=%s",
                (law_id,), database="tt",
            )
            if not law:
                continue
            execute(
                "INSERT INTO audit_violation_law_refs (violation_id,law_id,law_title,clause_ref) "
                "VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE law_title=VALUES(law_title), "
                "clause_ref=VALUES(clause_ref)",
                (violation_id, law_id, law_title, clause_ref), database="tt",
            )
        applied.append({"item_id": item["id"], "item_seq": item["seq"],
                        "violation_id": violation_id, "rule_code": rule["code"]})

    new_ids = [row["violation_id"] for row in applied]
    placeholders = ",".join(["%s"] * len(new_ids))
    execute(
        f"DELETE FROM audit_item_violation_refs WHERE project_id=%s "
        f"AND violation_id NOT IN ({placeholders})",
        (plan["project_id"], *new_ids), database="tt",
    )
    return applied


def verify(project_id: str) -> dict:
    rows = query(
        "SELECT i.seq, i.id AS item_id, v.id AS violation_id, v.violation_code, "
        "er.executor_type, er.executor_key, er.result_group_key "
        "FROM audit_items i JOIN audit_item_violation_refs r ON r.item_id=i.id "
        "JOIN audit_violations v ON v.id=r.violation_id "
        "JOIN audit_engine_rules er ON er.violation_id=v.id "
        "WHERE i.project_id=%s AND r.project_id=%s ORDER BY i.seq,v.id",
        (project_id, project_id), database="tt",
    )
    counts = [sum(1 for row in rows if int(row["seq"]) == seq) for seq in range(6)]
    return {"project_id": project_id, "counts_by_item": counts, "ok": counts == [1, 1, 1, 2, 1, 1],
            "bindings": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    plan = build_plan(args.project)
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, default=str, indent=2))
        return
    applied = apply_plan(plan)
    result = verify(args.project)
    print(json.dumps({"applied": applied, "verify": result}, ensure_ascii=False, default=str, indent=2))
    if not result["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
