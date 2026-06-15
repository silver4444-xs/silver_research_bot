"""Agent 用来主动向用户发送消息的工具"""

from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import ArraySchema, StringSchema, tool_parameters_schema
from silver_research_bot.bus.events import OutboundMessage


@tool_parameters(
    tool_parameters_schema(
        content=StringSchema("The message content to send"),
        channel=StringSchema("Optional: target channel (telegram, discord, etc.)"),
        chat_id=StringSchema("Optional: target chat/user ID"),
        media=ArraySchema(
            StringSchema(""),
            description="Optional: list of file paths to attach (images, audio, documents)",
        ),
        required=["content"],
    )
)
class MessageTool(Tool):
    """允许 Agent 在对话过程中主动向用户发送消息的 Tool"""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = "",
        default_message_id: str | None = None,
    ):
        self._send_callback = send_callback
        '实际发布 OutboundMessage 到消息总线的回调函数（通常绑定到 MessageBus.publish_outbound）'
        self._default_channel: ContextVar[str] = ContextVar("message_default_channel", default=default_channel)
        '存储当前会话的默认消息渠道（如 "cli", "feishu" 等）'
        self._default_chat_id: ContextVar[str] = ContextVar("message_default_chat_id", default=default_chat_id)
        '存储当前会话的默认对话 ID（用户 ID 或群聊 ID）'
        self._default_message_id: ContextVar[str | None] = ContextVar(
            "message_default_message_id",
            default=default_message_id,
        )
        '存储当前正在处理的消息的 ID（在某些渠道中用于回复引用）'
        self._sent_in_turn_var: ContextVar[bool] = ContextVar("message_sent_in_turn", default=False)
        '记录在当前对话轮次中是否已经通过该工具发送过消息'

    def set_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """设置当前消息上下文"""
        self._default_channel.set(channel)
        self._default_chat_id.set(chat_id)
        self._default_message_id.set(message_id)

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """设置发送信息的回调函数."""
        self._send_callback = callback

    def start_turn(self) -> None:
        """在 AgentLoop._process_message 中每个新消息处理开始时调用，重置标志."""
        self._sent_in_turn = False

    @property
    def _sent_in_turn(self) -> bool:
        return self._sent_in_turn_var.get()

    @_sent_in_turn.setter
    def _sent_in_turn(self, value: bool) -> None:
        self._sent_in_turn_var.set(value)

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return (
            "Send a message to the user, optionally with file attachments. "
            "This is the ONLY way to deliver files (images, documents, audio, video) to the user. "
            "Use the 'media' parameter with file paths to attach files. "
            "Do NOT use read_file to send files — that only reads content for your own analysis."
        )

    async def execute(
        self,
        content: str,
        channel: str | None = None,
        chat_id: str | None = None,
        message_id: str | None = None,
        media: list[str] | None = None,
        **kwargs: Any
    ) -> str:

        from silver_research_bot.utils.helpers import strip_think
        '''1. 参数与默认值解析'''
        content = strip_think(content)

        default_channel = self._default_channel.get()
        default_chat_id = self._default_chat_id.get()

        channel = channel or default_channel
        chat_id = chat_id or default_chat_id
        # Only inherit default message_id when targeting the same channel+chat.
        # Cross-chat sends must not carry the original message_id, because
        # some channels (e.g. Feishu) use it to determine the target
        # conversation via their Reply API, which would route the message
        # to the wrong chat entirely.
        '''
        2 处理 message_id 的继承规则
        只有当目标渠道和对话 ID 与默认值完全相同时，才继承原始消息的 message_id。
        如果发送到其他渠道/对话，必须清空 message_id，否则某些渠道（如飞书）会将其视为回复原始消息，导致路由错误。
        '''
        if channel == default_channel and chat_id == default_chat_id:
            message_id = message_id or self._default_message_id.get()
        else:
            message_id = None

        '''3 参数校验'''
        if not channel or not chat_id:
            return "Error: No target channel/chat specified"

        if not self._send_callback:
            return "Error: Message sending not configured"

        '''4 构造并发送 OutboundMessage'''
        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            media=media or [],
            metadata={
                "message_id": message_id,
            } if message_id else {},
        )

        '''5 更新状态与返回结果'''
        try:
            await self._send_callback(msg)
            if channel == default_channel and chat_id == default_chat_id:
                self._sent_in_turn = True
            media_info = f" with {len(media)} attachments" if media else ""
            return f"Message sent to {channel}:{chat_id}{media_info}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
