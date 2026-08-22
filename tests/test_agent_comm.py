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


def test_spawn_and_wait_does_not_announce(tmp_path):
    async def go():
        from silver_research_bot.agent.runner import AgentRunResult

        mgr = _make_manager(tmp_path)
        # 让子代理立即返回固定结果，避免真实 LLM
        async def fake_run(spec):
            return AgentRunResult(
                final_content="hello result",
                messages=[{"role": "assistant", "content": "hello result"}],
                stop_reason="completed",
            )
        mgr.runner.run = fake_run  # type: ignore[method-assign]
        published = []
        async def record(msg):
            published.append(msg)
        mgr.bus.publish_inbound = record  # type: ignore[method-assign]

        res = await mgr.spawn_and_wait("do", label="t2", session_key="s2", timeout=10)
        assert res["status"] == "ok"
        assert res["result"] == "hello result"
        assert published == []  # 不再走 MessageBus 注入
    asyncio.run(go())


def test_comm_bus_full_queue_returns_zero():
    from silver_research_bot.agent.comm import AgentCommBus, AgentMessage

    async def go():
        bus = AgentCommBus()
        bus.register("a")
        q = bus.get_queue("a")
        for i in range(20):  # 塞满 maxsize=20 的队列
            await q.put(AgentMessage(from_agent="x", to_agent="a", subject=str(i)))
        sent = await bus.send(AgentMessage(from_agent="b", to_agent="a", subject="overflow"), timeout=0.05)
        assert sent == 0
    asyncio.run(go())


def test_comm_bus_message_log_bounded():
    from silver_research_bot.agent.comm import AgentCommBus, AgentMessage

    async def go():
        bus = AgentCommBus(max_log=3)
        bus.register("a")
        for i in range(5):
            await bus.send(AgentMessage(from_agent="b", to_agent="a", subject=str(i)))
        assert len(bus._message_log) == 3
    asyncio.run(go())
