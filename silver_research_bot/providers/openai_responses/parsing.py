"""
    实现了 OpenAI Responses API 的响应解析功能，
    包括流式（SSE）事件解析和（非流式）响应对象解析，并将结果统一转换为项目内部的 LLMResponse 格式
    （与 Chat Completions API 兼容）。它是 OpenAICompatProvider 能够同时支持两种 API 的基础设施
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, AsyncGenerator

import httpx
import json_repair
from loguru import logger

from silver_research_bot.providers.base import LLMResponse, ToolCallRequest

'''
    结束原因映射
    Responses API 使用 status 字段（如 "completed", "incomplete"），
    而 Chat Completions API 使用 finish_reason（"stop", "length", "error"）。
    这个映射将前者转换为后者，使上层重试逻辑、工具执行判断等能够统一处理。
'''
FINISH_REASON_MAP = {
    "completed": "stop",
    "incomplete": "length",
    "failed": "error",
    "cancelled": "error",
}


def map_finish_reason(status: str | None) -> str:
    """Map a Responses API status string to a Chat-Completions-style finish_reason."""
    return FINISH_REASON_MAP.get(status or "completed", "stop")


async def iter_sse(response: httpx.Response) -> AsyncGenerator[dict[str, Any], None]:
    """将 HTTP 响应体中的 Server-Sent Events（SSE）原始行解析为字典事件。

    SSE 格式：每个事件由若干以 data: 开头的行组成，空行分隔不同事件。

    实现步骤：
    1.维护一个 buffer 列表，累积当前事件的所有以 data: 开头的行。
    2.遇到空行时，将 buffer 中的行合并，去掉 data: 前缀，拼接成 JSON 字符串，调用 json.loads。
    3.处理 [DONE] 标记（忽略）。
    4.流结束时刷新 buffer，确保最后一个事件被处理。
    """
    buffer: list[str] = []

    def _flush() -> dict[str, Any] | None:
        data_lines = [l[5:].strip() for l in buffer if l.startswith("data:")]
        buffer.clear()
        if not data_lines:
            return None
        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            return None
        try:
            return json.loads(data)
        except Exception:
            logger.warning("Failed to parse SSE event JSON: {}", data[:200])
            return None

    async for line in response.aiter_lines():
        if line == "":
            if buffer:
                event = _flush()
                if event is not None:
                    yield event
            continue
        buffer.append(line)

    # Flush any remaining buffer at EOF (#10)
    if buffer:
        event = _flush()
        if event is not None:
            yield event


async def consume_sse(
    response: httpx.Response,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[str, list[ToolCallRequest], str]:
    """
    迭代 SSE 事件，累积完整的文本内容、工具调用列表和结束原因。
    关键事件处理：
        -response.output_item.added + type: function_call：初始化工具调用缓冲区（tool_call_buffers[call_id]）。
        -response.output_text.delta：累积文本内容，并调用 on_content_delta 回调（实时输出）。
        -response.function_call_arguments.delta：追加参数片段（因为参数可能分多个事件传输）。
        -response.function_call_arguments.done：最终覆盖完整参数字符串。
        -response.output_item.done + type: function_call：组装最终的 ToolCallRequest，使用复合 ID {call_id}|{item_id}。
        -response.completed：获取 status 并映射为 finish_reason。
        -error 或 response.failed：抛出异常。

    工具调用缓冲区：
        -使用字典 call_id -> {id, name, arguments} 暂存部分信息，直到收到 done 事件才最终生成 ToolCallRequest。
        -参数 JSON 可能分片传输，因此需要字符串拼接。

    返回值：(content, tool_calls, finish_reason)，不包含 usage（usage 通常只在 response.completed 事件中才有，但该函数未提取，因为非流式场景下 usage 可从最终 response 对象获得；而流式场景使用 consume_sdk_stream 处理）."""
    content = ""
    tool_calls: list[ToolCallRequest] = []
    tool_call_buffers: dict[str, dict[str, Any]] = {}
    finish_reason = "stop"

    async for event in iter_sse(response):
        event_type = event.get("type")
        if event_type == "response.output_item.added":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if not call_id:
                    continue
                tool_call_buffers[call_id] = {
                    "id": item.get("id") or "fc_0",
                    "name": item.get("name"),
                    "arguments": item.get("arguments") or "",
                }
        elif event_type == "response.output_text.delta":
            delta_text = event.get("delta") or ""
            content += delta_text
            if on_content_delta and delta_text:
                await on_content_delta(delta_text)
        elif event_type == "response.function_call_arguments.delta":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] += event.get("delta") or ""
        elif event_type == "response.function_call_arguments.done":
            call_id = event.get("call_id")
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] = event.get("arguments") or ""
        elif event_type == "response.output_item.done":
            item = event.get("item") or {}
            if item.get("type") == "function_call":
                call_id = item.get("call_id")
                if not call_id:
                    continue
                buf = tool_call_buffers.get(call_id) or {}
                args_raw = buf.get("arguments") or item.get("arguments") or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    logger.warning(
                        "Failed to parse tool call arguments for '{}': {}",
                        buf.get("name") or item.get("name"),
                        args_raw[:200],
                    )
                    args = json_repair.loads(args_raw)
                    if not isinstance(args, dict):
                        args = {"raw": args_raw}
                tool_calls.append(
                    ToolCallRequest(
                        id=f"{call_id}|{buf.get('id') or item.get('id') or 'fc_0'}",
                        name=buf.get("name") or item.get("name") or "",
                        arguments=args,
                    )
                )
        elif event_type == "response.completed":
            status = (event.get("response") or {}).get("status")
            finish_reason = map_finish_reason(status)
        elif event_type in {"error", "response.failed"}:
            detail = event.get("error") or event.get("message") or event
            raise RuntimeError(f"Response failed: {str(detail)[:500]}")

    return content, tool_calls, finish_reason


def parse_response_output(response: Any) -> LLMResponse:
    """
    将 responses.create(stream=False) 返回的 SDK 对象（或从 API 直接获得的 dict）转换为 LLMResponse。

    兼容多种输入：
        -如果 response 是 dict，直接使用。
        -否则尝试调用 model_dump()（Pydantic 模型）或 vars() 转为字典。

    处理 output 列表：
        -type: "message"：遍历 content 中的块，提取 output_text 的 text。
        -type: "reasoning"：遍历 summary，提取 summary_text 并拼接成 reasoning_content。
        -type: "function_call"：解析参数 JSON，构造 ToolCallRequest，ID 为 {call_id}|{id}。

    使用量提取：
        -usage.input_tokens → prompt_tokens
        -usage.output_tokens → completion_tokens
        -usage.total_tokens → total_tokens

    结束原因：通过 response.status 映射。

    返回值：完整的 LLMResponse 对象。
    """
    if not isinstance(response, dict):
        dump = getattr(response, "model_dump", None)
        response = dump() if callable(dump) else vars(response)

    output = response.get("output") or []
    content_parts: list[str] = []
    tool_calls: list[ToolCallRequest] = []
    reasoning_content: str | None = None

    for item in output:
        if not isinstance(item, dict):
            dump = getattr(item, "model_dump", None)
            item = dump() if callable(dump) else vars(item)

        item_type = item.get("type")
        if item_type == "message":
            for block in item.get("content") or []:
                if not isinstance(block, dict):
                    dump = getattr(block, "model_dump", None)
                    block = dump() if callable(dump) else vars(block)
                if block.get("type") == "output_text":
                    content_parts.append(block.get("text") or "")
        elif item_type == "reasoning":
            for s in item.get("summary") or []:
                if not isinstance(s, dict):
                    dump = getattr(s, "model_dump", None)
                    s = dump() if callable(dump) else vars(s)
                if s.get("type") == "summary_text" and s.get("text"):
                    reasoning_content = (reasoning_content or "") + s["text"]
        elif item_type == "function_call":
            call_id = item.get("call_id") or ""
            item_id = item.get("id") or "fc_0"
            args_raw = item.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except Exception:
                logger.warning(
                    "Failed to parse tool call arguments for '{}': {}",
                    item.get("name"),
                    str(args_raw)[:200],
                )
                args = json_repair.loads(args_raw) if isinstance(args_raw, str) else args_raw
                if not isinstance(args, dict):
                    args = {"raw": args_raw}
            tool_calls.append(ToolCallRequest(
                id=f"{call_id}|{item_id}",
                name=item.get("name") or "",
                arguments=args if isinstance(args, dict) else {},
            ))

    usage_raw = response.get("usage") or {}
    if not isinstance(usage_raw, dict):
        dump = getattr(usage_raw, "model_dump", None)
        usage_raw = dump() if callable(dump) else vars(usage_raw)
    usage = {}
    if usage_raw:
        usage = {
            "prompt_tokens": int(usage_raw.get("input_tokens") or 0),
            "completion_tokens": int(usage_raw.get("output_tokens") or 0),
            "total_tokens": int(usage_raw.get("total_tokens") or 0),
        }

    status = response.get("status")
    finish_reason = map_finish_reason(status)

    return LLMResponse(
        content="".join(content_parts) or None,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
    )


async def consume_sdk_stream(
    stream: Any,
    on_content_delta: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[str, list[ToolCallRequest], str, dict[str, int], str | None]:
    """
    处理 OpenAI SDK 返回的异步流（即 client.responses.create(stream=True) 的返回值），聚合内容、工具调用、结束原因、用量和推理内容。

    与 consume_sse 的区别：
    -输入是 SDK 的事件对象（而非 httpx.Response），因此使用 getattr(event, "type") 而不是 event.get("type")。
    -能够直接提取 usage（在 response.completed 事件中通过 resp.usage 获取）。
    -能够提取 reasoning_content（通过 response.completed 事件中的 resp.output 中的 reasoning 项）。

    事件处理逻辑：
    -response.output_item.added：初始化工具调用缓冲区。
    -response.output_text.delta：累积文本，触发回调。
    -response.function_call_arguments.delta / .done：累积参数。
    -response.output_item.done：构造 ToolCallRequest（与 consume_sse 相同）。
    -response.completed：
        *获取 status 映射为 finish_reason。
        *提取 usage（input_tokens / output_tokens / total_tokens）。
        *遍历 resp.output，找到 type: "reasoning" 的项，解析其 summary 中的 summary_text 拼接成 reasoning_content。
    -返回值：(content, tool_calls, finish_reason, usage, reasoning_content)，包含了所有必要信息，供上层构建 LLMResponse。
    """
    content = ""
    tool_calls: list[ToolCallRequest] = []
    tool_call_buffers: dict[str, dict[str, Any]] = {}
    finish_reason = "stop"
    usage: dict[str, int] = {}
    reasoning_content: str | None = None

    async for event in stream:
        event_type = getattr(event, "type", None)
        if event_type == "response.output_item.added":
            item = getattr(event, "item", None)
            if item and getattr(item, "type", None) == "function_call":
                call_id = getattr(item, "call_id", None)
                if not call_id:
                    continue
                tool_call_buffers[call_id] = {
                    "id": getattr(item, "id", None) or "fc_0",
                    "name": getattr(item, "name", None),
                    "arguments": getattr(item, "arguments", None) or "",
                }
        elif event_type == "response.output_text.delta":
            delta_text = getattr(event, "delta", "") or ""
            content += delta_text
            if on_content_delta and delta_text:
                await on_content_delta(delta_text)
        elif event_type == "response.function_call_arguments.delta":
            call_id = getattr(event, "call_id", None)
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] += getattr(event, "delta", "") or ""
        elif event_type == "response.function_call_arguments.done":
            call_id = getattr(event, "call_id", None)
            if call_id and call_id in tool_call_buffers:
                tool_call_buffers[call_id]["arguments"] = getattr(event, "arguments", "") or ""
        elif event_type == "response.output_item.done":
            item = getattr(event, "item", None)
            if item and getattr(item, "type", None) == "function_call":
                call_id = getattr(item, "call_id", None)
                if not call_id:
                    continue
                buf = tool_call_buffers.get(call_id) or {}
                args_raw = buf.get("arguments") or getattr(item, "arguments", None) or "{}"
                try:
                    args = json.loads(args_raw)
                except Exception:
                    logger.warning(
                        "Failed to parse tool call arguments for '{}': {}",
                        buf.get("name") or getattr(item, "name", None),
                        str(args_raw)[:200],
                    )
                    args = json_repair.loads(args_raw)
                    if not isinstance(args, dict):
                        args = {"raw": args_raw}
                tool_calls.append(
                    ToolCallRequest(
                        id=f"{call_id}|{buf.get('id') or getattr(item, 'id', None) or 'fc_0'}",
                        name=buf.get("name") or getattr(item, "name", None) or "",
                        arguments=args,
                    )
                )
        elif event_type == "response.completed":
            resp = getattr(event, "response", None)
            status = getattr(resp, "status", None) if resp else None
            finish_reason = map_finish_reason(status)
            if resp:
                usage_obj = getattr(resp, "usage", None)
                if usage_obj:
                    usage = {
                        "prompt_tokens": int(getattr(usage_obj, "input_tokens", 0) or 0),
                        "completion_tokens": int(getattr(usage_obj, "output_tokens", 0) or 0),
                        "total_tokens": int(getattr(usage_obj, "total_tokens", 0) or 0),
                    }
                for out_item in getattr(resp, "output", None) or []:
                    if getattr(out_item, "type", None) == "reasoning":
                        for s in getattr(out_item, "summary", None) or []:
                            if getattr(s, "type", None) == "summary_text":
                                text = getattr(s, "text", None)
                                if text:
                                    reasoning_content = (reasoning_content or "") + text
        elif event_type in {"error", "response.failed"}:
            detail = getattr(event, "error", None) or getattr(event, "message", None) or event
            raise RuntimeError(f"Response failed: {str(detail)[:500]}")

    return content, tool_calls, finish_reason, usage, reasoning_content
