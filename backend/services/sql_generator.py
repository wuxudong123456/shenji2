"""Q2.2 — LLM 辅助 SQL 生成 + 人工确认缓存

策略（用户确认）:
  1. 聚合表达式首次出现 → LLM 生成 MySQL SQL
  2. 人工确认（管理界面）→ 缓存到 audit_expression_sql 表
  3. 后续直接用缓存 SQL 执行（确定性硬判定）

用法:
    sql, status = get_or_generate_sql(expression, table, project_id)
    # status: 'cached' | 'generated_pending' | 'failed'
"""
import hashlib
import json
from services.db import query_one, execute, insert
from services.llm_client import call_llm


def _hash_expression(expression: str) -> str:
    """表达式 SHA256（用于缓存查重）"""
    normalized = expression.strip().replace("\n", " ").replace("  ", " ")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def get_cached_sql(expression: str) -> dict | None:
    """查询已缓存的 SQL

    Returns:
        {id, generated_sql, target_table, review_status} 或 None
    """
    h = _hash_expression(expression)
    row = query_one(
        "SELECT id, generated_sql, target_table, review_status "
        "FROM audit_expression_sql WHERE expression_hash = %s",
        (h,), database="tt",
    )
    return dict(row) if row else None


def get_or_generate_sql(expression: str, table: str) -> dict:
    """获取可执行的 SQL：优先用缓存，无缓存则 LLM 生成

    Returns:
        {
            "sql": "SELECT ...",          # 生成的 SQL（含 :project_id 占位符）
            "status": "cached" | "generated_pending" | "failed",
            "review_status": "approved" | "pending" | ...,
            "id": 缓存记录ID
        }

    状态说明:
        cached             → 已确认的缓存SQL，可直接执行
        generated_pending  → LLM新生成但未确认，执行结果仅供参考
        failed             → LLM 生成失败
    """
    # 1. 查缓存
    cached = get_cached_sql(expression)
    if cached:
        if cached["review_status"] == "approved" and cached.get("generated_sql"):
            return {
                "sql": cached["generated_sql"],
                "status": "cached",
                "review_status": "approved",
                "id": cached["id"],
            }
        # 有记录但未确认，返回现有 SQL（标记为 pending）
        if cached.get("generated_sql"):
            return {
                "sql": cached["generated_sql"],
                "status": "generated_pending",
                "review_status": cached["review_status"],
                "id": cached["id"],
            }

    # 2. 无缓存或无 SQL → LLM 生成
    sql = _generate_sql_via_llm(expression, table)

    # 3. 写入缓存表（pending 状态，待人工确认）
    cache_id = None
    if sql:
        h = _hash_expression(expression)
        if cached and cached.get("id"):
            # 更新现有记录的 SQL
            execute(
                "UPDATE audit_expression_sql SET generated_sql=%s, target_table=%s "
                "WHERE id=%s",
                (sql, table, cached["id"]), database="tt",
            )
            cache_id = cached["id"]
        else:
            cache_id = insert(
                "INSERT INTO audit_expression_sql "
                "(expression_text, expression_hash, generated_sql, target_table, review_status, created_at) "
                "VALUES (%s,%s,%s,%s,'pending',NOW())",
                (expression[:5000], h, sql, table), database="tt",
            )

        return {
            "sql": sql,
            "status": "generated_pending",
            "review_status": "pending",
            "id": cache_id,
        }

    return {"sql": None, "status": "failed", "review_status": None, "id": None}


def _generate_sql_via_llm(expression: str, table: str) -> str | None:
    """用 LLM 把伪 SQL 翻译成可执行的 MySQL SQL

    LLM 上下文：表结构 + 字段映射 + 原始表达式
    输出：纯 SQL（带 :project_id 参数占位符）
    """
    # 获取表的列结构
    schema_hint = _get_table_schema_hint(table)

    prompt = f"""请把下面的审计违规伪SQL表达式翻译成可执行的 MySQL SELECT 语句。

## 原始表达式
{expression}

## 目标表结构: {table}
{schema_hint}

## 翻译规则
1. 输出完整的 SELECT 语句，返回命中的记录（选关键字段）
2. 项目过滤固定用 WHERE project_id = :project_id
3. 中文字段名要映射到表的英文列名（如"采购方式"→procurement_method、"合同金额"→amount）
4. SUM/COUNT/MAX/MIN 等聚合函数配合 GROUP BY ... HAVING ... 使用；带 GROUP BY 时 SELECT 列表只能含分组列和聚合函数（如 SELECT party_a, SUM(amount)），严禁 SELECT *（MySQL only_full_group_by 严格模式会报错）
5. DATE_DIFF(a,b,'DAY') → DATEDIFF(a,b)；CURRENT_DATE → CURDATE()
6. 中文时间窗如"最近24个月" → 用 DATE_SUB(CURDATE(), INTERVAL 24 MONTH)
7. 中文顿号"、"分隔的列表 → SQL IN ('a','b','c')
8. 只返回 SQL 语句本身，不要任何解释文字、不要 markdown 代码块标记
9. 如果表达式无法翻译（语义函数如 SAME_DEPT_AND_SUPPLIER_GROUP），返回 CANNOT_TRANSLATE

## 输出
纯 SQL 语句："""

    try:
        text = call_llm(prompt=prompt, max_tokens=1024, temperature=0)
        sql = text.strip().strip("`").strip()
        # 去掉可能的 markdown 代码块标记
        if sql.startswith("sql"):
            sql = sql[3:].strip()
        if "CANNOT_TRANSLATE" in sql.upper():
            return None
        return sql
    except Exception:
        return None


def _get_table_schema_hint(table: str) -> str:
    """获取表的列结构提示（给 LLM 做字段映射参考）"""
    SCHEMA_HINTS = {
        "data_contracts": "id, project_id, party_a(甲方), party_b(乙方/供应商), amount(合同金额), currency, sign_date(签订日期), effective_date, expiry_date, contract_no(合同编号), procurement_method(采购方式)",
        "data_finance": "id, project_id, account_name(账户名称), account_no(账号), debit_amount(借方金额), credit_amount(贷方金额), voucher_no(凭证号), voucher_date(凭证日期), bank_name(银行名称)",
        "data_legal_docs": "id, project_id, case_no(案件编号), issuing_body(发布机关), doc_date, legal_basis, verdict(判决)",
        "data_registers": "id, project_id, register_type(登记类型), item_name(项目名称), quantity(数量), unit(单位), responsible_person(责任人), register_date",
        "data_credentials": "id, project_id, cert_type(证照类型), cert_no(证照编号), holder(持有人), issue_date, expire_date, issuing_body",
        "data_general": "id, project_id, category(分类), title(标题), summary(摘要), issuing_body(发布机关), doc_date",
    }
    return SCHEMA_HINTS.get(table, "(未知表结构)")


def approve_sql(cache_id: int, reviewer: str = "admin") -> bool:
    """人工确认 SQL（管理界面调用）"""
    return execute(
        "UPDATE audit_expression_sql SET review_status='approved', reviewed_by=%s, "
        "reviewed_at=NOW() WHERE id=%s",
        (reviewer, cache_id), database="tt",
    ) > 0


def reject_sql(cache_id: int, reviewer: str = "admin") -> bool:
    """人工拒绝 SQL"""
    return execute(
        "UPDATE audit_expression_sql SET review_status='rejected', reviewed_by=%s, "
        "reviewed_at=NOW() WHERE id=%s",
        (reviewer, cache_id), database="tt",
    ) > 0


def list_pending_sql(limit: int = 50) -> list[dict]:
    """列出待确认的 SQL（管理界面用）"""
    from services.db import query
    return [
        dict(r) for r in query(
            "SELECT id, expression_text, generated_sql, target_table, review_status, created_at "
            "FROM audit_expression_sql WHERE review_status='pending' "
            "ORDER BY created_at DESC LIMIT %s",
            (limit,), database="tt",
        )
    ]


# P2-2: 自动批准安全的聚合 SQL（只读 SELECT + 无危险操作）
_DANGEROUS_KEYWORDS = {"DELETE", "DROP", "UPDATE", "INSERT", "TRUNCATE", "ALTER", "GRANT", "REVOKE"}
_SAFE_AGGREGATE_FUNCS = {"SUM", "COUNT", "MAX", "MIN", "AVG", "GROUP BY", "HAVING"}


def auto_approve_safe_sql(reviewer: str = "system") -> dict:
    """自动批准安全的聚合 SQL（P2-2）

    检查规则：
      - SQL 必须以 SELECT 开头
      - 不得含 DELETE/UPDATE/INSERT/DROP/TRUNCATE/ALTER 等危险关键字
      - 必须含 GROUP BY 或聚合函数（否则不是聚合表达式）
      - 必须含 project_id 过滤

    Returns:
        {"approved": N, "rejected": N, "skipped": N}
    """
    pending = list_pending_sql(limit=100)
    result = {"approved": 0, "rejected": 0, "skipped": 0}

    for item in pending:
        sql = (item.get("generated_sql") or "").strip()
        sql_upper = sql.upper()

        # 检查1：必须以 SELECT 开头
        if not sql_upper.startswith("SELECT"):
            result["skipped"] += 1
            continue

        # 检查2：不含危险关键字
        if any(kw in sql_upper for kw in _DANGEROUS_KEYWORDS):
            result["skipped"] += 1
            continue

        # 检查3：必须含聚合特征
        has_aggregate = any(kw in sql_upper for kw in _SAFE_AGGREGATE_FUNCS)
        if not has_aggregate:
            result["skipped"] += 1
            continue

        # 检查4：必须含 project_id 过滤
        if "PROJECT_ID" not in sql_upper and ":PROJECT_ID" not in sql_upper:
            result["skipped"] += 1
            continue

        # 安全检查通过 → 自动批准
        approve_sql(item["id"], reviewer=reviewer)
        result["approved"] += 1

    return result
