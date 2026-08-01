"""团队编排工具 — 让主 Agent 动态组建和运行多 Agent 协作 DAG"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger

from silver_research_bot.agent.tools.base import Tool, tool_parameters
from silver_research_bot.agent.tools.schema import StringSchema, tool_parameters_schema

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from silver_research_bot.agent.team import TeamOrchestrator

ROLE_HINT = "paper_reviewer, code_reviewer, literature_review, translator, formula_expert"


@tool_parameters(
    tool_parameters_schema(
        plan=StringSchema(
            'JSON team plan: {"tasks":[{"task_id":"1","description":"...","role":"paper_reviewer",'
            '"depends_on":[],"artifacts_in":[],"artifacts_out":["review"],'
            '"review_by":"2","revision_rounds":1,"arbiter_for":[]}],'
            '"aggregation_prompt":"optional summary instruction"}. '
            f"Available roles: {ROLE_HINT}. "
            "Parallel tasks can peer-review each other via review_by. "
            "Conflicting artifacts auto-trigger arbitration."
        ),
        required=["plan"],
    )
)
class TeamTool(Tool):
    """Orchestrate a team of role-based sub-agents to collaborate on a complex task using a DAG plan."""

    def __init__(self, orchestrator: "TeamOrchestrator") -> None:
        self._orchestrator = orchestrator
        self._event_emitter: "Callable[[str], Awaitable[None]] | None" = None

    @property
    def name(self) -> str:
        return "orchestrate_team"

    @property
    def description(self) -> str:
        return (
            "Orchestrate a team of role-based sub-agents to collaborate on a complex task. "
            "Provide a JSON plan with tasks (each has: task_id, description, role, depends_on, "
            "artifacts_in, artifacts_out). Tasks run in parallel within each dependency layer. "
            f"Available roles: {ROLE_HINT}. "
            "Use when: multi-step research, comparative analysis, review pipelines."
        )

    @property
    def read_only(self) -> bool:
        return False

    def set_event_emitter(self, emitter: "Callable[[str], Awaitable[None]]") -> None:
        self._event_emitter = emitter

    async def execute(self, plan: str, **kwargs: Any) -> str:
        from silver_research_bot.agent.team import TeamPlan

        try:
            plan_data = json.loads(plan)
            team_plan = TeamPlan(**plan_data)
        except (json.JSONDecodeError, ValueError) as e:
            return f"Error: invalid plan JSON — {e}"

        if not team_plan.tasks:
            return "Error: plan must contain at least one task."

        task_ids = [t.task_id for t in team_plan.tasks]
        logger.info("TeamTool executing plan with tasks: {}", task_ids)

        async def _on_progress(evt: dict) -> None:
            if self._event_emitter:
                await self._event_emitter(json.dumps(evt, ensure_ascii=False))

        try:
            run = await self._orchestrator.execute(
                team_plan, on_progress=_on_progress,
            )
            status_summary = {tid: s for tid, s in run.tasks_status.items()}
            done = sum(1 for s in status_summary.values() if s == "done")
            failed = sum(1 for s in status_summary.values() if s == "failed")
            return json.dumps({
                "run_id": run.run_id,
                "total": len(status_summary),
                "done": done,
                "failed": failed,
                "status": status_summary,
                "message": (
                    f"Team execution complete: {done}/{len(status_summary)} tasks succeeded"
                    + (f", {failed} failed" if failed else "")
                    + f". Run ID: {run.run_id}"
                ),
            }, ensure_ascii=False, indent=2)
        except ValueError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error executing team plan: {e}"
