from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.mcp import MCP
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.tool import ToolContext
import flocks.server.routes.workflow as workflow_module


def _minimal_workflow_json(metadata=None):
    workflow = {
        "name": "minimal",
        "start": "start",
        "nodes": [{"id": "start", "type": "python", "code": "outputs['ok'] = True"}],
        "edges": [],
    }
    if metadata is not None:
        workflow["metadata"] = metadata
    return workflow


def _two_node_workflow_json(edge):
    return {
        "name": "two-node",
        "start": "prepare_message",
        "nodes": [
            {
                "id": "prepare_message",
                "type": "python",
                "code": "outputs['message_text'] = inputs.get('message', '')",
            },
            {
                "id": "transform_message",
                "type": "python",
                "code": "outputs['final_message'] = inputs.get('message_text', '').upper()",
            },
        ],
        "edges": [edge],
    }


@pytest.mark.asyncio
async def test_workflow_control_route_is_denied_before_filesystem_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _DenyWorkflowControl(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "deny", "reason": "workflow_control_denied"}

    write_workflow = Mock(side_effect=AssertionError("workflow write reached"))
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("deny-workflow-control", _DenyWorkflowControl(), critical=True)
    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", write_workflow)
    try:
        with pytest.raises(workflow_module.HTTPException) as exc_info:
            await workflow_module.create_workflow(
                workflow_module.WorkflowCreateRequest(
                    name="blocked workflow",
                    workflowJson=_minimal_workflow_json(),
                )
            )
    finally:
        HookPipeline.reset()

    assert exc_info.value.status_code == 403
    write_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_create_workflow_applies_vertex_cache_runtime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict] = []

    def _fake_write_workflow_to_fs(workflow_id, workflow_json, meta, *args, **kwargs):
        writes.append({"workflow_id": workflow_id, "workflow_json": workflow_json, "meta": meta})

    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", _fake_write_workflow_to_fs)
    monkeypatch.setattr(workflow_module, "_get_workflow_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(workflow_module, "publish_event", AsyncMock(return_value=None))

    req = workflow_module.WorkflowCreateRequest(
        name="new workflow",
        workflowJson=_minimal_workflow_json(),
    )

    result = await workflow_module.create_workflow(req)

    runtime = result.workflowJson["metadata"]["runtime"]
    assert runtime["strict_edge_mapping"] is True
    assert runtime["dataflow_mode"] == "vertex_cache"
    assert writes[0]["workflow_json"]["metadata"]["runtime"] == runtime


@pytest.mark.asyncio
async def test_create_workflow_preserves_explicit_runtime_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[dict] = []

    def _fake_write_workflow_to_fs(workflow_id, workflow_json, meta, *args, **kwargs):
        writes.append({"workflow_id": workflow_id, "workflow_json": workflow_json, "meta": meta})

    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", _fake_write_workflow_to_fs)
    monkeypatch.setattr(workflow_module, "_get_workflow_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(workflow_module, "publish_event", AsyncMock(return_value=None))

    req = workflow_module.WorkflowCreateRequest(
        name="legacy workflow",
        workflowJson=_minimal_workflow_json(
            {
                "runtime": {
                    "strict_edge_mapping": False,
                    "dataflow_mode": "legacy",
                }
            }
        ),
    )

    result = await workflow_module.create_workflow(req)

    runtime = result.workflowJson["metadata"]["runtime"]
    assert runtime["strict_edge_mapping"] is False
    assert runtime["dataflow_mode"] == "legacy"
    assert writes[0]["workflow_json"]["metadata"]["runtime"] == runtime


@pytest.mark.asyncio
async def test_create_workflow_rejects_unmapped_edges_after_strict_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_workflow = Mock()
    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", write_workflow)

    req = workflow_module.WorkflowCreateRequest(
        name="new workflow",
        workflowJson=_two_node_workflow_json(
            {"from": "prepare_message", "to": "transform_message", "order": 0}
        ),
    )

    with pytest.raises(workflow_module.HTTPException) as exc_info:
        await workflow_module.create_workflow(req)

    assert exc_info.value.status_code == 400
    assert "Workflow strict edge mapping failed" in str(exc_info.value.detail)
    assert "prepare_message" in str(exc_info.value.detail)
    write_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_create_workflow_accepts_explicit_mapping_after_strict_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[dict] = []

    def _fake_write_workflow_to_fs(workflow_id, workflow_json, meta, *args, **kwargs):
        writes.append({"workflow_id": workflow_id, "workflow_json": workflow_json, "meta": meta})

    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", _fake_write_workflow_to_fs)
    monkeypatch.setattr(workflow_module, "_get_workflow_stats", AsyncMock(return_value={}))
    monkeypatch.setattr(workflow_module, "publish_event", AsyncMock(return_value=None))

    req = workflow_module.WorkflowCreateRequest(
        name="new mapped workflow",
        workflowJson=_two_node_workflow_json(
            {
                "from": "prepare_message",
                "to": "transform_message",
                "order": 0,
                "mapping": {"message_text": "message_text"},
            }
        ),
    )

    result = await workflow_module.create_workflow(req)

    runtime = result.workflowJson["metadata"]["runtime"]
    assert runtime["strict_edge_mapping"] is True
    assert runtime["dataflow_mode"] == "vertex_cache"
    assert writes[0]["workflow_json"]["edges"][0]["mapping"] == {"message_text": "message_text"}


@pytest.mark.asyncio
async def test_create_workflow_rejects_schema_lint_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_workflow = Mock()
    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", write_workflow)

    workflow_json = _two_node_workflow_json(
        {
            "from": "prepare_message",
            "to": "transform_message",
            "order": 0,
            "mapping": {"message_text": "missing_message_text"},
        }
    )
    workflow_json["nodes"][0]["outputSchema"] = {"message_text": {"type": "str"}}

    req = workflow_module.WorkflowCreateRequest(
        name="bad schema workflow",
        workflowJson=workflow_json,
    )

    with pytest.raises(workflow_module.HTTPException) as exc_info:
        await workflow_module.create_workflow(req)

    assert exc_info.value.status_code == 400
    assert "Workflow schema lint failed" in str(exc_info.value.detail)
    assert "schema_mapping_src_not_declared" in str(exc_info.value.detail)
    write_workflow.assert_not_called()


@pytest.mark.asyncio
async def test_update_workflow_rejects_unmapped_edges_when_strict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_workflow = Mock()
    existing = {
        "id": "wf-1",
        "name": "existing workflow",
        "category": "default",
        "status": "draft",
        "createdAt": 1,
        "updatedAt": 1,
        "source": "global",
        "workflowJson": _minimal_workflow_json(
            {"runtime": {"strict_edge_mapping": True, "dataflow_mode": "vertex_cache"}}
        ),
        "markdownContent": None,
        "editMarkdownContent": None,
    }

    monkeypatch.setattr(workflow_module, "_read_workflow_from_fs", lambda _workflow_id: dict(existing))
    monkeypatch.setattr(workflow_module, "_write_workflow_to_fs", write_workflow)

    req = workflow_module.WorkflowUpdateRequest(
        workflowJson={
            **_two_node_workflow_json(
                {"from": "prepare_message", "to": "transform_message", "order": 0}
            ),
            "metadata": {"runtime": {"strict_edge_mapping": True, "dataflow_mode": "vertex_cache"}},
        }
    )

    with pytest.raises(workflow_module.HTTPException) as exc_info:
        await workflow_module.update_workflow("wf-1", req)

    assert exc_info.value.status_code == 400
    assert "Workflow strict edge mapping failed" in str(exc_info.value.detail)
    assert "prepare_message" in str(exc_info.value.detail)
    write_workflow.assert_not_called()


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
