"""The login session a request came with is known for that request only.

An account backend that keeps its accounts elsewhere passes it on, so the
place that keeps them decides who asks for a change; an API-token request and
work outside a request carry none.  ``flocks admin`` commands use the account
backend an installed extension names, not the runtime's own tables.
"""

from __future__ import annotations

import importlib.metadata

import pytest
from starlette.requests import Request
from typer.testing import CliRunner

from flocks.auth.context import AuthUser, get_current_auth_session, get_current_auth_user
from flocks.auth.service import AuthService
from flocks.server import auth as auth_module


def _request(headers: dict[str, str]) -> Request:
    return Request({
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": "/api/session", "raw_path": b"/api/session", "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": ("127.0.0.1", 12345), "server": ("127.0.0.1", 8000),
    })


class _LoggedIn:
    def to_auth_user(self) -> AuthUser:
        return AuthUser(id="usr_1", username="mia", role="member", status="active")


@pytest.fixture
def sessions(monkeypatch):
    async def has_users():
        return True

    async def by_session(session_id):
        return _LoggedIn() if session_id == "sess-mia" else None

    monkeypatch.setattr(auth_module.AuthService, "has_users", has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", by_session)


@pytest.mark.parametrize("agent", ["Mozilla/5.0", "curl/8.0"])
async def test_a_request_on_a_login_session_carries_it_until_it_ends(sessions, agent):
    _, token, user = await auth_module.apply_auth_for_request(_request({"user-agent": agent, "cookie": "flocks_session=sess-mia"}))
    try:
        assert user.username == "mia" and get_current_auth_user().id == "usr_1"
        assert get_current_auth_session() == "sess-mia"
    finally:
        auth_module.clear_auth_context(token)
    assert get_current_auth_session() is None and get_current_auth_user() is None


async def test_an_api_token_request_carries_no_login_session(monkeypatch):
    class _Secrets:
        def get(self, key):
            return "abc123" if key == auth_module.API_TOKEN_SECRET_ID else None

    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _Secrets())
    _, token, user = await auth_module.apply_auth_for_request(_request({"user-agent": "curl/8.0", "authorization": "Bearer abc123"}))
    try:
        assert user.username == "api-token-service" and get_current_auth_session() is None
    finally:
        auth_module.clear_auth_context(token)


class _ElsewhereBackend:
    """An account backend an extension provides (a stand-in)."""

    listed: list[str] = []

    @classmethod
    async def init(cls):
        return None

    @classmethod
    async def list_users(cls):
        cls.listed.append("elsewhere")
        from flocks.auth.service import LocalUser

        return [LocalUser(id="usr_x", username="kept-elsewhere", role="admin", status="active",
                          must_reset_password=False, tenant_ids=(), asset_groups=(),
                          created_at="", updated_at="", last_login_at=None)]


for _name in ("has_users", "get_bootstrap_status", "bootstrap_admin", "get_user_by_id", "get_user_by_username",
              "get_user_by_session_id", "revoke_session", "login", "change_password", "set_password",
              "generate_admin_temp_password", "reassign_orphan_sessions", "migrate_legacy_sessions_to_admin"):
    setattr(_ElsewhereBackend, _name, classmethod(lambda cls, *a, **k: None))


@pytest.fixture
def installed_backend(monkeypatch):
    class _EntryPoint:
        def load(self):
            return lambda: _ElsewhereBackend

    real = importlib.metadata.entry_points

    def entry_points(**params):
        if params.get("group") == AuthService.BACKEND_ENTRYPOINT_GROUP:
            return [_EntryPoint()]
        return real(**params)

    monkeypatch.setattr(importlib.metadata, "entry_points", entry_points)
    yield
    AuthService.reset_backend()


def test_admin_commands_use_the_account_backend_the_install_names(installed_backend):
    from flocks.cli.commands.admin import admin_app

    result = CliRunner().invoke(admin_app, ["list-users"])
    assert result.exit_code == 0, result.output
    assert "kept-elsewhere" in result.output and _ElsewhereBackend.listed == ["elsewhere"]


async def test_without_an_installed_backend_nothing_changes():
    assert await AuthService.use_installed_backend() is False
    assert AuthService.get_backend().__name__ == "LocalAuthBackend"
