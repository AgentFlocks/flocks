import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from flocks.mcp import MCP
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


def _progress_writer(exec_id: str, **updates):
    summary = {
        "id": exec_id,
        "workflowId": "wf-1",
        "status": "running",
        "executionLog": [],
    }
    summary.update(updates)
    return workflow_module.ExecutionProgressWriter(summary)


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
        workflowJson=_two_node_workflow_json({"from": "prepare_message", "to": "transform_message", "order": 0}),
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
            **_two_node_workflow_json({"from": "prepare_message", "to": "transform_message", "order": 0}),
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
    step_result = SimpleNamespace(
        model_dump=lambda mode: {
            "node_id": "node-1",
            "node_type": "tool",
            "inputs": {},
            "outputs": {"ok": True},
        }
    )

    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="tool"),
            {},
        )
        kwargs["on_step_complete"](step_result)
        return SimpleNamespace(
            outputs={"ok": True},
            history=[],
            last_node_id="node-1",
            steps=1,
        )

    run_mock = Mock(side_effect=run_workflow_mock)
    record_result = AsyncMock(return_value=None)
    upsert_execution = AsyncMock(return_value=None)
    monkeypatch.setattr(MCP, "init", init_mock)
    monkeypatch.setattr(workflow_module, "run_workflow", run_mock)
    monkeypatch.setattr(workflow_module.WorkflowStore, "upsert_execution", upsert_execution)
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(workflow_module, "compact_outputs_for_storage", lambda value: value)
    monkeypatch.setattr(workflow_module, "compact_history_for_storage", lambda value: value)

    req = workflow_module.WorkflowRunRequest(inputs={"ip": "8.8.8.8"}, trace=False)
    tool_context = ToolContext(session_id="session-1", message_id="message-1", agent="rex")
    progress_writer = _progress_writer("exec-1")

    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=req,
        exec_id="exec-1",
        cancel_event=workflow_module.threading.Event(),
        progress_writer=progress_writer,
        tool_context=tool_context,
    )

    init_mock.assert_not_awaited()
    run_mock.assert_called_once()
    assert run_mock.call_args.kwargs["tool_context"] is tool_context
    assert upsert_execution.await_count >= 1
    assert all(call.args[0]["executionLog"] == [] for call in upsert_execution.await_args_list)
    assert upsert_execution.await_args.args[0]["currentNodeId"] == "node-1"
    record_result.assert_awaited_once()
    expected_step = {
        "node_id": "node-1",
        "node_type": "tool",
        "inputs": {},
        "outputs": {"ok": True},
    }
    assert record_result.await_args.args[2]["executionLog"] == [expected_step]
    assert record_result.await_args.kwargs["steps"] == [(1, expected_step)]


@pytest.mark.asyncio
async def test_run_workflow_execution_task_batches_cancelled_pending_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="tool"),
            {"message": "hello"},
        )
        return SimpleNamespace(
            run_id="run-1",
            outputs={},
            history=[],
            last_node_id="node-1",
            steps=0,
        )

    record_result = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_module, "run_workflow", Mock(side_effect=run_workflow_mock))
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(
        workflow_module.WorkflowStore,
        "upsert_execution",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(workflow_module, "compact_outputs_for_storage", lambda value: value)
    monkeypatch.setattr(workflow_module, "compact_history_for_storage", lambda value: value)

    cancel_event = workflow_module.threading.Event()
    cancel_event.set()
    progress_writer = _progress_writer("exec-cancelled")
    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=workflow_module.WorkflowRunRequest(inputs={"message": "hello"}, trace=False),
        exec_id="exec-cancelled",
        cancel_event=cancel_event,
        progress_writer=progress_writer,
    )

    record_result.assert_awaited_once()
    final_data = record_result.await_args.args[2]
    assert final_data["status"] == "cancelled"
    assert final_data["stepCount"] == 1
    pending_step = {
        "node_id": "node-1",
        "node_type": "tool",
        "inputs": {"message": "hello"},
        "outputs": {},
        "error": "Run cancelled before node completed",
    }
    assert final_data["executionLog"] == [pending_step]
    assert record_result.await_args.kwargs["steps"] == [(1, pending_step)]


@pytest.mark.asyncio
async def test_run_workflow_execution_task_keeps_completed_and_pending_step_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancel_event = workflow_module.threading.Event()
    completed_step = SimpleNamespace(
        model_dump=lambda mode: {
            "node_id": "node-1",
            "node_type": "python",
            "inputs": {"value": 1},
            "outputs": {"value": 2},
        }
    )

    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="python"),
            {"value": 1},
        )
        kwargs["on_step_complete"](completed_step)
        cancel_event.set()
        kwargs["on_step_start"](
            "run-1",
            2,
            SimpleNamespace(id="node-2", type="tool"),
            {"message": "hello"},
        )
        return SimpleNamespace(
            run_id="run-1",
            outputs={"value": 2},
            history=[],
            last_node_id="node-2",
            steps=1,
        )

    record_result = AsyncMock(return_value=None)
    upsert_execution = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_module, "run_workflow", Mock(side_effect=run_workflow_mock))
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(workflow_module.WorkflowStore, "upsert_execution", upsert_execution)

    progress_writer = _progress_writer("exec-partial-cancel")
    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=workflow_module.WorkflowRunRequest(inputs={"value": 1}, trace=False),
        exec_id="exec-partial-cancel",
        cancel_event=cancel_event,
        progress_writer=progress_writer,
    )

    steps = record_result.await_args.kwargs["steps"]
    assert [step_index for step_index, _ in steps] == [1, 2]
    assert [step["node_id"] for _, step in steps] == ["node-1", "node-2"]
    assert steps[1][1]["error"] == "Run cancelled before node completed"
    assert record_result.await_args.args[2]["status"] == "cancelled"
    assert upsert_execution.await_args.args[0]["currentPhase"] == "cancelling"


@pytest.mark.asyncio
async def test_run_workflow_execution_task_keeps_steps_when_runner_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed_step = SimpleNamespace(
        model_dump=lambda mode: {
            "node_id": "node-1",
            "node_type": "python",
            "inputs": {},
            "outputs": {"ok": True},
        }
    )

    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="python"),
            {},
        )
        kwargs["on_step_complete"](completed_step)
        kwargs["on_step_start"](
            "run-1",
            2,
            SimpleNamespace(id="node-2", type="tool"),
            {"message": "hello"},
        )
        raise RuntimeError("runner failed")

    record_result = AsyncMock(return_value=None)
    monkeypatch.setattr(workflow_module, "run_workflow", Mock(side_effect=run_workflow_mock))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(
        workflow_module.WorkflowStore,
        "upsert_execution",
        AsyncMock(return_value=None),
    )

    progress_writer = _progress_writer("exec-runner-error")
    await workflow_module._run_workflow_execution_task(
        workflow_id="wf-1",
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        req=workflow_module.WorkflowRunRequest(inputs={}, trace=False),
        exec_id="exec-runner-error",
        cancel_event=workflow_module.threading.Event(),
        progress_writer=progress_writer,
    )

    final_data = record_result.await_args.args[2]
    steps = record_result.await_args.kwargs["steps"]
    assert final_data["status"] == "error"
    assert final_data["errorMessage"] == "runner failed"
    assert [step_index for step_index, _ in steps] == [1, 2]
    assert [step["node_id"] for _, step in steps] == ["node-1", "node-2"]


@pytest.mark.asyncio
async def test_run_workflow_execution_task_does_not_reclassify_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    step_result = SimpleNamespace(
        model_dump=lambda mode: {
            "node_id": "node-1",
            "node_type": "python",
            "inputs": {},
            "outputs": {"ok": True},
        }
    )

    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="python"),
            {},
        )
        kwargs["on_step_complete"](step_result)
        return SimpleNamespace(
            outputs={"ok": True},
            history=[],
            last_node_id="node-1",
            steps=1,
        )

    record_result = AsyncMock(side_effect=RuntimeError("storage failed"))
    monkeypatch.setattr(workflow_module, "run_workflow", Mock(side_effect=run_workflow_mock))
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result)
    monkeypatch.setattr(
        workflow_module.WorkflowStore,
        "upsert_execution",
        AsyncMock(return_value=None),
    )

    progress_writer = _progress_writer("exec-storage-error")

    with pytest.raises(RuntimeError, match="storage failed"):
        await workflow_module._run_workflow_execution_task(
            workflow_id="wf-1",
            workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
            req=workflow_module.WorkflowRunRequest(inputs={}, trace=False),
            exec_id="exec-storage-error",
            cancel_event=workflow_module.threading.Event(),
            progress_writer=progress_writer,
        )

    record_result.assert_awaited_once()
    final_data = record_result.await_args.args[2]
    assert final_data["status"] == "success"
    assert record_result.await_args.kwargs["steps"] == [
        (
            1,
            {
                "node_id": "node-1",
                "node_type": "python",
                "inputs": {},
                "outputs": {"ok": True},
            },
        )
    ]


@pytest.mark.asyncio
async def test_run_workflow_callbacks_do_not_wait_for_blocked_progress_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_started = asyncio.Event()
    release_write = asyncio.Event()
    runner_finished = workflow_module.threading.Event()
    write_order: list[str] = []
    step_result = SimpleNamespace(
        model_dump=lambda mode: {
            "node_id": "node-1",
            "node_type": "python",
            "inputs": {},
            "outputs": {"ok": True},
        }
    )

    async def blocked_upsert(_summary):
        write_order.append("progress-start")
        write_started.set()
        await release_write.wait()
        write_order.append("progress-end")

    async def record_result(*args, **kwargs):  # noqa: ANN002, ANN003
        write_order.append("final")

    def run_workflow_mock(**kwargs):
        kwargs["on_step_start"](
            "run-1",
            1,
            SimpleNamespace(id="node-1", type="python"),
            {},
        )
        kwargs["on_step_complete"](step_result)
        runner_finished.set()
        return SimpleNamespace(
            outputs={"ok": True},
            history=[],
            last_node_id="node-1",
            steps=1,
        )

    record_result_mock = AsyncMock(side_effect=record_result)
    monkeypatch.setattr(workflow_module, "run_workflow", Mock(side_effect=run_workflow_mock))
    monkeypatch.setattr(workflow_module, "_resolve_execution_outcome", lambda _result: ("success", None))
    monkeypatch.setattr(workflow_module, "_record_execution_result", record_result_mock)
    monkeypatch.setattr(workflow_module.WorkflowStore, "upsert_execution", blocked_upsert)

    progress_writer = _progress_writer("exec-blocked-progress")
    task = asyncio.create_task(
        workflow_module._run_workflow_execution_task(
            workflow_id="wf-1",
            workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
            req=workflow_module.WorkflowRunRequest(inputs={}, trace=False),
            exec_id="exec-blocked-progress",
            cancel_event=workflow_module.threading.Event(),
            progress_writer=progress_writer,
        )
    )

    await write_started.wait()
    assert await asyncio.to_thread(runner_finished.wait, 0.1)
    record_result_mock.assert_not_awaited()
    release_write.set()
    await task

    record_result_mock.assert_awaited_once()
    assert write_order[-2:] == ["progress-end", "final"]


@pytest.mark.asyncio
async def test_cancel_workflow_execution_uses_active_progress_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persisted_summaries: list[dict] = []

    async def capture_upsert(summary):
        persisted_summaries.append(dict(summary))

    monkeypatch.setattr(
        workflow_module.WorkflowStore,
        "get_execution",
        AsyncMock(
            return_value={
                "id": "exec-cancel-route",
                "workflowId": "wf-1",
                "status": "running",
                "currentPhase": "running",
                "executionLog": [],
            }
        ),
    )
    monkeypatch.setattr(workflow_module.WorkflowStore, "upsert_execution", capture_upsert)

    progress_writer = _progress_writer("exec-cancel-route", currentPhase="queued")
    progress_writer.submit({"currentPhase": "running", "currentNodeId": "node-1"})
    cancel_event = workflow_module.threading.Event()
    current_task = asyncio.current_task()
    assert current_task is not None
    workflow_module._active_workflow_executions["exec-cancel-route"] = (
        workflow_module.ActiveWorkflowExecution(
            workflow_id="wf-1",
            task=current_task,
            cancel_event=cancel_event,
            progress_writer=progress_writer,
        )
    )

    try:
        response = await workflow_module.cancel_workflow_execution(
            "wf-1",
            "exec-cancel-route",
        )
        await progress_writer.close_and_drain()
    finally:
        workflow_module._active_workflow_executions.pop("exec-cancel-route", None)

    assert response["status"] == "accepted"
    assert cancel_event.is_set()
    assert persisted_summaries[-1]["currentPhase"] == "cancelling"
    assert persisted_summaries[-1]["currentNodeId"] == "node-1"
    assert persisted_summaries[-1]["errorMessage"] == "Cancellation requested"


@pytest.mark.asyncio
async def test_workflow_tool_context_preserves_current_opaque_extension_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def build_context(**kwargs):
        observed.update(kwargs)
        return ToolContext(session_id="session-1", message_id="message-1", agent="rex")

    monkeypatch.setattr(workflow_module, "build_workflow_tool_context", build_context)
    monkeypatch.setattr(
        workflow_module,
        "current_execution_context",
        lambda: {"workflow_transfer": "opaque-pro-token"},
    )

    await workflow_module._build_workflow_tool_context(
        workflow_id="wf-1",
        action_name="run",
    )

    assert observed["execution_context"] == {"workflow_transfer": "opaque-pro-token"}


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
