"""Regression tests for /api/provider route fixes.

* GET /api/provider/{id} reports ``configured`` from the runtime provider
  (it was hard-coded ``False``).
* Models soft-deleted in flocks.json (``disabled: true``) are hidden from the
  provider model list.
* base_url is validated on configure / PUT / credentials / service-credentials.
* Saving a new key always rewrites ``options.apiKey`` to ``{secret:<id>}``.
* PATCH /api-services/{id}: bad body -> 422 (was 500), unknown service -> 404
  (PATCH used to create an entry out of thin air).
* POST /{id}/credentials for an id the system does not know -> 404 (it used
  to persist a bogus provider entry, e.g. "null").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.routes import provider as provider_routes


def _admin_client() -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id="admin-1", username="admin", role="admin", status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(provider_routes.router, prefix="/api/provider")
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch):
    """Hermetic runtime: one provider object, stubbed secret store / config writer."""
    from flocks.provider.provider import ModelCapabilities, ModelInfo

    provider = MagicMock()
    provider.name = "Fake"
    provider.is_configured.return_value = True
    provider._client = None
    models = [ModelInfo(id="kept", name="kept", provider_id="fake"), ModelInfo(id="gone", name="gone", provider_id="fake")]
    secrets = MagicMock()
    secrets.get.return_value = None
    writer_calls: list = []

    monkeypatch.setattr("flocks.security.get_secret_manager", lambda: secrets)
    monkeypatch.setattr(provider_routes, "_ensure_provider_initialized", AsyncMock())
    monkeypatch.setattr(provider_routes.Provider, "_ensure_initialized", MagicMock())
    monkeypatch.setattr(provider_routes.Provider, "get", classmethod(lambda cls, pid: provider if pid == "fake" else None))
    monkeypatch.setattr(provider_routes.Provider, "apply_config", AsyncMock())
    monkeypatch.setattr(provider_routes.Provider, "list_models", classmethod(lambda cls, pid=None: models))
    cfg = SimpleNamespace(provider={"fake": SimpleNamespace(models={"gone": {"disabled": True}, "kept": {"name": "Kept"}})})
    monkeypatch.setattr(provider_routes.Config, "get", AsyncMock(return_value=cfg))
    monkeypatch.setattr(provider_routes.Config, "clear_cache", MagicMock())
    monkeypatch.setattr(provider_routes.ConfigWriter, "get_provider_raw", classmethod(lambda cls, pid: {"options": {"baseURL": "http://existing/v1"}} if pid == "fake" else None))
    monkeypatch.setattr(provider_routes.ConfigWriter, "update_provider_field", classmethod(lambda cls, *a: writer_calls.append(("update", *a))))
    monkeypatch.setattr(provider_routes.ConfigWriter, "add_provider", classmethod(lambda cls, *a: writer_calls.append(("add", *a))))
    monkeypatch.setattr(provider_routes, "_get_provider_custom_settings", lambda _p: {})
    return {"provider": provider, "secrets": secrets, "writer": writer_calls}


def test_get_provider_reports_configured_and_hides_disabled_models(runtime):
    resp = _admin_client().get("/api/provider/fake")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert "kept" in body["models"] and "gone" not in body["models"]

    runtime["provider"].is_configured.return_value = False
    assert _admin_client().get("/api/provider/fake").json()["configured"] is False


@pytest.mark.parametrize("bad", ["ftp://x/v1", "http://169.254.169.254/", "nonsense"])
def test_base_url_is_validated_on_every_write_path(runtime, bad):
    client = _admin_client()
    responses = [
        client.post("/api/provider/fake/configure", json={"base_url": bad}),
        client.put("/api/provider/fake", json={"base_url": bad}),
        client.post("/api/provider/fake/credentials", json={"api_key": "sk-new-key-000000", "base_url": bad}),
        client.post("/api/provider/fake/service-credentials", json={"api_key": "svc-key", "base_url": bad}),
    ]
    assert [r.status_code for r in responses] == [422] * 4, [r.text for r in responses]
    assert runtime["writer"] == []
    runtime["secrets"].set.assert_not_called()


def test_new_key_rewrites_secret_reference_even_without_base_url(runtime):
    resp = _admin_client().post("/api/provider/fake/credentials", json={"api_key": "sk-new-key-000000", "secret_id": "fake_custom_secret"})
    assert resp.status_code == 200, resp.text
    runtime["secrets"].set.assert_called_once_with("fake_custom_secret", "sk-new-key-000000")
    assert ("update", "fake", "options.apiKey", "{secret:fake_custom_secret}") in runtime["writer"]


def test_unknown_provider_credentials_return_404_without_persisting(runtime, monkeypatch):
    monkeypatch.setattr(provider_routes, "_is_creatable_provider_id", lambda pid: pid == "openai")
    resp = _admin_client().post("/api/provider/null/credentials", json={"api_key": "sk-new-key-000000", "base_url": "http://127.0.0.1:5184/v1"})
    assert resp.status_code == 404, resp.text
    assert "Unknown provider" in resp.json()["detail"]
    runtime["secrets"].set.assert_not_called()
    assert runtime["writer"] == []


def test_is_creatable_provider_id_accepts_catalog_and_generic_ids(monkeypatch):
    monkeypatch.setattr("flocks.provider.model_catalog.get_raw_catalog", lambda: {"openai": {}})
    monkeypatch.setattr(provider_routes.Provider, "get", classmethod(lambda cls, pid: object() if pid == "registered" else None))
    assert provider_routes._is_creatable_provider_id("openai")
    assert provider_routes._is_creatable_provider_id("openai-compatible")
    assert provider_routes._is_creatable_provider_id("registered")
    assert not provider_routes._is_creatable_provider_id("null")
    assert not provider_routes._is_creatable_provider_id("custom-typo")


@pytest.mark.asyncio
async def test_patch_api_service_bad_body_is_422_and_unknown_is_404(monkeypatch):
    class Registry:
        @staticmethod
        async def init_async():
            return None

        @staticmethod
        def get_api_service_ids():
            return ["known-svc"]

    monkeypatch.setattr("flocks.tool.registry.ToolRegistry", Registry)
    monkeypatch.setattr(provider_routes.ConfigWriter, "get_api_service_raw", classmethod(lambda cls, pid: None))
    written: list = []
    monkeypatch.setattr(provider_routes.ConfigWriter, "set_api_service", classmethod(lambda cls, pid, data: written.append((pid, data))))

    with pytest.raises(HTTPException) as bad:
        await provider_routes.update_api_service_route("known-svc", {"enabled": "maybe"})
    assert bad.value.status_code == 422

    with pytest.raises(HTTPException) as unknown:
        await provider_routes.update_api_service_route("ghost-svc", {"enabled": False})
    assert unknown.value.status_code == 404
    assert written == []
