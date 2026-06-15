"""用于解耦通道与代理之间通信的消息总线模块"""

from silver_research_bot.bus.events import InboundMessage, OutboundMessage
from silver_research_bot.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
