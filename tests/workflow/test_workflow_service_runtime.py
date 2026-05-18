from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

import flocks.workflow.service_runtime as service_runtime


def test_service_runtime_lifespan_initializes_and_shuts_down_mcp(
    monkeypatch,
) -> None:
    init_mock = AsyncMock()
    shutdown_mock = AsyncMock()
    manager = SimpleNamespace(shutdown=shutdown_mock)

    monkeypatch.setattr(service_runtime.MCP, "init", init_mock)
    monkeypatch.setattr(service_runtime, "get_manager", lambda: manager)

    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["workflow_id"] == "wf-1"

    init_mock.assert_awaited_once()
    shutdown_mock.assert_awaited_once()


def test_service_runtime_lifespan_tolerates_mcp_init_failure(
    monkeypatch,
) -> None:
    init_mock = AsyncMock(side_effect=RuntimeError("mcp init boom"))
    shutdown_mock = AsyncMock()
    manager = SimpleNamespace(shutdown=shutdown_mock)
    run_result = SimpleNamespace(
        status="SUCCEEDED",
        run_id="run-1",
        outputs={"ok": True},
        error=None,
    )

    monkeypatch.setattr(service_runtime.MCP, "init", init_mock)
    monkeypatch.setattr(service_runtime, "get_manager", lambda: manager)
    monkeypatch.setattr(service_runtime, "run_workflow", lambda **_kwargs: run_result)

    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post("/invoke", json={"inputs": {"ip": "8.8.8.8"}})
        assert response.status_code == 200
        assert response.json()["status"] == "SUCCEEDED"
        assert response.json()["outputs"] == {"ok": True}

    init_mock.assert_awaited_once()
    shutdown_mock.assert_awaited_once()
