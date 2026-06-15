"""工具使用 agents的共享执行循环"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import inspect
from pathlib import Path
from typing import Any

from loguru import logger

from silver_research_bot.agent.hook import AgentHook, AgentHookContext
from silver_research_bot.utils.prompt_templates import render_template
from silver_research_bot.agent.tools.registry import ToolRegistry
from silver_research_bot.providers.base import LLMProvider, ToolCallRequest
from silver_research_bot.utils.helpers import (
    build_assistant_message,
    estimate_message_tokens,
    estimate_prompt_tokens_chain,
    find_legal_message_start,
    maybe_persist_tool_result,
    truncate_text,
)
from silver_research_bot.utils.runtime import (
    EMPTY_FINAL_RESPONSE_MESSAGE,
    build_finalization_retry_message,
    build_length_recovery_message,
    ensure_nonempty_tool_result,
    is_blank_text,
    repeated_external_lookup_error,
)

_DEFAULT_ERROR_MESSAGE = "Sorry, I encountered an error calling the AI model."
'LLM 调用失败时返回给用户的默认错误提示。'
_PERSISTED_MODEL_ERROR_PLACEHOLDER = "[Assistant reply unavailable due to model error.]"
'当模型调用出错时，在持久化的会话历史中占位的内容（避免丢失对话结构）'
_MAX_EMPTY_RETRIES = 2
'当 LLM 返回空内容（无文本、无工具调用）时，最多重试的次数'
_MAX_LENGTH_RECOVERIES = 3
'当模型因输出超长而失败时，尝试缩短上下文后重试的次数'
_MAX_INJECTIONS_PER_TURN = 3
'单次 ReAct 迭代中最多从中轮注入队列中取出的消息数量（防止上下文爆炸）'
_MAX_INJECTION_CYCLES = 5
'在因注入新消息而导致再次需要 LLM 调用时，最多循环的次数（防止无限循环）'
_SNIP_SAFETY_BUFFER = 1024
'在截断上下文时保留的额外 token 缓冲量（确保不超过模型限制）'
_MICROCOMPACT_KEEP_RECENT = 10
'微压缩（micro‑compact）时保留的最近消息条数'
_MICROCOMPACT_MIN_CHARS = 500
'触发微压缩所需的最小字符数（避免对过短消息进行压缩）'
_COMPACTABLE_TOOLS = frozenset({
    "read_file", "exec", "grep", "glob",
    "web_search", "web_fetch", "list_dir",
})
'一组工具名称，其输出结果在压缩时可以安全地丢弃或简化（例如只读工具）'
_BACKFILL_CONTENT = "[Tool result unavailable — call was interrupted or lost]"
'当某个工具调用因中断而丢失结果时，用作回填的错误提示消息内容'


@dataclass(slots=True)
class AgentRunSpec:
    """Agent 单次执行的配置参数"""

    initial_messages: list[dict[str, Any]]
    '发送给 LLM 的初始消息列表（已包含历史、系统提示等）'
    tools: ToolRegistry
    '可用的工具注册表'
    model: str
    '使用的模型名称'
    max_iterations: int
    '最大 ReAct 迭代次数（工具调用轮数）'
    max_tool_result_chars: int
    '工具返回结果的最大字符数（超长截断）'
    temperature: float | None = None
    '采样温度（控制随机性）'
    max_tokens: int | None = None
    'LLM 生成的最大 token 数'
    reasoning_effort: str | None = None
    '推理强度（某些模型支持，如 “low”/“high”）'
    hook: AgentHook | None = None
    '钩子对象，用于监听事件（流式、进度等）'
    error_message: str | None = _DEFAULT_ERROR_MESSAGE
    'LLM 错误时的返回信息'
    max_iterations_message: str | None = None
    '达到最大迭代次数时的回复（可选）'
    concurrent_tools: bool = False
    '是否并发执行工具调用'
    fail_on_tool_error: bool = False
    '工具执行失败时是否立即终止整个会话'
    workspace: Path | None = None
    '工作目录（用于工具路径解析）'
    session_key: str | None = None
    '会话标识（用于检查点存储）'
    context_window_tokens: int | None = None
    'LLM 上下文窗口大小（用于动态压缩）'
    context_block_limit: int | None = None
    '消息块数量限制（某些 API）'
    provider_retry_mode: str = "standard"
    'LLM 提供者的重试模式（标准/指数退避等）'
    progress_callback: Any | None = None
    '进度回调（接收思考内容和工具提示）'
    retry_wait_callback: Any | None = None
    '重试等待回调（通知用户正在重试）'
    checkpoint_callback: Any | None = None
    '检查点持久化回调（保存中断状态）'
    injection_callback: Any | None = None
    '中轮注入回调（从队列中取出新消息）'

@dataclass(slots=True)
class AgentRunResult:
    """单次执行的输出结果"""
    final_content: str | None
    '最终返回给用户的文本（可能为 None）'
    messages: list[dict[str, Any]]
    '本次执行产生的完整消息历史（包括中间轮次）'
    tools_used: list[str] = field(default_factory=list)
    '实际调用过的工具名称列表'
    usage: dict[str, int] = field(default_factory=dict)
    'LLM token 使用量（prompt/completion）'
    stop_reason: str = "completed"
    '停止原因（"completed", "max_iterations", "error", "empty_final_response"）'
    error: str | None = None
    '若发生错误，记录错误信息'
    tool_events: list[dict[str, str]] = field(default_factory=list)
    '工具调用事件（用于调试/日志）'
    had_injections: bool = False
    '本轮执行中是否发生过中轮注入'


class AgentRunner:
    """运行一个支持工具的 LLM 循环，无需担心产品层面的问题"""

    def __init__(self, provider: LLMProvider):
        self.provider = provider

    @staticmethod
    def _merge_message_content(left: Any, right: Any) -> str | list[dict[str, Any]]:
        """将两个消息的 content 字段合并成一个，支持字符串和列表（多模态块）两种格式。
        当连续两条用户消息需要合并为一条（因为模型要求角色交替）时，调用此函数将它们的 content 合并。
        """
        if isinstance(left, str) and isinstance(right, str):
            return f"{left}\n\n{right}" if left else right

        def _to_blocks(value: Any) -> list[dict[str, Any]]:
            if isinstance(value, list):
                return [
                    item if isinstance(item, dict) else {"type": "text", "text": str(item)}
                    for item in value
                ]
            if value is None:
                return []
            return [{"type": "text", "text": str(value)}]

        return _to_blocks(left) + _to_blocks(right)

    @classmethod
    def _append_injected_messages(
        cls,
        messages: list[dict[str, Any]],
        injections: list[dict[str, Any]],
    ) -> None:
        """将注入的消息列表（来自 _drain_injections）追加到当前 messages 中，同时确保同一角色不会连续出现（尤其是 user 角色）"""
        for injection in injections:
            if (
                messages
                and injection.get("role") == "user"
                and messages[-1].get("role") == "user"
            ):
                merged = dict(messages[-1])
                merged["content"] = cls._merge_message_content(
                    merged.get("content"),
                    injection.get("content"),
                )
                messages[-1] = merged
                continue
            messages.append(injection)

    async def _try_drain_injections(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        assistant_message: dict[str, Any] | None,
        injection_cycles: int,
        *,
        phase: str = "after error",
        iteration: int | None = None,
    ) -> tuple[bool, int]:
        """
        处理待处理的注入。返回 (should_continue, updated_cycles)。

        尝试从 injection_callback 中取出待注入的消息，并将其插入到当前对话中。
        如果成功获取到至少一条消息，则返回 (True, 增加的循环计数)，指示调用者应继续当前迭代；
        否则返回 (False, 原计数)

        步骤:
        1.检查次数限制：如果 injection_cycles >= _MAX_INJECTION_CYCLES（默认 5），直接返回 (False, injection_cycles)，不再尝试。
        2.调用 _drain_injections 获取注入消息列表。
        3.若无消息，返回 (False, injection_cycles)。
        4.增加计数：injection_cycles += 1。
        5.如果提供了 assistant_message：
        -将其追加到 messages 中（因为注入的新消息应该出现在 assistant 回复之后）。
        -如果同时提供了 iteration，则调用 _emit_checkpoint 保存一个检查点（状态为 "final_response"，
        表示 assistant 消息已发出，但还没有执行新的工具调用）。
        6.调用 _append_injected_messages 将注入消息合并到 messages。
        7.记录日志，说明注入了多少条消息以及当前循环次数。
        8.返回 (True, injection_cycles) 表示应该继续迭代（因为新消息可能触发新的 LLM 调用）
        """
        if injection_cycles >= _MAX_INJECTION_CYCLES:
            return False, injection_cycles
        injections = await self._drain_injections(spec)
        if not injections:
            return False, injection_cycles
        injection_cycles += 1
        if assistant_message is not None:
            messages.append(assistant_message)
            if iteration is not None:
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "final_response",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [],
                    },
                )
        self._append_injected_messages(messages, injections)
        logger.info(
            "Injected {} follow-up message(s) {} ({}/{})",
            len(injections), phase, injection_cycles, _MAX_INJECTION_CYCLES,
        )
        return True, injection_cycles

    async def _drain_injections(self, spec: AgentRunSpec) -> list[dict[str, Any]]:
        """通过注入回调处理待处理的用户消息。

        返回经过规范化的用户消息（上限为``_MAX_INJECTIONS_PER_TURN``），若无消息可注入，则返回
        空列表。超出上限的消息会被记录到日志中，以避免它们被无声丢失。

        逻辑：
        1.若 spec.injection_callback 为空，返回空列表。
        2.使用 inspect.signature 检测回调函数是否接受 limit 参数：
            -如果接受，调用 callback(limit=_MAX_INJECTIONS_PER_TURN)（默认 3）。
            -否则调用 callback()。
        3.捕获异常并记录日志，保证主流程不因回调失败而崩溃。
        4.将回调返回的结果转换为标准的 {"role": "user", "content": <text>}：
            -如果已经是字典且 role="user" 且有 content，直接保留。
            -否则，尝试取 item.content 属性或转为字符串，并生成消息。
        5.截断：如果返回的消息数量超过 _MAX_INJECTIONS_PER_TURN，只取前 _MAX_INJECTIONS_PER_TURN 条，
        并记录警告（多出的会被丢弃，因为无法在一次迭代中处理太多）。
        6.返回标准化后的消息列表。
        """
        if spec.injection_callback is None:
            return []
        try:
            signature = inspect.signature(spec.injection_callback)
            accepts_limit = (
                "limit" in signature.parameters
                or any(
                    parameter.kind is inspect.Parameter.VAR_KEYWORD
                    for parameter in signature.parameters.values()
                )
            )
            if accepts_limit:
                items = await spec.injection_callback(limit=_MAX_INJECTIONS_PER_TURN)
            else:
                items = await spec.injection_callback()
        except Exception:
            logger.exception("injection_callback failed")
            return []
        if not items:
            return []
        injected_messages: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict) and item.get("role") == "user" and "content" in item:
                injected_messages.append(item)
                continue
            text = getattr(item, "content", str(item))
            if text.strip():
                injected_messages.append({"role": "user", "content": text})
        if len(injected_messages) > _MAX_INJECTIONS_PER_TURN:
            dropped = len(injected_messages) - _MAX_INJECTIONS_PER_TURN
            logger.warning(
                "Injection callback returned {} messages, capping to {} ({} dropped)",
                len(injected_messages), _MAX_INJECTIONS_PER_TURN, dropped,
            )
            injected_messages = injected_messages[:_MAX_INJECTIONS_PER_TURN]
        return injected_messages

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        hook = spec.hook or AgentHook()
        '钩子对象，用于监听Agent执行生命周期事件（流式输出、进度等）'
        messages = list(spec.initial_messages)
        '可变消息列表，存储本次执行的完整对话历史'
        final_content: str | None = None
        '最终返回给用户的文本内容'
        tools_used: list[str] = []
        '记录本次执行中实际调用过的工具名称列表'
        usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        '累计的token使用量（提示词和生成）'
        error: str | None = None
        '执行过程中发生的错误信息字符串'
        stop_reason = "completed"
        '循环停止原因，如"completed", "max_iterations", "error"等'
        tool_events: list[dict[str, str]] = []
        '工具调用事件列表（用于调试或前端展示）'
        external_lookup_counts: dict[str, int] = {}
        '外部工具查找次数统计（用于工具执行中的缓存或限流）'
        empty_content_retries = 0
        '空响应重试次数计数器'
        length_recovery_count = 0
        '输出长度截断恢复尝试次数计数器'
        had_injections = False
        '本轮执行是否发生过中轮注入（用户中途插入消息）'
        injection_cycles = 0
        '中轮注入循环次数（防止无限循环）'

        for iteration in range(spec.max_iterations):
            try:
                '''1. 上下文治理（Context Governance）在每次调用 LLM 之前，对当前消息列表进行一系列清理和优化：'''
                # 移除没有对应 assistant 工具调用消息的 tool 结果
                messages_for_model = self._drop_orphan_tool_results(messages)
                # 为已存在的 tool 调用补上缺失的结果（用占位符填充）
                messages_for_model = self._backfill_missing_tool_results(messages_for_model)
                # 对最近几条消息进行轻量压缩（如果内容过长）
                messages_for_model = self._microcompact(messages_for_model)
                # 根据 max_tool_result_chars 截断过长的工具结果
                messages_for_model = self._apply_tool_result_budget(spec, messages_for_model)
                # 如果消息 token 总数超过上下文窗口，从开头裁剪或压缩历史
                messages_for_model = self._snip_history(spec, messages_for_model)
                # 治理期间如果出现异常，会尝试最小修复（仅 drop 和 backfill），保证循环不被中断
                messages_for_model = self._drop_orphan_tool_results(messages_for_model)
                messages_for_model = self._backfill_missing_tool_results(messages_for_model)
            except Exception as exc:
                logger.warning(
                    "Context governance failed on turn {} for {}: {}; applying minimal repair",
                    iteration,
                    spec.session_key or "default",
                    exc,
                )
                try:
                    messages_for_model = self._drop_orphan_tool_results(messages)
                    messages_for_model = self._backfill_missing_tool_results(messages_for_model)
                except Exception:
                    messages_for_model = messages

            '''
            2. 钩子回调：before_iteration
            允许外部钩子（如流式输出、进度提示）在 LLM 请求前执行自定义逻辑
            '''
            context = AgentHookContext(iteration=iteration, messages=messages)
            await hook.before_iteration(context)

            '''3. 调用模型 返回 ModelResponse 对象，包含 content、tool_calls、finish_reason 等'''
            response = await self._request_model(spec, messages_for_model, hook, context)
            raw_usage = self._usage_dict(response.usage)
            context.response = response
            context.usage = dict(raw_usage)
            context.tool_calls = list(response.tool_calls)
            self._accumulate_usage(usage, raw_usage)

            '''4. 处理工具调用（response.should_execute_tools）'''
            if response.should_execute_tools:
                # 4.1 流式结束标记：如果钩子支持流式，调用 hook.on_stream_end(context, resuming=True) 告知前端准备开始工具执行。
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=True)

                # 4.2 构建 assistant 消息：包含 content（可能为空）和 tool_calls，追加到 messages
                assistant_message = build_assistant_message(
                    response.content or "",
                    tool_calls=[tc.to_openai_tool_call() for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )
                messages.append(assistant_message)
                # 4.3 更新工具使用列表：tools_used.extend(tc.name ...)
                tools_used.extend(tc.name for tc in response.tool_calls)
                # 4.4 保存检查点：_emit_checkpoint 保存当前未完成工具调用的状态（用于中断恢复）
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "awaiting_tools",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": [],
                        "pending_tool_calls": [tc.to_openai_tool_call() for tc in response.tool_calls],
                    },
                )

                await hook.before_execute_tools(context)

                # 4.5 执行工具
                results, new_events, fatal_error = await self._execute_tools(
                    spec,
                    response.tool_calls,
                    external_lookup_counts,
                )
                tool_events.extend(new_events)
                context.tool_results = list(results)
                context.tool_events = list(new_events)
                completed_tool_results: list[dict[str, Any]] = []
                # 4.6 将工具结果追加到 messages
                for tool_call, result in zip(response.tool_calls, results):
                    tool_message = {
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": tool_call.name,
                        "content": self._normalize_tool_result(
                            spec,
                            tool_call.id,
                            tool_call.name,
                            result,
                        ),
                    }
                    messages.append(tool_message)
                    completed_tool_results.append(tool_message)
                # 4.7 检查致命错误：如果某个工具执行失败且 fail_on_tool_error=True，会立即终止循环，生成错误消息。
                if fatal_error is not None:
                    error = f"Error: {type(fatal_error).__name__}: {fatal_error}"
                    final_content = error
                    stop_reason = "tool_error"
                    self._append_final_message(messages, final_content)
                    context.final_content = final_content
                    context.error = error
                    context.stop_reason = stop_reason
                    await hook.after_iteration(context)
                    should_continue, injection_cycles = await self._try_drain_injections(
                        spec, messages, None, injection_cycles,
                        phase="after tool error",
                    )
                    if should_continue:
                        had_injections = True
                        continue
                    break
                # 4.8 保存完成检查点：工具执行完成后，保存包含所有工具结果的检查点
                await self._emit_checkpoint(
                    spec,
                    {
                        "phase": "tools_completed",
                        "iteration": iteration,
                        "model": spec.model,
                        "assistant_message": assistant_message,
                        "completed_tool_results": completed_tool_results,
                        "pending_tool_calls": [],
                    },
                )
                empty_content_retries = 0
                length_recovery_count = 0
                # 4.9 中轮注入（本轮第一次）：调用 _try_drain_injections 检查是否有新消息插入，
                # 若有则 had_injections = True 并继续循环（不退出，让 LLM 再次看到新消息）
                _drained, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after tool execution",
                )
                if _drained:
                    had_injections = True
                # 4.10 钩子回调：hook.after_iteration(context)
                await hook.after_iteration(context)
                continue

            if response.has_tool_calls:
                logger.warning(
                    "Ignoring tool calls under finish_reason='{}' for {}",
                    response.finish_reason,
                    spec.session_key or "default",
                )

            '''5. 处理无工具调用的情况'''
            clean = hook.finalize_content(context, response.content)
            # 5.1 处理空内容重试：如果响应内容为空（经钩子过滤后），但 finish_reason != "error"，
            # 则尝试调用 _request_finalization_retry（一种更简单的 prompt）来获取最终回复，最多 _MAX_EMPTY_RETRIES 次
            if response.finish_reason != "error" and is_blank_text(clean):
                empty_content_retries += 1
                if empty_content_retries < _MAX_EMPTY_RETRIES:
                    logger.warning(
                        "Empty response on turn {} for {} ({}/{}); retrying",
                        iteration,
                        spec.session_key or "default",
                        empty_content_retries,
                        _MAX_EMPTY_RETRIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=False)
                    await hook.after_iteration(context)
                    continue
                logger.warning(
                    "Empty response on turn {} for {} after {} retries; attempting finalization",
                    iteration,
                    spec.session_key or "default",
                    empty_content_retries,
                )
                if hook.wants_streaming():
                    await hook.on_stream_end(context, resuming=False)
                response = await self._request_finalization_retry(spec, messages_for_model)
                retry_usage = self._usage_dict(response.usage)
                self._accumulate_usage(usage, retry_usage)
                raw_usage = self._merge_usage(raw_usage, retry_usage)
                context.response = response
                context.usage = dict(raw_usage)
                context.tool_calls = list(response.tool_calls)
                clean = hook.finalize_content(context, response.content)

            # 5.2 处理长度截断：如果 finish_reason == "length" 且内容非空，说明输出被截断。
            # 此时追加一条“继续”消息（build_length_recovery_message），让 LLM 可以继续输出，最多 _MAX_LENGTH_RECOVERIES 次。
            if response.finish_reason == "length" and not is_blank_text(clean):
                length_recovery_count += 1
                if length_recovery_count <= _MAX_LENGTH_RECOVERIES:
                    logger.info(
                        "Output truncated on turn {} for {} ({}/{}); continuing",
                        iteration,
                        spec.session_key or "default",
                        length_recovery_count,
                        _MAX_LENGTH_RECOVERIES,
                    )
                    if hook.wants_streaming():
                        await hook.on_stream_end(context, resuming=True)
                    messages.append(build_assistant_message(
                        clean,
                        reasoning_content=response.reasoning_content,
                        thinking_blocks=response.thinking_blocks,
                    ))
                    messages.append(build_length_recovery_message())
                    await hook.after_iteration(context)
                    continue

            # 5.3 构造 assistant 消息：如果最终获得了有效内容，构建 assistant 消息（可能为空，留待下一步处理）
            assistant_message: dict[str, Any] | None = None
            if response.finish_reason != "error" and not is_blank_text(clean):
                assistant_message = build_assistant_message(
                    clean,
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                )

            # 5.4 中轮注入检查：这是关键点 —— 在最终回复之前先检查是否有注入的新消息。
            # 如果有，将当前的 assistant_message 提前追加到 messages 中（保持时序），
            # 然后通过 _append_injected_messages 将注入消息合并进去，设置 should_continue=True 并继续循环。
            # 这样最新的用户追问可以在本轮被 LLM 看见，而不是等下一轮
            should_continue, injection_cycles = await self._try_drain_injections(
                spec, messages, assistant_message, injection_cycles,
                phase="after final response",
                iteration=iteration,
            )
            if should_continue:
                had_injections = True

            if hook.wants_streaming():
                await hook.on_stream_end(context, resuming=should_continue)

            if should_continue:
                await hook.after_iteration(context)
                continue

            # 5.5 处理错误或空最终响应：
            # 若 finish_reason == "error"：返回错误消息并终止。
            # 若最终内容为空：返回占位符 EMPTY_FINAL_RESPONSE_MESSAGE 并终止。
            if response.finish_reason == "error":
                final_content = clean or spec.error_message or _DEFAULT_ERROR_MESSAGE
                stop_reason = "error"
                error = final_content
                self._append_model_error_placeholder(messages)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after LLM error",
                )
                if should_continue:
                    had_injections = True
                    continue
                break
            if is_blank_text(clean):
                final_content = EMPTY_FINAL_RESPONSE_MESSAGE
                stop_reason = "empty_final_response"
                error = final_content
                self._append_final_message(messages, final_content)
                context.final_content = final_content
                context.error = error
                context.stop_reason = stop_reason
                await hook.after_iteration(context)
                should_continue, injection_cycles = await self._try_drain_injections(
                    spec, messages, None, injection_cycles,
                    phase="after empty response",
                )
                if should_continue:
                    had_injections = True
                    continue
                break

            # 5.6 正常结束：将 assistant 消息追加到 messages，保存最终检查点，跳出循环
            messages.append(assistant_message or build_assistant_message(
                clean,
                reasoning_content=response.reasoning_content,
                thinking_blocks=response.thinking_blocks,
            ))
            await self._emit_checkpoint(
                spec,
                {
                    "phase": "final_response",
                    "iteration": iteration,
                    "model": spec.model,
                    "assistant_message": messages[-1],
                    "completed_tool_results": [],
                    "pending_tool_calls": [],
                },
            )
            final_content = clean
            context.final_content = final_content
            context.stop_reason = stop_reason
            await hook.after_iteration(context)
            break
        # 6. 循环结束
        # 如果循环正常执行完 max_iterations 次（没有被 break 中断），则：
        # stop_reason = "max_iterations"
        # 生成一个总结消息（可使用模板 agent/max_iterations_message.md）。
        # 将总结消息追加到 messages。
        # 调用一次 _try_drain_injections 把队列中剩余的消息也拉取进来（但不再继续循环），确保它们最终出现在历史中，而不是被丢失。
        else:
            stop_reason = "max_iterations"
            if spec.max_iterations_message:
                final_content = spec.max_iterations_message.format(
                    max_iterations=spec.max_iterations,
                )
            else:
                final_content = render_template(
                    "agent/max_iterations_message.md",
                    strip=True,
                    max_iterations=spec.max_iterations,
                )
            self._append_final_message(messages, final_content)
            # Drain any remaining injections so they are appended to the
            # conversation history instead of being re-published as
            # independent inbound messages by _dispatch's finally block.
            # We ignore should_continue here because the for-loop has already
            # exhausted all iterations.
            drained_after_max_iterations, injection_cycles = await self._try_drain_injections(
                spec, messages, None, injection_cycles,
                phase="after max_iterations",
            )
            if drained_after_max_iterations:
                had_injections = True

        return AgentRunResult(
            final_content=final_content,
            messages=messages,
            tools_used=tools_used,
            usage=usage,
            stop_reason=stop_reason,
            error=error,
            tool_events=tool_events,
            had_injections=had_injections,
        )

    def _build_request_kwargs(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None,
    ) -> dict[str, Any]:
        """构建发送给 LLM provider 的标准请求参数字典"""
        kwargs: dict[str, Any] = {
            "messages": messages,
            "tools": tools,
            "model": spec.model,
            "retry_mode": spec.provider_retry_mode,
            "on_retry_wait": spec.retry_wait_callback,
        }
        if spec.temperature is not None:
            kwargs["temperature"] = spec.temperature
        if spec.max_tokens is not None:
            kwargs["max_tokens"] = spec.max_tokens
        if spec.reasoning_effort is not None:
            kwargs["reasoning_effort"] = spec.reasoning_effort
        return kwargs

    async def _request_model(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
        hook: AgentHook,
        context: AgentHookContext,
    ):
        """根据钩子是否要求流式输出，调用相应的 LLM 请求方法"""
        kwargs = self._build_request_kwargs(
            spec,
            messages,
            tools=spec.tools.get_definitions(),
        )
        if hook.wants_streaming():
            async def _stream(delta: str) -> None:
                await hook.on_stream(context, delta)

            return await self.provider.chat_stream_with_retry(
                **kwargs,
                on_content_delta=_stream,
            )
        return await self.provider.chat_with_retry(**kwargs)

    async def _request_finalization_retry(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ):
        """当模型连续返回空内容时，用一条特殊消息提示模型必须输出有效回复，并禁用工具"""
        retry_messages = list(messages)
        retry_messages.append(build_finalization_retry_message())
        kwargs = self._build_request_kwargs(spec, retry_messages, tools=None)
        return await self.provider.chat_with_retry(**kwargs)

    @staticmethod
    def _usage_dict(usage: dict[str, Any] | None) -> dict[str, int]:
        """将 provider 返回的 usage 字典（值可能是 int、float 或字符串）规范化成 dict[str, int]"""
        if not usage:
            return {}
        result: dict[str, int] = {}
        for key, value in usage.items():
            try:
                result[key] = int(value or 0)
            except (TypeError, ValueError):
                continue
        return result

    @staticmethod
    def _accumulate_usage(target: dict[str, int], addition: dict[str, int]) -> None:
        """将 addition 字典中的 token 计数累加到 target 字典中"""
        for key, value in addition.items():
            target[key] = target.get(key, 0) + value

    @staticmethod
    def _merge_usage(left: dict[str, int], right: dict[str, int]) -> dict[str, int]:
        """合并两个 usage 字典（左右相同键的值相加），返回新字典"""
        merged = dict(left)
        for key, value in right.items():
            merged[key] = merged.get(key, 0) + value
        return merged

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
        external_lookup_counts: dict[str, int],
    ) -> tuple[list[Any], list[dict[str, str]], BaseException | None]:
        """
        执行一批工具调用，支持依赖分批和并发控制
        逻辑：
        1.调用 self._partition_tool_batches(spec, tool_calls) 将工具调用分组（可能基于依赖关系，例如某些工具必须串行）。未给出该函数，但可推断返回一个列表的列表：list[list[ToolCallRequest]]。

        2.对每个批次：
        -若 spec.concurrent_tools 为真且批次大小 > 1，使用 asyncio.gather 并发执行该批次内的所有工具。
        -否则，串行执行该批次。
        -每个工具通过 self._run_tool 执行，返回 (result, event, error) 元组。

        3.收集所有结果后，遍历提取：
        -results：每个工具的返回值（即使出错也可能有占位符）。
        -events：每个工具的事件。
        -如果某个工具返回了 error 且 fatal_error 尚未设置，则将该错误赋给 fatal_error（后续不会再覆盖，只记录第一个）。

        4.返回三元组
        """
        batches = self._partition_tool_batches(spec, tool_calls)
        tool_results: list[tuple[Any, dict[str, str], BaseException | None]] = []
        for batch in batches:
            if spec.concurrent_tools and len(batch) > 1:
                tool_results.extend(await asyncio.gather(*(
                    self._run_tool(spec, tool_call, external_lookup_counts)
                    for tool_call in batch
                )))
            else:
                for tool_call in batch:
                    tool_results.append(await self._run_tool(spec, tool_call, external_lookup_counts))

        results: list[Any] = []
        events: list[dict[str, str]] = []
        fatal_error: BaseException | None = None
        for result, event, error in tool_results:
            results.append(result)
            events.append(event)
            if error is not None and fatal_error is None:
                fatal_error = error
        return results, events, fatal_error

    async def _run_tool(
        self,
        spec: AgentRunSpec,
        tool_call: ToolCallRequest,
        external_lookup_counts: dict[str, int],
    ) -> tuple[Any, dict[str, str], BaseException | None]:
        """执行单个工具调用并处理结果的核心方法。它负责准备参数、调用工具、捕获异常、返回标准化结果以及生成事件日志"""

        '''
        1. 防止重复的外部查找
        repeated_external_lookup_error 是一个外部辅助函数，用于检测是否在短时间内重复执行相同的、昂贵的外部查询（例如重复的数据库查询或 API 调用）。
        如果检测到重复，返回一个错误提示字符串（例如 "Repeated external lookup of '...' blocked"）；否则返回 None。
        这种设计用于防止因模型陷入循环而重复调用相同的外部工具，浪费资源。
        '''
        _HINT = "\n\n[Analyze the error above and try a different approach.]"
        lookup_error = repeated_external_lookup_error(
            tool_call.name,
            tool_call.arguments,
            external_lookup_counts,
        )
        if lookup_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": "repeated external lookup blocked",
            }
            if spec.fail_on_tool_error:
                return lookup_error + _HINT, event, RuntimeError(lookup_error)
            return lookup_error + _HINT, event, None

        '''
        2. 准备工具调用（prepare_call 钩子）
        ToolRegistry 可能提供一个可选的 prepare_call 方法，允许在真正执行工具前进行动态参数替换、返回预实例化的工具对象或返回准备错误。

        prepare_call 应返回 (tool_obj, new_params, error_str) 三元组：
            -tool_obj：如果非 None，则直接使用该对象执行（避免重复查找）。
            -new_params：替换后的参数字典。
            -error_str：如果非空，说明准备失败。
        
        如果发生异常，忽略并继续使用默认（tool=None，原始参数）。
        '''
        prepare_call = getattr(spec.tools, "prepare_call", None)
        tool, params, prep_error = None, tool_call.arguments, None
        if callable(prepare_call):
            try:
                prepared = prepare_call(tool_call.name, tool_call.arguments)
                if isinstance(prepared, tuple) and len(prepared) == 3:
                    tool, params, prep_error = prepared
            except Exception:
                pass
        '''3. 处理准备错误——准备出错时，同样生成事件，并根据 fail_on_tool_error 决定是否抛出致命错误'''
        if prep_error:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": prep_error.split(": ", 1)[-1][:120],
            }
            return prep_error + _HINT, event, RuntimeError(prep_error) if spec.fail_on_tool_error else None
        '''
        4. 执行工具
        优先使用 prepare_call 返回的具体工具对象；否则通过 spec.tools.execute 查找并执行。
        捕获 CancelledError 并重新抛出，确保外部取消能够正确传播。
        捕获其他所有异常（BaseException），统一处理。
        '''
        try:
            if tool is not None:
                result = await tool.execute(**params)
            else:
                result = await spec.tools.execute(tool_call.name, params)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": str(exc),
            }
            if spec.fail_on_tool_error:
                return f"Error: {type(exc).__name__}: {exc}", event, exc
            return f"Error: {type(exc).__name__}: {exc}", event, None

        '''
        5. 检查工具返回的假“Error”字符串
        某些工具可能以字符串 "Error: xxx" 的形式返回错误（而不是抛出异常）。
        这里将其视为非致命（除非 fail_on_tool_error=True），并同样生成事件和追加提示
        '''
        if isinstance(result, str) and result.startswith("Error"):
            event = {
                "name": tool_call.name,
                "status": "error",
                "detail": result.replace("\n", " ").strip()[:120],
            }
            if spec.fail_on_tool_error:
                return result + _HINT, event, RuntimeError(result)
            return result + _HINT, event, None

        '''6. 成功执行：生成成功事件'''
        detail = "" if result is None else str(result)
        detail = detail.replace("\n", " ").strip()
        if not detail:
            detail = "(empty)"
        elif len(detail) > 120:
            detail = detail[:120] + "..."
        return result, {"name": tool_call.name, "status": "ok", "detail": detail}, None

    async def _emit_checkpoint(
        self,
        spec: AgentRunSpec,
        payload: dict[str, Any],
    ) -> None:
        """将运行时检查点（payload）通过回调函数持久化"""
        callback = spec.checkpoint_callback
        if callback is not None:
            await callback(payload)

    @staticmethod
    def _append_final_message(messages: list[dict[str, Any]], content: str | None) -> None:
        """将最终回复（content）作为 assistant 消息追加到消息列表末尾，同时避免产生无意义的重复"""
        if not content:
            return
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not messages[-1].get("tool_calls")
        ):
            if messages[-1].get("content") == content:
                return
            messages[-1] = build_assistant_message(content)
            return
        messages.append(build_assistant_message(content))

    @staticmethod
    def _append_model_error_placeholder(messages: list[dict[str, Any]]) -> None:
        """当模型调用出错时，在消息列表中追加一个占位符 assistant 消息（内容为 _PERSISTED_MODEL_ERROR_PLACEHOLDER），以便会话历史保持有效的角色交替"""
        if messages and messages[-1].get("role") == "assistant" and not messages[-1].get("tool_calls"):
            return
        messages.append(build_assistant_message(_PERSISTED_MODEL_ERROR_PLACEHOLDER))

    def _normalize_tool_result(
        self,
        spec: AgentRunSpec,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> Any:
        """对工具执行结果进行规范化、持久化和长度限制，返回最终适合存入对话历史的 content"""
        result = ensure_nonempty_tool_result(tool_name, result)
        try:
            content = maybe_persist_tool_result(
                spec.workspace,
                spec.session_key,
                tool_call_id,
                result,
                max_chars=spec.max_tool_result_chars,
            )
        except Exception as exc:
            logger.warning(
                "Tool result persist failed for {} in {}: {}; using raw result",
                tool_call_id,
                spec.session_key or "default",
                exc,
            )
            content = result
        if isinstance(content, str) and len(content) > spec.max_tool_result_chars:
            return truncate_text(content, spec.max_tool_result_chars)
        return content

    @staticmethod
    def _drop_orphan_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """移除那些没有对应 assistant 工具调用（tool_calls）的 tool 结果消息（即“孤儿”结果）"""
        declared: set[str] = set()
        updated: list[dict[str, Any]] | None = None
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        declared.add(str(tc["id"]))
            if role == "tool":
                tid = msg.get("tool_call_id")
                if tid and str(tid) not in declared:
                    if updated is None:
                        updated = [dict(m) for m in messages[:idx]]
                    continue
            if updated is not None:
                updated.append(dict(msg))

        if updated is None:
            return messages
        return updated

    @staticmethod
    def _backfill_missing_tool_results(
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """为那些声明了工具调用但没有对应 tool 结果的消息，自动补充一个占位符错误结果（_BACKFILL_CONTENT）."""
        declared: list[tuple[int, str, str]] = []  # (assistant_idx, call_id, name)
        fulfilled: set[str] = set()
        for idx, msg in enumerate(messages):
            role = msg.get("role")
            if role == "assistant":
                for tc in msg.get("tool_calls") or []:
                    if isinstance(tc, dict) and tc.get("id"):
                        name = ""
                        func = tc.get("function")
                        if isinstance(func, dict):
                            name = func.get("name", "")
                        declared.append((idx, str(tc["id"]), name))
            elif role == "tool":
                tid = msg.get("tool_call_id")
                if tid:
                    fulfilled.add(str(tid))

        missing = [(ai, cid, name) for ai, cid, name in declared if cid not in fulfilled]
        if not missing:
            return messages

        updated = list(messages)
        offset = 0
        for assistant_idx, call_id, name in missing:
            insert_at = assistant_idx + 1 + offset
            while insert_at < len(updated) and updated[insert_at].get("role") == "tool":
                insert_at += 1
            updated.insert(insert_at, {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": _BACKFILL_CONTENT,
            })
            offset += 1
        return updated

    @staticmethod
    def _microcompact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对较旧的可压缩工具结果（工具名在 _COMPACTABLE_TOOLS 中）进行轻量替换，用简短的摘要代替原长文本"""
        compactable_indices: list[int] = []
        for idx, msg in enumerate(messages):
            if msg.get("role") == "tool" and msg.get("name") in _COMPACTABLE_TOOLS:
                compactable_indices.append(idx)

        if len(compactable_indices) <= _MICROCOMPACT_KEEP_RECENT:
            return messages

        stale = compactable_indices[: len(compactable_indices) - _MICROCOMPACT_KEEP_RECENT]
        updated: list[dict[str, Any]] | None = None
        for idx in stale:
            msg = messages[idx]
            content = msg.get("content")
            if not isinstance(content, str) or len(content) < _MICROCOMPACT_MIN_CHARS:
                continue
            name = msg.get("name", "tool")
            summary = f"[{name} result omitted from context]"
            if updated is None:
                updated = [dict(m) for m in messages]
            updated[idx]["content"] = summary

        return updated if updated is not None else messages

    def _apply_tool_result_budget(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """对每条 tool 消息应用长度预算（通过 _normalize_tool_result），确保单个工具结果不超过 spec.max_tool_result_chars"""
        updated = messages
        for idx, message in enumerate(messages):
            if message.get("role") != "tool":
                continue
            normalized = self._normalize_tool_result(
                spec,
                str(message.get("tool_call_id") or f"tool_{idx}"),
                str(message.get("name") or "tool"),
                message.get("content"),
            )
            if normalized != message.get("content"):
                if updated is messages:
                    updated = [dict(m) for m in messages]
                updated[idx]["content"] = normalized
        return updated

    def _snip_history(
        self,
        spec: AgentRunSpec,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """当整个消息列表的 token 估算值超过上下文窗口预算时，截断历史（从开头删除一些非系统消息），使总 token 数控制在预算内"""
        if not messages or not spec.context_window_tokens:
            return messages

        provider_max_tokens = getattr(getattr(self.provider, "generation", None), "max_tokens", 4096)
        max_output = spec.max_tokens if isinstance(spec.max_tokens, int) else (
            provider_max_tokens if isinstance(provider_max_tokens, int) else 4096
        )
        budget = spec.context_block_limit or (
            spec.context_window_tokens - max_output - _SNIP_SAFETY_BUFFER
        )
        if budget <= 0:
            return messages

        estimate, _ = estimate_prompt_tokens_chain(
            self.provider,
            spec.model,
            messages,
            spec.tools.get_definitions(),
        )
        if estimate <= budget:
            return messages

        system_messages = [dict(msg) for msg in messages if msg.get("role") == "system"]
        non_system = [dict(msg) for msg in messages if msg.get("role") != "system"]
        if not non_system:
            return messages

        system_tokens = sum(estimate_message_tokens(msg) for msg in system_messages)
        remaining_budget = max(128, budget - system_tokens)
        kept: list[dict[str, Any]] = []
        kept_tokens = 0
        for message in reversed(non_system):
            msg_tokens = estimate_message_tokens(message)
            if kept and kept_tokens + msg_tokens > remaining_budget:
                break
            kept.append(message)
            kept_tokens += msg_tokens
        kept.reverse()

        if kept:
            for i, message in enumerate(kept):
                if message.get("role") == "user":
                    kept = kept[i:]
                    break
            else:
                # Recover nearest user message from outside the kept window;
                # GLM rejects system→assistant (error 1214).  Budget is
                # intentionally exceeded — oversized beats invalid.
                for idx in range(len(non_system) - 1, -1, -1):
                    if non_system[idx].get("role") == "user":
                        kept = non_system[idx:]
                        break
                # If no user exists at all, _enforce_role_alternation
                # will insert a synthetic one as a safety net.
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
        if not kept:
            kept = non_system[-min(len(non_system), 4) :]
            start = find_legal_message_start(kept)
            if start:
                kept = kept[start:]
        return system_messages + kept

    def _partition_tool_batches(
        self,
        spec: AgentRunSpec,
        tool_calls: list[ToolCallRequest],
    ) -> list[list[ToolCallRequest]]:
        """根据工具是否支持并发（concurrency_safe 属性），将工具调用列表分成若干批次"""
        if not spec.concurrent_tools:
            return [[tool_call] for tool_call in tool_calls]

        batches: list[list[ToolCallRequest]] = []
        current: list[ToolCallRequest] = []
        for tool_call in tool_calls:
            get_tool = getattr(spec.tools, "get", None)
            tool = get_tool(tool_call.name) if callable(get_tool) else None
            can_batch = bool(tool and tool.concurrency_safe)
            if can_batch:
                current.append(tool_call)
                continue
            if current:
                batches.append(current)
                current = []
            batches.append([tool_call])
        if current:
            batches.append(current)
        return batches

