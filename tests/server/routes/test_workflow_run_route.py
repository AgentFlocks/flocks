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
    run_mock = AsyncMock(
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
    monkeypatch.setattr(workflow_module, "run_workflow_managed", run_mock)
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
    run_mock.assert_awaited_once()
    assert run_mock.call_args.kwargs["tool_context"] is tool_context
    record_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_kafka_config_persists_consumer_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.ingest.kafka import manager as kafka_manager

    put_config = AsyncMock(return_value=None)
    restart_workflow = AsyncMock(return_value={"state": "running", "error": None})
    persisted_triggers: list[list[str]] = []

    monkeypatch.setattr(
        workflow_module,
        "_read_workflow_from_fs",
        lambda _workflow_id: {"id": "wf-input", "workflowJson": {}},
    )
    monkeypatch.setattr(workflow_module.WorkflowStore, "put_config", put_config)
    monkeypatch.setattr(kafka_manager.default_manager, "restart_workflow", restart_workflow)
    monkeypatch.setattr(workflow_module, "_get_workflow_trigger_defs", AsyncMock(return_value=[]))

    async def _fake_persist(workflow_id: str, workflow_data: dict, triggers: list) -> dict:
        persisted_triggers.append([trigger.id for trigger in triggers])
        return {
            **workflow_data,
            "workflowJson": {
                **workflow_data["workflowJson"],
                "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
            },
        }

    monkeypatch.setattr(workflow_module, "_persist_workflow_triggers", _fake_persist)

    req = workflow_module.KafkaConfigRequest(
        enabled=True,
        inputBroker="localhost:9092",
        inputTopic="workflow-input",
        inputGroupId="wf-group",
        inputKey="kafka_message",
        inputs={
            "_comment": "remove me",
            "kafka_output_enabled": True,
            "kafka_output_topic": "topic_soc_flocks_result_log",
        },
    )

    response = await workflow_module.save_kafka_config("wf-input", req)

    assert response == {"ok": True, "consumer": {"state": "running", "error": None}}
    put_config.assert_awaited_once()
    workflow_id, saved_config = put_config.await_args.args
    assert workflow_id == "wf-input"
    assert put_config.await_args.kwargs["kind"] == "workflow_kafka_config"
    assert saved_config["enabled"] is True
    assert saved_config["inputBroker"] == "localhost:9092"
    assert saved_config["inputTopic"] == "workflow-input"
    assert saved_config["inputGroupId"] == "wf-group"
    assert saved_config["inputKey"] == "kafka_message"
    assert saved_config["inputs"] == {
        "kafka_output_enabled": True,
        "kafka_output_topic": "topic_soc_flocks_result_log",
    }
    assert "outputEnabled" not in saved_config
    assert "outputBroker" not in saved_config
    assert "outputTopic" not in saved_config
    assert persisted_triggers == [["kafka-default"]]
    restart_workflow.assert_awaited_once_with("wf-input")


@pytest.mark.asyncio
async def test_save_syslog_config_persists_listener_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.ingest.syslog import manager as syslog_manager

    put_config = AsyncMock(return_value=None)
    restart_workflow = AsyncMock(return_value={"state": "listening", "error": None})
    persisted_triggers: list[list[str]] = []

    monkeypatch.setattr(
        workflow_module,
        "_read_workflow_from_fs",
        lambda _workflow_id: {"id": "wf-input", "workflowJson": {}},
    )
    monkeypatch.setattr(workflow_module.WorkflowStore, "put_config", put_config)
    monkeypatch.setattr(syslog_manager.default_manager, "restart_workflow", restart_workflow)
    monkeypatch.setattr(workflow_module, "_get_workflow_trigger_defs", AsyncMock(return_value=[]))

    async def _fake_persist(workflow_id: str, workflow_data: dict, triggers: list) -> dict:
        persisted_triggers.append([trigger.id for trigger in triggers])
        return {
            **workflow_data,
            "workflowJson": {
                **workflow_data["workflowJson"],
                "triggers": [trigger.model_dump(mode="json") for trigger in triggers],
            },
        }

    monkeypatch.setattr(workflow_module, "_persist_workflow_triggers", _fake_persist)

    req = workflow_module.SyslogConfigRequest(
        enabled=True,
        protocol="udp",
        host="0.0.0.0",
        port=5514,
        format="auto",
        inputKey="syslog_message",
    )

    response = await workflow_module.save_syslog_config("wf-input", req)

    assert response == {"ok": True, "listener": {"state": "listening", "error": None}}
    put_config.assert_awaited_once()
    workflow_id, saved_config = put_config.await_args.args
    assert workflow_id == "wf-input"
    assert put_config.await_args.kwargs["kind"] == "workflow_syslog_config"
    assert saved_config["enabled"] is True
    assert saved_config["protocol"] == "udp"
    assert saved_config["host"] == "0.0.0.0"
    assert saved_config["port"] == 5514
    assert saved_config["inputKey"] == "syslog_message"
    assert persisted_triggers == [["syslog-default"]]
    restart_workflow.assert_awaited_once_with("wf-input")
