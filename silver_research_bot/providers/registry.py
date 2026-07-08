"""
整个 LLM Provider管理系统的单一数据源，集中定义了所有支持的 LLM 服务商的元数据，供配置匹配、环境变量设置、提供者实例化等环节使用
添加新提供商：
  1. 在下方的 PROVIDERS 中添加一个 ProviderSpec。
  2. 在 config/schema.py 中的 ProvidersConfig 中添加一个字段。
  完成。环境变量、配置匹配和状态显示均由此衍生。

顺序很重要 —— 它决定了匹配优先级和备用方案。网关优先。
每个条目都会写出所有字段，因此您可以将其复制粘贴作为模板。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic.alias_generators import to_snake


@dataclass(frozen=True)
class ProviderSpec:
    """
    某 LLM 提供商的元数据。具体示例请参见下文的“提供商”部分。

    env_extras 值中的占位符：
      {api_key}  — 用户的 API 密钥
      {api_base} — 来自配置的 api_base，或本规范中的 default_api_base
    """

    # identity
    name: str  # config field name, e.g. "dashscope"
    '配置字段名（如 "dashscope"），用作内部标识'
    keywords: tuple[str, ...]  # model-name keywords for matching (lowercase)
    '用于模型名称匹配的关键词（小写），例如 ("qwen", "dashscope")'
    env_key: str  # env var for API key, e.g. "DASHSCOPE_API_KEY"
    '存储 API 密钥的环境变量名（如 "OPENAI_API_KEY"）。若为空字符串，表示不需要或通过其他方式认证（如 OAuth）'
    display_name: str = ""  # shown in `silver_research_bot status`
    '展示给用户的名称，如 "DashScope"。未提供时默认用 name.title()'

    # which provider implementation to use
    # "openai_compat" | "anthropic" | "azure_openai" | "openai_codex" | "github_copilot"
    backend: str = "openai_compat"
    '使用哪个具体的提供者实现类。可选值："openai_compat"、"anthropic"、"azure_openai"、"openai_codex"、"github_copilot"'

    # extra env vars, e.g. (("ZHIPUAI_API_KEY", "{api_key}"),)
    env_extras: tuple[tuple[str, str], ...] = ()
    '额外的环境变量设置，每个元素为 (环境变量名, 值模板)。模板中可使用 {api_key} 和 {api_base} 占位符，在运行时替换'

    # gateway / local detection
    is_gateway: bool = False  # routes any model (OpenRouter, AiHubMix)
    '是否为网关服务（如 OpenRouter、AiHubMix）。网关可以路由任意模型，因此在 fallback 中优先级较高'
    is_local: bool = False  # local deployment (vLLM, Ollama)
    '是否为本地部署（如 vLLM、Ollama）。用于错误提示等场景'
    detect_by_key_prefix: str = ""  # match api_key prefix, e.g. "sk-or-"
    '通过 API 密钥前缀识别提供者（如 OpenRouter 的 "sk-or-"）'
    detect_by_base_keyword: str = ""  # match substring in api_base URL
    '通过 api_base URL 中的子字符串识别提供者（如 "openrouter"）'
    default_api_base: str = ""  # OpenAI-compatible base URL for this provider
    '该提供者的默认 API 基础 URL（用户未提供时使用）'

    # gateway behavior
    strip_model_prefix: bool = False  # strip "provider/" before sending to gateway
    '是否在发送请求前剥离模型名称中的 / 前缀部分（例如将 "anthropic/claude-3" 转为 "claude-3"）。某些网关需要'
    supports_max_completion_tokens: bool = False
    '是否支持较新的 max_completion_tokens 参数（而非 max_tokens）'

    # per-model param overrides, e.g. (("kimi-k2.5", {"temperature": 1.0}),)
    model_overrides: tuple[tuple[str, dict[str, Any]], ...] = ()
    '针对特定模型（子串匹配）的参数覆盖。例如某些 Kimi 模型强制 temperature=1.0'

    # OAuth-based providers (e.g., OpenAI Codex) don't use API keys
    is_oauth: bool = False
    '是否使用 OAuth 认证（如 GitHub Copilot、OpenAI Codex），不使用 API 密钥'

    # Direct providers skip API-key validation (user supplies everything)
    is_direct: bool = False
    '是否为“直接”提供者，即用户完全自己提供 endpoint 和密钥，系统不做额外匹配'

    # Provider supports cache_control on content blocks (e.g. Anthropic prompt caching)
    supports_prompt_caching: bool = False
    '是否支持 prompt caching（如 Anthropic 的缓存控制）'

    @property
    def label(self) -> str:
        '''返回展示名称，若 display_name 为空则返回 name.title()'''
        return self.display_name or self.name.title()


# ---------------------------------------------------------------------------
# PROVIDERS — the registry. Order = priority. Copy any entry as template.
# PROVIDERS 注册表
# 注册表是一个 tuple[ProviderSpec, ...]，顺序非常重要，它决定了匹配优先级。总体分组如下：
# ---------------------------------------------------------------------------

PROVIDERS: tuple[ProviderSpec, ...] = (
    # ===     1. 特殊 / 直接提供者 Custom (direct OpenAI-compatible endpoint) ========================
    # custom：用户自己指定的任意 OpenAI 兼容端点，不依赖任何关键词匹配。
    ProviderSpec(
        name="custom",
        keywords=(),
        env_key="",
        display_name="Custom",
        backend="openai_compat",
        is_direct=True,
    ),

    # === 2. Azure OpenAI (direct API calls with API version 2024-10-21) =====
    # 单独条目，使用 azure_openai 后端。
    ProviderSpec(
        name="azure_openai",
        keywords=("azure", "azure-openai"),
        env_key="",
        display_name="Azure OpenAI",
        backend="azure_openai",
        is_direct=True,
    ),

    # === 3. 网关 Gateways (detected by api_key / api_base, not model name) =========
    #     网关提供者可路由任意模型，优先级高。例如：
    #     openrouter：通过 sk-or- 前缀或 openrouter 关键词识别，支持 prompt caching，需要剥离模型前缀？不，它没有 strip_model_prefix=True，而是保持原始名称。
    #     aihubmix：需要 strip_model_prefix=True，因为它不理解 anthropic/ 前缀。
    #     siliconflow、volcengine、byteplus 等。
    ProviderSpec(
        name="openrouter",
        keywords=("openrouter",),
        env_key="OPENROUTER_API_KEY",
        display_name="OpenRouter",
        backend="openai_compat",
        is_gateway=True,
        detect_by_key_prefix="sk-or-",
        detect_by_base_keyword="openrouter",
        default_api_base="https://openrouter.ai/api/v1",
        supports_prompt_caching=True,
    ),

    # AiHubMix: global gateway, OpenAI-compatible interface.
    # strip_model_prefix=True: doesn't understand "anthropic/claude-3",
    # strips to bare "claude-3".
    ProviderSpec(
        name="aihubmix",
        keywords=("aihubmix",),
        env_key="OPENAI_API_KEY",
        display_name="AiHubMix",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="aihubmix",
        default_api_base="https://aihubmix.com/v1",
        strip_model_prefix=True,
    ),
    # SiliconFlow (硅基流动): OpenAI-compatible gateway, model names keep org prefix
    ProviderSpec(
        name="siliconflow",
        keywords=("siliconflow",),
        env_key="OPENAI_API_KEY",
        display_name="SiliconFlow",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="siliconflow",
        default_api_base="https://api.siliconflow.cn/v1",
    ),

    # VolcEngine (火山引擎): OpenAI-compatible gateway, pay-per-use models
    ProviderSpec(
        name="volcengine",
        keywords=("volcengine", "volces", "ark"),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="volces",
        default_api_base="https://ark.cn-beijing.volces.com/api/v3",
    ),

    # VolcEngine Coding Plan (火山引擎 Coding Plan): same key as volcengine
    ProviderSpec(
        name="volcengine_coding_plan",
        keywords=("volcengine-plan",),
        env_key="OPENAI_API_KEY",
        display_name="VolcEngine Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.cn-beijing.volces.com/api/coding/v3",
        strip_model_prefix=True,
    ),

    # BytePlus: VolcEngine international, pay-per-use models
    ProviderSpec(
        name="byteplus",
        keywords=("byteplus",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus",
        backend="openai_compat",
        is_gateway=True,
        detect_by_base_keyword="bytepluses",
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/v3",
        strip_model_prefix=True,
    ),

    # BytePlus Coding Plan: same key as byteplus
    ProviderSpec(
        name="byteplus_coding_plan",
        keywords=("byteplus-plan",),
        env_key="OPENAI_API_KEY",
        display_name="BytePlus Coding Plan",
        backend="openai_compat",
        is_gateway=True,
        default_api_base="https://ark.ap-southeast.bytepluses.com/api/coding/v3",
        strip_model_prefix=True,
    ),

    # === 4. 标准提供者 Standard providers (matched by model-name keywords) ===============
    # 通过模型名称中的关键词匹配（keywords）。例如：
    # anthropic（关键词 anthropic, claude）→ 使用 anthropic 后端。
    # openai（关键词 openai, gpt）→ 使用 openai_compat 后端。
    # deepseek、gemini、zhipu、dashscope、moonshot、minimax、mistral、stepfun、xiaomi_mimo 等。
    ProviderSpec(
        name="anthropic",
        keywords=("anthropic", "claude"),
        env_key="ANTHROPIC_API_KEY",
        display_name="Anthropic",
        backend="anthropic",
        supports_prompt_caching=True,
    ),
    # OpenAI: SDK default base URL (no override needed)
    ProviderSpec(
        name="openai",
        keywords=("openai", "gpt"),
        env_key="OPENAI_API_KEY",
        display_name="OpenAI",
        backend="openai_compat",
        supports_max_completion_tokens=True,
    ),
    # OpenAI Codex: OAuth-based, dedicated provider
    ProviderSpec(
        name="openai_codex",
        keywords=("openai-codex",),
        env_key="",
        display_name="OpenAI Codex",
        backend="openai_codex",
        detect_by_base_keyword="codex",
        default_api_base="https://chatgpt.com/backend-api",
        is_oauth=True,
    ),
    # GitHub Copilot: OAuth-based
    ProviderSpec(
        name="github_copilot",
        keywords=("github_copilot", "copilot"),
        env_key="",
        display_name="Github Copilot",
        backend="github_copilot",
        default_api_base="https://api.githubcopilot.com",
        strip_model_prefix=True,
        is_oauth=True,
    ),
    # DeepSeek: OpenAI-compatible at api.deepseek.com
    ProviderSpec(
        name="deepseek",
        keywords=("deepseek",),
        env_key="DEEPSEEK_API_KEY",
        display_name="DeepSeek",
        backend="openai_compat",
        default_api_base="https://api.deepseek.com",
    ),
    # Gemini: Google's OpenAI-compatible endpoint
    ProviderSpec(
        name="gemini",
        keywords=("gemini",),
        env_key="GEMINI_API_KEY",
        display_name="Gemini",
        backend="openai_compat",
        default_api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
    ),
    # Zhipu (智谱): OpenAI-compatible at open.bigmodel.cn
    ProviderSpec(
        name="zhipu",
        keywords=("zhipu", "glm", "zai"),
        env_key="ZAI_API_KEY",
        display_name="Zhipu AI",
        backend="openai_compat",
        env_extras=(("ZHIPUAI_API_KEY", "{api_key}"),),
        default_api_base="https://open.bigmodel.cn/api/paas/v4",
    ),
    # DashScope (通义): Qwen models, OpenAI-compatible endpoint
    ProviderSpec(
        name="dashscope",
        keywords=("qwen", "dashscope"),
        env_key="DASHSCOPE_API_KEY",
        display_name="DashScope",
        backend="openai_compat",
        default_api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
    ),
    # Moonshot (月之暗面): Kimi K2.5 / K2.6 enforce temperature >= 1.0.
    ProviderSpec(
        name="moonshot",
        keywords=("moonshot", "kimi"),
        env_key="MOONSHOT_API_KEY",
        display_name="Moonshot",
        backend="openai_compat",
        default_api_base="https://api.moonshot.ai/v1",
        model_overrides=(
            ("kimi-k2.5", {"temperature": 1.0}),
            ("kimi-k2.6", {"temperature": 1.0}),
        ),
    ),
    # MiniMax: OpenAI-compatible API
    ProviderSpec(
        name="minimax",
        keywords=("minimax",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax",
        backend="openai_compat",
        default_api_base="https://api.minimax.io/v1",
    ),
    # MiniMax Anthropic-compatible endpoint: supports thinking mode
    ProviderSpec(
        name="minimax_anthropic",
        keywords=("minimax_anthropic",),
        env_key="MINIMAX_API_KEY",
        display_name="MiniMax (Anthropic)",
        backend="anthropic",
        default_api_base="https://api.minimax.io/anthropic",
    ),
    # Mistral AI: OpenAI-compatible API
    ProviderSpec(
        name="mistral",
        keywords=("mistral",),
        env_key="MISTRAL_API_KEY",
        display_name="Mistral",
        backend="openai_compat",
        default_api_base="https://api.mistral.ai/v1",
    ),
    # Step Fun (阶跃星辰): OpenAI-compatible API
    ProviderSpec(
        name="stepfun",
        keywords=("stepfun", "step"),
        env_key="STEPFUN_API_KEY",
        display_name="Step Fun",
        backend="openai_compat",
        default_api_base="https://api.stepfun.com/v1",
    ),
    # Xiaomi MIMO (小米): OpenAI-compatible API
    ProviderSpec(
        name="xiaomi_mimo",
        keywords=("xiaomi_mimo", "mimo"),
        env_key="XIAOMIMIMO_API_KEY",
        display_name="Xiaomi MIMO",
        backend="openai_compat",
        default_api_base="https://api.xiaomimimo.com/v1",
    ),

    # === 5. 本地部署Local deployment (matched by config key, NOT by api_base) =========
    # vllm、ollama、lm_studio、ovms（OpenVINO Model Server），标记 is_local=True，用于友好错误提示。
    ProviderSpec(
        name="vllm",
        keywords=("vllm",),
        env_key="HOSTED_VLLM_API_KEY",
        display_name="vLLM/Local",
        backend="openai_compat",
        is_local=True,
    ),
    # Ollama (local, OpenAI-compatible)
    ProviderSpec(
        name="ollama",
        keywords=("ollama", "nemotron"),
        env_key="OLLAMA_API_KEY",
        display_name="Ollama",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="11434",
        default_api_base="http://localhost:11434/v1",
    ),
    # LM Studio (local, OpenAI-compatible)
    ProviderSpec(
        name="lm_studio",
        keywords=("lm-studio", "lmstudio", "lm_studio"),
        env_key="LM_STUDIO_API_KEY",
        display_name="LM Studio",
        backend="openai_compat",
        is_local=True,
        detect_by_base_keyword="1234",
        default_api_base="http://localhost:1234/v1",
    ),
    # === OpenVINO Model Server (direct, local, OpenAI-compatible at /v3) ===
    ProviderSpec(
        name="ovms",
        keywords=("openvino", "ovms"),
        env_key="",
        display_name="OpenVINO Model Server",
        backend="openai_compat",
        is_direct=True,
        is_local=True,
        default_api_base="http://localhost:8765/v3",
    ),

    # === 6. 辅助提供者 ============================
    # Groq: （主要用于 Whisper 语音转文字，也支持 LLM）、qianfan（百度千帆）。
    ProviderSpec(
        name="groq",
        keywords=("groq",),
        env_key="GROQ_API_KEY",
        display_name="Groq",
        backend="openai_compat",
        default_api_base="https://api.groq.com/openai/v1",
    ),
    # Qianfan (百度千帆): OpenAI-compatible API
    ProviderSpec(
        name="qianfan",
        keywords=("qianfan", "ernie"),
        env_key="QIANFAN_API_KEY",
        display_name="Qianfan",
        backend="openai_compat",
        default_api_base="https://qianfan.baidubce.com/v2"
    ),
)


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


def find_by_name(name: str) -> ProviderSpec | None:
    """
    根据配置字段名（如 "dashscope"）查找对应的 ProviderSpec。
    先标准化名称（将短横线转为下划线，再转为 snake_case），然后遍历 PROVIDERS 匹配 spec.name
    """
    normalized = to_snake(name.replace("-", "_"))
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None
