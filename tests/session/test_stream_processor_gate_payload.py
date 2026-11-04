"""The streamed tool gate must see the canonical ``tool_execution`` contract.

Regression for the arch_v2 verdict carry: a streamed call's pre-execution gate
once ran on the flat legacy payload, so a contract-aware policy gate (e.g. Pro
enforce mode) denied every streamed call as ``trusted_subject_missing`` -- the
ingress carrier never reached it.  The gate now fires once, inside the registry,
on the canonical contract; the stream layer only runs ``tool.input.rewrite``.
The flat legacy keys still ride along for hooks written against the old shape
(the Pro redaction hook restores placeholders by rewriting ``tool.input``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flocks.hooks.execution import execution_context_scope
from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.tool.registry import ToolResult

from tests.session.test_stream_processor import _make_processor, registry_sandbox  # noqa: F401


@pytest.mark.asyncio
async def test_streamed_gate_payload_carries_tool_execution_contract_and_ingress_context(registry_sandbox):  # noqa: F811
    from flocks.hooks.pipeline import HookBase, HookPipeline

    seen: list = []

    class Recorder(HookBase):
        async def tool_before(self, ctx):
            seen.append(ctx.input)

    HookPipeline.register("recorder", Recorder())

    async def handler(_ctx, **kwargs):
        return ToolResult(success=True, output="ok", title="ip query", metadata={})

    registry_sandbox.register("ip_query", handler, params=("ip",))
    proc = _make_processor()

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.parts", new=AsyncMock(return_value=[])),
        execution_context_scope({"subject": {"subject_id": "usr_1"}, "workflow_transfer": "tr_abc"}),
    ):
        await proc.process_event(ToolInputStartEvent(id="tc_gate", tool_name="ip_query"))
        await proc.process_event(
            ToolCallEvent(tool_call_id="tc_gate", tool_name="ip_query", input={"ip": "1.2.3.4"})
        )

    assert len(seen) == 1
    payload = seen[0]
    # canonical contract for contract-aware gates
    contract = payload["tool_execution"]
    assert contract["tool"]["name"] == "ip_query"
    assert contract["tool"]["validated_input"] == {"ip": "1.2.3.4"}
    assert contract["session"]["id"] == proc.session_id
    assert contract["execution"]["tool_call_id"] == "tc_gate"
    # the opaque ingress carrier rides along so a child-task gate can restore identity
    assert contract["extension_context"]["workflow_transfer"] == "tr_abc"
    assert contract["extension_context"]["subject"] == {"subject_id": "usr_1"}
    # the flat legacy shape stays next to the contract for in-place ``tool.input`` rewrites
    assert payload["tool"] == {"name": "ip_query", "input": {"ip": "1.2.3.4"}, "callID": "tc_gate"}
    assert payload["sessionID"] == proc.session_id
    assert proc.tool_calls["tc_gate"].status == "completed"
