"""
FastAPI auth dependencies and cookie helpers.
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request, Response, status

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.auth.service import AuthService
from flocks.security import get_secret_manager

SESSION_COOKIE_NAME = "flocks_session"
API_TOKEN_SECRET_ID = "server_api_token"

PROTECTED_PREFIXES = (
    "/api",
    "/session",
    "/provider",
    "/config",
    "/project",
    "/file",
    "/mcp",
    "/agent",
    "/app/agent",
    "/pty",
    "/lsp",
    "/path",
    "/vcs",
    "/find",
    "/permission",
    "/question",
    "/tui",
    "/global",
    "/channel",
    "/auth",
    "/admin",
    "/cloud-account",
    "/event",
    "/logs",
    "/update",
    "/workspace",
)


def should_use_secure_cookie(request: Request) -> bool:
    import os

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
    public_prefixes = {
        "/health",
        "/api/health",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/api/auth/login",
        "/api/auth/bootstrap-status",
        "/api/auth/bootstrap-admin",
        "/auth/login",
        "/auth/bootstrap-status",
        "/auth/bootstrap-admin",
    }
    return any(path == prefix or path.startswith(prefix + "/") for prefix in public_prefixes)


def is_protected_backend_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in PROTECTED_PREFIXES)


def _is_browser_like_request(request: Request) -> bool:
    """
    Identify browser-originated traffic (must keep strict login checks).

    We rely on standard browser headers, then fall back to User-Agent.
    """
    headers = request.headers
    if headers.get("origin"):
        return True
    if headers.get("sec-fetch-site") or headers.get("sec-fetch-mode") or headers.get("sec-fetch-dest"):
        return True
    user_agent = (headers.get("user-agent") or "").lower()
    return "mozilla/" in user_agent


def _is_loopback_direct_request(request: Request) -> bool:
    """
    Trust only local direct requests (no proxy forwarding headers).
    """
    if request.headers.get("x-forwarded-for"):
        return False
    client_host = request.client.host if request.client else None
    return client_host in {"127.0.0.1", "::1", "localhost", "testclient"}


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
    """
    if not is_protected_backend_path(request.url.path):
        token = set_current_auth_user(None)
        return None, token, None

    if auth_middleware_exempt(request.url.path):
        token = set_current_auth_user(None)
        return None, token, None

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
    return None, token, auth_user


def clear_auth_context(token) -> None:
    reset_current_auth_user(token)
