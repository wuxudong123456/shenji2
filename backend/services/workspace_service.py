"""资料空间服务（PHASE_2 §6.9）

集中资料空间规则：年度派生、safe_name 计算、文件分类映射、manifest 读写、
年度树聚合、跨项目归属校验。路由层只调不重复实现。

本文件随 P2-1..P2-10 逐步填充；当前实现：P2-1 derive_audit_year。
"""
import re
from datetime import datetime


# 4 位年份（19xx / 20xx）
_YEAR_RE = re.compile(r'(?:19|20)\d{2}')


def derive_audit_year(audit_period, created_at):
    """推导审计年度（决策 12）。

    优先从 audit_period 正则取第一个 4 位年份（如 "2026-01-01至2026-06-30" → "2026"）；
    audit_period 缺失或解析失败 → 兜底 created_at 年份。
    派生值落 audit_document_traces.audit_year（冗余，便于年度树/对账查询），不落 audit_projects。

    Args:
        audit_period: 审计期间字符串（可为 None / 空）
        created_at: 兜底时间源（datetime 或可解析字符串）

    Returns:
        (year: str, source: str) — source 为 'audit_period' 或 'created_at'；
        极端情况（两源皆无）year 为 None、source 为 'created_at'。
    """
    if audit_period:
        m = _YEAR_RE.search(str(audit_period))
        if m:
            return m.group(0), 'audit_period'
    # 兜底 created_at
    if created_at is None:
        return None, 'created_at'
    if isinstance(created_at, datetime):
        return str(created_at.year), 'created_at'
    m = _YEAR_RE.search(str(created_at))
    if m:
        return m.group(0), 'created_at'
    return None, 'created_at'


def classify_file(filename, content_type=None):
    """文件类型分类（§3.4）。后端判定，前端不传分类。

    判定优先级：扩展名（text 类）→ MIME（image/audio/video）→ 扩展名兜底 → other。
    Legacy {pid}/raw/ 旧文件首次纳入时按本表尽量归类，无法判定归 other。

    Returns:
        (category: str, subcategory: str|None)
        category ∈ text/image/audio/video/other
    """
    name = (filename or '').lower()
    ct = (content_type or '').lower()
    ext = '.' + name.rsplit('.', 1)[-1] if '.' in name else ''
    # text 类按扩展名
    if ext in ('.doc', '.docx'):
        return 'text', 'word'
    if ext == '.pdf' or ct == 'application/pdf':
        return 'text', 'pdf'
    if ext in ('.xls', '.xlsx', '.csv'):
        return 'text', 'excel'
    if ext in ('.txt', '.md'):
        return 'text', 'txt'
    # MIME 类
    if ct.startswith('image/'):
        return 'image', None
    if ct.startswith('audio/'):
        return 'audio', 'original'
    if ct.startswith('video/'):
        return 'video', None
    # 扩展名兜底（无 MIME 时）
    if ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.tiff'):
        return 'image', None
    if ext in ('.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg'):
        return 'audio', 'original'
    if ext in ('.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv'):
        return 'video', None
    return 'other', None
