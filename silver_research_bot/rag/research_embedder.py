"""嵌入引擎 — 包装 LLM Provider 的 embed_batch，支持批处理、哈希缓存和 TF-IDF 回退"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING

from filelock import FileLock
from loguru import logger

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_TOKEN_RE = re.compile(r"[\w一-鿿]+")


class EmbeddingEngine:
    """嵌入引擎。优先使用 provider 的 embed_batch，不支持时回退 TF-IDF。

    可选磁盘缓存 cache_path：持久化文档向量，重启后 reindex 无需重嵌未变 chunk。
    结构 {"model", "dim", "vectors": {sha256[:32]: [float, ...]}}；model/dim 不符或 JSON
    损坏时备份 .bak 后丢弃（镜像 ResearchRAG._load_index 恢复策略）。
    """

    def __init__(
        self,
        provider: "LLMProvider | None" = None,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
        cache_path: Path | None = None,
    ):
        self.provider = provider
        self.model = model
        self.dim = dim
        self.cache_path = cache_path
        self._cache: dict[str, list[float]] = {}
        self._use_provider = provider is not None
        self._load_cache()

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def _load_cache(self) -> None:
        """从磁盘加载向量缓存；model/dim 不符或损坏时备份后丢弃。"""
        if self.cache_path is None:
            return
        if not self.cache_path.exists():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            logger.warning("RAG: 嵌入磁盘缓存损坏，备份后重建: {} ({})", self.cache_path, exc)
            self._backup_cache()
            return
        if (data.get("model") != self.model) or (data.get("dim") != self.dim):
            logger.warning(
                "RAG: 嵌入缓存 model/dim 不符 (缓存 {}:{} vs 期望 {}:{})，备份后丢弃",
                data.get("model"), data.get("dim"), self.model, self.dim,
            )
            self._backup_cache()
            return
        vectors = data.get("vectors") or {}
        for h, vec in vectors.items():
            if isinstance(h, str) and isinstance(vec, list) and len(vec) == self.dim:
                self._cache[h] = [float(x) for x in vec]

    def _backup_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.replace(self.cache_path.with_suffix(self.cache_path.suffix + ".bak"))
        except OSError:
            pass

    def _save_cache(self) -> None:
        """原子写回磁盘缓存（FileLock + tmp + os.replace）。"""
        if self.cache_path is None:
            return
        payload = {"model": self.model, "dim": self.dim, "vectors": self._cache}
        try:
            with FileLock(str(self.cache_path) + ".lock"):
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
                tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                os.replace(tmp, self.cache_path)
        except OSError as exc:
            logger.warning("RAG: 嵌入磁盘缓存写回失败: {} ({})", self.cache_path, exc)

    def _flip_to_tfidf(self) -> None:
        """provider 维度不符/异常回退 TF-IDF：清空内存缓存避免混存旧 provider 向量，重写磁盘为空。"""
        self._use_provider = False
        self._cache = {}
        self._save_cache()

    def _tfidf_vector(self, text: str) -> list[float]:
        tokens = _TOKEN_RE.findall(text.lower())
        if not tokens:
            return [0.0] * self.dim
        counts: dict[str, float] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0.0) + 1.0
        norm = math.sqrt(sum(v * v for v in counts.values())) or 1.0
        vec = [0.0] * self.dim
        for token, val in counts.items():
            # 稳定哈希（md5），避免 Python 进程间 hash(str) 随机化导致向量跨重启不一致
            idx = int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[idx] += val / norm
        return vec

    async def embed_single(self, text: str) -> list[float]:
        cached = self._cache.get(self._content_hash(text))
        if cached:
            return cached
        if self._use_provider:
            try:
                vec = await self.provider.embed(text, model=self.model)
                if len(vec) != self.dim:
                    logger.warning(
                        "RAG: provider 嵌入维度不符 (期望 {}, 实际 {})，改用 TF-IDF 回退",
                        self.dim, len(vec),
                    )
                    self._flip_to_tfidf()
                else:
                    self._cache[self._content_hash(text)] = vec
                    return vec
            except (NotImplementedError, RuntimeError):
                self._flip_to_tfidf()
        return self._tfidf_vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        uncached_texts: list[tuple[int, str]] = []
        results: list[list[float] | None] = [None] * len(texts)
        for i, text in enumerate(texts):
            cached = self._cache.get(self._content_hash(text))
            if cached:
                results[i] = cached
            else:
                uncached_texts.append((i, text))
        if not uncached_texts:
            return [r for r in results if r is not None]  # type: ignore

        if self._use_provider:
            try:
                raw = [t for _, t in uncached_texts]
                vecs = await self.provider.embed_batch(raw, model=self.model)
                if vecs and any(len(v) != self.dim for v in vecs):
                    logger.warning(
                        "RAG: provider 批量嵌入维度不符 (期望 {}, 实际 {})，改用 TF-IDF 回退",
                        self.dim, len(vecs[0]),
                    )
                    self._flip_to_tfidf()
                else:
                    for (idx, _), vec in zip(uncached_texts, vecs):
                        results[idx] = vec
                        self._cache[self._content_hash(texts[idx])] = vec
                    await asyncio.to_thread(self._save_cache)
                    return [r for r in results if r is not None]  # type: ignore
            except (NotImplementedError, RuntimeError):
                self._flip_to_tfidf()

        for idx, text in uncached_texts:
            vec = self._tfidf_vector(text)
            results[idx] = vec
            self._cache[self._content_hash(text)] = vec
        await asyncio.to_thread(self._save_cache)
        return [r for r in results if r is not None]  # type: ignore
