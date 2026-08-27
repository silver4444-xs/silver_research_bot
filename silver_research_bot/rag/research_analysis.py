"""论文分析产物索引 — 把 8 阶段分析产出的 .md（含三视角审稿）与摘要关键发现写入 ResearchRAG。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from silver_research_bot.rag.research_rag import ResearchRAG


# 分析产物文件名映射：kind → 文件名（含三视角审稿 review_*.md，统一以 ResearchRAG 为单一检索源）
ANALYSIS_ARTIFACTS: dict[str, str] = {
    "system_model": "analysis_system_model.md",
    "problem": "analysis_problem.md",
    "algorithm": "analysis_algorithm.md",
    "experiment": "analysis_experiment.md",
    "formula": "formula_explanations.md",
    "review_theory": "review_theory.md",
    "review_engineering": "review_engineering.md",
    "review_domain": "review_domain.md",
    "translation": "translation.md",
    # "summary" 特殊处理，来自 analysis_summary.json 的 key_findings
}


def read_analysis_artifacts(paper_dir: Path) -> dict[str, str]:
    """读取一篇论文的分析产物，返回 {kind: 文本内容}；缺失文件与空摘要自动跳过。"""
    artifacts: dict[str, str] = {}
    for kind, filename in ANALYSIS_ARTIFACTS.items():
        path = paper_dir / filename
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8").strip()
        except Exception:
            continue
        if text:
            artifacts[kind] = text

    # 摘要关键发现：analysis_summary.json → key_findings（list[str]）
    summary_path = paper_dir / "analysis_summary.json"
    if summary_path.exists():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            findings = [str(f).strip() for f in (data.get("key_findings") or []) if str(f).strip()]
            if findings:
                artifacts["summary"] = "\n".join(f"- {f}" for f in findings)
        except Exception:
            pass

    return artifacts


def _extract_paper_metadata(extracted: dict) -> dict[str, Any]:
    """从 extracted.json 提取可过滤元数据（author/year/venue/tag/abstract），缺省/非法则跳过。

    诚实边界：paper-analyzer 管线的 p_xxx 命名空间与 RAG 的 uuid[:10] 不重叠，且当前
    extracted.json 大多不含这些字段 → 此处仅建立「机制」，真实 author/year 依赖未来数据源改造。
    """
    meta: dict[str, Any] = {}
    for key in ("authors", "tags"):
        val = extracted.get(key)
        if isinstance(val, list) and val:
            meta[key] = val
    for key in ("venue", "abstract"):
        val = extracted.get(key)
        if isinstance(val, str) and val.strip():
            meta[key] = val.strip()
    year = extracted.get("year")
    if isinstance(year, int):
        meta["year"] = year
    return meta


async def ingest_paper_analysis(rag: "ResearchRAG", papers_dir: Path, paper_id: str) -> None:
    """统一入口：把一篇论文的分析产物写入 ResearchRAG（幂等，失败仅记日志）。"""
    paper_dir = papers_dir / paper_id
    title = paper_id
    metadata: dict[str, Any] = {}

    # 从 extracted.json 解析论文标题 + 可过滤元数据（formula/figure/table 不入 RAG）
    extracted_path = paper_dir / "extracted.json"
    if extracted_path.exists():
        try:
            extracted = json.loads(extracted_path.read_text(encoding="utf-8"))
            title = extracted.get("title") or paper_id
            metadata = _extract_paper_metadata(extracted)
        except Exception:
            logger.exception("RAG title resolution failed for {}", paper_id)

    # 分析产物入库（chunk_type="analysis"）
    try:
        artifacts = read_analysis_artifacts(paper_dir)
        if artifacts:
            await rag.ingest_analysis(paper_id, title, artifacts, metadata=metadata or None)
    except Exception:
        logger.exception("RAG analysis ingest failed for {}", paper_id)
