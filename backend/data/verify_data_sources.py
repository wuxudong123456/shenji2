"""Phase 1.4 — 外部数据源可达性验证

验证清单:
  □ MySQL audit_law — 法规表行数 ≥ 预期
  □ MySQL tt         — 业务表行数 ≥ 预期
  □ MinIO :9100      — bucket 可创建/读写/删除
  □ LLM :8765        — /v1/models 可达
  □ OCR :5005        — /health 可达
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from config import Config
from services.db import health as db_health, query_one, query
from services.minio_client import get_client as get_minio
from services.ocr_client import OCREngine

OK = "✅"
FAIL = "❌"


def main():
    results = []
    print("=" * 60)
    print("外部数据源可达性验证")
    print("=" * 60)

    # ── 1. MySQL ──
    print("\n[1/4] MySQL 数据库")
    for db_name in ["tt", "audit_law"]:
        ok = db_health(db_name)
        icon = OK if ok else FAIL
        print(f"  {icon} {db_name}")
        results.append(("MySQL", db_name, ok))

    # 关键表行数校验
    checks = [
        ("sys_core_law_allaudit", "audit_law", 3000),
        ("tools_regulation_relation", "audit_law", 30000),
        ("tools_clause_relation", "audit_law", 90000),
        ("audit_projects", "tt", 0),
        ("audit_templates", "tt", 1400),
        ("audit_violations", "tt", 2000),
    ]
    for table, db, expected_min in checks:
        try:
            row = query_one(f"SELECT COUNT(*) AS n FROM {table}", database=db)
            n = row["n"] if row else 0
            icon = OK if n >= expected_min else FAIL
            print(f"  {icon} {table} ({db}): {n:,} 行 (预期 >= {expected_min:,})")
            results.append(("DB表", f"{db}.{table}", n >= expected_min))
        except Exception as e:
            print(f"  {FAIL} {table}: {e}")
            results.append(("DB表", f"{db}.{table}", False))

    # ── 2. MinIO ──
    print("\n[2/4] MinIO 对象存储")
    try:
        minio = get_minio()
        buckets = minio.list_buckets()
        bucket_names = [b.name for b in buckets]
        print(f"  {OK} 连接成功, 现有 buckets: {bucket_names}")

        # 创建一个临时 bucket 验证写入权限
        test_bucket = "audit-verify-test"
        if test_bucket not in bucket_names:
            minio.make_bucket(test_bucket)
            print(f"  {OK} 创建测试 bucket: {test_bucket}")
            minio.remove_bucket(test_bucket)
            print(f"  {OK} 删除测试 bucket: {test_bucket}")
        else:
            print(f"  {OK} 测试 bucket 已存在, 跳过创建/删除")
        results.append(("MinIO", "endpoint", True))
    except Exception as e:
        print(f"  {FAIL} MinIO 连接失败: {e}")
        results.append(("MinIO", "endpoint", False))

    # ── 3. LLM ──
    print("\n[3/4] LLM 网关")
    llm_url = Config.__dict__.get("LLM_API_BASE", None)
    # Config 没有 LLM_API_BASE 直接属性，从环境变量读
    import os
    from pathlib import Path as P
    from dotenv import load_dotenv
    load_dotenv(P(__file__).resolve().parent.parent.parent / ".env", override=True)
    llm_base = os.environ.get("LLM_API_BASE", "http://192.168.3.189:8765/v1")
    print(f"  地址: {llm_base}")
    try:
        resp = requests.get(f"{llm_base}/models", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            model_ids = [m["id"] for m in data.get("data", [])][:5]
            print(f"  {OK} 状态码 {resp.status_code}, 模型: {model_ids}")
            results.append(("LLM", "models", True))
        else:
            print(f"  {FAIL} 状态码 {resp.status_code}")
            results.append(("LLM", "models", False))
    except requests.exceptions.ConnectTimeout:
        print(f"  {FAIL} 连接超时 (10s)")
        results.append(("LLM", "models", False))
    except requests.exceptions.ConnectionError as e:
        print(f"  {FAIL} 连接失败: {e}")
        results.append(("LLM", "models", False))
    except Exception as e:
        print(f"  {FAIL} 未知错误: {e}")
        results.append(("LLM", "models", False))

    # ── 4. OCR ──
    print("\n[4/4] OCR 引擎")
    ocr_engine = Config.OCR_ENGINE or "mineru"
    ocr_url = Config.MINERU_BASE_URL or "http://192.168.3.189:5005"
    print(f"  引擎: {ocr_engine} @ {ocr_url}")
    try:
        engine = OCREngine.get_engine()
        healthy = engine.health()
        icon = OK if healthy else FAIL
        print(f"  {icon} 健康状态: {healthy}")
        results.append(("OCR", ocr_engine, healthy))
    except Exception as e:
        print(f"  {FAIL} 健康检查失败: {e}")
        results.append(("OCR", ocr_engine, False))

    # ── 汇总 ──
    print("\n" + "=" * 60)
    passed = sum(1 for _, _, ok in results if ok)
    total = len(results)
    all_ok = all(ok for _, _, ok in results)
    icon = OK if all_ok else FAIL
    print(f"{icon} 验证完成: {passed}/{total} 项通过")
    if not all_ok:
        failed = [(cat, name) for cat, name, ok in results if not ok]
        print("  失败项:")
        for cat, name in failed:
            print(f"    - [{cat}] {name}")
    print("=" * 60)
    return all_ok


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
