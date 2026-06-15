"""Heartbeat 服务 —— 心跳agent程序定期唤醒以检查任务。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Coroutine

from loguru import logger

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider

_HEARTBEAT_TOOL = [
    {
        "type": "function",
        "function": {
            "name": "heartbeat",
            "description": "Report heartbeat decision after reviewing tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["skip", "run"],
                        "description": "skip = nothing to do, run = has active tasks",
                    },
                    "tasks": {
                        "type": "string",
                        "description": "Natural-language summary of active tasks (required for run)",
                    },
                },
                "required": ["action"],
            },
        },
    }
]


class HeartbeatService:
    """
    周期性心跳服务，用于唤醒代理以检查任务。

    第 1 阶段（决策）：读取 HEARTBEAT.md 文件，并通过虚拟
    工具调用询问 LLM 是否存在活跃任务。这避免了自由文本解析
    以及不可靠的 HEARTBEAT_OK 令牌。

    第二阶段（执行）：仅在第一阶段返回 ``run`` 时触发。
    ``on_execute`` 回调会通过完整的代理循环运行任务，
    并将结果返回以供交付。
    """

    def __init__(self, workspace: Path, provider: LLMProvider, model: str,
                 on_execute: Callable[[str], Coroutine[Any, Any, str]] | None = None,
                 on_notify: Callable[[str], Coroutine[Any, Any, None]] | None = None, interval_s: int = 30 * 60,
                 enabled: bool = True, timezone: str | None = None):
        self.workspace = workspace
        '工作目录路径'
        self.provider = provider
        'LLM服务提供者实例'
        self.model = model
        '使用的模型名称'
        self.on_execute = on_execute
        '任务执行回调函数'
        self.on_notify = on_notify
        '通知回调函数'
        self.interval_s = interval_s
        '执行间隔时间，单位秒，默认30分钟'
        self.enabled = enabled
        '是否启用当前服务'
        self.timezone = timezone
        '时区配置'
        self._running = False
        '服务运行状态标记'
        self._task: asyncio.Task | None = None
        '异步任务实例'

    @property
    def heartbeat_file(self) -> Path:
        """返回 workspace / "HEARTBEAT.md" 的路径"""
        return self.workspace / "HEARTBEAT.md"

    def _read_heartbeat_file(self) -> str | None:
        """读取 HEARTBEAT.md 文件"""
        if self.heartbeat_file.exists():
            try:
                return self.heartbeat_file.read_text(encoding="utf-8")
            except Exception:
                return None
        return None

    async def _decide(self, content: str) -> tuple[str, str]:
        """Phase 1：通过虚拟工具调用，让 LLM 决定跳过或执行。

        返回 (action, tasks)，其中 action 为 ‘skip’ 或 ‘run’。
        """
        from silver_research_bot.utils.helpers import current_time_str

        response = await self.provider.chat_with_retry(
            messages=[
                {"role": "system", "content": "You are a heartbeat agent. Call the heartbeat tool to report your decision."},
                {"role": "user", "content": (
                    f"Current Time: {current_time_str(self.timezone)}\n\n"
                    "Review the following HEARTBEAT.md and decide whether there are active tasks.\n\n"
                    f"{content}"
                )},
            ],
            tools=_HEARTBEAT_TOOL,
            model=self.model,
        )

        if not response.should_execute_tools:
            if response.has_tool_calls:
                logger.warning(
                    "Ignoring heartbeat tool calls under finish_reason='{}'",
                    response.finish_reason,
                )
            return "skip", ""

        args = response.tool_calls[0].arguments
        return args.get("action", "skip"), args.get("tasks", "")

    async def start(self) -> None:
        """启动心跳服务"""
        if not self.enabled:
            logger.info("Heartbeat disabled")
            return
        if self._running:
            logger.warning("Heartbeat already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Heartbeat started (every {}s)", self.interval_s)

    def stop(self) -> None:
        """停止心跳服务."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run_loop(self) -> None:
        """主要 heartbeat 循环"""
        while self._running:
            try:
                await asyncio.sleep(self.interval_s)
                if self._running:
                    await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Heartbeat error: {}", e)

    async def _tick(self) -> None:
        """执行一次心跳计时。"""
        from silver_research_bot.utils.evaluator import evaluate_response

        content = self._read_heartbeat_file()
        if not content:
            logger.debug("Heartbeat: HEARTBEAT.md missing or empty")
            return

        logger.info("Heartbeat: checking for tasks...")

        try:
            action, tasks = await self._decide(content)

            if action != "run":
                logger.info("Heartbeat: OK (nothing to report)")
                return

            logger.info("Heartbeat: tasks found, executing...")
            if self.on_execute:
                response = await self.on_execute(tasks)

                if response:
                    should_notify = await evaluate_response(
                        response, tasks, self.provider, self.model,
                    )
                    if should_notify and self.on_notify:
                        logger.info("Heartbeat: completed, delivering response")
                        await self.on_notify(response)
                    else:
                        logger.info("Heartbeat: silenced by post-run evaluation")
        except Exception:
            logger.exception("Heartbeat execution failed")

    async def trigger_now(self) -> str | None:
        """手动触发心跳"""
        content = self._read_heartbeat_file()
        if not content:
            return None
        action, tasks = await self._decide(content)
        if action != "run" or not self.on_execute:
            return None
        return await self.on_execute(tasks)
