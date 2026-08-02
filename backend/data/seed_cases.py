"""P4-1 — 案例库种子数据生成

从 audit_violations 的典型违规生成示例案例骨架，
填充 audit_cases + audit_case_violations + audit_case_law_refs。

用法:
    cd backend && python data/seed_cases.py          # 试运行
    cd backend && python data/seed_cases.py --run     # 正式导入
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.db import query, query_one, execute, insert


# 典型审计领域 → 案例模板
CASE_TEMPLATES = [
    {
        "title": "某市教育局教学设备采购化整为零规避招标案",
        "domain": "政府采购审计",
        "summary": "某市教育局2026年度将总金额约500万元的教学设备采购项目，拆分为5个99万元子项目，分别采用询价方式采购，规避公开招标程序。5个子项目实际均由同一供应商中标。",
        "method": "通过采购合同台账与招标公告交叉比对，发现同一年度内同品类采购被拆分；调取银行流水确认资金实际流向同一供应商。",
        "amount": 4850000,
        "finding": "存在化整为零规避公开招标的违规行为，涉及合同5份，金额合计485万元。",
        "impact": "追回违规采购资金，对相关责任人进行追责，完善采购管理制度。",
        "source": "典型审计案例库",
        "violation_keywords": ["化整为零", "规避公开招标", "应公开招标未招标"],
    },
    {
        "title": "某县农业农村局惠农补贴资金滞留案",
        "domain": "农业农村审计",
        "summary": "某县农业农村局2025年度惠农补贴资金中有320万元超过60日未支付给农户和企业，违反《保障中小企业款项支付条例》。",
        "method": "通过财政补贴发放明细与银行流水比对，计算应付未付款项的时间差。",
        "amount": 3200000,
        "finding": "存在长期拖欠中小企业采购账款的违规行为，涉及23笔应付未付款项。",
        "impact": "督促及时支付欠款，完善资金拨付流程，建立支付时限预警机制。",
        "source": "典型审计案例库",
        "violation_keywords": ["拖欠", "未支付", "逾期", "中小企业"],
    },
    {
        "title": "某市卫健委医疗设备采购围标串标案",
        "domain": "政府采购审计",
        "summary": "某市卫健委2025年医疗设备采购项目中，3家投标企业存在关联关系（同一法人、同一地址），涉嫌围标串标。",
        "method": "通过工商登记信息交叉比对投标企业关联关系，结合投标文件雷同度分析。",
        "amount": 8500000,
        "finding": "存在围标串标嫌疑，3家投标企业为关联企业，投标文件技术参数高度雷同。",
        "impact": "取消中标结果，重新组织招标，将涉嫌违法线索移送司法机关。",
        "source": "典型审计案例库",
        "violation_keywords": ["围标", "串标", "关联", "供应商"],
    },
    {
        "title": "某局违规收取涉企保证金案",
        "domain": "营商环境审计",
        "summary": "某局在采购活动中违规收取质量保证金比例为合同金额的5%，超过法定3%的上限。",
        "method": "通过采购合同与保证金收取台账比对，计算实际收取比例。",
        "amount": 150000,
        "finding": "存在超法定比例收取质量保证金的违规行为，超出部分应予退还。",
        "impact": "退还超收保证金，规范保证金收取行为。",
        "source": "典型审计案例库",
        "violation_keywords": ["保证金", "超比例", "收取"],
    },
    {
        "title": "某单位未按规定发布采购公告案",
        "domain": "政府采购审计",
        "summary": "某单位2025年共有8个达到公开招标限额的采购项目未在指定媒体发布招标公告，直接采用询价方式确定供应商。",
        "method": "通过政府采购网公告记录与实际采购合同比对，核查公告发布情况。",
        "amount": 960000,
        "finding": "存在未按规定发布采购公告的违规行为，涉及8个项目。",
        "impact": "补发公告，对相关程序违规进行整改。",
        "source": "典型审计案例库",
        "violation_keywords": ["公告", "未发布", "采购公告"],
    },
]


def _find_violations(keywords: list[str]) -> list[int]:
    """根据关键词搜索违规模型 ID"""
    ids = []
    for kw in keywords:
        rows = query(
            "SELECT id FROM audit_violations WHERE violation_title LIKE %s AND deleted = 0 LIMIT 3",
            (f"%{kw}%",), database="tt",
        )
        for r in rows:
            if r["id"] not in ids:
                ids.append(r["id"])
    return ids[:5]


def _find_laws_for_violations(violation_ids: list[int]) -> list[str]:
    """从 audit_violation_law_refs 查关联法规 ID"""
    if not violation_ids:
        return []
    placeholders = ",".join(["%s"] * len(violation_ids))
    rows = query(
        f"SELECT DISTINCT law_id FROM audit_violation_law_refs WHERE violation_id IN ({placeholders})",
        tuple(violation_ids), database="tt",
    )
    return [r["law_id"] for r in rows if r["law_id"]][:5]


def seed_cases(dry_run: bool = True) -> dict:
    """生成种子案例"""
    # 检查是否已有数据
    existing = query_one("SELECT COUNT(*) AS n FROM audit_cases", database="tt")
    if existing and existing["n"] > 0:
        print(f"audit_cases 已有 {existing['n']} 条数据，跳过种子生成")
        return {"skipped": True, "existing": existing["n"]}

    print(f"=== 生成 {len(CASE_TEMPLATES)} 个种子案例 ===")
    inserted = 0

    for tmpl in CASE_TEMPLATES:
        # 查关联违规模型
        violation_ids = _find_violations(tmpl["violation_keywords"])
        # 查关联法规
        law_ids = _find_laws_for_violations(violation_ids)

        print(f"\n案例: {tmpl['title']}")
        print(f"  关联违规: {len(violation_ids)} 个 (IDs: {violation_ids})")
        print(f"  关联法规: {len(law_ids)} 个 (IDs: {law_ids})")

        if dry_run:
            continue

        # 插入案例
        case_id = insert(
            "INSERT INTO audit_cases (title, domain, case_summary, audit_method, "
            "involved_amount, audit_finding, audit_impact, source) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (tmpl["title"], tmpl["domain"], tmpl["summary"], tmpl["method"],
             tmpl["amount"], tmpl["finding"], tmpl["impact"], tmpl["source"]),
            database="tt",
        )
        inserted += 1

        # 关联违规
        for vid in violation_ids:
            try:
                insert(
                    "INSERT IGNORE INTO audit_case_violations (case_id, violation_id) VALUES (%s,%s)",
                    (case_id, vid), database="tt",
                )
            except Exception:
                pass

        # 关联法规
        for lid in law_ids:
            try:
                insert(
                    "INSERT IGNORE INTO audit_case_law_refs (case_id, law_id) VALUES (%s,%s)",
                    (case_id, lid), database="tt",
                )
            except Exception:
                pass

    if dry_run:
        print("\n=== 试运行模式（不写库）===")
        print(f"确认无误后执行: python data/seed_cases.py --run")
    else:
        print(f"\n=== 导入完成: {inserted} 个案例 ===")

    return {"inserted": inserted if not dry_run else 0, "dry_run": dry_run}


if __name__ == "__main__":
    dry_run = "--run" not in sys.argv
    seed_cases(dry_run=dry_run)
