"""论文管理器 — 统一管理、检索与横向对比"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from silver_research_bot.paper_analyzer.models import CrossPaperComparison

if TYPE_CHECKING:
    from silver_research_bot.providers.base import LLMProvider


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PaperManager:
    """管理所有已上传论文的元数据和分析结果。"""

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace)
        self.papers_dir = self.workspace / "papers"
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.papers_dir / "index.json"
        self._index: dict[str, dict] = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text(encoding="utf-8"))
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(
            json.dumps(self._index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def register_paper(self, meta: dict, analysis: Any, artifacts: list[dict]) -> None:
        """将论文分析结果注册到索引。"""
        self._index[meta["paper_id"]] = {
            "paper_id": meta["paper_id"],
            "title": meta["title"],
            "language": meta["language"],
            "page_count": meta.get("page_count", 0),
            "formula_count": meta.get("formula_count", 0),
            "figure_count": meta.get("figure_count", 0),
            "table_count": meta.get("table_count", 0),
            "status": "completed",
            "uploaded_at": _utc_now(),
            "has_translation": getattr(analysis, "translation", None) is not None,
            "artifacts": [a["name"] for a in artifacts],
            "workspace_dir": meta.get("workspace_dir", ""),
        }
        self._save_index()

    def update_status(self, paper_id: str, status: str = "completed", **kwargs) -> None:
        if paper_id in self._index:
            self._index[paper_id]["status"] = status
            self._index[paper_id].update(kwargs)
            self._save_index()

    def list_papers(self) -> list[dict]:
        """列出所有论文，按上传时间倒序。"""
        return sorted(
            list(self._index.values()),
            key=lambda x: x.get("uploaded_at", ""), reverse=True,
        )

    def get_paper(self, paper_id: str) -> dict | None:
        """获取单篇论文的完整信息（含分析内容）。"""
        entry = self._index.get(paper_id)
        if not entry:
            return None

        ws_dir = Path(entry.get("workspace_dir", ""))
        result = dict(entry)

        filename_map = {
            "translation": "translation.md",
            "system_model": "analysis_system_model.md",
            "problem_formulation": "analysis_problem.md",
            "optimization_algorithm": "analysis_algorithm.md",
            "experiment_design": "analysis_experiment.md",
            "formula_explanations": "formula_explanations.md",
            "visualization_html": "analysis_visualization.html",
            "audit": "audit_report.json",
        }
        for key, filename in filename_map.items():
            fpath = ws_dir / filename
            if fpath.exists():
                result[key] = fpath.read_text(encoding="utf-8")

        return result

    def get_artifact(self, paper_id: str, artifact_type: str) -> str | None:
        """获取论文特定分析产物的内容。"""
        entry = self._index.get(paper_id)
        if not entry:
            return None

        ws_dir = Path(entry.get("workspace_dir", ""))
        name_map = {
            "translation": "translation.md",
            "system_model": "analysis_system_model.md",
            "problem_formulation": "analysis_problem.md",
            "algorithm": "analysis_algorithm.md",
            "experiment": "analysis_experiment.md",
            "formulas": "formula_explanations.md",
            "visualization": "analysis_visualization.html",
            "audit": "audit_report.json",
        }
        filename = name_map.get(artifact_type)
        if not filename:
            return None

        fpath = ws_dir / filename
        return fpath.read_text(encoding="utf-8") if fpath.exists() else None

    def delete_paper(self, paper_id: str) -> bool:
        """删除论文及其所有分析结果。"""
        entry = self._index.pop(paper_id, None)
        if not entry:
            return False
        ws_dir = Path(entry.get("workspace_dir", ""))
        if ws_dir.exists():
            shutil.rmtree(ws_dir, ignore_errors=True)
        self._save_index()
        return True

    async def compare_papers(
        self,
        paper_ids: list[str],
        provider: "LLMProvider | None" = None,
        model: str = "",
    ) -> CrossPaperComparison:
        """横向对比多篇论文，可选 LLM 增强。"""
        comparison = CrossPaperComparison(paper_ids=paper_ids)
        papers = [p for pid in paper_ids if (p := self.get_paper(pid))]

        if len(papers) < 2:
            return comparison

        dims = {
            "研究问题": {}, "系统模型": {}, "优化方法": {},
            "实验设计": {}, "论文规模": {},
        }
        for p in papers:
            pid = p["paper_id"]
            dims["研究问题"][pid] = p.get("problem_formulation", "")[:300]
            dims["系统模型"][pid] = p.get("system_model", "")[:300]
            dims["优化方法"][pid] = p.get("optimization_algorithm", "")[:300]
            dims["实验设计"][pid] = p.get("experiment_design", "")[:300]
            dims["论文规模"][pid] = (
                f"页数: {p.get('page_count', '?')}, "
                f"公式: {p.get('formula_count', '?')}"
            )
        comparison.dimensions = dims

        if provider and model:
            await self._llm_compare(comparison, papers, provider, model)

        return comparison

    async def _llm_compare(
        self, comparison: CrossPaperComparison,
        papers: list[dict], provider: "LLMProvider", model: str,
    ) -> None:
        from silver_research_bot.utils.prompt_templates import render_template

        system_prompt = render_template("paper/comparison.md", strip=True)
        parts = []
        for p in papers:
            parts.append(
                f"## {p['title']} (ID: {p['paper_id']})\n"
                f"- 系统模型: {p.get('system_model', '')[:800]}\n"
                f"- 问题表述: {p.get('problem_formulation', '')[:800]}\n"
                f"- 优化算法: {p.get('optimization_algorithm', '')[:800]}\n"
                f"- 实验设计: {p.get('experiment_design', '')[:800]}\n"
            )
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "\n\n".join(parts)},
                ],
                tools=None,
            )
            comparison.synthesis = response.content or ""
        except Exception:
            comparison.synthesis = "LLM 对比分析不可用，请查看各论文独立分析。"
