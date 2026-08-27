"""跑完 8 阶段论文分析。用法: python 01_analyze_paper.py /path/to/paper.pdf"""
import asyncio, sys
from pathlib import Path
from silver_research_bot.config.loader import load_config, resolve_config_env_vars
from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider
from silver_research_bot.paper_analyzer.orchestrator import PaperOrchestrator

async def main(pdf_path: str):
    config = resolve_config_env_vars(load_config())
    model = config.agents.defaults.model
    api_key = config.get_api_key(model)
    api_base = config.get_api_base(model)
    if not api_key and not api_base:
        sys.exit("未配置 API Key：请先 `cp config.example.json ~/.silver_research_bot/config.json` "
                 "并在 providers.<name> 下填写 apiKey（或用 ${ENV_VAR} 引用环境变量）。")
    provider = OpenAICompatProvider(
        api_key=api_key,
        api_base=api_base,
        default_model=model,
    )
    workspace = Path("~/.silver_research_bot/workspace").expanduser()
    orch = PaperOrchestrator(provider=provider, model=model, workspace=workspace)
    result = await orch.analyze_paper(pdf_path)
    print(result)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("用法: python 01_analyze_paper.py /path/to/paper.pdf")
    asyncio.run(main(sys.argv[1]))
