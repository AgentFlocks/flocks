"""Regression tests for /api/custom provider & model routes.

Covers: admin-only mutations, provider id / base_url validation, refusing to
delete non custom-* providers here, strict booleans on model creation, and the
bounded models.dev fetch.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.routes import custom_provider as cp_routes


@pytest.fixture
def custom_provider_config(tmp_path, monkeypatch):
    """Temporary flocks.json holding one custom provider."""
    config_dir = tmp_path / "home" / ".flocks" / "config"
    config_dir.mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    from flocks.config.config import Config

    Config._global_config = None
    Config._cached_config = None
    (config_dir / "flocks.json").write_text(json.dumps({
        "provider": {
            "custom-tb-inner": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {"apiKey": "{secret:custom-tb-inner_llm_key}", "baseURL": "https://llm-internal.example.test/api"},
                "models": {},
                "name": "TB-inner",
            },
            "openai": {
                "npm": "@ai-sdk/openai",
                "options": {"apiKey": "{secret:openai_llm_key}"},
                "models": {"gpt-4o": {"name": "gpt-4o"}},
            },
        }
    }, indent=2))
    from flocks.provider.provider import Provider

    monkeypatch.setattr(Provider, "_models", Provider._models.copy())
    return config_dir


def _client(role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id=f"{role}-1", username=role, role=role, status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(cp_routes.router, prefix="/api/custom")
    return TestClient(app, raise_server_exceptions=True)


def test_mutations_require_admin(custom_provider_config):
    member = _client("member")
    responses = [
        member.post("/api/custom/providers", json={"name": "x", "base_url": "https://api.example.test/v1", "api_key": "k"}),
        member.delete("/api/custom/providers/custom-tb-inner"),
        member.post("/api/custom/models/custom-tb-inner", json={"model_id": "m", "name": "m"}),
        member.delete("/api/custom/models/custom-tb-inner/m"),
    ]
    assert [r.status_code for r in responses] == [403] * 4
    # nothing was written
    from flocks.config.config_writer import ConfigWriter

    assert ConfigWriter.get_provider_raw("custom-tb-inner") is not None
    assert ConfigWriter.get_provider_raw("custom-x") is None


@pytest.mark.parametrize("name", ["a/b", "a\\b", "..", "a\tb", "a\nb"])
def test_create_provider_rejects_unroutable_names(custom_provider_config, name):
    resp = _client("admin").post("/api/custom/providers", json={"name": name, "base_url": "https://api.example.test/v1", "api_key": "k"})
    assert resp.status_code == 422, resp.text
    assert "must not contain" in resp.json()["detail"]


@pytest.mark.parametrize("base_url", ["ftp://host/v1", "not a url", "http://169.254.169.254/latest", "http://[fe80::1]/v1", "https:///v1"])
def test_create_provider_rejects_bad_base_url(custom_provider_config, base_url):
    resp = _client("admin").post("/api/custom/providers", json={"name": "bad-url", "base_url": base_url, "api_key": "k"})
    assert resp.status_code == 422, (base_url, resp.text)
    from flocks.config.config_writer import ConfigWriter

    assert ConfigWriter.get_provider_raw("custom-bad-url") is None


def test_create_provider_allows_private_gateway_with_warning(custom_provider_config, monkeypatch):
    warnings: list = []
    from flocks.provider import base_url as base_url_mod

    monkeypatch.setattr(base_url_mod.log, "warn", lambda msg, extra=None: warnings.append((msg, extra)), raising=False)
    monkeypatch.setattr(base_url_mod.log, "warning", lambda msg, extra=None: warnings.append((msg, extra)), raising=False)
    resp = _client("admin").post("/api/custom/providers", json={"name": "inner-gw", "base_url": "http://10.0.0.8:8000/v1", "api_key": ""})
    assert resp.status_code == 201, resp.text
    assert any(m == "provider.base_url.private_network" for m, _ in warnings), warnings


def test_delete_refuses_builtin_provider(custom_provider_config):
    resp = _client("admin").delete("/api/custom/providers/openai")
    assert resp.status_code == 400, resp.text
    from flocks.config.config_writer import ConfigWriter

    assert ConfigWriter.get_provider_raw("openai") is not None
    assert _client("admin").delete("/api/custom/providers/custom-missing").status_code == 404


@pytest.mark.parametrize("field", ["supports_vision", "supports_tools", "supports_streaming", "supports_reasoning"])
def test_create_model_rejects_lax_booleans(custom_provider_config, field):
    resp = _client("admin").post("/api/custom/models/custom-tb-inner", json={"model_id": "m1", "name": "m1", field: "no"})
    assert resp.status_code == 422, (field, resp.text)


@pytest.mark.asyncio
async def test_models_dev_fetch_is_bounded(monkeypatch):
    """httpx timeouts are per phase; the whole fetch must respect MODELS_DEV_TIMEOUT_SEC."""
    import httpx

    class SlowClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    monkeypatch.setattr(httpx, "AsyncClient", SlowClient)
    monkeypatch.setattr(cp_routes, "MODELS_DEV_TIMEOUT_SEC", 0.1)
    monkeypatch.setattr(cp_routes, "_models_dev_cache", None)
    monkeypatch.setattr(cp_routes, "_models_dev_cache_time", 0.0)
    started = time.monotonic()
    data = await cp_routes._fetch_models_dev()
    assert data is None
    assert time.monotonic() - started < 1.0
