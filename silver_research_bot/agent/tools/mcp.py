"""
    实现了一个MCP（Model Context Protocol）客户端，
    用于连接外部 MCP 服务器，并将其提供的工具（tools）、资源（resources）、提示（prompts）
    包装成 silver_research_bot 原生 Tool 子类，然后注册到 ToolRegistry 中，使 Agent 能够动态调用外部能力.
"""

import asyncio
from contextlib import AsyncExitStack
from typing import Any

import httpx
from loguru import logger

from silver_research_bot.agent.tools.base import Tool
from silver_research_bot.agent.tools.registry import ToolRegistry

_TRANSIENT_EXC_NAMES: frozenset[str] = frozenset((
    "ClosedResourceError",
    "BrokenResourceError",
    "EndOfStream",
    "BrokenPipeError",
    "ConnectionResetError",
    "ConnectionRefusedError",
    "ConnectionAbortedError",
    "ConnectionError",
))
'仅需重试一次的临时连接错误。这些错误通常发生在 MCP 服务器重启时，或调用之间网络连接中断时。'

def _is_transient(exc: BaseException) -> bool:
    """判断异常是否为临时性连接错误."""
    return type(exc).__name__ in _TRANSIENT_EXC_NAMES


def _extract_nullable_branch(options: Any) -> tuple[dict[str, Any], bool] | None:
    """处理 JSON Schema 中的 oneOf / anyOf 结构，提取其中唯一的非 null 分支."""
    if not isinstance(options, list):
        return None

    non_null: list[dict[str, Any]] = []
    saw_null = False
    for option in options:
        if not isinstance(option, dict):
            return None
        if option.get("type") == "null":
            saw_null = True
            continue
        non_null.append(option)

    if saw_null and len(non_null) == 1:
        return non_null[0], True
    return None


def _normalize_schema_for_openai(schema: Any) -> dict[str, Any]:
    """将 MCP 服务器返回的 JSON Schema 转换成 OpenAI 兼容的格式"""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}

    normalized = dict(schema)

    raw_type = normalized.get("type")
    if isinstance(raw_type, list):
        non_null = [item for item in raw_type if item != "null"]
        if "null" in raw_type and len(non_null) == 1:
            normalized["type"] = non_null[0]
            normalized["nullable"] = True

    for key in ("oneOf", "anyOf"):
        nullable_branch = _extract_nullable_branch(normalized.get(key))
        if nullable_branch is not None:
            branch, _ = nullable_branch
            merged = {k: v for k, v in normalized.items() if k != key}
            merged.update(branch)
            normalized = merged
            normalized["nullable"] = True
            break

    if "properties" in normalized and isinstance(normalized["properties"], dict):
        normalized["properties"] = {
            name: _normalize_schema_for_openai(prop) if isinstance(prop, dict) else prop
            for name, prop in normalized["properties"].items()
        }

    if "items" in normalized and isinstance(normalized["items"], dict):
        normalized["items"] = _normalize_schema_for_openai(normalized["items"])

    if normalized.get("type") != "object":
        return normalized

    normalized.setdefault("properties", {})
    normalized.setdefault("required", [])
    return normalized


class MCPToolWrapper(Tool):
    """将一个MCP server tool包装成 silver_research_bot 原生 Tool 子类"""

    def __init__(self, session, server_name: str, tool_def, tool_timeout: int = 30):
        self._session = session
        '会话实例'
        self._original_name = tool_def.name
        '工具原始名称'
        self._name = f"mcp_{server_name}_{tool_def.name}"
        '名称：mcp_{server_name}_{tool_def.name}，避免不同服务器的同名工具冲突'
        self._description = tool_def.description or tool_def.name
        '工具描述（无描述时使用工具名）'
        raw_schema = tool_def.inputSchema or {"type": "object", "properties": {}}
        '工具输入Schema，无定义时使用默认空对象结构'
        self._parameters = _normalize_schema_for_openai(raw_schema)
        '标准化为OpenAI格式的工具参数'
        self._tool_timeout = tool_timeout
        '工具调用超时时间'

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        """
        -调用 self._session.call_tool(original_name, arguments=kwargs)，设置超时（tool_timeout，默认30秒）。
        -支持一次自动重试：如果捕获到临时性错误且是第一次尝试，等待1秒后重试。
        -处理超时（返回超时消息）、取消（区分外部取消与内部取消）、其他异常。
        -成功后将结果中的 TextContent 块拼接成字符串返回。
        """
        from mcp import types

        for attempt in range(2):  # At most 1 retry
            try:
                result = await asyncio.wait_for(
                    self._session.call_tool(self._original_name, arguments=kwargs),
                    timeout=self._tool_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP tool '{}' timed out after {}s", self._name, self._tool_timeout
                )
                return f"(MCP tool call timed out after {self._tool_timeout}s)"
            except asyncio.CancelledError:
                # MCP SDK's anyio cancel scopes can leak CancelledError on timeout/failure.
                # Re-raise only if our task was externally cancelled (e.g. /stop).
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP tool '{}' was cancelled by server/SDK", self._name)
                return "(MCP tool call was cancelled)"
            except Exception as exc:
                if _is_transient(exc):
                    if attempt == 0:
                        logger.warning(
                            "MCP tool '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)  # Brief backoff before retry
                        continue
                    # Second transient failure — give up with retry-specific message
                    logger.error(
                        "MCP tool '{}' failed after retry: {}: {}",
                        self._name,
                        type(exc).__name__,
                        exc,
                    )
                    return f"(MCP tool call failed after retry: {type(exc).__name__})"
                logger.exception(
                    "MCP tool '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP tool call failed: {type(exc).__name__})"
            else:
                # Success — extract result
                parts = []
                for block in result.content:
                    if isinstance(block, types.TextContent):
                        parts.append(block.text)
                    else:
                        parts.append(str(block))
                return "\n".join(parts) or "(no output)"

        return "(MCP tool call failed)"  # Unreachable, but satisfies type checkers


class MCPResourceWrapper(Tool):
    """将一个 MCP URI 资源包装为只读的 silver_research_bot Tool."""

    def __init__(self, session, server_name: str, resource_def, resource_timeout: int = 30):
        self._session = session
        'MCP会话实例'
        self._uri = resource_def.uri
        '资源的统一资源标识符'
        self._name = f"mcp_{server_name}_resource_{resource_def.name}"
        '资源名称，带mcp前缀避免冲突'
        desc = resource_def.description or resource_def.name
        '资源描述（无描述时使用资源名）'
        self._description = f"[MCP Resource] {desc}\nURI: {self._uri}"
        '格式化后的完整资源描述'
        self._parameters: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        '资源调用参数，默认为空对象结构'
        self._resource_timeout = resource_timeout
        '资源读取超时时间'

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        """调用 session.read_resource(uri)，处理类似工具的错误和重试，返回资源内容（文本或二进制占位符）"""
        from mcp import types

        for attempt in range(2):
            try:
                result = await asyncio.wait_for(
                    self._session.read_resource(self._uri),
                    timeout=self._resource_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP resource '{}' timed out after {}s", self._name, self._resource_timeout
                )
                return f"(MCP resource read timed out after {self._resource_timeout}s)"
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP resource '{}' was cancelled by server/SDK", self._name)
                return "(MCP resource read was cancelled)"
            except Exception as exc:
                if _is_transient(exc):
                    if attempt == 0:
                        logger.warning(
                            "MCP resource '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)
                        continue
                    logger.error(
                        "MCP resource '{}' failed after retry: {}: {}",
                        self._name,
                        type(exc).__name__,
                        exc,
                    )
                    return f"(MCP resource read failed after retry: {type(exc).__name__})"
                logger.exception(
                    "MCP resource '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP resource read failed: {type(exc).__name__})"
            else:
                parts: list[str] = []
                for block in result.contents:
                    if isinstance(block, types.TextResourceContents):
                        parts.append(block.text)
                    elif isinstance(block, types.BlobResourceContents):
                        parts.append(f"[Binary resource: {len(block.blob)} bytes]")
                    else:
                        parts.append(str(block))
                return "\n".join(parts) or "(no output)"

        return "(MCP resource read failed)"  # Unreachable


class MCPPromptWrapper(Tool):
    """将一个MCP prompt 包装为只读的 silver_research_bot Tool."""

    def __init__(self, session, server_name: str, prompt_def, prompt_timeout: int = 30):
        self._session = session
        'MCP会话实例'
        self._prompt_name = prompt_def.name
        '提示词原始名称'
        self._name = f"mcp_{server_name}_prompt_{prompt_def.name}"
        '带MCP前缀和服务端标识的提示词唯一名称，避免冲突'
        desc = prompt_def.description or prompt_def.name
        '提示词描述（无描述时使用提示词名称）'
        self._description = (
            f"[MCP Prompt] {desc}\nReturns a filled prompt template that can be used as a workflow guide.")
        '格式化后的提示词完整描述，说明功能为返回可作为工作流指南的填充后提示词模板'
        self._prompt_timeout = prompt_timeout
        '提示词调用超时时间'

        # Build parameters from prompt arguments
        properties: dict[str, Any] = {}
        '存储提示词参数的属性集合'
        required: list[str] = []
        '存储必填的提示词参数名称列表'
        for arg in prompt_def.arguments or []:
            '遍历提示词定义中的所有参数（无参数时为空列表）'
            prop: dict[str, Any] = {"type": "string"}
            '初始化参数属性，默认类型为字符串'
            if getattr(arg, "description", None):
                '如果参数存在描述信息，则添加到属性中'
                prop["description"] = arg.description

            properties[arg.name] = prop
            '将参数名称和对应属性存入集合'
            if arg.required:
                '如果参数为必填项，将其名称加入必填列表'
            required.append(arg.name)


        self._parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "required": required,
        }
        '构建完成的标准化提示词参数结构'

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, **kwargs: Any) -> str:
        """调用 session.get_prompt(prompt_name, arguments=kwargs)，处理 McpError 和其他异常，返回拼装后的提示内容"""
        from mcp import types
        from mcp.shared.exceptions import McpError

        for attempt in range(2):
            try:
                result = await asyncio.wait_for(
                    self._session.get_prompt(self._prompt_name, arguments=kwargs),
                    timeout=self._prompt_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "MCP prompt '{}' timed out after {}s", self._name, self._prompt_timeout
                )
                return f"(MCP prompt call timed out after {self._prompt_timeout}s)"
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling() > 0:
                    raise
                logger.warning("MCP prompt '{}' was cancelled by server/SDK", self._name)
                return "(MCP prompt call was cancelled)"
            except McpError as exc:
                logger.error(
                    "MCP prompt '{}' failed: code={} message={}",
                    self._name,
                    exc.error.code,
                    exc.error.message,
                )
                return f"(MCP prompt call failed: {exc.error.message} [code {exc.error.code}])"
            except Exception as exc:
                if _is_transient(exc):
                    if attempt == 0:
                        logger.warning(
                            "MCP prompt '{}' hit transient error ({}), retrying once...",
                            self._name,
                            type(exc).__name__,
                        )
                        await asyncio.sleep(1)
                        continue
                    logger.error(
                        "MCP prompt '{}' failed after retry: {}: {}",
                        self._name,
                        type(exc).__name__,
                        exc,
                    )
                    return f"(MCP prompt call failed after retry: {type(exc).__name__})"
                logger.exception(
                    "MCP prompt '{}' failed: {}: {}",
                    self._name,
                    type(exc).__name__,
                    exc,
                )
                return f"(MCP prompt call failed: {type(exc).__name__})"
            else:
                parts: list[str] = []
                for message in result.messages:
                    content = message.content
                    if isinstance(content, types.TextContent):
                        parts.append(content.text)
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, types.TextContent):
                                parts.append(block.text)
                            else:
                                parts.append(str(block))
                    else:
                        parts.append(str(content))
                return "\n".join(parts) or "(no output)"

        return "(MCP prompt call failed)"  # Unreachable


async def connect_mcp_servers(
    mcp_servers: dict, registry: ToolRegistry
) -> dict[str, AsyncExitStack]:
    """连接到已配置的 MCP 服务器，并注册其工具、资源和提示。

    返回一个字典，其中服务器名称映射到其专用的 AsyncExitStack。
    每个服务器都有自己的栈，并在自己的任务中运行，以防止
    在配置了多个 MCP 服务器时发生取消作用域冲突。
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.sse import sse_client
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client

    async def connect_single_server(name: str, cfg) -> tuple[str, AsyncExitStack | None]:
        '''1.创建 AsyncExitStack 作为该服务器的资源栈，自动管理 MCP 客户端会话、连接等上下文的退出'''
        server_stack = AsyncExitStack()
        await server_stack.__aenter__()

        '''
        2.确定传输类型：
        - 若 cfg.type 已指定，直接使用。
        - 否则若提供 command，默认为 stdio。
        - 若提供 url，根据 URL 是否以 /sse 结尾判断为 sse 或 streamableHttp。
        '''
        try:
            transport_type = cfg.type
            if not transport_type:
                if cfg.command:
                    transport_type = "stdio"
                elif cfg.url:
                    transport_type = (
                        "sse" if cfg.url.rstrip("/").endswith("/sse") else "streamableHttp"
                    )
                else:
                    logger.warning("MCP server '{}': no command or url configured, skipping", name)
                    await server_stack.aclose()
                    return name, None
            '''
            3.根据传输类型建立连接：
            stdio：使用 stdio_client，传入命令、参数、环境变量。
            sse：使用自定义 httpx_client_factory（合并配置中的 headers），调用 sse_client。
            streamableHttp：创建 httpx.AsyncClient，调用 streamable_http_client。
            '''
            if transport_type == "stdio":
                params = StdioServerParameters(
                    command=cfg.command, args=cfg.args, env=cfg.env or None
                )
                read, write = await server_stack.enter_async_context(stdio_client(params))
            elif transport_type == "sse":

                def httpx_client_factory(
                    headers: dict[str, str] | None = None,
                    timeout: httpx.Timeout | None = None,
                    auth: httpx.Auth | None = None,
                ) -> httpx.AsyncClient:
                    merged_headers = {
                        "Accept": "application/json, text/event-stream",
                        **(cfg.headers or {}),
                        **(headers or {}),
                    }
                    return httpx.AsyncClient(
                        headers=merged_headers or None,
                        follow_redirects=True,
                        timeout=timeout,
                        auth=auth,
                    )

                read, write = await server_stack.enter_async_context(
                    sse_client(cfg.url, httpx_client_factory=httpx_client_factory)
                )
            elif transport_type == "streamableHttp":
                http_client = await server_stack.enter_async_context(
                    httpx.AsyncClient(
                        headers=cfg.headers or None,
                        follow_redirects=True,
                        timeout=None,
                    )
                )
                read, write, _ = await server_stack.enter_async_context(
                    streamable_http_client(cfg.url, http_client=http_client)
                )
            else:
                logger.warning("MCP server '{}': unknown transport type '{}'", name, transport_type)
                await server_stack.aclose()
                return name, None

            '''4.创建 ClientSession 并初始化'''
            session = await server_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()

            '''
            4.注册工具：
            调用 session.list_tools() 获取工具列表。
            根据 enabled_tools 配置筛选（支持 "*" 表示全部，也支持原始名或包装名）。
            对每个允许的工具创建 MCPToolWrapper 并调用 registry.register。
            记录未匹配到的 enabledTools 并警告
            '''
            tools = await session.list_tools()
            enabled_tools = set(cfg.enabled_tools)
            allow_all_tools = "*" in enabled_tools
            registered_count = 0
            matched_enabled_tools: set[str] = set()
            available_raw_names = [tool_def.name for tool_def in tools.tools]
            available_wrapped_names = [f"mcp_{name}_{tool_def.name}" for tool_def in tools.tools]
            for tool_def in tools.tools:
                wrapped_name = f"mcp_{name}_{tool_def.name}"
                if (
                    not allow_all_tools
                    and tool_def.name not in enabled_tools
                    and wrapped_name not in enabled_tools
                ):
                    logger.debug(
                        "MCP: skipping tool '{}' from server '{}' (not in enabledTools)",
                        wrapped_name,
                        name,
                    )
                    continue
                wrapper = MCPToolWrapper(session, name, tool_def, tool_timeout=cfg.tool_timeout)
                registry.register(wrapper)
                logger.debug("MCP: registered tool '{}' from server '{}'", wrapper.name, name)
                registered_count += 1
                if enabled_tools:
                    if tool_def.name in enabled_tools:
                        matched_enabled_tools.add(tool_def.name)
                    if wrapped_name in enabled_tools:
                        matched_enabled_tools.add(wrapped_name)

            if enabled_tools and not allow_all_tools:
                unmatched_enabled_tools = sorted(enabled_tools - matched_enabled_tools)
                if unmatched_enabled_tools:
                    logger.warning(
                        "MCP server '{}': enabledTools entries not found: {}. Available raw names: {}. "
                        "Available wrapped names: {}",
                        name,
                        ", ".join(unmatched_enabled_tools),
                        ", ".join(available_raw_names) or "(none)",
                        ", ".join(available_wrapped_names) or "(none)",
                    )
            '''
            6.注册资源（可选）：
            尝试 session.list_resources()，为每个资源创建 MCPResourceWrapper 并注册，失败仅 debug 日志
            '''
            try:
                resources_result = await session.list_resources()
                for resource in resources_result.resources:
                    wrapper = MCPResourceWrapper(
                        session, name, resource, resource_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
                    registered_count += 1
                    logger.debug(
                        "MCP: registered resource '{}' from server '{}'", wrapper.name, name
                    )
            except Exception as e:
                logger.debug("MCP server '{}': resources not supported or failed: {}", name, e)
            '''
            7.注册提示（可选）：
            尝试 session.list_prompts()，为每个提示创建 MCPPromptWrapper 并注册，失败仅 debug 日志
            '''
            try:
                prompts_result = await session.list_prompts()
                for prompt in prompts_result.prompts:
                    wrapper = MCPPromptWrapper(
                        session, name, prompt, prompt_timeout=cfg.tool_timeout
                    )
                    registry.register(wrapper)
                    registered_count += 1
                    logger.debug("MCP: registered prompt '{}' from server '{}'", wrapper.name, name)
            except Exception as e:
                logger.debug("MCP server '{}': prompts not supported or failed: {}", name, e)

            '''8.返回 (name, server_stack) 或若失败则返回 (name, None)'''
            logger.info(
                "MCP server '{}': connected, {} capabilities registered", name, registered_count
            )
            return name, server_stack

        except Exception as e:
            hint = ""
            text = str(e).lower()
            if any(
                marker in text
                for marker in (
                    "parse error",
                    "invalid json",
                    "unexpected token",
                    "jsonrpc",
                    "content-length",
                )
            ):
                hint = (
                    " Hint: this looks like stdio protocol pollution. Make sure the MCP server writes "
                    "only JSON-RPC to stdout and sends logs/debug output to stderr instead."
                )
            logger.error("MCP server '{}': failed to connect: {}{}", name, e, hint)
            try:
                await server_stack.aclose()
            except Exception:
                pass
            return name, None

    server_stacks: dict[str, AsyncExitStack] = {}
    '存储成功连接的服务器名称与其对应的 AsyncExitStack 对象'

    '''
    遍历 mcp_servers 配置字典（键为服务器名称，值为配置对象）。
    对每个服务器，调用 asyncio.create_task 创建一个异步任务，执行 connect_single_server(name, cfg)。
    所有任务对象被添加到 tasks 列表中
    '''
    tasks: list[asyncio.Task] = []
    for name, cfg in mcp_servers.items():
        task = asyncio.create_task(connect_single_server(name, cfg))
        tasks.append(task)

    '''等待所有任务完成'''
    results = await asyncio.gather(*tasks, return_exceptions=True)

    '''处理每个任务的结果'''
    for i, result in enumerate(results):
        name = list(mcp_servers.keys())[i]
        if isinstance(result, BaseException):
            if not isinstance(result, asyncio.CancelledError):
                logger.error("MCP server '{}' connection task failed: {}", name, result)
        elif result is not None and result[1] is not None:
            server_stacks[result[0]] = result[1]

    return server_stacks
