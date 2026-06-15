"""主动记忆检索 — 每轮对话前从长期记忆中检索相关内容注入上下文"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from silver_research_bot.agent.memory import MemoryEntry, MemoryStore

META_RE = re.compile(r"<!--\s*uid:(\S+)\s+imp:(\d+)\s+ts:(\S+)\s+acc:(\S+)\s*-->")


class ActiveMemoryRetriever:
    """使用嵌入引擎从 MEMORY.md 检索与当前对话相关的长期记忆。"""

    def __init__(
        self,
        memory_store: "MemoryStore",
        embedder: Any | None = None,
        config: Any | None = None,
    ):
        self.memory_store = memory_store
        self.embedder = embedder
        self.top_k = getattr(config, "active_retrieval_top_k", 3) if config else 3
        self.enabled = getattr(config, "active_retrieval_enabled", True) if config else True

    async def retrieve(self, query: str) -> list["MemoryEntry"]:
        if not self.enabled or not self.embedder:
            return []
        entries = self.memory_store.parse_memory_entries()
        if not entries:
            return []
        query_vec = await self.embedder.embed_single(query)
        scored = []
        for entry in entries:
            entry_vec = await self.embedder.embed_single(entry.text)
            sim = self._cosine(query_vec, entry_vec)
            combined = sim * (0.5 + 0.5 * entry.importance / 10.0)
            scored.append((entry, combined))
        scored.sort(key=lambda x: x[1], reverse=True)
        result = [e for e, s in scored[:self.top_k] if s > 0.2]
        for entry in result:
            self.memory_store.touch_entry(entry.uid)
        return result

    def build_context_block(self, entries: list["MemoryEntry"]) -> str:
        if not entries:
            return ""
        lines = ["## Active Memory (相关长期记忆)"]
        for i, entry in enumerate(entries, 1):
            lines.append(f"{i}. {entry.text[:300]}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(y * y for y in b)) + 1e-10
        return dot / (na * nb)
