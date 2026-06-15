"""
silver_research_bot - 一个轻量级 AI agent 框架
"""

from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
import tomllib

from silver_research_bot.research_app import app as research_fastapi_app


def _read_pyproject_version() -> str | None:
    """Read the source-tree version when package metadata is unavailable."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    if not pyproject.exists():
        return None
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    return data.get("project", {}).get("version")


def _resolve_version() -> str:
    try:
        return _pkg_version("silver_research_bot-ai")
    except PackageNotFoundError:
        # Source checkouts often import silver_research_bot without installed dist-info.
        return _read_pyproject_version() or "0.1.5.post1"


__version__ = _resolve_version()
__logo__ = "⚪"

from silver_research_bot.silver_research_bot import silver_research_bot, RunResult

__all__ = ["silver_research_bot", "RunResult", "research_fastapi_app"]
