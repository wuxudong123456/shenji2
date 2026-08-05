"""知识工坊 服务层 — 法规检索 + 违规行为查询

数据源:
  - audit_law.sys_core_law_allaudit  — 审计常用法规 (3,584条)
  - audit_law.sys_core_law           — 全量法规 (353,063条，扩展搜索用)
  - tt.audit_violations              — 违规行为库 (2,077条)

任务映射:
  - Phase 2.1: search_laws / get_law_detail
  - Phase 2.3: search_violations / get_violation_detail
"""
from services.db import query, query_one


# ────────────────────────────────────────────────────────────
#  法规检索 (Phase 2.1)
# ────────────────────────────────────────────────────────────

_LAW_LIST_COLS = (
    "id, title, issue_unit, issue_no, issue_date, implement_date, "
    "invalid_date, potency_level, timeliness, region_type, status"
)


def _build_law_where(query_text: str, potency_level: str = None,
                     timeliness: str = None, region_type: int = None) -> tuple[str, list]:
    """构建 WHERE 子句和参数列表（所有参数使用 %s 占位符，防注入）"""
    clauses = ["status = 1"]  # 只查审核通过的法规
    params = []

    if query_text:
        clauses.append("(title LIKE %s OR content LIKE %s)")
        like_val = f"%{query_text}%"
        params.extend([like_val, like_val])

    if potency_level:
        clauses.append("potency_level = %s")
        params.append(potency_level)

    if timeliness:
        clauses.append("timeliness = %s")
        params.append(timeliness)

    if region_type is not None:
        clauses.append("region_type = %s")
        params.append(region_type)

    return " AND ".join(clauses), params


def search_laws(query_text: str = "",
                potency_level: str = None,
                timeliness: str = None,
                region_type: int = None,
                limit: int = 50,
                offset: int = 0,
                include_all: bool = False) -> list[dict]:
    """法规全文检索

    Args:
        query_text: 搜索关键词（匹配标题和正文）
        potency_level: 效力级别筛选（法律/行政法规/部门规章/地方法规/党内法规/司法解释/...）
        timeliness: 时效性筛选（现行有效/失效/已修改/部分失效/尚未施行）
        region_type: 地域类型（0=国家法规, 1=地方法规）
        limit: 每页条数
        offset: 偏移量
        include_all: 是否包含全量法规库 sys_core_law（默认仅审计常用法规）

    Returns:
        [{id, title, issue_unit, issue_no, issue_date, implement_date,
          invalid_date, potency_level, timeliness, region_type, snippet}]
    """
    table = "sys_core_law" if include_all else "sys_core_law_allaudit"
    where, params = _build_law_where(query_text, potency_level, timeliness, region_type)

    # 按标题匹配优先排序
    order = ""
    if query_text:
        order = (f"ORDER BY CASE WHEN title LIKE %s THEN 0 ELSE 1 END, "
                 f"         CASE WHEN title LIKE %s THEN 0 ELSE 1 END")
        like_val = f"%{query_text}%"
        params.extend([like_val, like_val])

    # 附带被违规/案例引用的数量（跨库统计 tt.audit_*_law_refs，供三库跳转）
    sql = (f"SELECT {_LAW_LIST_COLS}, "
           f"(SELECT COUNT(*) FROM tt.audit_violation_law_refs vl "
           f" WHERE vl.law_id COLLATE utf8mb4_0900_ai_ci = {table}.id) AS violation_count, "
           f"(SELECT COUNT(*) FROM tt.audit_case_law_refs cl "
           f" WHERE cl.law_id COLLATE utf8mb4_0900_ai_ci = {table}.id) AS case_count "
           f"FROM {table} WHERE {where} {order} LIMIT %s OFFSET %s")
    params.extend([limit, offset])

    rows = query(sql, tuple(params), database="audit_law")

    # 生成摘要（关键词所在位置的前后文字）
    results = []
    for r in rows:
        item = dict(r)
        if query_text and r.get("content"):
            content = r["content"]
            pos = content.find(query_text)
            if pos >= 0:
                start = max(0, pos - 40)
                end = min(len(content), pos + len(query_text) + 80)
                item["snippet"] = content[start:end]
        results.append(item)

    return results


def count_laws(query_text: str = "",
               potency_level: str = None,
               timeliness: str = None,
               region_type: int = None,
               include_all: bool = False) -> int:
    """法规搜索结果计数（用于分页）"""
    table = "sys_core_law" if include_all else "sys_core_law_allaudit"
    where, params = _build_law_where(query_text, potency_level, timeliness, region_type)

    row = query_one(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}",
                    tuple(params), database="audit_law")
    return row["n"] if row else 0


def get_law_detail(law_id: str) -> dict | None:
    """获取法规全文详情

    Returns:
        {id, title, content, pro_content(HTML), issue_unit, issue_no,
         issue_date, implement_date, invalid_date, repeal_date,
         potency_level, timeliness, region_type, ...}
    """
    row = query_one(
        "SELECT * FROM sys_core_law_allaudit WHERE id = %s AND status = 1",
        (law_id,), database="audit_law"
    )
    if not row:
        # 回退查全量库
        row = query_one(
            "SELECT * FROM sys_core_law WHERE id = %s AND status = 1",
            (law_id,), database="audit_law"
        )
    return dict(row) if row else None


def list_potency_levels() -> list[str]:
    """获取所有效力级别（供前端下拉筛选）"""
    rows = query(
        "SELECT DISTINCT potency_level FROM sys_core_law_allaudit "
        "WHERE potency_level IS NOT NULL AND potency_level != '' AND status = 1 "
        "ORDER BY FIELD(potency_level, '法律','行政法规','部门规章','地方法规','司法解释','党内法规','团体及行业规定','监察法规')",
        database="audit_law"
    )
    return [r["potency_level"] for r in rows]


def list_timeliness_options() -> list[str]:
    """获取所有时效性选项（供前端下拉筛选）"""
    rows = query(
        "SELECT DISTINCT timeliness FROM sys_core_law_allaudit "
        "WHERE timeliness IS NOT NULL AND timeliness != '' AND status = 1 "
        "ORDER BY FIELD(timeliness, '现行有效','尚未施行','已修改','部分失效','失效')",
        database="audit_law"
    )
    return [r["timeliness"] for r in rows]


# ────────────────────────────────────────────────────────────
#  违规行为查询 (Phase 2.3)
# ────────────────────────────────────────────────────────────

_VIOLATION_LIST_COLS = (
    "id, violation_code, violation_title, audititem_id, category_path, "
    "severity, expression_text, description, source_file, is_reviewed, review_status, create_time"
)


def search_violations(query_text: str = "",
                      severity: str = None,
                      is_reviewed: int = None,
                      category: str = None,
                      limit: int = 50,
                      offset: int = 0) -> list[dict]:
    """违规行为检索

    Args:
        query_text: 搜索关键词（匹配标题和描述）
        severity: 严重程度筛选（high/medium/low）
        is_reviewed: 审核状态（0=未审核, 1=已审核）
        category: 审计事项分类前缀筛选（匹配 category_path，如 业务类-部门预算执行审计）
        limit: 每页条数
        offset: 偏移量
    """
    clauses = ["deleted = 0"]
    params = []

    if query_text:
        clauses.append("(violation_title LIKE %s OR description LIKE %s)")
        like_val = f"%{query_text}%"
        params.extend([like_val, like_val])

    if severity:
        clauses.append("severity = %s")
        params.append(severity)

    if is_reviewed is not None:
        clauses.append("is_reviewed = %s")
        params.append(is_reviewed)

    if category:
        clauses.append("category_path LIKE %s")
        params.append(f"{category}%")

    where = " AND ".join(clauses)
    order = ""
    if query_text:
        order = ("ORDER BY CASE WHEN violation_title LIKE %s THEN 0 ELSE 1 END, "
                 "         CASE WHEN violation_title LIKE %s THEN 0 ELSE 1 END")
        like_val = f"%{query_text}%"
        params.extend([like_val, like_val])

    # 附带每个违规的关联案例数 + 案例 ID 数组（audit_case_violations 统计）
    sql = (f"SELECT {_VIOLATION_LIST_COLS}, COALESCE(cv.cnt, 0) AS case_count, "
           f"COALESCE(cv2.case_ids, JSON_ARRAY()) AS case_ids "
           f"FROM audit_violations "
           f"LEFT JOIN (SELECT violation_id, COUNT(*) AS cnt "
           f"           FROM audit_case_violations GROUP BY violation_id) cv "
           f"ON cv.violation_id = audit_violations.id "
           f"LEFT JOIN (SELECT violation_id, JSON_ARRAYAGG(case_id) AS case_ids "
           f"           FROM audit_case_violations GROUP BY violation_id) cv2 "
           f"ON cv2.violation_id = audit_violations.id "
           f"WHERE {where} {order} LIMIT %s OFFSET %s")
    params.extend([limit, offset])

    return query(sql, tuple(params), database="tt")


def count_violations(query_text: str = "",
                     severity: str = None,
                     is_reviewed: int = None,
                     category: str = None) -> int:
    """违规行为计数"""
    clauses = ["deleted = 0"]
    params = []

    if query_text:
        clauses.append("(violation_title LIKE %s OR description LIKE %s)")
        like_val = f"%{query_text}%"
        params.extend([like_val, like_val])

    if severity:
        clauses.append("severity = %s")
        params.append(severity)

    if is_reviewed is not None:
        clauses.append("is_reviewed = %s")
        params.append(is_reviewed)

    if category:
        clauses.append("category_path LIKE %s")
        params.append(f"{category}%")

    where = " AND ".join(clauses)
    row = query_one(f"SELECT COUNT(*) AS n FROM audit_violations WHERE {where}",
                    tuple(params), database="tt")
    return row["n"] if row else 0


def list_violation_categories() -> list[str]:
    """获取违规行为的审计事项分类列表（供前端下拉框筛选）"""
    rows = query(
        "SELECT DISTINCT category_path FROM audit_violations "
        "WHERE deleted = 0 AND category_path IS NOT NULL AND category_path != '' "
        "ORDER BY category_path",
        database="tt",
    )
    return [r["category_path"] for r in rows]


def get_violation_detail(violation_id: int) -> dict | None:
    """获取违规行为详情"""
    row = query_one(
        "SELECT * FROM audit_violations WHERE id = %s AND deleted = 0",
        (violation_id,), database="tt"
    )
    if not row:
        return None
    d = dict(row)
    # BIT(1) 列由 pymysql 返回 bytes，转成 bool 以便 JSON 序列化
    for k, v in d.items():
        if isinstance(v, bytes):
            d[k] = bool(v and v != b"\x00")
    return d


# ────────────────────────────────────────────────────────────
#  审计事项分类查询 (Phase 2.4)
#  数据源: audit_law.sys_audititem_SLFF (1,983节点树)
#          audit_law.sys_audititem_qualitative (134K定性依据)
#          audit_law.sys_audititem_punish (81K处罚依据)
# ────────────────────────────────────────────────────────────

def get_audititem_children(parent_id: str | None = None) -> list[dict]:
    """获取审计事项分类的子节点

    Args:
        parent_id: 父节点ID。传 None 获取根节点（level=1），传 '' 获取所有顶层

    Returns:
        [{id, name, pid, level, has_child, path_names, type, is_recommend}]
    """
    if parent_id is None:
        # 根节点：level=1 且 status=1
        rows = query(
            "SELECT id, name, pid, level, has_child, path_names, type, is_recommend "
            "FROM sys_audititem_SLFF "
            "WHERE level = 1 AND 1 = 1 "
            "ORDER BY order_no",
            database="audit_law"
        )
    else:
        rows = query(
            "SELECT id, name, pid, level, has_child, path_names, type, is_recommend "
            "FROM sys_audititem_SLFF "
            "WHERE pid = %s AND 1 = 1 "
            "ORDER BY order_no",
            (parent_id,), database="audit_law"
        )
    return [dict(r) for r in rows]


def get_audititem_tree(max_depth: int = 3) -> list[dict]:
    """获取审计事项完整分类树（一次性加载，适合小型树）

    Args:
        max_depth: 最大深度（默认3层，SLFF 树共约4层）

    Returns:
        嵌套树形结构 [{id, name, children: [...]}]
    """
    # 一次性取所有节点
    rows = query(
        "SELECT id, name, pid, level, has_child, path_names, type, is_recommend "
        "FROM sys_audititem_SLFF "
        "WHERE 1 = 1 AND level <= %s "
        "ORDER BY level, order_no",
        (max_depth,), database="audit_law"
    )

    # 构建内存树
    nodes = {r["id"]: {**dict(r), "children": []} for r in rows}
    roots = []
    for n in nodes.values():
        if n["level"] == 1 or n["pid"] not in nodes:
            roots.append(n)
        else:
            nodes[n["pid"]]["children"].append(n)

    return roots


def search_audititems(query_text: str, limit: int = 50) -> list[dict]:
    """按关键词搜索审计事项

    Args:
        query_text: 搜索关键词
        limit: 最大返回数
    """
    rows = query(
        "SELECT id, name, pid, level, has_child, path_names, type, is_recommend "
        "FROM sys_audititem_SLFF "
        "WHERE name LIKE %s AND 1 = 1 "
        "ORDER BY level, order_no LIMIT %s",
        (f"%{query_text}%", limit), database="audit_law"
    )
    return [dict(r) for r in rows]


def get_audititem_law_refs(audititem_id: str) -> dict:
    """获取审计事项的法规依据（定性依据 + 处罚依据）

    通过 sys_audititem_audititem_meta_SLFF 连接表，
    找到 audititem → meta → qualitative/punish 的完整法规链。

    Args:
        audititem_id: SLFF 树节点 ID

    Returns:
        {
            "qualitative": [{law_title, law_issue_no, law_items_paragraphs, summary}],
            "punish": [{law_title, law_issue_no, law_items_paragraphs, summary}],
            "total": N
        }
    """
    # 通过连接表获取 meta_id 列表
    meta_rows = query(
        "SELECT audititem_meta_id FROM sys_audititem_audititem_meta_SLFF "
        "WHERE audititem_id = %s AND deleted = 0",
        (audititem_id,), database="audit_law"
    )
    meta_ids = [r["audititem_meta_id"] for r in meta_rows]

    if not meta_ids:
        return {"qualitative": [], "punish": [], "total": 0}

    result = {"qualitative": [], "punish": [], "total": 0}

    # 定性依据 — 使用 COLLATE 解决字符集不一致
    for rel, label in [("qualitative", "qualitative"), ("punish", "punish")]:
        placeholders = ",".join(["%s"] * len(meta_ids))
        rows = query(
            f"SELECT law_title, law_issue_no, law_items_paragraphs, summary "
            f"FROM sys_audititem_{rel} "
            f"WHERE audititem_id IN ({placeholders}) AND deleted = 0 "
            f"GROUP BY law_title, law_issue_no, law_items_paragraphs, summary "
            f"ORDER BY MIN(order_no) LIMIT 50",
            tuple(meta_ids), database="audit_law"
        )
        result[label] = [dict(r) for r in rows]
        result["total"] += len(rows)

    return result


def get_law_audititems(law_id: str) -> dict:
    """查询某部法规引用于哪些审计事项（法规 → 审计事项的反向查询）

    Args:
        law_id: 法规 ID（sys_core_law_allaudit.id）

    Returns:
        {"qualitative": [...], "punish": [...], "total": N}
    """
    result = {"qualitative": [], "punish": [], "total": 0}

    for rel, label in [("qualitative", "qualitative"), ("punish", "punish")]:
        rows = query(
            f"SELECT audititem_id, law_title, law_issue_no, "
            f"law_items_paragraphs, summary "
            f"FROM sys_audititem_{rel} "
            f"WHERE law_id = %s AND deleted = 0 "
            f"GROUP BY audititem_id, law_title, law_issue_no, law_items_paragraphs, summary "
            f"ORDER BY MIN(order_no) LIMIT 50",
            (law_id,), database="audit_law"
        )
        result[label] = [dict(r) for r in rows]
        result["total"] += len(rows)

    return result
