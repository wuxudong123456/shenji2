"""审计阈值对照服务 — 批量执行阈值规则，产出合规/违规判定。

复用 expression_engine.execute_expression，不造新引擎。
规则定义在 data/threshold_rules.yaml，审计员可增删改。
"""
from pathlib import Path

_RULES_PATH = Path(__file__).resolve().parent.parent / "data" / "threshold_rules.yaml"
_rules_cache = None


def _load_rules():
    """加载阈值规则（带缓存）"""
    global _rules_cache
    if _rules_cache is not None:
        return _rules_cache
    import yaml
    with open(_RULES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _rules_cache = (data or {}).get("rules", [])
    return _rules_cache


def reload_rules():
    """清除缓存，重新加载规则（规则文件改后调用）"""
    global _rules_cache
    _rules_cache = None
    return _load_rules()


def check_thresholds(project_id: str = "", table: str = "data_contracts") -> dict:
    """批量执行阈值规则，返回每条规则的合规/违规判定。

    Args:
        project_id: 项目ID（空=全局扫全库）
        table: 目标表（默认 data_contracts）

    Returns:
        {success, results: [...], summary: {total_rules, violated, compliant, insufficient_data}}
    """
    from services.expression_engine import execute_expression

    rules = _load_rules()
    results = []
    violated = 0
    compliant = 0
    insufficient = 0

    for rule in rules:
        expr = rule.get("expression", "")
        rule_table = rule.get("table", table)
        try:
            scan = execute_expression(expr, rule_table, project_id)
            hits = scan.get("hits", 0)
            total = scan.get("total", 0)
        except Exception:
            hits, total = 0, 0

        if total == 0:
            status = "数据不足"
            insufficient += 1
        elif hits > 0:
            status = "违规"
            violated += 1
        else:
            status = "合规"
            compliant += 1

        results.append({
            "id": rule.get("id", ""),
            "name": rule.get("name", ""),
            "expression": expr,
            "law_ref": rule.get("law_ref", ""),
            "threshold": rule.get("threshold", ""),
            "severity": rule.get("severity", ""),
            "hits": hits,
            "total": total,
            "status": status,
        })

    return {
        "success": True,
        "results": results,
        "summary": {
            "total_rules": len(results),
            "violated": violated,
            "compliant": compliant,
            "insufficient_data": insufficient,
        },
    }
