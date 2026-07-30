"""法规关系图查询服务 — Phase 2.2 ★核心

数据源: audit_law.tools_regulation_relation (64,097条关系)

关系类型:
  - superior:        上位法 (law_id = 上位法, related_law_id = 下位法)
  - related:         相关法 (双向)
  - history_version: 历史版本 (双向)

用法:
    graph = get_regulation_graph("a00000236765")
    # → {center, superior_chain, inferior, related, history_versions}
"""
from services.db import query, query_one

_RELATION_TABLE = "tools_regulation_relation"
_LAW_TABLE = "sys_core_law_allaudit"
_LAW_TABLE_FULL = "sys_core_law"


def _batch_law_titles(law_ids: list[str]) -> dict[str, dict]:
    """批量查询法规标题和元数据（优先查审计常用库，回退全量库）"""
    if not law_ids:
        return {}
    unique_ids = list(set(law_ids))

    # 分批（IN 子句不超过 1000 个）
    result = {}
    for i in range(0, len(unique_ids), 500):
        batch = unique_ids[i:i + 500]
        placeholders = ",".join(["%s"] * len(batch))

        rows = query(
            f"SELECT id, title, potency_level, timeliness, issue_date, issue_unit "
            f"FROM {_LAW_TABLE} WHERE id IN ({placeholders})",
            tuple(batch), database="audit_law"
        )

        found_ids = {r["id"] for r in rows}
        for r in rows:
            result[r["id"]] = {
                "id": r["id"], "title": r["title"],
                "potency_level": r.get("potency_level"),
                "timeliness": r.get("timeliness"),
                "issue_date": r.get("issue_date"),
                "issue_unit": r.get("issue_unit"),
            }

        # 回退查全量库
        missing = [lid for lid in batch if lid not in found_ids]
        if missing:
            p2 = ",".join(["%s"] * len(missing))
            rows2 = query(
                f"SELECT id, title, potency_level, timeliness, issue_date, issue_unit "
                f"FROM {_LAW_TABLE_FULL} WHERE id IN ({p2})",
                tuple(missing), database="audit_law"
            )
            for r in rows2:
                result[r["id"]] = {
                    "id": r["id"], "title": r["title"],
                    "potency_level": r.get("potency_level"),
                    "timeliness": r.get("timeliness"),
                    "issue_date": r.get("issue_date"),
                    "issue_unit": r.get("issue_unit"),
                }

    return result


def _get_center_law(law_id: str) -> dict | None:
    """获取中心法规信息"""
    row = query_one(
        f"SELECT id, title, potency_level, timeliness, issue_date, issue_unit, "
        f"issue_no, implement_date, invalid_date "
        f"FROM {_LAW_TABLE} WHERE id = %s",
        (law_id,), database="audit_law"
    )
    if not row:
        row = query_one(
            f"SELECT id, title, potency_level, timeliness, issue_date, issue_unit, "
            f"issue_no, implement_date, invalid_date "
            f"FROM {_LAW_TABLE_FULL} WHERE id = %s",
            (law_id,), database="audit_law"
        )
    return dict(row) if row else None


def _get_relations(law_id: str, relation_type: str) -> list[dict]:
    """获取指定类型的关系（双向查询）"""
    rows = query(
        f"SELECT id, law_id, related_law_id, relation_type, confidence, extra_data "
        f"FROM {_RELATION_TABLE} "
        f"WHERE (law_id = %s OR related_law_id = %s) AND relation_type = %s AND status = 1",
        (law_id, law_id, relation_type), database="audit_law"
    )
    return [dict(r) for r in rows]


def get_regulation_graph(law_id: str, max_superior_depth: int = 3) -> dict:
    """获取法规完整关系图

    Args:
        law_id: 中心法规ID（sys_core_law_allaudit.id）
        max_superior_depth: 上位法递归最大深度（默认3层）

    Returns:
        {
            "center": {id, title, potency_level, timeliness, issue_date, ...},
            "superior_chain": [{id, title, potency_level, timeliness, relation, depth}, ...],
            "inferior": [{id, title, potency_level, timeliness, relation}, ...],
            "related": [{id, title, potency_level, timeliness, relation, confidence}, ...],
            "history_versions": [{id, title, timeliness, relation}, ...],
            "total_relations": 15
        }

    Example:
        >>> g = get_regulation_graph("a00000236765")
        >>> g["center"]["title"]
        '中华人民共和国招标投标法'
        >>> len(g["superior_chain"])
        2
    """
    # 1. 中心法规
    center = _get_center_law(law_id)
    if not center:
        return {"center": None, "error": f"法规不存在: {law_id}"}

    # 2. 上位法链（递归，带深度标记，防循环）
    superior_rows = _get_relations(law_id, "superior")
    superior_chain = []
    visited = {law_id}

    # 第一层上位法（related_law_id == law_id 表示这些 law_id 是上位法）
    current_level_ids = set()
    for r in superior_rows:
        if r["related_law_id"] == law_id:
            current_level_ids.add(r["law_id"])  # law_id 是上位法

    # 递归获取更上层上位法
    depth = 1
    while current_level_ids and depth <= max_superior_depth:
        titles = _batch_law_titles(list(current_level_ids))
        next_level_ids = set()

        for sid in current_level_ids:
            if sid in visited:
                continue
            visited.add(sid)
            if sid in titles:
                superior_chain.append({**titles[sid], "relation": "superior", "depth": depth})

            # 查 sid 的上位法
            upper_rows = _get_relations(sid, "superior")
            for ur in upper_rows:
                if ur["related_law_id"] == sid and ur["law_id"] not in visited:
                    next_level_ids.add(ur["law_id"])

        current_level_ids = next_level_ids
        depth += 1

    # 3. 下位法（law_id == center, related_law_id 是下位法）
    inferior = []
    inferior_ids = set()
    for r in superior_rows:
        if r["law_id"] == law_id:
            inferior_ids.add(r["related_law_id"])
    if inferior_ids:
        titles = _batch_law_titles(list(inferior_ids))
        for iid in inferior_ids:
            if iid in titles:
                inferior.append({**titles[iid], "relation": "inferior"})

    # 4. 相关法
    related_rows = _get_relations(law_id, "related")
    related = []
    related_ids = set()
    for r in related_rows:
        other = r["related_law_id"] if r["law_id"] == law_id else r["law_id"]
        related_ids.add(other)
    if related_ids:
        titles = _batch_law_titles(list(related_ids))
        for rid in related_ids:
            if rid in titles:
                # 找到置信度
                confs = [rr["confidence"] for rr in related_rows
                         if rr["law_id"] == rid or rr["related_law_id"] == rid]
                related.append({**titles[rid], "relation": "related",
                                "confidence": max(confs) if confs else None})

    # 5. 历史版本
    history_rows = _get_relations(law_id, "history_version")
    history = []
    history_ids = set()
    for r in history_rows:
        other = r["related_law_id"] if r["law_id"] == law_id else r["law_id"]
        history_ids.add(other)
    if history_ids:
        titles = _batch_law_titles(list(history_ids))
        for hid in history_ids:
            if hid in titles:
                history.append({**titles[hid], "relation": "history_version"})

    total = len(superior_chain) + len(inferior) + len(related) + len(history)

    return {
        "center": center,
        "superior_chain": superior_chain,
        "inferior": inferior,
        "related": related,
        "history_versions": history,
        "total_relations": total,
    }


def get_law_clauses(law_id: str) -> list[dict]:
    """获取法规条款分析（Phase 2.5 预实现）

    从 tools_clause_relation 查询条款特征，关联 sys_core_law_allaudit 获取条款原文。

    Returns:
        [{clause_id, clause_text, feature_type, feature_name, audit_scenario}]
    """
    rows = query(
        "SELECT id, law_id, clause_type, clause_number, clause_summary, "
        "clause_keywords, audit_scenario, audit_tags "
        "FROM tools_clause_relation WHERE law_id = %s AND deleted = 0 LIMIT 200",
        (law_id,), database="audit_law"
    )
    return [dict(r) for r in rows]
