"""科研工作流的核心数据结构与工具函数。

该模块用于把研究想法、计划、实验结果、论文草稿和审计记录拆成清晰的可维护组件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class ResearchArtifact:
    """单个科研产物。"""

    name: str
    path: str
    kind: str


@dataclass
class ResearchAuditEvent:
    """审计事件。"""

    stage: str
    message: str
    timestamp: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchRunSummary:
    """研究运行摘要。"""

    run_id: str
    topic: str
    workspace: str
    status: str
    artifacts: list[ResearchArtifact] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


def utc_now() -> str:
    """返回 UTC 时间字符串。"""

    return datetime.now(timezone.utc).isoformat()


def ensure_workspace(path: str | Path) -> Path:
    """确保工作区目录存在。"""

    workspace = Path(path)
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace
