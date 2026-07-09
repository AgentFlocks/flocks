from fastapi import HTTPException
import pytest
from starlette.requests import Request

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
            id="usr_subject",
            username="subject-user",
            role="member",
            status="active",
            must_reset_password=self.must_reset_password,
            tenant_ids=("tenant-a",),
            department="sec",
            asset_groups=("ag-1",),
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


@pytest.mark.asyncio
async def test_apply_auth_writes_subject_for_cookie_session(monkeypatch):
    async def _has_users():
        return True

    async def _get_user_by_session_id(_session_id: str):
        return _FakeLocalUser(must_reset_password=False)

    monkeypatch.setattr(auth_module.AuthService, "has_users", _has_users)
    monkeypatch.setattr(auth_module.AuthService, "get_user_by_session_id", _get_user_by_session_id)

    request = _make_request(
        headers={
            "user-agent": "Mozilla/5.0",
            "origin": "http://localhost:5173",
            "cookie": f"{auth_module.SESSION_COOKIE_NAME}=session-123",
        },
        path="/api/auth/me",
    )
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert request.state.subject is not None
        assert request.state.subject.subject_id == "usr_subject"
        assert request.state.subject.entry == "webui"
        assert request.state.subject.auth_source == "cookie_session"
        assert request.state.subject.department == "sec"
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_writes_subject_for_api_token(monkeypatch):
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": "Bearer abc123"})
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert request.state.subject is not None
        assert request.state.subject.entry == "api"
        assert request.state.subject.auth_source == "api_token"
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_subject_shadow_can_be_disabled(monkeypatch):
    monkeypatch.setenv("FLOCKS_IDENTITY_SUBJECT", "0")
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": "Bearer abc123"})
    _, token, _ = await auth_module.apply_auth_for_request(request)
    try:
        with pytest.raises(AttributeError):
            _ = request.state.subject
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_still_rejects_missing_token(monkeypatch):
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "abc123"}),
    )
    request = _make_request(headers={"user-agent": "curl/8.0"}, client_host="10.0.0.2")
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)
    assert exc_info.value.status_code == 401
