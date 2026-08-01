"""OCR 引擎客户端 — 支持 MinerU(OntoSKU) / LiteParse 双引擎切换

引擎分工:
  - MinerU (189:5005): OCR + OntoSKU 结构化提取（主力，处理扫描件）
  - LiteParse (127.0.0.1:5006): 仅 Markdown 转换（处理原生数字 PDF，不做 OCR）

两个引擎的 parse() 返回统一形状:
  {success, engine, text/markdown, fields, ...}
"""
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
        """统一解析接口

        Args:
            file_path: 本地文件路径
            template: OntoSKU sku_profiles 模板名（如 "audit/合同协议类/采购合同"）
                      仅 MinerU 引擎使用；LiteParse 忽略此参数
        """
        return OCREngine.get_engine().parse(file_path, template)


class MinerUClient:
    """MinerU (OntoSKU) OCR + 结构化提取引擎 — 远程异步服务

    改造说明（Q3.2）:
      原实现只提交任务拿 job_id，不取结果 → 调用方读到空 text
      现改为: 提交 → 同步轮询等待 → 返回含 text/fields 的完整结果
      实际的轮询逻辑委托给 ontosku_client.OntoSKUClient
    """

    def __init__(self):
        from services.ontosku_client import OntoSKUClient
        self._client = OntoSKUClient(Config.MINERU_BASE_URL or 'http://192.168.3.189:5005')

    def parse(self, file_path: str, template: str = None) -> dict:
        """调用 OntoSKU 提取（同步等待结果）

        Returns:
            {
                success: True,
                engine: 'mineru',
                text: '...',          # Markdown 全文（和 LiteParse 对齐）
                fields: {...},        # 结构化字段（中文字段名）
                document_id: '...',   # OntoSKU 文档ID（溯源用）
                chunks: [...],        # 溯源 chunk（page_nums + bbox）
                pages: N,
            }
        """
        try:
            result = self._client.extract(file_path, sku_profile=template)
            return {
                'success': True,
                'engine': 'mineru',
                'text': result.get('markdown', ''),
                'markdown': result.get('markdown', ''),
                'fields': result.get('fields', {}),
                'document_id': result.get('document_id', ''),
                'chunks': result.get('chunks', []),
                'pages': result.get('pages', 0),
            }
        except Exception as e:
            return {
                'success': False,
                'engine': 'mineru',
                'error': str(e),
            }

    def health(self) -> bool:
        return self._client.health()


class LiteParseClient:
    """LiteParse OCR 引擎 — 本地 Markdown 转换（不做 OCR）"""

    def __init__(self):
        self.base_url = Config.LITEPARSE_URL or 'http://127.0.0.1:5006'

    def parse(self, file_path: str, template: str = None) -> dict:
        """调用 LiteParse 解析接口（template 参数被忽略）"""
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
                'markdown': result.get('text', ''),
                'metadata': result.get('metadata', {}),
                'pages': result.get('pages', 0),
            }
        return {'success': False, 'engine': 'liteparse', 'error': resp.text}

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=3)
            return r.status_code == 200
        except:
            return False
