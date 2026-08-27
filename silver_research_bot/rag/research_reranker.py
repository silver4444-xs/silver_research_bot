"""LLM 重排序器 — 使用现有 LLM Provider 做 Cross-Encoder 相关性打分"""

from __future__ import annotations

import hashlib
import re
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

RERANK_PROMPT = """你是一个文档相关性评分器。给定一个查询和一组候选文档，对每篇文档与查询的相关性从 1 到 10 打分。

查询: {query}

候选文档:
{docs}

请按以下格式输出每篇文档的分数（每行一个）：
<文档编号>:<分数>

只输出分数列表，不要添加任何解释或额外文字。"""


class LLMReranker:
    """使用 LLM 对候选文档进行重排序，带查询级 TTL 缓存。"""

    def __init__(
        self,
        provider: "LLMProvider",
        model: str | None = None,
        ttl_seconds: int = 3600,
        max_entries: int = 512,
        max_tokens: int = 512,
        candidate_max_chars: int = 1000,
    ):
        self.provider = provider
        self.model = model
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_tokens = max_tokens
        self.candidate_max_chars = candidate_max_chars
        # 查询级缓存：key → (过期单调时间, [(cid, score)])，缓存全量排序结果，命中时按 top_k 切片
        self._cache: dict[str, tuple[float, list[tuple[str, float]]]] = {}

    @staticmethod
    def _cache_key(query: str, cids: list[str]) -> str:
        """与候选顺序解耦的缓存 key（排序后拼接）。"""
        raw = query + "\x00" + "\x00".join(sorted(cids))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_k: int = 5
    ) -> list[tuple[str, float]] | None:
        """返回重排序 [(cid, score)]；失败或无需重排时返回 None（调用方维持原融合序）。"""
        if len(candidates) <= top_k:
            return None

        key = self._cache_key(query, [cid for cid, _ in candidates])
        if self.ttl_seconds > 0:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > time.monotonic():
                return cached[1][:top_k]

        docs_text = "\n\n".join(
            f"<{i}>{text[:self.candidate_max_chars]}</{i}>" for i, (_, text) in enumerate(candidates)
        )
        prompt = RERANK_PROMPT.format(query=query[:800], docs=docs_text)

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=self.max_tokens,
                temperature=0.0,
            )
        except Exception as e:
            logger.warning("RAG: reranker 调用失败，维持融合序: {}", e)
            return None

        scores = self._parse_scores(response.content or "", len(candidates))
        if not scores:
            logger.warning("RAG: reranker 解析无结果，维持融合序")
            return None

        # 排序修复：无分候选分数记 0.0 沉底，永不压过真实 1-10 分（原 scores.get(i, 5.0) 会把缺分候选误排进前 k）
        results = [(cid, scores.get(i, 0.0)) for i, (cid, _) in enumerate(candidates)]
        results.sort(key=lambda x: x[1], reverse=True)

        if self.ttl_seconds > 0:
            self._cache[key] = (time.monotonic() + self.ttl_seconds, results)
            # 简单容量裁剪：按插入序淘汰最旧
            while len(self._cache) > self.max_entries:
                self._cache.pop(next(iter(self._cache)))
        return results[:top_k]

    @staticmethod
    def _parse_scores(content: str, num_candidates: int) -> dict[int, float]:
        scores: dict[int, float] = {}
        for line in content.strip().split("\n"):
            m = re.match(r"<?(\d+)>?\s*[:：]\s*(\d+(?:\.\d+)?)", line.strip())
            if m:
                idx = int(m.group(1))
                score = float(m.group(2))
                scores[idx] = max(1.0, min(10.0, score))
        return scores
