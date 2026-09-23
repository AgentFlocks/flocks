"""Model timing must survive reload and never change execution semantics."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.session.message import Message, PartTime, StepStartPart
from flocks.session.streaming.model_activity import ModelActivity
from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.session.streaming.stream_processor import StreamProcessor


@pytest.fixture
def recording(monkeypatch):
    clock = SimpleNamespace(now=1.0)
    monkeypatch.setattr("flocks.session.streaming.model_activity.time.time", lambda: clock.now)
    store = AsyncMock()
    monkeypatch.setattr(Message, "store_part", store)
    publish = AsyncMock()
    activity = ModelActivity("session-timing", "message-timing", publish)
    return activity, clock, store, publish


async def test_boundaries_survive_serialization_and_do_not_mutate_open_event(recording):
    activity, clock, store, publish = recording
    await activity.start()
    await activity.start()  # No repeated writes.
    opened = store.await_args_list[0].args[2]
    assert opened.time.end is None
    clock.now = 11
    await activity.stop()
    await activity.stop()
    assert store.await_count == publish.await_count == 2
    closed = store.await_args_list[-1].args[2]
    reloaded = Message.deserialize_part(closed.model_dump(mode="json"))
    assert reloaded.time == PartTime(start=1000, end=11000)
    assert opened.time.end is None
    assert publish.await_args_list[0].args[1]["part"]["time"] == {"start": 1000}
    assert publish.await_args_list[-1].args[1]["part"]["id"] == opened.id
    legacy = StepStartPart(sessionID="s", messageID="m")
    assert Message.deserialize_part(legacy.model_dump()).time is None


async def test_pending_generation_is_timed_and_sync_tool_wait_is_excluded(recording, monkeypatch):
    activity, clock, store, publish = recording
    proc = StreamProcessor(
        session_id="session-timing", assistant_message=SimpleNamespace(id="message-timing"),
        agent=SimpleNamespace(name="rex"), model_activity=activity,
    )
    await activity.start()
    clock.now = 3
    await proc.process_event(ToolInputStartEvent(id="call", tool_name="read"))
    assert activity.current is not None  # Parameter generation is still model work.
    clock.now = 8

    async def tool(_event):
        assert activity.current is None  # Includes permission wait inside the tool.
        clock.now = 30

    monkeypatch.setattr(proc, "_handle_tool_call", tool)
    await proc.process_event(ToolCallEvent(tool_call_id="call", tool_name="read", input={}))
    assert activity.current.time.start == 30000
    clock.now = 32
    await activity.stop()
    intervals = [c.args[2].time for c in store.await_args_list if isinstance(c.args[2], StepStartPart) and c.args[2].time.end is not None]
    assert intervals == [PartTime(start=1000, end=8000), PartTime(start=30000, end=32000)]
    assert publish.await_count == 4


@pytest.mark.parametrize("error", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_failure_during_tool_does_not_resume_model_clock(recording, error):
    activity, clock, store, _ = recording
    await activity.start()
    clock.now = 5
    with pytest.raises(type(error)):
        async with activity.suspend():
            raise error
    assert activity.current is None
    assert store.await_args_list[-1].args[2].time.end == 5000


async def test_parallel_tool_does_not_pause_model_stream(recording, monkeypatch):
    activity, _, _, _ = recording
    proc = StreamProcessor(
        session_id="session-timing", assistant_message=SimpleNamespace(id="message-timing"),
        agent=SimpleNamespace(name="rex"), model_activity=activity,
    )
    execute = AsyncMock()
    monkeypatch.setattr(proc, "_handle_tool_call", execute)
    await activity.start()
    marker = activity.current
    await proc.process_event(ToolCallEvent(tool_call_id="call", tool_name="task", input={"subagent_type": "explore"}))
    await proc.drain_parallel_tool_calls()
    execute.assert_awaited_once()
    assert activity.current is marker
    await activity.stop()


async def test_observability_failure_does_not_break_execution(recording):
    activity, _, store, publish = recording
    store.side_effect = RuntimeError("storage offline")
    publish.side_effect = RuntimeError("SSE offline")
    await activity.start()
    async with activity.suspend():
        pass
    await activity.stop()
    assert activity.current is None


async def test_timing_markers_do_not_bypass_doom_loop_detection(recording, monkeypatch):
    from flocks.session.message import ToolPart, ToolStateRunning
    from flocks.tool.registry import ToolRegistry
    from flocks.hooks.pipeline import HookPipeline

    activity, _, _, _ = recording
    proc = StreamProcessor(
        session_id="session-timing", assistant_message=SimpleNamespace(id="message-timing"),
        agent=SimpleNamespace(name="rex"), model_activity=activity,
        allowed_tool_names=["read"],
    )
    parts = []
    for index in range(3):
        parts.extend([
            StepStartPart(sessionID="session-timing", messageID="message-timing", time=PartTime(start=index * 1000, end=index * 1000 + 100)),
            ToolPart(sessionID="session-timing", messageID="message-timing", callID=str(index), tool="read",
                     state=ToolStateRunning(input={"filePath": "same"}, time={"start": 1000})),
        ])
    monkeypatch.setattr(Message, "parts", AsyncMock(return_value=parts))
    monkeypatch.setattr(HookPipeline, "run_tool_before", AsyncMock(return_value=None))
    execute = AsyncMock()
    monkeypatch.setattr(ToolRegistry, "execute", execute)
    await proc.process_event(ToolCallEvent(tool_call_id="2", tool_name="read", input={"filePath": "same"}))
    assert proc._stop_tool_processing is True
    execute.assert_not_awaited()


async def test_cancelled_close_can_be_finalized_by_runner(recording):
    activity, clock, store, _ = recording
    await activity.start()
    clock.now = 4
    store.side_effect = [asyncio.CancelledError(), None]
    with pytest.raises(asyncio.CancelledError):
        await activity.stop()
    assert activity.current is not None
    await activity.stop()
    assert activity.current is None
    assert store.await_args_list[-1].args[2].time.end == 4000


async def test_open_running_tool_retains_its_interval_after_storage_reload():
    from flocks.session.message import MessageRole, ToolPart, ToolStateRunning

    session_id = "timing-reload"
    message = await Message.create(session_id, MessageRole.ASSISTANT, "", agent="rex")
    tool = ToolPart(sessionID=session_id, messageID=message.id, callID="call", tool="read",
                    state=ToolStateRunning(input={}, time={"start": 1000}))
    await Message.store_part(session_id, message.id, tool)
    # Exercise the same deserializer used by storage/HTTP imports.
    reloaded = Message.deserialize_part(tool.model_dump(mode="json"))
    assert reloaded.state.time == {"start": 1000}
    legacy = Message.deserialize_part({**tool.model_dump(mode="json"), "state": {"status": "running", "input": {}}})
    assert legacy.state.time == {"start": 0, "end": 0}
