"""Authorization boundaries for global provider and Hub mutations."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from flocks.auth.context import AuthUser


def _user_client(router, *, prefix: str, role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(
            id=f"{role}-test",
            username=f"{role}-test",
            role=role,
            status="active",
            must_reset_password=False,
        )
        return await call_next(request)

    app.include_router(router, prefix=prefix)
    return TestClient(app, raise_server_exceptions=True)


def test_provider_global_configuration_credentials_and_tests_require_admin():
    from flocks.server.routes.provider import router

    client = _user_client(router, prefix="/api/provider", role="member")
    responses = [
        client.post("/api/provider/example/oauth/authorize?method=0"),
        client.post("/api/provider/example/oauth/callback?method=0"),
        client.post("/api/provider/example/configure", json={}),
        client.post("/api/provider/example/test"),
        client.put("/api/provider/example", json={}),
        client.get("/api/provider/example/credentials"),
        client.post("/api/provider/example/credentials/reveal"),
        client.post("/api/provider/example/credentials", json={"api_key": "secret"}),
        client.delete("/api/provider/example/credentials"),
        client.get("/api/provider/example/service-credentials"),
        client.post("/api/provider/example/service-credentials", json={"api_key": "secret"}),
        client.post("/api/provider/example/test-credentials", json={}),
        client.patch("/api/provider/api-services/example", json={"enabled": False}),
        client.delete("/api/provider/api-services/example"),
        client.post("/api/provider/api-services/refresh"),
    ]

    assert [response.status_code for response in responses] == [403] * len(responses)


def test_admin_reveal_returns_raw_llm_key_with_audit_and_no_store(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import provider as provider_routes

    raw_llm_key = "sk-raw-llm-secret-1234"
    raw_service_key = "raw-service-secret-5678"
    secrets = MagicMock()
    secrets.get.side_effect = lambda key: {
        "llm-example_llm_key": raw_llm_key,
        "service-example_api_key": raw_service_key,
    }.get(key)
    audit = AsyncMock()
    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: secrets)
    monkeypatch.setattr(provider_routes, "emit_audit_event", audit)
    monkeypatch.setattr(
        provider_routes.ConfigWriter,
        "get_provider_raw",
        lambda _provider_id: None,
    )
    monkeypatch.setattr(
        provider_routes.ConfigWriter,
        "get_api_service_raw",
        lambda _provider_id: {
            "apiKey": "{secret:service-example_api_key}",
            "base_url": "https://service.example.test",
        },
    )
    monkeypatch.setattr(
        provider_routes,
        "_load_api_service_metadata_data",
        lambda _provider_id: {
            "credential_fields": [
                {
                    "key": "api_key",
                    "storage": "secret",
                    "sensitive": True,
                    "config_key": "apiKey",
                    "secret_id": "service-example_api_key",
                },
                {
                    "key": "base_url",
                    "storage": "config",
                    "config_key": "base_url",
                },
            ],
        },
    )

    client = _user_client(provider_routes.router, prefix="/api/provider", role="admin")
    llm_response = client.get("/api/provider/llm-example/credentials")
    reveal_response = client.post("/api/provider/llm-example/credentials/reveal")
    service_response = client.get("/api/provider/service-example/service-credentials")

    assert llm_response.status_code == 200
    assert reveal_response.status_code == 200
    assert service_response.status_code == 200
    llm_payload = llm_response.json()
    reveal_payload = reveal_response.json()
    service_payload = service_response.json()
    assert llm_payload["api_key"] is None
    assert llm_payload["api_key_masked"]
    assert reveal_payload["api_key"] == raw_llm_key
    assert reveal_response.headers["Cache-Control"] == "no-store"
    assert reveal_response.headers["Pragma"] == "no-cache"
    assert service_payload["api_key"] is None
    assert service_payload["api_key_masked"]
    assert service_payload["fields"]["api_key"] != raw_service_key
    assert service_payload["fields"]["base_url"] == "https://service.example.test"
    assert raw_llm_key not in llm_response.text
    assert raw_llm_key in reveal_response.text
    assert raw_service_key not in service_response.text
    audit.assert_awaited_once()
    event_type, audit_payload = audit.await_args.args
    assert event_type == "provider.credentials_reveal"
    assert audit_payload["provider_id"] == "llm-example"
    assert audit_payload["username"] == "admin-test"
    assert raw_llm_key not in repr(audit_payload)


def test_admin_provider_update_never_writes_raw_api_key_to_storage(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import provider as provider_routes

    runtime_provider = MagicMock()
    storage_write = AsyncMock()
    monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", MagicMock())
    monkeypatch.setattr(provider_routes.Provider, "get", lambda _provider_id: runtime_provider)
    monkeypatch.setattr(provider_routes.Storage, "write", storage_write)
    monkeypatch.setattr(
        provider_routes,
        "get_provider",
        AsyncMock(return_value=provider_routes.ProviderInfo(id="example", name="Example")),
    )

    client = _user_client(provider_routes.router, prefix="/api/provider", role="admin")
    response = client.put(
        "/api/provider/example",
        json={
            "api_key": "raw-update-secret",
            "base_url": "https://provider.example.test",
            "custom_settings": {"timeout": 10},
        },
    )

    assert response.status_code == 200
    storage_write.assert_awaited_once()
    stored_payload = storage_write.await_args.args[1]
    assert "api_key" not in stored_payload
    assert "raw-update-secret" not in repr(stored_payload)


def test_hub_global_mutations_require_admin():
    from flocks.server.routes.hub import router

    client = _user_client(router, prefix="/api", role="member")
    responses = [
        client.post("/api/hub/plugins/skill/example/install", json={"scope": "global"}),
        client.post("/api/hub/plugins/component/example/install/stream", json={"scope": "global"}),
        client.post("/api/hub/plugins/skill/example/update", json={"scope": "global"}),
        client.delete("/api/hub/plugins/skill/example"),
        client.post("/api/hub/refresh"),
    ]

    assert [response.status_code for response in responses] == [403, 403, 403, 403, 403]
