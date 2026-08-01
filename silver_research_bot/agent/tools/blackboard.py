"""Blackboard 工具 — Agent 读写共享工作区"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from silver_research_bot.agent.blackboard import Blackboard


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema('Operation: "write", "read", "propose", "accept", "reject", or "list"'),
        key=StringSchema("Entry key (required for write/read/propose/accept/reject)"),
        value=StringSchema("Value to write/propose (JSON string for complex values)"),
        reason=StringSchema("Reason for rejection (only for reject action)"),
        status_filter=StringSchema("Filter by status: draft, proposed, accepted, rejected (only for list action)"),
        required=["action"],
    )
)
class BlackboardTool(Tool):
    """Read, write, propose, and manage entries on the shared team Blackboard."""

    def __init__(self, blackboard: "Blackboard") -> None:
        self._bb = blackboard
        self._event_emitter: "Callable[[str], Awaitable[None]] | None" = None

    @property
    def name(self) -> str:
        return "blackboard"

    @property
    def description(self) -> str:
        return (
            "Read and write to the shared team Blackboard. Operations:\n"
            "- write(key, value): Save a draft entry.\n"
            "- read(key): Read an entry.\n"
            "- propose(key, value): Propose a value for team consensus.\n"
            "- accept(key): Accept a proposed entry.\n"
            "- reject(key, reason): Reject a proposed entry with a reason.\n"
            "- list(status_filter): List entries, optionally filtered by status.\n"
            "Use to share findings, coordinate decisions, and build consensus across agents."
        )

    @property
    def read_only(self) -> bool:
        return False

    def set_event_emitter(self, emitter: "Callable[[str], Awaitable[None]]") -> None:
        self._event_emitter = emitter

    async def execute(
        self, action: str, key: str = "", value: str = "",
        reason: str = "", status_filter: str = "", **kwargs: Any,
    ) -> str:
        act = action.strip().lower()

        async def _emit(evt: dict) -> None:
            if self._event_emitter:
                await self._event_emitter(json.dumps(evt, ensure_ascii=False))

        try:
            if act == "write":
                parsed = self._parse_value(value)
                entry = await self._bb.write(key, parsed, author="agent")
                await _emit({"type": "blackboard_update", "key": key, "status": entry.status, "version": entry.version})
                return f"Written: {key} (v{entry.version})"

            elif act == "read":
                entry = await self._bb.read(key)
                if entry is None:
                    return f"Key '{key}' not found."
                return json.dumps({
                    "key": entry.key, "value": entry.value, "author": entry.author,
                    "version": entry.version, "status": entry.status,
                    "updated_at": entry.updated_at,
                }, ensure_ascii=False, indent=2)

            elif act == "propose":
                parsed = self._parse_value(value)
                entry = await self._bb.propose(key, parsed, author="agent")
                await _emit({"type": "blackboard_update", "key": key, "status": "proposed", "version": entry.version})
                return f"Proposed: {key} (v{entry.version}). Awaiting review."

            elif act == "accept":
                entry = await self._bb.accept(key, reviewer="agent")
                if entry is None:
                    return f"Key '{key}' not found."
                await _emit({"type": "blackboard_update", "key": key, "status": "accepted"})
                return f"Accepted: {key} (v{entry.version})"

            elif act == "reject":
                entry = await self._bb.reject(key, reviewer="agent", reason=reason)
                if entry is None:
                    return f"Key '{key}' not found."
                await _emit({"type": "blackboard_update", "key": key, "status": "rejected", "reason": reason})
                return f"Rejected: {key} — {reason}" if reason else f"Rejected: {key}"

            elif act == "list":
                sf = status_filter.strip().lower() if status_filter else None
                entries = await self._bb.list_entries(status_filter=sf)
                result = [{
                    "key": e.key, "status": e.status, "author": e.author,
                    "version": e.version, "value_preview": str(e.value)[:200],
                } for e in entries]
                return json.dumps(result, ensure_ascii=False, indent=2)

            else:
                return f"Unknown action: {action}. Valid: write, read, propose, accept, reject, list."

        except Exception as e:
            return f"Error: {e}"

    @staticmethod
    def _parse_value(raw: str) -> Any:
        if not raw:
            return ""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
