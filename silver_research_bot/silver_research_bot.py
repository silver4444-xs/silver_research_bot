"""silver_research_bot 的高级程序化接口."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from silver_research_bot.agent.hook import AgentHook
from silver_research_bot.agent.loop import AgentLoop
from silver_research_bot.bus.queue import MessageBus


@dataclass(slots=True)
class RunResult:
    """单次调用 Agent 的返回结果"""
    content: str
    '执行结果/响应内容文本'
    tools_used: list[str]
    '执行过程中使用的工具名称列表'
    messages: list[dict[str, Any]]
    '完整的对话消息历史记录'


class silver_research_bot:
    """用于运行 silver_research_bot 代理的程序化接口。

    用法::

        bot = silver_research_bot.from_config()
        result = await bot.run(“Summarize this repo”, hooks=[MyHook()])
        print(result.content)
    """

    def __init__(self, loop: AgentLoop) -> None:
        self._loop = loop

    @classmethod
    def from_config(
        cls,
        config_path: str | Path | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> silver_research_bot:
        """从配置文件创建一个 silver_research_bot 实例。

        参数：
            config_path：``config.json`` 的路径。默认值为
                ``~/.silver_research_bot/config.json``。
            workspace：覆盖配置文件中的工作区目录。
        """
        from silver_research_bot.config.loader import load_config, resolve_config_env_vars
        from silver_research_bot.config.schema import Config

        resolved: Path | None = None
        if config_path is not None:
            resolved = Path(config_path).expanduser().resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Config not found: {resolved}")

        config: Config = resolve_config_env_vars(load_config(resolved))
        if workspace is not None:
            config.agents.defaults.workspace = str(
                Path(workspace).expanduser().resolve()
            )

        provider = _make_provider(config)
        bus = MessageBus()
        defaults = config.agents.defaults

        loop = AgentLoop(
            bus=bus,
            provider=provider,
            workspace=config.workspace_path,
            model=defaults.model,
            max_iterations=defaults.max_tool_iterations,
            context_window_tokens=defaults.context_window_tokens,
            context_block_limit=defaults.context_block_limit,
            max_tool_result_chars=defaults.max_tool_result_chars,
            provider_retry_mode=defaults.provider_retry_mode,
            web_config=config.tools.web,
            exec_config=config.tools.exec,
            restrict_to_workspace=config.tools.restrict_to_workspace,
            mcp_servers=config.tools.mcp_servers,
            timezone=defaults.timezone,
            unified_session=defaults.unified_session,
            disabled_skills=defaults.disabled_skills,
            session_ttl_minutes=defaults.session_ttl_minutes,
            tools_config=config.tools,
        )
        return cls(loop)

    async def run(
        self,
        message: str,
        *,
        session_key: str = "sdk:default",
        hooks: list[AgentHook] | None = None,
    ) -> RunResult:
        """运行代理一次并返回结果。

        参数：
            message：待处理的用户消息。
            session_key：用于会话隔离的会话标识符。
                不同的键将生成独立的历史记录。
            hooks：本次运行的可选生命周期钩子。
        """
        prev = self._loop._extra_hooks
        if hooks is not None:
            self._loop._extra_hooks = list(hooks)
        try:
            response = await self._loop.process_direct(
                message, session_key=session_key,
            )
        finally:
            self._loop._extra_hooks = prev

        content = (response.content if response else None) or ""
        return RunResult(content=content, tools_used=[], messages=[])


def _make_provider(config: Any) -> Any:
    """根据配置（从 CLI 提取）创建 LLM 提供程序。"""
    from silver_research_bot.providers.base import GenerationSettings
    from silver_research_bot.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    if backend == "azure_openai":
        if not p or not p.api_key or not p.api_base:
            raise ValueError("Azure OpenAI requires api_key and api_base in config.")
    elif backend == "openai_compat" and not model.startswith("bedrock/"):
        needs_key = not (p and p.api_key)
        exempt = spec and (spec.is_oauth or spec.is_local or spec.is_direct)
        if needs_key and not exempt:
            raise ValueError(f"No API key configured for provider '{provider_name}'.")

    if backend == "openai_codex":
        from silver_research_bot.providers.openai_codex_provider import OpenAICodexProvider

        provider = OpenAICodexProvider(default_model=model)
    elif backend == "github_copilot":
        from silver_research_bot.providers.github_copilot_provider import GitHubCopilotProvider

        provider = GitHubCopilotProvider(default_model=model)
    elif backend == "azure_openai":
        from silver_research_bot.providers.azure_openai_provider import AzureOpenAIProvider

        provider = AzureOpenAIProvider(
            api_key=p.api_key, api_base=p.api_base, default_model=model
        )
    elif backend == "anthropic":
        from silver_research_bot.providers.anthropic_provider import AnthropicProvider

        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider

        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider
