from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from httpx import AsyncClient

from flocks.server.routes import workflow as workflow_routes


@pytest.mark.asyncio
async def test_save_poller_config_restarts_manager(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict[str, Any]]] = []

    async def _fake_write(key: Any, value: dict[str, Any]) -> None:
        writes.append((key, value))

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        assert workflow_id == "wf-1"
        return {"workflowId": workflow_id, "state": "running", "lastStatus": None}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None,
    )
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_write)
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(restart_workflow=_fake_restart),
    )

    response = await client.post(
        "/api/workflow/wf-1/poller-config",
        json={
            "enabled": True,
            "intervalSeconds": 45,
            "timeoutSeconds": 3600,
            "noOverlap": True,
            "inputs": {"persist_triage_output": True},
        },
    )

    assert response.status_code == 200, response.text
    poller_writes = [(key, value) for key, value in writes if key == "workflow_poller_config/wf-1"]
    assert poller_writes
    key, payload = poller_writes[0]
    assert key == "workflow_poller_config/wf-1"
    assert payload["enabled"] is True
    assert payload["intervalSeconds"] == 45
    assert payload["timeoutSeconds"] == 3600
    assert payload["inputs"] == {"persist_triage_output": True}


@pytest.mark.asyncio
async def test_save_poller_config_preserves_cron_schedule(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, dict[str, Any]]] = []
    persisted_sources: list[dict[str, Any]] = []

    async def _fake_write(key: Any, value: dict[str, Any]) -> None:
        writes.append((key, value))

    async def _fake_persist(
        _workflow_id: str,
        _workflow_data: dict[str, Any],
        triggers: list[Any],
    ) -> None:
        persisted_sources.extend(dict(trigger.source or {}) for trigger in triggers if trigger.type == "schedule")

    async def _fake_restart(workflow_id: str) -> dict[str, Any]:
        assert workflow_id == "wf-1"
        return {"workflowId": workflow_id, "state": "running", "cronExpression": "*/10 * * * *"}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None,
    )
    monkeypatch.setattr(workflow_routes.Storage, "write", _fake_write)
    monkeypatch.setattr(workflow_routes, "_persist_workflow_triggers", _fake_persist)
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(restart_workflow=_fake_restart),
    )

    response = await client.post(
        "/api/workflow/wf-1/poller-config",
        json={
            "enabled": True,
            "intervalSeconds": 300,
            "cronExpression": "*/10 * * * *",
            "timeoutSeconds": 3600,
            "noOverlap": True,
            "inputs": {"source": "cron"},
        },
    )

    assert response.status_code == 200, response.text
    poller_payload = next(value for key, value in writes if key == "workflow_poller_config/wf-1")
    assert poller_payload["cronExpression"] == "*/10 * * * *"
    assert persisted_sources == [
        {
            "mode": "cron",
            "intervalSeconds": 300,
            "cron": "*/10 * * * *",
        }
    ]


@pytest.mark.asyncio
async def test_get_poller_config_returns_saved_data(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_read(_key: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        if _key != "workflow_poller_config/wf-1":
            return None
        return {
            "workflowId": "wf-1",
            "enabled": True,
            "intervalSeconds": 30,
            "timeoutSeconds": 7200,
            "noOverlap": True,
            "inputs": {"dedup_source_workflow_name": "stream_alert_denoise_gt_fast"},
        }

    monkeypatch.setattr(workflow_routes.Storage, "read", _fake_read)

    response = await client.get("/api/workflow/wf-1/poller-config")
    assert response.status_code == 200, response.text
    assert response.json()["workflowId"] == "wf-1"
    assert response.json()["intervalSeconds"] == 30


@pytest.mark.asyncio
async def test_get_poller_status_returns_runtime_snapshot(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(
            get_status=lambda workflow_id: {
                "workflowId": workflow_id,
                "state": "running",
                "lastStatus": "success",
                "selectedCount": 12,
            },
        ),
    )

    response = await client.get("/api/workflow/wf-1/poller-status")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "running"
    assert response.json()["selectedCount"] == 12


@pytest.mark.asyncio
async def test_run_poller_once_returns_latest_status(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_run_once(workflow_id: str) -> dict[str, Any]:
        return {
            "workflowId": workflow_id,
            "state": "stopped",
            "lastStatus": "success",
            "selectedCount": 5,
        }

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda workflow_id: {"workflowJson": {"start": "n1", "nodes": [], "edges": []}} if workflow_id == "wf-1" else None,
    )
    monkeypatch.setattr(
        "flocks.workflow.poller_manager.default_manager",
        SimpleNamespace(run_once=_fake_run_once),
    )

    response = await client.post("/api/workflow/wf-1/poller-run-once")
    assert response.status_code == 200, response.text
    assert response.json()["status"]["lastStatus"] == "success"
    assert response.json()["status"]["selectedCount"] == 5
