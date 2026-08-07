"""资料空间服务（PHASE_2 §6.9）

集中资料空间规则：年度派生、safe_name 计算、文件分类映射、manifest 读写、
年度树聚合、跨项目归属校验。路由层只调不重复实现。

本文件随 P2-1..P2-10 逐步填充；当前实现：P2-1 derive_audit_year。
"""
import json
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


# ── workspace manifest（§3.3 / §6.9）──
# manifest 是 MinIO 对象（非 MySQL 表），存在 {year}/{pid}-{safe_name}/workspace-manifest.json。
# 年度树/文件列表以 manifest 为单一事实源；上传/软删是唯一合法变更点（§7）。

MANIFEST_VERSION = 1
MANIFEST_FILENAME = "workspace-manifest.json"


def compute_safe_name(project_name):
    """计算项目安全名（用于 MinIO 前缀，§3.1/§3.3）。

    前缀形如 {year}/{pid}-{safe_name}/。安全名取项目名，仅清洗 MinIO 对象键非法字符
    （路径分隔符 / 控制字符），中文等保留。§3.3 示例中 safe_name 与 project_name 文字略异
    系示意，方案未定义文字级转换规则，故本实现只做路径清洗，不臆造删字规则。
    """
    cleaned = []
    for ch in (project_name or '').strip():
        if ch in ('/', '\\'):
            cleaned.append('_')
        elif ord(ch) < 0x20:
            continue  # 丢弃控制字符
        else:
            cleaned.append(ch)
    safe = ''.join(cleaned).strip()
    return safe or 'unnamed'


def build_file_prefix(audit_year, project_id, safe_name):
    """项目文件前缀（带尾斜杠）：{year}/{pid}-{safe_name}/"""
    return "{}/{}-{}/".format(audit_year, project_id, safe_name)


def build_manifest_path(audit_year, project_id, safe_name):
    """manifest 对象键：{year}/{pid}-{safe_name}/workspace-manifest.json"""
    return "{}workspace-manifest.json".format(build_file_prefix(audit_year, project_id, safe_name))


def load_manifest(bucket, manifest_path):
    """读取 manifest；不存在或解析失败返回 None（§7：读失败可重建，不静默崩）。"""
    from services.minio_client import download_file
    try:
        data = download_file(manifest_path, bucket=bucket)
    except Exception:
        return None
    try:
        return json.loads(data.decode('utf-8'))
    except Exception:
        return None


def save_manifest(bucket, manifest_path, manifest):
    """写回 manifest（覆盖，JSON）。"""
    from services.minio_client import upload_file
    data = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    upload_file(data, manifest_path, content_type='application/json', bucket=bucket)


def init_first_manifest(project_id, project_name, audit_year, bucket):
    """finalize 生成首版 manifest（§6.6，幂等）。

    已存在则直接返回不覆盖；不存在则建空 files[] 首版并写回。
    Returns: manifest dict。
    """
    safe_name = compute_safe_name(project_name)
    manifest_path = build_manifest_path(audit_year, project_id, safe_name)
    existing = load_manifest(bucket, manifest_path)
    if existing:
        return existing
    now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "project_id": project_id,
        "project_name": project_name,
        "safe_name": safe_name,
        "audit_year": audit_year,
        "bucket": bucket,
        "prefix": build_file_prefix(audit_year, project_id, safe_name),
        "created_at": now,
        "updated_at": now,
        "files": [],
    }
    save_manifest(bucket, manifest_path, manifest)
    return manifest


def build_file_entry(trace_id, file_name, object_key, category, subcategory,
                     size=None, md5=None, content_type=None, legacy_raw=False):
    """构造 files[] 单条记录（§3.3 结构）。object_key 由调用方（P2-6 upload）按 §6.1 构造。"""
    return {
        "trace_id": trace_id,
        "file_name": file_name,
        "object_key": object_key,
        "category": category,
        "subcategory": subcategory,
        "size": size,
        "md5": md5,
        "content_type": content_type,
        "uploaded_at": datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        "deleted": False,
        "legacy_raw": bool(legacy_raw),
    }


def append_file_to_manifest(manifest, file_entry):
    """把 file_entry 追加进 manifest.files[]（增量更新 updated_at）。

    纯内存操作；调用方随后 save_manifest 落盘（上传是唯一合法变更点，§7）。
    """
    manifest.setdefault("files", []).append(file_entry)
    manifest["updated_at"] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return manifest


def mark_file_deleted(manifest, trace_id=None, object_key=None):
    """软删标记：files[].deleted=True（增量更新 updated_at，§3.5）。

    按 trace_id 优先匹配，否则 object_key。纯内存操作；调用方随后 save_manifest。
    Returns: 是否找到并标记。
    """
    found = False
    for f in manifest.get("files", []):
        if (trace_id is not None and f.get("trace_id") == trace_id) or \
           (object_key is not None and f.get("object_key") == object_key):
            f["deleted"] = True
            found = True
    if found:
        manifest["updated_at"] = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    return found


def parse_pid_from_key(object_key):
    """从 object_key 前缀解析 project_id（§3.6 P2-10 跨项目归属校验）。

    支持两种前缀：
      - 新格式：{year}/{pid}-{safe_name}/...（first 段是 4 位年份 → pid 在 second 段 dash 前）
      - legacy 格式：{pid}/raw/...（first 段即 pid）
    解析失败/无法判定返回 None。
    """
    if not object_key:
        return None
    parts = object_key.split("/")
    if len(parts) < 2:
        return None
    first, second = parts[0], parts[1]
    if _YEAR_RE.fullmatch(first):
        # 新格式：second = "{pid}-{safe_name}"，pid 在首个 dash 前（pid 本身无 dash）
        return second.split("-", 1)[0] if "-" in second else None
    # legacy 格式：first 段即 pid
    return first
