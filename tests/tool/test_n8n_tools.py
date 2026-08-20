from __future__ import annotations

import json

import pytest

from flocks.tool.registry import ToolContext, ToolRegistry


@pytest.fixture(autouse=True)
def init_tool_registry():
    ToolRegistry.init()
    yield


def test_n8n_tools_are_registered() -> None:
    for name in [
        "n8n_health_check",
        "n8n_workflow_render",
        "n8n_workflow_lint",
        "n8n_workflow_create",
        "n8n_workflow_update",
        "n8n_workflow_activate",
        "n8n_workflow_deactivate",
        "n8n_workflow_delete",
        "n8n_workflow_get",
        "n8n_execution_get",
        "n8n_webhook_call",
        "n8n_test_run",
        "n8n_repair_context",
    ]:
        assert ToolRegistry.get(name) is not None


@pytest.mark.anyio
async def test_render_and_lint_tools_work_without_n8n() -> None:
    ctx = ToolContext(session_id="test-session", message_id="test-message", agent="test")
    ir = {
        "name": "flocks-test-tool",
        "steps": [
            {"id": "code", "kind": "code"},
            {"id": "respond", "kind": "respond_to_webhook"},
        ],
        "tests": [{"name": "t", "input": {}, "expect": {"status": 200}}],
    }

    rendered = await ToolRegistry.execute("n8n_workflow_render", ctx=ctx, ir=ir)
    assert rendered.success is True
    output = json.loads(rendered.output) if isinstance(rendered.output, str) else rendered.output
    workflow = output["workflow"]

    linted = await ToolRegistry.execute(
        "n8n_workflow_lint",
        ctx=ctx,
        workflow=workflow,
        require_tests=True,
        tests=ir["tests"],
    )
    assert linted.success is True
    lint_output = json.loads(linted.output) if isinstance(linted.output, str) else linted.output
    assert lint_output["issues"] == []
