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


def main():
    print(f"[migrate] 开始迁移，目标库: {DATABASE}")
    try:
        migrate_trace_md5()
        migrate_expression_sql()
    except Exception as e:
        print(f"[migrate] X 迁移失败: {e}")
        raise
    print("[migrate] DONE 迁移完成")


if __name__ == "__main__":
    main()
