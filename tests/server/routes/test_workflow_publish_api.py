from __future__ import annotations

from typing import Any

import pytest

from flocks.server.routes import workflow as workflow_routes


@pytest.mark.asyncio
async def test_publish_workflow_as_api_reuses_key_for_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    workflow_id = "wf-1"
    existing_key = "existing-api-key"
    publish_calls: list[dict[str, Any]] = []
    writes: dict[str, Any] = {}

    monkeypatch.setattr(
        workflow_routes,
        "_read_workflow_from_fs",
        lambda requested_id: {
            "id": requested_id,
            "name": "Demo Workflow",
            "workflowJson": {
                "id": requested_id,
                "start": "n1",
                "nodes": [{"id": "n1", "type": "python", "code": "outputs['ok'] = True"}],
                "edges": [],
            },
        } if requested_id == workflow_id else None,
    )
    monkeypatch.setattr(workflow_routes.Config, "get_data_path", lambda: tmp_path)

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        if str(key) == workflow_routes._api_service_key(workflow_id):
            return {"apiKey": existing_key}
        return None

    async def fake_write(key: Any, value: Any) -> None:
        writes[str(key)] = value

    async def fake_publish_workflow(
        requested_id: str,
        image: str | None = None,
        driver: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        publish_calls.append({
            "workflow_id": requested_id,
            "image": image,
            "driver": driver,
            "api_key": api_key,
        })
        return {
            "serviceUrl": "http://127.0.0.1:19000",
            "containerName": "local-wf-1",
            "driver": driver or "local",
            "apiKey": api_key,
        }

    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)
    monkeypatch.setattr(workflow_routes, "publish_workflow", fake_publish_workflow)

    result = await workflow_routes.publish_workflow_as_api(
        workflow_id,
        workflow_routes.WorkflowCenterPublishRequest(driver="local"),
    )

    assert publish_calls == [{
        "workflow_id": workflow_id,
        "image": None,
        "driver": "local",
        "api_key": existing_key,
    }]
    assert result["apiKey"] == existing_key
    assert writes[workflow_routes._api_service_key(workflow_id)]["apiKey"] == existing_key


@pytest.mark.asyncio
async def test_reconcile_published_workflow_api_services_restarts_unhealthy_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-1"
    existing_key = "existing-api-key"
    service_key = workflow_routes._api_service_key(workflow_id)
    store: dict[str, Any] = {
        service_key: {
            "workflowId": workflow_id,
            "workflowName": "Demo Workflow",
            "serviceUrl": "http://127.0.0.1:19000",
            "invokeUrl": "http://127.0.0.1:19000/invoke",
            "apiKey": existing_key,
            "status": "running",
            "driver": "docker",
            "image": "custom-image:latest",
        }
    }
    publish_calls: list[dict[str, Any]] = []

    async def fake_list_keys(prefix: str) -> list[str]:
        assert prefix == workflow_routes._API_SERVICE_PREFIX
        return list(store.keys())

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        return store.get(str(key))

    async def fake_write(key: Any, value: Any) -> None:
        store[str(key)] = value

    async def fake_health(requested_id: str) -> dict[str, Any]:
        assert requested_id == workflow_id
        return {"ok": False, "published": True, "endpointOk": False}

    async def fake_prepare_registry(requested_id: str) -> tuple[dict[str, Any], int]:
        assert requested_id == workflow_id
        return {"name": "Demo Workflow"}, 123

    async def fake_publish_workflow(
        requested_id: str,
        image: str | None = None,
        driver: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        publish_calls.append({
            "workflow_id": requested_id,
            "image": image,
            "driver": driver,
            "api_key": api_key,
        })
        return {
            "serviceUrl": "http://127.0.0.1:19001",
            "containerName": "flocks-wf-wf-1-rel-1",
            "driver": driver,
            "image": image,
            "apiKey": api_key,
        }

    monkeypatch.setattr(workflow_routes.Storage, "list_keys", fake_list_keys)
    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)
    monkeypatch.setattr(workflow_routes, "get_workflow_health", fake_health)
    monkeypatch.setattr(workflow_routes, "_prepare_workflow_api_registry", fake_prepare_registry)
    monkeypatch.setattr(workflow_routes, "publish_workflow", fake_publish_workflow)

    result = await workflow_routes.reconcile_published_workflow_api_services()

    assert result["checked"] == 1
    assert result["restarted"] == 1
    assert publish_calls == [{
        "workflow_id": workflow_id,
        "image": "custom-image:latest",
        "driver": "docker",
        "api_key": existing_key,
    }]
    assert store[service_key]["status"] == "running"
    assert store[service_key]["apiKey"] == existing_key
    assert store[service_key]["serviceUrl"] == "http://127.0.0.1:19001"
    assert store[service_key]["invokeUrl"] == "http://127.0.0.1:19001/invoke"


@pytest.mark.asyncio
async def test_reconcile_published_workflow_api_services_restarts_health_marked_stopped_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-health-stopped"
    service_key = workflow_routes._api_service_key(workflow_id)
    store: dict[str, Any] = {
        service_key: {
            "workflowId": workflow_id,
            "status": "stopped",
            "apiKey": "existing-api-key",
        }
    }
    publish_calls: list[str] = []

    async def fake_list_keys(prefix: str) -> list[str]:
        assert prefix == workflow_routes._API_SERVICE_PREFIX
        return list(store.keys())

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        return store.get(str(key))

    async def fake_write(key: Any, value: Any) -> None:
        store[str(key)] = value

    async def fake_health(requested_id: str) -> dict[str, Any]:
        assert requested_id == workflow_id
        return {"ok": False, "published": False}

    async def fake_prepare_registry(requested_id: str) -> tuple[dict[str, Any], int]:
        assert requested_id == workflow_id
        return {"name": "Demo Workflow"}, 123

    async def fake_publish_workflow(
        requested_id: str,
        image: str | None = None,
        driver: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        publish_calls.append(requested_id)
        return {
            "serviceUrl": "http://127.0.0.1:19002",
            "containerName": "local-wf-health-stopped",
            "driver": driver or "local",
            "apiKey": api_key,
        }

    monkeypatch.setattr(workflow_routes.Storage, "list_keys", fake_list_keys)
    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)
    monkeypatch.setattr(workflow_routes, "get_workflow_health", fake_health)
    monkeypatch.setattr(workflow_routes, "_prepare_workflow_api_registry", fake_prepare_registry)
    monkeypatch.setattr(workflow_routes, "publish_workflow", fake_publish_workflow)

    result = await workflow_routes.reconcile_published_workflow_api_services()

    assert result["checked"] == 1
    assert result["restarted"] == 1
    assert result["skipped"] == 0
    assert publish_calls == [workflow_id]
    assert store[service_key]["status"] == "running"


@pytest.mark.asyncio
async def test_reconcile_published_workflow_api_services_skips_manually_stopped_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-manual-stopped"
    service_key = workflow_routes._api_service_key(workflow_id)
    store: dict[str, Any] = {
        service_key: {
            "workflowId": workflow_id,
            "status": "stopped",
            "stoppedAt": 123,
            "apiKey": "existing-api-key",
        }
    }
    health_calls: list[str] = []

    async def fake_list_keys(prefix: str) -> list[str]:
        assert prefix == workflow_routes._API_SERVICE_PREFIX
        return list(store.keys())

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        return store.get(str(key))

    async def fake_health(requested_id: str) -> dict[str, Any]:
        health_calls.append(requested_id)
        return {"ok": True}

    monkeypatch.setattr(workflow_routes.Storage, "list_keys", fake_list_keys)
    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes, "get_workflow_health", fake_health)

    result = await workflow_routes.reconcile_published_workflow_api_services()

    assert result["skipped"] == 1
    assert result["checked"] == 0
    assert health_calls == []


@pytest.mark.asyncio
async def test_get_workflow_service_does_not_probe_runtime_health(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-service-read"
    service = {
        "workflowId": workflow_id,
        "status": "running",
        "serviceUrl": "http://127.0.0.1:19000",
    }
    writes: list[Any] = []
    health_calls: list[str] = []

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        assert str(key) == workflow_routes._api_service_key(workflow_id)
        return service

    async def fake_write(key: Any, value: Any) -> None:
        writes.append((key, value))

    async def fake_health(requested_id: str) -> dict[str, Any]:
        health_calls.append(requested_id)
        return {"ok": False, "published": False}

    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)
    monkeypatch.setattr(workflow_routes, "get_workflow_health", fake_health)

    result = await workflow_routes.get_workflow_service(workflow_id)

    assert result is service
    assert health_calls == []
    assert writes == []


@pytest.mark.asyncio
async def test_list_workflow_services_marks_stale_running_service_stopped_in_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_id = "wf-stale-service"
    service_key = workflow_routes._api_service_key(workflow_id)
    service = {
        "workflowId": workflow_id,
        "workflowName": "Stale Workflow",
        "serviceUrl": "http://127.0.0.1:19002",
        "invokeUrl": "http://127.0.0.1:19002/invoke",
        "apiKey": "existing-api-key",
        "status": "running",
        "publishedAt": 123,
        "driver": "local",
    }
    store: dict[str, Any] = {service_key: service}

    async def fake_list_keys(prefix: str) -> list[str]:
        assert prefix == workflow_routes._API_SERVICE_PREFIX
        return [service_key]

    async def fake_read(key: Any, *_args: Any, **_kwargs: Any) -> Any:
        return store.get(str(key))

    writes: list[tuple[Any, Any]] = []

    async def fake_write(key: Any, value: Any) -> None:
        writes.append((key, value))

    monkeypatch.setattr(workflow_routes.Storage, "list_keys", fake_list_keys)
    monkeypatch.setattr(workflow_routes.Storage, "read", fake_read)
    monkeypatch.setattr(workflow_routes.Storage, "write", fake_write)

    result = await workflow_routes.list_workflow_services()

    assert result[0]["status"] == "stopped"
    assert result[0]["health"] == {
        "ok": False,
        "stale": True,
        "reason": "missing_runtime",
    }
    assert "stoppedAt" not in result[0]
    assert store[service_key]["status"] == "running"
    assert writes == []
