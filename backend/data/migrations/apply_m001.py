"""M001 迁移执行器（Phase 1 项目生命周期）

幂等：逐列检查 information_schema，不存在才 ADD。
用法：cd backend && .venv\Scripts\python.exe data\migrations\apply_m001.py
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from services.db import query, execute  # noqa: E402

# (表, 列, DDL片段, 注释)
ADD_COLS = [
    ("audit_projects", "setup_stage",         "VARCHAR(20) DEFAULT 'basic'", "basic/target_scope/items/workspace"),
    ("audit_projects", "workspace_created_at", "DATETIME NULL",               "资料空间创建时间"),
    ("audit_projects", "business_start_date",  "DATE NULL",                   "被审计业务实际发生起始时间"),
    ("audit_projects", "business_end_date",    "DATE NULL",                   "被审计业务实际发生结束时间"),
    ("audit_projects", "start_date",           "DATE NULL",                   "项目开始日期"),
    ("audit_projects", "entry_date",           "DATE NULL",                   "审计进点日期"),
    ("audit_projects", "extend_unit",          "VARCHAR(500) NULL",           "延伸审计单位"),
    ("audit_projects", "audit_focus",          "JSON NULL",                   "审计重点标签列表"),
    ("audit_projects", "target_unit",          "VARCHAR(500) NULL",           "审计对象（被审计单位全称）"),
]


def col_exists(table, col):
    rows = query(
        "SELECT 1 AS x FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA='tt' AND TABLE_NAME=%s AND COLUMN_NAME=%s",
        (table, col), database="tt",
    )
    return bool(rows)


def main():
    added = 0
    for table, col, ddl, comment in ADD_COLS:
        if col_exists(table, col):
            print(f"  = {table}.{col} 已存在，跳过")
            continue
        execute(
            f"ALTER TABLE `tt`.`{table}` ADD COLUMN `{col}` {ddl} COMMENT %s",
            (comment,), database="tt",
        )
        print(f"  + {table}.{col} 已添加")
        added += 1
    print(f"完成：新增 {added} 列")


if __name__ == "__main__":
    main()
