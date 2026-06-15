"""记忆重要性评分引擎 — 使用轻量 LLM 评分记忆条目的长期价值"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

SCORE_PROMPT = """评估以下信息的长期重要性，从 1 到 10 打分：
- 1-3: 临时/瞬间信息（闲聊、当前任务细节）
- 4-6: 上下文相关（当前会话有用的背景）
- 7-9: 重要知识（用户偏好、项目约定、关键决策）
- 10: 关键长期知识（身份信息、核心约束、不可替代的事实）

信息: {text}

仅输出数字分数（1-10），不要解释。"""


class MemoryScorer:
    """使用 LLM 评估记忆重要性。支持批量评分。"""

    def __init__(self, provider: "LLMProvider", model: str | None = None):
        self.provider = provider
        self.model = model

    async def score(self, text: str) -> int:
        scores = await self.score_batch([text])
        return scores[0] if scores else 1

    async def score_batch(self, entries: list[str]) -> list[int]:
        if not entries:
            return []
        results: list[int] = []
        for text in entries:
            try:
                response = await self.provider.chat_with_retry(
                    model=self.model,
                    messages=[{"role": "user", "content": SCORE_PROMPT.format(text=text[:800])}],
                    tools=None,
                    max_tokens=10,
                    temperature=0.0,
                )
                results.append(self._parse_score(response.content or ""))
            except Exception:
                results.append(5)
        return results

    @staticmethod
    def _parse_score(content: str) -> int:
        m = re.search(r"\b(10|[1-9])\b", content.strip())
        if m:
            return max(1, min(10, int(m.group(1))))
        return 5
