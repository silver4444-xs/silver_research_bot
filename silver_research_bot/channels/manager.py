"""用于协调聊天频道的频道管理员"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from silver_research_bot.bus.events import OutboundMessage
from silver_research_bot.bus.queue import MessageBus
from silver_research_bot.channels.base import BaseChannel
from silver_research_bot.config.schema import Config
from silver_research_bot.utils.restart import consume_restart_notice_from_env, format_restart_completed_message

if TYPE_CHECKING:
    from silver_research_bot.session.manager import SessionManager


def _default_webui_dist() -> Path | None:
    """返回 Web UI 静态文件的路径（用于 WebSocket 通道内嵌 Web 界面）."""
    try:
        import silver_research_bot.web as web_pkg  # type: ignore[import-not-found]
    except ImportError:
        return None
    candidate = Path(web_pkg.__file__).resolve().parent / "dist"
    return candidate if candidate.is_dir() else None

# Retry delays for message sending (exponential backoff: 1s, 2s, 4s)
_SEND_RETRY_DELAYS = (1, 2, 4)
'''定义重试延迟（秒），采用指数退避'''

class ChannelManager:
    """
    管理聊天频道并协调消息路由。

    职责：
    - 初始化已启用的频道（Telegram、WhatsApp 等）
    - 启动/停止频道
    - 路由外发消息
    """

    def __init__(
        self,
        config: Config,
        bus: MessageBus,
        *,
        session_manager: "SessionManager | None" = None,
    ):
        self.config = config
        '系统配置对象'
        self.bus = bus
        '事件总线，用于组件间通信'
        self._session_manager = session_manager
        '会话管理器，管理对话生命周期'
        self.channels: dict[str, BaseChannel] = {}
        '通信通道字典，存储所有已注册的交互通道'
        self._dispatch_task: asyncio.Task | None = None
        '消息分发异步任务'
        self._init_channels()
        '初始化所有通信通道'

    def _init_channels(self) -> None:
        """初始化通过 pkgutil scan + entry_points 插件发现的通道。"""
        from silver_research_bot.channels.registry import discover_all

        transcription_provider = self.config.channels.transcription_provider
        transcription_key = self._resolve_transcription_key(transcription_provider)
        transcription_base = self._resolve_transcription_base(transcription_provider)

        for name, cls in discover_all().items():
            section = getattr(self.config.channels, name, None)
            if section is None:
                continue
            enabled = (
                section.get("enabled", False)
                if isinstance(section, dict)
                else getattr(section, "enabled", False)
            )
            if not enabled:
                continue
            try:
                kwargs: dict[str, Any] = {}
                # Only the WebSocket channel currently hosts the embedded webui
                # surface; other channels stay oblivious to these knobs.
                if cls.name == "websocket" and self._session_manager is not None:
                    kwargs["session_manager"] = self._session_manager
                    static_path = _default_webui_dist()
                    if static_path is not None:
                        kwargs["static_dist_path"] = static_path
                channel = cls(section, self.bus, **kwargs)
                channel.transcription_provider = transcription_provider
                channel.transcription_api_key = transcription_key
                channel.transcription_api_base = transcription_base
                self.channels[name] = channel
                logger.info("{} channel enabled", cls.display_name)
            except Exception as e:
                logger.warning("{} channel not available: {}", name, e)

        self._validate_allow_from()

    def _resolve_transcription_key(self, provider: str) -> str:
        """选择已配置的转录服务提供商的 API 密钥."""
        try:
            if provider == "openai":
                return self.config.providers.openai.api_key
            return self.config.providers.groq.api_key
        except AttributeError:
            return ""

    def _resolve_transcription_base(self, provider: str) -> str:
        """选择已配置转录服务提供商的 API 基础 URL"""
        try:
            if provider == "openai":
                return self.config.providers.openai.api_base or ""
            return self.config.providers.groq.api_base or ""
        except AttributeError:
            return ""

    def _validate_allow_from(self) -> None:
        """检查通道的 allow_from 配置，若为空列表（拒绝所有人）则抛出 SystemExit"""
        for name, ch in self.channels.items():
            cfg = ch.config
            if isinstance(cfg, dict):
                if "allow_from" in cfg:
                    allow = cfg.get("allow_from")
                else:
                    allow = cfg.get("allowFrom")
            else:
                allow = getattr(cfg, "allow_from", None)
            if allow == []:
                raise SystemExit(
                    f'Error: "{name}" has empty allowFrom (denies all). '
                    f'Set ["*"] to allow everyone, or add specific user IDs.'
                )

    async def _start_channel(self, name: str, channel: BaseChannel) -> None:
        """创建一个通道并记录任何异常."""
        try:
            await channel.start()
        except Exception as e:
            logger.error("Failed to start channel {}: {}", name, e)

    async def start_all(self) -> None:
        """启动所有频道和外发调度程序。"""
        if not self.channels:
            logger.warning("No channels enabled")
            return

        # Start outbound dispatcher
        self._dispatch_task = asyncio.create_task(self._dispatch_outbound())

        # Start channels
        tasks = []
        for name, channel in self.channels.items():
            logger.info("Starting {} channel...", name)
            tasks.append(asyncio.create_task(self._start_channel(name, channel)))

        self._notify_restart_done_if_needed()

        # Wait for all to complete (they should run forever)
        await asyncio.gather(*tasks, return_exceptions=True)

    def _notify_restart_done_if_needed(self) -> None:
        """当存在运行时环境标记时，发送重启完成消息。"""
        notice = consume_restart_notice_from_env()
        if not notice:
            return
        target = self.channels.get(notice.channel)
        if not target:
            return
        asyncio.create_task(self._send_with_retry(
            target,
            OutboundMessage(
                channel=notice.channel,
                chat_id=notice.chat_id,
                content=format_restart_completed_message(notice.started_at_raw),
            ),
        ))

    async def stop_all(self) -> None:
        """关闭所有频道及分发器"""
        logger.info("Stopping all channels...")

        # Stop dispatcher
        if self._dispatch_task:
            self._dispatch_task.cancel()
            try:
                await self._dispatch_task
            except asyncio.CancelledError:
                pass

        # Stop all channels
        for name, channel in self.channels.items():
            try:
                await channel.stop()
                logger.info("Stopped {} channel", name)
            except Exception as e:
                logger.error("Error stopping {}: {}", name, e)

    async def _dispatch_outbound(self) -> None:
        """将发出的消息发送到相应的频道."""
        logger.info("Outbound dispatcher started")

        pending: list[OutboundMessage] = []
        '用于存储在增量合并过程中无法处理的消息的缓冲区（因为 asyncio.Queue 不支持 push_front）'

        while True:
            try:
                '''1.从消息总线的 outbound 队列中取出一条消息（若缓冲区 pending 非空则优先处理）'''
                if pending:
                    msg = pending.pop(0)
                else:
                    msg = await asyncio.wait_for(
                        self.bus.consume_outbound(),
                        timeout=1.0
                    )

                '''
                2.根据消息元数据决定是否跳过：

                _progress 标记的进度消息，若配置未开启 send_progress 或 send_tool_hints 则跳过。
                _retry_wait 标记的重试等待消息直接跳过（已由重试逻辑处理）
                '''
                if msg.metadata.get("_progress"):
                    if msg.metadata.get("_tool_hint") and not self.config.channels.send_tool_hints:
                        continue
                    if not msg.metadata.get("_tool_hint") and not self.config.channels.send_progress:
                        continue

                if msg.metadata.get("_retry_wait"):
                    continue

                '''
                3.流式消息合并：
                -检查消息是否带有 _stream_delta 且未标记 _stream_end。
                -调用 _coalesce_stream_deltas 从队列中贪婪地取出同一通道、同一会话的后续 _stream_delta 消息，合并它们的 content 和元数据。
                -合并后得到一个最终消息，以及未能合并的其他消息（放入 pending 缓冲区）。
                '''
                # Coalesce consecutive _stream_delta messages for the same (channel, chat_id)
                # to reduce API calls and improve streaming latency
                if msg.metadata.get("_stream_delta") and not msg.metadata.get("_stream_end"):
                    msg, extra_pending = self._coalesce_stream_deltas(msg)
                    pending.extend(extra_pending)

                '''4.根据 msg.channel 找到对应的通道实例，调用 _send_with_retry 发送'''
                channel = self.channels.get(msg.channel)
                if channel:
                    await self._send_with_retry(channel, msg)
                else:
                    logger.warning("Unknown channel: {}", msg.channel)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    @staticmethod
    async def _send_once(channel: BaseChannel, msg: OutboundMessage) -> None:
        """发送一条外发消息，且不启用重试策略."""
        if msg.metadata.get("_stream_delta") or msg.metadata.get("_stream_end"):
            await channel.send_delta(msg.chat_id, msg.content, msg.metadata)
        elif not msg.metadata.get("_streamed"):
            await channel.send(msg)

    def _coalesce_stream_deltas(
        self, first_msg: OutboundMessage
    ) -> tuple[OutboundMessage, list[OutboundMessage]]:
        """合并同一 (channel, chat_id) 组合的连续 _stream_delta 消息。

        当队列中积累了多个
        delta 消息时，此操作可减少 API 调用次数——这种情况通常发生在 LLM 的生成速度超过频道处理能力时。

        返回值：
            (merged_message, list_of_non_matching_messages) 组成的元组
        """
        target_key = (first_msg.channel, first_msg.chat_id)
        combined_content = first_msg.content
        final_metadata = dict(first_msg.metadata or {})
        non_matching: list[OutboundMessage] = []

        # Only merge consecutive deltas. As soon as we hit any other message,
        # stop and hand that boundary back to the dispatcher via `pending`.
        while True:
            try:
                next_msg = self.bus.outbound.get_nowait()
            except asyncio.QueueEmpty:
                break

            # Check if this message belongs to the same stream
            same_target = (next_msg.channel, next_msg.chat_id) == target_key
            is_delta = next_msg.metadata and next_msg.metadata.get("_stream_delta")
            is_end = next_msg.metadata and next_msg.metadata.get("_stream_end")

            if same_target and is_delta and not final_metadata.get("_stream_end"):
                # Accumulate content
                combined_content += next_msg.content
                # If we see _stream_end, remember it and stop coalescing this stream
                if is_end:
                    final_metadata["_stream_end"] = True
                    # Stream ended - stop coalescing this stream
                    break
            else:
                # First non-matching message defines the coalescing boundary.
                non_matching.append(next_msg)
                break

        merged = OutboundMessage(
            channel=first_msg.channel,
            chat_id=first_msg.chat_id,
            content=combined_content,
            metadata=final_metadata,
        )
        return merged, non_matching

    async def _send_with_retry(self, channel: BaseChannel, msg: OutboundMessage) -> None:
        """发送消息，并在失败时使用指数退避算法进行重试。

        注意：会重新抛出 CancelledError 异常，以便实现优雅关闭。
        """
        max_attempts = max(self.config.channels.send_max_retries, 1)

        for attempt in range(max_attempts):
            try:
                await self._send_once(channel, msg)
                return  # Send succeeded
            except asyncio.CancelledError:
                raise  # Propagate cancellation for graceful shutdown
            except Exception as e:
                if attempt == max_attempts - 1:
                    logger.error(
                        "Failed to send to {} after {} attempts: {} - {}",
                        msg.channel, max_attempts, type(e).__name__, e
                    )
                    return
                delay = _SEND_RETRY_DELAYS[min(attempt, len(_SEND_RETRY_DELAYS) - 1)]
                logger.warning(
                    "Send to {} failed (attempt {}/{}): {}, retrying in {}s",
                    msg.channel, attempt + 1, max_attempts, type(e).__name__, delay
                )
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    raise  # Propagate cancellation during sleep

    def get_channel(self, name: str) -> BaseChannel | None:
        """按名称获取频道."""
        return self.channels.get(name)

    def get_status(self) -> dict[str, Any]:
        """获取所有频道的状态。"""
        return {
            name: {
                "enabled": True,
                "running": channel.is_running
            }
            for name, channel in self.channels.items()
        }

    @property
    def enabled_channels(self) -> list[str]:
        """获取已启用频道名称的列表"""
        return list(self.channels.keys())
