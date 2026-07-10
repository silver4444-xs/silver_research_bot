"""论文管理器 — 统一管理、检索与横向对比"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import json_repair
from loguru import logger

from silver_research_bot.paper_analyzer.models import (
    ComparisonDimension, CrossPaperComparison, StructuredComparison,
)

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
            "citation_graph_html": "citation_graph.html",
            "review_theory": "review_theory.md",
            "review_engineering": "review_engineering.md",
            "review_domain": "review_domain.md",
            "audit": "audit_report.json",
        }
        for key, filename in filename_map.items():
            fpath = ws_dir / filename
            if fpath.exists():
                result[key] = fpath.read_text(encoding="utf-8")

        ef = ws_dir / "extracted.json"
        if ef.exists():
            try:
                ex = json.loads(ef.read_text(encoding="utf-8"))
                result["formulas"] = ex.get("formulas", [])
                result["full_text"] = ex.get("full_text", "")
            except Exception:
                pass

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

    def delete_all_papers(self) -> int:
        """删除所有论文。返回删除数量。"""
        count = 0
        for paper_id in list(self._index.keys()):
            if self.delete_paper(paper_id):
                count += 1
        return count

    MAX_COMPARE_PAPERS = 8
    COMPARISONS_DIR = "comparisons"

    async def compare_papers(
        self,
        paper_ids: list[str],
        provider: "LLMProvider | None" = None,
        model: str = "",
        structured: bool = True,
        fast: bool = False,
    ) -> CrossPaperComparison:
        """横向对比多篇论文 (v2: 多阶段结构化对比 + 降级兼容)。"""
        if len(paper_ids) > self.MAX_COMPARE_PAPERS:
            paper_ids = paper_ids[:self.MAX_COMPARE_PAPERS]

        comparison = CrossPaperComparison(paper_ids=paper_ids)
        all_papers = [p for pid in paper_ids if (p := self.get_paper(pid))]

        papers = [p for p in all_papers if p.get("status") == "completed"]
        skipped = [p for p in all_papers if p.get("status") != "completed"]
        comparison.skipped_ids = [p["paper_id"] for p in skipped]

        if len(papers) < 2:
            extra = ""
            if len(paper_ids) > self.MAX_COMPARE_PAPERS:
                extra = "（已自动截断至前{}篇）".format(self.MAX_COMPARE_PAPERS)
            comparison.synthesis = (
                f"需要至少 2 篇已完成分析的论文才能对比"
                f"（已选 {len(all_papers)} 篇{extra}，"
                f"其中 {len(papers)} 篇已完成，{len(skipped)} 篇分析中）"
            )
            return comparison

        N = len(papers)
        trunc = max(200, int(500 * 5 / max(N, 5)))

        dims = {
            "系统模型": {}, "问题建模": {}, "算法方案": {}, "实验设计": {},
        }
        for p in papers:
            pid = p["paper_id"]
            dims["系统模型"][pid] = p.get("system_model", "")[:trunc]
            dims["问题建模"][pid] = p.get("problem_formulation", "")[:trunc]
            dims["算法方案"][pid] = p.get("optimization_algorithm", "")[:trunc]
            dims["实验设计"][pid] = p.get("experiment_design", "")[:trunc]
        comparison.dimensions = dims

        if provider and model:
            if structured and not fast:
                try:
                    await self._compare_structured(comparison, papers, provider, model)
                except Exception:
                    await self._llm_compare(comparison, papers, provider, model, trunc)
            else:
                await self._llm_compare(comparison, papers, provider, model, trunc)

        self._save_comparison(comparison, trunc)
        return comparison

    async def _compare_structured(
        self, comparison: CrossPaperComparison,
        papers: list[dict], provider: "LLMProvider", model: str,
    ) -> None:
        """v3: Per-dimension parallel scoring with calibration and visible failure."""
        sc = StructuredComparison(
            paper_ids=[p["paper_id"] for p in papers],
            created_at=_utc_now(),
        )
        logger.info(f"[compare_structured] Starting for {len(papers)} papers")

        # Phase 1: Per-dimension parallel scoring
        sc.dimensions = await self._score_dimensions_parallel(papers, provider, model)

        # Phase 1.5: Metrics extraction (separate focused call)
        sc.metrics = await self._extract_metrics(papers, provider, model)

        # Phase 2: Collect scores — NO silent 5.0 fallback. Missing = error.
        sc.scores = {}
        for p in papers:
            pid = p["paper_id"]
            sc.scores[pid] = {}
            for dim_name, dim in sc.dimensions.items():
                score = dim.extracted_scores.get(pid)
                if score is None:
                    raise ValueError(
                        f"Missing score for paper '{pid}' in dimension '{dim_name}'"
                    )
                sc.scores[pid][dim_name] = float(score)
        logger.info(f"[compare_structured] Collected {len(sc.scores)}p x {len(sc.dimensions)}d scores")

        # Phase 2.5: Score calibration (soft failure)
        await self._calibrate_scores(sc, papers, provider, model)

        # Phase 3: Local computation
        sc.formula_overlap = self._compute_formula_overlap(papers)
        sc.similarity_matrix = self._compute_similarity_matrix(papers)
        sc.citation_overlap = self._compute_citation_overlap(papers)

        # Phase 4: Chart data
        sc.chart_data = self._build_chart_data(sc, papers)

        # Phase 5: Synthesis
        sc.synthesis_md = await self._synthesize(sc, papers, provider, model)
        comparison.synthesis = sc.synthesis_md

        comparison.structured = sc
        comparison.chart_data = sc.chart_data
        comparison.metrics = sc.metrics
        comparison.comparison_html = self._build_comparison_html(sc.synthesis_md)
        logger.info("[compare_structured] Complete")

    async def _score_dimensions_parallel(
        self, papers: list[dict], provider: "LLMProvider", model: str,
    ) -> dict[str, ComparisonDimension]:
        """Score each dimension independently in parallel LLM calls."""
        from silver_research_bot.utils.prompt_templates import render_template

        dim_defs = {
            "系统模型": {"description": "实体组成、关键假设、模型复杂度、数学工具", "paper_key": "system_model"},
            "问题建模": {"description": "目标函数、约束条件、优化框架", "paper_key": "problem_formulation"},
            "算法方案": {"description": "算法类型、计算复杂度、收敛性与理论保证", "paper_key": "optimization_algorithm"},
            "实验设计": {"description": "数据集规模与多样性、基线数量、消融实验充分性、性能指标", "paper_key": "experiment_design"},
        }

        async def score_one(dim_name: str, dim_cfg: dict) -> ComparisonDimension:
            system_prompt = render_template("paper/comparison_dimension.md", strip=True)
            parts = []
            for p in papers:
                content = p.get(dim_cfg["paper_key"], "")[:4000]
                parts.append(f"## {p['title']} (paper_id: {p['paper_id']})\n{content}\n")
            user_msg = (
                f"维度名称：{dim_name}\n维度描述：{dim_cfg['description']}\n"
                f"共 {len(papers)} 篇论文需要评分：{[p['paper_id'] for p in papers]}\n\n"
                + "\n\n".join(parts) +
                "\n\n请按 JSON Schema 输出该维度的对比评分结果，必须包含 JSON 对象。"
            )
            logger.info(f"[score_dim] Calling LLM for '{dim_name}' ({len(papers)} papers)")
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
                tools=None, response_format={"type": "json_object"},
            )
            data = self._safe_json_extract(response.content or "", f"dim={dim_name}")
            return self._json_to_single_dimension(data, dim_name, papers)

        tasks = [score_one(name, cfg) for name, cfg in dim_defs.items()]
        dim_names = list(dim_defs.keys())
        results = await asyncio.gather(*tasks, return_exceptions=True)

        dimensions: dict[str, ComparisonDimension] = {}
        for i, result in enumerate(results):
            dn = dim_names[i]
            if isinstance(result, Exception):
                logger.error(f"[score_dim] '{dn}' FAILED: {result}. Retrying once...")
                try:
                    dimensions[dn] = await score_one(dn, dim_defs[dn])
                    logger.info(f"[score_dim] Retry SUCCEEDED for '{dn}'")
                except Exception as retry_exc:
                    raise RuntimeError(f"Failed to score '{dn}' after retry: {retry_exc}") from retry_exc
            else:
                dimensions[dn] = result
                logger.info(f"[score_dim] '{dn}' OK")
        return dimensions

    @staticmethod
    def _safe_json_extract(text: str, context: str = "unknown") -> dict:
        """Robust JSON extraction using json_repair. Raises on failure."""
        if not text:
            raise ValueError(f"Empty LLM response for '{context}'")
        stripped = text.strip()
        # Attempt 1: direct json_repair
        try:
            r = json_repair.loads(stripped)
            if isinstance(r, dict): return r
        except Exception: pass
        # Attempt 2: extract from markdown fence
        m = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
        if m:
            try:
                r = json_repair.loads(m.group(1).strip())
                if isinstance(r, dict): return r
            except Exception: pass
        # Attempt 3: find balanced { ... }
        start = text.find("{")
        if start >= 0:
            depth, end = 0, start
            for i in range(start, len(text)):
                if text[i] == "{": depth += 1
                elif text[i] == "}":
                    depth -= 1
                    if depth == 0: end = i + 1; break
            if end > start:
                try:
                    r = json_repair.loads(text[start:end])
                    if isinstance(r, dict): return r
                except Exception: pass
        logger.error(f"[json_extract] ALL attempts failed for '{context}'. Preview: {text[:300]}")
        raise ValueError(f"Failed to extract JSON for '{context}'")

    @staticmethod
    def _json_to_single_dimension(data: dict, dim_name: str, papers: list[dict]) -> ComparisonDimension:
        """Convert single-dimension LLM JSON to ComparisonDimension."""
        dim = ComparisonDimension(name=dim_name)
        paper_scores = data.get("paper_score", {})
        key_items = data.get("key_items", {})
        paper_key_map = {
            "系统模型": "system_model", "问题建模": "problem_formulation",
            "算法方案": "optimization_algorithm", "实验设计": "experiment_design",
        }
        for p in papers:
            pid = p["paper_id"]
            dim.paper_data[pid] = p.get(paper_key_map.get(dim_name, ""), "")[:500]
            score = paper_scores.get(pid)
            if score is None:
                raise ValueError(f"Missing score for '{pid}' in dimension '{dim_name}'")
            dim.extracted_scores[pid] = float(score)
            dim.extracted_items[pid] = key_items.get(pid, [])
        return dim

    async def _calibrate_scores(
        self, sc: StructuredComparison, papers: list[dict],
        provider: "LLMProvider", model: str,
    ) -> list[str]:
        """Post-scoring calibration: review score consistency across dimensions."""
        dim_names = list(next(iter(sc.scores.values())).keys())
        header = "| 论文 | " + " | ".join(dim_names) + " |"
        sep = "|------|" + "|".join(["------"] * len(dim_names)) + "|"
        rows = []
        for pid in sc.paper_ids:
            scores_str = " | ".join(str(sc.scores.get(pid, {}).get(d, "?")) for d in dim_names)
            title = ""
            for p in papers:
                if p["paper_id"] == pid: title = p.get("title", pid)[:50]; break
            rows.append(f"| {title} ({pid}) | {scores_str} |")
        scores_table = "\n".join([header, sep] + rows)
        user_msg = (
            f"## 当前评分矩阵\n\n{scores_table}\n\n"
            "请审查评分一致性：\n1. 同论文各维度是否一致\n2. 不同论文区分度是否足够（标准差≥1.0）\n"
            "3. 输出校准后的评分\n\n"
            '严格 JSON：{"calibrated_scores":{"paper_id":{"维度":分数}},"calibration_notes":["理由"]}'
        )
        logger.info("[calibrate] Running calibration")
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": "你是学术评审专家，审查评分一致性。"}, {"role": "user", "content": user_msg}],
                tools=None, response_format={"type": "json_object"},
            )
            data = self._safe_json_extract(response.content or "", "calibration")
            calibrated = data.get("calibrated_scores", {})
            notes = data.get("calibration_notes", [])
            if calibrated:
                for pid in sc.paper_ids:
                    if pid in calibrated:
                        for d, s in calibrated[pid].items():
                            if d in sc.scores.get(pid, {}):
                                old = sc.scores[pid][d]
                                sc.scores[pid][d] = float(s)
                                logger.info(f"[calibrate] {pid}/{d}: {old} -> {s}")
            return notes
        except Exception as e:
            logger.warning(f"[calibrate] Failed, keeping original scores: {e}")
            return []

    async def _extract_metrics(
        self, papers: list[dict], provider: "LLMProvider", model: str,
    ) -> list[dict]:
        """Extract quantitative metrics from experiment sections."""
        parts = []
        for p in papers:
            exp = p.get("experiment_design", "")[:2000]
            if exp:
                parts.append(f"## {p['title']} (paper_id: {p['paper_id']})\n{exp}\n")
        if not parts:
            logger.info("[metrics] No experiment content available"); return []
        user_msg = (
            "从以下论文实验部分提取共同可量化性能指标（准确率、F1、BLEU、推理时间等）：\n\n"
            + "\n\n".join(parts) +
            "\n\n输出 JSON 数组：\n"
            '[{"metric_name":"准确率","paper_values":{"paper_id_1":0.953},"unit":"%","dataset":"测试集","higher_is_better":true}]\n'
            "每条必须有 metric_name, paper_values, unit, dataset, higher_is_better。不少于 2 条。"
        )
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": "从论文实验部分提取定量性能指标。直接输出 JSON，禁止 Markdown 包裹。"}, {"role": "user", "content": user_msg}],
                tools=None, response_format={"type": "json_object"},
            )
            data = self._safe_json_extract(response.content or "", "metrics")
            metrics = data.get("metrics", data if isinstance(data, list) else [])
            if not isinstance(metrics, list): metrics = []
            logger.info(f"[metrics] Extracted {len(metrics)} metrics")
            return metrics
        except Exception as e:
            logger.warning(f"[metrics] Extraction failed: {e}"); return []

    def _compute_formula_overlap(self, papers: list[dict]) -> dict[str, float]:
        """Phase 3a: Compute formula Jaccard overlap between paper pairs."""
        paper_formulas: dict[str, set[str]] = {}
        for p in papers:
            pid = p["paper_id"]
            formulas = p.get("formulas", [])
            if isinstance(formulas, list):
                cmds = set()
                for f in formulas:
                    if isinstance(f, dict):
                        latex = f.get("latex", "")
                    else:
                        latex = str(f)
                    cmds.update(re.findall(r"\\[a-zA-Z]+", latex))
                paper_formulas[pid] = cmds
            else:
                paper_formulas[pid] = set()

        overlap: dict[str, float] = {}
        pids = [p["paper_id"] for p in papers]
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                a, b = paper_formulas.get(pids[i], set()), paper_formulas.get(pids[j], set())
                union = len(a | b)
                overlap[f"{pids[i]}|{pids[j]}"] = len(a & b) / max(union, 1)
        return overlap

    def _compute_citation_overlap(self, papers: list[dict]) -> dict[str, int]:
        """Phase 3b: Count shared citations between paper pairs."""
        paper_refs: dict[str, set[str]] = {}
        for p in papers:
            pid = p["paper_id"]
            citation_html = p.get("citation_graph_html", "")
            # Extract titles from citation graph data
            titles = set(re.findall(r'"name"\s*:\s*"([^"]+)"', citation_html))
            paper_refs[pid] = titles

        overlap: dict[str, int] = {}
        pids = [p["paper_id"] for p in papers]
        for i in range(len(pids)):
            for j in range(i + 1, len(pids)):
                a, b = paper_refs.get(pids[i], set()), paper_refs.get(pids[j], set())
                overlap[f"{pids[i]}|{pids[j]}"] = len(a & b)
        return overlap

    def _compute_similarity_matrix(self, papers: list[dict]) -> list[list[float]]:
        """Phase 3c: Compute NxN cosine similarity matrix based on text overlap."""
        import difflib
        n = len(papers)
        if n <= 1:
            return [[1.0]]

        texts = []
        for p in papers:
            parts = []
            for key in ["system_model", "problem_formulation", "optimization_algorithm", "experiment_design"]:
                t = p.get(key, "")
                if t:
                    parts.append(t[:2000])
            texts.append(" ".join(parts))

        matrix = [[1.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i + 1, n):
                sim = difflib.SequenceMatcher(None, texts[i], texts[j]).ratio()
                matrix[i][j] = round(sim, 3)
                matrix[j][i] = round(sim, 3)
        return matrix

    def _build_chart_data(self, sc: StructuredComparison, papers: list[dict]) -> dict:
        """Phase 4: Pre-compute chart data for D3.js rendering."""
        pids = sc.paper_ids
        labels = []
        for pid in pids:
            title = ""
            for p in papers:
                if p["paper_id"] == pid:
                    title = p.get("title", pid)[:40]
                    break
            labels.append(title or pid[:12])

        dim_names = list(sc.scores[pids[0]].keys()) if pids and pids[0] in sc.scores else []

        return {
            "paper_ids": pids,
            "paper_labels": labels,
            "dimension_names": dim_names,
            "radar": {
                "labels": dim_names,
                "datasets": [{"label": labels[i] if i < len(labels) else pid, "data": [
                    sc.scores.get(pid, {}).get(d, 5.0) for d in dim_names
                ]} for i, pid in enumerate(pids)],
            },
            "heatmap": {
                "labels": labels,
                "matrix": sc.similarity_matrix,
            },
            "stacked": {
                "labels": labels,
                "dimensions": dim_names,
                "data": [[sc.scores.get(pid, {}).get(d, 5.0) for d in dim_names] for pid in pids],
            },
            "bars": {
                "labels": labels,
                "datasets": [{"label": d, "data": [
                    sc.scores.get(pid, {}).get(d, 5.0) for pid in pids
                ]} for d in dim_names],
            },
            "overlap": {
                "pairs": list(sc.formula_overlap.keys()),
                "values": list(sc.formula_overlap.values()),
            },
        }

    async def _synthesize(
        self, sc: StructuredComparison, papers: list[dict],
        provider: "LLMProvider", model: str,
    ) -> str:
        """Phase 5: LLM synthesis with full structured context."""
        from silver_research_bot.utils.prompt_templates import render_template

        system_prompt = render_template("paper/comparison.md", strip=True)

        # Build a compact but complete context with clear title mapping
        title_map = {}
        for p in papers:
            title_map[p["paper_id"]] = p.get("title", p["paper_id"])[:60]

        lines = []
        lines.append("## 论文列表（综合分析中必须使用论文标题，禁止使用 paper_id）\n")
        for pid in sc.paper_ids:
            lines.append(f"- **{title_map.get(pid, pid)}** (ID: {pid})")

        lines.append("\n## 评分矩阵\n")
        dim_names = list(next(iter(sc.scores.values())).keys()) if sc.scores else []
        lines.append("| 论文 | " + " | ".join(dim_names) + " |")
        lines.append("|------|" + "|".join(["------"] * len(dim_names)) + "|")
        for pid in sc.paper_ids:
            title = title_map.get(pid, pid)[:30]
            scores_str = " | ".join(str(sc.scores.get(pid, {}).get(d, "?")) for d in dim_names)
            lines.append(f"| {title} | {scores_str} |")

        if sc.formula_overlap:
            lines.append("\n## 公式重叠度\n")
            for pair, val in sc.formula_overlap.items():
                pids = pair.split("|")
                a = title_map.get(pids[0], pids[0])[:20] if len(pids) > 0 else "?"
                b = title_map.get(pids[1], pids[1])[:20] if len(pids) > 1 else "?"
                lines.append(f"- {a} ↔ {b}: {val:.1%}")

        if sc.similarity_matrix:
            lines.append("\n## 论文相似度\n")
            for i, pid in enumerate(sc.paper_ids):
                if i < len(sc.similarity_matrix):
                    lines.append(f"- {title_map.get(pid, pid)[:30]}: {sc.similarity_matrix[i]}")

        lines.append("\n---\n## 各论文摘要\n")
        for p in papers:
            title = p.get("title", p["paper_id"])[:60]
            lines.append(
                f"### {title}\n"
                f"- 系统模型: {p.get('system_model', '')[:600]}\n"
                f"- 问题建模: {p.get('problem_formulation', '')[:600]}\n"
                f"- 算法方案: {p.get('optimization_algorithm', '')[:600]}\n"
                f"- 实验设计: {p.get('experiment_design', '')[:600]}\n"
            )

        user_msg = (
            f"## 重要规则\n"
            f"综合分析中引用论文时，必须使用论文标题，禁止使用 paper_id（如 p_xxx）。\n\n"
            f"共 {len(papers)} 篇论文。请基于以上数据生成综合分析报告。"
            f"要求：方法谱系、指标排行榜、趋势时间线、综合差异总结。"
            f"如有对比图表，请使用 ```mermaid 代码块输出。\n\n"
            + "\n".join(lines)
        )

        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
                tools=None,
            )
            return self._strip_mermaid_blocks(response.content or "")
        except Exception:
            return "综合分析生成失败"

    def _save_comparison(self, comparison: CrossPaperComparison, trunc: int) -> str:
        """Persist comparison result and return the comparison ID."""
        comp_dir = self.workspace / self.COMPARISONS_DIR
        comp_dir.mkdir(parents=True, exist_ok=True)
        ts = _utc_now().replace(":", "-")
        comp_id = f"cmp_{ts[:19]}"
        record = {
            "id": comp_id,
            "paper_ids": comparison.paper_ids,
            "dimensions": comparison.dimensions,
            "synthesis": comparison.synthesis,
            "comparison_html": comparison.comparison_html,
            "skipped_ids": comparison.skipped_ids,
            "truncation": trunc,
            "created_at": ts,
            "metrics": comparison.metrics,
        }
        if comparison.structured:
            sc = comparison.structured
            record["structured"] = {
                "paper_ids": sc.paper_ids,
                "scores": sc.scores,
                "formula_overlap": sc.formula_overlap,
                "citation_overlap": sc.citation_overlap,
                "similarity_matrix": sc.similarity_matrix,
                "chart_data": sc.chart_data,
                "synthesis_md": sc.synthesis_md,
                "created_at": sc.created_at,
            }
            record["chart_data"] = comparison.chart_data
        (comp_dir / f"{comp_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # Update index
        idx_path = comp_dir / "index.json"
        idx = []
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        idx.insert(0, {"id": comp_id, "paper_ids": comparison.paper_ids, "created_at": ts, "paper_count": len(comparison.paper_ids)})
        idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        return comp_id

    def list_comparisons(self) -> list[dict]:
        """List all saved comparisons."""
        comp_dir = self.workspace / self.COMPARISONS_DIR
        idx_path = comp_dir / "index.json"
        if not idx_path.exists():
            return []
        try:
            return json.loads(idx_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def get_comparison(self, comp_id: str) -> dict | None:
        """Get a saved comparison by ID."""
        comp_dir = self.workspace / self.COMPARISONS_DIR
        fpath = comp_dir / f"{comp_id}.json"
        if not fpath.exists():
            return None
        try:
            return json.loads(fpath.read_text(encoding="utf-8"))
        except Exception:
            return None

    def delete_comparison(self, comp_id: str) -> bool:
        """Delete a saved comparison."""
        comp_dir = self.workspace / self.COMPARISONS_DIR
        fpath = comp_dir / f"{comp_id}.json"
        if not fpath.exists():
            return False
        fpath.unlink()
        # Update index
        idx_path = comp_dir / "index.json"
        if idx_path.exists():
            try:
                idx = json.loads(idx_path.read_text(encoding="utf-8"))
                idx = [e for e in idx if e.get("id") != comp_id]
                idx_path.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        return True

    def export_comparison(self, comp_id: str) -> str | None:
        """Export comparison as ZIP file path (HTML report + CSV + MD)."""
        import zipfile
        import io

        record = self.get_comparison(comp_id)
        if not record:
            return None

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            # Markdown report
            md_content = f"# 论文横向对比报告\n\n生成时间: {record.get('created_at', '')}\n\n"
            md_content += f"## 对比论文\n\n"
            for pid in record.get("paper_ids", []):
                paper = self.get_paper(pid)
                title = paper.get("title", pid) if paper else pid
                md_content += f"- {title} ({pid})\n"
            md_content += f"\n## 综合分析\n\n{record.get('synthesis', '')}\n"
            zf.writestr("comparison_report.md", md_content)

            # Structured data CSV
            structured = record.get("structured", {})
            scores = structured.get("scores", {})
            if scores:
                dims = list(next(iter(scores.values())).keys()) if scores else []
                csv_lines = ["paper_id," + ",".join(dims)]
                for pid, sc in scores.items():
                    csv_lines.append(f"{pid}," + ",".join(str(sc.get(d, "")) for d in dims))
                zf.writestr("scores.csv", "\n".join(csv_lines))

            # HTML report
            html = "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"UTF-8\">"
            html += "<title>论文横向对比报告</title>"
            html += "<style>body{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;background:#0f0f1a;color:#e0e0e0}"
            html += "table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #333;padding:8px;text-align:left}"
            html += "th{background:#1a1a2e}h1,h2{color:#a78bfa}</style></head><body>"
            html += f"<h1>论文横向对比报告</h1><p>生成时间: {record.get('created_at', '')}</p>"
            paper_list_items = []
            for pid in record.get("paper_ids", []):
                paper = self.get_paper(pid)
                title = paper.get("title", pid) if paper else pid
                paper_list_items.append(f"<li>{title} ({pid})</li>")
            html += f"<h2>对比论文</h2><ul>{''.join(paper_list_items)}</ul>"
            if scores:
                dims = list(next(iter(scores.values())).keys()) if scores else []
                html += "<h2>评分矩阵</h2><table><tr><th>论文</th>" + "".join(f"<th>{d}</th>" for d in dims) + "</tr>"
                for pid, sc in scores.items():
                    html += f"<tr><td>{pid}</td>" + "".join(f"<td>{sc.get(d, '-')}</td>" for d in dims) + "</tr>"
                html += "</table>"
            html += f"<h2>综合分析</h2><pre style=\"white-space:pre-wrap\">{record.get('synthesis', '')}</pre>"
            html += "</body></html>"
            zf.writestr("comparison_report.html", html)

        buf.seek(0)
        export_dir = self.workspace / self.COMPARISONS_DIR / "exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        export_path = export_dir / f"{comp_id}.zip"
        export_path.write_bytes(buf.read())
        return str(export_path)

    async def _llm_compare(
        self, comparison: CrossPaperComparison,
        papers: list[dict], provider: "LLMProvider", model: str,
        trunc: int = 500,
    ) -> None:
        from silver_research_bot.utils.prompt_templates import render_template

        system_prompt = render_template("paper/comparison.md", strip=True)
        parts = []
        for p in papers:
            parts.append(
                f"## {p['title']} (ID: {p['paper_id']})\n"
                f"- 问题建模: {p.get('problem_formulation', '')[:trunc]}\n"
                f"- 系统模型: {p.get('system_model', '')[:trunc]}\n"
                f"- 算法方案: {p.get('optimization_algorithm', '')[:trunc]}\n"
                f"- 实验设计: {p.get('experiment_design', '')[:trunc]}\n"
            )
        meta = ", ".join(
            f"{p['paper_id']}: 页数{p.get('page_count') or '?'}/公式{p.get('formula_count') or '?'}"
            for p in papers
        )
        user_msg = (
            f"共 {len(papers)} 篇论文。规模参考: {meta}\n\n"
            + "\n\n".join(parts) +
            "\n\n请按以下结构输出对比分析（Markdown 格式）：\n"
            "## 方法谱系\n## 指标排行榜\n## 趋势时间线\n## 综合差异总结\n\n"
            "如有对比图表，请使用 ```mermaid 代码块输出。"
        )
        try:
            response = await provider.chat_with_retry(
                model=model,
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_msg}],
                tools=None,
            )
            content = response.content or ""
            comparison.synthesis = self._strip_mermaid_blocks(content)
            comparison.comparison_html = self._build_comparison_html(content)
        except Exception:
            comparison.synthesis = "LLM 对比分析不可用"

    @staticmethod
    def _strip_mermaid_blocks(text: str) -> str:
        """移除 mermaid 代码块（将放在 comparison_html 中单独渲染）。"""
        import re
        return re.sub(r"```mermaid\s*\n[\s\S]*?```", "", text).strip()

    @staticmethod
    def _build_comparison_html(text: str) -> str:
        """从 LLM 响应中提取 mermaid 代码块，构建独立 HTML 可视化页面。"""
        import re
        mermaid_blocks = re.findall(r"```mermaid\s*\n([\s\S]*?)```", text)
        if not mermaid_blocks:
            return ""
        charts = "\n".join(
            f'<div class="mermaid">{b.strip()}</div>' for b in mermaid_blocks
        )
        return (
            '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
            '<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>'
            "<script>mermaid.initialize({startOnLoad:true,theme:'default'});</script>"
            '<style>body{font-family:sans-serif;max-width:960px;margin:0 auto;padding:20px;'
            "background:#0f0f1a;color:#e0e0e0}.mermaid{margin:20px 0;text-align:center}"
            "</style></head><body>"
            f'<h2 style="color:#a78bfa">论文横向对比 · 可视化</h2>{charts}</body></html>'
        )
