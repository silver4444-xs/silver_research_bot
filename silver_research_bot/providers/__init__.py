"""提供统一的 LLM 提供者接口，同时通过“延迟导入”机制优化模块加载性能和依赖管理"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

from silver_research_bot.providers.base import LLMProvider, LLMResponse

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "AnthropicProvider",
    "OpenAICompatProvider",
    "OpenAICodexProvider",
    "GitHubCopilotProvider",
    "AzureOpenAIProvider",
]

_LAZY_IMPORTS = {
    "AnthropicProvider": ".anthropic_provider",
    "OpenAICompatProvider": ".openai_compat_provider",
    "OpenAICodexProvider": ".openai_codex_provider",
    "GitHubCopilotProvider": ".github_copilot_provider",
    "AzureOpenAIProvider": ".azure_openai_provider",
}

if TYPE_CHECKING:
    from silver_research_bot.providers.anthropic_provider import AnthropicProvider
    from silver_research_bot.providers.azure_openai_provider import AzureOpenAIProvider
    from silver_research_bot.providers.github_copilot_provider import GitHubCopilotProvider
    from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider
    from silver_research_bot.providers.openai_codex_provider import OpenAICodexProvider

def __getattr__(name: str):
    """
    动态属性访问函数 __getattr__
    触发时机：当访问当前模块中不存在的属性时（例如 from silver_research_bot.providers import AnthropicProvider，而该模块并未直接定义 AnthropicProvider），Python 会调用这个模块级 __getattr__ 函数。

    逻辑：
    检查 name 是否在 _LAZY_IMPORTS 映射中。
    如果不在，抛出正常的 AttributeError。
    如果在，使用 import_module(module_name, __name__) 动态导入对应的子模块。
    例如：name="AnthropicProvider" → 导入 .anthropic_provider。
    从导入的模块中通过 getattr(module, name) 取出真正的类（例如 AnthropicProvider）返回该类。
    效果：用户第一次访问某个具体提供者类时，对应的子模块才会被真正导入。之后该属性会被正常缓存到模块的 __dict__ 中（Python 的导入机制会缓存模块，但属性查找仍会走 __getattr__？实际上动态导入的类不会自动赋值给模块属性，但每次访问都会重新动态导入并返回，不过 Python 会缓存已导入的模块对象，所以第二次及以后 import_module 只是从 sys.modules 中取，开销很小。也可以优化为将获取到的类赋值给模块属性，但当前写法足够简洁。）

    """

    """Lazily expose provider implementations without importing all backends up front."""
    module_name = _LAZY_IMPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    return getattr(module, name)
