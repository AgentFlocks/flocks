import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.hooks.pipeline import HookPipeline
from flocks.tool.registry import ToolResult


@pytest.mark.asyncio
@pytest.mark.parametrize("execute_deferred", [True, False])
async def test_foreground_subagent_tool_calls_start_in_parallel(execute_deferred):
    processor = StreamProcessor(
        session_id="ses-parent",
        assistant_message=SimpleNamespace(id="msg-assistant"),
        agent=SimpleNamespace(name="rex"),
    )
    release = asyncio.Event()
    both_started = asyncio.Event()
    started: list[str] = []

    async def fake_execute(tool_name, ctx, **_kwargs):
        started.append(ctx.call_id)
        if len(started) == 2:
            both_started.set()
        await release.wait()
        return ToolResult(success=True, output=f"{tool_name} done")

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", AsyncMock(return_value=[])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", fake_execute),
        patch.object(
            processor,
            "_resolve_sandbox_meta",
            AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
        ),
    ):
        await processor.process_event(ToolCallEvent(
            tool_call_id="call-1",
            tool_name="delegate_task",
            input={
                "description": "inspect one",
                "prompt": "Inspect one",
                "subagent_type": "explore",
            },
        ))
        await processor.process_event(ToolCallEvent(
            tool_call_id="call-2",
            tool_name="delegate_task",
            input={
                "description": "inspect two",
                "prompt": "Inspect two",
                "subagent_type": "explore",
            },
        ))

        await asyncio.wait_for(both_started.wait(), timeout=0.5)
        assert set(started) == {"call-1", "call-2"}

        release.set()
        await processor.drain_parallel_tool_calls(execute_deferred=execute_deferred)

    assert processor.tool_calls["call-1"].status == "completed"
    assert processor.tool_calls["call-2"].status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("input_started", [True, False])
async def test_cleanup_discards_deferred_sleep_and_finalizes_state(input_started):
    publish = AsyncMock()
    processor = StreamProcessor(
        session_id="ses-interrupted-sleep",
        assistant_message=SimpleNamespace(id="msg-assistant"),
        agent=SimpleNamespace(name="rex"),
        event_publish_callback=publish,
    )
    execute = AsyncMock(return_value=ToolResult(success=True))
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()) as store,
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute),
    ):
        if input_started:
            await processor.process_event(ToolInputStartEvent(id="call-sleep", tool_name="sleep"))
        await processor.process_event(ToolCallEvent(
            tool_call_id="call-sleep", tool_name="sleep", input={"timeout_s": 3600},
        ))
        await processor.drain_parallel_tool_calls(execute_deferred=False)
        assert processor._sleep_calls == []
        await processor.drain_parallel_tool_calls()

    execute.assert_not_awaited()
    state = processor.tool_calls["call-sleep"]
    assert state.status == "error"
    assert state.input == {"timeout_s": 3600}
    assert state.metadata["interrupted"] is True
    assert store.await_args.args[2].state.status == "error"
    assert store.await_args.args[2].state.metadata["interrupted"] is True
    assert publish.await_args.args[1]["part"]["state"]["metadata"]["interrupted"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("other_tool", [None, "read", "delegate_task"])
async def test_sleep_runs_after_stream_and_other_tools(other_tool):
    from flocks.tool.agent.communication import sleep
    processor = StreamProcessor(
        session_id="sleep-session", assistant_message=SimpleNamespace(id="msg"),
        agent=SimpleNamespace(name="rex"),
    )
    results = []
    order = []

    async def execute(tool_name, ctx, **kwargs):
        if tool_name != "sleep":
            await asyncio.sleep(0)
        order.append(tool_name)
        result = await sleep(ctx, .001) if tool_name == "sleep" else ToolResult(success=True)
        results.append(result)
        return result

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", AsyncMock(return_value=[])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute),
        patch.object(processor, "_resolve_sandbox_meta", AsyncMock(return_value={"blocked": False, "error": None, "extra": {}})),
    ):
        await processor.process_event(ToolCallEvent(tool_call_id="sleep", tool_name="sleep", input={}))
        assert not results
        if other_tool:
            await processor.process_event(ToolCallEvent(tool_call_id="other", tool_name=other_tool, input={}))
        await processor.drain_parallel_tool_calls()
    assert results[-1].success
    assert order == ([other_tool, "sleep"] if other_tool else ["sleep"])


@pytest.mark.asyncio
async def test_cancelling_first_deferred_sleep_finalizes_all_pending_siblings():
    published = AsyncMock()
    processor = StreamProcessor(
        session_id="ses-two-sleeps", assistant_message=SimpleNamespace(id="msg-two-sleeps"),
        agent=SimpleNamespace(name="rex"), event_publish_callback=published,
    )
    started = asyncio.Event()
    executed = []

    async def execute(tool_name, ctx, **_kwargs):
        executed.append(ctx.call_id)
        started.set()
        await asyncio.Event().wait()

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()) as store,
        patch("flocks.session.streaming.stream_processor.Message.parts", AsyncMock(return_value=[])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute),
        patch.object(HookPipeline, "run_tool_before", AsyncMock(return_value=None)),
        patch.object(processor, "_resolve_sandbox_meta", AsyncMock(return_value={"blocked": False, "error": None, "extra": {}})),
    ):
        for index in (1, 2):
            await processor.process_event(ToolInputStartEvent(id=f"sleep-{index}", tool_name="sleep"))
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"sleep-{index}", tool_name="sleep", input={"timeout_s": index},
            ))

        drain = asyncio.create_task(processor.drain_parallel_tool_calls())
        await asyncio.wait_for(started.wait(), timeout=1)
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        await processor.drain_parallel_tool_calls()
        # An already-terminal tool call must not be re-enqueued on duplicate events.
        await processor.process_event(ToolCallEvent(
            tool_call_id="sleep-2", tool_name="sleep", input={"timeout_s": 2},
        ))
        await processor.drain_parallel_tool_calls()

    assert executed == ["sleep-1"]
    assert processor._sleep_calls == []
    assert processor._draining_sleep is False
    for index in (1, 2):
        call_id = f"sleep-{index}"
        persisted = [
            entry.args[2] for entry in store.await_args_list
            if entry.args[2].callID == call_id
        ]
        terminal = [part for part in persisted if part.state.status == "error"]
        assert len(terminal) == 1
        assert terminal[0].id == persisted[0].id
        assert terminal[0].state.input == {"timeout_s": index}
        assert terminal[0].state.metadata["interrupted"] is True
        published_terminal = [
            entry.args[1]["part"] for entry in published.await_args_list
            if entry.args[1].get("part", {}).get("callID") == call_id
            and entry.args[1]["part"]["state"]["status"] == "error"
        ]
        assert len(published_terminal) == 1
        assert published_terminal[0]["id"] == persisted[0].id
        assert published_terminal[0]["state"]["input"] == {"timeout_s": index}


@pytest.mark.asyncio
async def test_cancellation_while_publishing_first_running_sleep_finalizes_both():
    started = asyncio.Event()
    published = []

    async def publish(_event, payload):
        part = payload["part"]
        if part["callID"] == "sleep-1" and part["state"]["status"] == "running":
            started.set()
            await asyncio.Event().wait()
        published.append(part)

    processor = StreamProcessor(
        session_id="ses-running-publish", assistant_message=SimpleNamespace(id="msg-running-publish"),
        agent=SimpleNamespace(name="rex"), event_publish_callback=publish,
    )
    execute = AsyncMock(return_value=ToolResult(success=True))
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()) as store,
        patch.object(HookPipeline, "run_tool_before", AsyncMock(return_value=None)),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute),
    ):
        for index in (1, 2):
            await processor.process_event(ToolInputStartEvent(id=f"sleep-{index}", tool_name="sleep"))
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"sleep-{index}", tool_name="sleep", input={"timeout_s": index},
            ))
        drain = asyncio.create_task(processor.drain_parallel_tool_calls())
        await asyncio.wait_for(started.wait(), timeout=1)
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain

    execute.assert_not_awaited()
    assert processor._draining_sleep is False
    for index in (1, 2):
        call_id = f"sleep-{index}"
        parts = [entry.args[2] for entry in store.await_args_list if entry.args[2].callID == call_id]
        assert parts[-1].id == parts[0].id
        assert parts[-1].state.status == "error"
        assert parts[-1].state.input == {"timeout_s": index}
        assert [p for p in published if p["callID"] == call_id][-1]["state"]["status"] == "error"


@pytest.mark.asyncio
async def test_multiple_deferred_sleeps_complete_once_on_success():
    processor = StreamProcessor(
        session_id="ses-multiple-sleeps", assistant_message=SimpleNamespace(id="msg-multiple-sleeps"),
        agent=SimpleNamespace(name="rex"),
    )
    execute = AsyncMock(return_value=ToolResult(success=True, output="done"))
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", AsyncMock(return_value=[])),
        patch.object(HookPipeline, "run_tool_before", AsyncMock(return_value=None)),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", execute),
        patch.object(processor, "_resolve_sandbox_meta", AsyncMock(return_value={"blocked": False, "error": None, "extra": {}})),
    ):
        for index in (1, 2):
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"sleep-{index}", tool_name="sleep", input={"timeout_s": index},
            ))
        await processor.drain_parallel_tool_calls()
        await processor.drain_parallel_tool_calls()

    assert [call.kwargs["ctx"].call_id for call in execute.await_args_list] == ["sleep-1", "sleep-2"]
    assert all(state.status == "completed" for state in processor.tool_calls.values())
    assert processor._draining_sleep is False


@pytest.mark.asyncio
async def test_cancelled_tool_before_hook_preserves_cancellation_and_finalizes_part():
    processor = StreamProcessor(
        session_id="ses-hook-cancel", assistant_message=SimpleNamespace(id="msg-hook-cancel"),
        agent=SimpleNamespace(name="rex"), event_publish_callback=AsyncMock(),
    )
    cancellation = asyncio.CancelledError("pre-hook cancelled")
    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", AsyncMock()) as store,
        patch.object(HookPipeline, "run_tool_before", AsyncMock(side_effect=cancellation)),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", AsyncMock()) as execute,
    ):
        await processor.process_event(ToolInputStartEvent(id="before-1", tool_name="read"))
        with pytest.raises(asyncio.CancelledError) as raised:
            await processor.process_event(ToolCallEvent(
                tool_call_id="before-1", tool_name="read", input={"file_path": "/tmp/file"},
            ))

    assert raised.value is cancellation
    execute.assert_not_awaited()
    assert processor.tool_calls["before-1"].status == "error"
    assert store.await_args.args[2].id == store.await_args_list[0].args[2].id
    assert store.await_args.args[2].state.input == {"file_path": "/tmp/file"}
    assert store.await_args.args[2].state.metadata["interrupted"] is True
