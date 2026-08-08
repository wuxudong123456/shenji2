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
    """统一行查询（P5-3/P5-4）。

    双模式：
      require_project=False → 全局浏览（project_id 可空，per_page 硬 cap 200）
      require_project=True  → 项目分析（project_id 必填，空 → ProjectIDRequiredError）

    after/filters/fields 由后续切片（P5-5/P5-6）实现，本切片预留参数。
    """
    _validate_table(table)

    # P5-4 强制 project_id（服务层兜底，不信任调用方/LLM）
    if require_project and not project_id:
        raise ProjectIDRequiredError("项目分析模式 project_id 必填")

    # WHERE 构建（只允许 project_id 条件；筛选/游标后续切片填）
    where_parts: list[str] = []
    params: list = []
    if project_id:
        where_parts.append("project_id = %s")
        params.append(project_id)
    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""

    # 分页（per_page 硬 cap 200，page 下界 1）
    per_page = min(max(int(per_page or 20), 1), 200)
    page = max(int(page or 1), 1)
    offset = (page - 1) * per_page

    rows = query(
        f"SELECT * FROM {table}{where} ORDER BY id DESC LIMIT %s OFFSET %s",
        (*params, per_page, offset), database="tt",
    )
    total = query_one(
        f"SELECT COUNT(*) AS n FROM {table}{where}",
        tuple(params), database="tt",
    )
    return {
        "rows": [_clean_row(dict(r)) for r in rows],
        "total": total["n"] if total else 0,
        "page": page,
        "per_page": per_page,
    }
