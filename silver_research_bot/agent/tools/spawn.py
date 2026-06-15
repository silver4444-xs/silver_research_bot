"""Agent 用于在后台创建子代理的工具"""

from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from silver_research_bot.agent.subagent import SubagentManager


@tool_parameters(
    tool_parameters_schema(
        task=StringSchema("The task for the subagent to complete"),
        label=StringSchema("Optional short label for the task (for display)"),
        required=["task"],
    )
)
class SpawnTool(Tool):
    """ Agent 用于在后台创建子代理的工具。
    子代理可以独立执行耗时或复杂的任务，完成后向父 Agent 报告结果，从而提高系统的并发能力与任务隔离性。"""

    def __init__(self, manager: "SubagentManager"):
        self._manager = manager
        'SubagentManager 实例，负责实际创建和管理子代理进程'
        self._origin_channel: ContextVar[str] = ContextVar("spawn_origin_channel", default="cli")
        '父 Agent 当前所在的渠道（如 "cli", "feishu"）'
        self._origin_chat_id: ContextVar[str] = ContextVar("spawn_origin_chat_id", default="direct")
        '父 Agent 当前所在的对话 ID'
        self._session_key: ContextVar[str] = ContextVar("spawn_session_key", default="cli:direct")
        '用于中轮注入的会话键，通常为 "{channel}:{chat_id}"，在统一会话模式下可能不同'

    def set_context(self, channel: str, chat_id: str, effective_key: str | None = None) -> None:
        """子代理上下文注入方法"""
        self._origin_channel.set(channel)
        self._origin_chat_id.set(chat_id)
        self._session_key.set(effective_key or f"{channel}:{chat_id}")

    @property
    def name(self) -> str:
        return "spawn"

    @property
    def description(self) -> str:
        return (
            "Spawn a subagent to handle a task in the background. "
            "Use this for complex or time-consuming tasks that can run independently. "
            "The subagent will complete the task and report back when done. "
            "For deliverables or existing projects, inspect the workspace first "
            "and use a dedicated subdirectory when helpful."
        )

    async def execute(self, task: str, label: str | None = None, **kwargs: Any) -> str:
        """创建子代理执行给定任务"""
        return await self._manager.spawn(
            task=task,
            label=label,
            origin_channel=self._origin_channel.get(),
            origin_chat_id=self._origin_chat_id.get(),
            session_key=self._session_key.get(),
        )
