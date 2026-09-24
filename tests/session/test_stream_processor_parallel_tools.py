import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.session.streaming.stream_events import ToolCallEvent
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.tool.registry import ToolResult


@pytest.mark.asyncio
async def test_foreground_subagent_tool_calls_start_in_parallel():
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
        await processor.drain_parallel_tool_calls()

    assert processor.tool_calls["call-1"].status == "completed"
    assert processor.tool_calls["call-2"].status == "completed"


@pytest.mark.asyncio
async def test_max_parallel_subagents_caps_running_siblings():
    processor = StreamProcessor(
        session_id="ses-parent",
        assistant_message=SimpleNamespace(id="msg-assistant"),
        agent=SimpleNamespace(name="rex"),
        max_parallel_subagents=2,
    )
    release = asyncio.Event()
    started: list[str] = []
    running = 0
    peak = 0

    async def fake_execute(tool_name, ctx, **_kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        started.append(ctx.call_id)
        await release.wait()
        running -= 1
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
        for index in range(1, 6):
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"call-{index}",
                tool_name="delegate_task",
                input={
                    "description": f"inspect {index}",
                    "prompt": f"Inspect {index}",
                    "subagent_type": "explore",
                },
            ))

        await asyncio.sleep(0.05)
        # The gate is FIFO: the first two siblings run, the rest wait.
        assert set(started) == {"call-1", "call-2"}
        assert len(processor._parallel_tool_tasks) == 5

        release.set()
        await processor.drain_parallel_tool_calls()

    assert set(started) == {f"call-{i}" for i in range(1, 6)}
    assert peak == 2
    assert all(processor.tool_calls[f"call-{i}"].status == "completed" for i in range(1, 6))


@pytest.mark.asyncio
async def test_max_parallel_subagents_zero_means_unbounded():
    processor = StreamProcessor(
        session_id="ses-parent",
        assistant_message=SimpleNamespace(id="msg-assistant"),
        agent=SimpleNamespace(name="rex"),
        max_parallel_subagents=0,
    )
    release = asyncio.Event()
    started: list[str] = []

    async def fake_execute(tool_name, ctx, **_kwargs):
        started.append(ctx.call_id)
        await release.wait()
        return ToolResult(success=True, output="done")

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
        for index in range(1, 4):
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"call-{index}",
                tool_name="delegate_task",
                input={"prompt": f"Inspect {index}", "subagent_type": "explore"},
            ))
        await asyncio.sleep(0.05)
        assert set(started) == {"call-1", "call-2", "call-3"}
        release.set()
        await processor.drain_parallel_tool_calls()


@pytest.mark.asyncio
async def test_queued_siblings_do_not_start_after_the_turn_is_aborted():
    """Stop must not let siblings that were waiting for a slot start a child session."""
    abort_event = asyncio.Event()
    processor = StreamProcessor(
        session_id="ses-parent",
        assistant_message=SimpleNamespace(id="msg-assistant"),
        agent=SimpleNamespace(name="rex"),
        abort_event=abort_event,
        max_parallel_subagents=1,
    )
    release = asyncio.Event()
    executed: list[str] = []
    stored_parts: list = []

    async def fake_execute(tool_name, ctx, **_kwargs):
        executed.append(ctx.call_id)
        await release.wait()
        return ToolResult(success=True, output=f"{tool_name} done")

    async def fake_store_part(_session_id, _message_id, part):
        stored_parts.append(part)

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", fake_store_part),
        patch("flocks.session.streaming.stream_processor.Message.parts", AsyncMock(return_value=[])),
        patch("flocks.session.streaming.stream_processor.ToolRegistry.execute", fake_execute),
        patch.object(
            processor,
            "_resolve_sandbox_meta",
            AsyncMock(return_value={"blocked": False, "error": None, "extra": {}}),
        ),
    ):
        for index in range(1, 4):
            await processor.process_event(ToolCallEvent(
                tool_call_id=f"call-{index}",
                tool_name="delegate_task",
                input={"prompt": f"Inspect {index}", "subagent_type": "explore"},
            ))
        await asyncio.sleep(0.05)
        assert executed == ["call-1"]

        # User hits Stop: the running sibling finishes (its child session is
        # aborted elsewhere), the queued ones must be marked interrupted.
        abort_event.set()
        release.set()
        await processor.drain_parallel_tool_calls()

    assert executed == ["call-1"]
    assert processor.tool_calls["call-1"].status == "completed"
    for call_id in ("call-2", "call-3"):
        state = processor.tool_calls[call_id]
        assert state.status == "error"
        assert state.error == "Tool execution was interrupted"
    interrupted_parts = [
        part for part in stored_parts
        if getattr(part, "callID", None) in {"call-2", "call-3"}
        and getattr(getattr(part, "state", None), "status", None) == "error"
    ]
    assert len(interrupted_parts) == 2
