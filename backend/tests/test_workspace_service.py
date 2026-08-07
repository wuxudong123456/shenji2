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

    passed = sum(1 for _, c in results if c)
    total = len(results)
    print('\n[result] workspace_service 单测：通过 %d / 失败 %d' % (passed, total - passed))
    return passed == total


if __name__ == '__main__':
    sys.exit(0 if main() else 1)
