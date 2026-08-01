"""OntoSKU 原生提取引擎客户端 — 真实 API 契约（2026-07-31 探测确认）

OntoSKU 1.0.0.1 真实流程（presigned URL 模式）:
  1. POST /v1/jobs {source_type:file, file_name, sku_profiles} → {job_id, upload_url, upload_headers}
  2. PUT {upload_url} 文件字节 + upload_headers → 直传 MinIO 预签名地址
  3. POST /v1/jobs/{job_id}/confirm-upload → 触发处理
  4. 轮询 GET /v1/jobs/{job_id} → status: pending→running→done/failed
  5. done 后 GET /v1/documents/{document_id} → 结构化字段 + chunks

认证: Authorization: Bearer {api_key}
API Key 获取: GET /v1/sku/local-api-key
模板: GET /v1/sku/profiles（服务端自带 1548 套）

用法:
    client = OntoSKUClient()
    result = client.extract("合同.pdf", sku_profile="audit/合同协议类/采购合同")
    # → {markdown, fields, document_id, chunks}
"""
import json
import time
import requests
from config import Config


class OntoSKUError(Exception):
    """OntoSKU 调用异常"""


class OntoSKUClient:
    """OntoSKU 原生提取客户端（presigned URL 模式）"""

    def __init__(self, base_url: str = None, api_key: str = None):
        self.base_url = (base_url or Config.MINERU_BASE_URL or
                         "http://192.168.3.189:5005").rstrip("/")
        # API key：优先参数 > 配置 > 动态获取
        self.api_key = api_key or getattr(Config, "ONTOSKU_API_KEY", None)
        # 超时/轮询/重试 均从 Config(.env) 读取，运维可调
        self.timeout = getattr(Config, "ONTOSKU_TIMEOUT", 300)
        self.poll_interval = getattr(Config, "ONTOSKU_POLL_INTERVAL", 5)
        self.max_wait = getattr(Config, "ONTOSKU_MAX_WAIT", 600)
        self.retries = getattr(Config, "ONTOSKU_RETRIES", 2)

    def _headers(self) -> dict:
        """带认证的请求头"""
        h = {}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _ensure_api_key(self):
        """如果没配 API key，动态获取本地 key"""
        if self.api_key:
            return
        try:
            r = requests.get(f"{self.base_url}/v1/sku/local-api-key", timeout=5)
            if r.status_code == 200:
                self.api_key = r.json().get("api_key")
        except Exception:
            pass

    # ── 主流程 ──

    def extract(self, file_path: str, sku_profile: str = None,
                max_wait: int = None) -> dict:
        """一站式提取：建job→上传→确认→轮询→取结果

        Args:
            file_path: 本地文件路径
            sku_profile: SKU 模板名（如 "audit/合同协议类/采购合同"），留空则自动分类
            max_wait: 最大等待秒数

        Returns:
            {document_id, markdown, fields, chunks, job_id, raw}
        """
        self._ensure_api_key()
        import os
        file_name = os.path.basename(file_path)

        # 1. 创建 job
        job = self._create_job(file_name, sku_profile)
        job_id = job["job_id"]
        upload_url = job.get("upload_url")
        upload_headers = job.get("upload_headers") or {}

        if not upload_url:
            raise OntoSKUError(f"job 未返回 upload_url: {job}")

        # 2. 上传文件到 presigned URL
        with open(file_path, "rb") as f:
            file_bytes = f.read()
        self._upload_file(upload_url, file_bytes, upload_headers)

        # 3. 确认上传
        self._confirm_upload(job_id)

        # 4. 轮询直到完成
        result = self._wait_for_job(job_id, max_wait or self.max_wait)

        # 5. 取结构化结果（ZIP 包含 markdown + sku 字段 + chunks）
        document_id = result.get("document_id")
        result_url = (result.get("result") or {}).get("result_url") if isinstance(result.get("result"), dict) else None
        # 兼容 result_url 直接在顶层
        if not result_url:
            result_url = result.get("result_url")

        zip_data = {"markdown": "", "fields": {}, "chunks": []}
        if result_url:
            zip_data = self._fetch_result_zip(result_url)

        # chunks 也可从 document chunks 端点补充（若 ZIP 没有）
        if not zip_data.get("chunks") and document_id:
            doc = self._get_document(document_id)
            zip_data["chunks"] = doc.get("chunks", [])

        return self._normalize(result, zip_data, document_id, job_id)

    # ── 步骤实现 ──

    def _create_job(self, file_name: str, sku_profile: str = None) -> dict:
        """POST /v1/jobs — 创建任务，拿到 presigned upload_url（带重试）"""
        payload = {
            "source_type": "file",
            "file_name": file_name,
            "parsing_params": {"model": "base"},
        }
        if sku_profile:
            payload["sku_profiles"] = sku_profile
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.post(f"{self.base_url}/v1/jobs", json=payload,
                                  headers=self._headers(), timeout=15)
                if r.status_code == 200:
                    return r.json()
                last = f"HTTP {r.status_code}: {r.text[:300]}"
            except requests.RequestException as e:
                last = str(e)
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        raise OntoSKUError(f"创建 job 失败（重试 {self.retries} 次）: {last}")

    def _upload_file(self, upload_url: str, file_bytes: bytes,
                     upload_headers: dict):
        """PUT 文件字节到 presigned MinIO URL（带重试）"""
        headers = {"Content-Type": "application/octet-stream"}
        headers.update(upload_headers)
        last = None
        for attempt in range(self.retries + 1):
            try:
                r = requests.put(upload_url, data=file_bytes, headers=headers, timeout=120)
                if r.status_code in (200, 204):
                    return
                last = f"HTTP {r.status_code}: {r.text[:300]}"
            except requests.RequestException as e:
                last = str(e)
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        raise OntoSKUError(f"上传文件失败（重试 {self.retries} 次）: {last}")

    def _confirm_upload(self, job_id: str):
        """POST /v1/jobs/{id}/confirm-upload — 触发处理"""
        try:
            r = requests.post(f"{self.base_url}/v1/jobs/{job_id}/confirm-upload",
                              headers=self._headers(), timeout=15)
        except requests.RequestException as e:
            raise OntoSKUError(f"确认上传失败: {e}") from e
        if r.status_code != 200:
            raise OntoSKUError(f"确认上传 HTTP {r.status_code}: {r.text[:300]}")

    def _wait_for_job(self, job_id: str, max_wait: int) -> dict:
        """轮询 GET /v1/jobs/{id} 直到 done/failed"""
        deadline = time.time() + max_wait
        last_msg = None
        while time.time() < deadline:
            try:
                r = requests.get(f"{self.base_url}/v1/jobs/{job_id}",
                                 headers=self._headers(), timeout=10)
            except requests.RequestException:
                time.sleep(self.poll_interval)
                continue
            if r.status_code != 200:
                time.sleep(self.poll_interval)
                continue
            data = r.json()
            status = data.get("status")
            progress = data.get("progress") or {}
            msg = progress.get("message", "") if isinstance(progress, dict) else ""

            if status == "done":
                return data
            if status == "failed":
                err = data.get("error") or msg or "未知错误"
                raise OntoSKUError(f"OntoSKU 处理失败: {err}")
            # 进度变化时记录（调试用）
            if msg != last_msg:
                last_msg = msg
            time.sleep(self.poll_interval)

        raise OntoSKUError(f"OntoSKU 处理超时（{max_wait}s），最后状态: {status}")

    def _get_document(self, document_id: str) -> dict:
        """取结构化提取结果（通过 document_id）

        OntoSKU 真实结构（2026-08-01 验证）:
          - GET /v1/documents/{id} 只返回元数据（不含内容）
          - GET /v1/documents/{id}/chunks 返回解析的 chunks（表格/文本）
          - 完整结果（含 sku 字段提取）在 result_url 的 ZIP 里：
              full.md          — Markdown 全文
              sku_results.json — 按 sku_profile 分类的结构化字段（核心）
              chunks.json      — 带溯源的 chunks
        本方法下载 ZIP 并解析出 markdown + fields + chunks。
        """
        # 先取 document 元数据（拿不到 result_url，result_url 在 job 里）
        doc_meta = {}
        try:
            r = requests.get(f"{self.base_url}/v1/documents/{document_id}",
                             headers=self._headers(), timeout=15)
            if r.status_code == 200:
                doc_meta = r.json()
        except requests.RequestException:
            pass
        # 取 chunks（溯源用）
        chunks = []
        try:
            r = requests.get(f"{self.base_url}/v1/documents/{document_id}/chunks",
                             headers=self._headers(), timeout=15)
            if r.status_code == 200:
                chunks = r.json().get("chunks", [])
        except requests.RequestException:
            pass
        return {"doc_meta": doc_meta, "chunks": chunks}

    def _fetch_result_zip(self, result_url: str) -> dict:
        """下载 result_url 的 ZIP 包，解析出 markdown / fields / chunks

        Returns:
            {markdown, fields, chunks}
        """
        import zipfile, io
        try:
            r = requests.get(result_url, timeout=60)
            if r.status_code != 200:
                return {"markdown": "", "fields": {}, "chunks": []}
            z = zipfile.ZipFile(io.BytesIO(r.content))
        except Exception:
            return {"markdown": "", "fields": {}, "chunks": []}

        # full.md → markdown 全文
        markdown = ""
        if "full.md" in z.namelist():
            markdown = z.read("full.md").decode("utf-8", errors="ignore")

        # sku_results.json → 结构化字段（按 sku_profile 分类）
        # 结构: {"general/base_model": {"model_result": {"fields": {...}}},
        #        "audit/财务票据类/发票": {"model_result": {"fields": {...}}}, ...}
        # 取所有 profile 的 fields 合并，audit/* 优先
        fields = {}
        if "sku_results.json" in z.namelist():
            try:
                sku = json.loads(z.read("sku_results.json"))
                # 优先取 audit/* 的字段（业务模板），其次 general
                ordered_keys = sorted(sku.keys(),
                                      key=lambda k: (0 if k.startswith("audit/") else 1))
                for key in ordered_keys:
                    prof = sku.get(key) or {}
                    model_result = prof.get("model_result") or {}
                    f = model_result.get("fields") or {}
                    if isinstance(f, dict):
                        for fk, fv in f.items():
                            if fk not in fields and fv:  # 不覆盖已审计模板的值
                                fields[fk] = fv
            except Exception:
                pass

        # chunks.json → 带溯源的 chunks
        chunks = []
        if "chunks.json" in z.namelist():
            try:
                chunks = json.loads(z.read("chunks.json")).get("chunks", [])
            except Exception:
                pass

        return {"markdown": markdown, "fields": fields, "chunks": chunks}

    # ── 结果归一化 ──

    def _normalize(self, job_result: dict, zip_data: dict,
                   document_id: str, job_id: str) -> dict:
        """把 OntoSKU 响应统一为标准结构

        zip_data 来自 result_url 的 ZIP，含 markdown / fields / chunks
        """
        return {
            "document_id": document_id or job_result.get("document_id", ""),
            "job_id": job_id,
            "markdown": zip_data.get("markdown", ""),
            "fields": zip_data.get("fields", {}),
            "chunks": zip_data.get("chunks", []),
            "raw_job": job_result,
        }

    # ── 健康检查 ──

    def health(self) -> bool:
        """检查 OntoSKU 服务是否可用"""
        try:
            r = requests.get(f"{self.base_url}/health", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


# ── 全局单例 ──
_client: OntoSKUClient = None


def get_client() -> OntoSKUClient:
    """获取 OntoSKU 客户端单例"""
    global _client
    if _client is None:
        _client = OntoSKUClient()
    return _client
