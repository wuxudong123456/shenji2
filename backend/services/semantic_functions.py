"""Q2.3 — 语义函数 UDF（User-Defined Functions）

实现 SQL 无法表达的业务语义判定函数。
这些函数在违规表达式中出现时，由 sql_generator 翻译成 SQL 占位符，
SQL 执行后由本模块做 Python 二次过滤。

已实现函数:
  - SAME_DEPT_AND_SUPPLIER_GROUP(buyer, supplier): 判断采购单位与供应商是否关联
  - SAME_CATEGORY_OR_SIMILAR(cat1, cat2): 采购品目相似度
  - DATE_DIFF_WORKDAY(d1, d2): 工作日差（排除周末）
  - SIMILAR_TEXT(s1, s2): 文本相似度（0-1）

注册机制: SEMANTIC_FUNCTIONS 字典供 sql_generator / expression_engine 查找
"""
import re
from datetime import datetime, timedelta
from difflib import SequenceMatcher


def same_dept_and_supplier_group(buyer: str, supplier: str) -> bool:
    """判断采购单位与供应商是否存在关联关系（化整为零/围标串标检测）

    判定依据（满足任一即认为关联）:
      1. 名称高度相似（>0.6，可能是空壳公司）
      2. 名称包含对方（如"XX局"和"XX局劳动服务中心"）
      3. 关键词重叠（去掉常见公司后缀后，核心词相同）

    Args:
        buyer: 采购单位名称
        supplier: 供应商名称

    Returns:
        True 表示存在关联（可疑）
    """
    if not buyer or not supplier:
        return False
    b = _strip_company_suffix(str(buyer))
    s = _strip_company_suffix(str(supplier))
    if not b or not s:
        return False

    # 1. 名称相似度
    similarity = SequenceMatcher(None, b, s).ratio()
    if similarity > 0.6:
        return True

    # 2. 包含关系
    if b in s or s in b:
        return True

    # 3. 核心词重叠（取2字以上的共有片段）
    common = _common_substrings(b, s, min_len=2)
    if any(len(c) >= 3 for c in common):
        return True

    return False


def same_category_or_similar(cat1: str, cat2: str) -> bool:
    """判断两个采购品目是否相同或相似（化整为零检测）

    Args:
        cat1, cat2: 采购品目名称（如"办公设备""电脑设备"）

    Returns:
        True 表示相似
    """
    if not cat1 or not cat2:
        return False
    c1, c2 = str(cat1), str(cat2)
    if c1 == c2:
        return True
    # 相似度 > 0.5 视为相似
    return SequenceMatcher(None, c1, c2).ratio() > 0.5


def date_diff_workday(d1, d2) -> int:
    """计算两个日期之间的工作日差（排除周末，不含法定节假日）

    Args:
        d1, d2: 日期（datetime / 字符串 'YYYY-MM-DD'）

    Returns:
        工作日天数（绝对值）
    """
    dt1 = _parse_date(d1)
    dt2 = _parse_date(d2)
    if not dt1 or not dt2:
        return 0
    if dt1 > dt2:
        dt1, dt2 = dt2, dt1
    days = 0
    cur = dt1
    while cur < dt2:
        cur += timedelta(days=1)
        if cur.weekday() < 5:  # 0-4 是周一到周五
            days += 1
    return days


def similar_text(s1: str, s2: str) -> float:
    """文本相似度（0-1）"""
    if not s1 or not s2:
        return 0.0
    return SequenceMatcher(None, str(s1), str(s2)).ratio()


# ── 工具函数 ──

_COMPANY_SUFFIXES = [
    "有限公司", "股份有限公司", "有限责任公司", "集团有限公司",
    "公司", "集团", "中心", "部", "局", "处", "办", "院", "会",
]


def _strip_company_suffix(name: str) -> str:
    """去掉公司/机构后缀，提取核心名称"""
    n = name.strip()
    for suffix in sorted(_COMPANY_SUFFIXES, key=len, reverse=True):
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    return n


def _common_substrings(s1: str, s2: str, min_len: int = 2) -> list[str]:
    """找两个字符串的公共子串"""
    result = []
    len1, len2 = len(s1), len(s2)
    # 简化的公共子串查找
    for i in range(len1 - min_len + 1):
        for j in range(len2 - min_len + 1):
            k = 0
            while (i + k < len1 and j + k < len2 and s1[i+k] == s2[j+k]):
                k += 1
            if k >= min_len:
                sub = s1[i:i+k]
                if sub not in result:
                    result.append(sub)
    return result


def _parse_date(d) -> datetime | None:
    """解析多种日期格式"""
    if isinstance(d, datetime):
        return d
    if isinstance(d, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y年%m月%d日", "%Y%m%d"):
            try:
                return datetime.strptime(d.strip()[:10] if fmt == "%Y-%m-%d" else d.strip(), fmt)
            except ValueError:
                continue
    return None


# ── 函数注册表（供 sql_generator / engine 查找）──
# 键: 函数名（大写）→ Python 函数
SEMANTIC_FUNCTIONS = {
    "SAME_DEPT_AND_SUPPLIER_GROUP": same_dept_and_supplier_group,
    "SAME_CATEGORY_OR_SIMILAR": same_category_or_similar,
    "DATE_DIFF_WORKDAY": date_diff_workday,
    "SIMILAR_TEXT": similar_text,
}


def has_semantic_function(expression: str) -> bool:
    """检查表达式是否含语义函数"""
    expr_upper = expression.upper()
    return any(fname in expr_upper for fname in SEMANTIC_FUNCTIONS)


def list_semantic_functions() -> list[str]:
    """列出所有已注册的语义函数名"""
    return list(SEMANTIC_FUNCTIONS.keys())
