from __future__ import annotations

import pytest

from flocks.tool import ToolContext
from flocks.workflow.events import WorkflowWorkerLimits, WorkflowWorkerRequest, workflow_event
from flocks.workflow.process_executor import _serialize_tool_context, run_workflow_process


def _simple_workflow() -> dict:
    return {
        "name": "process_executor_test",
        "start": "produce",
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "outputs['answer'] = inputs.get('base', 0) + 1",
            }
        ],
        "edges": [],
    }


def test_worker_request_serializes_defaults() -> None:
    request = WorkflowWorkerRequest(
        request_id="req-1",
        workflow_id="wf-1",
        workflow=_simple_workflow(),
        limits=WorkflowWorkerLimits(),
    )

    payload = request.to_dict()

    assert payload["request_id"] == "req-1"
    assert payload["history_mode"] == "summary"
    assert payload["retain_history"] is False
    assert payload["limits"]["memory_limit_mb"] > 0
    assert payload["limits"]["soft_memory_budget_mb"] == int(payload["limits"]["memory_limit_mb"] * 0.75)


def test_worker_context_serializes_safe_extra_only(tmp_path) -> None:
    ctx = ToolContext(
        session_id="session-safe-extra",
        message_id="message-safe-extra",
        agent="rex",
        call_id="call-1",
        extra={
            "workspace_dir": str(tmp_path),
            "main_session_key": "main-session",
            "workflowAction": "invoke",
            "sandbox": {
                "container_name": "sandbox-1",
                "workspace_dir": str(tmp_path),
                "container_workdir": "/workspace",
                "env": {"SAFE": "1"},
            },
            "not_allowed": "drop-me",
            "sandbox_elevated": {"enabled": True, "tools": ["bash"]},
            "bad_object": object(),
        },
    )

    payload = _serialize_tool_context(ctx, workflow_id="wf-safe-extra")

    assert payload["workspace_dir"] == str(tmp_path)
    assert payload["action_name"] == "invoke"
    assert payload["call_id"] == "call-1"
    assert payload["extra"]["sandbox"]["container_name"] == "sandbox-1"
    assert payload["extra"]["sandbox_elevated"] == {"enabled": True, "tools": ["bash"]}
    assert "not_allowed" not in payload["extra"]
    assert "bad_object" not in payload["extra"]


def test_worker_limits_default_to_eighty_percent_machine_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("flocks.workflow.events._detect_total_memory_mb", lambda: 10_000)

    limits = WorkflowWorkerLimits()

    assert limits.memory_limit_mb == 8_000
    assert limits.soft_memory_budget_mb == 6_000


def test_workflow_event_uses_jsonl_schema_shape() -> None:
    event = workflow_event("step_completed", "req-1", step=1, step_result={"node_id": "produce"})

    assert event == {
        "type": "step_completed",
        "request_id": "req-1",
        "step": 1,
        "step_result": {"node_id": "produce"},
    }


@pytest.mark.asyncio
async def test_process_executor_runs_workflow_and_dispatches_step_events() -> None:
    completed_steps: list[dict] = []

    result = await run_workflow_process(
        workflow=_simple_workflow(),
        inputs={"base": 41},
        ensure_requirements=False,
        retain_history=True,
        on_step_complete=completed_steps.append,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs == {"answer": 42}
    assert result.steps == 1
    assert completed_steps
    assert completed_steps[0]["node_id"] == "produce"


@pytest.mark.asyncio
async def test_process_executor_times_out_worker_without_crashing_parent() -> None:
    workflow = {
        "name": "process_timeout_test",
        "start": "sleep",
        "nodes": [
            {
                "id": "sleep",
                "type": "python",
                "code": "import time\ntime.sleep(2)\noutputs['done'] = True",
            }
        ],
        "edges": [],
    }

    result = await run_workflow_process(
        workflow=workflow,
        ensure_requirements=False,
        timeout_s=0.1,
    )

    assert result.status == "TIMED_OUT"
    assert "timeout" in str(result.error).lower()


@pytest.mark.asyncio
async def test_process_executor_returns_failed_for_strict_implicit_edge_mapping() -> None:
    workflow = {
        "name": "strict_mapping_test",
        "metadata": {"runtime": {"strict_edge_mapping": True}},
        "start": "produce",
        "nodes": [
            {
                "id": "produce",
                "type": "python",
                "code": "outputs['raw'] = 'x' * 1000",
            },
            {
                "id": "consume",
                "type": "python",
                "code": "outputs['ok'] = bool(inputs.get('raw'))",
            },
        ],
        "edges": [{"from": "produce", "to": "consume"}],
    }

    result = await run_workflow_process(
        workflow=workflow,
        ensure_requirements=False,
    )

    assert result.status == "FAILED"
    assert "strict edge mapping" in str(result.error).lower()


@pytest.mark.asyncio
async def test_process_executor_bridges_tool_permission_callback() -> None:
    permission_requests: list[tuple[str, list[str]]] = []

    async def _track_permission(request) -> None:  # noqa: ANN001
        permission_requests.append((request.permission, list(request.patterns)))

    workflow = {
        "name": "permission_bridge_test",
        "start": "run_tool",
        "nodes": [
            {
                "id": "run_tool",
                "type": "python",
                "code": "\n".join(
                    [
                        "result = tool.run('bash', command='printf workflow-permission-bridge', timeout=5000)",
                        "outputs['result'] = result",
                    ]
                ),
            }
        ],
        "edges": [],
    }

    result = await run_workflow_process(
        workflow=workflow,
        ensure_requirements=False,
        timeout_s=10,
        tool_context=ToolContext(
            session_id="session-1",
            message_id="message-1",
            agent="rex",
            permission_callback=_track_permission,
        ),
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["result"] == "workflow-permission-bridge"
    assert ("bash", ["printf workflow-permission-bridge"]) in permission_requests


@pytest.mark.asyncio
async def test_process_executor_bridges_tool_event_publish_callback() -> None:
    published_events: list[tuple[str, dict]] = []

    async def _track_event(event_name: str, payload: dict) -> None:
        published_events.append((event_name, payload))

    workflow = {
        "name": "event_publish_bridge_test",
        "start": "publish_event",
        "nodes": [
            {
                "id": "publish_event",
                "type": "python",
                "code": "\n".join(
                    [
                        "import asyncio",
                        "ctx = getattr(getattr(tool, 'registry', None), '_ctx', None)",
                        "callback = getattr(ctx, 'event_publish_callback', None)",
                        "if callback:",
                        "    asyncio.run(callback('workflow.bridge_event', {'value': 'ok'}))",
                        "outputs['published'] = bool(callback)",
                    ]
                ),
            }
        ],
        "edges": [],
    }

    result = await run_workflow_process(
        workflow=workflow,
        ensure_requirements=False,
        timeout_s=10,
        tool_context=ToolContext(
            session_id="session-event-bridge",
            message_id="message-1",
            agent="rex",
            event_publish_callback=_track_event,
        ),
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["published"] is True
    assert ("workflow.bridge_event", {"value": "ok"}) in published_events


@pytest.mark.asyncio
async def test_process_executor_restores_serialized_tool_context_extra(tmp_path) -> None:
    workflow = {
        "name": "extra_bridge_test",
        "start": "inspect_extra",
        "nodes": [
            {
                "id": "inspect_extra",
                "type": "python",
                "code": "\n".join(
                    [
                        "ctx = getattr(getattr(tool, 'registry', None), '_ctx', None)",
                        "extra = getattr(ctx, 'extra', {}) or {}",
                        "outputs['workspace_dir'] = extra.get('workspace_dir')",
                        "outputs['sandbox_container'] = extra.get('sandbox', {}).get('container_name')",
                        "outputs['workflow_action'] = extra.get('workflowAction')",
                    ]
                ),
            }
        ],
        "edges": [],
    }

    result = await run_workflow_process(
        workflow=workflow,
        ensure_requirements=False,
        timeout_s=10,
        tool_context=ToolContext(
            session_id="session-extra-bridge",
            message_id="message-extra-bridge",
            agent="rex",
            extra={
                "workspace_dir": str(tmp_path),
                "workflowAction": "invoke",
                "sandbox": {
                    "container_name": "sandbox-extra-bridge",
                    "workspace_dir": str(tmp_path),
                    "container_workdir": "/workspace",
                },
            },
        ),
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["workspace_dir"] == str(tmp_path)
    assert result.outputs["sandbox_container"] == "sandbox-extra-bridge"
    assert result.outputs["workflow_action"] == "invoke"
