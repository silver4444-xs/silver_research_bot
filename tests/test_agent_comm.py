import asyncio
from pathlib import Path

from silver_research_bot.agent.subagent import SpawnHandle, SubagentManager
from silver_research_bot.bus.queue import MessageBus


class _FakeProvider:
    def get_default_model(self) -> str:
        return "test-model"


def _make_manager(tmp_path: Path) -> SubagentManager:
    return SubagentManager(
        provider=_FakeProvider(),           # type: ignore[arg-type]
        workspace=tmp_path,
        bus=MessageBus(),
        max_tool_result_chars=4000,
    )


def test_spawn_returns_spawn_handle(tmp_path):
    async def go():
        mgr = _make_manager(tmp_path)
        handle = await mgr.spawn("do a thing", label="t1", session_key="s1")
        assert isinstance(handle, SpawnHandle)
        assert handle.task_id and handle.label == "t1"
        assert "t1" in str(handle) and handle.task_id in str(handle)
        assert handle.status is not None
        # 后台任务已创建但尚未完成，清理取消
        await mgr.cancel_by_session("s1")
    asyncio.run(go())


def test_spawn_invalid_role_returns_error_string(tmp_path):
    async def go():
        mgr = _make_manager(tmp_path)
        result = await mgr.spawn("x", role="no_such_role")
        assert isinstance(result, str) and result.startswith("Error:")
    asyncio.run(go())
