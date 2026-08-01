"""共享工作区 Blackboard — 多 Agent 持久化产物存储 + 提案-审查-采纳共识流程"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field


class BlackboardEntry(BaseModel):
    """Blackboard 中的单条记录"""
    key: str
    value: Any
    author: str = ""
    version: int = 1
    status: str = "draft"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Blackboard:
    """多 Agent 共享工作区。write/read/propose/accept/reject + JSON 持久化。"""

    def __init__(self, workspace: str | Path) -> None:
        self._workspace = Path(workspace)
        self._entries: dict[str, BlackboardEntry] = {}
        self._lock: asyncio.Lock | None = None
        self._file = self._workspace / "blackboard.json"

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock


    async def write(
        self, key: str, value: Any, author: str = "", metadata: dict[str, Any] | None = None,
    ) -> BlackboardEntry:
        async with self._get_lock():
            existing = self._entries.get(key)
            now = time.time()
            if existing:
                existing.value = value
                existing.author = author
                existing.version += 1
                existing.updated_at = now
                existing.status = "draft"
                if metadata:
                    existing.metadata.update(metadata)
                entry = existing
            else:
                entry = BlackboardEntry(
                    key=key, value=value, author=author,
                    created_at=now, updated_at=now,
                    metadata=metadata or {},
                )
                self._entries[key] = entry
            logger.debug("Blackboard write: {} v{} by {}", key, entry.version, author)
            return entry

    async def read(self, key: str) -> BlackboardEntry | None:
        return self._entries.get(key)

    async def list_entries(self, status_filter: str | None = None) -> list[BlackboardEntry]:
        entries = list(self._entries.values())
        if status_filter:
            entries = [e for e in entries if e.status == status_filter]
        return sorted(entries, key=lambda e: e.updated_at, reverse=True)

    async def propose(self, key: str, value: Any, author: str = "") -> BlackboardEntry:
        async with self._get_lock():
            existing = self._entries.get(key)
            now = time.time()
            if existing:
                existing.value = value
                existing.author = author
                existing.version += 1
                existing.updated_at = now
                existing.status = "proposed"
                entry = existing
            else:
                entry = BlackboardEntry(
                    key=key, value=value, author=author,
                    status="proposed", created_at=now, updated_at=now,
                )
                self._entries[key] = entry
            logger.info("Blackboard propose: {} v{} by {}", key, entry.version, author)
            return entry

    async def accept(self, key: str, reviewer: str = "") -> BlackboardEntry | None:
        async with self._get_lock():
            entry = self._entries.get(key)
            if entry is None or entry.status != "proposed":
                return entry
            entry.status = "accepted"
            entry.version += 1
            entry.updated_at = time.time()
            if reviewer:
                entry.metadata["reviewed_by"] = reviewer
            logger.info("Blackboard accept: {} by {}", key, reviewer)
            return entry

    async def reject(self, key: str, reviewer: str = "", reason: str = "") -> BlackboardEntry | None:
        async with self._get_lock():
            entry = self._entries.get(key)
            if entry is None or entry.status != "proposed":
                return entry
            entry.status = "rejected"
            entry.version += 1
            entry.updated_at = time.time()
            if reviewer:
                entry.metadata["reviewed_by"] = reviewer
            if reason:
                entry.metadata["rejection_reason"] = reason
            logger.info("Blackboard reject: {} by {} ({})", key, reviewer, reason)
            return entry

    async def save(self) -> None:
        async with self._get_lock():
            data = {"entries": {k: e.model_dump() for k, e in self._entries.items()}}
            tmp = self._file.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self._file)
            logger.debug("Blackboard saved: {} entries", len(self._entries))

    async def load(self) -> None:
        async with self._get_lock():
            if not self._file.exists():
                return
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                for k, v in data.get("entries", {}).items():
                    self._entries[k] = BlackboardEntry(**v)
                logger.info("Blackboard loaded: {} entries", len(self._entries))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Blackboard load failed: {}", e)

    async def promote_to_memory(self, key: str, memory_store: Any) -> str | None:
        """将 accepted 条目提升到长期 Memory"""
        entry = self._entries.get(key)
        if entry is None or entry.status != "accepted":
            return None
        val_str = json.dumps(entry.value, ensure_ascii=False) if not isinstance(entry.value, str) else entry.value
        text = f"[{key}] (by {entry.author}): {val_str[:500]}"
        memory_store.append_history({
            "cursor": -1, "timestamp": time.strftime("%Y-%m-%d %H:%M"), "content": text,
        })
        logger.info("Blackboard promoted {} to memory", key)
        return text

    @property
    def entry_count(self) -> int:
        return len(self._entries)
