"""P2-3 — 业务阈值×法规条款对照表提取器

从违规模型的 expression_text 提取阈值数字，
从 description 提取法规名称并查询效力级别，
组装成动态对照表。

纯读取操作，不写数据库。
"""
import re
from services.db import query, query_one
from services.expression_parser import parse_expression


# 符号阈值 → 默认值的映射（法规标准金额，单位：元）
SYMBOLIC_THRESHOLD_MAP = {
    "公开招标限额标准": 2000000,       # 货物服务 ≥ 200万
    "政府采购限额标准": 100000,        # 货物服务 ≥ 10万
    "本级政府采购限额标准": 100000,
    "邀请招标限额标准": 2000000,
    "竞争性谈判限额标准": 2000000,
}


def extract_thresholds_from_expression(expression_text: str) -> list[dict]:
    """从违规表达式提取阈值（P2-3 核心）

    Args:
        expression_text: 伪SQL表达式

    Returns:
        [{field, operator, value, display}, ...]
        display 是人类可读的阈值描述（如 "≥200万" 或 "≥2000000"）
    """
    if not expression_text or not expression_text.strip():
        return []

    results = []
    try:
        ast = parse_expression(expression_text)
        _walk_ast_for_thresholds(ast, results)
    except Exception:
        # 解析失败的表达式，尝试从文本里正则提取数字阈值
        _regex_extract_thresholds(expression_text, results)

    return results


def _walk_ast_for_thresholds(node: dict, results: list):
    """递归遍历 AST，找比较节点的阈值"""
    if not isinstance(node, dict):
        return

    t = node.get("type", "")

    # 比较节点
    if t in ("GT", "GTE", "LT", "LTE", "EQ", "NE"):
        field = node.get("field", "")
        value = node.get("value")

        # 处理符号阈值（如"公开招标限额标准"）
        if isinstance(value, str) and value in SYMBOLIC_THRESHOLD_MAP:
            value = SYMBOLIC_THRESHOLD_MAP[value]

        if isinstance(value, (int, float)) and value > 0:
            results.append({
                "field": field,
                "operator": t,
                "value": value,
                "display": _format_threshold(t, value),
            })

    # BETWEEN 节点
    elif t == "BETWEEN":
        field = node.get("field", "")
        low = node.get("low")
        high = node.get("high")
        if isinstance(low, (int, float)) and low > 0:
            results.append({"field": field, "operator": "GTE", "value": low,
                           "display": _format_threshold("GTE", low)})
        if isinstance(high, (int, float)) and high > 0:
            results.append({"field": field, "operator": "LTE", "value": high,
                           "display": _format_threshold("LTE", high)})

    # 递归子节点
    for key in ("left", "right"):
        child = node.get(key)
        if isinstance(child, dict):
            _walk_ast_for_thresholds(child, results)


def _regex_extract_thresholds(text: str, results: list):
    """从文本正则提取阈值（解析失败时的兜底）"""
    # 匹配 "金额 > 1000000" "≥200万" ">50万" 等
    patterns = [
        (r'(\w+?)\s*[>≥]\s*(\d+(?:\.\d+)?)\s*万', lambda m: float(m.group(2)) * 10000),
        (r'(\w+?)\s*[>≥]\s*(\d+(?:\.\d+)?)\s*亿', lambda m: float(m.group(2)) * 100000000),
        (r'(\w+?)\s*[>≥]\s*(\d{4,})', lambda m: float(m.group(2))),
        (r'(\w+?)\s*[<≤]\s*(\d+(?:\.\d+)?)\s*万', lambda m: float(m.group(2)) * 10000),
    ]
    for pat, val_fn in patterns:
        for m in re.finditer(pat, text):
            try:
                val = val_fn(m)
                if val > 0:
                    op = "GTE" if "≥" in m.group(0) or ">" in m.group(0) else "LTE"
                    results.append({
                        "field": m.group(1),
                        "operator": op,
                        "value": val,
                        "display": _format_threshold(op, val),
                    })
            except (ValueError, IndexError):
                pass


def _format_threshold(operator: str, value: float) -> str:
    """格式化阈值为人类可读"""
    op_map = {"GT": ">", "GTE": "≥", "LT": "<", "LTE": "≤", "EQ": "=", "NE": "≠"}
    op_str = op_map.get(operator, "?")

    if value >= 100000000:
        return f"{op_str}{value / 100000000:.1f}亿"
    elif value >= 10000:
        return f"{op_str}{value / 10000:.0f}万"
    else:
        return f"{op_str}{value:.0f}"


def _extract_law_names_from_description(description: str) -> list[str]:
    """从违规描述里提取法规名称"""
    if not description:
        return []
    # 匹配《法规名》格式
    laws = re.findall(r'《([^》]+)》', description)
    return laws


def build_threshold_table(violation_titles: list[str] = None,
                          violation_ids: list[int] = None,
                          target_level: str = "") -> dict:
    """组装对照表（P2-3 主入口）

    Args:
        violation_titles: 违规模型名称列表（如 ["应公开招标未招标"]）
        violation_ids: 违规模型 ID 列表（优先于 titles）
        target_level: 被审计对象层级（国家/省/市/县/乡）

    Returns:
        {
            "rows": [{violation_title, thresholds, laws, applicability}],
            "applicability_order": "法律 > 行政法规 > ...",
        }
    """
    rows = []

    # 查违规模型
    if violation_ids:
        placeholders = ",".join(["%s"] * len(violation_ids))
        violations = query(
            f"SELECT id, violation_title, expression_text, description, severity "
            f"FROM audit_violations WHERE id IN ({placeholders}) AND deleted = 0",
            tuple(violation_ids), database="tt",
        )
    elif violation_titles:
        conditions = " OR ".join(["violation_title LIKE %s" for _ in violation_titles])
        params = [f"%{t}%" for t in violation_titles]
        violations = query(
            f"SELECT id, violation_title, expression_text, description, severity "
            f"FROM audit_violations WHERE ({conditions}) AND deleted = 0 LIMIT 20",
            tuple(params), database="tt",
        )
    else:
        return {"rows": [], "applicability_order": _default_order()}

    for v in violations:
        title = v.get("violation_title", "未命名违规")
        expr = v.get("expression_text", "")
        desc = v.get("description", "")
        severity = v.get("severity", "medium")

        # 提取阈值
        thresholds = extract_thresholds_from_expression(expr)
        threshold_display = "；".join(t["display"] for t in thresholds) if thresholds else "见表达式描述"

        # 提取法规
        law_names = _extract_law_names_from_description(desc)
        laws = []
        for law_name in law_names[:5]:
            # 查法规库拿效力级别
            law_row = query_one(
                "SELECT id, title, potency_level FROM sys_core_law_allaudit "
                "WHERE title LIKE %s AND status = 1 LIMIT 1",
                (f"%{law_name}%",), database="audit_law",
            )
            if law_row:
                laws.append({
                    "law_title": law_row["title"],
                    "potency_level": law_row.get("potency_level", ""),
                    "law_id": law_row["id"],
                })
            else:
                laws.append({"law_title": law_name, "potency_level": "未知", "law_id": ""})

        # 适用结论
        applicability = _determine_applicability(laws, target_level, severity)

        rows.append({
            "violation_title": title,
            "thresholds": threshold_display,
            "threshold_details": thresholds[:3],  # 原始阈值数据
            "laws": laws,
            "applicability": applicability,
            "severity": severity,
        })

    return {
        "rows": rows,
        "applicability_order": _default_order(),
    }


def _default_order() -> str:
    return "法律 > 行政法规 > 部门规章 > 地方性法规 > 单位制度"


def _determine_applicability(laws: list, target_level: str, severity: str) -> str:
    """根据法规效力级别和审计对象层级，判定适用结论"""
    if not laws:
        return "需人工判断"

    has_law = any(l.get("potency_level") == "法律" for l in laws)
    has_local = any("地方" in (l.get("potency_level") or "") for l in laws)
    has_dept = any("部门" in (l.get("potency_level") or "") for l in laws)

    parts = []
    if has_law:
        parts.append("国家强制要求")
    if has_local and target_level in ("市级", "县级", "乡级"):
        parts.append("地方优先适用")
    if has_dept:
        parts.append("部门细化标准")
    if severity == "high":
        parts.append("高风险事项")

    return "；".join(parts) if parts else "一般适用"
