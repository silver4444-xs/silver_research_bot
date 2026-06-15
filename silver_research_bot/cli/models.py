"""用于内置向导的模型信息辅助工具。

在替换 litellm 期间，模型数据库/自动完成功能已暂时禁用。
所有公共函数的签名均得以保留，因此调用方
无需修改即可继续正常工作。
"""

from __future__ import annotations

from typing import Any


def get_all_models() -> list[str]:
    return []


def find_model_info(model_name: str) -> dict[str, Any] | None:
    return None


def get_model_context_limit(model: str, provider: str = "auto") -> int | None:
    return None


def get_model_suggestions(partial: str, provider: str = "auto", limit: int = 20) -> list[str]:
    return []


def format_token_count(tokens: int) -> str:
    """将令牌数量格式化以便显示（例如，200000 -> ‘200,000’）。"""
    return f"{tokens:,}"
