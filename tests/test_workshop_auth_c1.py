"""C2 pre-work tests — workshop_auth plugin mount + adapter + JWKS.

Run:
    cd /path/to/flocks
    pytest tests/test_workshop_auth_c1.py -v
or in the PoC venv:
    PYTHONPATH=. .venv-poc/bin/python -m pytest tests/test_workshop_auth_c1.py -v

Verifies:
    1. Env-gated mount: FLOCKS_AUTH=workshop_jwt → backend registered,
       FLOCKS_AUTH unset → backend NOT registered.
    2. Adapter injects permissions + tenant_ids into extension_context,
       and is a no-op for admin / unauthenticated requests.
    3. JWT decode paths:
        - HS256 happy + forged signature + expired
        - RS256 missing JWKS URL → clear error
    4. Public surface: register_workshop_auth is idempotent.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest


def _reset_module_env(monkeypatch, **overrides):
    for k in ("FLOCKS_AUTH", "WORKSHOP_JWT_SECRET", "WORKSHOP_JWT_ALG", "WORKSHOP_JWKS_URL"):
        monkeypatch.delenv(k, raising=False)
    for k, v in overrides.items():
        monkeypatch.setenv(k, v)


def _hs256_token(secret: str, *, teams=("team_A",), user="u_a", role="member", permissions=("read",), exp_s=900):
    import jwt as pyjwt

    now = int(time.time())
    return pyjwt.encode(
        {
            "sub": user,
            "username": user,
            "teams": list(teams),
            "iss": "ai-agent-workshop",
            "aud": "flocks",
            "iat": now,
            "exp": now + exp_s,
            "flocks_role": role,
            "permissions": list(permissions),
        },
        secret,
        algorithm="HS256",
    )


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    """Each test starts with backend unregistered so we can observe env effects."""
    from flocks.auth.service import AuthService
    from flocks.server.auth import _auth_context_adapters, unregister_auth_context_adapter

    AuthService._backend = None  # type: ignore[attr-defined]
    _auth_context_adapters.clear()
    yield
    AuthService._backend = None  # type: ignore[attr-defined]
    unregister_auth_context_adapter("wk_rbac")


# ---------------------------------------------------------------------------
# 1. Env-gated mount
# ---------------------------------------------------------------------------


def test_maybe_register_on_env_off_by_default():
    """Without FLOCKS_AUTH, backend must NOT be registered (zero core change)."""
    from flocks.auth.service import AuthService
    from flocks.workshop_auth import maybe_register_on_env

    os.environ.pop("FLOCKS_AUTH", None)
    registered = maybe_register_on_env()
    assert registered is False
    # get_backend() 返回类对象本身(register_backend 存 class),
    # 因此用 `is` 比较而非 isinstance(isinstance(类, 类) 恒 False)
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    assert AuthService.get_backend() is not TeamJWTAuthBackend


def test_maybe_register_on_env_workshop_jwt(monkeypatch):
    monkeypatch.setenv("FLOCKS_AUTH", "workshop_jwt")
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.auth.service import AuthService
    from flocks.workshop_auth import maybe_register_on_env
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    assert maybe_register_on_env() is True
    assert AuthService.get_backend() is TeamJWTAuthBackend


def test_register_workshop_auth_idempotent(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth import register_workshop_auth
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    register_workshop_auth()
    register_workshop_auth()  # second call must not throw
    from flocks.auth.service import AuthService

    assert AuthService.get_backend() is TeamJWTAuthBackend


# ---------------------------------------------------------------------------
# 2. Adapter
# ---------------------------------------------------------------------------


def _make_http_connection(cookie: str | None = None) -> "HTTPConnection":
    from starlette.requests import HTTPConnection

    headers = []
    if cookie is not None:
        headers.append((b"cookie", f"flocks_session={cookie}".encode()))
    scope = {
        "type": "http",
        "scheme": "http",
        "method": "POST",
        "path": "/api/agent/new",
        "headers": headers,
        "query_string": b"",
        "client": ("127.0.0.1", 50000),
        "server": ("flocks", 8080),
    }
    return HTTPConnection(scope)


@pytest.mark.asyncio
async def test_adapter_injects_permissions_for_member(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.auth.context import AuthUser
    from flocks.workshop_auth.adapter import inject_workshop_context

    token = _hs256_token("test-secret", teams=["team_A"], permissions=["employee:view"])
    conn = _make_http_connection(cookie=token)
    user = AuthUser(id="u_a", username="u_a", role="member", tenant_ids=("team_A",))

    out = await inject_workshop_context(conn, user)
    assert out is not None
    assert out["wk_permissions"] == ["employee:view"]
    assert out["wk_tenant_ids"] == ("team_A",)
    assert out["wk_user_id"] == "u_a"


@pytest.mark.asyncio
async def test_adapter_noop_for_admin():
    from flocks.auth.context import AuthUser
    from flocks.workshop_auth.adapter import inject_workshop_context

    # no cookie needed; admin role short-circuits before cookie check
    conn = _make_http_connection(cookie=None)
    user = AuthUser(id="root", username="root", role="admin")
    assert await inject_workshop_context(conn, user) is None


@pytest.mark.asyncio
async def test_adapter_noop_for_unauthenticated():
    from flocks.workshop_auth.adapter import inject_workshop_context

    conn = _make_http_connection(cookie="garbage")
    assert await inject_workshop_context(conn, None) is None


# ---------------------------------------------------------------------------
# 3. Backend decode paths
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_hs256_happy(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_ALG", "HS256")
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    token = _hs256_token("test-secret", teams=["team_A"])
    local_user = await TeamJWTAuthBackend.get_user_by_session_id(token)
    assert tuple(local_user.to_auth_user().tenant_ids) == ("team_A",)


@pytest.mark.asyncio
async def test_backend_hs256_forged_signature(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_ALG", "HS256")
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    token = _hs256_token("wrong-secret", teams=["team_A"])
    with pytest.raises(Exception):  # InvalidSignatureError
        await TeamJWTAuthBackend.get_user_by_session_id(token)


@pytest.mark.asyncio
async def test_backend_hs256_expired(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_ALG", "HS256")
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth.backend import TeamJWTAuthBackend

    token = _hs256_token("test-secret", teams=["team_A"], exp_s=-10)
    with pytest.raises(Exception):  # ExpiredSignatureError
        await TeamJWTAuthBackend.get_user_by_session_id(token)


def test_backend_rs256_requires_jwks_url(monkeypatch):
    """RS256 模式未配 JWKS_URL 时, _decode(现为 async)必须拒。
    用 asyncio.run 驱动而非事件循环内 await(该用例为同步测试)。"""
    import asyncio

    monkeypatch.setenv("WORKSHOP_JWT_ALG", "RS256")
    monkeypatch.delenv("WORKSHOP_JWKS_URL", raising=False)
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth.backend import _decode
    import jwt as pyjwt

    token = pyjwt.encode(
        {"sub": "x", "iss": "ai-agent-workshop", "aud": "flocks",
         "iat": int(time.time()), "exp": int(time.time()) + 60},
        "irrelevant",
        algorithm="HS256",  # wrong alg on purpose — env check fires first
    )
    with pytest.raises(RuntimeError, match="WORKSHOP_JWKS_URL"):
        asyncio.run(_decode(token))


# ---------------------------------------------------------------------------
# 4. JWKS client shape (no real fetch — just construction)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_jwks_cache_miss_then_store(monkeypatch):
    monkeypatch.setenv("WORKSHOP_JWT_SECRET", "test-secret")
    from flocks.workshop_auth import client as client_mod

    # Force cache miss
    client_mod._cache._keys = {}
    client_mod._cache._fetched_at = 0.0

    keys = await client_mod.fetch_jwks("http://127.0.0.1:1/never", refresh=True)
    # Fetch fails (unreachable); cache should still be readable but empty.
    assert keys == {}
    assert client_mod._cache.get(kid="missing") is None