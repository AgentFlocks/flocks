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
