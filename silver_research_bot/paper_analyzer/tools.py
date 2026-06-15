"""论文研读 Agent 专用工具"""

from __future__ import annotations

from typing import TYPE_CHECKING

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    pass


@tool_parameters(
    tool_parameters_schema(
        pdf_path=StringSchema("PDF 论文文件的绝对路径"),
        language=StringSchema("论文语言: 'auto' 自动检测, 'en' 英文, 'zh' 中文"),
        required=["pdf_path"],
    )
)
class AnalyzePaperTool(Tool):
    """触发论文分析流程的工具。"""

    def __init__(self, orchestrator=None):
        self._orchestrator = orchestrator

    @property
    def name(self) -> str:
        return "analyze_paper"

    @property
    def description(self) -> str:
        return (
            "Analyze a PDF academic paper. English papers get translated "
            "(formulas to LaTeX), systematically analyzed across 4 dimensions, "
            "with per-formula explanations and Mermaid visualization. "
            "Chinese papers skip translation."
        )

    @property
    def read_only(self) -> bool:
        return False

    async def execute(self, pdf_path: str, language: str = "auto", **kwargs):
        if not self._orchestrator:
            return "Error: orchestrator not configured"
        try:
            result = await self._orchestrator.analyze_paper(pdf_path, language)
            return result
        except Exception as e:
            return f"Error analyzing paper: {e}"


@tool_parameters(
    tool_parameters_schema(
        paper_ids=StringSchema("要对比的论文 ID 列表，逗号分隔"),
        required=["paper_ids"],
    )
)
class ComparePapersTool(Tool):
    """触发多篇论文横向对比的工具。"""

    def __init__(self, manager=None):
        self._manager = manager

    @property
    def name(self) -> str:
        return "compare_papers"

    @property
    def description(self) -> str:
        return (
            "Compare multiple analyzed papers across dimensions: "
            "research problem, system model, optimization method, "
            "experiment design, and contributions."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, paper_ids: str, **kwargs):
        if not self._manager:
            return "Error: paper manager not configured"
        try:
            ids = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
            result = await self._manager.compare_papers(ids)
            return result
        except Exception as e:
            return f"Error comparing papers: {e}"
