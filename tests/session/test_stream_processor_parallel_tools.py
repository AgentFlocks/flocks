import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.session.streaming.stream_processor import StreamProcessor
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
@pytest.mark.parametrize("mixed", [False, True])
async def test_sleep_runs_after_stream_and_rejects_mixed_batches(mixed):
    from flocks.tool.agent.communication import sleep
    processor = StreamProcessor(
        session_id="sleep-session", assistant_message=SimpleNamespace(id="msg"),
        agent=SimpleNamespace(name="rex"),
    )
    results = []

    async def execute(tool_name, ctx, **kwargs):
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
        if mixed:
            await processor.process_event(ToolCallEvent(tool_call_id="other", tool_name="read", input={}))
        await processor.drain_parallel_tool_calls()
    assert results[-1].success is not mixed
