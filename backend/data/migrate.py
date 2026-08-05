#!/usr/bin/env python
"""AuditWorkbench 数据库迁移脚本（幂等）

每个步骤先查 information_schema 确认当前 schema 状态，已应用则跳过——
重复执行安全。连接配置从 .env 的 MYSQL_* 读取（与后端 services.db 共用）。

用法（在仓库根目录运行）:
    python backend/data/migrate.py

设计说明:
  - MySQL 不支持 ALTER TABLE ADD COLUMN IF NOT EXISTS（那是 MariaDB 语法），
    所以用 information_schema 预检实现幂等。
  - 不在应用启动时自动跑 DDL——审计系统对生产库结构变更应留痕、由人触发，
    开发期可随时手动执行本脚本。
"""
import os
import sys

# 把 backend/ 加入 sys.path，以便复用 services.db 的连接池（连接配置统一）
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

from services.db import query, execute  # noqa: E402

DATABASE = "tt"


# ── 幂等预检 ──

def _table_exists(table: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = %s AND table_name = %s LIMIT 1",
        (DATABASE, table), database=DATABASE,
    )
    return len(rows) > 0


def _column_exists(table: str, column: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s AND column_name = %s LIMIT 1",
        (DATABASE, table, column), database=DATABASE,
    )
    return len(rows) > 0


def _index_exists(table: str, index: str) -> bool:
    rows = query(
        "SELECT 1 FROM information_schema.statistics "
        "WHERE table_schema = %s AND table_name = %s AND index_name = %s LIMIT 1",
        (DATABASE, table, index), database=DATABASE,
    )
    return len(rows) > 0


# ── 迁移步骤 ──

def migrate_trace_md5():
    """Q1.4 — audit_document_traces 增加 file_md5 列 + 索引（上传去重校验）"""
    table = "audit_document_traces"

    if not _column_exists(table, "file_md5"):
        execute(
            f"ALTER TABLE {DATABASE}.{table} "
            "ADD COLUMN file_md5 VARCHAR(32) DEFAULT NULL "
            "COMMENT '文件MD5（去重校验）' AFTER file_name",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.file_md5 列")
    else:
        print(f"[migrate] = {table}.file_md5 已存在，跳过")

    if not _index_exists(table, "idx_project_md5"):
        execute(
            f"ALTER TABLE {DATABASE}.{table} "
            "ADD INDEX idx_project_md5 (project_id, file_md5)",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.idx_project_md5 索引")
    else:
        print(f"[migrate] = {table}.idx_project_md5 已存在，跳过")


def migrate_expression_sql():
    """Q2.2 — 聚合表达式 SQL 缓存表（LLM 生成 + 人工确认）"""
    table = "audit_expression_sql"
    if _table_exists(table):
        print(f"[migrate] = 表 {table} 已存在，跳过")
        return

    execute(f"""CREATE TABLE {DATABASE}.{table} (
        id               INT AUTO_INCREMENT PRIMARY KEY COMMENT '主键ID',
        expression_text  TEXT NOT NULL                  COMMENT '原始违规表达式（伪SQL）',
        expression_hash  CHAR(64) NOT NULL              COMMENT '表达式SHA256（快速查重）',
        generated_sql    TEXT                           COMMENT 'LLM生成的MySQL SQL',
        target_table     VARCHAR(100)                   COMMENT '目标数据表',
        review_status    VARCHAR(20) DEFAULT 'pending'  COMMENT 'pending/approved/rejected/disabled',
        reviewed_by      VARCHAR(64)                    COMMENT '审核人',
        reviewed_at      DATETIME                       COMMENT '审核时间',
        last_executed_at DATETIME                       COMMENT '最后执行时间',
        hit_count        INT DEFAULT 0                  COMMENT '累计命中次数',
        error_msg        TEXT                           COMMENT '执行错误记录',
        created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY uk_hash (expression_hash),
        INDEX idx_status (review_status),
        INDEX idx_table (target_table)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='聚合表达式SQL缓存（LLM生成+人工确认）'""", database=DATABASE)
    print(f"[migrate] + 表 {table}")


def migrate_project_context_columns():
    """Phase1 — audit_projects 增加立项业务字段（项目上下文持久化）

    让"被审计单位/审计类型/层级/编号/目标"等立项信息真正落库，
    供 /api/audit/analysis 按 project_id 读取注入 Agent 上下文。
    幂等：每列/索引先查 information_schema，已存在则跳过。
    """
    table = "audit_projects"
    columns = [
        ("project_code", "VARCHAR(64) DEFAULT NULL COMMENT '项目编号（如审通〔2026〕001号）'"),
        ("audited_unit", "VARCHAR(128) DEFAULT NULL COMMENT '被审计单位'"),
        ("audit_type", "VARCHAR(32) DEFAULT NULL COMMENT '审计类型（预算执行/专项调查/经济责任等）'"),
        ("audit_method", "VARCHAR(32) DEFAULT NULL COMMENT '审计方式（就地/送达/联网）'"),
        ("target_level", "VARCHAR(16) DEFAULT NULL COMMENT '单位层级（省级/市级/县级）'"),
        ("leader", "VARCHAR(32) DEFAULT NULL COMMENT '审计组长'"),
        ("auditor", "VARCHAR(64) DEFAULT NULL COMMENT '审计员'"),
        ("objective", "TEXT DEFAULT NULL COMMENT '审计目标'"),
        ("scope", "TEXT DEFAULT NULL COMMENT '审计范围'"),
        ("amount", "DECIMAL(14,2) DEFAULT NULL COMMENT '涉及金额'"),
    ]
    for col, ddl in columns:
        if not _column_exists(table, col):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD COLUMN {col} {ddl}", database=DATABASE)
            print(f"[migrate] + {table}.{col}")
        else:
            print(f"[migrate] = {table}.{col} 已存在，跳过")
    for idx, col in [("idx_unit", "audited_unit"), ("idx_type", "audit_type")]:
        if not _index_exists(table, idx):
            execute(f"ALTER TABLE {DATABASE}.{table} ADD INDEX {idx} ({col})", database=DATABASE)
            print(f"[migrate] + {table}.{idx}")
        else:
            print(f"[migrate] = {table}.{idx} 已存在，跳过")


def migrate_case_indexes():
    """性能加固 — 案例库查询索引（知识工坊案例列表 + 违规关联聚合提速）

    背景：Excel 导入后 audit_cases 数千行，案例列表接口的
    ORDER BY created_at DESC、GROUP_CONCAT 关联聚合变慢。
    补三类索引，幂等：先查 information_schema.statistics。
    """
    # 1. audit_cases.created_at（列表 ORDER BY created_at DESC LIMIT n）
    if not _index_exists("audit_cases", "idx_created_at"):
        execute(
            "ALTER TABLE tt.audit_cases ADD INDEX idx_created_at (created_at)",
            database=DATABASE,
        )
        print("[migrate] + audit_cases.idx_created_at 索引")
    else:
        print("[migrate] = audit_cases.idx_created_at 已存在，跳过")

    # 2. audit_case_violations.violation_id（违规→案例聚合查询 GROUP BY violation_id）
    if not _index_exists("audit_case_violations", "idx_violation"):
        execute(
            "ALTER TABLE tt.audit_case_violations ADD INDEX idx_violation (violation_id)",
            database=DATABASE,
        )
        print("[migrate] + audit_case_violations.idx_violation 索引")
    else:
        print("[migrate] = audit_case_violations.idx_violation 已存在，跳过")

    # 3. audit_case_law_refs.case_id（案例→法规关联 JOIN WHERE cl.case_id = ?）
    if not _index_exists("audit_case_law_refs", "idx_case"):
        execute(
            "ALTER TABLE tt.audit_case_law_refs ADD INDEX idx_case (case_id)",
            database=DATABASE,
        )
        print("[migrate] + audit_case_law_refs.idx_case 索引")
    else:
        print("[migrate] = audit_case_law_refs.idx_case 已存在，跳过")


def migrate_law_refs_collation():
    """性能加固 — 关联表 law_id 字符集对齐 audit_law.sys_core_law_allaudit.id

    背景：tt 库 law_id 用 utf8mb4_unicode_ci，audit_law.id 用 utf8mb4_0900_ai_ci，
    跨库 JOIN 时代码写 COLLATE utf8mb4_0900_ai_ci 转换 → 左列加 COLLATE 使
    audit_law.id 主键索引失效 → 全表扫描法规表（含大段正文）→ 冷启动 ~2s。
    把两列 COLLATE 改为 utf8mb4_0900_ai_ci 后 JOIN 可直接用主键索引。幂等。
    """
    for table in ("audit_case_law_refs", "audit_violation_law_refs"):
        rows = query(
            "SELECT COLLATION_NAME FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s AND column_name = 'law_id'",
            (DATABASE, table), database=DATABASE,
        )
        collation = rows[0]["COLLATION_NAME"] if rows else None
        if collation == "utf8mb4_0900_ai_ci":
            print(f"[migrate] = {table}.law_id 已是 utf8mb4_0900_ai_ci，跳过")
            continue
        execute(
            f"ALTER TABLE {DATABASE}.{table} MODIFY law_id VARCHAR(32) "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci NOT NULL "
            "COMMENT 'sys_core_law_allaudit.id'",
            database=DATABASE,
        )
        print(f"[migrate] + {table}.law_id COLLATE → utf8mb4_0900_ai_ci")


def main():
    print(f"[migrate] 开始迁移，目标库: {DATABASE}")
    try:
        migrate_trace_md5()
        migrate_expression_sql()
        migrate_project_context_columns()
        migrate_case_indexes()
        migrate_law_refs_collation()
    except Exception as e:
        print(f"[migrate] X 迁移失败: {e}")
        raise
    print("[migrate] DONE 迁移完成")


if __name__ == "__main__":
    main()
