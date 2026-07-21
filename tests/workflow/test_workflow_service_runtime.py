import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

import flocks.workflow.service_runtime as service_runtime
from flocks.hooks.pipeline import HookBase, HookPipeline
from flocks.tool import ToolContext


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


def test_service_runtime_invoke_builds_real_tool_context(
    monkeypatch,
) -> None:
    init_mock = AsyncMock()
    shutdown_mock = AsyncMock()
    manager = SimpleNamespace(shutdown=shutdown_mock)
    tool_context = ToolContext(session_id="session-1", message_id="message-1", agent="rex")
    build_context_mock = AsyncMock(return_value=tool_context)
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
    monkeypatch.setattr(service_runtime, "build_workflow_tool_context", build_context_mock)
    monkeypatch.setattr(service_runtime, "run_workflow", run_workflow_mock)

    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        response = client.post("/invoke", json={"inputs": {"ip": "8.8.8.8"}})

    assert response.status_code == 200
    assert response.json()["status"] == "SUCCEEDED"
    build_context_mock.assert_awaited_once_with(
        workflow_id="wf-1",
        action_name="invoke",
    )
    run_workflow_mock.assert_called_once()
    assert run_workflow_mock.call_args.kwargs["tool_context"] is tool_context


def test_service_runtime_passes_opaque_ingress_context_to_workflow_tool_context(
    monkeypatch,
) -> None:
    """The service transports hook context without interpreting its contents."""
    observed = []

    class TransferIngress(HookBase):
        async def ingress_before(self, _ctx):
            return {"context": {"workflow_transfer": "opaque-token"}}

    HookPipeline.reset()
    HookPipeline._initialized = True
    HookPipeline.register("transfer-ingress", TransferIngress())
    monkeypatch.setattr(service_runtime.MCP, "init", AsyncMock())
    monkeypatch.setattr(
        service_runtime,
        "get_manager",
        lambda: SimpleNamespace(shutdown=AsyncMock()),
    )

    async def build_context(**kwargs):
        observed.append(kwargs)
        return ToolContext(session_id="session-1", message_id="message-1")

    monkeypatch.setattr(service_runtime, "build_workflow_tool_context", build_context)
    monkeypatch.setattr(
        service_runtime,
        "run_workflow",
        Mock(return_value=SimpleNamespace(status="SUCCEEDED", run_id="r", outputs={}, error=None)),
    )
    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
    )

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post("/invoke", json={"inputs": {}})
    finally:
        HookPipeline.reset()

    assert response.status_code == 200
    assert observed == [
        {
            "workflow_id": "wf-1",
            "action_name": "invoke",
            "execution_context": {"workflow_transfer": "opaque-token"},
        }
    ]


def test_service_runtime_requires_api_key_when_configured(
    monkeypatch,
) -> None:
    init_mock = AsyncMock()
    shutdown_mock = AsyncMock()
    manager = SimpleNamespace(shutdown=shutdown_mock)
    tool_context = ToolContext(session_id="session-1", message_id="message-1", agent="rex")
    build_context_mock = AsyncMock(return_value=tool_context)
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
    monkeypatch.setattr(service_runtime, "build_workflow_tool_context", build_context_mock)
    monkeypatch.setattr(service_runtime, "run_workflow", run_workflow_mock)

    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
        api_key="secret-key",
    )

    with TestClient(app, raise_server_exceptions=True) as client:
        missing = client.post("/invoke", json={"inputs": {"ip": "8.8.8.8"}})
        wrong = client.post(
            "/invoke",
            json={"inputs": {"ip": "8.8.8.8"}},
            headers={"x-api-key": "wrong-key"},
        )
        allowed = client.post(
            "/invoke",
            json={"inputs": {"ip": "8.8.8.8"}},
            headers={"x-api-key": "secret-key"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert missing.json()["detail"] == "Invalid API key"
    assert wrong.json()["detail"] == "Invalid API key"
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "SUCCEEDED"
    build_context_mock.assert_awaited_once()
    run_workflow_mock.assert_called_once()


def test_service_runtime_emits_only_api_key_fingerprint_at_headless_ingress(
    monkeypatch,
) -> None:
    observed = []

    class IngressRecorder(HookBase):
        async def ingress_before(self, ctx):
            observed.append(dict(ctx.input))

    api_key = "service-api-key-not-for-hooks"
    key_id = f"sha256:{hashlib.sha256(api_key.encode('utf-8')).hexdigest()}"
    HookPipeline.reset()
    HookPipeline._initialized = True
    HookPipeline.register("ingress-recorder", IngressRecorder())
    monkeypatch.setattr(service_runtime.MCP, "init", AsyncMock())
    monkeypatch.setattr(
        service_runtime,
        "get_manager",
        lambda: SimpleNamespace(shutdown=AsyncMock()),
    )
    monkeypatch.setattr(
        service_runtime,
        "build_workflow_tool_context",
        AsyncMock(return_value=ToolContext(session_id="s", message_id="m")),
    )
    monkeypatch.setattr(
        service_runtime,
        "run_workflow",
        Mock(return_value=SimpleNamespace(status="SUCCEEDED", run_id="r", outputs={}, error=None)),
    )
    app = service_runtime.create_service_app(
        workflow_json={"id": "wf-1", "start": "node-1", "nodes": [], "edges": []},
        workflow_id="wf-1",
        release_id="rel-1",
        api_key=api_key,
    )

    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.post(
                "/invoke",
                json={"inputs": {"value": "raw-input"}},
                headers={"x-api-key": api_key},
            )
    finally:
        HookPipeline.reset()

    assert response.status_code == 200
    assert observed[0]["evidence"] == {
        "auth_scheme": "api_key",
        "api_key_id": key_id,
    }
    assert api_key not in repr(observed[0])
