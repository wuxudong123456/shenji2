#!/usr/bin/env python
"""minio_client bucket 参数单测（PHASE_2 §6.7）

验证三点：
  1. 默认桶不破：bucket=None → 回落 Config.MINIO_BUCKET（旧调用行为不变）
  2. 指定 bucket 隔离：bucket=A 的操作不影响 bucket=B 的对象
  3. list_objects recursive 参数生效

直连真实 MinIO（Config.MINIO_*）。
跑法：cd backend && python tests/test_minio_client_bucket.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import minio_client
from config import Config

DEFAULT = Config.MINIO_BUCKET
TEST_BUCKET = 'aw-test-bucket-param'

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(('  PASS ' if cond else '  FAIL ') + name)


def setup():
    client = minio_client.get_client()
    if not client.bucket_exists(TEST_BUCKET):
        client.make_bucket(TEST_BUCKET)


def cleanup_prefix():
    """清两个桶里所有 test-bucket-param/ 前缀对象"""
    for b in (DEFAULT, TEST_BUCKET):
        try:
            for o in minio_client.list_objects(prefix='test-bucket-param/', bucket=b):
                minio_client.delete_object(o['name'], bucket=b)
        except Exception:
            pass


def main():
    setup()
    cleanup_prefix()  # 起点干净
    stamp = int(time.time())
    data = b'hello-bucket-param'

    print('[1] 默认桶不破（bucket=None → Config.MINIO_BUCKET）')
    k1 = 'test-bucket-param/sec1-%d.txt' % stamp
    k1n = 'test-bucket-param/sec1n-%d.txt' % stamp
    minio_client.upload_file(data, k1)   # 默认桶
    minio_client.upload_file(data, k1n)  # 默认桶
    objs = minio_client.list_objects(prefix='test-bucket-param/')
    check('list_objects(bucket=None) 列默认桶对象', any(o['name'] == k1 for o in objs))
    info = minio_client.get_object_info(k1)
    check('get_object_info(bucket=None) 取默认桶元数据', info['name'] == k1 and info['size'] == len(data))
    url = minio_client.get_presigned_url(k1)
    check('get_presigned_url(bucket=None) 返回URL', isinstance(url, str) and len(url) > 0)
    minio_client.delete_object(k1n)
    objs2 = minio_client.list_objects(prefix='test-bucket-param/')
    check('delete_object(bucket=None) 删默认桶对象', not any(o['name'] == k1n for o in objs2))
    minio_client.delete_object(k1)  # [1] 自清，避免污染 [2] 隔离断言

    print('[2] 指定 bucket 隔离路由')
    k2 = 'test-bucket-param/sec2-%d.txt' % stamp  # 仅上传 TEST_BUCKET
    minio_client.upload_file(data, k2, bucket=TEST_BUCKET)
    objs_def = minio_client.list_objects(prefix='test-bucket-param/', bucket=DEFAULT)
    objs_tst = minio_client.list_objects(prefix='test-bucket-param/', bucket=TEST_BUCKET)
    check('指定 bucket 隔离：默认桶无该对象', not any(o['name'] == k2 for o in objs_def))
    check('指定 bucket 隔离：TEST桶有该对象', any(o['name'] == k2 for o in objs_tst))
    info_t = minio_client.get_object_info(k2, bucket=TEST_BUCKET)
    check('get_object_info(bucket=TEST) 指定桶元数据', info_t['name'] == k2)
    url_t = minio_client.get_presigned_url(k2, bucket=TEST_BUCKET)
    check('get_presigned_url(bucket=TEST) 指定桶URL', isinstance(url_t, str) and len(url_t) > 0)
    minio_client.delete_object(k2, bucket=TEST_BUCKET)
    objs_t2 = minio_client.list_objects(prefix='test-bucket-param/', bucket=TEST_BUCKET)
    check('delete_object(bucket=TEST) 删指定桶对象', not any(o['name'] == k2 for o in objs_t2))

    print('[3] list_objects recursive 参数')
    top = 'test-bucket-param/sec3-top-%d.txt' % stamp
    sub = 'test-bucket-param/sec3-dir/sub-%d.txt' % stamp
    minio_client.upload_file(b'a', top, bucket=TEST_BUCKET)
    minio_client.upload_file(b'b', sub, bucket=TEST_BUCKET)
    lst_top = minio_client.list_objects(prefix='test-bucket-param/', bucket=TEST_BUCKET, recursive=False)
    lst_deep = minio_client.list_objects(prefix='test-bucket-param/', bucket=TEST_BUCKET, recursive=True)
    has_dir_prefix = any(o['name'].startswith('test-bucket-param/sec3-dir/') for o in lst_top)
    no_sub_in_top = not any('sec3-dir/sub' in o['name'] for o in lst_top)
    check('recursive=False 只列顶层（dir前缀在，sub文件不在）', has_dir_prefix and no_sub_in_top)
    check('recursive=True 递归列出 sub 文件', any('sec3-dir/sub' in o['name'] for o in lst_deep))
    check('recursive=True 结果不少于 recursive=False', len(lst_deep) >= len(lst_top))

    cleanup_prefix()

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print('\n[result] minio_client bucket 参数单测：通过 %d / 失败 %d' % (passed, total - passed))
    return passed == total


if __name__ == '__main__':
    ok = main()
    sys.exit(0 if ok else 1)
