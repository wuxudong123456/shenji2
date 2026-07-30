import os
from pathlib import Path
from dotenv import load_dotenv
# 用绝对路径确保 nohup/后台启动也能找到 .env
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path, override=True)

class Config:
    FLASK_HOST = os.environ.get('FLASK_HOST', '0.0.0.0')
    FLASK_PORT = int(os.environ.get('FLASK_PORT', '5000'))

    # MinIO
    MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', '192.168.3.164:9100')
    MINIO_ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', 'minioadmin')
    MINIO_SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', 'minioadmin')
    MINIO_BUCKET = os.environ.get('MINIO_BUCKET', 'audit-materials')
    MINIO_SECURE = os.environ.get('MINIO_SECURE', 'false').lower() == 'true'

    # MySQL
    MYSQL_HOST = os.environ.get('MYSQL_HOST', '127.0.0.1')
    MYSQL_PORT = int(os.environ.get('MYSQL_PORT', '3306'))
    MYSQL_USER = os.environ.get('MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('MYSQL_PASSWORD', '123456')
    MYSQL_DATABASE = os.environ.get('MYSQL_DATABASE', 'auditkm_factory')

    UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')

    # OCR 引擎配置
    OCR_ENGINE = os.environ.get('OCR_ENGINE', 'liteparse')
    MINERU_BASE_URL = os.environ.get('MINERU_BASE_URL', 'http://192.168.3.189:5005')
    LITEPARSE_URL = os.environ.get('LITEPARSE_URL', 'http://127.0.0.1:5006')
