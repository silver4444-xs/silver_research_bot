"""
    所有与 OpenAI API 兼容的 LLM 提供者的统一实现，
    包括原生 OpenAI、Azure OpenAI、OpenRouter、DeepSeek、Moonshot（Kimi）、智谱、MiniMax、StepFun 等数百个服务商。
"""

from __future__ import annotations

import asyncio
import json
import hashlib
import importlib.util
import os
import secrets
import string
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import json_repair
from loguru import logger

if os.environ.get("LANGFUSE_SECRET_KEY") and importlib.util.find_spec("langfuse"):
    from langfuse.openai import AsyncOpenAI
else:
    if os.environ.get("LANGFUSE_SECRET_KEY"):
        import logging
        logging.getLogger(__name__).warning(
            "LANGFUSE_SECRET_KEY is set but langfuse is not installed; "
            "install with `pip install langfuse` to enable tracing"
        )
    from openai import AsyncOpenAI

from silver_research_bot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from silver_research_bot.providers.openai_responses import (
    consume_sdk_stream,
    convert_messages,
    convert_tools,
    parse_response_output,
)

if TYPE_CHECKING:
    from silver_research_bot.providers.registry import ProviderSpec

_ALLOWED_MSG_KEYS = frozenset({
    "role", "content", "tool_calls", "tool_call_id", "name",
    "reasoning_content", "extra_content",
})
'允许传递给 API 的消息顶层键（过滤掉内部字段如 _meta）'

_ALNUM = string.ascii_letters + string.digits
'用于生成随机字母数字字符串的常量定义'

_STANDARD_TC_KEYS = frozenset({"id", "type", "index", "function"})
'标准工具调用字段，用于提取扩展字段'
_STANDARD_FN_KEYS = frozenset({"name", "arguments"})
'标准function调用字段，用于提取扩展字段'

_DEFAULT_OPENROUTER_HEADERS = {
    "HTTP-Referer": "https://github.com/HKUDS/silver_research_bot",
    "X-OpenRouter-Title": "silver_research_bot",
    "X-OpenRouter-Categories": "cli-agent,personal-agent",
}
'为 OpenRouter 提供的默认头部（用于统计和识别）'

_KIMI_THINKING_MODELS: frozenset[str] = frozenset({
    "kimi-k2.5",
    "kimi-k2.6",
    "k2.6-code-preview",
})
'支持思维链的月之暗面（Kimi）模型集合'



def _is_kimi_thinking_model(model_name: str) -> bool:
    """
        判断是否为支持思考的 Kimi 模型（支持 moonshotai/kimi-k2.5 等 OpenRouter 格式）
        如果 model_name 指向一个具备 Kimi 思考能力的模型，则返回 True。
        支持两种形式：
        - 完全匹配：例如 _KIMI_THINKING_MODELS 中的 kimi-k2.5 / kimi-k2.6
        - 别名匹配：moonshotai/kimi-k2.5 -> 检查最后一个“/”之后的部分
                       是否与 _KIMI_THINKING_MODELS 匹配

        这同时涵盖了原生 Moonshot 提供商（纯别名）和
        OpenRouter 风格的名称（``“publisher/slug”``）
    """
    name = model_name.lower()
    if name in _KIMI_THINKING_MODELS:
        return True
    if "/" in name and name.rsplit("/", 1)[1] in _KIMI_THINKING_MODELS:
        return True
    return False


def _short_tool_id() -> str:
    """生成 9 位字母数字的短 ID，兼容所有提供商（Mistral 等要求长度固定）"""
    return "".join(secrets.choice(_ALNUM) for _ in range(9))


def _get(obj: Any, key: str) -> Any:
    """从字典或对象中安全获取属性"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _coerce_dict(value: Any) -> dict[str, Any] | None:
    """将 Pydantic 模型或字典转为普通 dict"""
    if value is None:
        return None
    if isinstance(value, dict):
        return value if value else None
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict) and dumped:
            return dumped
    return None


def _extract_tc_extras(tc: Any) -> tuple[
    dict[str, Any] | None,
    dict[str, Any] | None,
    dict[str, Any] | None,
]:
    """
    从 SDK 对象或字典中提取 extra_content、provider_specific_fields、function_provider_specific_fields，
    用于保留非标准字段（如 Gemini 的 extra_content）以及工具调用/函数中的任何非标准键
    """
    extra_content = _coerce_dict(_get(tc, "extra_content"))

    tc_dict = _coerce_dict(tc)
    prov = None
    fn_prov = None
    if tc_dict is not None:
        leftover = {k: v for k, v in tc_dict.items()
                    if k not in _STANDARD_TC_KEYS and k != "extra_content" and v is not None}
        if leftover:
            prov = leftover
        fn = _coerce_dict(tc_dict.get("function"))
        if fn is not None:
            fn_leftover = {k: v for k, v in fn.items()
                          if k not in _STANDARD_FN_KEYS and v is not None}
            if fn_leftover:
                fn_prov = fn_leftover
    else:
        prov = _coerce_dict(_get(tc, "provider_specific_fields"))
        fn_obj = _get(tc, "function")
        if fn_obj is not None:
            fn_prov = _coerce_dict(_get(fn_obj, "provider_specific_fields"))

    return extra_content, prov, fn_prov


def _uses_openrouter_attribution(spec: "ProviderSpec | None", api_base: str | None) -> bool:
    """判断是否应添加 OpenRouter 统计头（默认对 OpenRouter 提供者或 base URL 包含 openrouter 时启用）"""
    if spec and spec.name == "openrouter":
        return True
    return bool(api_base and "openrouter" in api_base.lower())


_RESPONSES_FAILURE_THRESHOLD = 3
'Responses API 熔断阈值'
_RESPONSES_PROBE_INTERVAL_S = 300  # 5 minutes
'Responses API 探测间隔'


def _is_direct_openai_base(api_base: str | None) -> bool:
    """判断是否为真正的 OpenAI 端点（而非第三方网关）"""
    if not api_base:
        return True
    normalized = api_base.strip().lower().rstrip("/")
    return "api.openai.com" in normalized and "openrouter" not in normalized


def _responses_circuit_key(
    model: str | None,
    default_model: str,
    reasoning_effort: str | None,
) -> str:
    """
    构造 Responses API 的熔断键
    熔断器的作用是 记录每个特定组合的连续失败次数，并在一段时间内跳过 Responses API，回退到标准的 Chat Completions API
    """
    model_name = (model or default_model).lower()
    effort = reasoning_effort.lower() if isinstance(reasoning_effort, str) else ""
    return f"{model_name}:{effort}"


class OpenAICompatProvider(LLMProvider):
    """
    适用于所有兼容 OpenAI 的 API 的统一提供程序。
    其核心亮点包括：
    1.智能参数适配：根据提供者特性（ProviderSpec）、模型名称、reasoning_effort 动态决定是否包含 temperature、选择 max_tokens / max_completion_tokens、注入提供者特有的思维链参数。
    2.Responses API 熔断降级：自动识别支持场景，对不兼容的组合自动回退到稳定 API，并带有熔断器避免反复试探。
    3.健壮的响应解析：同时支持原始 JSON 字典和 SDK 对象，统一提取文本、工具调用、用量统计，并兼容多个提供者对 cached_tokens 的不同表示。
    4.完善的流式处理：支持流式累积、心跳超时、工具调用参数合并。
    5.友好的错误处理：提取结构化的重试元数据，为本地模型提供可操作的建议。
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "gpt-4o",
        extra_headers: dict[str, str] | None = None,
        spec: ProviderSpec | None = None,
    ):
        """
        spec：ProviderSpec 对象，包含提供者的元信息（名称、环境变量、默认 base URL、是否网关、是否支持 prompt caching、模型特定覆盖等）。
        default_model：当调用未指定 model 时使用的模型名。
        extra_headers：额外的 HTTP 头（例如认证头）。
        _client：异步 OpenAI 客户端实例，配置了 max_retries=0（由上层重试控制）。
        _responses_failures 和 _responses_tripped_at：熔断器状态字典。
        """
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._spec = spec

        if api_key and spec and spec.env_key:
            self._setup_env(api_key, api_base)

        effective_base = api_base or (spec.default_api_base if spec else None) or None
        self._effective_base = effective_base
        default_headers = {"x-session-affinity": uuid.uuid4().hex}
        if _uses_openrouter_attribution(spec, effective_base):
            default_headers.update(_DEFAULT_OPENROUTER_HEADERS)
        if extra_headers:
            default_headers.update(extra_headers)

        self._client = AsyncOpenAI(
            api_key=api_key or "no-key",
            base_url=effective_base,
            default_headers=default_headers,
            max_retries=0,
        )

        # Responses API circuit breaker: skip after repeated failures,
        # probe again after _RESPONSES_PROBE_INTERVAL_S seconds.
        self._responses_failures: dict[str, int] = {}
        self._responses_tripped_at: dict[str, float] = {}

    def _setup_env(self, api_key: str, api_base: str | None) -> None:
        """ 根据 spec 设置环境变量（例如为某些网关提供者注入 OPENAI_API_KEY）。"""
        spec = self._spec
        if not spec or not spec.env_key:
            return
        if spec.is_gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key).replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    @classmethod
    def _apply_cache_control(
        cls,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """为支持 prompt caching 的提供者（如 Anthropic/Claude）在系统消息和最后一条用户消息、工具定义上添加 cache_control 标记，减少重复计算成本"""
        cache_marker = {"type": "ephemeral"}
        new_messages = list(messages)

        def _mark(msg: dict[str, Any]) -> dict[str, Any]:
            content = msg.get("content")
            if isinstance(content, str):
                return {**msg, "content": [
                    {"type": "text", "text": content, "cache_control": cache_marker},
                ]}
            if isinstance(content, list) and content:
                nc = list(content)
                nc[-1] = {**nc[-1], "cache_control": cache_marker}
                return {**msg, "content": nc}
            return msg

        if new_messages and new_messages[0].get("role") == "system":
            new_messages[0] = _mark(new_messages[0])
        if len(new_messages) >= 3:
            new_messages[-2] = _mark(new_messages[-2])

        new_tools = tools
        if tools:
            new_tools = list(tools)
            for idx in cls._tool_cache_marker_indices(new_tools):
                new_tools[idx] = {**new_tools[idx], "cache_control": cache_marker}
        return new_messages, new_tools

    @staticmethod
    def _normalize_tool_call_id(tool_call_id: Any) -> Any:
        """将任意工具调用 ID 规范化为 9 字符字母数字 ID（Mistral 等要求）"""
        if not isinstance(tool_call_id, str):
            return tool_call_id
        if len(tool_call_id) == 9 and tool_call_id.isalnum():
            return tool_call_id
        return hashlib.sha1(tool_call_id.encode()).hexdigest()[:9]

    @staticmethod
    def _normalize_tool_call_arguments(arguments: Any) -> str:
        """将工具参数强制转换为有效的 JSON 对象字符串（使用 json_repair 修复常见格式问题）"""
        if isinstance(arguments, str):
            stripped = arguments.strip()
            if not stripped:
                return "{}"
            try:
                parsed = json_repair.loads(stripped)
            except Exception:
                return "{}"
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
            return "{}"
        if isinstance(arguments, dict):
            return json.dumps(arguments, ensure_ascii=False)
        return "{}"

    def _sanitize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        -调用基类的 _sanitize_request_messages 过滤掉非法键。
        -标准化所有 tool_call_id。
        -修复 tool_calls 中的 arguments 字段。
        -对于 assistant 消息，如果有 tool_calls 则强制 content=None（某些网关拒绝同时有文本内容和工具调用的消息）。
        -最后调用 _enforce_role_alternation 处理角色交替。
        """
        sanitized = LLMProvider._sanitize_request_messages(messages, _ALLOWED_MSG_KEYS)
        id_map: dict[str, str] = {}

        def map_id(value: Any) -> Any:
            if not isinstance(value, str):
                return value
            return id_map.setdefault(value, self._normalize_tool_call_id(value))

        for clean in sanitized:
            if isinstance(clean.get("tool_calls"), list):
                normalized = []
                for tc in clean["tool_calls"]:
                    if not isinstance(tc, dict):
                        normalized.append(tc)
                        continue
                    tc_clean = dict(tc)
                    tc_clean["id"] = map_id(tc_clean.get("id"))
                    function = tc_clean.get("function")
                    if isinstance(function, dict):
                        function_clean = dict(function)
                        if "arguments" in function_clean:
                            function_clean["arguments"] = self._normalize_tool_call_arguments(
                                function_clean.get("arguments")
                            )
                        else:
                            function_clean["arguments"] = "{}"
                        tc_clean["function"] = function_clean
                    normalized.append(tc_clean)
                clean["tool_calls"] = normalized
                if clean.get("role") == "assistant":
                    # Some OpenAI-compatible gateways reject assistant messages
                    # that mix non-empty content with tool_calls.
                    clean["content"] = None
            if "tool_call_id" in clean and clean["tool_call_id"]:
                clean["tool_call_id"] = map_id(clean["tool_call_id"])
        return self._enforce_role_alternation(sanitized)

    # ------------------------------------------------------------------
    # Build kwargs 构造参数
    # ------------------------------------------------------------------

    @staticmethod
    def _supports_temperature(
        model_name: str,
        reasoning_effort: str | None = None,
    ) -> bool:
        """
            判断模型是否允许 temperature 参数
            当模型接受温度参数时返回 True。
            GPT-5 系列及推理模型（o1/o3/o4）在 reasoning_effort 设置为除 ``“none”`` 以外的任何值时，
            会拒绝温度参数
        """
        if reasoning_effort and reasoning_effort.lower() != "none":
            return False
        name = model_name.lower()
        return not any(token in name for token in ("gpt-5", "o1", "o3", "o4"))

    def _build_kwargs(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
        response_format: dict | None = None,
    ) -> dict[str, Any]:
        """
        构造 Chat Completions API 参数
        - 根据 spec 决定是否剥离模型前缀（如 anthropic/claude-3 → claude-3）。
        - 支持 prompt caching 时调用 _apply_cache_control。
        - 动态判断模型是否支持 temperature：推理模型（o1/o3/o4/GPT-5）在启用 reasoning_effort 时禁止 temperature。
        - 根据 spec.supports_max_completion_tokens 决定使用 max_completion_tokens 还是 max_tokens。
        - 支持模型级别的参数覆盖（spec.model_overrides）。
        - 为特定提供者（DashScope、MiniMax、火山引擎）注入 extra_body 中的思维链参数。
        - 为 Kimi 思考模型自动注入 thinking 参数（基于 reasoning_effort）。
        - 如果提供了 tools，则添加 tools 和 tool_choice
        """
        model_name = model or self.default_model
        spec = self._spec

        if spec and spec.supports_prompt_caching:
            model_name = model or self.default_model
            if any(model_name.lower().startswith(k) for k in ("anthropic/", "claude")):
                messages, tools = self._apply_cache_control(messages, tools)

        if spec and spec.strip_model_prefix:
            model_name = model_name.split("/")[-1]

        kwargs: dict[str, Any] = {
            "model": model_name,
            "messages": self._sanitize_messages(self._sanitize_empty_content(messages)),
        }

        # GPT-5 and reasoning models (o1/o3/o4) reject temperature when
        # reasoning_effort is active.  Only include it when safe.
        if self._supports_temperature(model_name, reasoning_effort):
            kwargs["temperature"] = temperature

        if spec and getattr(spec, "supports_max_completion_tokens", False):
            kwargs["max_completion_tokens"] = max(1, max_tokens)
        else:
            kwargs["max_tokens"] = max(1, max_tokens)

        if spec:
            model_lower = model_name.lower()
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    break

        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort

        # Provider-specific thinking parameters.
        # Only sent when reasoning_effort is explicitly configured so that
        # the provider default is preserved otherwise.
        if spec and reasoning_effort is not None:
            thinking_enabled = reasoning_effort.lower() != "minimal"
            extra: dict[str, Any] | None = None
            if spec.name == "dashscope":
                extra = {"enable_thinking": thinking_enabled}
            elif spec.name == "minimax":
                extra = {"reasoning_split": thinking_enabled}
            elif spec.name in (
                "volcengine", "volcengine_coding_plan",
                "byteplus", "byteplus_coding_plan",
            ):
                extra = {
                    "thinking": {"type": "enabled" if thinking_enabled else "disabled"}
                }
            if extra:
                kwargs.setdefault("extra_body", {}).update(extra)

        # Model-level thinking injection for Kimi thinking-capable models.
        # Strip any provider prefix (e.g. "moonshotai/") before the set lookup
        # so that OpenRouter-style names like "moonshotai/kimi-k2.5" are handled
        # identically to bare names like "kimi-k2.5".
        if reasoning_effort is not None and _is_kimi_thinking_model(model_name):
            thinking_enabled = reasoning_effort.lower() != "minimal"
            kwargs.setdefault("extra_body", {}).update(
                {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
            )

        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        if response_format is not None:
            kwargs["response_format"] = response_format

        return kwargs

    """
    Responses API 相关方法
    OpenAI 新推出的 Responses API 是一个统一接口，目前仅对直接 OpenAI 客户（api.openai.com）
    且使用推理模型或 GPT‑5 系列时可用。以下方法实现 选择性启用 + 熔断降级。
    """
    def _should_use_responses_api(
        self,
        model: str | None,
        reasoning_effort: str | None,
    ) -> bool:
        """
        仅当提供者为 OpenAI 且直接调用 api.openai.com（非第三方网关）时可能启用。
        启用条件：reasoning_effort 不为 "none"，或模型名包含 gpt-5/o1/o3/o4。
        熔断检查：如果同一 (model, reasoning_effort) 连续失败 3 次，则进入冷却期 5 分钟，期间返回 False。
        """
        if self._spec and self._spec.name != "openai":
            return False
        if not _is_direct_openai_base(self._effective_base):
            return False

        model_name = (model or self.default_model).lower()
        wants = False
        if reasoning_effort and reasoning_effort.lower() != "none":
            wants = True
        elif any(token in model_name for token in ("gpt-5", "o1", "o3", "o4")):
            wants = True
        if not wants:
            return False

        # Circuit breaker: skip after repeated failures, probe periodically.
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        failures = self._responses_failures.get(key, 0)
        if failures >= _RESPONSES_FAILURE_THRESHOLD:
            tripped = self._responses_tripped_at.get(key, 0.0)
            if (time.monotonic() - tripped) < _RESPONSES_PROBE_INTERVAL_S:
                return False
            # Half-open: allow one probe attempt
        return True

    def _record_responses_failure(self, model: str | None, reasoning_effort: str | None) -> None:
        """失败记录：增加失败计数，当达到阈值时记录熔断开启时间并打印警告"""
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        count = self._responses_failures.get(key, 0) + 1
        self._responses_failures[key] = count
        if count >= _RESPONSES_FAILURE_THRESHOLD:
            self._responses_tripped_at[key] = time.monotonic()
            logger.warning(
                "Responses API circuit open for {} — falling back to Chat Completions",
                key,
            )

    def _record_responses_success(self, model: str | None, reasoning_effort: str | None) -> None:
        """成功记录：清除失败计数和熔断时间（重置电路）"""
        key = _responses_circuit_key(model, self.default_model, reasoning_effort)
        self._responses_failures.pop(key, None)
        self._responses_tripped_at.pop(key, None)

    @staticmethod
    def _should_fallback_from_responses_error(e: Exception) -> bool:
        """
        作用：判断一个异常是否属于 Responses API 不兼容 导致的错误。

        判断依据：
            HTTP 状态码为 400、404 或 422
            错误消息中包含特定标记，如 "responses"、"response api"、"max_output_tokens"、"instructions"、"unsupported"、"unknown parameter" 等
        若符合，则回退到 Chat Completions API；否则直接抛出异常。
        """
        response = getattr(e, "response", None)
        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)
        if status_code not in {400, 404, 422}:
            return False

        body = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        body_text = str(body).lower() if body is not None else ""
        compatibility_markers = (
            "responses",
            "response api",
            "max_output_tokens",
            "instructions",
            "previous_response",
            "unsupported",
            "not supported",
            "unknown parameter",
            "unrecognized request argument",
        )
        return any(marker in body_text for marker in compatibility_markers)

    def _build_responses_body(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        max_tokens: int,
        temperature: float,
        reasoning_effort: str | None,
        tool_choice: str | dict[str, Any] | None,
    ) -> dict[str, Any]:
        """
        功能：构造 Responses API 的请求体。
        步骤：
        1.使用 convert_messages 将聊天消息拆分为 instructions（系统提示）和 input（消息序列）。
        2.设置 model、instructions、input、max_output_tokens、store=False、stream=False。
        3.条件添加 temperature（同上 _supports_temperature）。
        4.如果 reasoning_effort 且不为 "none"，添加 reasoning 对象和 include = ["reasoning.encrypted_content"]。
        5.如果提供工具，调用 convert_tools 转换并添加 tools 与 tool_choice
        """
        """Build a Responses API body for direct OpenAI requests."""
        model_name = model or self.default_model
        sanitized_messages = self._sanitize_messages(self._sanitize_empty_content(messages))
        instructions, input_items = convert_messages(sanitized_messages)

        body: dict[str, Any] = {
            "model": model_name,
            "instructions": instructions or None,
            "input": input_items,
            "max_output_tokens": max(1, max_tokens),
            "store": False,
            "stream": False,
        }

        if self._supports_temperature(model_name, reasoning_effort):
            body["temperature"] = temperature

        if reasoning_effort and reasoning_effort.lower() != "none":
            body["reasoning"] = {"effort": reasoning_effort}
            body["include"] = ["reasoning.encrypted_content"]

        if tools:
            body["tools"] = convert_tools(tools)
            body["tool_choice"] = tool_choice or "auto"

        return body

    # ------------------------------------------------------------------
    # Response parsing 响应解析方法
    # ------------------------------------------------------------------

    @staticmethod
    def _maybe_mapping(value: Any) -> dict[str, Any] | None:
        """
        作用：将对象转换为字典。支持原生 dict 或 Pydantic 模型（通过 model_dump()）。
        使用场景：统一处理 SDK 返回的对象和原始 JSON 字典。
        """
        if isinstance(value, dict):
            return value
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            if isinstance(dumped, dict):
                return dumped
        return None

    @classmethod
    def _extract_text_content(cls, value: Any) -> str | None:
        """从可能是字符串、列表或带 text 属性的对象中提取纯文本内容，递归处理列表，最终拼接成一个字符串。"""
        if value is None:
            return None
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            parts: list[str] = []
            for item in value:
                item_map = cls._maybe_mapping(item)
                if item_map:
                    text = item_map.get("text")
                    if isinstance(text, str):
                        parts.append(text)
                        continue
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
                    continue
                if isinstance(item, str):
                    parts.append(item)
            return "".join(parts) or None
        return str(value)

    @classmethod
    def _extract_usage(cls, response: Any) -> dict[str, int]:
        """
        提取 token 用量：支持 dict 和 SDK 对象。
        标准化 cached_tokens：按优先级从以下路径提取：
            1.prompt_tokens_details.cached_tokens（OpenAI、智谱、MiniMax、通义、Mistral、xAI 等）
            2.cached_tokens 顶层（StepFun、Moonshot）
            3.prompt_cache_hit_tokens（DeepSeek、SiliconFlow）
        返回值包含 prompt_tokens、completion_tokens、total_tokens，以及可能的 cached_tokens
        """
        # --- resolve usage object ---
        usage_obj = None
        response_map = cls._maybe_mapping(response)
        if response_map is not None:
            usage_obj = response_map.get("usage")
        elif hasattr(response, "usage") and response.usage:
            usage_obj = response.usage

        usage_map = cls._maybe_mapping(usage_obj)
        if usage_map is not None:
            result = {
                "prompt_tokens": int(usage_map.get("prompt_tokens") or 0),
                "completion_tokens": int(usage_map.get("completion_tokens") or 0),
                "total_tokens": int(usage_map.get("total_tokens") or 0),
            }
        elif usage_obj:
            result = {
                "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
                "completion_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
            }
        else:
            return {}

        # --- cached_tokens (normalised across providers) ---
        # Try nested paths first (dict), fall back to attribute (SDK object).
        # Priority order ensures the most specific field wins.
        for path in (
            ("prompt_tokens_details", "cached_tokens"),  # OpenAI/Zhipu/MiniMax/Qwen/Mistral/xAI
            ("cached_tokens",),                          # StepFun/Moonshot (top-level)
            ("prompt_cache_hit_tokens",),                # DeepSeek/SiliconFlow
        ):
            cached = cls._get_nested_int(usage_map, path)
            if not cached and usage_obj:
                cached = cls._get_nested_int(usage_obj, path)
            if cached:
                result["cached_tokens"] = cached
                break

        return result

    @staticmethod
    def _get_nested_int(obj: Any, path: tuple[str, ...]) -> int:
        """
        按路径（元组）逐层深入对象（字典或属性），返回整数值，不存在返回 0。
        """
        current = obj
        for segment in path:
            if current is None:
                return 0
            if isinstance(current, dict):
                current = current.get(segment)
            else:
                current = getattr(current, segment, None)
        return int(current or 0) if current is not None else 0

    def _parse(self, response: Any) -> LLMResponse:
        """
            非流式响应解析,处理三种情况：
            如果 response 是字符串 → 直接返回 LLMResponse(content=...)
            如果是 dict（或可通过 _maybe_mapping 转换的 dict）：
                - 处理 choices 为空的情况（某些提供者直接返回 content 字段）
                - 正常 choices 流程：提取 message 中的 content、reasoning_content、tool_calls
                - 特殊处理 StepFun：当 content 为空时使用 reasoning 作为回退内容
                - 遍历所有 choices 收集工具调用
                - 解析参数（JSON 或字符串）并构造 ToolCallRequest
            如果是 SDK 对象（如 openai.types.chat.ChatCompletion）：
                - 通过属性访问提取相同信息
                - 同样收集工具调用

        """
        if isinstance(response, str):
            return LLMResponse(content=response, finish_reason="stop")

        response_map = self._maybe_mapping(response)
        if response_map is not None:
            choices = response_map.get("choices") or []
            if not choices:
                content = self._extract_text_content(
                    response_map.get("content") or response_map.get("output_text")
                )
                reasoning_content = self._extract_text_content(
                    response_map.get("reasoning_content")
                )
                if content is not None:
                    return LLMResponse(
                        content=content,
                        reasoning_content=reasoning_content,
                        finish_reason=str(response_map.get("finish_reason") or "stop"),
                        usage=self._extract_usage(response_map),
                    )
                return LLMResponse(content="Error: API returned empty choices.", finish_reason="error")

            choice0 = self._maybe_mapping(choices[0]) or {}
            msg0 = self._maybe_mapping(choice0.get("message")) or {}
            content = self._extract_text_content(msg0.get("content"))
            finish_reason = str(choice0.get("finish_reason") or "stop")

            raw_tool_calls: list[Any] = []
            # StepFun Plan: fallback to reasoning field when content is empty
            if not content and msg0.get("reasoning"):
                content = self._extract_text_content(msg0.get("reasoning"))
            reasoning_content = msg0.get("reasoning_content")
            if not reasoning_content and msg0.get("reasoning"):
                reasoning_content = self._extract_text_content(msg0.get("reasoning"))
            for ch in choices:
                ch_map = self._maybe_mapping(ch) or {}
                m = self._maybe_mapping(ch_map.get("message")) or {}
                tool_calls = m.get("tool_calls")
                if isinstance(tool_calls, list) and tool_calls:
                    raw_tool_calls.extend(tool_calls)
                    if ch_map.get("finish_reason") in ("tool_calls", "stop"):
                        finish_reason = str(ch_map["finish_reason"])
                if not content:
                    content = self._extract_text_content(m.get("content"))
                if not reasoning_content:
                    reasoning_content = m.get("reasoning_content")

            parsed_tool_calls = []
            for tc in raw_tool_calls:
                tc_map = self._maybe_mapping(tc) or {}
                fn = self._maybe_mapping(tc_map.get("function")) or {}
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json_repair.loads(args)
                ec, prov, fn_prov = _extract_tc_extras(tc)
                parsed_tool_calls.append(ToolCallRequest(
                    id=_short_tool_id(),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                    extra_content=ec,
                    provider_specific_fields=prov,
                    function_provider_specific_fields=fn_prov,
                ))

            return LLMResponse(
                content=content,
                tool_calls=parsed_tool_calls,
                finish_reason=finish_reason,
                usage=self._extract_usage(response_map),
                reasoning_content=reasoning_content if isinstance(reasoning_content, str) else None,
            )

        if not response.choices:
            return LLMResponse(content="Error: API returned empty choices.", finish_reason="error")

        choice = response.choices[0]
        msg = choice.message
        content = msg.content
        finish_reason = choice.finish_reason

        raw_tool_calls: list[Any] = []
        for ch in response.choices:
            m = ch.message
            if hasattr(m, "tool_calls") and m.tool_calls:
                raw_tool_calls.extend(m.tool_calls)
                if ch.finish_reason in ("tool_calls", "stop"):
                    finish_reason = ch.finish_reason
            if not content and m.content:
                content = m.content
            if not content and getattr(m, "reasoning", None):
                content = m.reasoning

        tool_calls = []
        for tc in raw_tool_calls:
            args = tc.function.arguments
            if isinstance(args, str):
                args = json_repair.loads(args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            tool_calls.append(ToolCallRequest(
                id=_short_tool_id(),
                name=tc.function.name,
                arguments=args,
                extra_content=ec,
                provider_specific_fields=prov,
                function_provider_specific_fields=fn_prov,
            ))

        reasoning_content = getattr(msg, "reasoning_content", None) or None
        if not reasoning_content and getattr(msg, "reasoning", None):
            reasoning_content = msg.reasoning

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason or "stop",
            usage=self._extract_usage(response),
            reasoning_content=reasoning_content,
        )

    @classmethod
    def _parse_chunks(cls, chunks: list[Any]) -> LLMResponse:
        """
        流式响应块解析
        输入：一个由多个流式 chunk（可以是字符串、dict 或 SDK 对象）组成的列表。
        处理：
            - 维护 content_parts、reasoning_parts、tc_bufs（按 index 累积部分工具调用参数）
            - 对每个 chunk：
                *如果是字符串，直接追加到 content
                *否则统一转为 dict 格式（通过 _maybe_mapping）
                *提取 delta.content、delta.reasoning_content / delta.reasoning
                *提取 delta.tool_calls 并调用 _accum_tc 累积（arguments 可能分多次传输，直接拼接）
            - 最终合并所有部分，构建完整的 LLMResponse
        """
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_bufs: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        def _accum_tc(tc: Any, idx_hint: int) -> None:
            """Accumulate one streaming tool-call delta into *tc_bufs*."""
            tc_index: int = _get(tc, "index") if _get(tc, "index") is not None else idx_hint
            buf = tc_bufs.setdefault(tc_index, {
                "id": "", "name": "", "arguments": "",
                "extra_content": None, "prov": None, "fn_prov": None,
            })
            tc_id = _get(tc, "id")
            if tc_id:
                buf["id"] = str(tc_id)
            fn = _get(tc, "function")
            if fn is not None:
                fn_name = _get(fn, "name")
                if fn_name:
                    buf["name"] = str(fn_name)
                fn_args = _get(fn, "arguments")
                if fn_args:
                    buf["arguments"] += str(fn_args)
            ec, prov, fn_prov = _extract_tc_extras(tc)
            if ec:
                buf["extra_content"] = ec
            if prov:
                buf["prov"] = prov
            if fn_prov:
                buf["fn_prov"] = fn_prov

        for chunk in chunks:
            if isinstance(chunk, str):
                content_parts.append(chunk)
                continue

            chunk_map = cls._maybe_mapping(chunk)
            if chunk_map is not None:
                choices = chunk_map.get("choices") or []
                if not choices:
                    usage = cls._extract_usage(chunk_map) or usage
                    text = cls._extract_text_content(
                        chunk_map.get("content") or chunk_map.get("output_text")
                    )
                    if text:
                        content_parts.append(text)
                    continue
                choice = cls._maybe_mapping(choices[0]) or {}
                if choice.get("finish_reason"):
                    finish_reason = str(choice["finish_reason"])
                delta = cls._maybe_mapping(choice.get("delta")) or {}
                text = cls._extract_text_content(delta.get("content"))
                if text:
                    content_parts.append(text)
                text = cls._extract_text_content(delta.get("reasoning_content"))
                if not text:
                    text = cls._extract_text_content(delta.get("reasoning"))
                if text:
                    reasoning_parts.append(text)
                for idx, tc in enumerate(delta.get("tool_calls") or []):
                    _accum_tc(tc, idx)
                usage = cls._extract_usage(chunk_map) or usage
                continue

            if not chunk.choices:
                usage = cls._extract_usage(chunk) or usage
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish_reason = choice.finish_reason
            delta = choice.delta
            if delta and delta.content:
                content_parts.append(delta.content)
            if delta:
                reasoning = getattr(delta, "reasoning_content", None)
                if not reasoning:
                    reasoning = getattr(delta, "reasoning", None)
                if reasoning:
                    reasoning_parts.append(reasoning)
            for tc in (delta.tool_calls or []) if delta else []:
                _accum_tc(tc, getattr(tc, "index", 0))

        return LLMResponse(
            content="".join(content_parts) or None,
            tool_calls=[
                ToolCallRequest(
                    id=b["id"] or _short_tool_id(),
                    name=b["name"],
                    arguments=json_repair.loads(b["arguments"]) if b["arguments"] else {},
                    extra_content=b.get("extra_content"),
                    provider_specific_fields=b.get("prov"),
                    function_provider_specific_fields=b.get("fn_prov"),
                )
                for b in tc_bufs.values()
            ],
            finish_reason=finish_reason,
            usage=usage,
            reasoning_content="".join(reasoning_parts) or None,
        )

    @classmethod
    def _extract_error_metadata(cls, e: Exception) -> dict[str, Any]:
        """
        从异常对象中提取结构化错误信息：
            - 状态码（status_code）
            - 错误类型和错误代码（通过基类的 _extract_error_type_code）
            - retry_after 秒数（通过 _extract_retry_after_from_headers）
            - x-should-retry 头部（某些提供者自定义重试建议）
            - 错误类型（timeout / connection）通过异常类名判断
        """
        response = getattr(e, "response", None)
        headers = getattr(response, "headers", None)
        payload = (
            getattr(e, "body", None)
            or getattr(e, "doc", None)
            or getattr(response, "text", None)
        )
        if payload is None and response is not None:
            response_json = getattr(response, "json", None)
            if callable(response_json):
                try:
                    payload = response_json()
                except Exception:
                    payload = None
        error_type, error_code = LLMProvider._extract_error_type_code(payload)

        status_code = getattr(e, "status_code", None)
        if status_code is None and response is not None:
            status_code = getattr(response, "status_code", None)

        should_retry: bool | None = None
        if headers is not None:
            raw = headers.get("x-should-retry")
            if isinstance(raw, str):
                lowered = raw.strip().lower()
                if lowered == "true":
                    should_retry = True
                elif lowered == "false":
                    should_retry = False

        error_kind: str | None = None
        error_name = e.__class__.__name__.lower()
        if "timeout" in error_name:
            error_kind = "timeout"
        elif "connection" in error_name:
            error_kind = "connection"

        return {
            "error_status_code": int(status_code) if status_code is not None else None,
            "error_kind": error_kind,
            "error_type": error_type,
            "error_code": error_code,
            "error_retry_after_s": cls._extract_retry_after_from_headers(headers),
            "error_should_retry": should_retry,
        }

    @staticmethod
    def _handle_error(
        e: Exception,
        *,
        spec: ProviderSpec | None = None,
        api_base: str | None = None,
    ) -> LLMResponse:
        """
        生成用户友好的错误消息（截取前 500 字符）。
        对于本地模型（spec.is_local 为真）且错误包含 502 / connection / refused 时，添加提示信息，指导用户检查本地服务地址。
        尝试从 headers 或错误文本中提取 retry_after。
        返回 LLMResponse(finish_reason="error", ...) 并附带所有元数据
        """
        body = (
            getattr(e, "doc", None)
            or getattr(e, "body", None)
            or getattr(getattr(e, "response", None), "text", None)
        )
        body_text = body if isinstance(body, str) else str(body) if body is not None else ""
        msg = f"Error: {body_text.strip()[:500]}" if body_text.strip() else f"Error calling LLM: {e}"

        text = f"{body_text} {e}".lower()
        if spec and spec.is_local and ("502" in text or "connection" in text or "refused" in text):
            msg += (
                "\nHint: this is a local model endpoint. Check that the local server is reachable at "
                f"{api_base or spec.default_api_base}, and if you are using a proxy/tunnel, make sure it "
                "can reach your local Ollama/vLLM service instead of routing localhost through the remote host."
            )

        response = getattr(e, "response", None)
        retry_after = LLMProvider._extract_retry_after_from_headers(getattr(response, "headers", None))
        if retry_after is None:
            retry_after = LLMProvider._extract_retry_after(msg)
        return LLMResponse(
            content=msg,
            finish_reason="error",
            retry_after=retry_after,
            **OpenAICompatProvider._extract_error_metadata(e),
        )

    # ------------------------------------------------------------------
    # Public API 公开 API 方法
    # ------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        """
        流程：
        1.如果 _should_use_responses_api 为真，则尝试调用 _client.responses.create（使用 _build_responses_body）。
        2.成功 → 调用 parse_response_output（外部函数）转换为 LLMResponse，并记录成功。
        3.失败且 _should_fallback_from_responses_error 为真 → 记录失败，回退到 Chat Completions API。
        4.构建 Chat Completions 参数（_build_kwargs），调用 _client.chat.completions.create。
        5.调用 _parse 解析响应。
        6.任何异常都交给 _handle_error 返回错误响应。
        """
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    result = parse_response_output(await self._client.responses.create(**body))
                    self._record_responses_success(model, reasoning_effort)
                    return result
                except Exception as responses_error:
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice, response_format,
            )
            return self._parse(await self._client.chat.completions.create(**kwargs))
        except Exception as e:
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

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
        流程与 chat 类似，但：
            -对 Responses API：设置 stream=True，通过 consume_sdk_stream 辅助函数处理流式事件（并支持 on_content_delta 回调）。
            -对 Chat Completions API：设置 stream=True 和 stream_options={"include_usage": True}。
            -增加 空闲超时 控制（默认 90 秒，可通过环境变量 silver_research_bot_STREAM_IDLE_TIMEOUT_S 覆盖）。
            -收集所有 chunk，最后调用 _parse_chunks 合并。
        超时异常：单独捕获 asyncio.TimeoutError 并返回 error_kind="timeout" 的响应
        """
        idle_timeout_s = int(os.environ.get("silver_research_bot_STREAM_IDLE_TIMEOUT_S", "90"))
        try:
            if self._should_use_responses_api(model, reasoning_effort):
                try:
                    body = self._build_responses_body(
                        messages, tools, model, max_tokens, temperature,
                        reasoning_effort, tool_choice,
                    )
                    body["stream"] = True
                    stream = await self._client.responses.create(**body)

                    async def _timed_stream():
                        stream_iter = stream.__aiter__()
                        while True:
                            try:
                                yield await asyncio.wait_for(
                                    stream_iter.__anext__(),
                                    timeout=idle_timeout_s,
                                )
                            except StopAsyncIteration:
                                break

                    content, tool_calls, finish_reason, usage, reasoning_content = await consume_sdk_stream(
                        _timed_stream(),
                        on_content_delta,
                    )
                    self._record_responses_success(model, reasoning_effort)
                    return LLMResponse(
                        content=content or None,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                        usage=usage,
                        reasoning_content=reasoning_content,
                    )
                except Exception as responses_error:
                    if not self._should_fallback_from_responses_error(responses_error):
                        raise
                    self._record_responses_failure(model, reasoning_effort)

            kwargs = self._build_kwargs(
                messages, tools, model, max_tokens, temperature,
                reasoning_effort, tool_choice,
            )
            kwargs["stream"] = True
            kwargs["stream_options"] = {"include_usage": True}
            stream = await self._client.chat.completions.create(**kwargs)
            chunks: list[Any] = []
            stream_iter = stream.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_iter.__anext__(),
                        timeout=idle_timeout_s,
                    )
                except StopAsyncIteration:
                    break
                chunks.append(chunk)
                if on_content_delta and chunk.choices:
                    text = getattr(chunk.choices[0].delta, "content", None)
                    if text:
                        await on_content_delta(text)
            return self._parse_chunks(chunks)
        except asyncio.TimeoutError:
            return LLMResponse(
                content=(
                    f"Error calling LLM: stream stalled for more than "
                    f"{idle_timeout_s} seconds"
                ),
                finish_reason="error",
                error_kind="timeout",
            )
        except Exception as e:
            return self._handle_error(e, spec=self._spec, api_base=self.api_base)

    def get_default_model(self) -> str:
        return self.default_model

    async def embed(self, text: str, model: str | None = None) -> list[float]:
        result = await self.embed_batch([text], model=model)
        return result[0] if result else []

    async def embed_batch(
        self, texts: list[str], model: str | None = None
    ) -> list[list[float]]:
        if not texts:
            return []
        model_name = model or "text-embedding-3-small"
        model_name = self._strip_provider_prefix(model_name)
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), 100):
            batch = texts[i:i + 100]
            try:
                resp = await self._client.embeddings.create(
                    input=batch, model=model_name,
                )
                all_embeddings.extend([d.embedding for d in resp.data])
            except Exception as e:
                raise RuntimeError(f"Embedding API call failed: {e}") from e
        return all_embeddings
