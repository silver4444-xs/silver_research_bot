"""用自定义 OpenAI 兼容端点（DeepSeek / Ollama / vLLM）。"""
import asyncio
from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider

async def main():
    # 改为你的端点；Ollama 默认 http://localhost:11434/v1
    provider = OpenAICompatProvider(
        api_key="EMPTY",
        api_base="http://localhost:11434/v1",
        default_model="llama3",
    )
    reply = await provider.chat_with_retry([{"role": "user", "content": "你好"}])
    print(reply.content)

if __name__ == "__main__":
    asyncio.run(main())
