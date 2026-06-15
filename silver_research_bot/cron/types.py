"""描述和管理定时任务的数据结构"""

from dataclasses import dataclass, field
from typing import Literal


@dataclass
class CronSchedule:
    """
    Cron 任务的调度规则
    定义任务何时触发，支持三种方式：

    **"at"**：在某个绝对时间点执行，用at_ms` 存储毫秒级时间戳。

    **"every"**：以固定间隔执行，用every_ms` 存储间隔毫秒数。

    **"cron"**：标准 cron 表达式（如"0 9 * * *"表示每天 9:00），配合可选的tz时区字符串（如"Asia/Shanghai"`）。
    """
    kind: Literal["at", "every", "cron"]
    '定时任务类型：at（指定时刻）、every（固定间隔）、cron（cron表达式）'
    at_ms: int | None = None
    '指定时刻执行的时间戳（毫秒级，仅at类型使用）'
    every_ms: int | None = None
    '固定间隔执行的时间间隔（毫秒级，仅every类型使用）'
    expr: str | None = None
    'cron表达式（仅cron类型使用，例："0 9 * * *"）'
    tz: str | None = None
    'cron表达式对应的时区'


@dataclass
class CronPayload:
    """任务载荷——任务运行时该怎么做"""
    kind: Literal["system_event", "agent_turn"] = "agent_turn"
    '任务执行类型：system_event 或 agent_turn'
    message: str = ""
    '任务执行时携带的消息内容'
    deliver: bool = False
    '是否需要将执行结果推送到指定通道'
    channel: str | None = None
    '结果推送的通道（如 whatsapp、cli 等）'
    to: str | None = None
    '消息接收目标'


@dataclass
class CronRunRecord:
    """一个定时任务的单次执行记录."""
    run_at_ms: int
    '执行时间戳（毫秒）'
    status: Literal["ok", "error", "skipped"]
    '执行状态：成功/失败/跳过'
    duration_ms: int = 0
    '执行耗时（毫秒，默认0）'
    error: str | None = None
    '错误信息（执行失败时填写）'


@dataclass
class CronJobState:
    """任务的运行状态"""
    next_run_at_ms: int | None = None
    '下次执行的时间戳（毫秒），未设置则为 None'
    last_run_at_ms: int | None = None
    '最后一次执行的时间戳（毫秒），未执行过则为 None'
    last_status: Literal["ok", "error", "skipped"] | None = None
    '最后一次执行的状态：成功/失败/跳过，未执行过则为 None'
    last_error: str | None = None
    '最后一次执行的错误信息，无错误则为 None'
    run_history: list[CronRunRecord] = field(default_factory=list)
    '执行历史记录列表，存储所有 CronRunRecord 执行记录'


@dataclass
class CronJob:
    """一个定时任务"""
    id: str
    '定时任务唯一标识 ID'
    name: str
    '任务名称（用于展示/管理）'
    enabled: bool = True
    '是否启用该任务，默认启用（True）'
    schedule: CronSchedule = field(default_factory=lambda: CronSchedule(kind="every"))
    '任务调度规则（时间/间隔/cron），默认是循环执行类型'
    payload: CronPayload = field(default_factory=CronPayload)
    '任务执行时的载荷（要做什么、发什么消息）'
    state: CronJobState = field(default_factory=CronJobState)
    '任务运行状态（下次执行时间、历史记录等）'
    created_at_ms: int = 0
    '任务创建时间戳（毫秒）'
    updated_at_ms: int = 0
    '任务最后更新时间戳（毫秒）'
    delete_after_run: bool = False
    '是否执行一次后自动删除，默认不删除'

    @classmethod
    def from_dict(cls, kwargs: dict):
        """用于从字典重建 CronJob 对象"""
        state_kwargs = dict(kwargs.get("state", {}))
        state_kwargs["run_history"] = [
            record if isinstance(record, CronRunRecord) else CronRunRecord(**record)
            for record in state_kwargs.get("run_history", [])
        ]
        kwargs["schedule"] = CronSchedule(**kwargs.get("schedule", {"kind": "every"}))
        kwargs["payload"] = CronPayload(**kwargs.get("payload", {}))
        kwargs["state"] = CronJobState(**state_kwargs)
        return cls(**kwargs)


@dataclass
class CronStore:
    """定时任务的持久化存储"""
    version: int = 1
    '存储格式版本，用于兼容性'
    jobs: list[CronJob] = field(default_factory=list)
    '定时任务列表'
