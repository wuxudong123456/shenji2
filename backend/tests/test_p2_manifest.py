r"""P2-3 manifest MinIO 往返单测（PHASE_2）

验证 init_first_manifest / load_manifest / save_manifest / append / mark_deleted 的
真实 MinIO 往返 + 幂等。需 MinIO 运行。

跑法：cd backend && .venv\Scripts\python.exe tests\test_p2_manifest.py
"""
import sys
import os
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.minio_client import get_client, delete_object  # noqa: E402
from services.workspace_service import (  # noqa: E402
    init_first_manifest, load_manifest, save_manifest,
    build_manifest_path, compute_safe_name,
    append_file_to_manifest, build_file_entry, mark_file_deleted,
)

TEST_BUCKET = "aw-test-manifest-p2"
PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {detail}")


def main():
    print("[test] P2-3 manifest MinIO 往返\n")
    client = get_client()
    if not client.bucket_exists(TEST_BUCKET):
        client.make_bucket(TEST_BUCKET)

    pid = "p2m" + uuid.uuid4().hex[:8]
    year = "2026"
    name = "测试项目/{}".format(pid)  # 含斜杠 → 测 safe_name 清洗
    safe = compute_safe_name(name)
    mpath = build_manifest_path(year, pid, safe)

    # ① init 首版
    m = init_first_manifest(pid, name, year, TEST_BUCKET)
    check("init 首版 manifest_version=1", m.get("manifest_version") == 1)
    check("init 首版 files 为空", m.get("files") == [])
    check("init 首版 safe_name 已清洗斜杠", "/" not in m.get("safe_name", ""))
    check("init 首版 prefix 带尾斜杠", m.get("prefix", "").endswith("/"))
    loaded = load_manifest(TEST_BUCKET, mpath)
    check("load 能读回首版", loaded is not None and loaded.get("project_id") == pid)
    first_created = m.get("created_at")

    # ② 幂等：再 init 不覆盖（created_at 不变）
    m2 = init_first_manifest(pid, name, year, TEST_BUCKET)
    check("幂等：created_at 不变", m2.get("created_at") == first_created)

    # ③ append + save 往返
    entry = build_file_entry(1, "a.pdf", "2026/%s/text/pdf/a.pdf" % pid, "text", "pdf", size=10)
    append_file_to_manifest(m2, entry)
    save_manifest(TEST_BUCKET, mpath, m2)
    loaded2 = load_manifest(TEST_BUCKET, mpath)
    check("append 后 files 含 1 条", len(loaded2.get("files", [])) == 1)
    # updated_at 用秒级精度（§3.3），init/append 同秒内可能相等；
    # 真实不变量：updated_at 已重算为有效 ISO 且不早于 created_at
    check("append 后 updated_at 有效且不早于 created_at",
          bool(loaded2.get("updated_at")) and loaded2.get("updated_at") >= (first_created or ""))

    # ④ mark_deleted + save 往返
    mark_file_deleted(loaded2, trace_id=1)
    save_manifest(TEST_BUCKET, mpath, loaded2)
    loaded3 = load_manifest(TEST_BUCKET, mpath)
    check("软删标记落盘 deleted=True", loaded3["files"][0]["deleted"] is True)

    # ⑤ load 不存在 → None
    none_m = load_manifest(TEST_BUCKET, "不存在/路径/workspace-manifest.json")
    check("load 不存在 manifest → None", none_m is None)

    # 清理
    try:
        delete_object(mpath, bucket=TEST_BUCKET)
    except Exception:
        pass

    print(f"\n[result] 通过 {PASS} / 失败 {FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
