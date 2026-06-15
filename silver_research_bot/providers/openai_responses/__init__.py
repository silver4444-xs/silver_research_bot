"""OpenAI Responses API 提供商（Codex、Azure OpenAI）的通用辅助函数"""

'''1. 从 converters 导入的消息转换函数'''
from silver_research_bot.providers.openai_responses.converters import (
    convert_messages,
    convert_tools,
    convert_user_message,
    split_tool_call_id,
)
'''2. 从 parsing 导入的响应解析函数与常量'''
from silver_research_bot.providers.openai_responses.parsing import (
    FINISH_REASON_MAP,
    consume_sdk_stream,
    consume_sse,
    iter_sse,
    map_finish_reason,
    parse_response_output,
)

__all__ = [
    "convert_messages",
    "convert_tools",
    "convert_user_message",
    "split_tool_call_id",
    "iter_sse",
    "consume_sse",
    "consume_sdk_stream",
    "map_finish_reason",
    "parse_response_output",
    "FINISH_REASON_MAP",
]
