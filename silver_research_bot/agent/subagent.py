"""后台任务执行的子代理管理器"""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from silver_research_bot.agent.hook import AgentHook, AgentHookContext
from silver_research_bot.utils.prompt_templates import render_template
from silver_research_bot.agent.runner import AgentRunSpec, AgentRunner
from silver_research_bot.agent.skills import BUILTIN_SKILLS_DIR
from silver_research_bot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from silver_research_bot.agent.tools.registry import ToolRegistry
from silver_research_bot.agent.tools.search import GlobTool, GrepTool
from silver_research_bot.agent.tools.shell import ExecTool
from silver_research_bot.agent.tools.web import WebFetchTool, WebSearchTool
from silver_research_bot.bus.events import InboundMessage
from silver_research_bot.bus.queue import MessageBus
from silver_research_bot.config.schema import ExecToolConfig, WebToolsConfig
from silver_research_bot.providers.base import LLMProvider


@dataclass(slots=True)
class SubagentStatus:
    """子代理实时状态."""
    task_id: str
    '唯一标识符'
    label: str
    '简短标签（用于显示）'
    task_description: str
    '原始任务描述'
    started_at: float
    '启动时间（单调时钟）'
    phase: str = "initializing"
    '当前阶段（initializing, awaiting_tools, tools_completed, final_response, done, error）'
    iteration: int = 0
    '已执行的迭代次数（ReAct 轮数）'
    tool_events: list = field(default_factory=list)
    '工具调用事件列表'
    usage: dict = field(default_factory=dict)
    '累计 token 使用量'
    stop_reason: str | None = None
    '结束原因（如 completed, max_iterations, tool_error）'
    error: str | None = None
    '错误信息（如果失败）'


class _SubagentHook(AgentHook):
    """子代理执行钩子——记录工具调用并更新状态。"""

    def __init__(self, task_id: str, status: SubagentStatus | None = None) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        """在子代理即将执行一组工具调用之前，将每个工具的名称和参数以 JSON 格式写入调试日志"""
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )

    async def after_iteration(self, context: AgentHookContext) -> None:
        """在每轮 ReAct 迭代结束时（即 LLM 响应处理完毕，
        可能刚生成最终答案或即将执行下一轮工具），将执行进度同步到 SubagentStatus 对象中"""
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)


class SubagentManager:
    """管理后台子代理执行"""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
    ):
        self.provider = provider
        'LLM 服务提供者实例'
        self.workspace = workspace
        '工作目录路径'
        self.bus = bus
        '消息总线实例'
        self.model = model or provider.get_default_model()
        '使用的模型名称，未指定时使用提供者默认模型'
        self.web_config = web_config or WebToolsConfig()
        '网页工具配置，未指定时使用默认配置'
        self.max_tool_result_chars = max_tool_result_chars
        '工具返回结果的最大字符数'
        self.exec_config = exec_config or ExecToolConfig()
        '执行工具配置，未指定时使用默认配置'
        self.restrict_to_workspace = restrict_to_workspace
        '是否限制操作在工作目录内'
        self.disabled_skills = set(disabled_skills or [])
        '禁用的技能集合'
        self.runner = AgentRunner(provider)
        '智能体执行器实例'
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        '正在运行的异步任务字典'
        self._task_statuses: dict[str, SubagentStatus] = {}
        '子任务状态字典'
        self._session_tasks: dict[str, set[str]] = {}
        '会话关联的任务ID集合字典'

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """创建子代理在后台执行任务"""
        '''1.生成任务短 ID（8 字符）'''
        task_id = str(uuid.uuid4())[:8]

        '''2.创建状态对象并存储'''
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        '''3.创建后台任务执行 _run_subagent'''
        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, status)
        )
        self._running_tasks[task_id] = bg_task
        '''4.记录 _session_tasks 以便后续按会话取消'''
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        '''5.添加完成回调自动清理'''
        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        '''6.立即返回确认信息（非阻塞）'''
        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus,
    ) -> None:
        """执行子代理任务并报告结果."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)

        async def _on_checkpoint(payload: dict) -> None:
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)

        try:
            '''
            1.构建子代理工具集
            限制子代理的工具集：没有 MessageTool 和 SpawnTool，
            只提供文件操作、搜索、Web、Shell 等基本工具。这样避免了子代理再创建子代理（嵌套）
            '''
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
            extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else None
            tools.register(ReadFileTool(workspace=self.workspace, allowed_dir=allowed_dir, extra_allowed_dirs=extra_read))
            tools.register(WriteFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(EditFileTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(ListDirTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GlobTool(workspace=self.workspace, allowed_dir=allowed_dir))
            tools.register(GrepTool(workspace=self.workspace, allowed_dir=allowed_dir))
            if self.exec_config.enable:
                tools.register(ExecTool(
                    working_dir=str(self.workspace),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                ))
            if self.web_config.enable:
                tools.register(WebSearchTool(config=self.web_config.search, proxy=self.web_config.proxy))
                tools.register(WebFetchTool(proxy=self.web_config.proxy))

            '''3.构建系统提示（_build_subagent_prompt）：包含时间上下文、工作区信息、技能列表等'''
            system_prompt = self._build_subagent_prompt()

            '''4.构建消息列表'''
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            '''5.使用 AgentRunner.run 执行，并传入自定义钩子 _SubagentHook 和检查点回调，以便更新状态'''
            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=15,
                max_tool_result_chars=self.max_tool_result_chars,
                hook=_SubagentHook(task_id, status),
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=True,
                checkpoint_callback=_on_checkpoint,
            ))
            status.phase = "done"
            status.stop_reason = result.stop_reason

            '''6.根据停止原因处理结果'''
            # 6.1 tool_error：提取部分进度并作为错误返回
            if result.stop_reason == "tool_error":
                status.tool_events = list(result.tool_events)
                await self._announce_result(
                    task_id, label, task,
                    self._format_partial_progress(result),
                    origin, "error",
                )
            # 6.2 error：直接返回错误消息
            elif result.stop_reason == "error":
                await self._announce_result(
                    task_id, label, task,
                    result.error or "Error: subagent execution failed.",
                    origin, "error",
                )
            # 6.3 其他（正常完成）：返回最终内容
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                logger.info("Subagent [{}] completed successfully", task_id)
                await self._announce_result(task_id, label, task, final_result, origin, "ok")

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._announce_result(task_id, label, task, f"Error: {e}", origin, "error")

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
    ) -> None:
        """通过消息总线将子代理的结果通知给主代理"""

        '''1.渲染模板生成结果文本（包含任务、标签、结果）'''
        status_text = "completed successfully" if status == "ok" else "failed"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            task=task,
            result=result,
        )

        '''
        2.构造系统消息
        将子代理执行结果作为系统消息注入以触发主代理。
        使用 session_key_override 与主代理的有效会话密钥（用于处理统一会话）保持一致，
        从而确保结果被路由到正确的待处理队列（中途注入），而不是作为竞争性的独立任务进行分派。
        
        metadata 携带 injected_event 和任务 ID，便于父 Agent 去重或标记
        '''
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        """用于在子代理因工具错误提前终止时，生成一个可读的部分进度报告。
        它从 AgentRunResult 中提取已完成的和失败的工具事件，并格式化为文本"""
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self) -> str:
        """
        构建子代理专用的系统提示字符串，注入时间上下文、工作区路径和可用技能摘要

        运行时上下文：ContextBuilder._build_runtime_context(None, None) 生成包含当前时间的基础块（不包含 channel/chat_id 等信息）。
        技能摘要：通过 SkillsLoader 加载工作区中的技能（排除 disabled_skills），并生成可供 LLM 使用的技能列表摘要。
        模板渲染：使用 Jinja2 模板 agent/subagent_system.md，填入时间上下文、工作区路径和技能摘要。模板内容通常包含子代理的角色定义、可用工具说明、工作区规则等
        """
        from silver_research_bot.agent.context import ContextBuilder
        from silver_research_bot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        skills_summary = SkillsLoader(
            self.workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """
        取消指定父会话所创建的所有子代理任务，并返回取消的数量

        流程：
        1.从 self._session_tasks 中获取该会话下所有子代理的任务 ID 集合。
        2.过滤出尚未完成的任务（not done()）并收集对应的 asyncio.Task 对象。
        3.对每个任务调用 cancel()。
        4.如果有取消的任务，使用 asyncio.gather 等待它们真正结束（return_exceptions=True 避免异常传播）。
        5.返回取消的任务总数
        """
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """返回当前所有运行中的子代理数量（无论属于哪个会话）."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """获取指定会话下的所有任务 ID 集合"""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )
