"""字段名映射器 — OntoSKU 中文字段 → data_xxx 表英文列名

解决"字段名映射鸿沟":
  - OntoSKU 提取返回中文字段名（甲方/合同金额/采购方式...）
  - data_xxx 表用英文列名（party_a/amount/procurement_method...）
  - 违规表达式又用中文字段名引用

本模块提供双向映射:
  map_extracted_fields(table, fields) — 提取结果 → 表列（写入用）
  get_column_for_expr_field(table, cn_field_name) — 表达式中文字段 → 表列（扫描用）
"""
import re
import json
from typing import Any

# ── 中文别名 → 英文列名（每张表的别名集）──
# 别名收集自 YAML 模板 output.fields[] 常见字段名 + 业务同义词
FIELD_ALIAS_MAP: dict[str, dict[str, str]] = {
    "data_contracts": {
        # 甲方
        "甲方": "party_a", "采购人": "party_a", "采购单位": "party_a", "采购单位名称": "party_a",
        "甲方名称": "party_a", "买方": "party_a", "订购方": "party_a", "采购方": "party_a",
        # 乙方
        "乙方": "party_b", "供应商": "party_b", "供应商名称": "party_b", "中标人": "party_b",
        "中标单位": "party_b", "中标供应商": "party_b", "卖方": "party_b", "供货方": "party_b",
        "供货单位": "party_b", "承包方": "party_b", "合同乙方名称": "party_b",
        # 金额（含 OntoSKU 信封键「涉及金额」= 文档所述交易额）
        "合同金额": "amount", "金额": "amount", "采购金额": "amount", "合同总价": "amount",
        "总价": "amount", "中标金额": "amount", "成交金额": "amount", "价款": "amount",
        "合同总额": "amount", "总额": "amount", "合计金额": "amount", "价税合计": "amount",
        "不含税金额": "amount", "合同不含税金额": "amount", "预算金额": "amount", "涉及金额": "amount",
        # 币种
        "币种": "currency", "货币": "currency",
        # 日期
        "签订日期": "sign_date", "合同签订日期": "sign_date", "签署日期": "sign_date", "订立日期": "sign_date",
        "签约日期": "sign_date", "合同日期": "sign_date",
        "生效日期": "effective_date", "起效日期": "effective_date",
        "终止日期": "expiry_date", "到期日期": "expiry_date", "结束日期": "expiry_date", "履约期限": "expiry_date",
        # 编号
        "合同编号": "contract_no", "合同号": "contract_no", "协议编号": "contract_no",
        # 采购方式
        "采购方式": "procurement_method", "招标方式": "procurement_method",
        # 批次（GP-PLAN-001 聚合三批合同）
        "批次编号": "batch_no", "批次": "batch_no",
    },
    "data_finance": {
        "账户名称": "account_name", "户名": "account_name", "开户名称": "account_name",
        "账号": "account_no", "银行账号": "account_no", "账户号码": "account_no",
        "借方金额": "debit_amount", "借方": "debit_amount", "借方发生额": "debit_amount",
        "贷方金额": "credit_amount", "贷方": "credit_amount", "贷方发生额": "credit_amount",
        "凭证号": "voucher_no", "凭证编号": "voucher_no", "记账凭证号": "voucher_no",
        "凭证日期": "voucher_date", "记账日期": "voucher_date", "制单日期": "voucher_date",
        "银行名称": "bank_name", "开户行": "bank_name", "开户银行": "bank_name",
        "币种": "currency",
        # 发票本身（GP-FINANCE-001：发票号被多凭证引用）
        "发票号": "invoice_no", "发票号码": "invoice_no", "票据编号": "invoice_no",
        # 发票金额：OntoSKU 的"发票金额"常=未提供，真值落在 金额合计/金额/涉及金额(信封键，最稳)。
        # 用精确别名兜住 金额，避免它被模糊匹配到 借方金额→debit_amount。
        "发票金额": "invoice_amount", "价税合计": "invoice_amount", "金额合计": "invoice_amount",
        "金额": "invoice_amount", "涉及金额": "invoice_amount",
        # 凭证引用的原始发票号（跨文档匹配键）
        "关联原始凭证编号": "ref_invoice_no", "原始凭证编号": "ref_invoice_no",
        "附件发票号": "ref_invoice_no", "引用发票号码": "ref_invoice_no",
        # 批次（跨文档连接键）
        "批次编号": "batch_no", "批次": "batch_no",
    },
    "data_legal_docs": {
        "案件编号": "case_no", "案号": "case_no", "文书编号": "case_no",
        "发布机关": "issuing_body", "出具机关": "issuing_body", "裁决机关": "issuing_body",
        "发文机关": "issuing_body", "颁布机关": "issuing_body",
        "文书日期": "doc_date", "发文日期": "doc_date", "判决日期": "doc_date", "裁决日期": "doc_date",
        "文档日期": "doc_date",
        "生效日期": "effective_date",
        "法律依据": "legal_basis", "法律条文": "legal_basis", "适用依据": "legal_basis",
        "判决": "verdict", "判决结果": "verdict", "裁决": "verdict", "结论": "verdict", "处理决定": "verdict",
    },
    "data_registers": {
        "登记类型": "register_type", "台账类型": "register_type", "名册类型": "register_type",
        "项目名称": "item_name", "事项名称": "item_name", "品名": "item_name", "事项": "item_name",
        "数量": "quantity", "数目": "quantity",
        "单位": "unit", "计量单位": "unit",
        "责任人": "responsible_person", "经办人": "responsible_person", "负责人": "responsible_person",
        "登记日期": "register_date", "台账日期": "register_date", "记录日期": "register_date",
        # 履约单据各自的关键日期（每份单据一行，register_type 区分）
        "送货日期": "register_date", "交付日期": "register_date", "到货日期": "register_date",
        "安装日期": "register_date", "调试日期": "register_date",
        "验收日期": "register_date", "验收时间": "register_date",
        "资产登记日期": "register_date",
        # 文档角色（enrich 设的 delivery/installation/acceptance…）→ 区分履约行类型
        "文档角色": "register_type",
        # 批次（GP-ACCEPT/CONTRACT 跨文档按批次连接）
        "批次编号": "batch_no", "批次": "batch_no",
    },
    "data_credentials": {
        "证照类型": "cert_type", "资质类型": "cert_type", "证书类型": "cert_type", "执照类型": "cert_type",
        "证照编号": "cert_no", "证书编号": "cert_no", "资质编号": "cert_no", "执照编号": "cert_no",
        "持有人": "holder", "持有者": "holder", "权利人": "holder", "所有者": "holder",
        "签发日期": "issue_date", "发证日期": "issue_date", "颁发日期": "issue_date",
        "失效日期": "expire_date", "有效期至": "expire_date", "到期日期": "expire_date",
        "签发机关": "issuing_body", "发证机关": "issuing_body", "颁发机关": "issuing_body",
    },
    "data_general": {
        "分类": "category", "类别": "category",
        "标题": "title", "名称": "title", "主题": "title",
        "name": "title", "文档标题": "title",
        "摘要": "summary", "内容简介": "summary", "概述": "summary", "description": "summary",
        "发布机关": "issuing_body", "发文单位": "issuing_body", "发布单位": "issuing_body",
        "日期": "doc_date", "发文日期": "doc_date", "发布日期": "doc_date", "文档日期": "doc_date",
    },
    "data_procurements": {
        # 采购方式
        "采购方式": "procurement_method", "招标方式": "procurement_method",
        # 采购项目名称/标的
        "采购项目名称": "subject_name", "项目名称": "subject_name", "采购标的": "subject_name",
        "标的名称": "subject_name", "采购内容": "subject_name",
        # 供应商
        "供应商": "supplier", "供应商名称": "supplier", "中标人": "supplier",
        "中标单位": "supplier", "中标供应商": "supplier",
        # 供应商联系方式（GP-SUPPLIER-001：不同供应商共用电话/邮箱）
        "联系电话": "supplier_phone", "电话": "supplier_phone", "联系方式": "supplier_phone",
        "手机": "supplier_phone",
        "电子邮箱": "supplier_email", "邮箱": "supplier_email", "email": "supplier_email",
        # 批次
        "批次编号": "batch_no", "批次": "batch_no",
        # 预算金额
        "预算金额": "budget_amount", "采购预算": "budget_amount", "预算价": "budget_amount",
        "控制价": "budget_amount", "最高限价": "budget_amount",
        "预算控制数": "budget_amount", "预算指标": "budget_amount",
        # 合同/中标金额（含 OntoSKU 信封键「涉及金额」= 文档所述交易额）
        "合同金额": "contract_amount", "中标金额": "contract_amount", "成交金额": "contract_amount",
        "合同价": "contract_amount", "采购金额": "contract_amount",
        "涉及金额": "contract_amount", "合同总价": "contract_amount",
        "合同总额": "contract_amount", "总价": "contract_amount",
        # 招标/开标日期
        "招标日期": "bid_date", "开标日期": "bid_date", "投标截止日期": "bid_date",
        # 签订日期
        "签订日期": "sign_date", "合同签订日期": "sign_date",
        "合同日期": "sign_date", "签约日期": "sign_date",
    },
    "data_interviews": {
        "被访谈人": "interviewee", "受访人": "interviewee", "谈话对象": "interviewee",
        "被询问人": "interviewee", "访谈对象": "interviewee",
        "访谈日期": "interview_date", "谈话日期": "interview_date", "询问日期": "interview_date",
        "访谈地点": "location", "谈话地点": "location", "地点": "location", "场所": "location",
        # 谈话内容/笔录（OntoSKU 常见键）
        "谈话内容": "transcript", "笔录": "transcript", "谈话笔录": "transcript", "转写": "transcript",
    },
}

# ── 表达式中文字段名 → 表列名（用于扫描时取值）──
# 复用 FIELD_ALIAS_MAP，但需要时可扩展（违规表达式用的字段名可能和模板字段名略不同）
EXPR_FIELD_MAP = FIELD_ALIAS_MAP  # 当前共用同一张表


def _normalize_table_name(table: str) -> str:
    """标准化表名（允许简写: contracts → data_contracts）"""
    if not table:
        return "data_general"
    if not table.startswith("data_"):
        table = f"data_{table}"
    return table


def map_extracted_fields(table: str, extracted_fields: dict | list) -> tuple[dict, dict]:
    """把 OntoSKU 提取的中文字段映射到 data_xxx 表列

    Args:
        table: 目标表名（data_contracts / contracts / ...）
        extracted_fields: OntoSKU 提取结果
            dict 形式: {"甲方": "XX局", "合同金额": "100万", ...}
            list 形式: [{"name": "甲方", "value": "XX局"}, ...]（LiteParse 风格）

    Returns:
        (row_dict, extra_fields):
            row_dict: 能映射到表列的 {列名: 值}
            extra_fields: 无法映射的 {中文字段名: 值}，存入 extra_fields JSON 列
    """
    table = _normalize_table_name(table)
    alias = FIELD_ALIAS_MAP.get(table, {})

    # 统一转成 {字段名: 值} 的 dict
    flat = {}
    if isinstance(extracted_fields, dict):
        # 可能是 {字段名: 值} 或 {字段名: {value: ...}}
        for k, v in extracted_fields.items():
            if isinstance(v, dict):
                flat[k] = v.get("value", v.get("text", ""))
            elif isinstance(v, (list, tuple)) and len(v) > 0:
                flat[k] = v[0] if not isinstance(v[0], (list, dict)) else str(v[0])
            else:
                flat[k] = v
    elif isinstance(extracted_fields, list):
        for item in extracted_fields:
            if isinstance(item, dict):
                name = item.get("name", item.get("field", ""))
                value = item.get("value", item.get("text", ""))
                if name:
                    flat[name] = value
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                flat[str(item[0])] = item[1]

    row_dict = {}
    extra_fields = {}

    for cn_name, value in flat.items():
        # 防御首尾空白键（OntoSKU 偶发），strip 后再精确/模糊匹配
        cn = cn_name.strip() if isinstance(cn_name, str) else cn_name
        col = alias.get(cn) or _fuzzy_match_alias(cn, alias)
        if col:
            casted = _cast_value(col, value, table)
            # 防 last-wins 覆盖：仅当列未赋值或现值为空(None/"")时才写入。
            # 这样允许 None→真值（后续真值补上），但阻止真值被后面的"未提供"→None 覆盖。
            if row_dict.get(col) in (None, ""):
                row_dict[col] = casted
        else:
            extra_fields[cn] = value

    return row_dict, extra_fields


def _fuzzy_match_alias(cn_name: str, alias: dict[str, str]) -> str | None:
    """模糊匹配字段名（处理细微差异，如"合同金额（元）"→"合同金额"）"""
    for alias_key, col in alias.items():
        if alias_key in cn_name or cn_name in alias_key:
            return col
    return None


def _cast_value(col: str, value: Any, table: str) -> Any:
    """根据列类型做值转换

    - 数值列：从"100万""1,234.56元"提取 float，提取失败返回 None
    - 日期列：必须是合法日期，否则返回 None（避免 MySQL 'Incorrect date value' 报错）
    - 其他列：原样返回
    """
    # 空值或 LLM 常见的"未提供"/"无"/"未知"统一转 None（这些值不该写入业务列）
    if value is None or value == "" or str(value).strip() in ("未提供", "无", "未知", "暂无", "未填写", "null", "None", "-"):
        return None

    # 数值列
    NUMERIC_COLS = {"amount", "debit_amount", "credit_amount", "quantity",
                    "budget_amount", "contract_amount", "invoice_amount"}
    if col in NUMERIC_COLS:
        try:
            s = str(value)
            # 识别"万/亿"单位并换算（与模板 guideline"保留万元/亿元单位"一致）
            mult = 1
            if "亿" in s:
                mult = 100000000
            elif "万" in s:
                mult = 10000
            num_str = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
            return float(num_str) * mult if num_str else None
        except (ValueError, TypeError):
            return None

    # 日期列：校验是否合法日期，不合法返回 None
    DATE_COLS = {
        "sign_date", "effective_date", "expiry_date",
        "voucher_date", "doc_date", "register_date",
        "issue_date", "expire_date",
        "bid_date", "interview_date",
    }
    if col in DATE_COLS:
        if _is_valid_date(str(value)):
            return _normalize_date(str(value))
        return None  # '未提供' 等无效日期 → None

    return value


def _is_valid_date(s: str) -> bool:
    """判断字符串是否像日期（YYYY-MM-DD / YYYY/MM/DD / 含年月日）"""
    s = s.strip()
    for pat in (r"^\d{4}-\d{1,2}-\d{1,2}", r"^\d{4}/\d{1,2}/\d{1,2}",
                r"^\d{4}年\d{1,2}月\d{1,2}日", r"^\d{8}$"):
        if re.match(pat, s):
            return True
    return False


def _normalize_date(s: str) -> str:
    """把各种日期格式归一化为 YYYY-MM-DD"""
    s = s.strip()
    m = re.match(r"^(\d{4})\D(\d{1,2})\D(\d{1,2})", s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.match(r"^(\d{4})(\d{2})(\d{2})$", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return s


def get_column_for_expr_field(table: str, cn_field_name: str) -> str | None:
    """违规表达式中的中文字段名 → 表列名（扫描时取值用）

    Args:
        table: 数据表名
        cn_field_name: 表达式里的中文字段名（如"采购方式"）

    Returns:
        英文列名（如"procurement_method"），未找到返回 None
    """
    table = _normalize_table_name(table)
    alias = EXPR_FIELD_MAP.get(table, {})

    # 精确匹配
    if cn_field_name in alias:
        return alias[cn_field_name]

    # 模糊匹配
    return _fuzzy_match_alias(cn_field_name, alias)


def get_all_aliases(table: str) -> dict[str, str]:
    """获取某张表的全部字段别名（调试/管理界面用）"""
    table = _normalize_table_name(table)
    return FIELD_ALIAS_MAP.get(table, {}).copy()


def enrich_fields_from_text(fields: dict, markdown: str, filename: str = "") -> dict:
    """从 OCR 文本里 regex 补抽 LLM 可能漏掉的关键审计字段。

    只填充缺失/空/未提供 的字段——绝不覆盖已有真值。
    适用于"采购方式 公开招标"这种键值对格式（OCR 常见）。
    """
    if not markdown and not filename:
        return fields
    # 关键审计字段 → 正则（匹配 "字段名: 值" / "字段名  值" 等格式）
    _PATTERNS = {
        "采购方式": r"采购方式[\s:：]+(\S+)",
        "合同金额": r"(?:合同(?:含税)?总价|合同金额|合同总额)(?:为)?[\s:：]*(?:人民币)?\s*([0-9,，.]+\s*[万元元亿吨]*)",
        "合同编号": r"(?:合同编号|合同号)[\s:：|]+([A-Za-z0-9\-]+)",
        "供应商": r"(?:供应商|乙方|中标人|中标单位|报价人)[\s:：]+([^；;。\n（(]+)",
        "采购人": r"(?:采购人|甲方|采购单位)[\s:：]+(\S+)",
        "签订日期": r"(?:签订日期|签署日期|签约日期)[\s:：]+([0-9\-/年月日]+)",
        "联系电话": r"联系电话[\s:：|]+([^；;\s|]+)",
        "电子邮箱": r"电子邮箱[\s:：|]+([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+)",
        "付款申请编号": r"付款申请编号[\s:：|]+([A-Za-z0-9\-]+)",
        "凭证号": r"(?:凭证号|凭证编号|记账凭证号)[\s:：|]+([A-Za-z0-9\-]+)",
        "发票号码": r"发票号码[\s:：|]+([A-Za-z0-9\-]+)",
        "申请金额": r"申请金额[\s:：|]+([0-9,，.]+)",
        "预算控制数": r"(?:预算控制数|预算指标)(?:为)?[\s:：]*(?:人民币)?\s*([0-9,，.]+\s*[万元元]*)",
        "借方金额": r"借方金额[\s:：|]+([0-9,，.]+)",
        "贷方金额": r"贷方金额[\s:：|]+([0-9,，.]+)",
        "交易金额": r"交易金额[\s:：|]+([0-9,，.]+)",
        "送货单号": r"送货单号[\s:：|]+([A-Za-z0-9\-]+)",
        "安装记录编号": r"安装记录编号[\s:：|]+([A-Za-z0-9\-]+)",
        "验收编号": r"验收编号[\s:：|]+([A-Za-z0-9\-]+)",
        "报价编号": r"报价编号[\s:：|]+([A-Za-z0-9\-]+)",
    }
    for cn_name, pattern in _PATTERNS.items():
        existing = fields.get(cn_name)
        if existing and str(existing).strip() and str(existing).strip() not in ("未提供", "无", "未知", "暂无", "-"):
            continue  # 已有真值，跳过
        m = re.search(pattern, markdown, re.MULTILINE)
        if m:
            fields[cn_name] = m.group(1).strip()

    # 文件名是该案例最稳定的文档角色、批次和业务日期来源；仅补空值。
    fname = filename or ""
    role_specs = (
        (("设备采购合同", "采购合同"), "contract", "签订日期"),
        (("送货清单",), "delivery", "送货日期"),
        (("安装调试记录",), "installation", "安装日期"),
        (("验收报告",), "acceptance", "验收日期"),
        (("报价函",), "supplier_response", None),
        (("资格审查",), "supplier_qualification", None),
        (("评审记录", "成交意见"), "evaluation", None),
        (("成交通知",), "award_notice", None),
        (("付款申请",), "payment_application", "付款日期"),
        (("银行电子回单", "银行回单"), "bank_receipt", "付款日期"),
        (("记账凭证",), "accounting_voucher", "凭证日期"),
        (("增值税发票",), "invoice", "发票日期"),
        (("采购计划",), "annual_plan", "文档日期"),
        (("采购需求申请",), "procurement_request", "文档日期"),
        (("采购审批表",), "procurement_approval", "文档日期"),
    )
    for keywords, role, date_field in role_specs:
        if any(keyword in fname for keyword in keywords):
            fields.setdefault("文档角色", role)
            date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", fname)
            if date_field and date_match:
                fields.setdefault(date_field, date_match.group(1))
            break

    batch_match = re.search(r"(?:^|[_-])(B0[1-9])(?:[_-]|(?=[^A-Za-z0-9]))", fname, re.IGNORECASE)
    if batch_match:
        fields.setdefault("批次编号", batch_match.group(1).upper())
    elif not fields.get("批次编号"):
        # 文件名用「第N批」(审批表/需求申请) 时映射到 B0N 批次键
        cn_batch = re.search(r"第([一二三四五])批", fname)
        if cn_batch:
            fields["批次编号"] = "B0" + {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5"}[cn_batch.group(1)]
    supplier_match = re.search(r"(?:^|[_-])(S0[1-9])(?:[_-]|(?=[^A-Za-z0-9]))", fname, re.IGNORECASE)
    if supplier_match:
        fields.setdefault("供应商编号", supplier_match.group(1).upper())
    date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", fname)
    if date_match:
        fields.setdefault("文档日期", date_match.group(1))
    return fields
