"""论文研读 Agent 专用工具"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    pass


def _already_analyzed_response(paper_id: str, extracted: dict, ws_root) -> str:
    """Build a helpful response for papers that have already been analyzed."""
    paper_dir = ws_root / "papers" / paper_id
    artifacts = []
    for fn in sorted(paper_dir.glob("*.md")):
        artifacts.append(fn.name)
    for fn in sorted(paper_dir.glob("*.html")):
        artifacts.append(fn.name)
    return json.dumps({
        "status": "already_analyzed",
        "paper_id": paper_id,
        "title": extracted.get("title", ""),
        "language": extracted.get("language", "auto"),
        "workspace_dir": str(paper_dir),
        "message": (
            f"此论文已分析完成。可用 read_paper_artifact 读取以下产物:\n"
            + ", ".join(artifacts[:12])
            + ("…" if len(artifacts) > 12 else "")
        ),
        "available_artifacts": artifacts,
        "suggested_next": [
            "用 read_paper_artifact(paper_id=\"{}\", artifact_type=\"translation\") 读翻译".format(paper_id),
            "用 read_paper_artifact(paper_id=\"{}\", artifact_type=\"system_model\") 读系统模型分析".format(paper_id),
            "用 compare_papers 与其他论文横向对比",
        ],
    }, ensure_ascii=False, indent=2)


@tool_parameters(
    tool_parameters_schema(
        pdf_path=StringSchema("PDF 论文文件的绝对路径"),
        language=StringSchema("论文语言: 'auto' 自动检测, 'en' 英文, 'zh' 中文"),
        paper_id=StringSchema("可选：已上传的论文 ID，若提供则跳过重复的 PDF 提取阶段"),
        required=["pdf_path"],
    )
)
class AnalyzePaperTool(Tool):
    """触发论文分析流程的工具。支持 paper_id 参数复用已有的提取数据。"""

    def __init__(self, orchestrator=None):
        self._orchestrator = orchestrator
        self._event_emitter: Callable[[str], Awaitable[None]] | None = None

    @property
    def name(self) -> str:
        return "analyze_paper"

    @property
    def description(self) -> str:
        return (
            "Analyze a PDF academic paper. English papers get translated "
            "(formulas to LaTeX), systematically analyzed across 4 dimensions, "
            "with per-formula explanations and Mermaid visualization. "
            "Chinese papers skip translation. "
            "If this paper was already uploaded (paper_id known), pass paper_id "
            "to skip re-extraction."
        )

    @property
    def read_only(self) -> bool:
        return False

    def set_event_emitter(self, emitter: Callable[[str], Awaitable[None]]) -> None:
        self._event_emitter = emitter
        logger.info("analyze_paper SSE emitter wired: %s", emitter is not None)

    async def _emit_analysis_complete(self, paper_id: str, ws_root, result_title: str = "",
                                       result_language: str = "", result_artifacts: list | None = None,
                                       result_workspace_dir: str = "") -> None:
        """Emit analysis_complete SSE event from existing paper data."""
        if not self._event_emitter:
            return
        try:
            paper_dir = ws_root / "papers" / paper_id
            summary = {
                "paper_id": paper_id,
                "title": result_title,
                "language": result_language or "en",
                "page_count": 0,
                "formula_count": 0,
                "figure_count": 0,
                "has_translation": False,
                "scores": {},
                "audit_passed": True,
                "issue_count": 0,
                "key_findings": [],
                "artifacts": result_artifacts or [],
                "workspace_dir": result_workspace_dir or str(paper_dir),
                "venue": "",
            }
            if paper_dir.exists():
                ef = paper_dir / "extracted.json"
                if ef.exists():
                    ed = json.loads(ef.read_text(encoding="utf-8"))
                    if not summary["title"]:
                        summary["title"] = ed.get("title", "")
                    if not summary["language"] or summary["language"] == "auto":
                        summary["language"] = ed.get("language", "en")
                    summary["page_count"] = ed.get("page_count", 0)
                    summary["formula_count"] = ed.get("formula_count", 0)
                    summary["figure_count"] = len(ed.get("figures", []))
                    summary["venue"] = ed.get("venue", "")
                rf = paper_dir / "review_scores.json"
                if rf.exists():
                    summary["scores"] = json.loads(rf.read_text(encoding="utf-8"))
                af = paper_dir / "audit_report.json"
                if af.exists():
                    ad = json.loads(af.read_text(encoding="utf-8"))
                    summary["audit_passed"] = ad.get("passed", True)
                    summary["issue_count"] = len(ad.get("issues", []))
                sf = paper_dir / "analysis_summary.json"
                if sf.exists():
                    sd = json.loads(sf.read_text(encoding="utf-8"))
                    summary["key_findings"] = sd.get("key_findings", [])
                    summary["has_translation"] = sd.get("has_translation", False)
                # Collect artifact filenames from disk
                if not summary["artifacts"]:
                    artifacts = []
                    for fn in sorted(paper_dir.glob("*.md")):
                        artifacts.append(fn.name)
                    for fn in sorted(paper_dir.glob("*.html")):
                        artifacts.append(fn.name)
                    summary["artifacts"] = artifacts
            await self._event_emitter(json.dumps({
                "type": "analysis_complete",
                "paper_id": paper_id,
                "summary": summary,
            }, ensure_ascii=False))
        except Exception:
            logger.exception("Failed to emit analysis_complete event")

    async def execute(self, pdf_path: str, language: str = "auto", paper_id: str = "", **kwargs):
        logger.info("analyze_paper execute start: emitter_wired=%s", self._event_emitter is not None)
        if not self._orchestrator:
            return "Error: orchestrator not configured"
        resolved = os.path.abspath(pdf_path)
        if not os.path.isfile(resolved):
            return f"Error: file not found: {pdf_path}"

        # If paper_id given, load existing extraction data to skip Stage 0
        paper_meta = None
        ws_root = getattr(self._orchestrator, "workspace", None)
        if paper_id and ws_root:
            ef = ws_root / "papers" / paper_id / "extracted.json"
            if ef.exists():
                try:
                    extracted = json.loads(ef.read_text(encoding="utf-8"))
                    # Check if already fully analyzed
                    pf = ws_root / "papers" / paper_id / "progress.json"
                    if pf.exists():
                        progress = json.loads(pf.read_text(encoding="utf-8"))
                        if progress.get("stage") == "review" and progress.get("status") == "completed":
                            await self._emit_analysis_complete(
                                paper_id, ws_root,
                                result_title=extracted.get("title", ""),
                                result_language=extracted.get("language", "auto"),
                            )
                            return _already_analyzed_response(paper_id, extracted, ws_root)
                    paper_meta = {
                        "paper_id": paper_id,
                        "title": extracted.get("title", ""),
                        "language": extracted.get("language", "auto"),
                        "workspace_dir": str(ws_root / "papers" / paper_id),
                        "full_text": extracted.get("full_text", ""),
                        "page_count": extracted.get("page_count", 0),
                        "formula_count": extracted.get("formula_count", 0),
                        "file_path": extracted.get("file_path", resolved),
                    }
                except Exception:
                    pass  # Fall through to fresh extraction

        # Idempotency: compute content hash, scan workspace for matching papers
        content_hash = ""
        if not paper_meta and ws_root:
            try:
                content_hash = hashlib.sha256(open(resolved, "rb").read()).hexdigest()
            except Exception:
                content_hash = ""
        if not paper_meta and ws_root and content_hash:
            papers_dir = ws_root / "papers"
            if papers_dir.exists():
                for d in papers_dir.iterdir():
                    if d.is_dir():
                        ef = d / "extracted.json"
                        if not ef.exists():
                            continue
                        try:
                            extracted = json.loads(ef.read_text(encoding="utf-8"))
                        except Exception:
                            continue
                        # Match by content_hash (preferred) or fallback to file_path
                        if extracted.get("content_hash") == content_hash or extracted.get("file_path") == resolved:
                            pf = d / "progress.json"
                            if pf.exists():
                                progress = json.loads(pf.read_text(encoding="utf-8"))
                                if progress.get("stage") == "review" and progress.get("status") == "completed":
                                    await self._emit_analysis_complete(
                                        d.name, ws_root,
                                        result_title=extracted.get("title", ""),
                                        result_language=extracted.get("language", "auto"),
                                    )
                                    return _already_analyzed_response(d.name, extracted, ws_root)
                            # Found extracted but not yet analyzed — reuse metadata
                            paper_meta = {
                                "paper_id": d.name,
                                "title": extracted.get("title", ""),
                                "language": extracted.get("language", "auto"),
                                "workspace_dir": str(d),
                                "full_text": extracted.get("full_text", ""),
                                "page_count": extracted.get("page_count", 0),
                                "formula_count": extracted.get("formula_count", 0),
                                "file_path": extracted.get("file_path", resolved),
                                "content_hash": extracted.get("content_hash", content_hash),
                            }
                            break
        try:
            async def _on_progress(evt: dict) -> None:
                logger.debug("analyze_paper progress: stage=%s status=%s emitter=%s",
                             evt.get("stage"), evt.get("status"), self._event_emitter is not None)
                if self._event_emitter:
                    await self._event_emitter(json.dumps({
                        "type": "tool_progress",
                        "name": "analyze_paper",
                        "stage": evt.get("stage", ""),
                        "status": evt.get("status", ""),
                        "message": evt.get("message", ""),
                    }, ensure_ascii=False))

            result = await self._orchestrator.analyze_paper(
                pdf_path, language, paper_meta=paper_meta, on_progress=_on_progress,
            )
            # Emit structured summary for frontend report rendering
            await self._emit_analysis_complete(
                result["paper_id"],
                getattr(self._orchestrator, "workspace", None),
                result_title=result["title"],
                result_language=result["language"],
                result_artifacts=result.get("artifacts", []),
                result_workspace_dir=result.get("workspace_dir", ""),
            )

            return json.dumps({
                "paper_id": result["paper_id"],
                "title": result["title"],
                "language": result["language"],
                "status": result["status"],
                "artifacts": result["artifacts"],
                "workspace_dir": result.get("workspace_dir", ""),
            }, ensure_ascii=False, indent=2)
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
        self._event_emitter: Callable[[str], Awaitable[None]] | None = None

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

    def set_event_emitter(self, emitter: Callable[[str], Awaitable[None]]) -> None:
        self._event_emitter = emitter

    async def execute(self, paper_ids: str, **kwargs):
        if not self._manager:
            return "Error: paper manager not configured"
        try:
            ids = [pid.strip() for pid in paper_ids.split(",") if pid.strip()]
            if len(ids) < 2:
                return "Error: need at least 2 paper IDs for comparison"
            if self._event_emitter:
                await self._event_emitter(json.dumps({
                    "type": "tool_progress",
                    "name": "compare_papers",
                    "stage": "compare",
                    "status": "running",
                    "message": f"正在对比 {len(ids)} 篇论文…",
                }, ensure_ascii=False))
            result = await self._manager.compare_papers(ids)
            if self._event_emitter:
                await self._event_emitter(json.dumps({
                    "type": "tool_progress",
                    "name": "compare_papers",
                    "stage": "compare",
                    "status": "completed",
                    "message": "对比完成",
                }, ensure_ascii=False))
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return f"Error comparing papers: {e}"


@tool_parameters(
    tool_parameters_schema(
        paper_id=StringSchema("特定论文 ID，留空则列出所有论文"),
        required=[],
    )
)
class ListPapersTool(Tool):
    """列出所有已分析的论文或查询单篇论文详情。"""

    def __init__(self, manager=None):
        self._manager = manager

    @property
    def name(self) -> str:
        return "list_papers"

    @property
    def description(self) -> str:
        return (
            "List all papers that have been uploaded and analyzed. "
            "Returns paper_id, title, language, status, and available artifacts for each. "
            "Pass paper_id to get detailed info for a specific paper (without full content — "
            "use read_paper_artifact for that)."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, paper_id: str = "", **kwargs):
        if not self._manager:
            return "Error: paper manager not configured"
        if paper_id:
            paper = self._manager.get_paper(paper_id)
            if not paper:
                return f"Paper '{paper_id}' not found."
            return json.dumps({
                "paper_id": paper.get("paper_id", ""),
                "title": paper.get("title", ""),
                "language": paper.get("language", ""),
                "status": paper.get("status", ""),
                "page_count": paper.get("page_count", 0),
                "has_translation": paper.get("has_translation", False),
                "artifacts": paper.get("artifacts", []),
            }, ensure_ascii=False, indent=2)
        papers = self._manager.list_papers()
        return json.dumps([{
            "paper_id": p.get("paper_id", ""),
            "title": p.get("title", ""),
            "language": p.get("language", ""),
            "status": p.get("status", ""),
            "artifacts": p.get("artifacts", []),
        } for p in papers], ensure_ascii=False, indent=2)


ARTIFACT_TYPES = {
    "translation": "translation.md",
    "system_model": "analysis_system_model.md",
    "problem_formulation": "analysis_problem.md",
    "optimization_algorithm": "analysis_algorithm.md",
    "experiment_design": "analysis_experiment.md",
    "algorithm": "analysis_algorithm.md",
    "experiment": "analysis_experiment.md",
    "formulas": "formula_explanations.md",
    "visualization": "analysis_visualization.html",
    "citation_graph": "citation_graph.html",
    "review_theory": "review_theory.md",
    "review_engineering": "review_engineering.md",
    "review_domain": "review_domain.md",
    "audit": "audit_report.json",
}

@tool_parameters(
    tool_parameters_schema(
        paper_id=StringSchema("论文 ID"),
        artifact_type=StringSchema(
            "产物类型: " + ", ".join(ARTIFACT_TYPES.keys())
        ),
        required=["paper_id", "artifact_type"],
    )
)
class ReadPaperArtifactTool(Tool):
    """读取论文的特定分析产物内容。"""

    MAX_CHARS = 8000

    def __init__(self, manager=None):
        self._manager = manager

    @property
    def name(self) -> str:
        return "read_paper_artifact"

    @property
    def description(self) -> str:
        return (
            "Read a specific analysis artifact for a previously analyzed paper. "
            "Use list_papers first to discover available papers and artifact types. "
            "Valid artifact types: " + ", ".join(sorted(ARTIFACT_TYPES.keys())) + "."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(self, paper_id: str, artifact_type: str, **kwargs):
        if not self._manager:
            return "Error: paper manager not configured"
        content = self._manager.get_artifact(paper_id, artifact_type)
        if content is None:
            return (
                f"Artifact '{artifact_type}' not found for paper '{paper_id}'. "
                f"Valid types: {', '.join(sorted(ARTIFACT_TYPES.keys()))}"
            )
        if len(content) > self.MAX_CHARS:
            return (
                content[:self.MAX_CHARS]
                + f"\n\n… (truncated at {self.MAX_CHARS} chars, "
                + f"total {len(content)} chars. Use specific artifact types for focused reading.)"
            )
        return content
