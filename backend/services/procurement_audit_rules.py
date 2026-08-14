"""政府采购跨文档确定性审计规则。

本模块只处理可由结构化事实直接证明的判断，不调用 LLM。规则返回统一的
命中、证据和去重键，供执行计划器与疑点生成器复用。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any, Callable


PUBLIC_TENDER_THRESHOLD = Decimal("4000000")
MAX_ADDITION_RATIO = Decimal("0.10")


RULE_DEFINITIONS = {
    "GP-PLAN-001": {
        "name": "年度采购计划拆分规避公开招标",
        "required_roles": ("annual_plan", "procurement_batches"),
        "result_group_key": "F01_SPLIT_TENDER",
        "evaluator": "_evaluate_split_tender",
    },
    "GP-METHOD-001": {
        "name": "采购方式未按累计规模适用公开招标",
        "required_roles": ("annual_plan", "procurement_batches"),
        "result_group_key": "F01_SPLIT_TENDER",
        "evaluator": "_evaluate_split_tender",
    },
    "GP-SUPPLIER-001": {
        "name": "不同供应商使用相同联系方式",
        "required_roles": ("suppliers",),
        "result_group_key": "F06_SHARED_CONTACT",
        "evaluator": "_evaluate_shared_contact",
    },
    "GP-CONTRACT-001": {
        "name": "合同签订晚于履约开始",
        "required_roles": ("contracts", "deliveries"),
        "result_group_key": "F02_SIGN_AFTER_DELIVERY",
        "evaluator": "_evaluate_sign_after_delivery",
    },
    "GP-CONTRACT-002": {
        "name": "合同追加金额超过原合同金额百分之十",
        "required_roles": ("contracts", "contract_additions"),
        "result_group_key": "F03_ADDITION_OVER_10_PERCENT",
        "evaluator": "_evaluate_addition_ratio",
    },
    "GP-ACCEPT-001": {
        "name": "验收日期早于送货或安装完成日期",
        "required_roles": ("deliveries", "acceptances"),
        "result_group_key": "F05_ACCEPT_BEFORE_PERFORMANCE",
        "evaluator": "_evaluate_acceptance_sequence",
    },
    "GP-FINANCE-001": {
        "name": "同一发票在不同凭证中重复报销",
        "required_roles": ("finance",),
        "result_group_key": "F04_DUPLICATE_INVOICE",
        "evaluator": "_evaluate_duplicate_invoice",
    },
}


FACT_TABLES = ("data_procurements", "data_contracts", "data_registers", "data_finance")


def load_project_facts(project_id: str) -> dict[str, list[dict]]:
    """从项目的结构化数据表加载事实；所有查询严格限定 project_id。"""
    if not project_id:
        raise ValueError("project_id 不能为空")
    from services.db import query

    rows_by_table = {}
    for table in FACT_TABLES:
        rows_by_table[table] = query(
            f"SELECT * FROM {table} WHERE project_id = %s ORDER BY id",
            (project_id,),
            database="tt",
        )
    return build_facts_from_rows(rows_by_table)


def build_facts_from_rows(rows_by_table: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """把四张业务表的数据行转换为跨文档规则使用的稳定事实角色。"""
    facts = {role: [] for role in (
        "annual_plan", "procurement_batches", "suppliers", "contracts",
        "contract_additions", "deliveries", "installations", "acceptances", "finance",
    )}

    for raw_row in rows_by_table.get("data_procurements", []):
        row = _merge_extra(raw_row)
        role = _role(row)
        meta = _meta(row)
        if role == "annual_plan" or "年度" in str(row.get("doc_name") or "") and "采购计划" in str(row.get("doc_name") or ""):
            budget = _first_number(
                row.get("budget_amount"),
                _regex_amount(row.get("raw_text"), r"预算控制数(?:为)?(?:人民币)?\s*([0-9,，.]+)"),
            )
            facts["annual_plan"].append({**meta, "budget_amount": budget,
                                          "procurement_method": row.get("procurement_method")})
            facts["procurement_batches"].extend(_parse_plan_batches(row))
        if role == "supplier_response" or "报价函" in str(row.get("doc_name") or ""):
            raw_text = row.get("raw_text")
            facts["suppliers"].append({
                **meta,
                "batch_no": _batch_no(row),
                "supplier_code": row.get("供应商编号") or _filename_code(row, "S"),
                # PDF 文本层的表格单元格可能把“报价人”字段拆行，优先从标题还原完整公司名。
                "supplier": _supplier_from_text(raw_text) or row.get("supplier"),
                "phone": row.get("联系电话") or row.get("phone"),
                # 邮箱经常在分页/窄列处换行，事实层先拼接后再参与跨供应商比对。
                "email": _email_from_text(raw_text) or row.get("电子邮箱") or row.get("email"),
            })
        elif role in ("procurement_request", "procurement_approval"):
            amount = _first_number(row.get("budget_amount"), row.get("contract_amount"))
            batch = _batch_no(row)
            if batch and amount:
                facts["procurement_batches"].append({
                    **meta,
                    "batch_no": batch,
                    "budget_amount": amount,
                    "procurement_method": row.get("procurement_method") or "询价采购",
                })

    for raw_row in rows_by_table.get("data_contracts", []):
        row = _merge_extra(raw_row)
        contract_amount = _first_number(
            row.get("amount"), row.get("合同金额"),
            _regex_amount(
                row.get("raw_text"),
                r"合同(?:含税)?总价(?:为)?(?:人民币)?\s*([0-9,，.]+)",
            ),
        )
        facts["contracts"].append({
            **_meta(row),
            "batch_no": _batch_no(row),
            "contract_no": row.get("contract_no") or row.get("合同编号"),
            "amount": contract_amount,
            "sign_date": _text_date(row.get("sign_date") or row.get("签订日期"))
                         or _filename_date(row),
            "party_a": row.get("party_a"),
            "party_b": row.get("party_b"),
        })

    for raw_row in rows_by_table.get("data_registers", []):
        row = _merge_extra(raw_row)
        role = _role(row)
        base = {**_meta(row), "batch_no": _batch_no(row),
                "contract_no": row.get("contract_no") or row.get("合同编号")}
        if role == "delivery" or "送货清单" in str(row.get("doc_name") or ""):
            facts["deliveries"].append({**base, "delivery_date": _text_date(row.get("送货日期")) or _filename_date(row)})
        elif role == "installation" or "安装调试" in str(row.get("doc_name") or ""):
            facts["installations"].append({**base, "installation_date": _text_date(row.get("安装日期")) or _filename_date(row)})
        elif role == "acceptance" or "验收报告" in str(row.get("doc_name") or ""):
            facts["acceptances"].append({**base, "acceptance_date": _text_date(row.get("验收日期")) or _filename_date(row)})

    for raw_row in rows_by_table.get("data_finance", []):
        row = _merge_extra(raw_row)
        role = _role(row)
        amount = _first_number(
            row.get("申请金额"), row.get("发票金额"), row.get("debit_amount"),
            row.get("credit_amount"), _regex_amount(row.get("raw_text"), r"人民币\s*([0-9,，.]+)\s*元"),
        )
        voucher_no = (
            row.get("voucher_no") or row.get("付款申请编号") or row.get("回单编号")
            or row.get("凭证编号")
        )
        finance_fact = {
            **_meta(row),
            "batch_no": _batch_no(row),
            "contract_no": row.get("合同编号") or row.get("contract_no"),
            "voucher_no": voucher_no,
            "invoice_no": row.get("invoice_no") or row.get("ref_invoice_no")
                          or row.get("发票号码") or row.get("引用发票号码"),
            "amount": amount,
            "role": role,
        }
        if finance_fact["invoice_no"] or voucher_no:
            facts["finance"].append(finance_fact)
        if role == "payment_application" and "追加" in str(row.get("raw_text") or ""):
            facts["contract_additions"].append({
                **_meta(row),
                "batch_no": _batch_no(row),
                "contract_no": row.get("合同编号") or row.get("contract_no"),
                "addition_amount": amount,
                "addition_ratio": _ratio(row.get("追加比例")),
            })

    facts["procurement_batches"] = _dedupe_batches(facts["procurement_batches"])
    return facts


def precheck_rule(rule_code: str, facts: dict[str, list[dict]]) -> dict:
    """检查规则所需文档角色是否齐全，不把资料缺失伪装成零命中。"""
    definition = RULE_DEFINITIONS.get(rule_code)
    if not definition:
        return {
            "verdict": "unknown_rule",
            "missing_roles": [],
            "detail": f"未知确定性规则：{rule_code}",
        }
    missing = [role for role in definition["required_roles"] if not facts.get(role)]
    return {
        "verdict": "hittable" if not missing else "missing_data",
        "missing_roles": missing,
        "detail": "资料角色齐全" if not missing else f"缺少资料角色：{'、'.join(missing)}",
    }


def evaluate_rule(rule_code: str, facts: dict[str, list[dict]], config: dict | None = None) -> dict:
    """执行一条确定性规则并返回统一结果契约。"""
    definition = RULE_DEFINITIONS.get(rule_code)
    if not definition:
        return _failure(rule_code, "unknown_rule", f"未知确定性规则：{rule_code}")

    check = precheck_rule(rule_code, facts)
    if check["verdict"] != "hittable":
        return _failure(
            rule_code,
            check["verdict"],
            check["detail"],
            result_group_key=definition["result_group_key"],
        )

    evaluator: Callable = globals()[definition["evaluator"]]
    rows, total = evaluator(facts, config or {})
    return {
        "success": True,
        "status": "completed",
        "rule_code": rule_code,
        "rule_name": definition["name"],
        "executor_type": "deterministic",
        "result_group_key": definition["result_group_key"],
        "total": total,
        "hits": len(rows),
        "rows": rows,
        "reason": "",
    }


def _failure(rule_code: str, status: str, reason: str, result_group_key: str = "") -> dict:
    return {
        "success": False,
        "status": status,
        "rule_code": rule_code,
        "executor_type": "deterministic",
        "result_group_key": result_group_key,
        "total": 0,
        "hits": 0,
        "rows": [],
        "reason": reason,
    }


def _evaluate_split_tender(facts: dict, config: dict) -> tuple[list[dict], int]:
    threshold = _decimal(config.get("public_tender_threshold")) or PUBLIC_TENDER_THRESHOLD
    plans = facts["annual_plan"]
    batches = facts["procurement_batches"]
    annual_budget = max((_amount(r, "budget_amount") for r in plans), default=Decimal("0"))
    batch_amounts = [_amount(r, "budget_amount", "contract_amount", "amount") for r in batches]
    non_public = [r for r in batches if not _is_public_tender(r.get("procurement_method"))]
    hit = (
        annual_budget >= threshold
        and len(batches) >= 2
        and len(non_public) == len(batches)
        and all(Decimal("0") < amount < threshold for amount in batch_amounts)
    )
    if not hit:
        return [], 1
    evidence = _evidence(plans + batches)
    return [{
        "hit_key": "annual-plan:split-public-tender-threshold",
        "summary": "同一年度采购计划累计达到公开招标门槛，但分批采用非公开招标方式采购",
        "annual_budget": float(annual_budget),
        "batch_total": float(sum(batch_amounts, Decimal("0"))),
        "threshold": float(threshold),
        "batch_count": len(batches),
        "evidence": evidence,
    }], 1


def _evaluate_shared_contact(facts: dict, _config: dict) -> tuple[list[dict], int]:
    suppliers = facts["suppliers"]
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in suppliers:
        identity = _supplier_identity(row)
        if not identity:
            continue
        grouped[identity].append(row)

    supplier_rows = []
    for identity, members in grouped.items():
        phone = next((str(r.get("phone") or "").strip() for r in members if r.get("phone")), "")
        email = next((str(r.get("email") or "").strip() for r in members if r.get("email")), "")
        supplier_rows.append({
            "identity": identity,
            "supplier": next((str(r.get("supplier") or "").strip() for r in members if r.get("supplier")), identity),
            "phone": phone,
            "phone_normalized": phone.lower(),
            "email": email,
            "email_normalized": email.lower(),
            "members": members,
        })

    pair_hits = []
    for idx, left in enumerate(supplier_rows):
        for right in supplier_rows[idx + 1:]:
            shared = []
            if left["phone_normalized"] and left["phone_normalized"] == right["phone_normalized"]:
                shared.append("联系电话")
            if left["email_normalized"] and left["email_normalized"] == right["email_normalized"]:
                shared.append("电子邮箱")
            if not shared:
                continue
            pair_hits.append({
                "hit_key": f"shared-contact:{left['identity']}:{right['identity']}",
                "summary": f"不同供应商使用相同{'、'.join(shared)}",
                "contact_types": shared,
                "phone": left["phone"] if "联系电话" in shared else "",
                "email": left["email"] if "电子邮箱" in shared else "",
                "suppliers": sorted([left["supplier"], right["supplier"]]),
                "evidence": _evidence(left["members"] + right["members"]),
            })
    comparisons = len(supplier_rows) * (len(supplier_rows) - 1) // 2
    return pair_hits, comparisons


def _evaluate_sign_after_delivery(facts: dict, _config: dict) -> tuple[list[dict], int]:
    contracts = _by_contract(facts["contracts"])
    rows = []
    comparisons = 0
    for delivery in facts["deliveries"]:
        contract = contracts.get(_contract_key(delivery))
        if not contract:
            continue
        sign_date = _date(contract.get("sign_date"))
        delivery_date = _date(delivery.get("delivery_date"))
        if not sign_date or not delivery_date:
            continue
        comparisons += 1
        if sign_date > delivery_date:
            contract_no = _contract_key(contract)
            rows.append({
                "hit_key": f"contract:{contract_no}:sign-after-delivery",
                "summary": "合同签订日晚于送货日，存在先履行后签约",
                "contract_no": contract_no,
                "sign_date": sign_date.isoformat(),
                "delivery_date": delivery_date.isoformat(),
                "evidence": _evidence([contract, delivery]),
            })
    return rows, comparisons


def _evaluate_addition_ratio(facts: dict, config: dict) -> tuple[list[dict], int]:
    limit = _decimal(config.get("max_addition_ratio")) or MAX_ADDITION_RATIO
    contracts = _by_contract(facts["contracts"])
    rows = []
    comparisons = 0
    for addition in facts["contract_additions"]:
        contract = contracts.get(_contract_key(addition))
        if not contract:
            continue
        base = _amount(contract, "amount", "contract_amount")
        added = _amount(addition, "addition_amount", "amount")
        ratio = _decimal(addition.get("addition_ratio"))
        if ratio is None and base > 0:
            ratio = added / base
        if base <= 0 or ratio is None:
            continue
        comparisons += 1
        if ratio > limit:
            contract_no = _contract_key(contract)
            rows.append({
                "hit_key": f"contract:{contract_no}:addition-over-limit",
                "summary": "合同追加金额超过原合同金额的百分之十",
                "contract_no": contract_no,
                "contract_amount": float(base),
                "addition_amount": float(added),
                "addition_ratio": float(ratio),
                "limit_ratio": float(limit),
                "evidence": _evidence([contract, addition]),
            })
    return rows, comparisons


def _evaluate_acceptance_sequence(facts: dict, _config: dict) -> tuple[list[dict], int]:
    deliveries = _by_contract(facts["deliveries"])
    installations = _by_contract(facts.get("installations", []))
    rows = []
    comparisons = 0
    for acceptance in facts["acceptances"]:
        key = _contract_key(acceptance)
        delivery = deliveries.get(key)
        installation = installations.get(key)
        completion_rows = [r for r in (delivery, installation) if r]
        completion_dates = [
            value for value in (
                _date((delivery or {}).get("delivery_date")),
                _date((installation or {}).get("installation_date")),
            ) if value
        ]
        acceptance_date = _date(acceptance.get("acceptance_date"))
        if not acceptance_date or not completion_dates:
            continue
        comparisons += 1
        completion_date = max(completion_dates)
        if acceptance_date < completion_date:
            rows.append({
                "hit_key": f"contract:{key}:accept-before-performance",
                "summary": "验收日期早于送货或安装完成日期",
                "contract_no": key,
                "acceptance_date": acceptance_date.isoformat(),
                "performance_completion_date": completion_date.isoformat(),
                "evidence": _evidence([acceptance] + completion_rows),
            })
    return rows, comparisons


def _evaluate_duplicate_invoice(facts: dict, _config: dict) -> tuple[list[dict], int]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in facts["finance"]:
        invoice_no = str(row.get("invoice_no") or "").strip().upper()
        if invoice_no:
            grouped[invoice_no].append(row)

    rows = []
    for invoice_no, members in grouped.items():
        accounting_rows = [r for r in members if r.get("role") == "accounting_voucher"]
        # 单独使用 evaluate_rule 的通用夹具未声明 role 时，仍按不同凭证号判断。
        relevant = accounting_rows or [r for r in members if r.get("role") not in ("invoice", "payment_application")]
        vouchers = {str(r.get("voucher_no") or "") for r in relevant if r.get("voucher_no")}
        if len(vouchers) < 2:
            continue
        rows.append({
            "hit_key": f"invoice:{invoice_no}:duplicate-voucher",
            "summary": "同一发票号码在不同会计凭证中重复列支",
            "invoice_no": invoice_no,
            "voucher_nos": sorted(vouchers),
            "amount_total": float(sum((_amount(r, "amount") for r in relevant), Decimal("0"))),
            "evidence": _evidence(relevant),
        })
    return rows, len(grouped)


def _by_contract(rows: list[dict]) -> dict[str, dict]:
    return {_contract_key(row): row for row in rows if _contract_key(row)}


def _contract_key(row: dict) -> str:
    # 案例中的履约单据以批次 B01/B02/B03 关联，合同则另有正式合同号；
    # 优先使用批次，缺批次时才退回合同号。
    return str(row.get("batch_no") or row.get("contract_no") or "").strip().upper()


def _evidence(rows: list[dict]) -> list[dict]:
    seen = set()
    evidence = []
    for row in rows:
        trace_id = row.get("document_trace_id")
        key = (trace_id, row.get("doc_name"))
        if not trace_id or key in seen:
            continue
        seen.add(key)
        evidence.append({
            "document_trace_id": trace_id,
            "doc_name": row.get("doc_name") or "",
            "page_number": row.get("page_number"),
            "position_anchor": row.get("position_anchor") or "",
        })
    return evidence


def _amount(row: dict, *keys: str) -> Decimal:
    for key in keys:
        value = _decimal(row.get(key))
        if value is not None:
            return value
    return Decimal("0")


def _decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").replace("，", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = normalized.replace("/", "-")
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        return None


def _is_public_tender(value: Any) -> bool:
    text = str(value or "")
    return "公开招标" in text


def _contact_label(field: str) -> str:
    return "联系电话" if field == "phone" else "电子邮箱"


def _supplier_identity(row: dict) -> str:
    return str(row.get("supplier_code") or row.get("supplier") or "").strip().upper()


def _merge_extra(raw_row: dict) -> dict:
    row = dict(raw_row or {})
    extra = row.get("extra_fields")
    if isinstance(extra, str):
        try:
            extra = json.loads(extra)
        except (TypeError, json.JSONDecodeError):
            extra = {}
    if isinstance(extra, dict):
        for key, value in extra.items():
            if row.get(key) in (None, ""):
                row[key] = value
    return row


def _role(row: dict) -> str:
    return str(row.get("文档角色") or row.get("document_role") or "").strip()


def _meta(row: dict) -> dict:
    return {
        "document_trace_id": row.get("document_trace_id"),
        "doc_name": row.get("doc_name") or "",
        "page_number": row.get("page_number") or row.get("页码") or 1,
        "position_anchor": row.get("position_anchor") or row.get("位置锚点") or "page:1",
    }


def _batch_no(row: dict) -> str:
    value = row.get("批次编号") or row.get("batch_no") or _filename_code(row, "B")
    return str(value or "").strip().upper()


def _filename_code(row: dict, prefix: str) -> str:
    name = str(row.get("doc_name") or "")
    match = re.search(rf"(?:^|[_-])({prefix}0[1-9])(?:[_-]|(?=[^A-Za-z0-9]))", name, re.I)
    return match.group(1).upper() if match else ""


def _filename_date(row: dict) -> str | None:
    match = re.search(r"(20\d{2}-\d{2}-\d{2})", str(row.get("doc_name") or ""))
    return match.group(1) if match else None


def _text_date(value: Any) -> str | None:
    parsed = _date(value)
    return parsed.isoformat() if parsed else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _decimal(value)
        if number is not None:
            return float(number)
    return None


def _regex_amount(text: Any, pattern: str) -> str | None:
    match = re.search(pattern, str(text or ""))
    return match.group(1) if match else None


def _supplier_from_text(text: Any) -> str:
    content = str(text or "")
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9（）()·]+有限公司)报价函", content)
    if match:
        return match.group(1).strip()
    match = re.search(r"报价人[\s:：]+([^；;。\n（(]+有限公司)", content)
    return match.group(1).strip() if match else ""


def _email_from_text(text: Any) -> str:
    """从 PDF 文本层还原可能被换行拆开的电子邮箱。"""
    content = str(text or "")
    match = re.search(r"电子邮箱[\s:：]+([^；;]+)", content, re.I)
    if not match:
        return ""
    candidate = re.sub(r"\s+", "", match.group(1)).strip("。,.，")
    valid = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", candidate)
    return valid.group(0) if valid else ""


def _parse_plan_batches(row: dict) -> list[dict]:
    text = str(row.get("raw_text") or "")
    results = []
    for match in re.finditer(
        r"\b(B0[1-9])\b\s*\|[^\n]*?\|\s*(公开招标|询价采购|竞争性谈判|竞争性磋商|单一来源采购)\s*\|\s*([0-9,，.]+)",
        text,
        re.I,
    ):
        results.append({
            **_meta(row),
            "batch_no": match.group(1).upper(),
            "procurement_method": match.group(2),
            "budget_amount": _first_number(match.group(3)),
        })
    if results:
        return results
    # PDF 表格文本层常按单元格逐行展开，批次到金额之间允许跨多行，但不能越过下一批。
    for match in re.finditer(r"(?ms)^\s*(B0[1-9])\s*$([\s\S]*?)(?=^\s*B0[1-9]\s*$|^\s*合计\s*$)", text):
        block = match.group(2)
        method = re.search(r"(公开招标|询价采购|竞争性谈判|竞争性磋商|单一来源采购)", block)
        amounts = re.findall(r"(?m)^\s*([0-9]{1,3}(?:[,，][0-9]{3})+(?:\.\d+)?)\s*$", block)
        if method and amounts:
            results.append({
                **_meta(row), "batch_no": match.group(1).upper(),
                "procurement_method": method.group(1), "budget_amount": _first_number(amounts[-1]),
            })
    return results


def _dedupe_batches(rows: list[dict]) -> list[dict]:
    by_batch = {}
    for row in rows:
        key = str(row.get("batch_no") or "").strip().upper()
        if not key:
            continue
        current = by_batch.get(key)
        if not current or (not current.get("budget_amount") and row.get("budget_amount")):
            by_batch[key] = row
    return [by_batch[key] for key in sorted(by_batch)]


def _ratio(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    number = _decimal(text.rstrip("%"))
    if number is None:
        return None
    if text.endswith("%") or number > 1:
        number = number / Decimal("100")
    return float(number)
