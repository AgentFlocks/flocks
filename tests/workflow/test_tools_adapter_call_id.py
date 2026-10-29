"""Regression: every workflow tool invocation carries its own ``call_id``.

Without one, the tool-before gate rejected tool nodes outright
("Tool execution blocked because tool-before hook failed"), and inheriting the
parent session's id made every call inside a workflow collide with the call
that started the workflow in the audit trail.
"""

from __future__ import annotations

import pytest

from flocks.tool import ToolContext
from flocks.workflow.tools_adapter import FlocksToolAdapter


@pytest.fixture
def adapter() -> FlocksToolAdapter:
    return FlocksToolAdapter()


def test_standalone_workflow_context_has_a_fresh_call_id_per_call(adapter: FlocksToolAdapter) -> None:
    first = adapter._tool_context()
    second = adapter._tool_context()

    assert first.call_id and first.call_id.startswith("wf-")
    assert second.call_id and second.call_id != first.call_id
    assert first.session_id == "workflow"


def test_inherited_context_keeps_session_but_not_parent_call_id() -> None:
    parent = ToolContext(session_id="ses_parent", message_id="msg_parent", call_id="call_parent")
    adapter = FlocksToolAdapter(tool_context=parent)

    ctx = adapter._tool_context()

    assert ctx.session_id == "ses_parent" and ctx.message_id == "msg_parent"
    assert ctx.call_id and ctx.call_id != "call_parent"
    assert ctx.call_id != adapter._tool_context().call_id
