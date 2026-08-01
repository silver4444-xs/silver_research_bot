"""agent运行的共享生命周期钩子原语。"""

from __future__ import annotations

from typing import Any

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from silver_research_bot.providers.base import LLMResponse, ToolCallRequest


class AgentHookContext(BaseModel):
    """Mutable per-iteration state exposed to runner hooks."""

    model_config = ConfigDict(extra='forbid')
    iteration: int
    messages: list[dict[str, Any]]
    response: LLMResponse | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    tool_calls: list[ToolCallRequest] = Field(default_factory=list)
    tool_results: list[Any] = Field(default_factory=list)
    tool_events: list[dict[str, str]] = Field(default_factory=list)
    final_content: str | None = None
    stop_reason: str | None = None
    error: str | None = None


class AgentHook:
    """Minimal lifecycle surface for shared runner customization."""

    def __init__(self, reraise: bool = False) -> None:
        self._reraise = reraise

    def wants_streaming(self) -> bool:
        return False

    async def before_iteration(self, context: AgentHookContext) -> None:
        pass

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        pass

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        pass

    async def on_tool_start(self, context: AgentHookContext, tool_name: str, args_summary: str) -> None:
        """工具调用开始前触发。args_summary 是参数的简短摘要（≤120 字符）。"""
        pass

    async def on_tool_end(
        self, context: AgentHookContext,
        tool_name: str, ok: bool, summary: str, duration_ms: float,
    ) -> None:
        """工具调用完成后触发。summary 是结果的简短摘要（≤200 字符）。"""
        pass

    async def on_governance(self, context: AgentHookContext, detail: str) -> None:
        """上下文治理完成后触发。detail 描述做了什么（如"清理3条孤立结果"）。"""
        pass

    async def on_injection(self, context: AgentHookContext, count: int) -> None:
        """中轮消息注入发生后触发。"""
        pass

    async def on_error(self, context: AgentHookContext, detail: str) -> None:
        """LLM 调用或工具执行出错时触发。"""
        pass

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        pass

    async def after_iteration(self, context: AgentHookContext) -> None:
        pass

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return content


class CompositeHook(AgentHook):
    """Fan-out hook that delegates to an ordered list of hooks.

    Error isolation: async methods catch and log per-hook exceptions
    so a faulty custom hook cannot crash the agent loop.
    ``finalize_content`` is a pipeline (no isolation — bugs should surface).
    """

    __slots__ = ("_hooks",)

    def __init__(self, hooks: list[AgentHook]) -> None:
        super().__init__()
        self._hooks = list(hooks)

    def wants_streaming(self) -> bool:
        return any(h.wants_streaming() for h in self._hooks)

    async def _for_each_hook_safe(self, method_name: str, *args: Any, **kwargs: Any) -> None:
        for h in self._hooks:
            if getattr(h, "_reraise", False):
                await getattr(h, method_name)(*args, **kwargs)
                continue

            try:
                await getattr(h, method_name)(*args, **kwargs)
            except Exception:
                logger.exception("AgentHook.{} error in {}", method_name, type(h).__name__)

    async def before_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_iteration", context)

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        await self._for_each_hook_safe("on_stream", context, delta)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        await self._for_each_hook_safe("on_stream_end", context, resuming=resuming)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("before_execute_tools", context)

    async def after_iteration(self, context: AgentHookContext) -> None:
        await self._for_each_hook_safe("after_iteration", context)

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        for h in self._hooks:
            content = h.finalize_content(context, content)
        return content
