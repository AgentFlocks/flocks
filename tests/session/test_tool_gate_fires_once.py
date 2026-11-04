"""
End-to-end check that a streamed tool call passes the pre-execution
checkpoint exactly once.

The streaming layer runs the gate before it publishes the running state (so the
UI shows the input the tool actually receives) and the registry runs it on the
way into the handler.  Those used to be two independent hook invocations with
two different decision shapes.  These tests pin the converged behaviour: one
invocation, and the verdict it produced is the one the registry enforces.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)

TOOL_NAME = "gate_probe_tool"


@pytest.fixture
def probe_tool():
    """A real registered tool, so ToolRegistry.execute runs its own gate."""
    calls: list[dict] = []

    @ToolRegistry.register_function(
        name=TOOL_NAME,
        description="Records the input it was executed with",
        category=ToolCategory.SYSTEM,
        parameters=[
            ToolParameter(
                name="ip",
                type=ParameterType.STRING,
                description="probe value",
                required=True,
            )
        ],
    )
    async def _tool(_ctx: ToolContext, ip: str) -> ToolResult:
        calls.append({"ip": ip})
        return ToolResult(success=True, output="ok")

    yield calls

    ToolRegistry._tools.pop(TOOL_NAME, None)


def _make_processor(session_id="ses_gate_once"):
    message = MagicMock()
    message.id = "msg_gate_once"
    message.sessionID = session_id
    message.role = "assistant"

    agent = MagicMock()
    agent.name = "rex"
    agent.model = None
    agent.provider = None

    return StreamProcessor(
        session_id=session_id,
        assistant_message=message,
        agent=agent,
        allowed_tool_names=[TOOL_NAME],
    )


async def _drive_tool_call(proc, tool_input):
    await proc.process_event(ToolInputStartEvent(id="call-gate-1", tool_name=TOOL_NAME))
    await proc.process_event(
        ToolCallEvent(tool_call_id="call-gate-1", tool_name=TOOL_NAME, input=tool_input)
    )


class _Ctx:
    def __init__(self, output=None, input_=None):
        self.output = output or {}
        self.input = input_ or {}
        self.execution_stop_requested = False
        self.execution_stop_detail = None


@pytest.mark.asyncio
async def test_tool_before_fires_once_per_streamed_call(probe_tool):
    """The handlers are asked once, not once per layer."""
    hook = AsyncMock(return_value=_Ctx())
    proc = _make_processor()

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=hook),
    ):
        await _drive_tool_call(proc, {"ip": "1.1.1.1"})

    assert hook.await_count == 1
    assert probe_tool == [{"ip": "1.1.1.1"}]


@pytest.mark.asyncio
async def test_canonical_deny_blocks_a_streamed_call(probe_tool):
    """A mapping-shaped deny stops the tool.

    This is the shape the streaming layer used to read with ``str(...) ==
    "block"``, which never matched, so the decision was computed and dropped.
    """
    hook = AsyncMock(return_value=_Ctx(output={"decision": {"action": "deny", "reason": "policy"}}))
    proc = _make_processor()

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=hook),
    ):
        await _drive_tool_call(proc, {"ip": "1.1.1.1"})

    assert hook.await_count == 1
    assert probe_tool == []  # the handler never ran
    state = proc.tool_calls["call-gate-1"]
    assert state.status == "error"


@pytest.mark.asyncio
async def test_legacy_block_string_also_stops_a_streamed_call(probe_tool):
    """The older bare-string shape keeps working through the same path."""
    hook = AsyncMock(return_value=_Ctx(output={"decision": "block"}))
    proc = _make_processor()

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch("flocks.hooks.pipeline.HookPipeline.run_tool_before", new=hook),
    ):
        await _drive_tool_call(proc, {"ip": "1.1.1.1"})

    assert hook.await_count == 1
    assert probe_tool == []
