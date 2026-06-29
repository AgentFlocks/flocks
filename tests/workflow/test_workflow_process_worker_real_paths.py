from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from flocks.ingest.kafka import manager as kafka_manager
from flocks.ingest.syslog import manager as syslog_manager
from flocks.task.executor import TaskExecutor
from flocks.task.models import ExecutionMode, TaskExecution, TaskScheduler
from flocks.tool import ToolContext
from flocks.workflow import execution_store
from flocks.workflow.execution_manager import WorkflowExecutionManager
from flocks.workflow.poller_manager import WorkflowPollerManager
from flocks.workflow.service_runtime import create_service_app
from flocks.workflow.triggers import runtime as trigger_runtime
from flocks.workflow.triggers.models import TriggerDefinition


def _real_workflow() -> dict[str, Any]:
    return {
        "id": "real-process-worker-workflow",
        "name": "Real Process Worker Workflow",
        "start": "normalize",
        "nodes": [
            {
                "id": "normalize",
                "type": "python",
                "code": "\n".join(
                    [
                        "value = inputs.get('value')",
                        "if value is None:",
                        "    value = inputs.get('message')",
                        "if isinstance(value, dict):",
                        "    value = value.get('value') or value.get('message')",
                        "outputs['value'] = str(value or 'missing')",
                        "outputs['source_count'] = len([k for k in inputs.keys() if not k.startswith('_')])",
                    ]
                ),
            },
            {
                "id": "finish",
                "type": "python",
                "code": "\n".join(
                    [
                        "outputs['summary'] = f\"processed:{inputs.get('value')}\"",
                        "outputs['source_count'] = inputs.get('source_count')",
                        "outputs['workflow_success'] = True",
                    ]
                ),
            },
        ],
        "edges": [
            {
                "from": "normalize",
                "to": "finish",
                "mapping": {
                    "value": "value",
                    "source_count": "source_count",
                },
            }
        ],
    }


def _context_workflow_without_payload_id() -> dict[str, Any]:
    return {
        "name": "Context Workflow Without Payload ID",
        "start": "inspect_context",
        "nodes": [
            {
                "id": "inspect_context",
                "type": "python",
                "code": "\n".join(
                    [
                        "ctx = getattr(getattr(tool, 'registry', None), '_ctx', None)",
                        "extra = getattr(ctx, 'extra', {}) or {}",
                        "outputs['workflow_id'] = extra.get('workflowId')",
                    ]
                ),
            }
        ],
        "edges": [],
    }


@pytest.fixture
def lifecycle_store(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    state: dict[str, list[Any]] = {"created": [], "results": [], "steps": []}

    async def _create_execution_record(
        workflow_id: str,
        *,
        input_params: dict[str, Any] | None = None,
        exec_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": exec_id or f"exec-{workflow_id}-{len(state['created']) + 1}",
            "workflowId": workflow_id,
            "inputParams": input_params or {},
            "status": "running",
            "executionLog": [],
            "currentPhase": "queued",
            "currentStepIndex": 0,
        }
        state["created"].append(record)
        return dict(record)

    async def _record_execution_result(
        workflow_id: str,
        exec_id: str,
        exec_data: dict[str, Any],
    ) -> None:
        state["results"].append((workflow_id, exec_id, dict(exec_data)))

    async def _record_execution_step(exec_id: str, step_index: int, step_data: dict[str, Any]) -> None:
        state["steps"].append((exec_id, step_index, dict(step_data)))

    for module in (trigger_runtime, syslog_manager, kafka_manager):
        monkeypatch.setattr(module, "create_execution_record", _create_execution_record)
        monkeypatch.setattr(module, "record_execution_result", _record_execution_result)
    monkeypatch.setattr("flocks.workflow.poller_manager.create_execution_record", _create_execution_record)
    monkeypatch.setattr("flocks.workflow.poller_manager.record_execution_result", _record_execution_result)
    monkeypatch.setattr(execution_store, "record_execution_step", _record_execution_step)
    return state


@pytest.mark.asyncio
async def test_execution_manager_persists_real_workflow_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict[str, list[Any]] = {"created": [], "results": [], "steps": [], "progress": []}

    async def _create_execution_record(
        workflow_id: str,
        *,
        input_params: dict[str, Any] | None = None,
        exec_id: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": exec_id or "exec-manager",
            "workflowId": workflow_id,
            "inputParams": input_params or {},
            "status": "running",
            "executionLog": [],
        }
        state["created"].append(record)
        return dict(record)

    async def _record_execution_result(
        workflow_id: str,
        exec_id: str,
        exec_data: dict[str, Any],
    ) -> None:
        state["results"].append((workflow_id, exec_id, dict(exec_data)))

    async def _record_execution_step(exec_id: str, step_index: int, step_data: dict[str, Any]) -> None:
        state["steps"].append((exec_id, step_index, dict(step_data)))

    async def _upsert_execution(data: dict[str, Any]) -> None:
        state["progress"].append(dict(data))

    monkeypatch.setattr("flocks.workflow.execution_manager.create_execution_record", _create_execution_record)
    monkeypatch.setattr("flocks.workflow.execution_manager.record_execution_result", _record_execution_result)
    monkeypatch.setattr("flocks.workflow.execution_manager.record_execution_step", _record_execution_step)
    monkeypatch.setattr("flocks.workflow.execution_manager.WorkflowStore.upsert_execution", _upsert_execution)

    result = await WorkflowExecutionManager().run(
        workflow_id="wf-manager-real",
        workflow=_real_workflow(),
        inputs={"value": "manager"},
        ensure_requirements=False,
        persist=True,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["summary"] == "processed:manager"
    assert state["results"][0][2]["status"] == "success"
    assert state["results"][0][2]["outputResults"]["summary"] == "processed:manager"
    assert [step[1] for step in state["steps"]] == [1, 2]
    assert state["steps"][0][2]["node_id"] == "normalize"
    assert state["steps"][1][2]["node_id"] == "finish"
    assert state["results"][0][2]["stepCount"] == 2
    assert any(progress.get("currentNodeId") == "normalize" for progress in state["progress"])


@pytest.mark.asyncio
async def test_execution_manager_passes_explicit_workflow_id_to_worker() -> None:
    workflow = {
        "start": "inspect_context",
        "nodes": [
            {
                "id": "inspect_context",
                "type": "python",
                "code": "\n".join(
                    [
                        "ctx = getattr(getattr(tool, 'registry', None), '_ctx', None)",
                        "extra = getattr(ctx, 'extra', {}) or {}",
                        "outputs['workflow_id'] = extra.get('workflowId')",
                    ]
                ),
            }
        ],
        "edges": [],
    }

    result = await WorkflowExecutionManager().run(
        workflow_id="wf-manager-explicit-id",
        workflow=workflow,
        ensure_requirements=False,
        persist=False,
    )

    assert result.status == "SUCCEEDED"
    assert result.outputs["workflow_id"] == "wf-manager-explicit-id"


def test_service_runtime_invoke_runs_real_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _noop_async() -> None:
        return None

    monkeypatch.setattr("flocks.workflow.service_runtime.MCP.init", _noop_async)
    monkeypatch.setattr(
        "flocks.workflow.service_runtime.get_manager",
        lambda: SimpleNamespace(shutdown=_noop_async),
    )

    async def _build_context(**_: Any) -> ToolContext:
        return ToolContext(session_id="session-1", message_id="message-1", agent="rex")

    monkeypatch.setattr("flocks.workflow.service_runtime.build_workflow_tool_context", _build_context)

    app = create_service_app(
        workflow_json=_real_workflow(),
        workflow_id="wf-service-real",
        release_id="rel-1",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post("/invoke", json={"inputs": {"value": "service"}})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["outputs"]["summary"] == "processed:service"


@pytest.mark.asyncio
async def test_task_executor_runs_real_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    from flocks.workflow import fs_store

    monkeypatch.setattr(
        fs_store,
        "read_workflow_from_fs",
        lambda workflow_id: {"workflowJson": _real_workflow()} if workflow_id == "wf-task-real" else None,
    )
    scheduler = TaskScheduler(
        title="workflow task",
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="wf-task-real",
        context={"value": "task"},
    )
    execution = TaskExecution(
        schedulerID=scheduler.id,
        title=scheduler.title,
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="wf-task-real",
        executionInputSnapshot={"context": {"value": "task"}},
    )

    result = await TaskExecutor._trigger_workflow(execution, scheduler)

    assert "processed:task" in str(result)


@pytest.mark.asyncio
async def test_task_executor_passes_scheduler_workflow_id_to_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.workflow import fs_store

    monkeypatch.setattr(
        fs_store,
        "read_workflow_from_fs",
        lambda workflow_id: (
            {"workflowJson": _context_workflow_without_payload_id()}
            if workflow_id == "wf-task-explicit-worker-id"
            else None
        ),
    )
    scheduler = TaskScheduler(
        title="workflow task",
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="wf-task-explicit-worker-id",
        context={},
    )
    execution = TaskExecution(
        schedulerID=scheduler.id,
        title=scheduler.title,
        executionMode=ExecutionMode.WORKFLOW,
        workflowID="wf-task-explicit-worker-id",
        executionInputSnapshot={"context": {}},
    )

    result = await TaskExecutor._trigger_workflow(execution, scheduler)

    assert "wf-task-explicit-worker-id" in str(result)


@pytest.mark.asyncio
async def test_trigger_runtime_runs_real_workflow(lifecycle_store: dict[str, list[Any]]) -> None:
    runtime = trigger_runtime.TriggerRuntime()
    trigger = TriggerDefinition.model_validate({"id": "manual-test", "type": "manual"})

    result = await runtime._execute_workflow(  # noqa: SLF001
        workflow_id="wf-trigger-real",
        workflow_json=_real_workflow(),
        trigger=trigger,
        mapped_inputs={"value": "trigger"},
    )

    assert result["status"] == "success"
    assert result["outputResults"]["summary"] == "processed:trigger"
    assert lifecycle_store["results"][0][2]["status"] == "success"


@pytest.mark.asyncio
async def test_poller_runs_real_workflow_and_records_steps(lifecycle_store: dict[str, list[Any]]) -> None:
    manager = WorkflowPollerManager()

    result = await manager._execute_run(  # noqa: SLF001
        "wf-poller-real",
        _real_workflow(),
        {"timeoutSeconds": 10, "inputs": {"value": "poller"}},
    )

    assert result["lastStatus"] == "success"
    exec_data = lifecycle_store["results"][0][2]
    assert exec_data["status"] == "success"
    assert exec_data["outputResults"]["summary"] == "processed:poller"
    assert exec_data["stepCount"] == 2
    assert len(lifecycle_store["steps"]) == 2


class _ImmediateDispatcher:
    def __init__(self, mapped_inputs: dict[str, Any]) -> None:
        self.mapped_inputs = mapped_inputs
        self.result: dict[str, Any] | None = None

    async def dispatch(self, *, trigger, event, executor):  # noqa: ANN001
        del trigger, event
        self.result = await executor(self.mapped_inputs)


@pytest.mark.asyncio
async def test_syslog_runs_real_workflow(lifecycle_store: dict[str, list[Any]]) -> None:
    manager = syslog_manager.SyslogManager()
    dispatcher = _ImmediateDispatcher({"value": "syslog"})
    manager._dispatcher = dispatcher  # noqa: SLF001

    await manager._trigger_workflow(  # noqa: SLF001
        "wf-syslog-real",
        _real_workflow(),
        {"message": "syslog"},
        "syslog_message",
    )

    assert dispatcher.result is not None
    assert dispatcher.result["status"] == "success"
    assert dispatcher.result["outputResults"]["summary"] == "processed:syslog"
    assert lifecycle_store["results"][-1][2]["status"] == "success"


@pytest.mark.asyncio
async def test_kafka_runs_real_workflow(lifecycle_store: dict[str, list[Any]]) -> None:
    manager = kafka_manager.KafkaManager()
    dispatcher = _ImmediateDispatcher({"value": "kafka"})
    manager._dispatcher = dispatcher  # noqa: SLF001

    await manager._trigger_workflow(  # noqa: SLF001
        "wf-kafka-real",
        _real_workflow(),
        {"message": "kafka"},
        "kafka_message",
    )

    assert dispatcher.result is not None
    assert dispatcher.result["status"] == "success"
    assert dispatcher.result["outputResults"]["summary"] == "processed:kafka"
    assert lifecycle_store["results"][-1][2]["status"] == "success"
