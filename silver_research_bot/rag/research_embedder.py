"""嵌入引擎 — 包装 LLM Provider 的 embed_batch，支持批处理、哈希缓存和 TF-IDF 回退"""

from __future__ import annotations

import hashlib
import math
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_TOKEN_RE = re.compile(r"[\w一-鿿]+")


class EmbeddingEngine:
    """嵌入引擎。优先使用 provider 的 embed_batch，不支持时回退 TF-IDF。"""

    def __init__(
        self,
        provider: "LLMProvider | None" = None,
        model: str = "text-embedding-3-small",
        dim: int = 1536,
    ):
        self.provider = provider
        self.model = model
        self.dim = dim
        self._cache: dict[str, list[float]] = {}
        self._use_provider = provider is not None

    @staticmethod
    def _content_hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

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
            idx = hash(token) % self.dim
            vec[idx] += val / norm
        return vec

    async def embed_single(self, text: str) -> list[float]:
        cached = self._cache.get(self._content_hash(text))
        if cached:
            return cached
        if self._use_provider:
            try:
                vec = await self.provider.embed(text, model=self.model)
                self._cache[self._content_hash(text)] = vec
                return vec
            except (NotImplementedError, RuntimeError):
                self._use_provider = False
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
                for (idx, _), vec in zip(uncached_texts, vecs):
                    results[idx] = vec
                    self._cache[self._content_hash(texts[idx])] = vec
                return [r for r in results if r is not None]  # type: ignore
            except (NotImplementedError, RuntimeError):
                self._use_provider = False

        for idx, text in uncached_texts:
            vec = self._tfidf_vector(text)
            results[idx] = vec
            self._cache[self._content_hash(text)] = vec
        return [r for r in results if r is not None]  # type: ignore
