"""论文研读 Agent 数据模型"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class FormulaExplanation:
    """单条公式解释"""

    index: int
    latex: str
    markdown: str
    explanation: str
    context: str = ""


@dataclass(slots=True)
class PaperMeta:
    """论文元数据"""

    paper_id: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    language: str = "en"
    page_count: int = 0
    formula_count: int = 0
    sections: list[dict] = field(default_factory=list)
    uploaded_at: str = field(default_factory=_utc_now)
    file_path: str = ""
    status: str = "uploaded"
    error_message: str | None = None


@dataclass(slots=True)
class PaperAnalysis:
    """论文分析结果容器"""

    paper_id: str
    translation: str | None = None
    system_model: str = ""
    problem_formulation: str = ""
    optimization_algorithm: str = ""
    experiment_design: str = ""
    formula_explanations: list[FormulaExplanation] = field(default_factory=list)
    visualization_html: str = ""
    artifacts: list[dict] = field(default_factory=list)
    audit_log: list[dict] = field(default_factory=list)
    completed_at: str | None = None


@dataclass(slots=True)
class CrossPaperComparison:
    """横向对比分析"""

    paper_ids: list[str] = field(default_factory=list)
    dimensions: dict = field(default_factory=dict)
    similarities: list[str] = field(default_factory=list)
    differences: list[str] = field(default_factory=list)
    synthesis: str = ""
    comparison_html: str = ""


@dataclass(slots=True)
class StageResult:
    """Pipeline 单个阶段的执行结果"""

    stage_name: str
    status: str = "pending"
    output_path: str = ""
    error: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    artifacts: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class AnalysisPlan:
    """论文分析执行计划"""

    paper_id: str
    paper_path: str
    language: str
    stages: list[StageResult] = field(default_factory=list)
    created_at: str = field(default_factory=_utc_now)
