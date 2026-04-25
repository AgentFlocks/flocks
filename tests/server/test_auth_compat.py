from starlette.requests import Request
from fastapi import HTTPException
import pytest

from flocks.auth.context import AuthUser
from flocks.server import auth as auth_module


class _FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, key: str):
        return self.values.get(key)


class _FakeLocalUser:
    def __init__(self, *, must_reset_password: bool = False):
        self.must_reset_password = must_reset_password

    def to_auth_user(self) -> AuthUser:
        return AuthUser(
            id="usr_test",
            username="test-user",
            role="member",
            status="active",
            must_reset_password=self.must_reset_password,
        )


def _make_request(
    *,
    headers: dict[str, str] | None = None,
    client_host: str = "127.0.0.1",
    path: str = "/api/session",
) -> Request:
    normalized_headers = []
    for key, value in (headers or {}).items():
        normalized_headers.append((key.lower().encode("latin-1"), value.encode("latin-1")))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": normalized_headers,
        "client": (client_host, 12345),
        "server": ("127.0.0.1", 8000),
    }
    return Request(scope)


def test_read_api_token_from_authorization_header():
    request = _make_request(headers={"authorization": "Bearer test-token"})
    assert auth_module._read_api_token_from_request(request) == "test-token"


def test_read_api_token_from_custom_header():
    request = _make_request(headers={"x-flocks-api-token": "custom-token"})
    assert auth_module._read_api_token_from_request(request) == "custom-token"


def test_is_valid_api_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}))
    assert auth_module._is_valid_api_token("abc123") is True
    assert auth_module._is_valid_api_token("wrong") is False


@pytest.mark.asyncio
async def test_apply_auth_for_request_non_browser_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}))
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": "Bearer abc123"})
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.username == "api-token-service"
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_for_request_non_browser_loopback_allows_without_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}))
    request = _make_request(headers={"user-agent": "curl/8.0"})
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.username == "local-service"
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_for_request_non_browser_remote_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}))
    request = _make_request(headers={"user-agent": "curl/8.0"}, client_host="10.0.0.2")
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)
    assert exc_info.value.status_code == 401
    assert "Bearer API Token" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_apply_auth_for_request_non_browser_remote_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}))
    request = _make_request(
        headers={"user-agent": "curl/8.0", "authorization": "Bearer wrong"},
        client_host="10.0.0.2",
    )
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_apply_auth_for_request_non_browser_remote_rejects_when_no_stored_token(monkeypatch):
    monkeypatch.setattr(auth_module, "get_secret_manager", lambda: _FakeSecrets({}))
    request = _make_request(headers={"user-agent": "curl/8.0"}, client_host="10.0.0.2")
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)
    assert exc_info.value.status_code == 401
    assert auth_module.API_TOKEN_SECRET_ID in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_apply_auth_for_request_requires_password_reset_before_access(monkeypatch):
    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeLocalUser(must_reset_password=True)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)

    request = _make_request(
        headers={
            "user-agent": "Mozilla/5.0",
            "origin": "http://localhost:5173",
            "cookie": f"{auth_module.SESSION_COOKIE_NAME}=session-123",
        },
        path="/api/session",
    )
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)

    assert exc_info.value.status_code == 403
    assert "必须先修改密码" in str(exc_info.value.detail)


@pytest.mark.asyncio
async def test_apply_auth_for_request_treats_referer_only_remote_request_as_browser(monkeypatch):
    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeLocalUser(must_reset_password=False)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )

    request = _make_request(
        headers={
            "user-agent": "Mozilla/5.0",
            "referer": "http://10.0.0.9:5173/login",
            "cookie": f"{auth_module.SESSION_COOKIE_NAME}=session-123",
        },
        client_host="10.0.0.2",
        path="/api/auth/me",
    )
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.username == "test-user"
    finally:
        auth_module.clear_auth_context(token)


class TestAuthMiddlewareExempt:
    """Cover ``auth_middleware_exempt`` — both fixed paths and regex patterns."""

    def test_fixed_public_path_is_exempt(self):
        assert auth_module.auth_middleware_exempt("/health") is True
        assert auth_module.auth_middleware_exempt("/api/auth/login") is True

    def test_static_prefix_is_exempt(self):
        assert auth_module.auth_middleware_exempt("/assets/main.js") is True
        assert auth_module.auth_middleware_exempt("/static/img/logo.png") is True

    def test_protected_path_is_not_exempt(self):
        assert auth_module.auth_middleware_exempt("/api/session") is False
        assert auth_module.auth_middleware_exempt("/api/admin/users") is False

    def test_channel_webhook_is_exempt_via_regex(self):
        # /api/channel/{channel_id}/webhook is the public callback entry for
        # IM platforms (DingTalk / WeCom / Feishu …).  Both /api/ and /
        # mounts must be reachable.
        assert auth_module.auth_middleware_exempt("/api/channel/dingtalk/webhook") is True
        assert auth_module.auth_middleware_exempt("/channel/dingtalk/webhook") is True
        assert auth_module.auth_middleware_exempt("/api/channel/wecom/webhook") is True
        assert auth_module.auth_middleware_exempt("/api/channel/feishu/webhook/") is True

    def test_other_channel_subpaths_are_still_protected(self):
        # Only ``/webhook`` is public; ``/bind``, ``/restart``, ``/status``
        # and friends still require auth.
        assert auth_module.auth_middleware_exempt("/api/channel/dingtalk/bind") is False
        assert auth_module.auth_middleware_exempt("/api/channel/dingtalk/restart") is False
        assert auth_module.auth_middleware_exempt("/api/channel/status") is False
        assert auth_module.auth_middleware_exempt("/api/channel/list") is False
        # Defense-in-depth: a malicious caller must not hide a protected path
        # behind a fake ``webhook`` segment.
        assert auth_module.auth_middleware_exempt("/api/channel/dingtalk/webhook/extra") is False


@pytest.mark.asyncio
async def test_apply_auth_for_request_channel_webhook_passes_without_credentials(monkeypatch):
    """External platform webhook must reach the route handler without 401.

    Channel webhooks are POSTed by IM platforms (DingTalk / WeCom / …)
    that present neither cookies nor an API token.  The plugin's
    ``handle_webhook`` is responsible for signature verification.
    """
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )
    request = _make_request(
        headers={"user-agent": "DingTalk-Server"},
        client_host="203.0.113.10",  # remote, not loopback
        path="/api/channel/dingtalk/webhook",
    )
    blocked, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert blocked is None
        # Public paths intentionally do not synthesize an auth user.
        assert user is None
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_for_request_allows_password_reset_endpoints_when_required(monkeypatch):
    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeLocalUser(must_reset_password=True)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)

    request = _make_request(
        headers={
            "user-agent": "Mozilla/5.0",
            "origin": "http://localhost:5173",
            "cookie": f"{auth_module.SESSION_COOKIE_NAME}=session-123",
        },
        path="/api/auth/change-password",
    )
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.must_reset_password is True
    finally:
        auth_module.clear_auth_context(token)
