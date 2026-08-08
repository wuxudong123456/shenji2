"""数据工坊查询服务层 — 所有 data_* 查询经此封装（PHASE_5 §3.1）

强制 project_id 双模式（方案 §4.4）：
  - 全局浏览模式（require_project=False，project_id=None）：只读列表/统计，per_page 硬 cap，仅供概览
  - 项目分析模式（require_project=True）：project_id 必填，自动附加 WHERE project_id=%s，
    不接受调用方（含 LLM）自由拼接该条件；空 → ProjectIDRequiredError（路由转 400）

本切片（切片2）落地 P5-2 表统计 / P5-3 行查询 / P5-4 强制 project_id；
P5-5 筛选 / P5-6 裁剪+游标 / P5-7 质量 / P5-8 缺失 由后续切片填（参数已预留）。
"""
from datetime import date, datetime
from decimal import Decimal

from services.db import query, query_one

# 8 表权威白名单（P5-1）
DATA_TABLES = [
    "data_contracts", "data_finance", "data_legal_docs",
    "data_registers", "data_credentials", "data_general",
    "data_procurements", "data_interviews",
]

# 列表查询默认裁剪的大字段（P5-6，切片4 启用）
LARGE_FIELDS = {"raw_text", "transcript"}

# 各表可筛选列白名单（P5-5 防注入，切片3 启用）
FILTERABLE_COLS = {
    "data_contracts": {"amount", "contract_no", "party_a", "party_b", "procurement_method",
                       "sign_date", "effective_date"},
    "data_finance": {"debit_amount", "credit_amount", "account_no", "bank_name", "voucher_date"},
    "data_legal_docs": {"case_no", "issuing_body", "doc_date", "effective_date"},
    "data_registers": {"register_type", "item_name", "register_date"},
    "data_credentials": {"cert_type", "cert_no", "holder", "issue_date", "expire_date"},
    "data_general": {"category", "title", "issuing_body", "doc_date"},
    "data_procurements": {"procurement_method", "supplier", "subject_name",
                          "budget_amount", "contract_amount", "bid_date", "sign_date"},
    "data_interviews": {"interviewee", "location", "interview_date"},
}

# 金额列（决策11 统一元；P5-5 范围筛选 + P5-7 质量检查用）
AMOUNT_COLS = {"amount", "debit_amount", "credit_amount", "budget_amount", "contract_amount"}
# 日期列（P5-5 日期范围筛选用）
DATE_COLS = {"sign_date", "effective_date", "expiry_date", "voucher_date", "doc_date",
             "register_date", "issue_date", "expire_date", "bid_date", "interview_date"}

# query string 中非筛选键（分页/游标/字段/项目），parse_query_filters 跳过
_PAGINATION_KEYS = {"page", "per_page", "project_id", "after", "fields"}

# 查询超时保护（P5-6，MySQL MAX_EXECUTION_TIME 优化器 hint，毫秒）
QUERY_TIMEOUT_MS = 10000

# 各表应用层「关键业务列」（P5-8 缺失检查 + P5-7 空值率；DB 仅 project_id NOT NULL）
KEY_COLS = {
    "data_contracts": ["party_a", "party_b", "amount", "contract_no", "sign_date"],
    "data_finance": ["account_name", "account_no", "voucher_no", "voucher_date",
                     "debit_amount", "credit_amount"],
    "data_legal_docs": ["case_no", "issuing_body", "doc_date"],
    "data_registers": ["register_type", "item_name", "register_date"],
    "data_credentials": ["cert_type", "cert_no", "holder", "issue_date"],
    "data_general": ["category", "title", "doc_date"],
    "data_procurements": ["subject_name", "supplier", "budget_amount", "contract_amount", "bid_date"],
    "data_interviews": ["interviewee", "interview_date", "location"],
}

# 各表金额列（决策11 单位=元；P5-7 金额统计 + 单位异常软告警）
TABLE_AMOUNT_COLS = {
    "data_contracts": ["amount"],
    "data_finance": ["debit_amount", "credit_amount"],
    "data_procurements": ["budget_amount", "contract_amount"],
}

# 金额单位异常软告警阈值（P5-7，决策11）
AMOUNT_TOO_LARGE = 1e9   # max > 1e9 疑似万元/亿元混入
AMOUNT_TOO_SMALL = 10    # max < 10 疑似应为万元单位


class ProjectIDRequiredError(ValueError):
    """项目分析模式 project_id 缺失（方案 §4.4 → 路由转 400）"""


def _validate_table(table: str) -> None:
    if table not in DATA_TABLES:
        raise ValueError(f"不支持的表: {table}")


def _clean_row(d: dict) -> dict:
    """清洗 DB 行：Decimal→float、datetime/date→isoformat、bytes→None"""
    out = {}
    for k, v in d.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif isinstance(v, (datetime, date)):
            out[k] = v.isoformat()
        elif isinstance(v, bytes):
            out[k] = None
        else:
            out[k] = v
    return out


def list_table_counts(project_id: str | None = None) -> list[dict]:
    """8 表行数统计（P5-2）。

    project_id=None → 全局（无 WHERE）；非空 → 项目级（WHERE project_id=%s）。
    不校验项目存在性（与现状一致：不存在项目返回全 0）。
    """
    result = []
    for t in DATA_TABLES:
        if project_id:
            row = query_one(
                f"SELECT COUNT(*) AS n FROM {t} WHERE project_id = %s",
                (project_id,), database="tt",
            )
        else:
            row = query_one(f"SELECT COUNT(*) AS n FROM {t}", (), database="tt")
        result.append({
            "table": t,
            "label": t.replace("data_", ""),
            "rows": row["n"] if row else 0,
        })
    return result


def list_rows(table: str, *, project_id: str | None = None,
              page: int = 1, per_page: int = 20,
              after: int | None = None, filters: dict | None = None,
              fields: list[str] | None = None,
              require_project: bool = False) -> dict:
    """统一行查询（P5-3/P5-4/P5-5/P5-6）。

    双模式：
      require_project=False → 全局浏览（project_id 可空，per_page 硬 cap 200）
      require_project=True  → 项目分析（project_id 必填，空 → ProjectIDRequiredError）

    filters（P5-5）：等于/金额范围/日期范围，白名单列防注入，见 parse_query_filters。
    P5-6：
      - 字段裁剪：默认剥离 LARGE_FIELDS（raw_text/transcript），fields 显式取回。
      - 游标分页：after=<id> → WHERE id<%s 深翻页（避开 OFFSET 越翻越慢），与 page 互斥。
      - 超时保护：SELECT 带 MAX_EXECUTION_TIME hint（QUERY_TIMEOUT_MS）。
    """
    _validate_table(table)

    # P5-4 强制 project_id（服务层兜底，不信任调用方/LLM）
    if require_project and not project_id:
        raise ProjectIDRequiredError("项目分析模式 project_id 必填")

    # base WHERE（project_id + filters）—— 不含游标，用于 total 计数
    base_parts: list[str] = []
    base_params: list = []
    if project_id:
        base_parts.append("project_id = %s")
        base_params.append(project_id)

    # P5-5 字段筛选（白名单列防注入；结构见 parse_query_filters）
    if filters:
        flt_cols = FILTERABLE_COLS.get(table, set())
        # ① 等于（列须在白名单）
        for col, val in filters.get("eq", {}).items():
            if col in flt_cols:
                base_parts.append(f"`{col}` = %s")
                base_params.append(val)
        # ② 金额范围（表的各金额列各自落在 [min,max] → OR 任一命中）
        amt_cols = AMOUNT_COLS & flt_cols
        amin, amax = filters.get("amount_min"), filters.get("amount_max")
        if amt_cols and (amin is not None or amax is not None):
            col_conds = []
            for c in sorted(amt_cols):
                parts = []
                if amin is not None:
                    parts.append(f"`{c}` >= %s"); base_params.append(amin)
                if amax is not None:
                    parts.append(f"`{c}` <= %s"); base_params.append(amax)
                col_conds.append("(" + " AND ".join(parts) + ")")
            base_parts.append("(" + " OR ".join(col_conds) + ")")
        # ③ 日期范围（表的各日期列各自落在 [from,to] → OR 任一命中）
        dt_cols = DATE_COLS & flt_cols
        dfrom, dto = filters.get("date_from"), filters.get("date_to")
        if dt_cols and (dfrom is not None or dto is not None):
            col_conds = []
            for c in sorted(dt_cols):
                parts = []
                if dfrom is not None:
                    parts.append(f"`{c}` >= %s"); base_params.append(dfrom)
                if dto is not None:
                    parts.append(f"`{c}` <= %s"); base_params.append(dto)
                col_conds.append("(" + " AND ".join(parts) + ")")
            base_parts.append("(" + " OR ".join(col_conds) + ")")

    # P5-6 游标条件（仅行查询 WHERE，不进 total）
    row_parts = base_parts[:]
    row_params = base_params[:]
    cursor_mode = after is not None
    if cursor_mode:
        row_parts.append("id < %s")
        row_params.append(int(after))

    base_where = (" WHERE " + " AND ".join(base_parts)) if base_parts else ""
    row_where = (" WHERE " + " AND ".join(row_parts)) if row_parts else ""

    per_page = min(max(int(per_page or 20), 1), 200)
    hint = f"/*+ MAX_EXECUTION_TIME({QUERY_TIMEOUT_MS}) */"

    if cursor_mode:
        rows = query(
            f"SELECT {hint} * FROM {table}{row_where} ORDER BY id DESC LIMIT %s",
            (*row_params, per_page), database="tt",
        )
        page_out = None
    else:
        page = max(int(page or 1), 1)
        offset = (page - 1) * per_page
        rows = query(
            f"SELECT {hint} * FROM {table}{row_where} ORDER BY id DESC LIMIT %s OFFSET %s",
            (*row_params, per_page, offset), database="tt",
        )
        page_out = page

    total = query_one(
        f"SELECT {hint} COUNT(*) AS n FROM {table}{base_where}",
        tuple(base_params), database="tt",
    )

    # P5-6 字段裁剪：默认剥离 LARGE_FIELDS，fields 白名单显式取回
    keep_large = ({f.strip() for f in (fields or [])} & LARGE_FIELDS) if fields else set()
    clean_rows = []
    for r in rows:
        d = _clean_row(dict(r))
        for lf in LARGE_FIELDS:
            if lf not in keep_large:
                d.pop(lf, None)
        clean_rows.append(d)

    result = {
        "rows": clean_rows,
        "total": total["n"] if total else 0,
        "page": page_out,
        "per_page": per_page,
        # 下一页游标（始终返回，便于从任意页切入游标翻页）：满页取末行 id，否则到尾 None
        "next_cursor": clean_rows[-1]["id"] if (clean_rows and len(clean_rows) == per_page) else None,
    }
    return result


def parse_query_filters(table: str, args: dict) -> dict:
    """从路由 query string 解析筛选条件（P5-5，白名单列防注入）。

    三类筛选（非白名单/无法解析的键静默忽略）：
      - 等于：?col=value（col 须在 FILTERABLE_COLS[table]）
      - 金额范围：?amount_min=&amount_max=（float，应用到表的金额列）
      - 日期范围：?date_from=&date_to=（YYYY-MM-DD，应用到表的日期列）

    返回结构供 list_rows 的 filters 参数消费：
      {"eq": {col: val}, "amount_min": float|None, "amount_max": float|None,
       "date_from": str|None, "date_to": str|None}
    """
    _validate_table(table)
    flt = {"eq": {}}
    flt_cols = FILTERABLE_COLS.get(table, set())
    for key, val in args.items():
        if key in _PAGINATION_KEYS or val is None or val == "":
            continue
        if key in flt_cols:
            flt["eq"][key] = val
        elif key == "amount_min":
            try:
                flt["amount_min"] = float(val)
            except (ValueError, TypeError):
                pass
        elif key == "amount_max":
            try:
                flt["amount_max"] = float(val)
            except (ValueError, TypeError):
                pass
        elif key == "date_from":
            flt["date_from"] = val
        elif key == "date_to":
            flt["date_to"] = val
        # 其他键忽略（防注入）
    return flt


def _num(v):
    """Decimal→float，None 保留（金额统计用）"""
    return float(v) if isinstance(v, Decimal) else v


def _table_stats(table: str, project_id: str) -> tuple[dict | None, list[str], list[str]]:
    """单表列统计（1 查询）：total + 各 KEY_COL 空值数 + 各金额列 min/max/count"""
    key_cols = KEY_COLS.get(table, [])
    amt_cols = TABLE_AMOUNT_COLS.get(table, [])
    selects = ["COUNT(*) AS total"]
    for c in key_cols:
        # CAST AS CHAR 避免 DATE/DECIMAL 列与 '' 比较时类型强转报错（Incorrect DATE value）
        selects.append(
            f"SUM(CASE WHEN `{c}` IS NULL OR CAST(`{c}` AS CHAR)='' THEN 1 ELSE 0 END) AS `null_{c}`"
        )
    for c in amt_cols:
        selects.append(f"MIN(`{c}`) AS `min_{c}`")
        selects.append(f"MAX(`{c}`) AS `max_{c}`")
        selects.append(f"COUNT(`{c}`) AS `cnt_{c}`")
    sql = f"SELECT {', '.join(selects)} FROM `{table}` WHERE project_id = %s"
    row = query_one(sql, (project_id,), database="tt")
    return row, key_cols, amt_cols


def quality_check(project_id: str) -> dict:
    """数据质量报告（P5-7）：每表空值率 + 金额列统计 + 金额单位异常软告警（决策11）。

    金额单位统一「元」（决策11）；max>1e9 或 max<10 标「疑似单位异常待核实」（软告警）。
    """
    tables_out = []
    for table in DATA_TABLES:
        row, key_cols, amt_cols = _table_stats(table, project_id)
        total = int((row or {}).get("total") or 0)
        entry = {"table": table, "label": table.replace("data_", ""), "total": total}
        if total == 0:
            tables_out.append(entry)
            continue
        # 空值率（关键业务列）
        entry["nulls"] = [
            {"col": c, "null": int(row.get(f"null_{c}") or 0),
             "rate": round(int(row.get(f"null_{c}") or 0) / total, 4)}
            for c in key_cols
        ]
        # 金额列统计 + 单位告警
        amounts = []
        for c in amt_cols:
            cnt = int(row.get(f"cnt_{c}") or 0)
            mn = _num(row.get(f"min_{c}"))
            mx = _num(row.get(f"max_{c}"))
            warnings = []
            if cnt > 0 and mx is not None:
                if mx > AMOUNT_TOO_LARGE:
                    warnings.append(f"max>{AMOUNT_TOO_LARGE:g}，疑似万元/亿元混入")
                if mx < AMOUNT_TOO_SMALL:
                    warnings.append(f"max<{AMOUNT_TOO_SMALL}，疑似应为万元单位")
            amounts.append({"col": c, "min": mn, "max": mx, "count": cnt,
                            "unit": "元", "warnings": warnings})
        entry["amounts"] = amounts
        tables_out.append(entry)
    return {"project_id": project_id, "unit": "元（决策11，应用层标注）", "tables": tables_out}


def missing_check(project_id: str) -> dict:
    """关键业务列缺失清单（P5-8）：DB 仅 project_id NOT NULL，关键列应用层定义（KEY_COLS）。

    仅列出缺失数>0 的列（null 或空串）。
    """
    tables_out = []
    for table in DATA_TABLES:
        row, key_cols, _ = _table_stats(table, project_id)
        total = int((row or {}).get("total") or 0)
        missing = []
        if total > 0:
            for c in key_cols:
                n = int(row.get(f"null_{c}") or 0)
                if n > 0:
                    missing.append({"col": c, "missing": n, "rate": round(n / total, 4)})
        tables_out.append({"table": table, "label": table.replace("data_", ""),
                           "total": total, "missing": missing})
    return {"project_id": project_id, "tables": tables_out}
