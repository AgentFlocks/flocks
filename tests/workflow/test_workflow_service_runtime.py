from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        assert response.json() == {
            "ok": True,
            "mcp_ready": True,
            "mcp_error": None,
            "workflow_id": "wf-1",
            "release_id": "rel-1",
        }

    init_mock.assert_awaited_once()
    shutdown_mock.assert_awaited_once()


def test_service_runtime_lifespan_reports_mcp_init_failure(
    monkeypatch,
) -> None:
    init_mock = AsyncMock(side_effect=RuntimeError("mcp init boom"))
    shutdown_mock = AsyncMock()
    manager = SimpleNamespace(shutdown=shutdown_mock)
    run_workflow_mock = Mock(
        return_value=SimpleNamespace(
            status="SUCCEEDED",
            run_id="run-1",
            outputs={"ok": True},
            error=None,
        )
    )

    monkeypatch.setattr(service_runtime.MCP, "init", init_mock)
    monkeypatch.setattr(service_runtime, "get_manager", lambda: manager)
    monkeypatch.setattr(service_runtime, "run_workflow", run_workflow_mock)

    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        health_response = client.get("/health")
        assert health_response.status_code == 503
        assert health_response.json() == {
            "ok": False,
            "mcp_ready": False,
            "mcp_error": "mcp init boom",
            "workflow_id": "wf-1",
            "release_id": "rel-1",
        }

        invoke_response = client.post("/invoke", json={"inputs": {"ip": "8.8.8.8"}})
        assert invoke_response.status_code == 503
        assert invoke_response.json()["detail"]["status"] == "FAILED"
        assert invoke_response.json()["detail"]["error"] == "mcp init boom"

    init_mock.assert_awaited_once()
    shutdown_mock.assert_awaited_once()
    run_workflow_mock.assert_not_called()
