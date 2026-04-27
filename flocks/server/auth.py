"""
FastAPI auth dependencies and cookie helpers.
"""

from __future__ import annotations

import hmac
import os
import re
from typing import Optional

from fastapi import HTTPException, Request, Response, status

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.auth.service import AuthService
from flocks.security import get_secret_manager

SESSION_COOKIE_NAME = "flocks_session"
API_TOKEN_SECRET_ID = "server_api_token"

# Paths that never require auth. Everything else is protected by default.
PUBLIC_PATHS = frozenset({
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
    "/api/health",
    "/api/auth/login",
    "/api/auth/bootstrap-status",
    "/api/auth/bootstrap-admin",
    "/auth/login",
    "/auth/bootstrap-status",
    "/auth/bootstrap-admin",
})

# Static asset prefixes never require auth (WebUI / favicon / etc.)
PUBLIC_PREFIXES = (
    "/assets/",
    "/static/",
)

# Regex patterns for dynamic public paths that cannot be expressed via
# fixed strings or simple prefixes (the path segment is provided by the
# caller / external platform).
#
# These endpoints are designed to be invoked by **external systems** that
# cannot present the local cookie or ``Authorization: Bearer`` header:
#
#   * IM platform webhooks (Feishu / Slack / Telegram / 3rd-party plugins…)
#     POST to ``/api/channel/{channel_id}/webhook`` — the concrete
#     ``ChannelPlugin.handle_webhook`` implementation is responsible for
#     signature / origin verification.  Note that the built-in DingTalk /
#     WeCom plugins currently use stream / long-poll mode and do not
#     implement ``handle_webhook``; this exemption mainly serves external
#     or custom plugins.
#
# TODO: add ``^/(?:api/)?provider/[^/]+/oauth/callback/?$`` once the
# provider OAuth callback in ``server/routes/provider.py`` is implemented
# (today it's a stub returning ``True``).
#
# WARNING: any path matched here bypasses the global auth guard.  The
# downstream handler is fully responsible for its own authentication
# (signature checks, IP allowlists, replay protection, …).  Do NOT add
# entries that touch user data without a per-request integrity check.
PUBLIC_PATH_REGEXES = (
    re.compile(r"^/(?:api/)?channel/[^/]+/webhook/?$"),
)


def should_use_secure_cookie(request: Request) -> bool:
    forced = os.getenv("FLOCKS_COOKIE_SECURE", "").strip().lower()
    if forced in {"1", "true", "yes", "on"}:
        return True
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto:
        proto = forwarded_proto.split(",")[0].strip().lower()
        if proto == "https":
            return True
    return request.url.scheme == "https"


def set_session_cookie(response: Response, session_id: str, *, secure: bool) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=secure,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")


def get_request_ip(request: Request) -> Optional[str]:
    if request.client:
        return request.client.host
    return None


def get_request_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("user-agent")


def get_optional_user(request: Request) -> Optional[AuthUser]:
    user = getattr(request.state, "auth_user", None)
    return user


def require_user(request: Request) -> AuthUser:
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def require_admin(request: Request) -> AuthUser:
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行该操作")
    return user


def auth_middleware_exempt(path: str) -> bool:
    """Return True if the path is public (no auth required)."""
    if path in PUBLIC_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return True
    return any(pattern.match(path) for pattern in PUBLIC_PATH_REGEXES)


_PASSWORD_RESET_ALLOWED = frozenset({
    "/api/auth/me",
    "/api/auth/change-password",
    "/api/auth/logout",
    "/auth/me",
    "/auth/change-password",
    "/auth/logout",
})


def password_reset_exempt(path: str) -> bool:
    return path in _PASSWORD_RESET_ALLOWED


def _has_session_cookie(request: Request) -> bool:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    return bool(session_id and session_id.strip())


def _is_browser_like_request(request: Request) -> bool:
    """
    Identify browser-originated traffic (must keep strict login checks).

    Modern browsers always send `sec-fetch-*` fetch-metadata headers on
    same-origin and cross-origin fetches. We rely on those, plus `origin`
    (covers older Chromium/Safari on some same-origin cases) and `referer`.
    Some browser flows seen behind reverse proxies (for example EventSource)
    can lose a subset of these headers, so we also accept an existing session
    cookie or a typical browser UA paired with HTML/SSE Accept headers.
    """
    headers = request.headers
    if headers.get("sec-fetch-site") or headers.get("sec-fetch-mode") or headers.get("sec-fetch-dest"):
        return True
    if headers.get("origin"):
        return True
    if headers.get("referer"):
        return True
    if _has_session_cookie(request):
        return True

    user_agent = (headers.get("user-agent") or "").lower()
    accept = (headers.get("accept") or "").lower()
    if "mozilla/" in user_agent and (
        "text/html" in accept
        or "application/xhtml+xml" in accept
        or "text/event-stream" in accept
    ):
        return True
    return False


def _loopback_hosts() -> frozenset[str]:
    hosts = {"127.0.0.1", "::1", "localhost"}
    # FastAPI TestClient reports client host as "testclient"; only trust it in tests.
    if os.getenv("PYTEST_CURRENT_TEST"):
        hosts.add("testclient")
    return frozenset(hosts)


def _is_loopback_direct_request(request: Request) -> bool:
    """
    Trust only local direct requests (no proxy forwarding headers).
    """
    if request.headers.get("x-forwarded-for"):
        return False
    client_host = request.client.host if request.client else None
    return client_host in _loopback_hosts()


def _read_api_token_from_request(request: Request) -> Optional[str]:
    """
    Read API token from Authorization Bearer or x-flocks-api-token header.
    """
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header:
        scheme, _, value = auth_header.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()

    alt = request.headers.get("x-flocks-api-token")
    if alt and alt.strip():
        return alt.strip()
    return None


def _get_expected_api_token() -> Optional[str]:
    try:
        token = get_secret_manager().get(API_TOKEN_SECRET_ID)
        if token:
            return token.strip() or None
        return None
    except Exception:
        return None


def _is_valid_api_token(token: Optional[str]) -> bool:
    expected = _get_expected_api_token()
    if not expected or not token:
        return False
    return hmac.compare_digest(token, expected)


def _build_api_token_user() -> AuthUser:
    """Synthetic service identity for API token clients."""
    return AuthUser(
        id="api-token-service",
        username="api-token-service",
        role="admin",
        status="active",
        must_reset_password=False,
    )


def _build_local_service_user() -> AuthUser:
    """Synthetic local service identity for loopback non-browser clients."""
    return AuthUser(
        id="local-service",
        username="local-service",
        role="admin",
        status="active",
        must_reset_password=False,
    )


async def apply_auth_for_request(request: Request):
    """
    Resolve user from cookie and bind context var.
    Returns (response_if_blocked, token, user).

    Policy: every path is protected by default; only whitelisted public paths
    (see `PUBLIC_PATHS` / `PUBLIC_PREFIXES`) bypass auth.
    """
    if auth_middleware_exempt(request.url.path):
        token = set_current_auth_user(None)
        return None, token, None

    if _has_session_cookie(request):
        bootstrapped = await AuthService.has_users()
        if not bootstrapped:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="系统尚未初始化管理员账号，请先完成初始化",
            )

        session_id = request.cookies.get(SESSION_COOKIE_NAME)
        if not session_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

        user = await AuthService.get_user_by_session_id(session_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")

        auth_user = user.to_auth_user()
        request.state.auth_user = auth_user
        token = set_current_auth_user(auth_user)
        if auth_user.must_reset_password and not password_reset_exempt(request.url.path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前账号必须先修改密码后才能继续使用",
            )
        return None, token, auth_user

    # Non-browser clients: local loopback can run without token; remote requires API token.
    if not _is_browser_like_request(request):
        provided = _read_api_token_from_request(request)
        if provided:
            if not _is_valid_api_token(provided):
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="非浏览器请求鉴权失败，请在 Authorization 中携带有效 Bearer API Token",
                )
            token_user = _build_api_token_user()
            request.state.auth_user = token_user
            token = set_current_auth_user(token_user)
            return None, token, token_user

        if _is_loopback_direct_request(request):
            local_user = _build_local_service_user()
            request.state.auth_user = local_user
            token = set_current_auth_user(local_user)
            return None, token, local_user

        expected = _get_expected_api_token()
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"远程非浏览器请求需要 API Token，请先在 .secret.json 中配置 {API_TOKEN_SECRET_ID}",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="远程非浏览器请求鉴权失败，请在 Authorization 中携带 Bearer API Token",
        )

    bootstrapped = await AuthService.has_users()
    if not bootstrapped:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="系统尚未初始化管理员账号，请先完成初始化",
        )

    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    user = await AuthService.get_user_by_session_id(session_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")

    auth_user = user.to_auth_user()
    request.state.auth_user = auth_user
    token = set_current_auth_user(auth_user)
    if auth_user.must_reset_password and not password_reset_exempt(request.url.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号必须先修改密码后才能继续使用",
        )
    return None, token, auth_user


def clear_auth_context(token) -> None:
    reset_current_auth_user(token)
