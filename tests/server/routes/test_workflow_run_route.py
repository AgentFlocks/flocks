from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.mcp import MCP
from flocks.tool import ToolContext
import flocks.server.routes.workflow as workflow_module


@pytest.mark.asyncio
async def test_run_workflow_execution_task_reuses_existing_mcp_without_reinit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_mock = AsyncMock()
    run_mock = Mock(
        return_value=SimpleNamespace(
            outputs={"ok": True},
            history=[],
            last_node_id="node-1",
            steps=1,
        )
    )
    record_result = AsyncMock(return_value=None)
    storage_read = AsyncMock(
        return_value={
            "id": "exec-1",
            "workflowId": "wf-1",
            "currentNodeType": "tool",
            "executionLog": [],
        }
    )

    monkeypatch.setattr(MCP, "init", init_mock)
    monkeypatch.setattr(workflow_module, "run_workflow", run_mock)
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(workflow_module.Storage, "read", storage_read)
    monkeypatch.setattr(workflow_module.Storage, "write", AsyncMock(return_value=None))
    monkeypatch.setattr(workflow_module, "compact_outputs_for_storage", lambda value: value)
    monkeypatch.setattr(workflow_module, "compact_history_for_storage", lambda value: value)

    req = workflow_module.WorkflowRunRequest(inputs={"ip": "8.8.8.8"}, trace=False)
    tool_context = ToolContext(session_id="session-1", message_id="message-1", agent="rex")

    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=req,
        exec_id="exec-1",
        cancel_event=workflow_module.threading.Event(),
        tool_context=tool_context,
    )

    init_mock.assert_not_awaited()
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["tool_context"] is tool_context
    record_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_workflow_execution_task_publishes_kafka_output_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_mock = Mock(
        return_value=SimpleNamespace(
            outputs={"ok": True},
            history=[],
            last_node_id="node-1",
            steps=1,
        )
    )
    record_result = AsyncMock(return_value=None)
    publish_output = AsyncMock(return_value=None)

    monkeypatch.setattr(workflow_module, "run_workflow", run_mock)
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(workflow_module, "_publish_kafka_execution_result", publish_output)
    monkeypatch.setattr(
        workflow_module.Storage,
        "read",
        AsyncMock(
            return_value={
                "id": "exec-1",
                "workflowId": "wf-1",
                "currentNodeType": "tool",
                "executionLog": [],
            }
        ),
    )
    monkeypatch.setattr(workflow_module.Storage, "write", AsyncMock(return_value=None))
    monkeypatch.setattr(workflow_module, "compact_outputs_for_storage", lambda value: value)
    monkeypatch.setattr(workflow_module, "compact_history_for_storage", lambda value: value)

    req = workflow_module.WorkflowRunRequest(inputs={"ip": "8.8.8.8"}, trace=False)

    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=req,
        exec_id="exec-1",
        cancel_event=workflow_module.threading.Event(),
    )

    record_result.assert_awaited_once()
    publish_output.assert_awaited_once_with("wf-1", "exec-1", {"ok": True})


@pytest.mark.asyncio
async def test_save_kafka_config_accepts_output_only_without_consumer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.ingest.kafka import manager as kafka_manager

    storage_write = AsyncMock(return_value=None)
    restart_workflow = AsyncMock(return_value={"state": "stopped", "error": None})

    monkeypatch.setattr(workflow_module, "_read_workflow_from_fs", lambda _workflow_id: {"workflowJson": {}})
    monkeypatch.setattr(workflow_module.Storage, "write", storage_write)
    monkeypatch.setattr(kafka_manager.default_manager, "restart_workflow", restart_workflow)

    req = workflow_module.KafkaConfigRequest(
        enabled=False,
        outputEnabled=True,
        outputBroker="localhost:9092",
        outputTopic="workflow-output",
    )

    response = await workflow_module.save_kafka_config("wf-output", req)

    assert response == {"ok": True, "consumer": {"state": "stopped", "error": None}}
    storage_write.assert_awaited_once()
    _, saved_config = storage_write.await_args.args
    assert saved_config["enabled"] is False
    assert saved_config["outputEnabled"] is True
    assert saved_config["inputTopic"] is None
    assert saved_config["outputBroker"] == "localhost:9092"
    assert saved_config["outputTopic"] == "workflow-output"
    restart_workflow.assert_awaited_once_with("wf-output")
