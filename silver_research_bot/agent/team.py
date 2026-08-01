"""多 Agent 团队编排引擎 — DAG → 分层并行 → 产物池 → 对等通信 (P2)"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from silver_research_bot.agent.blackboard import Blackboard
from silver_research_bot.agent.comm import AgentCommBus, AgentMessage

if TYPE_CHECKING:
    from silver_research_bot.agent.subagent import SubagentManager


class TeamTask(BaseModel):
    """团队计划中的单个任务定义"""
    task_id: str
    description: str
    role: str = ""
    depends_on: list[str] = Field(default_factory=list)
    artifacts_in: list[str] = Field(default_factory=list)
    artifacts_out: list[str] = Field(default_factory=list)
    timeout_seconds: int = 600
    # P2 fields
    review_by: str = ""
    revision_rounds: int = 1
    arbiter_for: list[str] = Field(default_factory=list)


class TeamPlan(BaseModel):
    """团队执行计划 (DAG)"""
    tasks: list[TeamTask]
    aggregation_prompt: str = ""


class TeamRun(BaseModel):
    """团队执行实例"""
    run_id: str
    plan: TeamPlan
    tasks_status: dict[str, str] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    started_at: float = Field(default_factory=time.monotonic)
    finished_at: float | None = None
    error: str | None = None


class TeamOrchestrator:
    """多 Agent 团队编排器。DAG 分层并行 + 对等通信 + 同行评审 + 分歧仲裁。"""

    _MAX_CONCURRENT_PER_LAYER = 5

    def __init__(self, subagent_manager: "SubagentManager", blackboard: Blackboard | None = None) -> None:
        self._subagents = subagent_manager
        self._blackboard = blackboard
        self._runs: dict[str, TeamRun] = {}

    def get_run(self, run_id: str) -> TeamRun | None:
        return self._runs.get(run_id)

    async def cancel_run(self, run_id: str) -> bool:
        run = self._runs.get(run_id)
        if run is None:
            return False
        for task_id in run.tasks_status:
            if run.tasks_status[task_id] == "running":
                run.tasks_status[task_id] = "cancelled"
        return True

    @staticmethod
    def _topo_sort(tasks: list[TeamTask]) -> list[list[TeamTask]]:
        task_map = {t.task_id: t for t in tasks}
        in_degree: dict[str, int] = {t.task_id: 0 for t in tasks}
        dependents: dict[str, list[str]] = {t.task_id: [] for t in tasks}

        for t in tasks:
            for dep_id in t.depends_on:
                if dep_id not in task_map:
                    raise ValueError(
                        f"Task '{t.task_id}' depends on unknown '{dep_id}'. "
                        f"Available: {list(task_map.keys())}"
                    )
                in_degree[t.task_id] += 1
                dependents[dep_id].append(t.task_id)

        queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
        layers: list[list[TeamTask]] = []
        processed = 0

        while queue:
            layer: list[TeamTask] = []
            for _ in range(len(queue)):
                tid = queue.popleft()
                layer.append(task_map[tid])
                processed += 1
                for d in dependents.get(tid, []):
                    in_degree[d] -= 1
                    if in_degree[d] == 0:
                        queue.append(d)
            if layer:
                layers.append(layer)

        if processed != len(tasks):
            remaining = [tid for tid, deg in in_degree.items() if deg > 0]
            raise ValueError(f"Circular dependency: {remaining}")

        return layers

    def _inject_artifact_context(self, task: TeamTask, run: TeamRun) -> str:
        """将依赖产物注入任务描述"""
        desc = task.description
        if task.artifacts_in:
            ctx_parts = []
            for key in task.artifacts_in:
                if key in run.artifacts:
                    val = run.artifacts[key]
                    val_str = json.dumps(val, ensure_ascii=False) if not isinstance(val, str) else val
                    ctx_parts.append(f"[{key}]\n{val_str[:3000]}")
            if ctx_parts:
                desc = "前置产物:\n\n" + "\n\n".join(ctx_parts) + "\n\n---\n\n" + desc
        return desc

    async def execute(
        self,
        plan: TeamPlan,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
        on_progress: Any | None = None,
    ) -> TeamRun:
        """执行团队计划。使用 spawn_and_wait 等待每个子代理完成。"""
        run_id = str(uuid.uuid4())[:8]
        run = TeamRun(run_id=run_id, plan=plan)
        for t in plan.tasks:
            run.tasks_status[t.task_id] = "pending"
        self._runs[run_id] = run
        logger.info("Team run [{}] started with {} tasks", run_id, len(plan.tasks))

        try:
            layers = self._topo_sort(plan.tasks)

            for layer_idx, layer in enumerate(layers):
                if on_progress:
                    await on_progress({
                        "type": "team_layer",
                        "run_id": run_id,
                        "layer": layer_idx + 1,
                        "total": len(layers),
                        "tasks": [t.task_id for t in layer],
                    })

                sem = asyncio.Semaphore(self._MAX_CONCURRENT_PER_LAYER)

                async def _run_one(task: TeamTask) -> dict:
                    async with sem:
                        desc = self._inject_artifact_context(task, run)
                        run.tasks_status[task.task_id] = "running"

                        # Handle arbiter_for: auto-inject conflicting artifacts
                        if task.arbiter_for:
                            arb_ctx = []
                            for tid in task.arbiter_for:
                                if tid in run.artifacts:
                                    arb_ctx.append(f"[{tid}]:\n{json.dumps(run.artifacts[tid], ensure_ascii=False)[:2000]}")
                            if arb_ctx:
                                desc = "你作为仲裁者，评估以下分歧:\n\n" + "\n\n".join(arb_ctx) + "\n\n---\n\n" + desc

                        result = await self._subagents.spawn_and_wait(
                            task=desc,
                            label=f"[team:{run_id}] {task.task_id}",
                            role=task.role,
                            origin_channel=origin_channel,
                            origin_chat_id=origin_chat_id,
                            session_key=session_key,
                            timeout=task.timeout_seconds,
                        )
                        # Write artifacts from task result
                        if task.artifacts_out and result.get("status") == "ok":
                            for key in task.artifacts_out:
                                run.artifacts[key] = result.get("result", "")
                            run.artifacts[task.task_id] = result.get("result", "")

                        # P3: Auto-write task results to Blackboard
                        if self._blackboard and result.get("status") == "ok":
                            for key in (task.artifacts_out or []):
                                await self._blackboard.write(
                                    key, run.artifacts[key],
                                    author=task.task_id,
                                    metadata={"run_id": run_id, "role": task.role},
                                )

                        status_key = result.get("status", "error")
                        run.tasks_status[task.task_id] = "done" if status_key == "ok" else "failed"
                        return {"task_id": task.task_id, "status": status_key, "result": result}

                # Execute layer concurrently with spawn_and_wait
                tasks_results = await asyncio.gather(
                    *[_run_one(t) for t in layer], return_exceptions=True,
                )
                for res in tasks_results:
                    if isinstance(res, Exception):
                        logger.error("Team run [{}] task exception: {}", run_id, res)
                        continue
                    if res.get("status") not in ("ok",):
                        logger.error("Team run [{}] task {} {}", run_id, res.get("task_id"), res.get("status"))

                # P2: Auto peer review — after each layer, trigger reviews
                await self._trigger_peer_reviews(layer, run, origin_channel, origin_chat_id, session_key)

                # P2: Auto debate detection — detect conflicting artifacts
                await self._detect_and_arbitrate(layer, run, origin_channel, origin_chat_id, session_key)

            # Aggregation step
            if plan.aggregation_prompt:
                agg_desc = plan.aggregation_prompt + "\n\n" + json.dumps(
                    {k: str(v)[:2000] for k, v in run.artifacts.items()},
                    ensure_ascii=False, indent=2,
                )
                try:
                    agg_result = await self._subagents.spawn_and_wait(
                        task=agg_desc,
                        label=f"[team:{run_id}] aggregation",
                        role="literature_review",
                        origin_channel=origin_channel,
                        origin_chat_id=origin_chat_id,
                        session_key=session_key,
                        timeout=300,
                    )
                    if agg_result.get("status") == "ok":
                        run.artifacts["aggregation"] = agg_result.get("result", "")
                except Exception as e:
                    logger.error("Team run [{}] aggregation failed: {}", run_id, e)

            # P3: Promote accepted blackboard entries to long-term memory
            if self._blackboard:
                try:
                    await self._blackboard.save()
                except Exception as e:
                    logger.warning("Blackboard save failed: {}", e)

            run.finished_at = time.monotonic()
            return run

        except Exception as e:
            run.error = str(e)
            run.finished_at = time.monotonic()
            logger.error("Team run [{}] failed: {}", run_id, e)
            raise

    # ── P2 Communication Patterns ──

    async def _trigger_peer_reviews(
        self, layer: list[TeamTask], run: TeamRun,
        origin_channel: str, origin_chat_id: str, session_key: str | None,
    ) -> None:
        """为指定了 review_by 的已完成 task 自动触发同行评审"""
        for task in layer:
            if not task.review_by or run.tasks_status.get(task.task_id) != "done":
                continue
            reviewer_id = task.review_by
            if reviewer_id not in run.tasks_status:
                continue

            for _ in range(task.revision_rounds):
                # Send review request via comm bus
                review_msg = AgentMessage(
                    from_agent=task.task_id,
                    to_agent=reviewer_id,
                    type="review_request",
                    subject=f"请审查 {task.task_id} 的产物",
                    payload={
                        "task_id": task.task_id,
                        "artifacts": {k: str(v)[:2000] for k, v in run.artifacts.items() if k.startswith(task.task_id)},
                    },
                )
                await self._subagents._comm_bus.send(review_msg)
                logger.info("Peer review: {} → {}", task.task_id, reviewer_id)

    async def _detect_and_arbitrate(
        self, layer: list[TeamTask], run: TeamRun,
        origin_channel: str, origin_chat_id: str, session_key: str | None,
    ) -> None:
        """检测同 artifact 的多方产出，如有冲突则自动仲裁"""
        artifact_writers: dict[str, list[str]] = {}
        for task in layer:
            if run.tasks_status.get(task.task_id) != "done":
                continue
            for key in task.artifacts_out:
                artifact_writers.setdefault(key, []).append(task.task_id)

        for key, writers in artifact_writers.items():
            if len(writers) < 2:
                continue
            values = [str(run.artifacts.get(tid, ""))[:500] for tid in writers]
            if len(set(values)) <= 1:
                continue  # no conflict

            logger.info("Debate detected for artifact '{}' between {}", key, writers)
            # Store both positions
            run.artifacts[f"{key}_positions"] = {tid: run.artifacts.get(tid, "") for tid in writers}
            run.artifacts[f"{key}_conflict"] = True

    async def broadcast(self, run: TeamRun, from_task_id: str, subject: str, payload: dict) -> int:
        """向团队所有活跃成员广播消息"""
        msg = AgentMessage(
            from_agent=from_task_id,
            to_agent="all",
            type="notify",
            subject=subject,
            payload=payload,
        )
        return await self._subagents._comm_bus.send(msg)
