from __future__ import annotations

import asyncio
from typing import Any

import pytest

from flocks.workflow import poller_manager
from flocks.workflow.runner import RunWorkflowResult


@pytest.mark.asyncio
async def test_restart_disabled_config_reports_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()

    async def _fake_read(_key: str) -> dict[str, Any]:
        return {"enabled": False}

    monkeypatch.setattr(poller_manager.Storage, "read", _fake_read)

    status = await manager.restart_workflow("wf-disabled")
    assert status["state"] == "stopped"
    assert status["error"] is None


@pytest.mark.asyncio
async def test_restart_missing_workflow_reports_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()

    async def _fake_read(_key: str) -> dict[str, Any]:
        return {"enabled": True, "intervalSeconds": 30}

    monkeypatch.setattr(poller_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(poller_manager, "read_workflow_from_fs", lambda _workflow_id: None)

    status = await manager.restart_workflow("wf-missing")
    assert status["state"] == "failed"
    assert status["error"] == "workflow_not_found"


@pytest.mark.asyncio
async def test_run_once_injects_dynamic_inputs_and_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    captured_inputs: dict[str, Any] = {}

    async def _fake_read(_key: str) -> dict[str, Any]:
        return {
            "enabled": False,
            "timeoutSeconds": 9,
            "inputs": {"dedup_source_workflow_name": "stream_alert_denoise_gt_fast"},
        }

    def _fake_run_workflow(*, workflow: Any, inputs: dict[str, Any], timeout_s: int, trace: bool, cancel):  # noqa: ANN001
        captured_inputs.update(inputs)
        assert workflow == {"start": "n1", "nodes": [], "edges": []}
        assert timeout_s == 9
        assert trace is False
        assert cancel() is False
        return RunWorkflowResult(
            status="success",
            run_id="run-1",
            outputs={
                "load_stats": {"record_count": 7},
                "processed_mark_count": 3,
                "channel_notify_status": "sent",
            },
        )

    monkeypatch.setattr(poller_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}},
    )
    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)

    status = await manager.run_once("wf-run-once")

    assert status["lastStatus"] == "success"
    assert status["selectedCount"] == 7
    assert status["processedMarkCount"] == 3
    assert status["channelNotifyStatus"] == "sent"
    assert status["state"] == "stopped"
    assert captured_inputs["dedup_source_workflow_name"] == "stream_alert_denoise_gt_fast"
    assert captured_inputs["input_date"]
    assert captured_inputs["_trigger"] == "poller"
    assert captured_inputs["_poller_run_id"].startswith("poller-")


@pytest.mark.asyncio
async def test_no_overlap_skips_when_previous_run_is_still_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = poller_manager.WorkflowPollerManager()
    threading_event = asyncio.Event()

    config = {
        "enabled": True,
        "intervalSeconds": 1,
        "timeoutSeconds": 5,
        "noOverlap": True,
        "inputs": {},
    }

    def _fake_run_workflow(*, workflow: Any, inputs: dict[str, Any], timeout_s: int, trace: bool, cancel):  # noqa: ANN001
        _ = workflow, inputs, timeout_s, trace, cancel
        # Keep the run active until the test releases it so a second tick skips.
        asyncio.run(asyncio.wait_for(threading_event.wait(), timeout=2.0))
        return RunWorkflowResult(status="success", outputs={"load_stats": {"record_count": 1}})

    monkeypatch.setattr(poller_manager, "run_workflow", _fake_run_workflow)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}},
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
async def test_start_all_only_restarts_enabled_configs(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    restarted: list[str] = []

    async def _fake_list_keys(_prefix: str) -> list[str]:
        return [
            "workflow_poller_config/wf-enabled",
            "workflow_poller_config/wf-disabled",
        ]

    async def _fake_read(key: str) -> dict[str, Any]:
        return {"enabled": key.endswith("wf-enabled")}

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        restarted.append(workflow_id)
        return {"workflowId": workflow_id, "state": "running"}

    monkeypatch.setattr(poller_manager.Storage, "list_keys", _fake_list_keys)
    monkeypatch.setattr(poller_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(manager, "restart_workflow", _fake_restart)

    await manager.start_all()
    assert restarted == ["wf-enabled"]


@pytest.mark.asyncio
async def test_restart_workflow_replaces_existing_task(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = poller_manager.WorkflowPollerManager()
    config = {"enabled": True, "intervalSeconds": 30, "timeoutSeconds": 10, "noOverlap": True, "inputs": {}}

    async def _fake_read(_key: str) -> dict[str, Any]:
        return config

    async def _fake_loop(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        await asyncio.sleep(60)

    monkeypatch.setattr(poller_manager.Storage, "read", _fake_read)
    monkeypatch.setattr(
        poller_manager,
        "read_workflow_from_fs",
        lambda _workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}},
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
