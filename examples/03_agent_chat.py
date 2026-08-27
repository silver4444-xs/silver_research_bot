"""最小 Agent 对话（推荐入口）。用法: python 03_agent_chat.py "帮我总结论文" """
import asyncio, sys
from silver_research_bot.SilverAgent import silver_research_bot

async def main(message: str):
    bot = silver_research_bot.from_config()
    result = await bot.run(message)
    print(result.content)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('用法: python 03_agent_chat.py "你的问题"')
    asyncio.run(main(sys.argv[1]))
