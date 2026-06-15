"""论文分析 Pipeline 编排器 — 先规划后执行，每阶段产出独立文档"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from silver_research_bot.paper_analyzer.models import (
    AnalysisPlan, PaperAnalysis, StageResult,
)
from silver_research_bot.paper_analyzer.extractor import extract_paper_meta

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperOrchestrator:
    """论文分析 Pipeline 编排器。

    实现"先规划后执行"模式，6个阶段顺序执行，
    每个阶段产出一个独立的 Markdown/HTML/JSON 文档。"""

    def __init__(self, provider: "LLMProvider", model: str, workspace: str | Path):
        self.provider = provider
        self.model = model
        self.workspace = Path(workspace)
        self._manager = None

    def set_manager(self, manager) -> None:
        self._manager = manager

    def _write_progress(self, paper_dir, stage, status, msg=""):
        (paper_dir / "progress.json").write_text(json.dumps({
            "stage": stage, "status": status, "message": msg,
            "updated_at": _utc_now(),
        }, ensure_ascii=False), encoding="utf-8")

    async def analyze_paper(self, pdf_path: str, language: str = "auto", paper_meta: dict | None = None) -> dict[str, Any]:
        """完整论文分析 Pipeline 入口。若提供 paper_meta 则跳过 Stage 0 提取。"""
        pdf_path_abs = str(Path(pdf_path).resolve())
        if paper_meta:
            meta_dict = paper_meta
            lang = meta_dict["language"]
            paper_dir = Path(meta_dict["workspace_dir"])
            paper_dir.mkdir(parents=True, exist_ok=True)
            full_text = meta_dict["full_text"]
            extracted = json.loads((paper_dir / "extracted.json").read_text(encoding="utf-8"))
        else:
            meta_dict = extract_paper_meta(pdf_path_abs, self.workspace)
            lang = language if language != "auto" else meta_dict["language"]
            paper_dir = Path(meta_dict["workspace_dir"])
            full_text = meta_dict["full_text"]
            extracted = json.loads((paper_dir / "extracted.json").read_text(encoding="utf-8"))
            self._write_progress(paper_dir, "parse", "completed", f"解析完成，{meta_dict['page_count']}页，{meta_dict['formula_count']}个公式")
        formulas: list[dict] = extracted.get("formulas", [])
        figures: list[dict] = extracted.get("figures", [])
        tables: list[dict] = extracted.get("tables", [])

        # ─── Build Plan ───
        plan = AnalysisPlan(
            paper_id=meta_dict["paper_id"],
            paper_path=pdf_path_abs, language=lang,
        )
        plan.stages = [StageResult(stage_name="parse", status="completed")]
        if lang == "en":
            plan.stages.append(StageResult(stage_name="translate"))
        plan.stages.extend([
            StageResult(stage_name="analyze"),
            StageResult(stage_name="formula_explain"),
            StageResult(stage_name="visualize"),
            StageResult(stage_name="audit"),
        ])
        _save_plan(plan, paper_dir)

        artifacts: list[dict] = []
        analysis = PaperAnalysis(paper_id=meta_dict["paper_id"])

        # ─── Stage 1a: Translate (en only) ───
        if lang == "en":
            from silver_research_bot.paper_analyzer.translator import translate_paper

            self._write_progress(paper_dir, "translate", "running", "正在翻译全文（公式→LaTeX）…")
            _mark(plan, "translate", "running")
            translation = await translate_paper(full_text, self.provider, self.model, figures=figures)
            (paper_dir / "translation.md").write_text(translation, encoding="utf-8")
            analysis.translation = translation
            artifacts.append({
                "name": "translation.md",
                "path": str(paper_dir / "translation.md"),
                "kind": "translation",
            })
            _mark(plan, "translate", "completed")
            self._write_progress(paper_dir, "translate", "completed", "翻译完成")

        # ─── Stage 1b: Analyze (4 dimensions parallel) ───
        from silver_research_bot.paper_analyzer.analyzer import analyze_dimensions

        self._write_progress(paper_dir, "analyze", "running", "正在四维系统分析（系统模型/问题表述/优化算法/实验设计）…")
        _mark(plan, "analyze", "running")
        dim_results = await analyze_dimensions(full_text, self.provider, self.model, lang)

        dim_files = {
            "system_model": "analysis_system_model.md",
            "problem_formulation": "analysis_problem.md",
            "optimization_algorithm": "analysis_algorithm.md",
            "experiment_design": "analysis_experiment.md",
        }
        for key, fname in dim_files.items():
            text = dim_results.get(key, "")
            (paper_dir / fname).write_text(text, encoding="utf-8")
            setattr(analysis, key, text)
            artifacts.append({
                "name": fname, "path": str(paper_dir / fname),
                "kind": f"analysis_{key}",
            })
        _mark(plan, "analyze", "completed")
        self._write_progress(paper_dir, "analyze", "completed", "四维分析完成")

        # ─── Stage 2: Formula explanations ───
        from silver_research_bot.paper_analyzer.formula_explainer import explain_formulas

        self._write_progress(paper_dir, "formula_explain", "running", "正在逐条解释公式…")
        _mark(plan, "formula_explain", "running")
        formulas_text = await explain_formulas(formulas, full_text, self.provider, self.model)
        (paper_dir / "formula_explanations.md").write_text(formulas_text, encoding="utf-8")
        artifacts.append({
            "name": "formula_explanations.md",
            "path": str(paper_dir / "formula_explanations.md"),
            "kind": "formula_explanations",
        })
        _mark(plan, "formula_explain", "completed")
        self._write_progress(paper_dir, "formula_explain", "completed", "公式解读完成")

        # ─── Stage 3: Visualization ───
        from silver_research_bot.paper_analyzer.visualizer import generate_visualization

        self._write_progress(paper_dir, "visualize", "running", "正在生成 Mermaid 可视化图表…")
        _mark(plan, "visualize", "running")
        vis_html = await generate_visualization(
            dim_results, formulas_text, meta_dict["title"],
            self.provider, self.model, formulas=formulas,
        )
        (paper_dir / "analysis_visualization.html").write_text(vis_html, encoding="utf-8")
        analysis.visualization_html = vis_html
        artifacts.append({
            "name": "analysis_visualization.html",
            "path": str(paper_dir / "analysis_visualization.html"),
            "kind": "visualization",
        })
        _mark(plan, "visualize", "completed")
        self._write_progress(paper_dir, "visualize", "completed", "可视化生成完成")

        # ─── Stage 4: Audit ───
        from silver_research_bot.paper_analyzer.auditor import audit_analysis

        self._write_progress(paper_dir, "audit", "running", "正在执行质量审计…")
        _mark(plan, "audit", "running")
        audit_report = await audit_analysis(
            meta_dict["paper_id"], analysis.translation, dim_results,
            formulas_text, vis_html, meta_dict["formula_count"],
            self.provider, self.model,
        )
        (paper_dir / "audit_report.json").write_text(
            json.dumps({
                "paper_id": audit_report.paper_id,
                "passed": audit_report.passed,
                "issues": audit_report.issues,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _mark(plan, "audit", "completed")
        self._write_progress(paper_dir, "audit", "completed", f"审计{'通过' if audit_report.passed else '未通过'}")

        # ─── Finalize ───
        analysis.artifacts = artifacts
        analysis.completed_at = _utc_now()

        # Update paper manager index
        if self._manager:
            self._manager.update_status(meta_dict["paper_id"],
                status="completed",
                has_translation=analysis.translation is not None)
        analysis.completed_at = _utc_now()

        (paper_dir / "analysis_summary.json").write_text(
            json.dumps({
                "paper_id": analysis.paper_id,
                "has_translation": analysis.translation is not None,
                "artifacts": analysis.artifacts,
                "completed_at": analysis.completed_at,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _save_plan(plan, paper_dir)

        if self._manager:
            self._manager.register_paper(meta_dict, analysis, artifacts)

        return {
            "paper_id": meta_dict["paper_id"],
            "title": meta_dict["title"],
            "language": lang,
            "status": "completed",
            "passed_audit": audit_report.passed,
            "artifacts": [a["name"] for a in artifacts],
            "workspace_dir": str(paper_dir),
        }


def _mark(plan: AnalysisPlan, stage_name: str, status: str) -> None:
    for s in plan.stages:
        if s.stage_name == stage_name:
            s.status = status
            if status == "running":
                s.started_at = _utc_now()
            elif status == "completed":
                s.completed_at = _utc_now()
            return


def _save_plan(plan: AnalysisPlan, paper_dir: Path) -> None:
    (paper_dir / "analysis_plan.json").write_text(
        json.dumps({
            "paper_id": plan.paper_id, "language": plan.language,
            "created_at": plan.created_at,
            "stages": [{
                "stage_name": s.stage_name, "status": s.status,
                "started_at": s.started_at, "completed_at": s.completed_at,
                "error": s.error,
            } for s in plan.stages],
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
