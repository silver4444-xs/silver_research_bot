"""聊天平台的底层通道接口"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from loguru import logger

from silver_research_bot.bus.events import InboundMessage, OutboundMessage
from silver_research_bot.bus.queue import MessageBus


class BaseChannel(ABC):
    """
    用于实现聊天频道的抽象基类。

    每个频道（Telegram、Discord 等）都应实现此接口，
    以便与 silver_research_bot 消息总线集成。
    """

    name: str = "base"
    '通道唯一标识名称，默认base'
    display_name: str = "Base"
    '界面显示的通道名称，默认Base'
    transcription_provider: str = "groq"
    '语音转文字服务提供商，默认groq'
    transcription_api_key: str = ""
    '语音转文字服务的API密钥'
    transcription_api_base: str = ""
    '语音转文字服务的API地址'

    def __init__(self, config: Any, bus: MessageBus):
        """
        初始化通道。

        参数：
            config：通道的特定配置。
            bus：用于通信的消息总线。
        """
        self.config = config
        '通道的特定配置'
        self.bus = bus
        '用于通信的消息总线'
        self._running = False
        '内部运行状态标志'

    async def transcribe_audio(self, file_path: str | Path) -> str:
        """通过 Whisper（OpenAI 或 Groq）转录音频文件。若操作失败，则返回空字符串。"""
        if not self.transcription_api_key:
            return ""
        try:
            if self.transcription_provider == "openai":
                from silver_research_bot.providers.transcription import OpenAITranscriptionProvider
                provider = OpenAITranscriptionProvider(
                    api_key=self.transcription_api_key,
                    api_base=self.transcription_api_base or None,
                )
            else:
                from silver_research_bot.providers.transcription import GroqTranscriptionProvider
                provider = GroqTranscriptionProvider(
                    api_key=self.transcription_api_key,
                    api_base=self.transcription_api_base or None,
                )
            return await provider.transcribe(file_path)
        except Exception as e:
            logger.warning("{}: audio transcription failed: {}", self.name, e)
            return ""

    async def login(self, force: bool = False) -> bool:
        """
        执行特定通道的交互式登录（例如扫描二维码）。

        参数：
            force：如果为 True，则忽略现有凭据并强制重新认证。

        如果已通过认证或登录成功，则返回 True。
        在支持交互式登录的子类中重写此方法。
        """
        return True

    @abstractmethod
    async def start(self) -> None:
        """
        启动频道并开始监听消息。

        这应该是一个长期运行的异步任务，其功能包括：
        1. 连接到聊天平台
        2. 监听传入的消息
        3. 通过 _handle_message() 函数将消息转发至消息总线
        """
        pass

    @abstractmethod
    async def stop(self) -> None:
        """停止该通道并清理资源。"""
        pass

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """
        通过此通道发送消息。

        参数：
            msg：要发送的消息。

        实现应在使用过程中抛出异常，以便通道管理器
        能够集中应用任何重试策略。
        """
        pass

    async def send_delta(self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None) -> None:
        """发送一个流式文本块。

        子类应重写此方法以启用流式传输。实现应
        在发送失败时抛出异常，以便通道管理器进行重试。

        流式传输契约：``_stream_delta`` 是一个数据块，``_stream_end`` 结束
        当前分段，且带状态的实现必须使用
        ``_stream_id`` 作为缓冲区的键，而不能仅使用 ``chat_id``。
        """
        pass

    @property
    def supports_streaming(self) -> bool:
        """当配置启用了流式传输，且该子类实现了 send_delta 方法时，此条件成立。"""
        cfg = self.config
        streaming = cfg.get("streaming", False) if isinstance(cfg, dict) else getattr(cfg, "streaming", False)
        return bool(streaming) and type(self).send_delta is not BaseChannel.send_delta

    def is_allowed(self, sender_id: str) -> bool:
        """检查 *sender_id* 是否被允许。空列表 → 拒绝所有；``“*”`` → 允许所有。"""
        if isinstance(self.config, dict):
            if "allow_from" in self.config:
                allow_list = self.config.get("allow_from")
            else:
                allow_list = self.config.get("allowFrom", [])
        else:
            allow_list = getattr(self.config, "allow_from", [])
        if not allow_list:
            logger.warning("{}: allow_from is empty — all access denied", self.name)
            return False
        if "*" in allow_list:
            return True
        return str(sender_id) in allow_list

    async def _handle_message(
        self,
        sender_id: str,
        chat_id: str,
        content: str,
        media: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        """
        处理来自聊天平台的传入消息。

        此方法会检查权限，并将消息转发至消息总线。

        参数：
            sender_id：发件人的标识符。
            chat_id：聊天/频道标识符。
            content：消息文本内容。
            media：可选的媒体 URL 列表。
            metadata：可选的频道特定元数据。
            session_key：可选的会话密钥覆盖（例如线程范围内的会话）。
        """
        if not self.is_allowed(sender_id):
            logger.warning(
                "Access denied for sender {} on channel {}. "
                "Add them to allowFrom list in config to grant access.",
                sender_id, self.name,
            )
            return

        meta = metadata or {}
        if self.supports_streaming:
            meta = {**meta, "_wants_stream": True}

        msg = InboundMessage(
            channel=self.name,
            sender_id=str(sender_id),
            chat_id=str(chat_id),
            content=content,
            media=media or [],
            metadata=meta,
            session_key_override=session_key,
        )

        await self.bus.publish_inbound(msg)

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        """恢复内置组件的默认配置。可在插件中进行覆盖，以自动填充 config.json。"""
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        """请检查频道是否正在运行。"""
        return self._running
