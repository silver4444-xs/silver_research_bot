"""定义了两种消息类型，用于在消息总线（message bus）中传递入站和出站消息"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class InboundMessage:
    """
    入站消息
    表示从外部聊天渠道（如 Telegram、Discord、Slack、WhatsApp）接收到的消息
    """
    channel: str  # telegram, discord, slack, whatsapp
    '消息来源渠道，例如 "telegram"、"discord" 等'
    sender_id: str  # User identifier
    '发送者的用户标识（渠道内的唯一 ID）'
    chat_id: str  # Chat/channel identifier
    '聊天或频道标识（用于确定消息所属的对话上下文）'
    content: str  # Message text
    '消息文本内容'
    timestamp: datetime = field(default_factory=datetime.now)
    '消息接收时间，默认值为当前时间（datetime.now()）'
    media: list[str] = field(default_factory=list)  # Media URLs
    '附带的媒体文件 URL 列表，默认为空列表'
    metadata: dict[str, Any] = field(default_factory=dict)  # Channel-specific data
    '渠道特定的额外元数据（如消息 ID、回复引用等），默认为空字典'
    session_key_override: str | None = None  # Optional override for thread-scoped sessions
    '可选的重写键，用于线程作用域会话标识，默认为 None'

    @property
    def session_key(self) -> str:
        """
        生成一个唯一的会话标识键，用于区分不同的对话会话
        逻辑：如果提供了 session_key_override 则返回该值；否则返回 f"{self.channel}:{self.chat_id}"。
        用途：允许同一个渠道内不同的 chat_id 拥有独立的会话（例如群聊中的不同子对话），或通过重写键实现更细粒度的线程/子会话隔离
        """
        return self.session_key_override or f"{self.channel}:{self.chat_id}"


@dataclass
class OutboundMessage:
    """
    出站消息
    表示需要发送到某个聊天渠道的消息
    """

    channel: str
    '目标渠道（如 "telegram"）'
    chat_id: str
    '目标聊天或频道标识'
    content: str
    '要发送的消息文本'
    reply_to: str | None = None
    '可选，回复哪条消息的标识符（如消息 ID），默认 None'
    media: list[str] = field(default_factory=list)
    '要发送的媒体文件 URL 列表'
    metadata: dict[str, Any] = field(default_factory=dict)
    '额外的渠道特定元数据（如消息格式选项、标记等）'


