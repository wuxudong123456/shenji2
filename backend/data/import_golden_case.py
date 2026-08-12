#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采购案例包端到端灌数脚本（幂等可重复）

把 testdata/real_source_adapted_procurement_case 的结构化金标准灌入 tt 库，
让 Step⑤扫描→Step⑥疑点 完整链路能跑通：
  1. 插一个"已 finalize"的项目行（status=active, setup_stage=workspace）
  2. 灌 7 行 data_contracts（CSV/golden_dataset 金标准，字段对齐英文列）
  3. 插 RA-001 违规模型（表达式字段命中 FIELD_ALIAS_MAP，对 7 行全命中）

用法（在仓库根目录）:
    python backend/data/import_golden_case.py          # 试运行（不写库）
    python backend/data/import_golden_case.py --run    # 正式灌库

设计:
  - 复用 services.db（读 .env MYSQL_*，无硬编码密码）
  - 幂等：每步先 DELETE（按 project_id / import_batch）再 INSERT，可重复运行
  - document_trace_id 是 INT 列但金标准里是字符串标记 TRACE-Uxx → 置 NULL（可空，无 FK）
"""
import json
import os
import sys

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from services.db import query, execute  # noqa: E402

PROJECT_ID = "REAL-ADAPT-2019-HN"
VIOLATION_EXPR = "采购方式 = '直接采购' AND 金额 > 0"
IMPORT_BATCH = "golden-case"
_CASE_DIR = os.path.join(
    _BACKEND_DIR, "..", "testdata", "real_source_adapted_procurement_case"
)
GOLDEN_JSON = os.path.normpath(
    os.path.join(_CASE_DIR, "05_结构化金标准", "golden_dataset.json")
)


def _load_contracts():
    """读 golden_dataset.json 的 data_contracts[]，返回列对齐后的 dict 列表"""
    with open(GOLDEN_JSON, encoding="utf-8") as f:
        data = json.load(f)
    rows = []
    for r in data.get("data_contracts", []):
        # extra_fields：金标准里是字符串 JSON，确保落库为合法 JSON 文本
        ef = r.get("extra_fields")
        if isinstance(ef, dict):
            ef = json.dumps(ef, ensure_ascii=False)
        rows.append({
            "project_id": r.get("project_id", PROJECT_ID),
            "document_trace_id": None,  # INT 列，金标准里是字符串标记 → 置 NULL
            "template_name": r.get("template_name"),
            "doc_name": r.get("doc_name"),
            "doc_type": r.get("doc_type"),
            "party_a": r.get("party_a"),
            "party_b": r.get("party_b"),
            "amount": r.get("amount"),
            "currency": r.get("currency"),
            "sign_date": r.get("sign_date"),
            "contract_no": r.get("contract_no"),
            "procurement_method": r.get("procurement_method"),
            "extra_fields": ef,
        })
    return rows


def _insert_project(dry_run):
    """插一个已 finalize 的项目行（幂等：先按 id 删）"""
    if dry_run:
        print(f"  [预览] 将插入项目 id={PROJECT_ID} (status=active, setup_stage=workspace)")
        return
    execute(f"DELETE FROM audit_projects WHERE id = %s", (PROJECT_ID,), database="tt")
    execute(
        "INSERT INTO audit_projects "
        "(id, name, description, audit_period, status, setup_stage, "
        "workspace_created_at, minio_bucket, creator, create_time, update_time, deleted) "
        "VALUES (%s,%s,%s,%s,%s,%s, NOW(), %s, 'system', NOW(), NOW(), 0)",
        (PROJECT_ID, "政府采购审计（改编测试案例）",
         "真实来源改编政府采购审计案例包——验证 Step⑤扫描命中→Step⑥疑点生成完整链路",
         "2019年度", "active", "workspace",
         f"audit-project-{PROJECT_ID.lower()}"),
        database="tt",
    )
    print(f"  项目已插入: id={PROJECT_ID} (active/workspace)")


def _insert_contracts(rows, dry_run):
    """灌 7 行 data_contracts（幂等：先按 project_id 删）"""
    if dry_run:
        print(f"  [预览] 将插入 data_contracts {len(rows)} 行 (project_id={PROJECT_ID})")
        for r in rows:
            print(f"    {r['contract_no']} | {r['party_a']} | {r['procurement_method']} | {r['amount']}")
        return
    execute(
        f"DELETE FROM data_contracts WHERE project_id = %s",
        (PROJECT_ID,), database="tt",
    )
    for r in rows:
        execute(
            "INSERT INTO data_contracts "
            "(project_id, document_trace_id, template_name, doc_name, doc_type, "
            "party_a, party_b, amount, currency, sign_date, contract_no, "
            "procurement_method, extra_fields) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (r["project_id"], r["document_trace_id"], r["template_name"],
             r["doc_name"], r["doc_type"], r["party_a"], r["party_b"],
             r["amount"], r["currency"], r["sign_date"], r["contract_no"],
             r["procurement_method"], r["extra_fields"]),
            database="tt",
        )
    print(f"  data_contracts 已插入: {len(rows)} 行")


def _insert_violation(dry_run):
    """插 RA-001 违规模型（幂等：先按 import_batch + expression_text 删）

    固定 id=1：现有违规 id 全在 8756+，1-100 空闲且 RA-001 无关联表引用，固定 id 安全。
    注意：违规搜索的 ORDER BY（knowledge_service.py:217）只有两个 CASE WHEN title LIKE，
    无 id tiebreaker，故 id 大小并不决定排序先后——RA-001 的可见性靠 (a) 进 _initData 的
    per_page=100 窗口（实测排第 24 位，稳进）+ (b) 前端 rank() 按关键词密度重排取 Top 8
    （标题/分类含"政府采购/采购"密度高，必进 Top 8）。固定 id=1 只为好记，不为排序。
    """
    if dry_run:
        print(f"  [预览] 将插入违规模型 RA-001 (id=1, expression={VIOLATION_EXPR!r})")
        return
    execute(
        f"DELETE FROM audit_violations WHERE import_batch = %s AND expression_text = %s",
        (IMPORT_BATCH, VIOLATION_EXPR), database="tt",
    )
    execute(
        "INSERT INTO audit_violations "
        "(id, violation_code, violation_title, category_path, severity, expression_text, "
        "description, source_file, author, import_batch, is_reviewed, review_status, "
        "creator, create_time, deleted) "
        "VALUES (1,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,'pending','system',NOW(),0)",
        ("RA-001", "应实行未实行政府采购（直接采购且金额大于零）",
         "政府采购/采购方式合规", "medium", VIOLATION_EXPR,
         "对应案例包 RA-001：合同数据.采购方式='直接采购' AND 合同数据.金额>0。"
         "预期命中 7 行（金额合计 10,921,400 元）。",
         "golden_dataset", "案例包", IMPORT_BATCH),
        database="tt",
    )
    print(f"  违规模型已插入: RA-001 (id=1, expression={VIOLATION_EXPR!r})")
    return 1


def main():
    dry_run = "--run" not in sys.argv
    print("=" * 60)
    print("采购案例包灌数" + ("【试运行，不写库】" if dry_run else "【正式执行】"))
    print("=" * 60)

    if not os.path.exists(GOLDEN_JSON):
        print(f"[错误] 找不到金标准文件: {GOLDEN_JSON}")
        sys.exit(1)

    contracts = _load_contracts()
    print(f"\n金标准文件: {GOLDEN_JSON}")
    print(f"data_contracts 行数: {len(contracts)}")

    print("\n— 步骤 1/3：插入项目行 —")
    _insert_project(dry_run)
    print("\n— 步骤 2/3：灌 data_contracts —")
    _insert_contracts(contracts, dry_run)
    print("\n— 步骤 3/3：插入 RA-001 违规 —")
    _insert_violation(dry_run)

    print("\n" + "=" * 60)
    if dry_run:
        print("试运行完成。确认无误后执行: python backend/data/import_golden_case.py --run")
    else:
        print("灌库完成。下一步：前端立项页选「政府采购审计（改编测试案例）」启动分析")
    print("=" * 60)


if __name__ == "__main__":
    main()
