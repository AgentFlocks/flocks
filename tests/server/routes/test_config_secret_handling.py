"""Regression tests for /api/config secret handling and authorization.

Before the fix GET /api/config returned the *resolved* configuration (every
``{secret:...}`` reference replaced by the plaintext key from .secret.json),
PATCH merged onto that resolved view and wrote the plaintext back into
flocks.json, and every signed-in member could mutate the global config.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.server.routes import config as config_routes

PLAIN = "sk-plain-secret-value-0123456789"


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    (config_dir / "flocks.json").write_text(json.dumps({
        "theme": "light",
        "provider": {
            "openai": {"npm": "@ai-sdk/openai", "options": {"apiKey": "{secret:openai_llm_key}"}, "models": {"gpt-4o": {"name": "gpt-4o"}}},
        },
    }, indent=2))
    (config_dir / ".secret.json").write_text(json.dumps({"openai_llm_key": PLAIN}))
    from flocks.config.config import Config
    from flocks.security import secrets as secrets_mod

    monkeypatch.setattr(secrets_mod, "_secret_manager", None)
    Config._global_config = None
    Config._cached_config = None
    Config.clear_cache()
    yield config_dir
    Config.clear_cache()
    monkeypatch.setattr(secrets_mod, "_secret_manager", None)


def _client(role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id=f"{role}-1", username=role, role=role, status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(config_routes.router, prefix="/api/config")
    return TestClient(app, raise_server_exceptions=True)


def test_get_config_keeps_secret_references(config_env):
    resp = _client("admin").get("/api/config")
    assert resp.status_code == 200, resp.text
    text = resp.text
    assert PLAIN not in text
    assert resp.json()["provider"]["openai"]["options"]["apiKey"] == "{secret:openai_llm_key}"


def test_patch_config_does_not_persist_plaintext(config_env):
    admin = _client("admin")
    resp = admin.patch("/api/config/", json={"theme": "dark"})
    assert resp.status_code == 200, resp.text
    assert PLAIN not in resp.text
    on_disk = json.loads((config_env / "flocks.json").read_text())
    assert on_disk["theme"] == "dark"
    assert on_disk["provider"]["openai"]["options"]["apiKey"] == "{secret:openai_llm_key}"
    assert PLAIN not in (config_env / "flocks.json").read_text()


def test_config_mutations_require_admin(config_env):
    member = _client("member")
    responses = [
        member.patch("/api/config/", json={"theme": "dark"}),
        member.patch("/api/config/ui", json={"displayName": "x"}),
        member.post("/api/config/ui/favicon", files={"file": ("f.png", b"\x89PNG\r\n\x1a\n", "image/png")}),
        member.delete("/api/config/ui/favicon"),
        member.patch("/api/config/tool-failure", json={"disableOnRepeatedFailure": False}),
    ]
    assert [r.status_code for r in responses] == [403] * len(responses)
    assert json.loads((config_env / "flocks.json").read_text())["theme"] == "light"


def test_patch_config_validates_memory_section(config_env):
    resp = _client("admin").patch("/api/config/", json={"memory": {"dream": {"interval_hours": 0}}})
    assert resp.status_code == 400, resp.text
    assert "interval_hours" in resp.text
    assert json.loads((config_env / "flocks.json").read_text()).get("memory") is None


def test_get_config_rejects_corrupt_file(config_env):
    (config_env / "flocks.json").write_text("{ this is not json")
    from flocks.config.config import Config

    Config.clear_cache()
    resp = _client("admin").get("/api/config")
    assert resp.status_code == 500, resp.text
