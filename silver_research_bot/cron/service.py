"""Cron service for scheduling agent tasks."""

import asyncio
import json
import time
import uuid
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Coroutine, Literal

from filelock import FileLock
from loguru import logger

from silver_research_bot.cron.types import CronJob, CronJobState, CronPayload, CronRunRecord, CronSchedule, CronStore


def _now_ms() -> int:
    """获取当前时间的毫秒级时间戳"""
    return int(time.time() * 1000)


def _compute_next_run(schedule: CronSchedule, now_ms: int) -> int | None:
    """根据调度定义和当前参考时间，计算出该任务下一次应该执行的时间戳（毫秒）"""
    if schedule.kind == "at":
        return schedule.at_ms if schedule.at_ms and schedule.at_ms > now_ms else None

    if schedule.kind == "every":
        if not schedule.every_ms or schedule.every_ms <= 0:
            return None
        # Next interval from now
        return now_ms + schedule.every_ms

    if schedule.kind == "cron" and schedule.expr:
        try:
            from zoneinfo import ZoneInfo

            from croniter import croniter
            # Use caller-provided reference time for deterministic scheduling
            base_time = now_ms / 1000
            tz = ZoneInfo(schedule.tz) if schedule.tz else datetime.now().astimezone().tzinfo
            base_dt = datetime.fromtimestamp(base_time, tz=tz)
            cron = croniter(schedule.expr, base_dt)
            next_dt = cron.get_next(datetime)
            return int(next_dt.timestamp() * 1000)
        except Exception:
            return None

    return None


def _validate_schedule_for_add(schedule: CronSchedule) -> None:
    """对那些否则会导致任务无法运行的计划字段进行验证。"""
    if schedule.tz and schedule.kind != "cron":
        raise ValueError("tz can only be used with cron schedules")

    if schedule.kind == "cron" and schedule.tz:
        try:
            from zoneinfo import ZoneInfo

            ZoneInfo(schedule.tz)
        except Exception:
            raise ValueError(f"unknown timezone '{schedule.tz}'") from None


class CronService:
    """用于管理和执行计划任务的服务。"""

    _MAX_RUN_HISTORY = 20
    '最大保留的任务执行历史记录条数'

    def __init__(
        self,
        store_path: Path,
        on_job: Callable[[CronJob], Coroutine[Any, Any, str | None]] | None = None,
        max_sleep_ms: int = 300_000,  # 5 minutes
    ):
        self.store_path = store_path
        '定时任务持久化存储的文件路径'
        self._action_path = store_path.parent / "action.jsonl"
        '任务执行动作日志的存储文件路径'
        self._lock = FileLock(str(self._action_path.parent) + ".lock")
        '文件锁，防止多进程同时读写导致数据冲突'
        self.on_job = on_job
        '任务触发时的回调函数（异步），接收 CronJob 并返回执行结果'
        self._store: CronStore | None = None
        '定时任务存储管理器实例'
        self._timer_task: asyncio.Task | None = None
        '调度定时器的异步任务对象'
        self._running = False
        '服务是否正在运行的标记'
        self._timer_active = False
        '调度计时器是否处于激活状态的标记'
        self.max_sleep_ms = max_sleep_ms
        '最大休眠时间（默认5分钟），防止长时间无任务时挂起过久'

    def _load_jobs(self) -> tuple[list[CronJob], int]:
        """从主存储文件（self.store_path）读取原始 JSON 数据，并将其解析为 CronJob 对象列表"""
        jobs = []
        version = 1
        if self.store_path.exists():
            try:
                data = json.loads(self.store_path.read_text(encoding="utf-8"))
                jobs = []
                version = data.get("version", 1)
                for j in data.get("jobs", []):
                    jobs.append(CronJob(
                        id=j["id"],
                        name=j["name"],
                        enabled=j.get("enabled", True),
                        schedule=CronSchedule(
                            kind=j["schedule"]["kind"],
                            at_ms=j["schedule"].get("atMs"),
                            every_ms=j["schedule"].get("everyMs"),
                            expr=j["schedule"].get("expr"),
                            tz=j["schedule"].get("tz"),
                        ),
                        payload=CronPayload(
                            kind=j["payload"].get("kind", "agent_turn"),
                            message=j["payload"].get("message", ""),
                            deliver=j["payload"].get("deliver", False),
                            channel=j["payload"].get("channel"),
                            to=j["payload"].get("to"),
                        ),
                        state=CronJobState(
                            next_run_at_ms=j.get("state", {}).get("nextRunAtMs"),
                            last_run_at_ms=j.get("state", {}).get("lastRunAtMs"),
                            last_status=j.get("state", {}).get("lastStatus"),
                            last_error=j.get("state", {}).get("lastError"),
                            run_history=[
                                CronRunRecord(
                                    run_at_ms=r["runAtMs"],
                                    status=r["status"],
                                    duration_ms=r.get("durationMs", 0),
                                    error=r.get("error"),
                                )
                                for r in j.get("state", {}).get("runHistory", [])
                            ],
                        ),
                        created_at_ms=j.get("createdAtMs", 0),
                        updated_at_ms=j.get("updatedAtMs", 0),
                        delete_after_run=j.get("deleteAfterRun", False),
                    ))
            except Exception as e:
                logger.warning("Failed to load cron store: {}", e)
        return jobs, version

    def _merge_action(self):
        """
        读取 action.jsonl 操作日志文件，按顺序回放其中的变更（添加、更新、删除），
        并将结果合并到当前 _store.jobs 中。如果服务正在运行且发生了变更，则清空 action 文件并保存主存储
        """
        if not self._action_path.exists():
            return

        jobs_map = {j.id: j for j in self._store.jobs}
        def _update(params: dict):
            j = CronJob.from_dict(params)
            jobs_map[j.id] = j

        def _del(params: dict):
            if job_id := params.get("job_id"):
                jobs_map.pop(job_id)

        with self._lock:
            with open(self._action_path, "r", encoding="utf-8") as f:
                changed = False
                for line in f:
                    try:
                        line = line.strip()
                        action = json.loads(line)
                        if "action" not in action:
                            continue
                        if action["action"] == "del":
                            _del(action.get("params", {}))
                        else:
                            _update(action.get("params", {}))
                        changed = True
                    except Exception as exp:
                        logger.debug(f"load action line error: {exp}")
                        continue
            self._store.jobs = list(jobs_map.values())
            if self._running and changed:
                self._action_path.write_text("", encoding="utf-8")
                self._save_store()
        return

    def _load_store(self) -> CronStore:
        """从磁盘加载任务。如果文件在外部被修改，则自动重新加载。
        - 每次都重新加载，因为需要合并来自其他实例的任务对象上的操作。
        - 在执行 _on_timer 期间，返回现有的存储，以防止并发
          _load_store 调用（例如来自 list_jobs 轮询的调用）在执行过程中将其替换。
        """
        if self._timer_active and self._store:
            return self._store
        jobs, version = self._load_jobs()
        self._store = CronStore(version=version, jobs=jobs)
        self._merge_action()

        return self._store

    def _save_store(self) -> None:
        """将任务保存到磁盘"""
        if not self._store:
            return

        self.store_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "version": self._store.version,
            "jobs": [
                {
                    "id": j.id,
                    "name": j.name,
                    "enabled": j.enabled,
                    "schedule": {
                        "kind": j.schedule.kind,
                        "atMs": j.schedule.at_ms,
                        "everyMs": j.schedule.every_ms,
                        "expr": j.schedule.expr,
                        "tz": j.schedule.tz,
                    },
                    "payload": {
                        "kind": j.payload.kind,
                        "message": j.payload.message,
                        "deliver": j.payload.deliver,
                        "channel": j.payload.channel,
                        "to": j.payload.to,
                    },
                    "state": {
                        "nextRunAtMs": j.state.next_run_at_ms,
                        "lastRunAtMs": j.state.last_run_at_ms,
                        "lastStatus": j.state.last_status,
                        "lastError": j.state.last_error,
                        "runHistory": [
                            {
                                "runAtMs": r.run_at_ms,
                                "status": r.status,
                                "durationMs": r.duration_ms,
                                "error": r.error,
                            }
                            for r in j.state.run_history
                        ],
                    },
                    "createdAtMs": j.created_at_ms,
                    "updatedAtMs": j.updated_at_ms,
                    "deleteAfterRun": j.delete_after_run,
                }
                for j in self._store.jobs
            ]
        }

        self.store_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    async def start(self) -> None:
        """启动 cron 服务"""
        self._running = True
        self._load_store()
        self._recompute_next_runs()
        self._save_store()
        self._arm_timer()
        logger.info("Cron service started with {} jobs", len(self._store.jobs if self._store else []))

    def stop(self) -> None:
        """停止 cron 服务."""
        self._running = False
        if self._timer_task:
            self._timer_task.cancel()
            self._timer_task = None

    def _recompute_next_runs(self) -> None:
        """为所有已启用的任务重新计算下次执行时间"""
        if not self._store:
            return
        now = _now_ms()
        for job in self._store.jobs:
            if job.enabled:
                job.state.next_run_at_ms = _compute_next_run(job.schedule, now)

    def _get_next_wake_ms(self) -> int | None:
        """获取所有任务中最早的下次执行时间戳"""
        if not self._store:
            return None
        times = [j.state.next_run_at_ms for j in self._store.jobs
                 if j.enabled and j.state.next_run_at_ms]
        return min(times) if times else None

    def _arm_timer(self) -> None:
        """安排下一次定时器唤醒"""
        if self._timer_task:
            self._timer_task.cancel()

        if not self._running:
            return

        next_wake = self._get_next_wake_ms()
        if next_wake is None:
            delay_ms = self.max_sleep_ms
        else:
            delay_ms = min(self.max_sleep_ms, max(0, next_wake - _now_ms()))
        delay_s = delay_ms / 1000

        async def tick():
            await asyncio.sleep(delay_s)
            if self._running:
                await self._on_timer()

        self._timer_task = asyncio.create_task(tick())

    async def _on_timer(self) -> None:
        """定时器唤醒后执行的实际逻辑：找出所有到期的任务并按顺序执行"""
        self._load_store()
        if not self._store:
            self._arm_timer()
            return

        self._timer_active = True
        try:
            now = _now_ms()
            due_jobs = [
                j for j in self._store.jobs
                if j.enabled and j.state.next_run_at_ms and now >= j.state.next_run_at_ms
            ]

            for job in due_jobs:
                await self._execute_job(job)

            self._save_store()
        finally:
            self._timer_active = False
        self._arm_timer()

    async def _execute_job(self, job: CronJob) -> None:
        """执行单个计划任务，记录执行结果、更新任务状态、处理一次性任务，并计算下次运行时间"""
        start_ms = _now_ms()
        logger.info("Cron: executing job '{}' ({})", job.name, job.id)

        try:
            if self.on_job:
                await self.on_job(job)
            job.state.last_status = "ok"
            job.state.last_error = None
            logger.info("Cron: job '{}' completed", job.name)
        except Exception as e:
            job.state.last_status = "error"
            job.state.last_error = str(e)
            logger.error("Cron: job '{}' failed: {}", job.name, e)

        end_ms = _now_ms()
        job.state.last_run_at_ms = start_ms
        job.updated_at_ms = end_ms

        # 记录运行历史
        job.state.run_history.append(CronRunRecord(
            run_at_ms=start_ms,
            status=job.state.last_status,
            duration_ms=end_ms - start_ms,
            error=job.state.last_error,
        ))
        job.state.run_history = job.state.run_history[-self._MAX_RUN_HISTORY:]

        # 处理一次性任务 (at)
        if job.schedule.kind == "at":
            if job.delete_after_run:
                self._store.jobs = [j for j in self._store.jobs if j.id != job.id]
            else:
                job.enabled = False
                job.state.next_run_at_ms = None
        else:
            # 重复任务（every 或 cron）重新计算下次执行时间
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

    def _append_action(self, action: Literal["add", "del", "update"], params: dict):
        """将任务变更操作（添加、更新、删除）写入 action.jsonl 日志文件，用于多实例之间的状态同步"""
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(self._action_path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"action": action, "params": params}, ensure_ascii=False) + "\n")


    # ========== Public API ==========

    def list_jobs(self, include_disabled: bool = False) -> list[CronJob]:
        """返回当前所有任务的列表"""
        store = self._load_store()
        jobs = store.jobs if include_disabled else [j for j in store.jobs if j.enabled]
        return sorted(jobs, key=lambda j: j.state.next_run_at_ms or float('inf'))

    def add_job(
        self,
        name: str,
        schedule: CronSchedule,
        message: str,
        deliver: bool = False,
        channel: str | None = None,
        to: str | None = None,
        delete_after_run: bool = False,
    ) -> CronJob:
        """添加一个新的普通任务"""
        _validate_schedule_for_add(schedule)
        now = _now_ms()

        job = CronJob(
            id=str(uuid.uuid4())[:8],
            name=name,
            enabled=True,
            schedule=schedule,
            payload=CronPayload(
                kind="agent_turn",
                message=message,
                deliver=deliver,
                channel=channel,
                to=to,
            ),
            state=CronJobState(next_run_at_ms=_compute_next_run(schedule, now)),
            created_at_ms=now,
            updated_at_ms=now,
            delete_after_run=delete_after_run,
        )
        if self._running:
            store = self._load_store()
            store.jobs.append(job)
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("add", asdict(job))

        logger.info("Cron: added job '{}' ({})", name, job.id)
        return job

    def register_system_job(self, job: CronJob) -> CronJob:
        """注册一个内部系统任务（payload.kind = "system_event"），该任务受保护，不能被删除或更新"""
        store = self._load_store()
        now = _now_ms()
        job.state = CronJobState(next_run_at_ms=_compute_next_run(job.schedule, now))
        job.created_at_ms = now
        job.updated_at_ms = now
        store.jobs = [j for j in store.jobs if j.id != job.id]
        store.jobs.append(job)
        self._save_store()
        self._arm_timer()
        logger.info("Cron: registered system job '{}' ({})", job.name, job.id)
        return job

    def remove_job(self, job_id: str) -> Literal["removed", "protected", "not_found"]:
        """根据 ID 删除一个普通任务。系统任务会被保护而拒绝删除."""
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            logger.info("Cron: refused to remove protected system job {}", job_id)
            return "protected"

        before = len(store.jobs)
        store.jobs = [j for j in store.jobs if j.id != job_id]
        removed = len(store.jobs) < before

        if removed:
            if self._running:
                self._save_store()
                self._arm_timer()
            else:
                self._append_action("del", {"job_id": job_id})
            logger.info("Cron: removed job {}", job_id)
            return "removed"

        return "not_found"

    def enable_job(self, job_id: str, enabled: bool = True) -> CronJob | None:
        """启用或禁用一个任务"""
        store = self._load_store()
        for job in store.jobs:
            if job.id == job_id:
                job.enabled = enabled
                job.updated_at_ms = _now_ms()
                if enabled:
                    job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())
                else:
                    job.state.next_run_at_ms = None
                if self._running:
                    self._save_store()
                    self._arm_timer()
                else:
                    self._append_action("update", asdict(job))
                return job
        return None

    def update_job(
        self,
        job_id: str,
        *,
        name: str | None = None,
        schedule: CronSchedule | None = None,
        message: str | None = None,
        deliver: bool | None = None,
        channel: str | None = ...,
        to: str | None = ...,
        delete_after_run: bool | None = None,
    ) -> CronJob | Literal["not_found", "protected"]:
        """更新现有作业的可变字段。系统作业无法被更新。

        对于 ``channel`` 和 ``to``，请传入明确的值（包括 ``None``）
        以进行更新；若省略（使用占位符 ``...``），则保持不变。
        """
        store = self._load_store()
        job = next((j for j in store.jobs if j.id == job_id), None)
        if job is None:
            return "not_found"
        if job.payload.kind == "system_event":
            return "protected"

        if schedule is not None:
            _validate_schedule_for_add(schedule)
            job.schedule = schedule
        if name is not None:
            job.name = name
        if message is not None:
            job.payload.message = message
        if deliver is not None:
            job.payload.deliver = deliver
        if channel is not ...:
            job.payload.channel = channel
        if to is not ...:
            job.payload.to = to
        if delete_after_run is not None:
            job.delete_after_run = delete_after_run

        job.updated_at_ms = _now_ms()
        if job.enabled:
            job.state.next_run_at_ms = _compute_next_run(job.schedule, _now_ms())

        if self._running:
            self._save_store()
            self._arm_timer()
        else:
            self._append_action("update", asdict(job))

        logger.info("Cron: updated job '{}' ({})", job.name, job.id)
        return job

    async def run_job(self, job_id: str, force: bool = False) -> bool:
        """立即手动执行一个任务，不影响正常的调度循环"""
        was_running = self._running
        self._running = True
        try:
            store = self._load_store()
            for job in store.jobs:
                if job.id == job_id:
                    if not force and not job.enabled:
                        return False
                    await self._execute_job(job)
                    self._save_store()
                    return True
            return False
        finally:
            self._running = was_running
            if was_running:
                self._arm_timer()

    def get_job(self, job_id: str) -> CronJob | None:
        """根据 ID 获取单个任务"""
        store = self._load_store()
        return next((j for j in store.jobs if j.id == job_id), None)

    def status(self) -> dict:
        """返回服务的状态摘要."""
        store = self._load_store()
        return {
            "enabled": self._running,
            "jobs": len(store.jobs),
            "next_wake_at_ms": self._get_next_wake_ms(),
        }
