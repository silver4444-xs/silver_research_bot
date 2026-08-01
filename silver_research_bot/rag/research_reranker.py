"""LLM 重排序器 — 使用现有 LLM Provider 做 Cross-Encoder 相关性打分"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

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
    """使用 LLM 对候选文档进行重排序。"""

    def __init__(self, provider: "LLMProvider", model: str | None = None):
        self.provider = provider
        self.model = model

    async def rerank(
        self, query: str, candidates: list[tuple[str, str]], top_k: int = 5
    ) -> list[tuple[str, float]]:
        if len(candidates) <= top_k:
            return [(cid, 10.0) for cid, _ in candidates]

        docs_text = "\n\n".join(
            f"<{i}>{text[:500]}</{i}>" for i, (_, text) in enumerate(candidates)
        )
        prompt = RERANK_PROMPT.format(query=query[:500], docs=docs_text)

        try:
            response = await self.provider.chat_with_retry(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                tools=None,
                max_tokens=200,
                temperature=0.0,
            )
        except Exception:
            return [(cid, 10.0) for cid, _ in candidates[:top_k]]

        scores = self._parse_scores(response.content or "", len(candidates))
        results = [(cid, scores.get(i, 5.0)) for i, (cid, _) in enumerate(candidates)]
        results.sort(key=lambda x: x[1], reverse=True)
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
