"""MinIO 对象存储客户端"""
from minio import Minio
from minio.error import S3Error
from config import Config
from datetime import timedelta
import os
import io

_client = None

def get_client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            Config.MINIO_ENDPOINT,
            access_key=Config.MINIO_ACCESS_KEY,
            secret_key=Config.MINIO_SECRET_KEY,
            secure=Config.MINIO_SECURE
        )
        # 确保 bucket 存在
        bucket = Config.MINIO_BUCKET
        if not _client.bucket_exists(bucket):
            _client.make_bucket(bucket)
    return _client


def upload_file(file_data: bytes, object_path: str, content_type: str = 'application/octet-stream', bucket: str = None) -> str:
    """上传文件到 MinIO，返回对象路径

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    client.put_object(
        target_bucket,
        object_path,
        io.BytesIO(file_data),
        length=len(file_data),
        content_type=content_type
    )
    return object_path


def download_file(object_path: str, bucket: str = None) -> bytes:
    """从 MinIO 下载文件

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）。
                项目文件存在 audit-project-{project_id} bucket，必须显式传入。
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    response = client.get_object(target_bucket, object_path)
    return response.read()


def get_presigned_url(object_path: str, bucket: str = None, expires: int = 3600) -> str:
    """生成预签名下载 URL（有效期默认1小时）

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）。
                项目文件存在 audit-project-{project_id} bucket，下载须显式传入。
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    # 兼容 int（秒）入参；新版 minio 的 presigned_get_object 要求 timedelta
    if isinstance(expires, (int, float)):
        expires = timedelta(seconds=int(expires))
    return client.presigned_get_object(target_bucket, object_path, expires=expires)


def list_objects(prefix: str = '', bucket: str = None, recursive: bool = True) -> list:
    """列出指定前缀下的对象

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）
        recursive: 是否递归列出子前缀下的对象（默认 True）
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    objects = client.list_objects(target_bucket, prefix=prefix, recursive=recursive)
    result = []
    for obj in objects:
        result.append({
            'name': obj.object_name,
            'size': obj.size,
            'last_modified': obj.last_modified.isoformat() if obj.last_modified else '',
            'etag': obj.etag
        })
    return result


def list_folders(prefix: str = '', bucket: str = None) -> list:
    """列出指定前缀下的文件夹（项目目录）

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    objects = client.list_objects(target_bucket, prefix=prefix, recursive=False)
    folders = set()
    for obj in objects:
        if obj.is_dir:
            folders.add(obj.object_name.rstrip('/'))
    return sorted(folders)


def delete_object(object_path: str, bucket: str = None):
    """删除对象

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    client.remove_object(target_bucket, object_path)


def get_object_info(object_path: str, bucket: str = None) -> dict:
    """获取对象元数据

    Args:
        bucket: 指定 bucket（默认用 Config.MINIO_BUCKET）
    """
    client = get_client()
    target_bucket = bucket or Config.MINIO_BUCKET
    stat = client.stat_object(target_bucket, object_path)
    return {
        'name': stat.object_name,
        'size': stat.size,
        'last_modified': stat.last_modified.isoformat() if stat.last_modified else '',
        'content_type': stat.content_type,
        'etag': stat.etag
    }
