"""Agent 间通信总线 — 消息路由、广播、审查请求、任务转交"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field


class AgentMessage(BaseModel):
    """Agent 间消息"""
    msg_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    from_agent: str = ""
    to_agent: str = ""
    type: str = "notify"
    subject: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    in_reply_to: str | None = None
    timestamp: float = Field(default_factory=time.monotonic)


class AgentCommBus:
    """Agent 间消息路由器，管理 per-task pending_queue 注册和消息投递。"""

    def __init__(self) -> None:
        self._task_queues: dict[str, asyncio.Queue[AgentMessage]] = {}
        self._message_log: dict[str, AgentMessage] = {}

    def register(self, task_id: str) -> asyncio.Queue[AgentMessage]:
        queue: asyncio.Queue[AgentMessage] = asyncio.Queue(maxsize=20)
        self._task_queues[task_id] = queue
        logger.debug("AgentCommBus registered queue for task {}", task_id)
        return queue

    def unregister(self, task_id: str) -> None:
        self._task_queues.pop(task_id, None)

    async def send(self, msg: AgentMessage) -> int:
        """发送消息到目标 Agent。返回成功投递数。"""
        self._message_log[msg.msg_id] = msg

        if msg.to_agent == "all":
            count = 0
            for tid, queue in list(self._task_queues.items()):
                if tid == msg.from_agent:
                    continue
                try:
                    queue.put_nowait(msg)
                    count += 1
                except asyncio.QueueFull:
                    logger.warning("Queue full for {}, dropping broadcast", tid)
            return count

        queue = self._task_queues.get(msg.to_agent)
        if queue is None:
            logger.warning("No queue for target agent {}", msg.to_agent)
            return 0
        try:
            queue.put_nowait(msg)
            return 1
        except asyncio.QueueFull:
            logger.warning("Queue full for {}, dropping message", msg.to_agent)
            return 0

    def get_queue(self, task_id: str) -> asyncio.Queue[AgentMessage] | None:
        return self._task_queues.get(task_id)

    @property
    def active_tasks(self) -> list[str]:
        return list(self._task_queues.keys())
