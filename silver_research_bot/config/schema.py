"""
    配置 Schema 定义，使用 Pydantic 和 Pydantic Settings 实现类型安全、支持环境变量覆盖的配置管理。
    亮点：
    1.类型安全与自文档：所有字段都有类型注解和默认值，利用 Pydantic 自动验证（如 ge=0、le=10）。
    2.多命名兼容：validation_alias + AliasChoices 优雅处理配置字段重命名或风格变更。
    3.灵活的多提供商支持：通过统一的 ProvidersConfig 容器 + 智能匹配算法，用户无需手动指定每个模型的提供商。
    4.安全默认：API 服务默认仅监听本地环回；SSRF 保护可配置白名单；工具文件访问可限制在工作区。
    5.环境变量友好：支持嵌套分隔符和大写前缀，方便在容器/K8s 环境中注入配置。
    6.扩展性：ChannelsConfig 等类允许 extra="allow"，通道插件可自定义额外字段；mcp_servers 动态支持任意数量的 MCP 服务器。
"""
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel
from pydantic_settings import BaseSettings

from silver_research_bot.cron.types import CronSchedule


class Base(BaseModel):
    """
    所有配置类的基类。
    alias_generator=to_camel：自动生成驼峰式（camelCase）字段别名，例如 send_progress 可被 sendProgress 识别。
    populate_by_name=True：允许使用原字段名或别名填充数据，方便 JSON/API 交互和配置文件兼容。
    """
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

class ChannelsConfig(Base):
    """
        聊天通道配置
        Built-in and plugin的通道配置以额外字段（字典）的形式存储。
        每个通道都在 __init__ 中解析其自身的配置。
        为每个通道设置 “streaming”: true 可启用流式输出（需要实现 send_delta 接口）。
    """

    model_config = ConfigDict(extra="allow")
    'extra="allow"：允许额外字段，因为不同通道（如 Discord、Telegram）可定义自己的私有配置。'

    send_progress: bool = True  # stream agent's text progress to the channel
    '是否将 Agent 的文本进度流式发送到通道。'
    send_tool_hints: bool = False  # stream tool-call hints (e.g. read_file("…"))
    '是否发送工具调用提示（如“正在读取文件…”）'
    send_max_retries: int = Field(default=3, ge=0, le=10)  # Max delivery attempts (initial send included)
    '消息最大投递尝试次数（含首次发送）'
    transcription_provider: str = "groq"  # Voice transcription backend: "groq" or "openai"
    '语音转文字后端，支持 groq 或 openai'

class DreamConfig(Base):
    """
        记忆巩固配置（Dream 机制）
        控制后台任务定期压缩/总结历史对话（“做梦”），将长期记忆存入 MEMORY.md
    """

    _HOUR_MS = 3_600_000

    interval_h: int = Field(default=2, ge=1)  # Every 2 hours by default
    '执行间隔（小时），默认 2 小时'
    cron: str | None = Field(default=None, exclude=True)  # Legacy compatibility override
    '遗留的 cron 表达式覆盖，若设置则优先使用'
    model_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("modelOverride", "model", "model_override"),
    )  # Optional Dream-specific model override
    '指定专用于 Dream 的 LLM 模型（不占用主模型配置）'
    max_batch_size: int = Field(default=20, ge=1)  # Max history entries per run
    '控制每次处理的历史条目数'
    # Bumped from 10 to 15 in #3212 (exp002: +30% dedup, no accuracy loss; >15 plateaus).
    max_iterations: int = Field(default=15, ge=1)  # Max tool calls per Phase 2
    '控制工具调用次数'
    annotate_line_ages: bool = True
    '是否在提示词中为 MEMORY.md 每一行添加 ← Nd 注释（表示该行 N 天前修改），有助于 LLM 理解信息时效性'

    def build_schedule(self, timezone: str) -> CronSchedule:
        """根据 cron 或 interval_h 生成调度对象"""
        if self.cron:
            return CronSchedule(kind="cron", expr=self.cron, tz=timezone)
        return CronSchedule(kind="every", every_ms=self.interval_h * self._HOUR_MS)

    def describe_schedule(self) -> str:
        """返回人类可读的调度描述"""
        if self.cron:
            return f"cron {self.cron} (legacy)"
        hours = self.interval_h
        return f"every {hours}h"


class AgentDefaults(Base):
    """Agent 默认配置"""

    workspace: str = "~/.silver_research_bot/workspace"
    '工作目录（支持 ~ 展开）'
    model: str = "deepseek-chat"
    '使用的 LLM 模型标识'
    provider: str = (
        "auto"  # Provider name (e.g. "anthropic", "openrouter") or "auto" for auto-detection
    )
    '"auto" 自动检测，或指定为 "openrouter"、"deepseek" 等'
    max_tokens: int = 8192
    '模型生成的最大 token 数'
    context_window_tokens: int = 65_536
    '上下文窗口总 token 容量'
    context_block_limit: int | None = None
    '可选，上下文分块限制'
    temperature: float = 0.1
    max_tool_iterations: int = 200
    'Agent 单次任务中最大工具调用轮次'
    max_tool_result_chars: int = 16_000
    '工具返回结果的最大字符数'
    provider_retry_mode: Literal["standard", "persistent"] = "standard"
    '重试模式：standard或persistent'
    reasoning_effort: str | None = None  # low / medium / high / adaptive - enables LLM thinking mode
    '推理强度（low/medium/high/adaptive），启用 LLM 思考模式'
    timezone: str = "UTC"  # IANA timezone, e.g. "Asia/Shanghai", "America/New_York"
    'IANA 时区，如 "Asia/Shanghai"'
    unified_session: bool = False  # Share one session across all channels (single-user multi-device)
    '是否在所有通道间共享同一会话（单用户多设备场景）'
    disabled_skills: list[str] = Field(default_factory=list)  # Skill names to exclude from loading (e.g. ["summarize", "skill-creator"])
    '禁用加载的技能名称列表'
    session_ttl_minutes: int = Field(
        default=0,
        ge=0,
        validation_alias=AliasChoices("idleCompactAfterMinutes", "sessionTtlMinutes"),
        serialization_alias="idleCompactAfterMinutes",
    )  # Auto-compact idle threshold in minutes (0 = disabled)
    '空闲会话自动压缩的阈值（分钟），0 为禁用。支持别名 idleCompactAfterMinutes 和 sessionTtlMinutes，序列化时输出为 idleCompactAfterMinutes'
    dream: DreamConfig = Field(default_factory=DreamConfig)
    '记忆整合（Dream）配置，嵌套结构'

class AgentsConfig(Base):
    """Agent 配置容器"""

    defaults: AgentDefaults = Field(default_factory=AgentDefaults)


class ProviderConfig(Base):
    """LLM provider 配置"""
    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] | None = None  # Custom headers (e.g. APP-Code for AiHubMix)


class ProvidersConfig(Base):
    """
    LLM providers 配置
    为数十种 LLM providers分别提供一个 ProviderConfig 实例
    """

    '''商业 API：openai, anthropic, deepseek, groq, gemini, mistral 等'''
    custom: ProviderConfig = Field(default_factory=ProviderConfig)  # Any OpenAI-compatible endpoint
    azure_openai: ProviderConfig = Field(default_factory=ProviderConfig)  # Azure OpenAI (model = deployment name)
    anthropic: ProviderConfig = Field(default_factory=ProviderConfig)
    openai: ProviderConfig = Field(default_factory=ProviderConfig)
    openrouter: ProviderConfig = Field(default_factory=ProviderConfig)
    deepseek: ProviderConfig = Field(default_factory=ProviderConfig)
    groq: ProviderConfig = Field(default_factory=ProviderConfig)

    '''国内平台：zhipu（智谱）、dashscope（通义）、stepfun（阶跃星辰）、qianfan（百度千帆）、siliconflow（硅基流动）、volcengine（火山引擎）等'''
    zhipu: ProviderConfig = Field(default_factory=ProviderConfig)
    dashscope: ProviderConfig = Field(default_factory=ProviderConfig)
    vllm: ProviderConfig = Field(default_factory=ProviderConfig)
    gemini: ProviderConfig = Field(default_factory=ProviderConfig)
    moonshot: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax: ProviderConfig = Field(default_factory=ProviderConfig)
    minimax_anthropic: ProviderConfig = Field(default_factory=ProviderConfig)  # MiniMax Anthropic endpoint (thinking)
    mistral: ProviderConfig = Field(default_factory=ProviderConfig)
    stepfun: ProviderConfig = Field(default_factory=ProviderConfig)  # Step Fun (阶跃星辰)
    xiaomi_mimo: ProviderConfig = Field(default_factory=ProviderConfig)  # Xiaomi MIMO (小米)
    aihubmix: ProviderConfig = Field(default_factory=ProviderConfig)  # AiHubMix API gateway
    siliconflow: ProviderConfig = Field(default_factory=ProviderConfig)  # SiliconFlow (硅基流动)
    volcengine: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine (火山引擎)
    volcengine_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # VolcEngine Coding Plan
    byteplus: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus (VolcEngine international)
    byteplus_coding_plan: ProviderConfig = Field(default_factory=ProviderConfig)  # BytePlus Coding Plan
    qianfan: ProviderConfig = Field(default_factory=ProviderConfig)  # Qianfan (百度千帆)

    '''本地/自托管：ollama, lm_studio, vllm, ovms'''
    ollama: ProviderConfig = Field(default_factory=ProviderConfig)  # Ollama local models
    lm_studio: ProviderConfig = Field(default_factory=ProviderConfig)  # LM Studio local models
    ovms: ProviderConfig = Field(default_factory=ProviderConfig)  # OpenVINO Model Server (OVMS)

    '''OAuth 特殊提供商（openai_codex, github_copilot）标记 exclude=True，避免在常规配置导出中包含'''
    openai_codex: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # OpenAI Codex (OAuth)
    github_copilot: ProviderConfig = Field(default_factory=ProviderConfig, exclude=True)  # Github Copilot (OAuth)



class HeartbeatConfig(Base):
    """Heartbeat 服务配置"""

    enabled: bool = True
    '心跳服务开关'
    interval_s: int = 30 * 60  # 30 minutes
    '间隔（秒）'
    keep_recent_messages: int = 8
    '保留的最近消息数'


class ApiConfig(Base):
    """OpenAI兼容的 API server 配置."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    '内置 OpenAI 兼容 API 服务器的监听地址'
    port: int = 8900
    '端口'
    timeout: float = 120.0  # Per-request timeout in seconds.
    '超时'

class GatewayConfig(Base):
    """Gateway/server 配置."""

    host: str = "127.0.0.1"  # Safer default: local-only bind.
    port: int = 18790
    heartbeat: HeartbeatConfig = Field(default_factory=HeartbeatConfig)


class WebSearchConfig(Base):
    """网络搜索工具配置"""

    provider: str = "duckduckgo"  # brave, tavily, duckduckgo, searxng, jina, kagi
    '搜索provider'
    api_key: str = ""
    base_url: str = ""  # SearXNG base URL
    max_results: int = 5
    timeout: int = 30  # Wall-clock timeout (seconds) for search operations


class WebToolsConfig(Base):
    """网络工具配置：统一控制 Web 工具是否启用、代理地址以及搜索子配置"""

    enable: bool = True
    proxy: str | None = (
        None  # HTTP/SOCKS5 proxy URL, e.g. "http://127.0.0.1:7890" or "socks5://127.0.0.1:1080"
    )
    search: WebSearchConfig = Field(default_factory=WebSearchConfig)


class ExecToolConfig(Base):
    """Shell执行工具配置"""

    enable: bool = True
    timeout: int = 60
    path_append: str = ""
    '额外添加到 PATH 的目录'
    sandbox: str = ""  # sandbox backend: "" (none) or "bwrap"
    '沙箱后端（bwrap 或空）'
    allowed_env_keys: list[str] = Field(default_factory=list)  # Env var names to pass through to subprocess (e.g. ["GOPATH", "JAVA_HOME"])
    '允许传递给子进程的环境变量白名单'

class MCPServerConfig(Base):
    """MCP（Model Context Protocol）服务器连接配置 (stdio or HTTP)."""

    type: Literal["stdio", "sse", "streamableHttp"] | None = None  # auto-detected if omitted
    '支持 stdio、SSE、streamableHttp 三种类型（可自动检测）'

    'Stdio：通过 command + args 启动子进程，可附加 env'
    command: str = ""  # Stdio: command to run (e.g. "npx")
    args: list[str] = Field(default_factory=list)  # Stdio: command arguments
    env: dict[str, str] = Field(default_factory=dict)  # Stdio: extra env vars

    'HTTP/SSE：配置 url 和 headers'
    url: str = ""  # HTTP/SSE: endpoint URL
    headers: dict[str, str] = Field(default_factory=dict)  # HTTP/SSE: custom headers

    tool_timeout: int = 30  # seconds before a tool call is cancelled
    '工具调用超时'
    enabled_tools: list[str] = Field(default_factory=lambda: ["*"])  # Only register these tools; accepts raw MCP names or wrapped mcp_<server>_<tool> names; ["*"] = all tools; [] = no tools
    '允许注册的工具名列表，["*"] 表示全部，[] 表示不注册任何工具'

class MyToolConfig(Base):
    """自检工具配置"""

    enable: bool = True  # register the `my` tool (agent runtime state inspection)
    '自检工具 my 的开关'
    allow_set: bool = False  # let `my` modify loop state (read-only if False)
    '是否允许修改 Agent 内部状态（allow_set 默认为 False，只读）'

class ToolsConfig(Base):
    """Tools 配置"""
    'web, exec, my 以及 mcp_servers 字典（服务器名 → 配置）'
    web: WebToolsConfig = Field(default_factory=WebToolsConfig)
    exec: ExecToolConfig = Field(default_factory=ExecToolConfig)
    my: MyToolConfig = Field(default_factory=MyToolConfig)
    mcp_servers: dict[str, MCPServerConfig] = Field(default_factory=dict)

    restrict_to_workspace: bool = False  # restrict all tool access to workspace directory
    '强制所有工具的文件访问限制在工作区内'
    ssrf_whitelist: list[str] = Field(default_factory=list)  # CIDR ranges to exempt from SSRF blocking (e.g. ["100.64.0.0/10"] for Tailscale)
    'SSRF 黑名单的白名单 CIDR 范围（例如 Tailscale 网段）'


class RAGConfig(Base):
    """RAG 检索增强生成配置"""

    embedding_model: str = "text-embedding-3-small"
    '嵌入模型名称'
    embedding_dimensions: int = 1536
    '嵌入向量维度'
    bm25_weight: float = Field(default=0.3, ge=0.0, le=1.0)
    'BM25 关键词匹配权重 (0-1)'
    vector_weight: float = Field(default=0.7, ge=0.0, le=1.0)
    '向量相似度权重 (0-1)'
    coarse_top_k: int = Field(default=20, ge=1, le=100)
    '粗排候选数'
    final_top_k: int = Field(default=5, ge=1, le=20)
    '精排最终返回数'
    rerank_enabled: bool = True
    '是否启用 LLM 重排序'


class Config(BaseSettings):
    """
        Root配置
        聚合了所有子配置（Agent、通道、提供商、API、网关、工具），
        并提供了一组用于动态匹配 LLM 提供商的方法，支持从环境变量覆盖配置
    """

    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    gateway: GatewayConfig = Field(default_factory=GatewayConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    rag: RAGConfig = Field(default_factory=RAGConfig)

    @property
    def workspace_path(self) -> Path:
        """获取 Agent 的工作目录路径，自动展开 ~ 为用户 home 目录。方便后续文件操作（如模型缓存、日志、技能存储等）"""
        return Path(self.agents.defaults.workspace).expanduser()

    def _match_provider(
        self, model: str | None = None
    ) -> tuple["ProviderConfig | None", str | None]:
        """
            provider匹配核心
            根据模型名称（或默认模型）和 provider 的设置，从 providers 配置中选择一个合适的 ProviderConfig，
            并返回 (config, provider_name)。
            匹配逻辑按优先级执行（一旦匹配成功即返回）：
        """
        from silver_research_bot.providers.registry import PROVIDERS, find_by_name

        '''
        ① 用户强制指定了 provider（不等于 "auto"）
        直接根据 forced 名称查找注册表（find_by_name）。
        如果找到对应的 ProviderConfig（通过 getattr(self.providers, spec.name)），就返回它。
        '''
        forced = self.agents.defaults.provider
        if forced != "auto":
            spec = find_by_name(forced)
            if spec:
                p = getattr(self.providers, spec.name, None)
                return (p, spec.name) if p else (None, None)
            return None, None

        '''
            ② 提取模型名中的前缀（如 anthropic/claude-3 → anthropic）
            若模型名包含 /，则前缀部分作为候选提供商名。
            遍历所有注册的提供商，如果某个提供商的名字与规范化后的前缀完全一致，并且该提供商满足条件（is_oauth、is_local 或已配置 api_key），则直接匹配。
            这确保 github-copilot/... 不会被错误匹配到 openai_codex。
        '''
        model_lower = (model or self.agents.defaults.model).lower()
        model_normalized = model_lower.replace("-", "_")
        model_prefix = model_lower.split("/", 1)[0] if "/" in model_lower else ""
        normalized_prefix = model_prefix.replace("-", "_")

        def _kw_matches(kw: str) -> bool:
            kw = kw.lower()
            return kw in model_lower or kw.replace("-", "_") in model_normalized

        # Explicit provider prefix wins — prevents `github-copilot/...codex` matching openai_codex.
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and model_prefix and normalized_prefix == spec.name:
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        '''
            ③ 根据提供商注册的关键词匹配
            将模型名转为小写，并规范化（- 转 _）。
            遍历每个提供商，如果模型名中包含提供商预设的 keywords 中的任何一个（例如 "openai"、"gpt" 对应 OpenAI），并且该提供商满足条件，则匹配。
        '''
        # Match by keyword (order follows PROVIDERS registry)
        for spec in PROVIDERS:
            p = getattr(self.providers, spec.name, None)
            if p and any(_kw_matches(kw) for kw in spec.keywords):
                if spec.is_oauth or spec.is_local or p.api_key:
                    return p, spec.name

        '''
            ④ 本地提供商的特殊回退（基于 api_base 特征）
            仅针对标记为 is_local 的提供商（如 Ollama、LM Studio）。
            如果某个本地提供商配置了 api_base，且其 detect_by_base_keyword（如 "11434"）出现在 api_base 中，则优先匹配。
            否则，记录第一个有 api_base 的本地提供商作为 local_fallback。
        '''
        # Fallback: configured local providers can route models without
        # provider-specific keywords (for example plain "llama3.2" on Ollama).
        # Prefer providers whose detect_by_base_keyword matches the configured api_base
        # (e.g. Ollama's "11434" in "http://localhost:11434") over plain registry order.
        local_fallback: tuple[ProviderConfig, str] | None = None
        for spec in PROVIDERS:
            if not spec.is_local:
                continue
            p = getattr(self.providers, spec.name, None)
            if not (p and p.api_base):
                continue
            if spec.detect_by_base_keyword and spec.detect_by_base_keyword in p.api_base:
                return p, spec.name
            if local_fallback is None:
                local_fallback = (p, spec.name)
        if local_fallback:
            return local_fallback

        '''
            ⑤ 兜底匹配
            按注册表顺序返回第一个非 OAuth 且配置了 api_key 的提供商。  
            OAuth 提供商（如 openai_codex、github_copilot）不会被自动回退使用，必须由模型名显式触发。
            返回值：元组 (ProviderConfig | None, str | None) – 前者是配置对象，后者是提供商在注册表中的名称（如 "deepseek"）。
        '''
        # Fallback: gateways first, then others (follows registry order)
        # OAuth providers are NOT valid fallbacks — they require explicit model selection
        for spec in PROVIDERS:
            if spec.is_oauth:
                continue
            p = getattr(self.providers, spec.name, None)
            if p and p.api_key:
                return p, spec.name
        return None, None

    def get_provider(self, model: str | None = None) -> ProviderConfig | None:
        """返回匹配的 ProviderConfig 对象（含 api_key、api_base、extra_headers）"""
        p, _ = self._match_provider(model)
        return p

    def get_provider_name(self, model: str | None = None) -> str | None:
        """返回匹配的provider名称（如 "openrouter"）"""
        _, name = self._match_provider(model)
        return name

    def get_api_key(self, model: str | None = None) -> str | None:
        """直接返回 API key，常用于初始化 LLM 客户端"""
        p = self.get_provider(model)
        return p.api_key if p else None

    def get_api_base(self, model: str | None = None) -> str | None:
        """返回 API 基础 URL，若配置中未显式设置，则尝试使用该提供商的默认 default_api_base（来自注册表）"""
        from silver_research_bot.providers.registry import find_by_name

        p, name = self._match_provider(model)
        if p and p.api_base:
            return p.api_base
        if name:
            spec = find_by_name(name)
            if spec and spec.default_api_base:
                return spec.default_api_base
        return None

    model_config = ConfigDict(env_prefix="silver_research_bot_", env_nested_delimiter="__")
    '配置可以从环境变量自动加载，前缀为 silver_research_bot_，嵌套分隔符为 __'

class ResearchConfig(Base):
    """鑷富绉戠爺鍔╂墜閰嶇疆銆?"""

    workspace_subdir: str = "research"
    template_format: str = "article"
    execution_profile: str = "local_cpu"
    max_retries: int = 2
    default_epochs: int = 18
    default_seeds: list[int] = Field(default_factory=lambda: [7, 13, 29])
    stage_models: dict[str, str] = Field(default_factory=dict)
