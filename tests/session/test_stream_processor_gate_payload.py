"""The streamed tool gate must see the canonical ``tool_execution`` contract.

Regression for the arch_v2 verdict carry: the stream processor's pre-execution
gate call used the flat legacy payload and its verdict was reused by the
registry, so a contract-aware policy gate (e.g. Pro enforce mode) denied every
streamed call as ``trusted_subject_missing`` -- the ingress carrier never
reached it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.hooks.execution import execution_context_scope
from flocks.session.streaming.stream_events import ToolCallEvent, ToolInputStartEvent
from flocks.tool.registry import ToolResult

from tests.session.test_stream_processor import _make_processor


@pytest.mark.asyncio
async def test_streamed_gate_payload_carries_tool_execution_contract_and_ingress_context():
    proc = _make_processor()
    seen: list = []

    async def _fake_tool_before(payload):
        seen.append(payload)
        ctx = MagicMock()
        ctx.input = payload
        ctx.output = {}
        return ctx

    result = ToolResult(success=True, output="ok", title="ip query", metadata={})

    with (
        patch("flocks.session.streaming.stream_processor.Message.store_part", new=AsyncMock()),
        patch("flocks.session.streaming.stream_processor.Message.update_part", new=AsyncMock()),
        patch(
            "flocks.session.streaming.stream_processor.ToolRegistry.execute",
            new=AsyncMock(return_value=result),
        ),
        patch(
            "flocks.hooks.pipeline.HookPipeline.run_tool_before",
            new=AsyncMock(side_effect=_fake_tool_before),
        ),
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
    # legacy flat keys stay for handlers written against the old shape
    assert payload["tool"] == {"name": "ip_query", "input": {"ip": "1.2.3.4"}, "callID": "tc_gate"}
    assert payload["sessionID"] == proc.session_id
