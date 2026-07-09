from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from flocks.auth.service import AuthService
from flocks.server import auth as auth_module


class _FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None):
        self.values = values or {}

    def get(self, key: str):
        return self.values.get(key)


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


@pytest.fixture
def _isolated_auth_db(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(tmp_path))
    AuthService._initialized = False
    AuthService._initialized_db_path = None
    AuthService._has_users_cached = False
    backend = AuthService.get_backend()
    backend._initialized = False
    backend._initialized_db_path = None
    backend._has_users_cached = False


@pytest.mark.asyncio
async def test_apply_auth_uses_db_api_key_subject(_isolated_auth_db):
    _, secret = await AuthService.create_api_key(
        name="ci-key",
        role="member",
        tenant_id="tenant-a",
        department="soc",
        permission_mode="readonly",
        created_by="test",
    )
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": f"Bearer {secret}"})
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.id.startswith("api-key:")
        assert user.role == "member"
        assert user.department == "soc"
        assert request.state.subject.subject_id.startswith("api-key:")
        assert request.state.subject.auth_source == "api_token"
    finally:
        auth_module.clear_auth_context(token)


@pytest.mark.asyncio
async def test_apply_auth_rejects_expired_api_key(_isolated_auth_db):
    expires_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    _, secret = await AuthService.create_api_key(
        name="expired-key",
        role="member",
        expires_at=expires_at,
        created_by="test",
    )
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": f"Bearer {secret}"})
    with pytest.raises(HTTPException) as exc_info:
        await auth_module.apply_auth_for_request(request)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_apply_auth_falls_back_to_legacy_server_token(monkeypatch, _isolated_auth_db):
    monkeypatch.setattr(
        auth_module,
        "get_secret_manager",
        lambda: _FakeSecrets({auth_module.API_TOKEN_SECRET_ID: "legacy-token"}),
    )
    request = _make_request(headers={"user-agent": "curl/8.0", "authorization": "Bearer legacy-token"})
    _, token, user = await auth_module.apply_auth_for_request(request)
    try:
        assert user is not None
        assert user.username == "api-token-service"
        assert user.role == "admin"
    finally:
        auth_module.clear_auth_context(token)
