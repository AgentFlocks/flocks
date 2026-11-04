"""Regression tests for the V1 /api/model routes.

Before the fix the V1 routes read ``model.capabilities.get(...)`` (a pydantic
model, no ``.get``) and called ``Provider.get_model(pid, mid)`` with two
arguments, so ``GET /api/model/`` was always empty and ``GET /api/model/{id}``
always 404. ``PUT /v2/settings`` accepted lax booleans, unknown models and
non-admin callers; ``DELETE /v2/definitions`` was open to every user.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.provider.provider import ModelCapabilities, ModelInfo
from flocks.server.routes import model as model_routes

PROVIDER = "fake-prov"


def _models() -> list[ModelInfo]:
    return [
        ModelInfo(
            id="m-stream-tools",
            name="Stream Tools",
            provider_id=PROVIDER,
            capabilities=ModelCapabilities(supports_streaming=True, supports_tools=True, max_tokens=4096, context_window=128000),
        ),
        ModelInfo(
            id="org/m-slash",
            name="Slash Model",
            provider_id=PROVIDER,
            capabilities=ModelCapabilities(supports_streaming=True, supports_tools=False),
        ),
        ModelInfo(
            id="m-no-stream",
            name="No Stream",
            provider_id=PROVIDER,
            capabilities=ModelCapabilities(supports_streaming=False, supports_tools=True),
        ),
    ]


@pytest.fixture
def fake_runtime(monkeypatch: pytest.MonkeyPatch):
    """One connected provider with three runtime models, no flocks.json / catalog access."""
    monkeypatch.setattr(model_routes, "_apply_provider_config", AsyncMock())
    monkeypatch.setattr(model_routes.ConfigWriter, "list_provider_ids", classmethod(lambda cls: [PROVIDER, "not-connected"]))

    def list_models(cls, provider_id=None):
        if provider_id == PROVIDER:
            return _models()
        if provider_id == "not-connected":
            raise RuntimeError("provider not configured")
        return []

    monkeypatch.setattr(model_routes.Provider, "list_models", classmethod(list_models))
    monkeypatch.setattr(model_routes.Provider, "get", classmethod(lambda cls, pid: None))
    monkeypatch.setattr(model_routes.Provider, "_ensure_initialized", classmethod(lambda cls: None))


def _client(role: str = "admin") -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id=f"{role}-1", username=role, role=role, status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(model_routes.router, prefix="/api/model")
    return TestClient(app, raise_server_exceptions=True)


def test_list_models_returns_connected_provider_models(fake_runtime):
    resp = _client().get("/api/model/")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert body["providers"] == [PROVIDER]
    first = next(m for m in body["models"] if m["id"] == "m-stream-tools")
    assert first["provider"] == PROVIDER
    assert first["capabilities"] == {"streaming": True, "tools": True, "vision": False, "json_mode": False}
    assert first["max_tokens"] == 4096 and first["context_window"] == 128000


def test_list_models_capability_filters(fake_runtime):
    client = _client()
    only_stream = client.get("/api/model/", params={"streaming": "true", "tools": "true"}).json()
    assert [m["id"] for m in only_stream["models"]] == ["m-stream-tools"]
    no_stream = client.get("/api/model/", params={"streaming": "false"}).json()
    assert [m["id"] for m in no_stream["models"]] == ["m-no-stream"]
    unknown = client.get("/api/model/", params={"provider": "nope"}).json()
    assert unknown["total"] == 0 and unknown["providers"] == []


def test_get_model_by_plain_and_provider_prefixed_id(fake_runtime):
    client = _client()
    plain = client.get("/api/model/m-stream-tools")
    assert plain.status_code == 200, plain.text
    assert plain.json()["provider"] == PROVIDER

    prefixed = client.get(f"/api/model/{PROVIDER}/m-stream-tools")
    assert prefixed.status_code == 200, prefixed.text
    assert prefixed.json()["id"] == "m-stream-tools"

    # a model id that itself contains a slash must still resolve
    slash = client.get("/api/model/org/m-slash")
    assert slash.status_code == 200, slash.text
    assert slash.json()["id"] == "org/m-slash"

    assert client.get("/api/model/does-not-exist").status_code == 404


def test_summary_filter_and_compatible_do_not_leak_query_defaults(fake_runtime):
    client = _client()
    summary = client.get("/api/model/providers/summary")
    assert summary.status_code == 200 and summary.json() == {PROVIDER: 3}

    filtered = client.post("/api/model/filter", json={"supports_tools": True})
    assert filtered.status_code == 200
    assert sorted(m["id"] for m in filtered.json()["models"]) == ["m-no-stream", "m-stream-tools"]

    compatible = client.get("/api/model/m-stream-tools/compatible", params={"limit": 5})
    assert compatible.status_code == 200, compatible.text
    assert set(compatible.json()) == {"org/m-slash", "m-no-stream"}


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch):
    updates: list[dict] = []

    class Manager:
        def get_model(self, provider_id, model_id):
            return SimpleNamespace(id=model_id) if model_id == "known" else None

        def update_setting(self, provider_id, model_id, enabled=None, default_parameters=None):
            updates.append({"provider_id": provider_id, "model_id": model_id, "enabled": enabled, "default_parameters": default_parameters})
            from flocks.provider.types import ModelSetting

            return ModelSetting(provider_id=provider_id, model_id=model_id, enabled=enabled if enabled is not None else True)

    monkeypatch.setattr(model_routes, "get_model_manager", lambda: Manager())
    monkeypatch.setattr(model_routes, "_apply_provider_config", AsyncMock())
    return updates


def test_update_settings_requires_admin_and_known_model(fake_manager):
    member = _client("member")
    assert member.put(f"/api/model/v2/settings/{PROVIDER}/known", json={"enabled": False}).status_code == 403
    assert fake_manager == []

    admin = _client("admin")
    missing = admin.put(f"/api/model/v2/settings/{PROVIDER}/unknown", json={"enabled": False})
    assert missing.status_code == 404, missing.text
    assert fake_manager == []

    ok = admin.put(f"/api/model/v2/settings/{PROVIDER}/known", json={"enabled": False})
    assert ok.status_code == 200, ok.text
    assert fake_manager[-1]["enabled"] is False


def test_update_settings_rejects_lax_booleans(fake_manager):
    admin = _client("admin")
    for value in ("no", "yes", 0, "false"):
        resp = admin.put(f"/api/model/v2/settings/{PROVIDER}/known", json={"enabled": value})
        assert resp.status_code == 422, (value, resp.text)
    assert fake_manager == []


def test_delete_definition_requires_admin(monkeypatch: pytest.MonkeyPatch):
    called = []
    monkeypatch.setattr(model_routes.ConfigWriter, "remove_model", classmethod(lambda cls, *a, **k: called.append(a)), raising=False)
    member = _client("member")
    assert member.delete(f"/api/model/v2/definitions/{PROVIDER}/known").status_code == 403
    assert called == []
