"""
    定义了所有 LLM 提供者必须遵循的统一接口，
    并实现了丰富的辅助功能：消息规范化、角色交替修正、图片占位处理、自动重试（含持久重试）、流式回调、错误分类等
"""

import asyncio
import json
import re
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from loguru import logger

from silver_research_bot.utils.helpers import image_placeholder_text


@dataclass
class ToolCallRequest:
    """LLM 返回的一个工具调用请求"""
    id: str
    'id：调用唯一标识（用于后续提交结果）'
    name: str
    'name：工具/函数名'
    arguments: dict[str, Any]
    'arguments：已解析的参数字典'

    'extra_content / provider_specific_fields / function_provider_specific_fields：用于保留不同提供商的扩展字段，保证可扩展性。'
    extra_content: dict[str, Any] | None = None
    provider_specific_fields: dict[str, Any] | None = None
    function_provider_specific_fields: dict[str, Any] | None = None


    def to_openai_tool_call(self) -> dict[str, Any]:
        """将对象序列化为 OpenAI API 兼容的 tool_call 字典格式，方便下游直接使用"""
        tool_call = {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments, ensure_ascii=False),
            },
        }
        if self.extra_content:
            tool_call["extra_content"] = self.extra_content
        if self.provider_specific_fields:
            tool_call["provider_specific_fields"] = self.provider_specific_fields
        if self.function_provider_specific_fields:
            tool_call["function"]["provider_specific_fields"] = self.function_provider_specific_fields
        return tool_call


@dataclass
class LLMResponse:
    """统一封装 LLM 返回的内容、工具调用、用量信息以及错误元数据"""
    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    finish_reason: str = "stop"
    '"stop"、"tool_calls"、"error" 等'
    usage: dict[str, int] = field(default_factory=dict)
    'token 统计'
    retry_after: float | None = None  # Provider supplied retry wait in seconds.
    'Provider 在重试之前应该等待多少秒'
    reasoning_content: str | None = None  # Kimi, DeepSeek-R1, MiMo etc.
    '如 Kimi, DeepSeek-R1, MiMo etc. 的思考过程'
    thinking_blocks: list[dict] | None = None  # Anthropic extended thinking
    'Anthropic 的扩展思考'

    '允许提供者将 HTTP 状态码、错误类型、重试等待时间等结构化信息写入响应，供重试策略使用'
    # Structured error metadata used by retry policy when finish_reason == "error".
    error_status_code: int | None = None
    error_kind: str | None = None  # e.g. "timeout", "connection"
    error_type: str | None = None  # Provider/type semantic, e.g. insufficient_quota.
    error_code: str | None = None  # Provider/code semantic, e.g. rate_limit_exceeded.
    error_retry_after_s: float | None = None
    error_should_retry: bool | None = None

    @property
    def has_tool_calls(self) -> bool:
        """检查响应是否包含工具调用"""
        return len(self.tool_calls) > 0

    @property
    def should_execute_tools(self) -> bool:
        """只有当 has_tool_calls 且 finish_reason 为 "tool_calls" 或 "stop" 时才应执行工具。
        阻止在某些错误/拒绝情况下误执行工具"""
        if not self.has_tool_calls:
            return False
        return self.finish_reason in ("tool_calls", "stop")


@dataclass(frozen=True)
class GenerationSettings:
    """保存提供者的默认生成参数，可以在实例化时设置，并在 chat_with_retry 等方法中作为后备值"""

    temperature: float = 0.7
    max_tokens: int = 4096
    reasoning_effort: str | None = None


_SYNTHETIC_USER_CONTENT = "(conversation continued)"


class LLMProvider(ABC):
    """LLM providers 基类."""

    _CHAT_RETRY_DELAYS = (1, 2, 4)
    '标准模式（retry_mode="standard"）下的指数退避基础延迟（单位：秒）。第一次重试等待 1 秒，第二次等待 2 秒，第三次及之后等待 4 秒（之后不再增长）。设计为较短延迟，适合快速恢复的临时故障'
    _PERSISTENT_MAX_DELAY = 60
    '持久模式（retry_mode="persistent"）下，任何重试延迟的上限（60 秒）'
    _PERSISTENT_IDENTICAL_ERROR_LIMIT = 10
    '持久模式下，连续出现相同错误内容的最大次数（10 次）'
    _RETRY_HEARTBEAT_CHUNK = 30
    '等待重试期间，每 30 秒“心跳”一次'
    _TRANSIENT_ERROR_MARKERS = (
        "429",
        "rate limit",
        "500",
        "502",
        "503",
        "504",
        "overloaded",
        "timeout",
        "timed out",
        "connection",
        "server error",
        "temporarily unavailable",
        "速率限制",
    )
    '临时性错误的文本特征（如 "429", "rate limit", "timeout", "速率限制" 等）'
    _RETRYABLE_STATUS_CODES = frozenset({408, 409, 429})
    '可重试的 HTTP 状态码集合 {408, 409, 429} 408 Request Timeout：客户端请求超时，通常可重试。409 Conflict：资源冲突，有时由并发引起，重试可能成功。429 Too Many Requests：速率限制或配额问题，需要根据具体错误类型决定是否重试'
    _TRANSIENT_ERROR_KINDS = frozenset({"timeout", "connection"})
    '当 LLMResponse.error_kind 为 "timeout" 或 "connection" 时，直接认定为临时错误，应进行重试'
    _NON_RETRYABLE_429_ERROR_TOKENS = frozenset({
        "insufficient_quota",
        "quota_exceeded",
        "quota_exhausted",
        "billing_hard_limit_reached",
        "insufficient_balance",
        "credit_balance_too_low",
        "billing_not_active",
        "payment_required",
    })
    '表示配额耗尽、计费问题等不应重试的 429 错误类型'
    _RETRYABLE_429_ERROR_TOKENS = frozenset({
        "rate_limit_exceeded",
        "rate_limit_error",
        "too_many_requests",
        "request_limit_exceeded",
        "requests_limit_exceeded",
        "overloaded_error",
    })
    '表示速率限制过载、请求过多等可重试的 429 错误类型'

    '针对纯文本问题的429错误'
    _NON_RETRYABLE_429_TEXT_MARKERS = (
        "insufficient_quota",
        "insufficient quota",
        "quota exceeded",
        "quota exhausted",
        "billing hard limit",
        "billing_hard_limit_reached",
        "billing not active",
        "insufficient balance",
        "insufficient_balance",
        "credit balance too low",
        "payment required",
        "out of credits",
        "out of quota",
        "exceeded your current quota",
    )
    _RETRYABLE_429_TEXT_MARKERS = (
        "rate limit",
        "rate_limit",
        "too many requests",
        "retry after",
        "try again in",
        "temporarily unavailable",
        "overloaded",
        "concurrency limit",
        "速率限制",
    )

    _SENTINEL = object()
    '一个唯一的单例对象，用于区分“未传递参数”与“显式传递 None”'

    def __init__(self, api_key: str | None = None, api_base: str | None = None):
        """每个提供者至少需要 API 密钥和可选的 base URL，并持有一份生成参数设置"""
        self.api_key = api_key
        self.api_base = api_base
        self.generation: GenerationSettings = GenerationSettings()

    @staticmethod
    def _sanitize_empty_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """负责清洗消息列表中的空内容或无效结构，并移除内部使用的 _meta 字段，以确保消息格式能被 LLM 提供商正确接受"""
        result: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")

            '1. 处理 content 为空字符串的情况'
            if isinstance(content, str) and not content:
                clean = dict(msg)
                clean["content"] = None if (msg.get("role") == "assistant" and msg.get("tool_calls")) else "(empty)"
                result.append(clean)
                continue

            '2. 处理 content 为列表（多模态内容块）'
            if isinstance(content, list):
                new_items: list[Any] = []
                changed = False
                for item in content:
                    # 跳过空文本块
                    if (
                        isinstance(item, dict)
                        and item.get("type") in ("text", "input_text", "output_text")
                        and not item.get("text")
                    ):
                        changed = True
                        continue
                    # 移除 _meta 字段
                    if isinstance(item, dict) and "_meta" in item:
                        new_items.append({k: v for k, v in item.items() if k != "_meta"})
                        changed = True
                    else:
                        new_items.append(item)

                '''
                如果列表发生过任何变更（changed == True），则重新构建消息。
                    如果清洗后 new_items 非空，将 content 替换为清洗后的列表。               
                    如果 new_items 为空：
                        若是助手消息且有 tool_calls，content 设为 None。
                        否则设为 "(empty)"
                将新消息加入结果，并跳过后续处理
                '''
                if changed:
                    clean = dict(msg)
                    if new_items:
                        clean["content"] = new_items
                    elif msg.get("role") == "assistant" and msg.get("tool_calls"):
                        clean["content"] = None
                    else:
                        clean["content"] = "(empty)"
                    result.append(clean)
                    continue

            '3. 处理 content 为字典（罕见情况）'
            if isinstance(content, dict):
                clean = dict(msg)
                clean["content"] = [content]
                result.append(clean)
                continue

            '4. 其他情况（正常内容）'
            result.append(msg)
        return result

    @staticmethod
    def _tool_name(tool: dict[str, Any]) -> str:
        """从 OpenAI 或 Anthropic 风格的工具定义中提取工具名"""
        name = tool.get("name")
        if isinstance(name, str):
            return name
        fn = tool.get("function")
        if isinstance(fn, dict):
            fname = fn.get("name")
            if isinstance(fname, str):
                return fname
        return ""

    @classmethod
    def _tool_cache_marker_indices(cls, tools: list[dict[str, Any]]) -> list[int]:
        """返回内置工具（不以 mcp_ 开头）的最后一个索引和工具列表的最后一个索引，用于缓存策略"""
        if not tools:
            return []

        tail_idx = len(tools) - 1
        last_builtin_idx: int | None = None
        for i in range(tail_idx, -1, -1):
            if not cls._tool_name(tools[i]).startswith("mcp_"):
                last_builtin_idx = i
                break

        ordered_unique: list[int] = []
        for idx in (last_builtin_idx, tail_idx):
            if idx is not None and idx not in ordered_unique:
                ordered_unique.append(idx)
        return ordered_unique

    @staticmethod
    def _sanitize_request_messages(
        messages: list[dict[str, Any]],
        allowed_keys: frozenset[str],
    ) -> list[dict[str, Any]]:
        """只保留 provider 允许的顶层键（例如 role, content, tool_calls 等），并确保 assistant 角色在没有 content 时显式设为 None"""
        sanitized = []
        for msg in messages:
            clean = {k: v for k, v in msg.items() if k in allowed_keys}
            if clean.get("role") == "assistant" and "content" not in clean:
                clean["content"] = None
            sanitized.append(clean)
        return sanitized

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> LLMResponse:
        """
        所有具体提供者必须实现的核心方法。

        参数说明：
            messages：对话历史（每个消息包含 role 和 content 等）。
            tools：工具定义列表（可选）。
            model：提供者特定的模型名称。
            max_tokens:回复最大token值
            temperature:取样温度
            reasoning_effort:控制某些支持显式推理过程的模型的推理深度或详细程度
            tool_choice：工具选择策略（"auto"、"required" 或指定工具）。

        返回:
            有着content and/or tool calls.统一的 LLMResponse 对象。
        """
        pass

    @abstractmethod
    async def embed(self, text: str, model: str | None = None) -> list[float]:
        """返回单个文本的嵌入向量。"""
        pass

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        """返回批量文本的嵌入向量列表。"""
        pass

    async def embed_batch_with_retry(
        self,
        texts: list[str],
        model: str | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[list[float]]:
        """在临时提供程序故障时对 embed_batch 进行重试。"""
        delays = self._retry_delays(retry_mode)
        last_exc = None
        for attempt, delay in enumerate(delays, start=1):
            try:
                return await self.embed_batch(texts=texts, model=model)
            except NotImplementedError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_exc = exc
                if attempt == len(delays):
                    break
            logger.warning(f"Embed retry {attempt}/{len(delays)} in {delay:.1f}s")
            if on_retry_wait:
                await on_retry_wait(f"Retrying embed in {delay:.0f}s")
            await asyncio.sleep(delay)
        raise RuntimeError(f"All embed retries exhausted") from last_exc

    @classmethod
    def _is_transient_error(cls, content: str | None) -> bool:
        """ 临时错误判断:将响应文本转为小写，检查是否包含 _TRANSIENT_ERROR_MARKERS 中的任一标记（如 "timeout"、"rate limit"、"429" 等）"""
        err = (content or "").lower()
        return any(marker in err for marker in cls._TRANSIENT_ERROR_MARKERS)

    @classmethod
    def _is_transient_response(cls, response: LLMResponse) -> bool:
        """
            综合临时错误判断
            优先级：
            1.如果 response.error_should_retry 字段不为 None，直接使用该布尔值。
            2.如果有 error_status_code：
                若为 429，调用 _is_retryable_429_response 细化判断。
                若在 _RETRYABLE_STATUS_CODES（408,409,429）中或 ≥500，视为临时错误。
            3.若 error_kind 为 "timeout" 或 "connection"，视为临时错误。
            4.最后回退到 _is_transient_error(response.content)。
        """

        if response.error_should_retry is not None:
            return bool(response.error_should_retry)

        if response.error_status_code is not None:
            status = int(response.error_status_code)
            if status == 429:
                return cls._is_retryable_429_response(response)
            if status in cls._RETRYABLE_STATUS_CODES or status >= 500:
                return True

        kind = (response.error_kind or "").strip().lower()
        if kind in cls._TRANSIENT_ERROR_KINDS:
            return True

        return cls._is_transient_error(response.content)

    @staticmethod
    def _normalize_error_token(value: Any) -> str | None:
        """错误 Token 规范化:将错误类型或错误代码转换为小写字符串，若为 None 或空则返回 None"""
        if value is None:
            return None
        token = str(value).strip().lower()
        return token or None

    @classmethod
    def _extract_error_type_code(cls, payload: Any) -> tuple[str | None, str | None]:
        """
            从响应载荷中提取错误类型和代码
                支持 dict 或 JSON 字符串格式的 payload。
                查找 error 对象或顶层的 type / code 字段。
                返回标准化后的 (error_type, error_code)
        """
        data: dict[str, Any] | None = None
        if isinstance(payload, dict):
            data = payload
        elif isinstance(payload, str):
            text = payload.strip()
            if text:
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, dict):
                    data = parsed
        if not isinstance(data, dict):
            return None, None

        error_obj = data.get("error")
        type_value = data.get("type")
        code_value = data.get("code")
        if isinstance(error_obj, dict):
            type_value = error_obj.get("type") or type_value
            code_value = error_obj.get("code") or code_value

        return cls._normalize_error_token(type_value), cls._normalize_error_token(code_value)

    @classmethod
    def _is_retryable_429_response(cls, response: LLMResponse) -> bool:
        """
        判断 429 错误是否可重试
        步骤：
            1.从 response.error_type 和 response.error_code 中提取标准化 token。
            2.若任一 token 属于 _NON_RETRYABLE_429_ERROR_TOKENS（如 insufficient_quota），返回 False（不重试）。
            3.检查响应文本内容，若包含 _NON_RETRYABLE_429_TEXT_MARKERS，返回 False。
            4.若 token 属于 _RETRYABLE_429_ERROR_TOKENS，返回 True。
            5.若文本包含 _RETRYABLE_429_TEXT_MARKERS，返回 True。
            6.默认返回 True（未知 429 也重试，但会遵守 retry_after）。
        """
        type_token = cls._normalize_error_token(response.error_type)
        code_token = cls._normalize_error_token(response.error_code)
        semantic_tokens = {
            token for token in (type_token, code_token)
            if token is not None
        }
        if any(token in cls._NON_RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
            return False

        content = (response.content or "").lower()
        if any(marker in content for marker in cls._NON_RETRYABLE_429_TEXT_MARKERS):
            return False

        if any(token in cls._RETRYABLE_429_ERROR_TOKENS for token in semantic_tokens):
            return True
        if any(marker in content for marker in cls._RETRYABLE_429_TEXT_MARKERS):
            return True
        # Unknown 429 defaults to WAIT+retry.
        return True

    @staticmethod
    def _enforce_role_alternation(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
            强制角色交替与尾部清理
            目的：解决许多 LLM 提供商（如 OpenAI、vLLM、Ollama、智谱等）对消息角色的严格要求。
            操作：
                -合并连续的相同角色（user/assistant）的消息，用 "\n\n" 连接文本。
                -删除末尾的所有 assistant 消息（防止预填充问题）。
                -如果删除后只剩下 system 消息，则将最后一个被删的 assistant 消息转为 user 消息，保证至少有一个用户消息。
                -如果第一个非系统消息是 assistant 且没有工具调用，则在它前面插入一条合成的 user 消息，内容为 "(conversation continued)"。
        """
        if not messages:
            return messages

        merged: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            if (
                merged
                and role != "system"
                and role not in ("tool",)
                and merged[-1].get("role") == role
                and role in ("user", "assistant")
            ):
                prev = merged[-1]
                if role == "assistant":
                    prev_has_tools = bool(prev.get("tool_calls"))
                    curr_has_tools = bool(msg.get("tool_calls"))
                    if curr_has_tools:
                        merged[-1] = dict(msg)
                        continue
                    if prev_has_tools:
                        continue
                prev_content = prev.get("content") or ""
                curr_content = msg.get("content") or ""
                if isinstance(prev_content, str) and isinstance(curr_content, str):
                    prev["content"] = (prev_content + "\n\n" + curr_content).strip()
                else:
                    merged[-1] = dict(msg)
            else:
                merged.append(dict(msg))

        last_popped = None
        while merged and merged[-1].get("role") == "assistant":
            last_popped = merged.pop()

        # If removing trailing assistant messages left only system messages,
        # the request would be invalid for most providers (e.g. Zhipu/GLM
        # error 1214).  Recover by converting the last popped assistant
        # message to a user message so the LLM can still see the content.
        if (
            merged
            and last_popped is not None
            and not any(m.get("role") in ("user", "tool") for m in merged)
        ):
            recovered = dict(last_popped)
            recovered["role"] = "user"
            merged.append(recovered)

        # Safety net: ensure the first non-system message is not a bare
        # ``assistant`` message.  Providers like GLM reject system→assistant
        # with error 1214.  This can happen when upstream truncation (e.g.
        # _snip_history) drops the only user message.  Insert a synthetic
        # user message to keep the sequence valid.
        for i, msg in enumerate(merged):
            if msg.get("role") != "system":
                if msg.get("role") == "assistant" and not msg.get("tool_calls"):
                    merged.insert(i, {"role": "user", "content": _SYNTHETIC_USER_CONTENT})
                break

        return merged

    @staticmethod
    def _strip_image_content(messages: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
        """
            图片内容替换（返回新列表）
            遍历消息，将 type == "image_url" 的内容块替换为纯文本占位符（通过 image_placeholder_text 生成）。
            若没有找到任何图片，返回 None；否则返回修改后的新消息列表。
            用于遇到非临时错误时尝试移除图片后重试。
        """
        found = False
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                new_content = []
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        path = (b.get("_meta") or {}).get("path", "")
                        placeholder = image_placeholder_text(path, empty="[image omitted]")
                        new_content.append({"type": "text", "text": placeholder})
                        found = True
                    else:
                        new_content.append(b)
                result.append({**msg, "content": new_content})
            else:
                result.append(msg)
        return result if found else None

    @staticmethod
    def _strip_image_content_inplace(messages: list[dict[str, Any]]) -> bool:
        """
            图片内容原地替换
            与 _strip_image_content 类似，但直接修改原消息列表（原地修改）。
            返回是否进行了任何替换。
            用于永久性移除图片，避免重复重试循环。
        """
        found = False
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                for i, b in enumerate(content):
                    if isinstance(b, dict) and b.get("type") == "image_url":
                        path = (b.get("_meta") or {}).get("path", "")
                        placeholder = image_placeholder_text(path, empty="[image omitted]")
                        content[i] = {"type": "text", "text": placeholder}
                        found = True
        return found

    async def _safe_chat(self, **kwargs: Any) -> LLMResponse:
        """
        安全调用 chat（异常捕获）
        调用 self.chat，捕获所有非 CancelledError 异常，返回一个 finish_reason="error" 的 LLMResponse，内容为错误信息。
        确保上层重试逻辑总是得到 LLMResponse 对象，不会因未捕获异常而崩溃。
        """
        try:
            return await self.chat(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {exc}", finish_reason="error")

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
            流式聊天 chat_stream
            默认实现：直接调用非流式的 chat，拿到完整响应后，如果提供了 on_content_delta 回调，则一次性将全部 content 传入。
            具体提供者应重写此方法以实现真正的流式传输（逐 token 回调）。
        """
        response = await self.chat(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
        )
        if on_content_delta and response.content:
            await on_content_delta(response.content)
        return response

    async def _safe_chat_stream(self, **kwargs: Any) -> LLMResponse:
        """安全调用 chat_stream 与 _safe_chat 类似，包装 chat_stream，将异常转为错误响应。"""
        try:
            return await self.chat_stream(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return LLMResponse(content=f"Error calling LLM: {exc}", finish_reason="error")

    """
    chat_stream_with_retry 与 chat_with_retry 方法
    核心作用
    这两个方法是对外公开的重试入口，分别对应流式和非流式调用。它们负责：
        1.规范化生成参数（max_tokens, temperature, reasoning_effort）。
        2.将参数打包成字典。
        3.调用内部重试引擎 _run_with_retry。
    """
    async def chat_stream_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """Call chat_stream() with retry on transient provider failures."""
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
            on_content_delta=on_content_delta,
        )
        return await self._run_with_retry(
            self._safe_chat_stream,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
        )

    async def chat_with_retry(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: object = _SENTINEL,
        temperature: object = _SENTINEL,
        reasoning_effort: object = _SENTINEL,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict | None = None,
        retry_mode: str = "standard",
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        """
            在临时提供程序发生故障时，调用 chat() 并进行重试。
            若未显式传入，参数将默认采用 ``self.generation``，
            因此调用方无需再将 temperature / max_tokens / reasoning_effort 参数传递至每一层。
            显式的 ``None`` 也会被规范化为提供者的生成默认值，从而确保下游
            的 ``_build_kwargs`` 永远不会看到 ``max_tokens`` / ``temperature`` 为 ``None``
            （否则会导致 ``max(1, max_tokens)`` 崩溃）。
        """
        if max_tokens is self._SENTINEL or max_tokens is None:
            max_tokens = self.generation.max_tokens
        if temperature is self._SENTINEL or temperature is None:
            temperature = self.generation.temperature
        if reasoning_effort is self._SENTINEL:
            reasoning_effort = self.generation.reasoning_effort

        kw: dict[str, Any] = dict(
            messages=messages, tools=tools, model=model,
            max_tokens=max_tokens, temperature=temperature,
            reasoning_effort=reasoning_effort, tool_choice=tool_choice,
            response_format=response_format,
        )
        return await self._run_with_retry(
            self._safe_chat,
            kw,
            messages,
            retry_mode=retry_mode,
            on_retry_wait=on_retry_wait,
        )

    @classmethod
    def _extract_retry_after(cls, content: str | None) -> float | None:
        """重试等待时间提取相关方法:从不同来源获取服务端建议的重试等待时间，最终供 _run_with_retry 使用"""
        text = (content or "").lower()
        patterns = (
            r"retry after\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)?",
            r"try again in\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)",
            r"wait\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds|s|sec|secs|seconds|m|min|minutes)\s*before retry",
            r"retry[_-]?after[\"'\s:=]+(\d+(?:\.\d+)?)",
        )
        for idx, pattern in enumerate(patterns):
            match = re.search(pattern, text)
            if not match:
                continue
            value = float(match.group(1))
            unit = match.group(2) if idx < 3 else "s"
            return cls._to_retry_seconds(value, unit)
        return None

    @classmethod
    def _to_retry_seconds(cls, value: float, unit: str | None = None) -> float:
        """将数值和单位转换为秒，并确保最小值 0.1 秒"""
        normalized_unit = (unit or "s").lower()
        if normalized_unit in {"ms", "milliseconds"}:
            return max(0.1, value / 1000.0)
        if normalized_unit in {"m", "min", "minutes"}:
            return max(0.1, value * 60.0)
        return max(0.1, value)

    @classmethod
    def _extract_retry_after_from_headers(cls, headers: Any) -> float | None:
        """
            从 HTTP 响应头中读取 retry-after-ms 或 retry-after 字段
            支持：
                字典或任何有 .get() 方法的对象（如 httpx.Headers）。
                retry-after-ms：毫秒值，直接转换。
                retry-after：可以是纯数字（秒），也可以是 HTTP 日期字符串（如 "Wed, 21 Oct 2015 07:28:00 GMT"）。如果是日期，则解析并计算剩余秒数。
            返回值：秒数或 None。
        """
        if not headers:
            return None

        def _header_value(name: str) -> Any:
            if hasattr(headers, "get"):
                value = headers.get(name) or headers.get(name.title())
                if value is not None:
                    return value
            if isinstance(headers, dict):
                for key, value in headers.items():
                    if isinstance(key, str) and key.lower() == name.lower():
                        return value
            return None

        try:
            retry_ms = _header_value("retry-after-ms")
            if retry_ms is not None:
                value = float(retry_ms) / 1000.0
                if value > 0:
                    return value
        except (TypeError, ValueError):
            pass

        retry_after = _header_value("retry-after")
        if retry_after is None:
            return None
        retry_after_text = str(retry_after).strip()
        if not retry_after_text:
            return None
        if re.fullmatch(r"\d+(?:\.\d+)?", retry_after_text):
            return cls._to_retry_seconds(float(retry_after_text), "s")
        try:
            retry_at = parsedate_to_datetime(retry_after_text)
        except Exception:
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        remaining = (retry_at - datetime.now(retry_at.tzinfo)).total_seconds()
        return max(0.1, remaining)

    @classmethod
    def _extract_retry_after_from_response(cls, response: LLMResponse) -> float | None:
        """
            作用：整合上述来源，按优先级返回重试等待时间。
            优先级：
                1.response.error_retry_after_s（专门用于错误的重试字段）
                2.response.retry_after（通用重试字段）
                3.调用 _extract_retry_after(response.content) 从文本提取
        """
        if response.error_retry_after_s is not None and response.error_retry_after_s > 0:
            return response.error_retry_after_s
        if response.retry_after is not None and response.retry_after > 0:
            return response.retry_after
        return cls._extract_retry_after(response.content)

    async def _sleep_with_heartbeat(
        self,
        delay: float,
        *,
        attempt: int,
        persistent: bool,
        on_retry_wait: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """
        核心作用：在重试等待期间，分段睡眠并定期通知上层，避免长时间阻塞时 UI 无响应。
        具体：
            -总延迟 delay 被切成块，每块大小 _RETRY_HEARTBEAT_CHUNK（默认 30 秒）。
            -在每个块开始前，如果提供了 on_retry_wait 回调，则调用它，传入类似 "Model request failed, retry in Xs (attempt N)" 的消息。
            -然后 await asyncio.sleep(chunk)。
            -循环直到剩余时间为 0。
        """
        remaining = max(0.0, delay)
        while remaining > 0:
            if on_retry_wait:
                kind = "persistent retry" if persistent else "retry"
                await on_retry_wait(
                    f"Model request failed, {kind} in {max(1, int(round(remaining)))}s "
                    f"(attempt {attempt})."
                )
            chunk = min(remaining, self._RETRY_HEARTBEAT_CHUNK)
            await asyncio.sleep(chunk)
            remaining -= chunk

    async def _run_with_retry(
        self,
        call: Callable[..., Awaitable[LLMResponse]],
        kw: dict[str, Any],
        original_messages: list[dict[str, Any]],
        *,
        retry_mode: str,
        on_retry_wait: Callable[[str], Awaitable[None]] | None,
    ) -> LLMResponse:
        """实现完整的重试循环，包含错误分类、图片降级、延迟计算、退出条件"""
        '''1.初始化'''
        attempt = 0
        delays = list(self._CHAT_RETRY_DELAYS)
        persistent = retry_mode == "persistent"
        last_response: LLMResponse | None = None
        last_error_key: str | None = None
        identical_error_count = 0
        '''
            2.循环：
                - 调用底层方法（_safe_chat 或 _safe_chat_stream），得到 response。
                - 如果成功（finish_reason != "error"），直接返回响应。           
                - 记录错误：
                    *保存 last_response。            
                    *提取错误内容作为 error_key（去除首尾空格、小写）。
                    *如果与上次相同，identical_error_count++；否则重置计数。
        '''
        while True:
            attempt += 1
            response = await call(**kw)
            if response.finish_reason != "error":
                return response
            last_response = response
            error_key = ((response.content or "").strip().lower() or None)
            if error_key and error_key == last_error_key:
                identical_error_count += 1
            else:
                last_error_key = error_key
                identical_error_count = 1 if error_key else 0

            """
                3.判断是否为临时错误（_is_transient_response）：
                -如果不是临时错误（即永久性错误）：
                    *尝试图片降级重试：调用 _strip_image_content(original_messages) 生成无图片版本的消息。
                    *如果原消息包含图片且新消息不同，则用新消息再调用一次底层方法。
                    *如果这次调用成功（非错误），则通过 _strip_image_content_inplace 原地修改原始消息列表（永久移除图片），并返回成功。
                    *否则（降级重试也失败或原消息无图片），直接返回原始错误响应（不再重试）。
                如果是临时错误，进入下面的重试逻辑。
            """
            if not self._is_transient_response(response):
                stripped = self._strip_image_content(original_messages)
                if stripped is not None and stripped != kw["messages"]:
                    logger.warning(
                        "Non-transient LLM error with image content, retrying without images"
                    )
                    retry_kw = dict(kw)
                    retry_kw["messages"] = stripped
                    result = await call(**retry_kw)
                    # Permanently strip images from the original messages so
                    # subsequent iterations do not repeat the error-retry cycle.
                    if result.finish_reason != "error":
                        self._strip_image_content_inplace(original_messages)
                    return result
                return response
            '''
                4.持久模式的相同错误上限检查：
                如果 persistent 且 identical_error_count >= _PERSISTENT_IDENTICAL_ERROR_LIMIT（10 次），
                则停止重试，返回错误。
            '''
            if persistent and identical_error_count >= self._PERSISTENT_IDENTICAL_ERROR_LIMIT:
                logger.warning(
                    "Stopping persistent retry after {} identical transient errors: {}",
                    identical_error_count,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Persistent retry stopped after {identical_error_count} identical errors."
                    )
                return response

            '''
                5.标准模式的尝试次数上限：
                如果非持久模式且 attempt > len(delays)（即超过 3 次重试），则停止，返回最后一次错误响应。
            '''
            if not persistent and attempt > len(delays):
                logger.warning(
                    "LLM request failed after {} retries, giving up: {}",
                    attempt,
                    (response.content or "")[:120].lower(),
                )
                if on_retry_wait:
                    await on_retry_wait(
                        f"Model request failed after {attempt} retries, giving up."
                    )
                break

            '''
                6.计算本次重试的延迟：
                -基础延迟：delays[min(attempt-1, len(delays)-1)]（1,2,4 秒，指数退避但有限）。
                -优先使用从响应中提取的 retry_after（调用 _extract_retry_after_from_response）。
                -持久模式下，延迟不能超过 _PERSISTENT_MAX_DELAY（60 秒）。
            '''
            base_delay = delays[min(attempt - 1, len(delays) - 1)]
            delay = self._extract_retry_after_from_response(response) or base_delay
            if persistent:
                delay = min(delay, self._PERSISTENT_MAX_DELAY)

            '''
                7.日志与心跳等待：
                -记录警告日志，包含尝试次数和错误摘要。
                -调用 _sleep_with_heartbeat 等待计算出的延迟，期间会通过 on_retry_wait 回调通知上层。
            '''
            logger.warning(
                "LLM transient error (attempt {}{}), retrying in {}s: {}",
                attempt,
                "+" if persistent and attempt > len(delays) else f"/{len(delays)}",
                int(round(delay)),
                (response.content or "")[:120].lower(),
            )
            await self._sleep_with_heartbeat(
                delay,
                attempt=attempt,
                persistent=persistent,
                on_retry_wait=on_retry_wait,
            )

        return last_response if last_response is not None else await call(**kw)

    @abstractmethod
    def get_default_model(self) -> str:
        """要求每个具体提供者返回该提供者的默认模型名称"""
        pass
