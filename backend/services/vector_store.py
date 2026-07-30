"""Phase 6.4 — FAISS 向量检索引擎

基于 sentence-transformers + faiss 的本地语义搜索。
不依赖外部 Embedding API，不依赖 Milvus。

用法:
    store = VectorStore()
    results = store.search("招标投标合规性", top_k=10)
    # → [{id, title, similarity: 0.95}, ...]
"""
import os
import pickle
import numpy as np
from pathlib import Path
from typing import Optional

import faiss
from sentence_transformers import SentenceTransformer

from services.db import query

# 模型和索引的缓存路径
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / ".vector_cache"
MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384  # 该模型的输出维度


class VectorStore:
    """FAISS 向量索引 + 语义搜索

    索引两类数据:
      - laws:      sys_core_law_allaudit 法规标题
      - violations: audit_violations 违规行为

    首次使用自动构建索引并缓存到磁盘，后续秒级加载。
    """

    def __init__(self):
        self._model: Optional[SentenceTransformer] = None
        self._law_index: Optional[faiss.IndexFlatIP] = None
        self._law_ids: list[str] = []
        self._violation_index: Optional[faiss.IndexFlatIP] = None
        self._violation_ids: list[int] = []
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = SentenceTransformer(MODEL_NAME)
        return self._model

    def _encode(self, texts: list[str]) -> np.ndarray:
        """批量文本 → 向量（内部归一化，适合内积相似度）"""
        if not texts:
            return np.array([], dtype=np.float32)
        vectors = self.model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
        return vectors.astype(np.float32)

    # ── 法规索引 ──

    def build_law_index(self, force: bool = False):
        """构建法规 FAISS 索引"""
        cache_file = CACHE_DIR / "law_index.pkl"
        if not force and cache_file.exists():
            with open(cache_file, "rb") as f:
                data = pickle.load(f)
                self._law_index = faiss.deserialize_index(data["index"])
                self._law_ids = data["ids"]
            return

        # 从数据库加载
        rows = query(
            "SELECT id, title FROM sys_core_law_allaudit WHERE status = 1 AND title IS NOT NULL",
            database="audit_law"
        )
        texts = [r["title"] for r in rows]
        self._law_ids = [r["id"] for r in rows]

        if texts:
            vectors = self._encode(texts)
            self._law_index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self._law_index.add(vectors)

            # 缓存到磁盘
            with open(cache_file, "wb") as f:
                pickle.dump({
                    "index": faiss.serialize_index(self._law_index),
                    "ids": self._law_ids,
                }, f)

    def search_laws(self, query_text: str, top_k: int = 10) -> list[dict]:
        """语义搜索法规"""
        self.build_law_index()
        if self._law_index is None or not self._law_ids:
            return []

        vec = self._encode([query_text])
        scores, indices = self._law_index.search(vec, min(top_k, len(self._law_ids)))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._law_ids):
                continue
            results.append({
                "id": self._law_ids[idx],
                "similarity": round(float(score), 4),
            })

        # 批量查标题
        if results:
            ids = [r["id"] for r in results]
            placeholders = ",".join(["%s"] * len(ids))
            rows = query(
                f"SELECT id, title, potency_level, timeliness FROM sys_core_law_allaudit "
                f"WHERE id IN ({placeholders})",
                tuple(ids), database="audit_law",
            )
            title_map = {r["id"]: r for r in rows}
            for r in results:
                law = title_map.get(r["id"], {})
                r["title"] = law.get("title", "")
                r["potency_level"] = law.get("potency_level")
                r["timeliness"] = law.get("timeliness")

        return results

    # ── 违规索引 ──

    def build_violation_index(self, force: bool = False):
        """构建违规行为 FAISS 索引"""
        cache_file = CACHE_DIR / "violation_index.pkl"
        if not force and cache_file.exists():
            with open(cache_file, "rb") as f:
                data = pickle.load(f)
                self._violation_index = faiss.deserialize_index(data["index"])
                self._violation_ids = data["ids"]
            return

        rows = query(
            "SELECT id, violation_title, description FROM audit_violations WHERE deleted = 0",
            database="tt",
        )
        texts = [(r["violation_title"] or "") + " " + (r["description"] or "")[:200] for r in rows]
        self._violation_ids = [r["id"] for r in rows]

        if texts:
            vectors = self._encode(texts)
            self._violation_index = faiss.IndexFlatIP(EMBEDDING_DIM)
            self._violation_index.add(vectors)

            with open(cache_file, "wb") as f:
                pickle.dump({
                    "index": faiss.serialize_index(self._violation_index),
                    "ids": self._violation_ids,
                }, f)

    def search_violations(self, query_text: str, top_k: int = 10) -> list[dict]:
        """语义搜索违规行为"""
        self.build_violation_index()
        if self._violation_index is None or not self._violation_ids:
            return []

        vec = self._encode([query_text])
        scores, indices = self._violation_index.search(vec, min(top_k, len(self._violation_ids)))

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._violation_ids):
                continue
            results.append({
                "id": self._violation_ids[idx],
                "similarity": round(float(score), 4),
            })

        if results:
            ids = [r["id"] for r in results]
            placeholders = ",".join(["%s"] * len(ids))
            rows = query(
                f"SELECT id, violation_title, severity, expression_text FROM audit_violations "
                f"WHERE id IN ({placeholders})",
                tuple(ids), database="tt",
            )
            title_map = {r["id"]: r for r in rows}
            for r in results:
                v = title_map.get(r["id"], {})
                r["violation_title"] = v.get("violation_title", "")
                r["severity"] = v.get("severity", "")
                r["expression_text"] = v.get("expression_text", "")

        return results


# ── 全局单例 ──
_store: Optional[VectorStore] = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        _store = VectorStore()
    return _store
