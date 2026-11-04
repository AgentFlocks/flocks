"""Regression tests for /api/default-model: admin-only mutations and PUT validation.

Before the fix any signed-in member could change / delete the default model
and the fallback chain, and PUT accepted blank ids and models the runtime
cannot resolve (Auto mode later failed with primary_provider_not_found).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.provider.types import DefaultModelConfig, ModelType
from flocks.server.routes import default_model as dm_routes


def _client(role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id=f"{role}-1", username=role, role=role, status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(dm_routes.router, prefix="/api/default-model")
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def fake_manager(monkeypatch: pytest.MonkeyPatch):
    calls: list = []

    class Manager:
        def get_model(self, provider_id, model_id):
            if provider_id == "prov" and model_id == "llm-1":
                return SimpleNamespace(model_type=ModelType.LLM)
            if provider_id == "prov" and model_id == "embed-1":
                return SimpleNamespace(model_type=ModelType.TEXT_EMBEDDING)
            return None

        def set_default_model(self, model_type, provider_id, model_id):
            calls.append(("set", model_type, provider_id, model_id))
            return DefaultModelConfig(model_type=model_type, provider_id=provider_id, model_id=model_id)

        def delete_default_model(self, model_type):
            calls.append(("delete", model_type))
            return True

    monkeypatch.setattr(dm_routes, "get_model_manager", lambda: Manager())
    monkeypatch.setattr(dm_routes.Config, "get", AsyncMock(return_value=SimpleNamespace(disabled_providers=[], enabled_providers=None)))
    monkeypatch.setattr(dm_routes.Provider, "apply_config", AsyncMock())
    monkeypatch.setattr(dm_routes.Provider, "get", classmethod(lambda cls, pid: object() if pid == "prov" else None))
    monkeypatch.setattr(dm_routes.ConfigWriter, "set_fallback_providers", classmethod(lambda cls, items: calls.append(("fallbacks", items))), raising=True)
    return calls


def test_mutations_require_admin(fake_manager):
    member = _client("member")
    responses = [
        member.put("/api/default-model/llm", json={"provider_id": "prov", "model_id": "llm-1"}),
        member.delete("/api/default-model/llm"),
        member.put("/api/default-model/fallbacks", json={"fallback_providers": []}),
    ]
    assert [r.status_code for r in responses] == [403, 403, 403]
    assert fake_manager == []


def test_put_rejects_blank_ids(fake_manager):
    admin = _client("admin")
    for body in ({"provider_id": "", "model_id": "llm-1"}, {"provider_id": "prov", "model_id": ""}, {"provider_id": "  ", "model_id": "llm-1"}):
        resp = admin.put("/api/default-model/llm", json=body)
        assert resp.status_code == 422, (body, resp.text)
    assert fake_manager == []


def test_put_rejects_unknown_provider_model_or_type_mismatch(fake_manager):
    admin = _client("admin")
    unknown_provider = admin.put("/api/default-model/llm", json={"provider_id": "nope", "model_id": "llm-1"})
    assert unknown_provider.status_code == 400 and "Provider 'nope' not found" in unknown_provider.text
    unknown_model = admin.put("/api/default-model/llm", json={"provider_id": "prov", "model_id": "ghost"})
    assert unknown_model.status_code == 400 and "Unknown model" in unknown_model.text
    wrong_type = admin.put("/api/default-model/llm", json={"provider_id": "prov", "model_id": "embed-1"})
    assert wrong_type.status_code == 400 and "is of type 'text-embedding'" in wrong_type.text
    assert fake_manager == []


def test_put_accepts_a_resolvable_model(fake_manager):
    resp = _client("admin").put("/api/default-model/llm", json={"provider_id": "prov", "model_id": "llm-1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["model_id"] == "llm-1"
    assert fake_manager == [("set", ModelType.LLM, "prov", "llm-1")]
