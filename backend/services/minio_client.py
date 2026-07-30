"""MinIO 对象存储客户端"""
from minio import Minio
from minio.error import S3Error
from config import Config
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


def upload_file(file_data: bytes, object_path: str, content_type: str = 'application/octet-stream') -> str:
    """上传文件到 MinIO，返回对象路径"""
    client = get_client()
    client.put_object(
        Config.MINIO_BUCKET,
        object_path,
        io.BytesIO(file_data),
        length=len(file_data),
        content_type=content_type
    )
    return object_path


def download_file(object_path: str) -> bytes:
    """从 MinIO 下载文件"""
    client = get_client()
    response = client.get_object(Config.MINIO_BUCKET, object_path)
    return response.read()


def get_presigned_url(object_path: str, expires: int = 3600) -> str:
    """生成预签名下载 URL（有效期默认1小时）"""
    client = get_client()
    return client.presigned_get_object(Config.MINIO_BUCKET, object_path, expires=expires)


def list_objects(prefix: str = '') -> list:
    """列出指定前缀下的对象"""
    client = get_client()
    objects = client.list_objects(Config.MINIO_BUCKET, prefix=prefix, recursive=True)
    result = []
    for obj in objects:
        result.append({
            'name': obj.object_name,
            'size': obj.size,
            'last_modified': obj.last_modified.isoformat() if obj.last_modified else '',
            'etag': obj.etag
        })
    return result


def list_folders(prefix: str = '') -> list:
    """列出指定前缀下的文件夹（项目目录）"""
    client = get_client()
    objects = client.list_objects(Config.MINIO_BUCKET, prefix=prefix, recursive=False)
    folders = set()
    for obj in objects:
        if obj.is_dir:
            folders.add(obj.object_name.rstrip('/'))
    return sorted(folders)


def delete_object(object_path: str):
    """删除对象"""
    client = get_client()
    client.remove_object(Config.MINIO_BUCKET, object_path)


def get_object_info(object_path: str) -> dict:
    """获取对象元数据"""
    client = get_client()
    stat = client.stat_object(Config.MINIO_BUCKET, object_path)
    return {
        'name': stat.object_name,
        'size': stat.size,
        'last_modified': stat.last_modified.isoformat() if stat.last_modified else '',
        'content_type': stat.content_type,
        'etag': stat.etag
    }
