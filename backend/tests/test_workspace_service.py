#!/usr/bin/env python
"""workspace_service 单测（PHASE_2）

P2-1: derive_audit_year 年度派生（决策12）
跑法：cd backend && python tests/test_workspace_service.py
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.workspace_service import derive_audit_year

results = []


def check(name, cond):
    results.append((name, bool(cond)))
    print(('  PASS ' if cond else '  FAIL ') + name)


def main():
    print('[P2-1] derive_audit_year 年度派生')
    # audit_period 优先
    y, s = derive_audit_year('2026-01-01至2026-06-30', None)
    check('audit_period 区间取首年 2026', y == '2026' and s == 'audit_period')
    y, s = derive_audit_year('2025年度预算执行', None)
    check('audit_period 含年度文字取 2025', y == '2025' and s == 'audit_period')
    y, s = derive_audit_year('2099-01-01', None)
    check('audit_period 20xx 取 2099', y == '2099' and s == 'audit_period')
    y, s = derive_audit_year('1999', None)
    check('audit_period 19xx 取 1999', y == '1999' and s == 'audit_period')
    # audit_period 缺失/无年份 → created_at 兜底
    y, s = derive_audit_year('', datetime(2026, 8, 7))
    check('audit_period 空 → created_at 兜底 2026', y == '2026' and s == 'created_at')
    y, s = derive_audit_year(None, datetime(2026, 8, 7))
    check('audit_period None → created_at 兜底 2026', y == '2026' and s == 'created_at')
    y, s = derive_audit_year('无年份的文字', datetime(2025, 1, 1))
    check('audit_period 无年份 → created_at 兜底 2025', y == '2025' and s == 'created_at')
    # 极端：两源皆无
    y, s = derive_audit_year(None, None)
    check('两源皆无 → year=None', y is None and s == 'created_at')

    print('[P2-4] classify_file 文件分类')
    from services.workspace_service import classify_file

    def cls(fn, ct=None):
        c, sc = classify_file(fn, ct)
        return '%s/%s' % (c, sc) if sc else c

    check('.docx → text/word', cls('report.docx') == 'text/word')
    check('.doc → text/word', cls('report.doc') == 'text/word')
    check('.pdf → text/pdf', cls('合同.pdf') == 'text/pdf')
    check('无扩展名靠 MIME application/pdf', cls('file', 'application/pdf') == 'text/pdf')
    check('.xlsx → text/excel', cls('data.xlsx') == 'text/excel')
    check('.csv → text/excel', cls('data.csv') == 'text/excel')
    check('.txt → text/txt', cls('note.txt') == 'text/txt')
    check('.md → text/txt', cls('readme.md') == 'text/txt')
    check('image MIME → image', classify_file('x', 'image/png')[0] == 'image')
    check('audio MIME → audio/original', cls('x', 'audio/mpeg') == 'audio/original')
    check('video MIME → video', classify_file('x', 'video/mp4')[0] == 'video')
    check('.png 靠扩展名 → image', classify_file('pic.png')[0] == 'image')
    check('未知 .dat → other', classify_file('data.dat')[0] == 'other')
    check('无扩展无 MIME → other', classify_file('noext')[0] == 'other')

    print('[P2-3] manifest 纯函数')
    from services.workspace_service import (
        compute_safe_name, build_manifest_path, build_file_prefix,
        build_file_entry, append_file_to_manifest, mark_file_deleted,
    )
    check('safe_name 保留中文', compute_safe_name('某局2026预算审计') == '某局2026预算审计')
    check('safe_name 替换斜杠', compute_safe_name('a/b\\c') == 'a_b_c')
    check('safe_name 去控制字符', compute_safe_name('a\x07b') == 'ab')
    check('safe_name 空串 → unnamed', compute_safe_name('') == 'unnamed')
    check('safe_name strip 首尾空', compute_safe_name('  x  ') == 'x')
    check('manifest_path 格式',
          build_manifest_path('2026', 'f0ce', '某局') == '2026/f0ce-某局/workspace-manifest.json')
    check('file_prefix 带尾斜杠',
          build_file_prefix('2026', 'f0ce', '某局') == '2026/f0ce-某局/')
    e = build_file_entry(123, '采购合同.pdf', '2026/x/text/pdf/a.pdf', 'text', 'pdf',
                         size=10, md5='abc', content_type='application/pdf')
    check('file_entry.trace_id', e['trace_id'] == 123)
    check('file_entry.deleted 默认 False', e['deleted'] is False)
    check('file_entry.legacy_raw 默认 False', e['legacy_raw'] is False)
    check('file_entry.uploaded_at 已设', bool(e.get('uploaded_at')))
    m = {"files": [], "updated_at": "old"}
    append_file_to_manifest(m, e)
    check('append 加入 files', len(m['files']) == 1)
    check('append 更新 updated_at', m['updated_at'] != 'old')
    marked = mark_file_deleted(m, trace_id=123)
    check('mark_deleted 按 trace_id 命中', marked and m['files'][0]['deleted'] is True)
    marked2 = mark_file_deleted(m, object_key='不存在')
    check('mark_deleted 未命中 → False', marked2 is False)

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print('\n[result] workspace_service 单测：通过 %d / 失败 %d' % (passed, total - passed))
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
