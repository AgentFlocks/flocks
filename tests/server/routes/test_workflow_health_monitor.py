from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from flocks.server.routes import workflow as workflow_routes


@pytest.fixture(autouse=True)
def clear_workflow_health_cache() -> None:
    workflow_routes._workflow_api_health_cache.clear()


@pytest.mark.asyncio
async def test_workflow_api_health_cache_has_process_wide_probe_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_ids = [f"wf-{index}" for index in range(7)]
    active = 0
    peak = 0

    async def fake_list_keys(_prefix: str) -> list[str]:
        return [workflow_routes._api_service_key(workflow_id) for workflow_id in workflow_ids]

    async def fake_kv_get(key: Any, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        workflow_id = str(key).removeprefix(workflow_routes._API_SERVICE_PREFIX)
        return {"workflowId": workflow_id, "status": "running"}

    async def fake_health(_workflow_id: str) -> dict[str, Any]:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"ok": True}

    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_list_keys", fake_list_keys)
    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_get", fake_kv_get)
    monkeypatch.setattr(workflow_routes, "get_workflow_health", fake_health)

    result = await workflow_routes.refresh_workflow_api_health_cache()

    assert result == {"checked": 7, "healthy": 7}
    assert peak == workflow_routes._WORKFLOW_API_HEALTH_PROBE_CONCURRENCY
    assert workflow_routes._workflow_api_health_cache == dict.fromkeys(workflow_ids, True)


@pytest.mark.asyncio
async def test_workflow_integration_status_summarizes_running_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-running"
    workflow_data = {
        "id": workflow_id,
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python"}],
            "edges": [],
            "triggers": [
                {"id": "schedule-default", "type": "schedule", "enabled": True},
                {"id": "webhook-default", "type": "webhook", "enabled": True},
            ],
        },
    }

    async def fake_kv_get(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        if str(key) == workflow_routes._api_service_key(workflow_id):
            return {"workflowId": workflow_id, "status": "running"}
        if str(key) == workflow_routes._runtime_key_main(workflow_id):
            return {"status": "running"}
        return None

    async def fake_trigger_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"triggerId": "webhook-default", "state": "ready"},
            {"triggerId": "schedule-default", "state": "running"},
        ]

    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_get", fake_kv_get)
    workflow_routes._workflow_api_health_cache[workflow_id] = True
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=fake_trigger_statuses),
    )

    status = await workflow_routes._get_workflow_integration_status(workflow_id, workflow_data)

    assert status.model_dump() == {
        "api": {"configured": True, "state": "running"},
        "trigger": {
            "configured": True,
            "state": "running",
            "count": 2,
            "items": [
                {
                    "id": "schedule-default",
                    "type": "schedule",
                    "name": None,
                    "state": "running",
                    "rawState": "running",
                },
                {
                    "id": "webhook-default",
                    "type": "webhook",
                    "name": None,
                    "state": "running",
                    "rawState": "ready",
                },
            ],
        },
    }


@pytest.mark.asyncio
async def test_workflow_integration_status_marks_unhealthy_api_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-unhealthy"
    workflow_data = {
        "id": workflow_id,
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python"}],
            "edges": [],
            "triggers": [],
        },
    }

    async def fake_kv_get(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        if str(key) == workflow_routes._api_service_key(workflow_id):
            return {"workflowId": workflow_id, "status": "running"}
        if str(key) == workflow_routes._runtime_key_main(workflow_id):
            return {"status": "running"}
        return None

    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_get", fake_kv_get)
    workflow_routes._workflow_api_health_cache[workflow_id] = False

    status = await workflow_routes._get_workflow_integration_status(workflow_id, workflow_data)

    assert status.api.model_dump() == {"configured": True, "state": "error"}


@pytest.mark.asyncio
async def test_workflow_integration_status_marks_stopped_and_failed_red_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-stopped"
    workflow_data = {
        "id": workflow_id,
        "workflowJson": {
            "start": "n1",
            "nodes": [{"id": "n1", "type": "python"}],
            "edges": [],
            "triggers": [
                {"id": "schedule-default", "type": "schedule", "enabled": False},
                {"id": "kafka-default", "type": "kafka", "enabled": True},
            ],
        },
    }

    async def fake_kv_get(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        if str(key) == workflow_routes._api_service_key(workflow_id):
            return {"workflowId": workflow_id, "status": "stopped", "stoppedAt": 1}
        return None

    async def fake_trigger_statuses(_workflow_id: str, _workflow_json: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"triggerId": "schedule-default", "state": "stopped"},
            {"triggerId": "kafka-default", "state": "failed"},
        ]

    monkeypatch.setattr(workflow_routes.WorkflowStore, "kv_get", fake_kv_get)
    monkeypatch.setattr(
        workflow_routes,
        "default_trigger_runtime",
        SimpleNamespace(get_workflow_trigger_statuses=fake_trigger_statuses),
    )

    status = await workflow_routes._get_workflow_integration_status(workflow_id, workflow_data)

    assert status.model_dump() == {
        "api": {"configured": True, "state": "stopped"},
        "trigger": {
            "configured": True,
            "state": "error",
            "count": 2,
            "items": [
                {
                    "id": "schedule-default",
                    "type": "schedule",
                    "name": None,
                    "state": "stopped",
                    "rawState": "stopped",
                },
                {
                    "id": "kafka-default",
                    "type": "kafka",
                    "name": None,
                    "state": "error",
                    "rawState": "failed",
                },
            ],
        },
    }
