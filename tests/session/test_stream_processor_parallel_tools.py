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
