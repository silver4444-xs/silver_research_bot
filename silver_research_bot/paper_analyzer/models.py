"""论文研读 Agent 数据模型"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FormulaExplanation(BaseModel):
    """单条公式解释"""

    model_config = ConfigDict(extra='forbid')
    index: int
    latex: str
    markdown: str
    explanation: str
    context: str = ""


class PaperMeta(BaseModel):
    """论文元数据"""

    model_config = ConfigDict(extra='forbid')
    paper_id: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    language: str = "en"
    page_count: int = 0
    formula_count: int = 0
    sections: list[dict] = Field(default_factory=list)
    uploaded_at: str = Field(default_factory=_utc_now)
    file_path: str = ""
    status: str = "uploaded"
    error_message: str | None = None


class PaperAnalysis(BaseModel):
    """论文分析结果容器"""

    model_config = ConfigDict(extra='forbid')
    paper_id: str
    translation: str | None = None
    system_model: str = ""
    problem_formulation: str = ""
    optimization_algorithm: str = ""
    experiment_design: str = ""
    formula_explanations: list[FormulaExplanation] = Field(default_factory=list)
    visualization_html: str = ""
    artifacts: list[dict] = Field(default_factory=list)
    audit_log: list[dict] = Field(default_factory=list)
    completed_at: str | None = None


class CrossPaperComparison(BaseModel):
    """横向对比分析"""

    model_config = ConfigDict(extra='forbid')
    paper_ids: list[str] = Field(default_factory=list)
    dimensions: dict = Field(default_factory=dict)
    synthesis: str = ""
    comparison_html: str = ""
    skipped_ids: list[str] = Field(default_factory=list)
    # v2 structured fields
    structured: Optional["StructuredComparison"] = None
    chart_data: dict = Field(default_factory=dict)
    metrics: list[dict] = Field(default_factory=list)


class ComparisonDimension(BaseModel):
    """单个对比维度"""

    model_config = ConfigDict(extra='forbid')
    name: str = ""
    paper_data: dict[str, str] = Field(default_factory=dict)
    extracted_scores: dict[str, float] = Field(default_factory=dict)
    extracted_items: dict[str, list] = Field(default_factory=dict)
    score_reasons: dict[str, str] = Field(default_factory=dict)


class StructuredComparison(BaseModel):
    """结构化对比结果 (v2)"""

    model_config = ConfigDict(extra='forbid')
    paper_ids: list[str] = Field(default_factory=list)
    dimensions: dict[str, ComparisonDimension] = Field(default_factory=dict)
    metrics: list[dict] = Field(default_factory=list)
    scores: dict[str, dict[str, float]] = Field(default_factory=dict)
    formula_overlap: dict[str, float] = Field(default_factory=dict)
    citation_overlap: dict[str, int] = Field(default_factory=dict)
    similarity_matrix: list[list[float]] = Field(default_factory=list)
    chart_data: dict = Field(default_factory=dict)
    synthesis_md: str = ""
    created_at: str = ""
    error: str = ""
    model_used: str = ""
    total_time_seconds: float = 0.0
    phase_times: dict[str, float] = Field(default_factory=dict)


class StageResult(BaseModel):
    """Pipeline 单个阶段的执行结果"""

    model_config = ConfigDict(extra='forbid')
    stage_name: str
    status: str = "pending"
    output_path: str = ""
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: list[dict] = Field(default_factory=list)


class AnalysisPlan(BaseModel):
    """论文分析执行计划"""

    model_config = ConfigDict(extra='forbid')
    paper_id: str
    paper_path: str
    language: str
    stages: list[StageResult] = Field(default_factory=list)
    created_at: str = Field(default_factory=_utc_now)


# Resolve forward references for self-referencing types
ComparisonDimension.model_rebuild()
StructuredComparison.model_rebuild()
CrossPaperComparison.model_rebuild()
