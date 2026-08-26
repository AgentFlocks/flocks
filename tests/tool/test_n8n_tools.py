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


@pytest.mark.anyio
async def test_lint_tool_accepts_kafka_group_prefixes() -> None:
    ctx = ToolContext(session_id="test-session", message_id="test-message", agent="test")
    ir = {
        "name": "flocks-test-kafka-tool",
        "trigger": {
            "type": "kafka",
            "topic": "security-alerts",
            "groupPrefix": "flocks_kafka",
            "credentialRef": {"name": "Kafka Production"},
        },
        "steps": [{"id": "mark", "kind": "code", "js_code": "return $input.all();"}],
        "tests": [],
    }

    rendered = await ToolRegistry.execute("n8n_workflow_render", ctx=ctx, ir=ir)
    workflow = (json.loads(rendered.output) if isinstance(rendered.output, str) else rendered.output)["workflow"]
    linted = await ToolRegistry.execute(
        "n8n_workflow_lint",
        ctx=ctx,
        workflow=workflow,
        kafka_group_prefixes=["flocks_kafka"],
    )
    lint_output = json.loads(linted.output) if isinstance(linted.output, str) else linted.output

    assert linted.success is True
    assert [issue for issue in lint_output["issues"] if issue["severity"] == "error"] == []


@pytest.mark.anyio
async def test_n8n_test_run_tool_publishes_kafka_without_webhook_call(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.tool.integration import n8n as n8n_tools

    class FakeClient:
        def __init__(self):
            self.activated: list[str] = []

        def create_workflow(self, payload):
            return {"status": 200, "body": {"id": "wf-kafka-tool", "name": payload["name"]}}

        def activate_workflow(self, workflow_id: str):
            self.activated.append(workflow_id)
            return {"status": 200, "body": {"id": workflow_id, "active": True}}

        def call_webhook(self, *args, **kwargs):
            raise AssertionError("Kafka n8n_test_run must not call webhook")

    fake_client = FakeClient()
    monkeypatch.setattr(n8n_tools, "_client", lambda **_kwargs: fake_client)
    ctx = ToolContext(session_id="test-session", message_id="test-message", agent="test")
    ir = {
        "name": "flocks-test-kafka-tool-run",
        "trigger": {
            "type": "kafka",
            "topic": "security-alerts",
            "groupPrefix": "flocks_kafka",
            "credentialRef": {"name": "Kafka Production"},
            "resolveOffset": "onCompletion",
        },
        "steps": [{"id": "mark", "kind": "code", "js_code": "return $input.all();"}],
        "tests": [],
    }

    result = await ToolRegistry.execute("n8n_test_run", ctx=ctx, ir=ir)
    output = json.loads(result.output) if isinstance(result.output, str) else result.output

    assert result.success is True
    assert output["trigger_type"] == "kafka"
    assert output["kafka_group_id"] == "flocks_kafka_n8n_flocks_test_kafka_tool_run"
    assert output["test_results"] == []
    assert fake_client.activated == ["wf-kafka-tool"]
