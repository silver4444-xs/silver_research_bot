"""
异步消息总线，
用于解耦聊天渠道（如 Telegram、Discord）和代理核心（Agent Core）
它基于 asyncio.Queue 提供两个独立的消息队列：入站队列和出站队列
"""

import asyncio

from silver_research_bot.bus.events import InboundMessage, OutboundMessage


class MessageBus:
    """
    一种异步消息总线，用于将聊天频道与代理核心解耦。

    频道将消息推送到入站队列，
    代理处理这些消息，并将响应推送到出站队列。
    """

    def __init__(self):
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        '存放 InboundMessage 对象，代表从渠道收到的原始消息'
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        '存放 OutboundMessage 对象，代表代理需要回复给渠道的消息'

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """将收到的消息放入入站队列。异步操作，如果队列满则等待"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """从入站队列取出一条消息。如果没有消息则阻塞，直到有消息可用"""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """将代理生成的回复消息放入出站队列"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """从出站队列取出待发送的消息，阻塞直到有消息"""
        return await self.outbound.get()

    @property
    def inbound_size(self) -> int:
        """返回入站队列中待处理的消息数量"""
        return self.inbound.qsize()

    @property
    def outbound_size(self) -> int:
        """返回出站队列中待发送的消息数量"""
        return self.outbound.qsize()
