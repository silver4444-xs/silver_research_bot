"""科研助手命令行入口。

保留 CLI 便于本地调试，但核心面向 Web 端工作台。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer

from silver_research_bot.research_app import BatchRequest, ResearchRequest, engine

research_app = typer.Typer(help="自主科研助手命令")
_workspace = Path("~/.silver_research_bot/workspace").expanduser()


@research_app.command("run")
def run(
    topic: str = typer.Option(..., "--topic", "-t", help="研究主题"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只创建工作区，不执行实验"),
):
    """创建一个研究工作区并可选执行实验。"""
    workspace = engine.create_workspace(ResearchRequest(topic=topic, dry_run=dry_run))
    if not dry_run:
        workspace = engine.execute(workspace.run_id, ResearchRequest(topic=topic))
    typer.echo(workspace.model_dump_json(indent=2, ensure_ascii=False))


@research_app.command("batch")
def batch(
    topic: list[str] = typer.Option(..., "--topic", "-t", help="研究主题，可重复传入"),
    dry_run: bool = typer.Option(False, "--dry-run", help="只创建工作区，不执行实验"),
):
    """批量运行多个研究主题。"""
    results = engine.batch(BatchRequest(topics=topic, dry_run=dry_run))
    typer.echo("[")
    for item in results:
        typer.echo(item.model_dump_json(indent=2, ensure_ascii=False))
    typer.echo("]")


# ── Paper Analysis CLI ──────────────────────────────────────────

paper_app = typer.Typer(help="论文研读 Agent 命令")


def _make_orchestrator():
    from silver_research_bot.config.loader import load_config, resolve_config_env_vars
    from silver_research_bot.providers.openai_compat_provider import OpenAICompatProvider
    from silver_research_bot.paper_analyzer.orchestrator import PaperOrchestrator
    from silver_research_bot.paper_analyzer.manager import PaperManager

    config = resolve_config_env_vars(load_config())
    defaults = config.agents.defaults
    provider = OpenAICompatProvider(
        api_key=config.get_api_key(defaults.model),
        api_base=config.get_api_base(defaults.model),
        default_model=defaults.model,
    )
    manager = PaperManager(workspace=_workspace)
    orch = PaperOrchestrator(provider=provider, model=defaults.model, workspace=_workspace)
    orch.set_manager(manager)
    return orch, manager


@paper_app.command("analyze")
def analyze(
    pdf_path: str = typer.Argument(..., help="PDF 论文文件路径"),
    language: str = typer.Option("auto", "--lang", "-l", help="论文语言: auto/en/zh"),
):
    """分析 PDF 论文：翻译→四维分析→公式解读→可视化→审计。"""
    orch, _ = _make_orchestrator()
    result = asyncio.run(orch.analyze_paper(pdf_path, language))
    typer.echo(f"论文 ID: {result['paper_id']}")
    typer.echo(f"标题: {result['title']}")
    typer.echo(f"语言: {result['language']}")
    typer.echo(f"状态: {result['status']}")
    typer.echo(f"审计通过: {result['passed_audit']}")
    typer.echo(f"产出物: {', '.join(result['artifacts'])}")
    typer.echo(f"工作目录: {result['workspace_dir']}")


@paper_app.command("list")
def list_papers():
    """列出所有已分析的论文。"""
    _, manager = _make_orchestrator()
    papers = manager.list_papers()
    if not papers:
        typer.echo("暂无已分析的论文。")
        return
    for p in papers:
        typer.echo(
            f"[{p['paper_id']}] {p['title'][:60]} "
            f"({p['language']}, {p.get('page_count', '?')}页)"
        )


@paper_app.command("show")
def show(paper_id: str = typer.Argument(..., help="论文 ID")):
    """查看论文详情。"""
    _, manager = _make_orchestrator()
    paper = manager.get_paper(paper_id)
    if not paper:
        typer.echo(f"论文 {paper_id} 未找到。")
        raise typer.Exit(code=1)
    for k, v in paper.items():
        if k in ("translation", "system_model", "problem_formulation",
                 "optimization_algorithm", "experiment_design",
                 "formula_explanations", "visualization_html"):
            continue
        typer.echo(f"{k}: {v}")


@paper_app.command("output")
def output(
    paper_id: str = typer.Argument(..., help="论文 ID"),
    artifact_type: str = typer.Argument(..., help="产物类型: translation/system_model/.../audit"),
):
    """查看特定分析产物。"""
    _, manager = _make_orchestrator()
    content = manager.get_artifact(paper_id, artifact_type)
    if content is None:
        typer.echo(f"产物 {artifact_type} 未找到。")
        raise typer.Exit(code=1)
    typer.echo(content)


@paper_app.command("compare")
def compare(paper_ids: list[str] = typer.Argument(..., help="要对比的论文 ID")):
    """横向对比多篇论文。"""
    _, manager = _make_orchestrator()
    orch, _ = _make_orchestrator()
    async def _run():
        return await manager.compare_papers(paper_ids, provider=orch.provider, model=orch.model)
    comparison = asyncio.run(_run())
    typer.echo("=== 论文横向对比 ===\n")
    typer.echo(comparison.synthesis or "对比分析完成。")


@paper_app.command("delete")
def delete(
    paper_id: str = typer.Argument(..., help="论文 ID"),
    force: bool = typer.Option(False, "--force", "-f", help="跳过确认"),
):
    """删除论文及其所有分析结果。"""
    if not force:
        confirm = typer.confirm(f"确认删除论文 {paper_id}？")
        if not confirm:
            raise typer.Abort()
    _, manager = _make_orchestrator()
    if manager.delete_paper(paper_id):
        typer.echo(f"论文 {paper_id} 已删除。")
    else:
        typer.echo(f"论文 {paper_id} 未找到。")
