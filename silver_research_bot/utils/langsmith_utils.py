"""LangSmith tracing utilities for LLM call monitoring and pipeline observability.

Minimal integration: wraps LangSmith's run tree API for automatic tracing
of all LLM calls and pipeline stages. Zero overhead when not configured.

Enable by setting environment variables:
    LANGCHAIN_TRACING_V2=true
    LANGCHAIN_API_KEY=ls_...
    LANGCHAIN_PROJECT=silver-research-bot  (optional, defaults to "silver-research-bot")
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from loguru import logger

_langsmith_available: bool | None = None


def _check_langsmith() -> bool:
    global _langsmith_available
    if _langsmith_available is not None:
        return _langsmith_available
    try:
        import langsmith

        api_key = os.getenv("LANGCHAIN_API_KEY") or os.getenv("LANGSMITH_API_KEY")
        if not api_key:
            _langsmith_available = False
            return False
        _langsmith_available = True
        return True
    except ImportError:
        _langsmith_available = False
        return False


def _project_name() -> str:
    return os.getenv("LANGCHAIN_PROJECT", "silver-research-bot")


@contextmanager
def trace_llm_call(
    model: str = "",
    provider: str = "",
    messages_count: int = 0,
    tools_count: int = 0,
    run_name: str | None = None,
):
    """Trace an LLM call via LangSmith. Use as async context manager.

    Usage:
        with trace_llm_call(model="gpt-4", provider="openai", messages_count=3) as run:
            response = await provider.chat(...)
            if run:
                run.outputs = {"content": response.content[:200]}
                run.metadata["usage"] = response.usage
    """
    if not _check_langsmith():
        yield None
        return

    import langsmith

    started_at = datetime.now(timezone.utc)
    run: Any = None
    try:
        parent = langsmith.get_current_run_tree()
        run = langsmith.RunTree(
            name=run_name or "llm.call",
            run_type="llm",
            inputs={
                "model": model,
                "provider": provider,
                "messages_count": messages_count,
                "tools_count": tools_count,
            },
            parent_run=parent,
            project_name=_project_name(),
        )
        yield run
    except Exception:
        yield None
    finally:
        if run is not None:
            try:
                run.end_time = datetime.now(timezone.utc)
                run.metadata["latency_ms"] = (run.end_time - started_at).total_seconds() * 1000
                run.post()
            except Exception as e:
                logger.debug(f"LangSmith run.post() failed: {e}")


@contextmanager
def trace_pipeline_stage(stage_name: str, paper_id: str = "", metadata: dict | None = None):
    """Trace a pipeline stage in the orchestrator.

    Usage:
        with trace_pipeline_stage("translate", paper_id="p_xxx") as run:
            result = await translate_paper(...)
            if run:
                run.outputs = {"chars": len(result)}
    """
    if not _check_langsmith():
        yield None
        return

    import langsmith

    started_at = datetime.now(timezone.utc)
    run: Any = None
    try:
        parent = langsmith.get_current_run_tree()
        run = langsmith.RunTree(
            name=f"pipeline.{stage_name}",
            run_type="chain",
            inputs={"stage": stage_name, "paper_id": paper_id},
            metadata=metadata or {},
            parent_run=parent,
            project_name=_project_name(),
        )
        yield run
    except Exception:
        yield None
    finally:
        if run is not None:
            try:
                run.end_time = datetime.now(timezone.utc)
                run.metadata["latency_ms"] = (run.end_time - started_at).total_seconds() * 1000
                run.post()
            except Exception as e:
                logger.debug(f"LangSmith pipeline run.post() failed: {e}")


def trace_eval_result(evaluator_name: str, score: float, comment: str = "", metadata: dict | None = None):
    """Record an evaluation score to LangSmith."""
    if not _check_langsmith():
        return
    try:
        import langsmith

        run = langsmith.RunTree(
            name=f"eval.{evaluator_name}",
            run_type="evaluator",
            inputs={"evaluator": evaluator_name},
            outputs={"score": score, "comment": comment},
            metadata=metadata or {},
            project_name=_project_name(),
        )
        run.end_time = datetime.now(timezone.utc)
        run.post()
    except Exception as e:
        logger.debug(f"LangSmith eval trace failed: {e}")
