"""OCR 引擎客户端 — 支持 MinerU / LiteParse 双引擎切换"""
import requests
from config import Config


class OCREngine:
    """OCR 引擎抽象层"""

    @staticmethod
    def get_engine():
        engine = Config.OCR_ENGINE or 'mineru'
        if engine == 'liteparse':
            return LiteParseClient()
        return MinerUClient()

    @staticmethod
    def parse(file_path: str, template: str = None) -> dict:
        """统一解析接口"""
        return OCREngine.get_engine().parse(file_path, template)


class MinerUClient:
    """MinerU (OntoSKU) OCR 引擎 — 远程服务"""

    def __init__(self):
        self.base_url = Config.MINERU_BASE_URL or 'http://192.168.3.189:5005'

    def parse(self, file_path: str, template: str = None) -> dict:
        """调用 OntoSKU SKU 提取接口"""
        url = f"{self.base_url}/v1/sku/upload"
        with open(file_path, 'rb') as f:
            files = {'file': f}
            data = {}
            if template:
                data['sku_profiles'] = template
            resp = requests.post(url, files=files, data=data, timeout=300)
        if resp.status_code == 200:
            result = resp.json()
            return {
                'success': True,
                'engine': 'mineru',
                'job_id': result.get('job_id', ''),
                'message': '已提交 MinerU 解析任务'
            }
        return {'success': False, 'engine': 'mineru', 'error': resp.text}

    def get_result(self, document_id: str) -> dict:
        """获取解析结果"""
        url = f"{self.base_url}/v1/sku/documents/{document_id}"
        resp = requests.get(url, timeout=60)
        if resp.status_code == 200:
            return {'success': True, 'data': resp.json()}
        return {'success': False, 'error': resp.text}

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except:
            return False


class LiteParseClient:
    """LiteParse OCR 引擎 — 本地快速引擎"""

    def __init__(self):
        self.base_url = Config.LITEPARSE_URL or 'http://127.0.0.1:5006'

    def parse(self, file_path: str, template: str = None) -> dict:
        """调用 LiteParse 解析接口"""
        url = f"{self.base_url}/parse"
        with open(file_path, 'rb') as f:
            files = {'file': f}
            resp = requests.post(url, files=files, timeout=120)
        if resp.status_code == 200:
            result = resp.json()
            return {
                'success': True,
                'engine': 'liteparse',
                'fields': result.get('fields', []),
                'text': result.get('text', ''),
                'metadata': result.get('metadata', {})
            }
        return {'success': False, 'engine': 'liteparse', 'error': resp.text}

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=3)
            return r.status_code == 200
        except:
            return False
