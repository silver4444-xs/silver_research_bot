"""
AI Agent核心循环（AgentLoop），
负责从消息总线接收用户消息，构建对话上下文，调用LLM，执行工具调用，并将响应发回。
它是整个silver_research_bot框架的“大脑”，协调会话管理、工具注册、子代理、记忆压缩、MCP集成、后台任务等
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import time
from contextlib import AsyncExitStack, nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from loguru import logger

from silver_research_bot.agent.autocompact import AutoCompact
from silver_research_bot.agent.context import ContextBuilder
from silver_research_bot.agent.hook import AgentHook, AgentHookContext, CompositeHook
from silver_research_bot.agent.memory import Consolidator, Dream
from silver_research_bot.agent.runner import _MAX_INJECTIONS_PER_TURN, AgentRunner, AgentRunSpec
from silver_research_bot.agent.skills import BUILTIN_SKILLS_DIR
from silver_research_bot.agent.subagent import SubagentManager
from silver_research_bot.agent.tools.cron import CronTool
from silver_research_bot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from silver_research_bot.agent.tools.message import MessageTool
from silver_research_bot.agent.tools.notebook import NotebookEditTool
from silver_research_bot.agent.tools.registry import ToolRegistry
from silver_research_bot.agent.tools.search import GlobTool, GrepTool
from silver_research_bot.agent.tools.shell import ExecTool
from silver_research_bot.agent.tools.self import MyTool
from silver_research_bot.agent.tools.spawn import SpawnTool
from silver_research_bot.agent.tools.web import WebFetchTool, WebSearchTool
from silver_research_bot.bus.events import InboundMessage, OutboundMessage
from silver_research_bot.bus.queue import MessageBus
from silver_research_bot.command import CommandContext, CommandRouter, register_builtin_commands
from silver_research_bot.config.schema import AgentDefaults
from silver_research_bot.providers.base import LLMProvider
from silver_research_bot.session.manager import Session, SessionManager
from silver_research_bot.utils.document import extract_documents
from silver_research_bot.utils.helpers import image_placeholder_text
from silver_research_bot.utils.helpers import truncate_text as truncate_text_fn
from silver_research_bot.utils.runtime import EMPTY_FINAL_RESPONSE_MESSAGE

if TYPE_CHECKING:
    from silver_research_bot.config.schema import ChannelsConfig, ExecToolConfig, ToolsConfig, WebToolsConfig
    from silver_research_bot.cron.service import CronService

UNIFIED_SESSION_KEY = "unified:default"


class _LoopHook(AgentHook):
    """Core hook for the main loop."""

    def __init__(
            self,
            agent_loop: AgentLoop,
            on_progress: Callable[..., Awaitable[None]] | None = None,
            on_stream: Callable[[str], Awaitable[None]] | None = None,
            on_stream_end: Callable[..., Awaitable[None]] | None = None,
            *,
            channel: str = "cli",
            chat_id: str = "direct",
            message_id: str | None = None,
    ) -> None:
        super().__init__(reraise=True)
        self._loop = agent_loop
        self._on_progress = on_progress
        self._on_stream = on_stream
        self._on_stream_end = on_stream_end
        self._channel = channel
        self._chat_id = chat_id
        self._message_id = message_id
        self._stream_buf = ""

    def wants_streaming(self) -> bool:
        return self._on_stream is not None

    async def on_stream(self, context: AgentHookContext, delta: str) -> None:
        from silver_research_bot.utils.helpers import strip_think

        prev_clean = strip_think(self._stream_buf)
        self._stream_buf += delta
        new_clean = strip_think(self._stream_buf)
        incremental = new_clean[len(prev_clean):]
        if incremental and self._on_stream:
            await self._on_stream(incremental)

    async def on_stream_end(self, context: AgentHookContext, *, resuming: bool) -> None:
        if self._on_stream_end:
            await self._on_stream_end(resuming=resuming)
        self._stream_buf = ""

    async def before_iteration(self, context: AgentHookContext) -> None:
        self._loop._current_iteration = context.iteration

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._on_progress:
            if not self._on_stream:
                thought = self._loop._strip_think(
                    context.response.content if context.response else None
                )
                if thought:
                    await self._on_progress(thought)
            tool_hint = self._loop._strip_think(self._loop._tool_hint(context.tool_calls))
            await self._on_progress(tool_hint, tool_hint=True)
        for tc in context.tool_calls:
            args_str = json.dumps(tc.arguments, ensure_ascii=False)
            logger.info("Tool call: {}({})", tc.name, args_str[:200])
        self._loop._set_tool_context(self._channel, self._chat_id, self._message_id)

    async def after_iteration(self, context: AgentHookContext) -> None:
        u = context.usage or {}
        logger.debug(
            "LLM usage: prompt={} completion={} cached={}",
            u.get("prompt_tokens", 0),
            u.get("completion_tokens", 0),
            u.get("cached_tokens", 0),
        )

    def finalize_content(self, context: AgentHookContext, content: str | None) -> str | None:
        return self._loop._strip_think(content)


class AgentLoop:
    """
    The agent loop是核心处理引擎。

    它：
    1. 从消息总线接收消息
    2. 结合历史、记忆和技能构建上下文
    3. 调用大型语言模型（LLM）
    4. 执行工具调用
    5. 将响应发回
    """

    _RUNTIME_CHECKPOINT_KEY = "runtime_checkpoint"
    '用作会话元数据中存储运行时检查点的键。检查点保存了未完成的 Assistant 消息、已完成和未完成的工具调用，用于中断恢复（如 /stop 或进程崩溃后继续）'
    _PENDING_USER_TURN_KEY = "pending_user_turn"
    '标记会话中是否有“已持久化但尚未完成响应的用户消息”。当 Agent 刚保存用户消息就崩溃时，恢复时可借此自动追加错误提示，避免消息丢失感。'

    def __init__(
            self,
            bus: MessageBus,
            provider: LLMProvider,
            workspace: Path,
            model: str | None = None,
            max_iterations: int | None = None,
            context_window_tokens: int | None = None,
            context_block_limit: int | None = None,
            max_tool_result_chars: int | None = None,
            provider_retry_mode: str = "standard",
            web_config: WebToolsConfig | None = None,
            exec_config: ExecToolConfig | None = None,
            cron_service: CronService | None = None,
            restrict_to_workspace: bool = False,
            session_manager: SessionManager | None = None,
            mcp_servers: dict | None = None,
            channels_config: ChannelsConfig | None = None,
            timezone: str | None = None,
            session_ttl_minutes: int = 0,
            hooks: list[AgentHook] | None = None,
            unified_session: bool = False,
            disabled_skills: list[str] | None = None,
            tools_config: ToolsConfig | None = None,
    ):
        from silver_research_bot.config.schema import ExecToolConfig, ToolsConfig, WebToolsConfig

        defaults = AgentDefaults()

        # =========================1 核心依赖（直接赋值）===============================
        self.bus = bus
        '消息总线，用于收发消息'
        self.channels_config = channels_config
        '各渠道（如 CLI、Feishu）的特定配置'
        self.provider = provider
        'LLM provider（如OpenAI、Anthropic等）'
        self.workspace = workspace
        '工作目录，工具读写文件时的根路径'
        self.model = model or provider.get_default_model()
        '模型名称，若不提供则使用 provider 的默认模型'
        self.cron_service = cron_service
        '定时任务服务，用于注册和管理周期性任务'

        # =========================2 行为控制参数===============================
        '模型名称（可覆盖provider默认）'
        self.max_iterations = (
            max_iterations if max_iterations is not None else defaults.max_tool_iterations
        )
        '主 Agent 最大迭代次数'
        self.context_window_tokens = (
            context_window_tokens
            if context_window_tokens is not None
            else defaults.context_window_tokens
        )
        'LLM上下文窗口大小，用于触发压缩'
        self.context_block_limit = context_block_limit
        '可选，限制上下文中的消息块数量（某些 provider 的特殊限制）'
        self.max_tool_result_chars = (
            max_tool_result_chars
            if max_tool_result_chars is not None
            else defaults.max_tool_result_chars
        )
        '工具返回结果的最大字符数，超过会被截断，防止撑爆上下文'
        self.provider_retry_mode = provider_retry_mode
        '当 LLM 调用失败时的重试策略（如标准、退避等）'

        '''控制Web搜索、Shell执行等工具的开关与配置'''
        self.web_config = web_config or WebToolsConfig()
        self.exec_config = exec_config or ExecToolConfig()
        _tc = tools_config or ToolsConfig()

        self.restrict_to_workspace = restrict_to_workspace
        '是否限制文件操作在 workspace 内'
        self._unified_session = unified_session
        '是否将所有会话合并到同一个key（测试/演示用）'

        # =========================3 运行时状态===============================
        self._start_time = time.time()
        '启动时间'
        self._last_usage: dict[str, int] = {}
        '最近一次 LLM 调用的 token 使用量'
        self._running = False
        '循环是否运行中'
        self._runtime_vars: dict[str, Any] = {}
        '当前正在处理的迭代次数（用于钩子）'
        self._current_iteration: int = 0
        '运行时变量，供 MyTool 等使用'

        # =========================4 核心子系统（构建器、管理器、注册表）===============================
        self.context = ContextBuilder(workspace, timezone=timezone, disabled_skills=disabled_skills)
        '负责组装发送给LLM的系统提示、运行时上下文、历史消息等'
        self.sessions = session_manager or SessionManager(workspace)
        '会话持久化管理'
        self.tools = ToolRegistry()
        '管理所有可用工具（文件读写、Shell、搜索、子代理、MCP工具等）'
        self.runner = AgentRunner(provider)
        '封装LLM调用和工具执行循环'
        self.subagents = SubagentManager(
            provider=provider,
            workspace=workspace,
            bus=bus,
            model=self.model,
            web_config=self.web_config,
            max_tool_result_chars=self.max_tool_result_chars,
            exec_config=self.exec_config,
            restrict_to_workspace=restrict_to_workspace,
            disabled_skills=disabled_skills,
        )
        '管理子代理进程'
        self.commands = CommandRouter()
        '处理内置命令（如 /help、/reset、/stop）'

        # =========================5 记忆与压缩===============================
        '信号量限制全局并发请求数（默认3）'
        self.consolidator = Consolidator(
            store=self.context.memory,
            provider=provider,
            model=self.model,
            sessions=self.sessions,
            context_window_tokens=self.context_window_tokens,
            build_messages=self.context.build_messages,
            get_tool_definitions=self.tools.get_definitions,
            max_completion_tokens=provider.generation.max_tokens,
        )
        '当历史过长时自动摘要合并'
        self.auto_compact = AutoCompact(
            sessions=self.sessions,
            consolidator=self.consolidator,
            session_ttl_minutes=session_ttl_minutes,
        )
        '检查空闲会话是否过期并清理'
        self.dream = Dream(
            store=self.context.memory,
            provider=provider,
            model=self.model,
        )
        '背景记忆增强'

        # =========================6 并发与任务管理===============================
        self._active_tasks: dict[str, list[asyncio.Task]] = {}  # session_key -> tasks
        ' 存储每个会话当前运行的asyncio任务，支持 /stop 取消'
        self._background_tasks: list[asyncio.Task] = []
        '列表，存放需要在关闭时等待的后台任务（如异步压缩）'
        self._session_locks: dict[str, asyncio.Lock] = {}
        '每个会话一个 asyncio.Lock，保证同一会话的消息串行处理'
        # Per-session pending queues for mid-turn message injection.
        # When a session has an active task, new messages for that session
        # are routed here instead of creating a new task.
        self._pending_queues: dict[str, asyncio.Queue] = {}
        '每个会话的挂起消息队列，用于中轮注入（mid-turn injection）——当agent正忙于处理时，新来的消息先排队，等当前turn完成后立即注入成新的用户消息，而不用等待用户重新输入'
        # silver_research_bot_MAX_CONCURRENT_REQUESTS: <=0 means unlimited; default 3.
        _max = int(os.environ.get("silver_research_bot_MAX_CONCURRENT_REQUESTS", "3"))
        self._concurrency_gate: asyncio.Semaphore | None = (
            asyncio.Semaphore(_max) if _max > 0 else None
        )
        '信号量（默认并发数为 3），限制同时处理的会话数量，防止过载'

        # =========================7.MCP（Model Context Protocol）集成===============================
        self._mcp_servers = mcp_servers or {}
        'MCP 服务器配置字典，用于动态扩展工具'
        self._mcp_stacks: dict[str, AsyncExitStack] = {}
        '每个服务器的异步退出栈'
        self._mcp_connected = False
        '是否已连接'
        self._mcp_connecting = False
        '是否正在连接中（防重入）'

        # =========================8.工具注册===============================
        self._register_default_tools()
        '注册内置工具（文件、搜索、Shell、Message、Spawn、Cron 等）'
        if _tc.my.enable:
            self.tools.register(MyTool(loop=self, modify_allowed=_tc.my.allow_set))
        '若 tools_config.my.enable 为真，还会注册 MyTool，让 Agent 可以查看/修改自身部分配置'

        # =========================9 钩子系统===============================
        self._extra_hooks: list[AgentHook] = hooks or []
        '外部钩子，可注入流式、日志等逻辑'

        register_builtin_commands(self.commands)

    def _register_default_tools(self) -> None:
        """注册 Agent 默认具备的工具集。这些工具允许 Agent 读写文件、执行 shell 命令、搜索网络、发送消息、启动子代理、管理定时任务等"""
        allowed_dir = (
            self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
        )
        extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
        self.tools.register(
            ReadFileTool(
                workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read
            )
        )
        for cls in (WriteFileTool, EditFileTool, ListDirTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        for cls in (GlobTool, GrepTool):
            self.tools.register(cls(workspace=self.workspace, allowed_dir=allowed_dir))
        self.tools.register(NotebookEditTool(workspace=self.workspace, allowed_dir=allowed_dir))
        if self.exec_config.enable:
            self.tools.register(
                ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                )
            )
        if self.web_config.enable:
            self.tools.register(
                WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy)
            )
            self.tools.register(WebFetchTool(proxy=self.web_config.proxy))
        self.tools.register(MessageTool(send_callback=self.bus.publish_outbound))
        self.tools.register(SpawnTool(manager=self.subagents))
        if self.cron_service:
            self.tools.register(
                CronTool(self.cron_service, default_timezone=self.context.timezone or "UTC")
            )

    async def _connect_mcp(self) -> None:
        """懒加载方式连接到配置的 MCP（Model Context Protocol）服务器，动态注册 MCP 提供的额外工具"""
        if self._mcp_connected or self._mcp_connecting or not self._mcp_servers:
            return
        self._mcp_connecting = True
        from silver_research_bot.agent.tools.mcp import connect_mcp_servers

        try:
            self._mcp_stacks = await connect_mcp_servers(self._mcp_servers, self.tools)
            if self._mcp_stacks:
                self._mcp_connected = True
            else:
                logger.warning("No MCP servers connected successfully (will retry next message)")
        except asyncio.CancelledError:
            logger.warning("MCP connection cancelled (will retry next message)")
            self._mcp_stacks.clear()
        except BaseException as e:
            logger.error("Failed to connect MCP servers (will retry next message): {}", e)
            self._mcp_stacks.clear()
        finally:
            self._mcp_connecting = False

    def _set_tool_context(self, channel: str, chat_id: str, message_id: str | None = None) -> None:
        """将当前对话的路由信息注入到那些需要知道“在哪里发送消息”的工具中（如 MessageTool、SpawnTool、CronTool、MyTool）"""
        # 计算有效会话密钥（适用于统一会话）
        # 以便子代理的结果能路由到正确的待处理队列。
        effective_key = UNIFIED_SESSION_KEY if self._unified_session else f"{channel}:{chat_id}"
        for name in ("message", "spawn", "cron", "my"):
            if tool := self.tools.get(name):
                if hasattr(tool, "set_context"):
                    if name == "spawn":
                        tool.set_context(channel, chat_id, effective_key=effective_key)
                    else:
                        tool.set_context(channel, chat_id, *([message_id] if name == "message" else []))

    @staticmethod
    def _strip_think(text: str | None) -> str | None:
        """移除模型输出中可能嵌入的 <think>...</think> 标签块。某些模型（如 DeepSeek）会在最终回复前输出“思考过程”，这些内容不应展示给最终用户"""
        if not text:
            return None
        from silver_research_bot.utils.helpers import strip_think

        return strip_think(text) or None

    @staticmethod
    def _tool_hint(tool_calls: list) -> str:
        """将工具调用列表格式化为简洁的人类可读提示，用于在流式界面中显示“Agent 正在调用什么工具”"""
        from silver_research_bot.utils.tool_hints import format_tool_hints

        return format_tool_hints(tool_calls)

    async def _dispatch_command_inline(
            self,
            msg: InboundMessage,
            key: str,
            raw: str,
            dispatch_fn: Callable[[CommandContext], Awaitable[OutboundMessage | None]],
    ) -> None:
        """从主循环 run() 中直接派发一个命令，并立即将结果发布到消息总线，不经过常规的 Agent 处理流程"""
        ctx = CommandContext(msg=msg, session=None, key=key, raw=raw, loop=self)
        result = await dispatch_fn(ctx)
        if result:
            await self.bus.publish_outbound(result)
        else:
            logger.warning("Command '{}' matched but dispatch returned None", raw)

    async def _cancel_active_tasks(self, key: str) -> int:
        """取消指定会话（key）的所有活跃任务（包括主 Agent 任务和子代理），并等待它们完成清理

        返回已取消任务的总数 + 子代理的数量。
        """
        tasks = self._active_tasks.pop(key, [])
        cancelled = sum(1 for t in tasks if not t.done() and t.cancel())
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        sub_cancelled = await self.subagents.cancel_by_session(key)
        return cancelled + sub_cancelled

    def _effective_session_key(self, msg: InboundMessage) -> str:
        """根据 unified_session 配置和消息中的覆盖标记，计算实际用于路由和队列管理的会话键."""
        if self._unified_session and not msg.session_key_override:
            return UNIFIED_SESSION_KEY
        return msg.session_key

    async def _run_agent_loop(
            self,
            initial_messages: list[dict],
            on_progress: Callable[..., Awaitable[None]] | None = None,
            on_stream: Callable[[str], Awaitable[None]] | None = None,
            on_stream_end: Callable[..., Awaitable[None]] | None = None,
            on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
            *,
            session: Session | None = None,
            channel: str = "cli",
            chat_id: str = "direct",
            message_id: str | None = None,
            pending_queue: asyncio.Queue | None = None,
    ) -> tuple[str | None, list[str], list[dict], str, bool]:
        """
        运行Agent迭代循环。

        *on_stream*：在流式传输过程中，每次内容增量更新时调用。
        *on_stream_end(resuming)*：流式传输会话结束时调用。
        ``resuming=True`` 表示后续将调用工具（加载图标应重新启动）；
        ``resuming=False`` 表示这是最终响应。

        返回 (final_content, tools_used, messages, stop_reason, had_injections)。
        """
        loop_hook = _LoopHook(
            self,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            channel=channel,
            chat_id=chat_id,
            message_id=message_id,
        )
        '''
        _LoopHook 是 AgentLoop 内部定义的钩子类，实现了 AgentHook 接口。它负责：
        - 将流式 delta 通过 on_stream 回调发送出去。
        - 在流结束时调用 on_stream_end。
        - 在每次迭代前后记录迭代次数、工具调用信息等
        '''
        hook: AgentHook = (
            CompositeHook([loop_hook] + self._extra_hooks) if self._extra_hooks else loop_hook
        )
        '如果提供了额外的钩子（self._extra_hooks），则用 CompositeHook 将它们组合在一起，这样所有钩子的方法都会被依次调用'

        async def _checkpoint(payload: dict[str, Any]) -> None:
            """
            检查点回调
            AgentRunner 在执行每个工具调用后会调用此回调，传入当前的快照（包含 assistant_message、completed_tool_results、pending_tool_calls 等）。
            _set_runtime_checkpoint 会将该 payload 保存到会话的元数据中（键为 _RUNTIME_CHECKPOINT_KEY），用于中断恢复。
            """
            if session is None:
                return
            self._set_runtime_checkpoint(session, payload)

        async def _drain_pending(*, limit: int = _MAX_INJECTIONS_PER_TURN) -> list[dict[str, Any]]:
            """
            中轮注入队列读取回调
            -该函数非阻塞地从 pending_queue 中取出最多 limit（默认常量 _MAX_INJECTIONS_PER_TURN = 3）条待注入消息。
            -每条待注入消息原本是 InboundMessage 对象，需要转换为 LLM 可接受的消息字典（role: user，content 包含运行时上下文 + 用户内容）。

            -转换过程：
                *处理媒体文件（调用 extract_documents 提取文本）。
                *调用 self.context._build_user_content 构建用户内容（可能为字符串或内容块列表）。
                *调用 self.context._build_runtime_context 生成运行时上下文（如当前时间、工作目录等）。
                *将运行时上下文与用户内容合并成一个完整的用户消息。

            -这个函数会作为 AgentRunSpec 的 injection_callback 传递给 AgentRunner。AgentRunner 在每次 LLM 调用之前（当没有待执行的工具时）会调用它，将新注入的消息追加到对话上下文中，实现“中轮注入”。
            """
            if pending_queue is None:
                return []
            items: list[dict[str, Any]] = []
            while len(items) < limit:
                try:
                    pending_msg = pending_queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                content = pending_msg.content
                media = pending_msg.media if pending_msg.media else None
                if media:
                    content, media = extract_documents(content, media)
                    media = media or None
                user_content = self.context._build_user_content(content, media)
                runtime_ctx = self.context._build_runtime_context(
                    pending_msg.channel,
                    pending_msg.chat_id,
                    self.context.timezone,
                )
                if isinstance(user_content, str):
                    merged: str | list[dict[str, Any]] = f"{runtime_ctx}\n\n{user_content}"
                else:
                    merged = [{"type": "text", "text": runtime_ctx}] + user_content
                items.append({"role": "user", "content": merged})
            return items

        result = await self.runner.run(AgentRunSpec(
            initial_messages=initial_messages,
            tools=self.tools,
            model=self.model,
            max_iterations=self.max_iterations,
            max_tool_result_chars=self.max_tool_result_chars,
            hook=hook,
            error_message="Sorry, I encountered an error calling the AI model.",
            concurrent_tools=True,
            workspace=self.workspace,
            session_key=session.key if session else None,
            context_window_tokens=self.context_window_tokens,
            context_block_limit=self.context_block_limit,
            provider_retry_mode=self.provider_retry_mode,
            progress_callback=on_progress,
            retry_wait_callback=on_retry_wait,
            checkpoint_callback=_checkpoint,
            injection_callback=_drain_pending,
        ))
        'AgentRunner.run 执行实际的 ReAct 循环'

        '''
            处理特殊停止原因
            1.达到最大迭代次数（例如 20 轮），此时即使没有生成最终答案也会结束循环。
            为了流式渠道（如飞书）能更新卡片，会强制调用 on_stream 和 on_stream_end 发送最终内容（可能为空）   
            
            2.LLM 调用出错（例如 API 超时、鉴权失败），记录错误日志 
        '''
        self._last_usage = result.usage
        if result.stop_reason == "max_iterations":
            logger.warning("Max iterations ({}) reached", self.max_iterations)
            # Push final content through stream so streaming channels (e.g. Feishu)
            # update the card instead of leaving it empty.
            if on_stream and on_stream_end:
                await on_stream(result.final_content or "")
                await on_stream_end(resuming=False)
        elif result.stop_reason == "error":
            logger.error("LLM returned error: {}", (result.final_content or "")[:200])
        return result.final_content, result.tools_used, result.messages, result.stop_reason, result.had_injections

    async def run(self) -> None:
        """运行 agent 循环, 将消息作为任务进行分发，以确保能响应 /stop 命令."""

        '''
        1. 初始化与连接 MCP
        - 设置运行标志，开始循环。
        -尝试连接配置的 MCP 服务器（懒加载，只在首次运行时连接）。
        -记录启动日志。
        '''
        self._running = True
        await self._connect_mcp()
        logger.info("Agent loop started")

        '''2.主循环：等待入站消息（带超时）'''
        while self._running:
            try:
                # 每 1 秒超时一次。这样即使没有消息，循环也能定期执行后台清理
                msg = await asyncio.wait_for(self.bus.consume_inbound(), timeout=1.0)
            except asyncio.TimeoutError:
                # 超时处理：调用 auto_compact.check_expired，它会找出空闲时间超过 session_ttl_minutes 的会话，并安排后台清理任务。
                # 传入 active_session_keys（当前有挂起队列的会话 key），避免清理正在活跃的会话。
                # _schedule_background 是一个辅助方法，用于启动后台异步任务并跟踪
                self.auto_compact.check_expired(
                    self._schedule_background,
                    active_session_keys=self._pending_queues.keys(),
                )
                continue
            # '''3. 异常处理'''
            except asyncio.CancelledError:
                # 当外部（如关闭时）取消 run() 任务时，需要区分：
                # 如果 _running 已为 False 或者当前任务正在被取消（cancelling()），则重新抛出异常，让上层处理关闭。
                # 否则（可能是某些集成库错误地抛出 CancelledError），忽略并继续循环。
                if not self._running or asyncio.current_task().cancelling():
                    raise
                continue
            except Exception as e:
                # 其他异常：记录警告，但不崩溃，继续处理下一条消息（鲁棒性设计）
                logger.warning("Error consuming inbound message: {}, continuing...", e)
                continue

            '''
            4. 获取原始消息内容并检查优先级命令
            is_priority 判断是否为必须立即响应的命令（如 /stop、/reset）。这些命令不能进入消息队列或挂起队列，必须同步处理。
            _dispatch_command_inline 直接派发命令并发布结果，不经过常规的 _dispatch 任务创建流程
            '''
            raw = msg.content.strip()
            if self.commands.is_priority(raw):
                await self._dispatch_command_inline(
                    msg, msg.session_key, raw,
                    self.commands.dispatch_priority,
                )
                continue
            '''
            5. 计算有效会话键（effective_key）
            若启用了统一会话模式（unified_session=True）且消息没有覆盖键，则返回 UNIFIED_SESSION_KEY（"unified:default"）。
            否则返回 msg.session_key（格式通常为 channel:chat_id）
            '''
            effective_key = self._effective_session_key(msg)

            '''
            6. 中轮注入路由：检查是否有活跃任务
            _pending_queues 字典的键为会话 key，值为 asyncio.Queue。只要某个会话的 key 在此字典中，就说明该会话当前有一个正在运行的 _dispatch 任务，该任务创建时会把 pending_queue 放进该字典。
            
            因此，如果当前消息的 effective_key 在 _pending_queues 中，意味着该会话已经在处理中（可能正在执行工具、等待 LLM 等），此时新消息不能被创建为另一个独立任务（否则会打破串行约束），而应该：
            - 要么作为命令直接处理；
            - 要么放入 pending_queue 等待当前任务在适当的时候注入（中轮注入）。
            '''
            if effective_key in self._pending_queues:
                '''
                    6.1 可派发命令的处理
                    某些非优先级命令（如 /help）虽然不紧急，但也不应该排队等待中轮注入，因为注入后的消息会在当前任务的某个迭代间隙被当作普通用户消息，会改变对话上下文，可能不符合用户预期。
                    因此，对“可派发命令”也采用内联派发，直接执行并返回结果，不放入队列。
                '''
                if self.commands.is_dispatchable_command(raw):
                    await self._dispatch_command_inline(
                        msg, effective_key, raw,
                        self.commands.dispatch,
                    )
                    continue
                '''
                6.2 普通消息：放入 pending_queue 等待中轮注入
                -如果当前消息的原始 session_key 与 effective_key 不同（发生统一会话模式下的覆盖），则创建一个新消息副本，设置 session_key_override 为 effective_key，确保子代理结果等能够正确路由回该会话的挂起队列。
                -尝试非阻塞地将消息放入队列（put_nowait）。队列默认最大容量为 20（在 _dispatch 中创建）。                
                -如果队列已满（不太可能，但防御性处理），记录警告，然后不 continue，而是降级为创建新任务（即下面的逻辑会执行）。这避免了消息丢失。
                -如果成功入队，记录日志并 continue（不继续创建新任务）。
                '''
                pending_msg = msg
                if effective_key != msg.session_key:
                    pending_msg = dataclasses.replace(
                        msg,
                        session_key_override=effective_key,
                    )
                try:
                    self._pending_queues[effective_key].put_nowait(pending_msg)
                except asyncio.QueueFull:
                    logger.warning(
                        "Pending queue full for session {}, falling back to queued task",
                        effective_key,
                    )
                else:
                    logger.info(
                        "Routed follow-up message to pending queue for session {}",
                        effective_key,
                    )
                    continue
            # Compute the effective session key before dispatching
            # This ensures /stop command can find tasks correctly when unified session is enabled
            '''
            7. 创建新任务处理消息（无活跃任务的情况）
            -为该消息创建一个异步任务，执行 self._dispatch(msg)（该方法内部会创建 pending_queue、获取锁、调用 _process_message 等）。
            -将这个任务添加到 _active_tasks[effective_key] 列表中，以便后续通过 /stop 命令取消。
            -添加一个完成回调，当任务结束时（无论成功、取消或异常），从 _active_tasks[effective_key] 中移除该任务引用，避免内存泄漏。
            '''
            task = asyncio.create_task(self._dispatch(msg))
            self._active_tasks.setdefault(effective_key, []).append(task)
            task.add_done_callback(
                lambda t, k=effective_key: self._active_tasks.get(k, [])
                                           and self._active_tasks[k].remove(t)
                if t in self._active_tasks.get(k, [])
                else None
            )

    async def _dispatch(self, msg: InboundMessage) -> None:
        """消息处理：单会话内顺序处理，跨会话并发处理。"""

        '''
        1.会话键（session key）的统一与覆盖
        计算真正的会话键（考虑 unified_session 模式和消息自身的覆盖标记）。
        如果与消息原有的 session_key 不同，则创建一个新消息副本，将 session_key_override 设为计算出的 session_key。
        这样在后面的逻辑中（如子代理回调）能够正确地路由到该会话的挂起队列
        '''
        session_key = self._effective_session_key(msg)
        if session_key != msg.session_key:
            msg = dataclasses.replace(msg, session_key_override=session_key)

        '''2.获取锁和并发闸门'''
        lock = self._session_locks.setdefault(session_key, asyncio.Lock())
        gate = self._concurrency_gate or nullcontext()

        '''
        3.注册挂起队列
        这个队列的作用是中轮注入：当该会话已有活跃任务时（即 session_key 已在 _pending_queues 中），新消息会被放进这个队列，而不是创建新任务。
        约定：只要某个会话的条目在 _pending_queues 中，就表示该会话当前有一个正在运行的 _dispatch 任务
        '''
        pending = asyncio.Queue(maxsize=20)
        self._pending_queues[session_key] = pending

        '''4.处理消息'''
        try:
            async with lock, gate:
                try:
                    '''
                    4.1 流式输出支持
                    如果消息的元数据要求流式输出（如前端支持实时逐字显示），则定义两个回调：
                        -on_stream：每次收到内容 delta 时，将其包装成 OutboundMessage 并发布到总线。消息会携带 _stream_delta=True 和递增的 _stream_id。
                        -on_stream_end：流式段结束时调用，resuming 参数表示是否马上就接着有工具调用（即 LLM 要执行工具，前端应重新显示 spinner）。
                    每次调用 on_stream_end 会递增 stream_segment，使得同一对话中的多个流式段落（例如 LLM 多次输出）能区分开。
                    这些回调最终会传递给 _process_message，再由 _run_agent_loop 传入 AgentRunner 的钩子。
                    '''
                    on_stream = on_stream_end = None
                    if msg.metadata.get("_wants_stream"):
                        # Split one answer into distinct stream segments.
                        stream_base_id = f"{msg.session_key}:{time.time_ns()}"
                        stream_segment = 0

                        def _current_stream_id() -> str:
                            return f"{stream_base_id}:{stream_segment}"

                        async def on_stream(delta: str) -> None:
                            meta = dict(msg.metadata or {})
                            meta["_stream_delta"] = True
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content=delta,
                                metadata=meta,
                            ))

                        async def on_stream_end(*, resuming: bool = False) -> None:
                            nonlocal stream_segment
                            meta = dict(msg.metadata or {})
                            meta["_stream_end"] = True
                            meta["_resuming"] = resuming
                            meta["_stream_id"] = _current_stream_id()
                            await self.bus.publish_outbound(OutboundMessage(
                                channel=msg.channel, chat_id=msg.chat_id,
                                content="",
                                metadata=meta,
                            ))
                            stream_segment += 1

                    '''
                    4.2 调用核心处理函数
                    _process_message 是真正完成会话加载、上下文构建、调用 LLM、执行工具、保存历史的函数。
                    传入 pending_queue，以便 _run_agent_loop 中的 _drain_pending 能从中取出中轮注入的消息。
                    返回一个 OutboundMessage 或 None（例如当 MessageTool 已经发送了响应，且无需额外回复时）。
                    '''
                    response = await self._process_message(
                        msg, on_stream=on_stream, on_stream_end=on_stream_end,
                        pending_queue=pending,
                    )

                    '''
                    4.3 发布响应
                    如果有响应消息，直接发布到出站总线。
                    
                    对于 cli 频道，即使 response 为 None（例如被 MessageTool 吞掉），也发送一条空消息，
                    目的是让 CLI 客户端知道处理已经完成（否则 CLI 可能会一直等待响应）
                    '''
                    if response is not None:
                        await self.bus.publish_outbound(response)
                    elif msg.channel == "cli":
                        await self.bus.publish_outbound(OutboundMessage(
                            channel=msg.channel, chat_id=msg.chat_id,
                            content="", metadata=msg.metadata or {},
                        ))
                # 5 异常处理
                # 5.1 CancelledError (任务被取消)
                except asyncio.CancelledError:
                    # 当用户输入 /stop 或系统主动取消任务时，任务会收到 CancelledError。
                    # 此时，之前通过 _checkpoint 回调保存的运行时检查点（未完成的 assistant 消息、已完成的部分工具结果）还在会话元数据中。
                    # 我们调用 _restore_runtime_checkpoint(session) 将这些部分结果物化到会话历史中，并清除 _PENDING_USER_TURN_KEY 标记，然后保存会话。
                    # 这样下次用户发送新消息时，中断前的上下文能够恢复，不会丢失已执行的工具结果。
                    # 重新抛出 CancelledError 以确保任务状态正确传播。
                    logger.info("Task cancelled for session {}", session_key)
                    try:
                        key = self._effective_session_key(msg)
                        session = self.sessions.get_or_create(key)
                        if self._restore_runtime_checkpoint(session):
                            self._clear_pending_user_turn(session)
                            self.sessions.save(session)
                            logger.info(
                                "Restored partial context for cancelled session {}",
                                key,
                            )
                    except Exception:
                        logger.debug(
                            "Could not restore checkpoint for cancelled session {}",
                            session_key,
                            exc_info=True,
                        )
                    raise
                # 5.2 其他异常
                # 任何未预期的异常会被记录，并向用户返回一个通用错误消息，避免 agent 静默崩溃
                except Exception:
                    logger.exception("Error processing message for session {}", session_key)
                    await self.bus.publish_outbound(OutboundMessage(
                        channel=msg.channel, chat_id=msg.chat_id,
                        content="Sorry, I encountered an error.",
                    ))
        # 6. 清理（finally 块）
        finally:
            # 无论任务成功、取消还是异常，最终都要从 _pending_queues 中移除当前会话的队列。
            # 检查队列中是否还有残留的消息（这些消息是在任务执行期间通过中轮注入放入但未被消费的，例如用户在任务结束后才发送的消息）
            # 将这些残留消息重新发布到入站消息总线，让 run() 循环在下一次迭代中重新处理（可能是创建新任务，或再次排队）。
            # 这样做保证了消息不会因为任务退出而丢失。
            queue = self._pending_queues.pop(session_key, None)
            if queue is not None:
                leftover = 0
                while True:
                    try:
                        item = queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    await self.bus.publish_inbound(item)
                    leftover += 1
                if leftover:
                    logger.info(
                        "Re-published {} leftover message(s) to bus for session {}",
                        leftover, session_key,
                    )

    async def close_mcp(self) -> None:
        """清空待处理的后台存档，然后关闭 MCP 连接。"""
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for name, stack in self._mcp_stacks.items():
            try:
                await stack.aclose()
            except (RuntimeError, BaseExceptionGroup):
                logger.debug("MCP server '{}' cleanup error (can be ignored)", name)
        self._mcp_stacks.clear()

    def _schedule_background(self, coro) -> None:
        """
        将协程安排为受监视的后台任务（关机时清空）.
        1.创建任务：task = asyncio.create_task(coro) 将协程包装成 asyncio.Task 并立即开始调度。
        2.添加到跟踪列表：self._background_tasks.append(task) 记录该任务，以便在 close_mcp 或最终关闭时能等待它。
        3.自动清理：task.add_done_callback(self._background_tasks.remove) 当任务完成（正常或异常）时，回调会将其从 _background_tasks 列表中移除。这样 _background_tasks 始终只包含尚未完成的后台任务。
        """
        task = asyncio.create_task(coro)
        self._background_tasks.append(task)
        task.add_done_callback(self._background_tasks.remove)

    def stop(self) -> None:
        """停止 agent 循环"""
        self._running = False
        logger.info("Agent loop stopping")

    async def _process_message(
            self,
            msg: InboundMessage,
            session_key: str | None = None,
            on_progress: Callable[[str], Awaitable[None]] | None = None,
            on_stream: Callable[[str], Awaitable[None]] | None = None,
            on_stream_end: Callable[..., Awaitable[None]] | None = None,
            pending_queue: asyncio.Queue | None = None,
    ) -> OutboundMessage | None:
        """处理单个入站消息并返回响应，包含了从会话加载、上下文构建、LLM 调用到响应返回的完整流程"""
        '''
        1. 系统消息分支 
        系统消息的 chat_id 格式通常为 "channel:chat_id"（例如 "cli:direct"），
        需要解析出真实的 channel 和 chat_id，以便后续构建会话键和工具上下文。
        '''
        if msg.channel == "system":
            channel, chat_id = (
                msg.chat_id.split(":", 1) if ":" in msg.chat_id else ("cli", msg.chat_id)
            )
            logger.info("Processing system message from {}", msg.sender_id)
            '''
            1.1 加载/恢复会话
            -获取或创建会话对象。
            -尝试恢复未完成的运行时检查点（工具部分执行后的状态）和挂起的用户 turn（用户消息已存储但未完成响应），并立即保存。
            '''
            key = f"{channel}:{chat_id}"
            session = self.sessions.get_or_create(key)
            if self._restore_runtime_checkpoint(session):
                self.sessions.save(session)
            if self._restore_pending_user_turn(session):
                self.sessions.save(session)

            '''
            1.2 自动压缩
            prepare_session：根据会话空闲时间和 TTL 决定是否清空部分历史（返回新的 session 和待注入的摘要消息）。
            maybe_consolidate_by_tokens：如果消息 token 数超过阈值，调用 LLM 生成摘要并压缩历史
            '''
            session, pending = self.auto_compact.prepare_session(session, key)

            ''''''
            await self.consolidator.maybe_consolidate_by_tokens(
                session,
                session_summary=pending,
            )

            '''
            1.3 子代理消息的特殊处理
            # 在提示语组装之前，将子代理的后续消息持久化到持久化历史记录中。
            # ContextBuilder 会合并相邻的同角色消息以确保提供商兼容性，这此前曾导致后续消息
            从 session.messages 中消失，但通过合并后的提示语,大型语言模型 (LLM) 仍能看到这些消息
            '''
            is_subagent = msg.sender_id == "subagent"
            if is_subagent and self._persist_subagent_followup(session, msg):
                self.sessions.save(session)
            self._set_tool_context(channel, chat_id, msg.metadata.get("message_id"))
            history = session.get_history(max_messages=0)
            current_role = "assistant" if is_subagent else "user"

            '''
            1.4 设置工具上下文并构建消息
            -为工具注入路由信息（channel, chat_id）。
            -获取完整历史（max_messages=0 表示不限条数）。
            -如果是子代理消息，current_message 为空（因为子代理的内容已包含在历史中），角色为 assistant；否则角色为 user。
            '''
            messages = self.context.build_messages(
                history=history,
                current_message="" if is_subagent else msg.content,
                channel=channel,
                chat_id=chat_id,
                session_summary=pending,
                current_role=current_role,
            )
            '''
            1.5 运行 Agent 循环并保存结果
            -调用 _run_agent_loop 执行 LLM 推理和工具循环。
            -将新产生的消息持久化到会话（_save_turn 跳过已存在的历史部分）。
            -清除运行时检查点，保存会话，并调度后台压缩（不阻塞当前响应）。
            -返回包含最终内容的出站消息。
            
            系统消息分支特点：没有媒体处理、没有命令解析、不保存用户消息（因为是内部消息），主要用于子代理结果回传。
            '''
            final_content, _, all_msgs, _, _ = await self._run_agent_loop(
                messages, session=session, channel=channel, chat_id=chat_id,
                message_id=msg.metadata.get("message_id"),
            )
            self._save_turn(session, all_msgs, 1 + len(history))
            self._clear_runtime_checkpoint(session)
            self.sessions.save(session)
            self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))
            return OutboundMessage(
                channel=channel,
                chat_id=chat_id,
                content=final_content or "Background task completed.",
            )

        '''2. 普通用户消息分支'''
        '''
        2.1 媒体预处理
        调用 extract_documents 从图片/文件中提取文本，替换原内容，并将媒体列表简化为仅图片占位符（image_only）。
        '''
        if msg.media:
            new_content, image_only = extract_documents(msg.content, msg.media)
            msg = dataclasses.replace(msg, content=new_content, media=image_only)

        preview = msg.content[:80] + "..." if len(msg.content) > 80 else msg.content
        logger.info("Processing message from {}:{}: {}", msg.channel, msg.sender_id, preview)

        '''
        2.2 加载会话与恢复
        确定会话键（优先使用传入的 session_key）。
        获取/创建会话，并恢复可能未完成的检查点和挂起的用户 turn。
        '''
        key = session_key or msg.session_key
        session = self.sessions.get_or_create(key)
        if self._restore_runtime_checkpoint(session):
            self.sessions.save(session)
        if self._restore_pending_user_turn(session):
            self.sessions.save(session)

        '''
        2.3 自动压缩
        类似系统消息，根据空闲时间清理历史，返回待注入的摘要消息（pending）
        '''
        session, pending = self.auto_compact.prepare_session(session, key)

        '''
        2.4 斜杠命令处理
        如果消息以 / 开头且匹配内置命令（如 /reset, /help），直接派发命令并返回响应，不进入正常的 Agent 流程
        '''
        # Slash commands
        raw = msg.content.strip()
        ctx = CommandContext(msg=msg, session=session, key=key, raw=raw, loop=self)
        if result := await self.commands.dispatch(ctx):
            return result

        '''
        2.5 根据 token 压缩（再次）
        再次检查 token 数，必要时进行摘要压缩（注意 pending 是摘要内容，会在构建消息时作为系统提示的一部分）
        '''
        await self.consolidator.maybe_consolidate_by_tokens(
            session,
            session_summary=pending,
        )

        '''
        2.6 设置工具上下文并重置 MessageTool 状态
        为工具注入当前对话的路由信息。
        重置 MessageTool 的 _sent_in_turn 标志（标记本轮是否已发送过消息），以便后续判断是否需要抑制空响应。
        '''
        self._set_tool_context(msg.channel, msg.chat_id, msg.metadata.get("message_id"))
        if message_tool := self.tools.get("message"):
            if isinstance(message_tool, MessageTool):
                message_tool.start_turn()

        '''
        2.7 构建初始消息列表
        获取完整历史。
        调用 ContextBuilder 组装系统提示、历史、运行时上下文、当前用户消息（包含媒体）等。
        '''
        history = session.get_history(max_messages=0)

        initial_messages = self.context.build_messages(
            history=history,
            current_message=msg.content,
            session_summary=pending,
            media=msg.media if msg.media else None,
            channel=msg.channel,
            chat_id=msg.chat_id,
        )

        '''
        2.8 定义进度和重试回调
        _bus_progress 将 Agent 的思考过程或工具提示通过消息总线推送到前端（用于显示“正在思考...”）。
        _on_retry_wait 在 LLM 调用因限流等原因需要重试时通知用户。
        '''
        async def _bus_progress(content: str, *, tool_hint: bool = False) -> None:
            meta = dict(msg.metadata or {})
            meta["_progress"] = True
            meta["_tool_hint"] = tool_hint
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        async def _on_retry_wait(content: str) -> None:
            meta = dict(msg.metadata or {})
            meta["_retry_wait"] = True
            await self.bus.publish_outbound(
                OutboundMessage(
                    channel=msg.channel,
                    chat_id=msg.chat_id,
                    content=content,
                    metadata=meta,
                )
            )

        '''
        2.9 提前持久化用户消息（关键！）
        为什么需要提前保存：如果进程在 Agent 执行过程中崩溃（如 OOM、SIGKILL），
        之前保存的运行时检查点只保留了 assistant 和工具的状态，而没有保存触发本次处理的用户消息。
        提前将用户消息写入会话并设置 _PENDING_USER_TURN_KEY 标记，即使崩溃后恢复，也能从历史中看到用户说了什么，
        并自动添加错误提示。

        user_persisted_early 标记用于后面 _save_turn 时跳过已保存的用户消息。
        '''
        user_persisted_early = False
        if isinstance(msg.content, str) and msg.content.strip():
            session.add_message("user", msg.content)
            self._mark_pending_user_turn(session)
            self.sessions.save(session)
            user_persisted_early = True

        '''
        2.10 运行 Agent 循环
        调用 _run_agent_loop，它会使用 AgentRunner 执行实际的 LLM 推理和工具循环，
        并支持中轮注入（通过 pending_queue）。

        返回最终内容、所有消息、停止原因等
        '''
        final_content, _, all_msgs, stop_reason, had_injections = await self._run_agent_loop(
            initial_messages,
            on_progress=on_progress or _bus_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
            on_retry_wait=_on_retry_wait,
            session=session,
            channel=msg.channel,
            chat_id=msg.chat_id,
            message_id=msg.metadata.get("message_id"),
            pending_queue=pending_queue,
        )

        '''
        2.11 确保最终内容非空
        如果最终内容为空（例如 LLM 没有输出任何文本），使用一个占位符消息（通常为 "Done."）
        '''
        if final_content is None or not final_content.strip():
            final_content = EMPTY_FINAL_RESPONSE_MESSAGE

        '''
        2.12 保存本次对话轮次
        -save_skip 计算需要跳过的历史消息数量：原有的 history 长度，加上提前保存的用户消息（如果 user_persisted_early 为真）。
        -_save_turn 将 all_msgs 中从 skip 开始的条目追加到会话历史中（会自动截断过长的工具结果、移除敏感内容）。
        -清除 pending user turn 标记和运行时检查点，保存会话。
        -再次调度后台压缩（确保对话不会无限增长）。
        '''
        save_skip = 1 + len(history) + (1 if user_persisted_early else 0)
        self._save_turn(session, all_msgs, save_skip)
        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        self.sessions.save(session)
        self._schedule_background(self.consolidator.maybe_consolidate_by_tokens(session))

        '''
        2.13 MessageTool 的响应抑制
        MessageTool 允许 Agent 主动向用户发送消息（例如通过 send_message 工具）。
        如果在本轮中已经发送过消息，且没有中轮注入（had_injections 为 False）或者最终原因只是空响应，
        那么就应该抑制本次最终的 OutboundMessage，因为用户已经收到过消息了，再发一个空响应或重复内容会冗余。

        如果发生了中轮注入（用户中途插话），即使 MessageTool 发过消息，也可能需要发送最终回复来回应插话，因此不抑制。
        '''
        if (mt := self.tools.get("message")) and isinstance(mt, MessageTool) and mt._sent_in_turn:
            if not had_injections or stop_reason == "empty_final_response":
                return None

        '''
        2.14 记录日志并返回
        -截断长内容用于日志。
        -如果本次使用了流式输出且没有发生错误，在元数据中标记 _streamed: True（前端可根据此标记决定是否已实时显示过内容）。
        -返回最终的出站消息。
        '''
        preview = final_content[:120] + "..." if len(final_content) > 120 else final_content
        logger.info("Response to {}:{}: {}", msg.channel, msg.sender_id, preview)

        meta = dict(msg.metadata or {})
        if on_stream is not None and stop_reason != "error":
            meta["_streamed"] = True
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=final_content,
            metadata=meta,
        )

    def _sanitize_persisted_blocks(
            self,
            content: list[dict[str, Any]],
            *,
            should_truncate_text: bool = False,
            drop_runtime: bool = False,
    ) -> list[dict[str, Any]]:
        """清洗多模态内容块.
        在保存消息内容（特别是多模态消息或工具结果）之前，移除不适宜持久化的信息，例如 base64 图片数据、运行时上下文文本块，并可选地截断长文本

        处理逻辑:
        1.删除运行时上下文块（如果 drop_runtime=True）
        若块类型为 text，文本内容以 ContextBuilder._RUNTIME_CONTEXT_TAG 开头，则跳过该块（不加入结果）。
        2.替换 base64 图片为占位符
        若块类型为 image_url 且 URL 以 data:image/ 开头（表示嵌入的 base64 图片），则提取原始文件路径（保存在 _meta.path），生成占位符文本（如 [Image: path]）替换原块。
        3.处理文本块（类型 text）
        若 should_truncate_text=True 且文本长度超过 self.max_tool_result_chars，则调用 truncate_text_fn 截断（保留首尾部分）。
        更新文本后加入结果。
        4.其他类型块：原样保留。

        返回值：清洗后的块列表，可直接存入会话历史。
        """
        filtered: list[dict[str, Any]] = []
        for block in content:
            if not isinstance(block, dict):
                filtered.append(block)
                continue

            if (
                    drop_runtime
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                    and block["text"].startswith(ContextBuilder._RUNTIME_CONTEXT_TAG)
            ):
                continue

            if block.get("type") == "image_url" and block.get("image_url", {}).get(
                    "url", ""
            ).startswith("data:image/"):
                path = (block.get("_meta") or {}).get("path", "")
                filtered.append({"type": "text", "text": image_placeholder_text(path)})
                continue

            if block.get("type") == "text" and isinstance(block.get("text"), str):
                text = block["text"]
                if should_truncate_text and len(text) > self.max_tool_result_chars:
                    text = truncate_text_fn(text, self.max_tool_result_chars)
                filtered.append({**block, "text": text})
                continue

            filtered.append(block)

        return filtered

    def _save_turn(self, session: Session, messages: list[dict], skip: int) -> None:
        """
        保存一次对话轮次的新消息
        将一次 Agent 运行（_run_agent_loop）产生的新消息追加到会话历史中，并在保存前进行必要的清理和转换

        核心步骤:

        1.跳过空的 assistant 消息
        某些 LLM 会返回无内容、无工具调用的 assistant 消息，这类消息会破坏对话结构，直接丢弃。

        2.处理工具结果（role == "tool"）
        若 content 是字符串且过长 → 截断字符串。
        若 content 是列表（多模态）→ 调用 _sanitize_persisted_blocks(content, should_truncate_text=True) 清洗；清洗后为空则跳过本条消息。

        3.处理用户消息（role == "user"）
        字符串形式：如果内容以 _RUNTIME_CONTEXT_TAG 开头，说明包含了运行时上下文（如当前时间、工作目录）。通过查找结束标记 _RUNTIME_CONTEXT_END 移除该上下文块，只保留用户真正输入的内容。如果移除后无内容，跳过。
        列表形式：调用 _sanitize_persisted_blocks(content, drop_runtime=True) 清洗，同时删除运行时上下文块；清洗后为空则跳过。

        4.为消息添加时间戳（若缺失）并追加到 session.messages
        最后更新 session.updated_at
        """
        from datetime import datetime

        for m in messages[skip:]:
            entry = dict(m)
            role, content = entry.get("role"), entry.get("content")
            if role == "assistant" and not content and not entry.get("tool_calls"):
                continue  # skip empty assistant messages — they poison session context
            if role == "tool":
                if isinstance(content, str) and len(content) > self.max_tool_result_chars:
                    entry["content"] = truncate_text_fn(content, self.max_tool_result_chars)
                elif isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, should_truncate_text=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            elif role == "user":
                if isinstance(content, str) and content.startswith(ContextBuilder._RUNTIME_CONTEXT_TAG):
                    # Strip the entire runtime-context block (including any session summary).
                    # The block is bounded by _RUNTIME_CONTEXT_TAG and _RUNTIME_CONTEXT_END.
                    end_marker = ContextBuilder._RUNTIME_CONTEXT_END
                    end_pos = content.find(end_marker)
                    if end_pos >= 0:
                        after = content[end_pos + len(end_marker):].lstrip("\n")
                        if after:
                            entry["content"] = after
                        else:
                            continue
                    else:
                        # Fallback: no end marker found, strip the tag prefix
                        after_tag = content[len(ContextBuilder._RUNTIME_CONTEXT_TAG):].lstrip("\n")
                        if after_tag.strip():
                            entry["content"] = after_tag
                        else:
                            continue
                if isinstance(content, list):
                    filtered = self._sanitize_persisted_blocks(content, drop_runtime=True)
                    if not filtered:
                        continue
                    entry["content"] = filtered
            entry.setdefault("timestamp", datetime.now().isoformat())
            session.messages.append(entry)
        session.updated_at = datetime.now()

    def _persist_subagent_followup(self, session: Session, msg: InboundMessage) -> bool:
        """
        持久化子代理结果
        在系统消息分支中，将子代理返回的结果作为一条 assistant 消息提前写入会话历史，
        确保在后续 ContextBuilder 构建上下文时不会因为相邻消息合并而丢失子代理的输出。

        逻辑:
        1.若无消息内容，返回 False。
        2.从消息元数据中获取 subagent_task_id。
        3.去重检查：如果会话历史中已存在相同 subagent_task_id 且 injected_event == "subagent_result" 的消息，则返回 False，避免重复插入（例如消息重放）。
        4.调用 session.add_message 添加一条 assistant 消息，内容为子代理的输出，并附上元数据：
            -sender_id="subagent"
            -injected_event="subagent_result"
            -subagent_task_id=task_id
        5.返回 True 表示已添加。
        """
        if not msg.content:
            return False
        task_id = msg.metadata.get("subagent_task_id") if isinstance(msg.metadata, dict) else None
        if task_id and any(
                m.get("injected_event") == "subagent_result" and m.get("subagent_task_id") == task_id
                for m in session.messages
        ):
            return False
        session.add_message(
            "assistant",
            msg.content,
            sender_id=msg.sender_id,
            injected_event="subagent_result",
            subagent_task_id=task_id,
        )
        return True

    def _set_runtime_checkpoint(self, session: Session, payload: dict[str, Any]) -> None:
        """将在执行工具调用过程中得到的中间状态（未完成的 assistant 消息、已完成的工具结果、待执行的工具调用）保存到会话元数据中"""
        session.metadata[self._RUNTIME_CHECKPOINT_KEY] = payload
        self.sessions.save(session)

    def _mark_pending_user_turn(self, session: Session) -> None:
        """在刚将用户消息存入会话历史，但尚未开始 Agent 处理时设置此标记。若此时进程崩溃，恢复时能知道“有一条用户消息已经存储，但未得到回复”"""
        session.metadata[self._PENDING_USER_TURN_KEY] = True

    def _clear_pending_user_turn(self, session: Session) -> None:
        """Agent 处理完成（无论成功或失败）后清除挂起标记"""
        session.metadata.pop(self._PENDING_USER_TURN_KEY, None)

    def _clear_runtime_checkpoint(self, session: Session) -> None:
        """会话正常结束（回复完整发送）后，清除检查点，避免残留状态影响下一次对话"""
        if self._RUNTIME_CHECKPOINT_KEY in session.metadata:
            session.metadata.pop(self._RUNTIME_CHECKPOINT_KEY, None)

    @staticmethod
    def _checkpoint_message_key(message: dict[str, Any]) -> tuple[Any, ...]:
        """从一条消息字典中提取关键字段，生成一个用于比较两条消息是否相同的元组"""
        return (
            message.get("role"),
            message.get("content"),
            message.get("tool_call_id"),
            message.get("name"),
            message.get("tool_calls"),
            message.get("reasoning_content"),
            message.get("thinking_blocks"),
        )

    def _restore_runtime_checkpoint(self, session: Session) -> bool:
        """
        将之前保存的检查点中的消息（未完成的 assistant 回复、已完成的工具结果、以及针对未执行工具的失败占位符）
        物化到会话历史中，使下一次请求能够接续上次中断的位置
        逻辑分解
        1.获取检查点数据
        -checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        -若不存在或不是字典，返回 False。

        2.提取三个部分
        -assistant_message：原本的 assistant 消息（可能带 tool_calls）。
        -completed_tool_results：已完成工具的结果列表（每条是一个角色为 tool 的消息）。
        -pending_tool_calls：尚未执行的工具调用列表。

        3.构建待恢复的消息列表 restored_messages
        -若存在 assistant_message，添加一条副本，并加上时间戳。
        -对于 completed_tool_results 中的每条消息，同样添加副本+时间戳。
        -对于 pending_tool_calls 中的每个工具调用，生成一条模拟的工具错误消息：
        {"role": "tool", "tool_call_id": ..., "name": ..., "content": "Error: Task interrupted before this tool finished."}
        这是因为工具尚未执行，无法获得真实结果；插入此错误消息可让 LLM 知道该工具没有成功运行，避免后续调用依赖它。

        4.去重：避免重复追加已存在于历史的消息
        -计算 max_overlap = min(len(session.messages), len(restored_messages))
        -从大往小尝试，找到最大的 size 使得 session.messages[-size:] 与 restored_messages[:size] 在内容上完全一致（利用 _checkpoint_message_key 比较）。
        -最后只追加 restored_messages[overlap:] 部分。

        5.清理标记
        -调用 _clear_pending_user_turn(session) 和 _clear_runtime_checkpoint(session)。
        -返回 True 表示已恢复。
        """
        from datetime import datetime

        checkpoint = session.metadata.get(self._RUNTIME_CHECKPOINT_KEY)
        if not isinstance(checkpoint, dict):
            return False

        assistant_message = checkpoint.get("assistant_message")
        completed_tool_results = checkpoint.get("completed_tool_results") or []
        pending_tool_calls = checkpoint.get("pending_tool_calls") or []

        restored_messages: list[dict[str, Any]] = []
        if isinstance(assistant_message, dict):
            restored = dict(assistant_message)
            restored.setdefault("timestamp", datetime.now().isoformat())
            restored_messages.append(restored)
        for message in completed_tool_results:
            if isinstance(message, dict):
                restored = dict(message)
                restored.setdefault("timestamp", datetime.now().isoformat())
                restored_messages.append(restored)
        for tool_call in pending_tool_calls:
            if not isinstance(tool_call, dict):
                continue
            tool_id = tool_call.get("id")
            name = ((tool_call.get("function") or {}).get("name")) or "tool"
            restored_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "name": name,
                    "content": "Error: Task interrupted before this tool finished.",
                    "timestamp": datetime.now().isoformat(),
                }
            )

        overlap = 0
        max_overlap = min(len(session.messages), len(restored_messages))
        for size in range(max_overlap, 0, -1):
            existing = session.messages[-size:]
            restored = restored_messages[:size]
            if all(
                    self._checkpoint_message_key(left) == self._checkpoint_message_key(right)
                    for left, right in zip(existing, restored)
            ):
                overlap = size
                break
        session.messages.extend(restored_messages[overlap:])

        self._clear_pending_user_turn(session)
        self._clear_runtime_checkpoint(session)
        return True

    def _restore_pending_user_turn(self, session: Session) -> bool:
        """处理“用户消息已保存，但进程在 Agent 开始运行前崩溃”的情形，自动添加一条错误提示，避免用户消息被静默忽略"""
        from datetime import datetime

        if not session.metadata.get(self._PENDING_USER_TURN_KEY):
            return False

        if session.messages and session.messages[-1].get("role") == "user":
            session.messages.append(
                {
                    "role": "assistant",
                    "content": "Error: Task interrupted before a response was generated.",
                    "timestamp": datetime.now().isoformat(),
                }
            )
            session.updated_at = datetime.now()

        self._clear_pending_user_turn(session)
        return True

    async def process_direct(
            self,
            content: str,
            session_key: str = "cli:direct",
            channel: str = "cli",
            chat_id: str = "direct",
            media: list[str] | None = None,
            on_progress: Callable[[str], Awaitable[None]] | None = None,
            on_stream: Callable[[str], Awaitable[None]] | None = None,
            on_stream_end: Callable[..., Awaitable[None]] | None = None,
    ) -> OutboundMessage | None:
        """提供一个简化的 API，允许外部代码（如测试脚本、命令行工具、HTTP 服务）直接向 Agent 发送一条用户消息，
            并获得响应，而无需构建 InboundMessage 对象或启动完整的 run() 循环"""
        await self._connect_mcp()
        msg = InboundMessage(
            channel=channel, sender_id="user", chat_id=chat_id,
            content=content, media=media or [],
        )
        return await self._process_message(
            msg,
            session_key=session_key,
            on_progress=on_progress,
            on_stream=on_stream,
            on_stream_end=on_stream_end,
        )
