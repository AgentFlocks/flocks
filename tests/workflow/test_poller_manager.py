from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from flocks.workflow import poller_manager
from flocks.workflow.runner import RunWorkflowResult


@pytest.mark.asyncio
async def test_restart_disabled_config_reports_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {"enabled": False}

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)

    status = await manager.restart_workflow("wf-disabled")
    assert status["state"] == "stopped"
    assert status["error"] is None


@pytest.mark.asyncio
async def test_restart_missing_workflow_reports_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {"enabled": True, "intervalSeconds": 30}

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(poller_manager, "read_workflow_from_fs", lambda _workflow_id: None)

    status = await manager.restart_workflow("wf-missing")
    assert status["state"] == "failed"
    assert status["error"] == "workflow_not_found"


@pytest.mark.asyncio
async def test_run_once_injects_dynamic_inputs_and_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    captured_inputs: dict[str, Any] = {}

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "timeoutSeconds": 9,
            "inputs": {"dedup_source_workflow_name": "stream_alert_denoise_gt_fast"},
        }

    def _fake_run_workflow(  # noqa: ANN001
        *,
        workflow: Any,
        inputs: dict[str, Any],
        timeout_s: int,
        trace: bool,
        cancel,
        on_step_complete,
        run_id: str,
        execution_profile: str,
        tool_context: Any = None,
    ):
        _ = tool_context
        captured_inputs.update(inputs)
        assert workflow["start"] == "n1"
        assert workflow["nodes"][0]["id"] == "n1"
        assert timeout_s == 9
        assert trace is False
        assert run_id == "exec-wf-run-once"
        assert execution_profile == "high_frequency"
        assert cancel() is False
        return RunWorkflowResult(
            status="success",
            run_id="run-1",
            outputs={
                "load_stats": {"record_count": 7},
                "processed_mark_count": 3,
                "processed_cache_size_after": 11,
                "channel_notify_status": "sent",
            },
        )

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        },
    )
    monkeypatch.setattr(
        poller_manager,
        "create_execution_record",
        lambda workflow_id, *, input_params=None, exec_id=None, scope_key=None: asyncio.sleep(
            0,
            result={
                "id": exec_id or f"exec-{workflow_id}",
                "workflowId": workflow_id,
                "inputParams": input_params or {},
                "status": "running",
                "startedAt": 111,
                "executionLog": [],
                "currentPhase": "queued",
                "currentStepIndex": 0,
            },
        ),
    )
    monkeypatch.setattr(
        poller_manager,
        "record_execution_result",
        lambda workflow_id, exec_id, exec_data, **kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    # This unit uses fake execution records; real lease checks are covered in test_postgres_shared_execution.
    monkeypatch.setattr(poller_manager, "execution_cancelled", lambda _id: False)

    status = await manager.run_once("wf-run-once")

    assert status["lastStatus"] == "success"
    assert status["selectedCount"] == 7
    assert status["processedMarkCount"] == 11
    assert status["channelNotifyStatus"] == "sent"
    assert status["state"] == "stopped"
    assert captured_inputs["dedup_source_workflow_name"] == "stream_alert_denoise_gt_fast"
    assert captured_inputs["input_date"]
    assert captured_inputs["_trigger"] == "poller"
    assert captured_inputs["_poller_run_id"].startswith("poller-")


@pytest.mark.asyncio
async def test_run_once_records_execution_and_normalizes_business_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = poller_manager.WorkflowPollerManager()
    created_records: list[dict[str, Any]] = []
    recorded_results: list[dict[str, Any]] = []
    recorded_steps: list[tuple[int, dict[str, Any]]] = []

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "timeoutSeconds": 9,
            "inputs": {"dedup_source_workflow_name": "stream_alert_denoise_gt_fast"},
        }

    async def _fake_create_execution_record(
        workflow_id: str,
        *,
        input_params: dict[str, Any] | None = None,
        exec_id: str | None = None,
        scope_key: str | None = None,
    ) -> dict[str, Any]:
        record = {
            "id": exec_id or "exec-1",
            "workflowId": workflow_id,
            "inputParams": input_params or {},
            "status": "running",
            "startedAt": 111,
            "executionLog": [],
            "currentPhase": "queued",
            "currentStepIndex": 0,
        }
        created_records.append(record)
        return dict(record)

    async def _fake_record_execution_result(
        workflow_id: str,
        exec_id: str,
        exec_data: dict[str, Any],
        *,
        steps: list[tuple[int, dict[str, Any]]] | None = None,
    ) -> None:
        _ = workflow_id, exec_id
        recorded_results.append(dict(exec_data))
        recorded_steps.extend(steps or [])

    def _fake_run_workflow(  # noqa: ANN001
        *,
        workflow: Any,
        inputs: dict[str, Any],
        timeout_s: int,
        trace: bool,
        cancel,
        on_step_complete,
        run_id: str,
        execution_profile: str,
        tool_context: Any = None,
    ):
        _ = tool_context
        assert workflow["start"] == "n1"
        assert workflow["nodes"][0]["id"] == "n1"
        assert timeout_s == 9
        assert trace is False
        assert run_id == "exec-1"
        assert execution_profile == "high_frequency"
        assert cancel() is False
        assert inputs["dedup_source_workflow_name"] == "stream_alert_denoise_gt_fast"
        on_step_complete(
            SimpleNamespace(
                model_dump=lambda mode="json": {
                    "node_id": "load",
                    "node_type": "python",
                    "inputs": {"iteration": 1, "total_iterations": 2},
                    "outputs": {"load_stats": {"record_count": 9}},
                }
            ),
        )
        return RunWorkflowResult(
            status="SUCCEEDED",
            run_id="run-1",
            steps=2,
            last_node_id="finish",
            outputs={
                "workflow_success": False,
                "reason": "business rule blocked",
                "load_stats": {"record_count": 9},
            },
        )

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        },
    )
    monkeypatch.setattr(poller_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(poller_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    # This unit uses fake execution records; real lease checks are covered in test_postgres_shared_execution.
    monkeypatch.setattr(poller_manager, "execution_cancelled", lambda _id: False)

    status = await manager.run_once("wf-business-failure")

    assert created_records[0]["inputParams"]["_trigger"] == "poller"
    assert created_records[0]["inputParams"]["_poller_run_id"].startswith("poller-")
    assert recorded_results[0]["status"] == "error"
    assert recorded_results[0]["errorMessage"] == "business rule blocked"
    assert recorded_results[0]["currentPhase"] == "error"
    assert recorded_results[0]["executionLog"] == []
    assert recorded_results[0]["stepCount"] == 1
    assert recorded_results[0]["loopProgress"]["total_iterations"] == 2
    assert recorded_steps == [
        (
            1,
            {
                "node_id": "load",
                "node_type": "python",
                "inputs": {"iteration": 1, "total_iterations": 2},
                "outputs": {"load_stats": {"record_count": 9}},
            },
        )
    ]
    assert status["lastStatus"] == "error"
    assert status["lastError"] == "business rule blocked"
    assert status["selectedCount"] == 9


@pytest.mark.asyncio
async def test_no_overlap_skips_when_previous_run_is_still_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = poller_manager.WorkflowPollerManager()
    threading_event = threading.Event()

    config = {
        "enabled": True,
        "intervalSeconds": 1,
        "timeoutSeconds": 5,
        "noOverlap": True,
        "inputs": {},
    }

    def _fake_run_workflow(  # noqa: ANN001
        *,
        workflow: Any,
        inputs: dict[str, Any],
        timeout_s: int,
        trace: bool,
        cancel,
        on_step_complete,
        run_id: str,
        execution_profile: str,
        tool_context: Any = None,
    ):
        _ = workflow, inputs, timeout_s, trace, cancel, run_id, tool_context
        _ = on_step_complete
        assert execution_profile == "high_frequency"
        # Keep the run active until the test releases it so a second tick skips.
        assert threading_event.wait(timeout=2.0)
        return RunWorkflowResult(status="success", outputs={"load_stats": {"record_count": 1}})

    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    # This unit uses fake execution records; real lease checks are covered in test_postgres_shared_execution.
    monkeypatch.setattr(poller_manager, "execution_cancelled", lambda _id: False)
    monkeypatch.setattr(
        poller_manager,
        "create_execution_record",
        lambda workflow_id, *, input_params=None, exec_id=None, scope_key=None: asyncio.sleep(
            0,
            result={
                "id": exec_id or f"exec-{workflow_id}",
                "workflowId": workflow_id,
                "inputParams": input_params or {},
                "status": "running",
                "startedAt": 111,
                "executionLog": [],
                "currentPhase": "queued",
                "currentStepIndex": 0,
            },
        ),
    )
    monkeypatch.setattr(
        poller_manager,
        "record_execution_result",
        lambda workflow_id, exec_id, exec_data, **kwargs: asyncio.sleep(0),
    )
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        },
    )

    await manager._schedule_run("wf-overlap", {"start": "n1", "nodes": [], "edges": []}, config)
    await asyncio.sleep(0.02)
    await manager._schedule_run("wf-overlap", {"start": "n1", "nodes": [], "edges": []}, config)
    status = manager.get_status("wf-overlap")

    threading_event.set()
    await asyncio.sleep(0.02)

    assert status["lastStatus"] == "skipped"
    assert status["lastError"] == "previous_run_still_active"


@pytest.mark.asyncio
async def test_stop_workflow_keeps_unfinished_run_tracked_until_thread_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = poller_manager.WorkflowPollerManager()
    release_run = threading.Event()

    async def _fake_create_execution_record(
        workflow_id: str,
        *,
        input_params: dict[str, Any] | None = None,
        exec_id: str | None = None,
        scope_key: str | None = None,
    ) -> dict[str, Any]:
        _ = input_params
        return {
            "id": exec_id or f"exec-{workflow_id}",
            "workflowId": workflow_id,
            "status": "running",
            "startedAt": 111,
            "executionLog": [],
            "currentPhase": "queued",
            "currentStepIndex": 0,
        }

    async def _fake_record_execution_result(
        workflow_id: str,
        exec_id: str,
        exec_data: dict[str, Any],
        *,
        steps: list[tuple[int, dict[str, Any]]] | None = None,
    ) -> None:
        _ = workflow_id, exec_id, exec_data, steps

    def _fake_run_workflow(  # noqa: ANN001
        *,
        workflow: Any,
        inputs: dict[str, Any],
        timeout_s: int,
        trace: bool,
        cancel,
        on_step_complete,
        run_id: str,
        execution_profile: str,
        tool_context: Any = None,
    ):
        _ = workflow, inputs, timeout_s, trace, cancel, run_id, tool_context
        _ = on_step_complete
        assert execution_profile == "high_frequency"
        release_run.wait(0.2)
        return RunWorkflowResult(status="SUCCEEDED", run_id="run-stop")

    monkeypatch.setattr(poller_manager, "RUN_SHUTDOWN_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(poller_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(poller_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    # This unit uses fake execution records; real lease checks are covered in test_postgres_shared_execution.
    monkeypatch.setattr(poller_manager, "execution_cancelled", lambda _id: False)

    await manager._schedule_run(
        "wf-stop",
        {"start": "n1", "nodes": [], "edges": []},
        {"enabled": True, "intervalSeconds": 1, "timeoutSeconds": 5, "noOverlap": True, "inputs": {}},
    )
    await asyncio.sleep(0.02)

    await manager.stop_workflow("wf-stop")
    assert manager.get_status("wf-stop")["activeRuns"] == 1

    release_run.set()
    # Up to 10 s: a loaded machine can take well over a second to finish the
    # worker thread and its bookkeeping (it took longer than 1 s under the full suite).
    for _ in range(1000):
        if manager.get_status("wf-stop")["activeRuns"] == 0:
            break
        await asyncio.sleep(0.01)
    assert manager.get_status("wf-stop")["activeRuns"] == 0


@pytest.mark.asyncio
async def test_start_all_only_restarts_enabled_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    restarted: list[str] = []

    async def _fake_list_configs(*, kind: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            ("wf-enabled", {"enabled": True}),
            ("wf-disabled", {"enabled": False}),
        ]

    async def _fake_restart(
        workflow_id: str,
        *,
        startup: bool = False,
    ) -> dict[str, Any]:
        assert startup is True
        restarted.append(workflow_id)
        return {"workflowId": workflow_id, "state": "running"}

    monkeypatch.setattr(poller_manager.WorkflowStore, "list_configs", _fake_list_configs)
    monkeypatch.setattr(manager, "restart_workflow", _fake_restart)

    await manager.start_all()
    assert restarted == ["wf-enabled"]


@pytest.mark.asyncio
async def test_restart_workflow_replaces_existing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    config = {"enabled": True, "intervalSeconds": 30, "timeoutSeconds": 10, "noOverlap": True, "inputs": {}}

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return config

    async def _fake_loop(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        await asyncio.sleep(60)

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {
            "workflowJson": {
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            }
        },
    )
    monkeypatch.setattr(manager, "_poller_loop", _fake_loop)

    first = await manager.restart_workflow("wf-restart")
    first_task = manager._tasks["wf-restart"]
    second = await manager.restart_workflow("wf-restart")
    second_task = manager._tasks["wf-restart"]

    assert first["state"] == "running"
    assert second["state"] == "running"
    assert first_task is not second_task
    assert first_task.cancelled() or first_task.done()

    await manager.stop_workflow("wf-restart")


def _patch_scheduled_run(monkeypatch: pytest.MonkeyPatch, workflow_json: dict[str, Any], *, fail: bool = False):
    created: list[dict[str, Any]] = []
    recorded: list[dict[str, Any]] = []

    async def _fake_get_config(_workflow_id: str, *, kind: str) -> dict[str, Any]:
        return {"enabled": True, "intervalSeconds": 3600, "timeoutSeconds": 5}

    async def _fake_create_execution_record(workflow_id: str, *, input_params=None, exec_id=None, scope_key=None):
        record = {"id": "exec-1", "workflowId": workflow_id, "inputParams": input_params or {}}
        created.append(record)
        return dict(record)

    async def _fake_record_execution_result(workflow_id, exec_id, exec_data, *, steps=None):
        recorded.append(dict(exec_data))

    def _fake_run_workflow(**kwargs):
        if fail:
            raise RuntimeError("node crashed")
        return RunWorkflowResult(status="SUCCEEDED", run_id="run-1", steps=1, outputs={"total": 42})

    monkeypatch.setattr(poller_manager.WorkflowStore, "get_config", _fake_get_config)
    monkeypatch.setattr(poller_manager, "read_workflow_from_fs", lambda _workflow_id: {"workflowJson": workflow_json})
    monkeypatch.setattr(poller_manager, "create_execution_record", _fake_create_execution_record)
    monkeypatch.setattr(poller_manager, "record_execution_result", _fake_record_execution_result)
    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    return created, recorded


_SCHEDULED_WORKFLOW = {
    "start": "n1",
    "nodes": [{"id": "n1", "type": "python", "code": "outputs['total'] = 42"}],
    "edges": [],
    "triggers": [{"id": "autotest-schedule", "type": "schedule", "source": {"mode": "interval", "intervalSeconds": 3600}}],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("fail", [False, True])
async def test_scheduled_run_history_keeps_the_trigger_id(monkeypatch: pytest.MonkeyPatch, fail: bool) -> None:
    """The loop path (restart -> first tick) used to write schedule-default (FL-BUG-009)."""
    manager = poller_manager.WorkflowPollerManager()
    created, recorded = _patch_scheduled_run(monkeypatch, _SCHEDULED_WORKFLOW, fail=fail)

    await manager.restart_workflow("wf-schedule")
    for _ in range(200):
        if recorded:
            break
        await asyncio.sleep(0.01)
    await manager.stop_workflow("wf-schedule")

    assert created[0]["inputParams"]["_flocks"]["trigger"]["id"] == "autotest-schedule"
    assert recorded[0]["triggerId"] == "autotest-schedule"
    assert recorded[0]["status"] == ("error" if fail else "success")


@pytest.mark.asyncio
async def test_run_once_uses_the_trigger_id_and_legacy_configs_keep_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = poller_manager.WorkflowPollerManager()
    _created, recorded = _patch_scheduled_run(monkeypatch, _SCHEDULED_WORKFLOW)
    await manager.run_once("wf-schedule")
    assert recorded[-1]["triggerId"] == "autotest-schedule"

    legacy = {key: value for key, value in _SCHEDULED_WORKFLOW.items() if key != "triggers"}
    _created, recorded = _patch_scheduled_run(monkeypatch, legacy)
    await manager.run_once("wf-legacy")
    assert recorded[-1]["triggerId"] == "schedule-default"
