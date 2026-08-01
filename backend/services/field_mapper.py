"""字段名映射器 — OntoSKU 中文字段 → data_xxx 表英文列名

解决"字段名映射鸿沟":
  - OntoSKU 提取返回中文字段名（甲方/合同金额/采购方式...）
  - data_xxx 表用英文列名（party_a/amount/procurement_method...）
  - 违规表达式又用中文字段名引用

本模块提供双向映射:
  map_extracted_fields(table, fields) — 提取结果 → 表列（写入用）
  get_expr_field_alias(table)        — 表达式中文字段 → 表列（扫描用）
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
        # 金额
        "合同金额": "amount", "金额": "amount", "采购金额": "amount", "合同总价": "amount",
        "总价": "amount", "中标金额": "amount", "成交金额": "amount", "价款": "amount",
        "合同总额": "amount", "总额": "amount", "合计金额": "amount", "价税合计": "amount",
        "不含税金额": "amount", "合同不含税金额": "amount", "预算金额": "amount",
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
    },
    "data_legal_docs": {
        "案件编号": "case_no", "案号": "case_no", "文书编号": "case_no",
        "发布机关": "issuing_body", "出具机关": "issuing_body", "裁决机关": "issuing_body",
        "发文机关": "issuing_body", "颁布机关": "issuing_body",
        "文书日期": "doc_date", "发文日期": "doc_date", "判决日期": "doc_date", "裁决日期": "doc_date",
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
        "摘要": "summary", "内容简介": "summary", "概述": "summary",
        "发布机关": "issuing_body", "发文单位": "issuing_body", "发布单位": "issuing_body",
        "日期": "doc_date", "发文日期": "doc_date", "发布日期": "doc_date",
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
        col = alias.get(cn_name) or _fuzzy_match_alias(cn_name, alias)
        if col:
            casted = _cast_value(col, value, table)
            # 防 last-wins 覆盖：仅当列未赋值或现值为空(None/"")时才写入。
            # 这样允许 None→真值（后续真值补上），但阻止真值被后面的"未提供"→None 覆盖。
            if row_dict.get(col) in (None, ""):
                row_dict[col] = casted
        else:
            extra_fields[cn_name] = value

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
    NUMERIC_COLS = {"amount", "debit_amount", "credit_amount", "quantity"}
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
