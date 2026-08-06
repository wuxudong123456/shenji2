"""Phase 0 勘察脚本：K1（DB diff）+ K3（双项目盘点）

只读查询，不改数据。用法：
    cd backend && .venv\Scripts\python.exe tests\survey_phase0.py
"""
import os
import sys

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.chdir(BACKEND_DIR)

from services.db import query, query_one  # noqa: E402


def _col(row):
    # SHOW TABLES 的列名是 Tables_in_<db>，兼容取第一个值
    return list(row.values())[0]


def k1_db_diff():
    print("=== K1: tt 库实际表清单 ===")
    rows = query("SHOW TABLES", database="tt")
    tables = sorted(_col(r) for r in rows)
    for t in tables:
        try:
            n = query_one(f"SELECT COUNT(*) AS n FROM `{t}`", database="tt")
            print(f"  {t}: {n['n'] if n else '?'} 行")
        except Exception as e:
            print(f"  {t}: 计数失败 {e}")

    print("\n--- audit_projects 列 ---")
    try:
        print("  ", [c["Field"] for c in query("SHOW COLUMNS FROM audit_projects", database="tt")])
    except Exception as e:
        print("  失败:", e)

    print("\n--- audit_analysis_tasks 列（核对 task_code 是否存在）---")
    try:
        print("  ", [c["Field"] for c in query("SHOW COLUMNS FROM audit_analysis_tasks", database="tt")])
    except Exception as e:
        print("  失败:", e)

    print("\n--- audit_document_traces 列（核对 position_anchor 等）---")
    try:
        print("  ", [c["Field"] for c in query("SHOW COLUMNS FROM audit_document_traces", database="tt")])
    except Exception as e:
        print("  失败:", e)


def k3_project_inventory():
    print("\n=== K3: 双项目盘点 ===")
    try:
        from services.minio_client import get_client, list_folders
        client = get_client()
        buckets = [b.name for b in client.list_buckets()]
        print("MinIO buckets:", buckets)
        try:
            folders = list_folders()
            print("MinIO 顶层文件夹:", folders)
        except Exception as e:
            print("list_folders 失败:", e)
    except Exception as e:
        print("MinIO 盘点失败:", e)

    try:
        projs = query("SELECT id, name, status, creator FROM audit_projects WHERE deleted=0", database="tt")
        print(f"\nMySQL 项目数: {len(projs)}")
        for p in projs:
            print(f"  {p['id']} | {p['name']} | {p['status']} | creator={p['creator']}")
    except Exception as e:
        print("MySQL 项目查询失败:", e)


if __name__ == "__main__":
    k1_db_diff()
    k3_project_inventory()
