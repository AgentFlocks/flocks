"""
FastAPI auth dependencies and cookie helpers.
"""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Optional

from fastapi import HTTPException, Request, Response, status
from starlette.requests import HTTPConnection

from flocks.auth.context import (
    API_TOKEN_SERVICE_USER_ID,
    AuthUser,
    reset_current_auth_session,
    reset_current_auth_user,
    set_current_auth_session,
    set_current_auth_user,
)
from flocks.auth.service import AuthService
from flocks.security import get_secret_manager

SESSION_COOKIE_NAME = "flocks_session"
API_TOKEN_SECRET_ID = "server_api_token"

AuthContextAdapter = Callable[
    [HTTPConnection, AuthUser | None],
    Awaitable[Mapping[str, Any] | None],
]
_auth_context_adapters: dict[str, AuthContextAdapter] = {}


def register_auth_context_adapter(name: str, adapter: AuthContextAdapter) -> None:
    """Register an optional post-auth extension-context adapter."""
    _auth_context_adapters[name] = adapter


def unregister_auth_context_adapter(name: str) -> None:
    _auth_context_adapters.pop(name, None)

# Paths that never require auth. Everything else is protected by default.
PUBLIC_PATHS = frozenset({
    "/",
    "/health",
    "/ready",
    "/favicon.ico",
    "/api/health",
    "/api/ready",
    "/api/config/ui-display",
    "/api/config/ui-favicon",
    "/api/auth/login",
    "/api/auth/bootstrap-status",
    "/api/auth/bootstrap-admin",
    "/auth/login",
    "/auth/bootstrap-status",
    "/auth/bootstrap-admin",
})

# The TUI-prefixed auth paths are public only for the verb that implements
# them: ``/auth/login`` etc. also match the TUI-compatible
# ``PUT /auth/{provider_id}`` route in ``routes/misc.py``, which must stay
# behind auth — a path-only exemption would let an anonymous ``PUT /auth/login``
# reach it.  Paths not listed here are public for every method (an unmapped
# verb then gets the router's usual 405).
PUBLIC_PATH_METHODS: dict[str, frozenset[str]] = {
    "/auth/login": frozenset({"POST", "OPTIONS"}),
    "/auth/bootstrap-admin": frozenset({"POST", "OPTIONS"}),
    "/auth/bootstrap-status": frozenset({"GET", "HEAD", "OPTIONS"}),
}

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
#
# Workflow webhook paths also need to be reachable by external systems that
# cannot present a browser session.  They are safe to exempt here only because
# _authorize_webhook_trigger() fails closed unless the trigger config supplies
# api_key or hmac authentication.  See: https://github.com/AgentFlocks/flocks/issues/454
PUBLIC_PATH_REGEXES = (
    re.compile(r"^/(?:api/)?channel/[^/]+/webhook/?$"),
    re.compile(r"^/webhook/workflows/[^/]+/[^/]+/?$"),
)

# Extensions add their own self-authenticating endpoints (signed webhooks)
# through flocks.extensions.register_public_path; the warning above applies.
_EXTENSION_PUBLIC_PATH_REGEXES: list[re.Pattern[str]] = []


def register_public_path_pattern(pattern: str | re.Pattern[str]) -> re.Pattern[str]:
    """Exempt paths that ``pattern`` matches in full from the auth guard (idempotent).

    Matching uses ``fullmatch``, so neither a missing anchor nor an alternation
    such as ``^/a|/b$`` can widen the exemption to a path prefix.
    """
    if isinstance(pattern, str):
        compiled = re.compile(pattern)
    elif isinstance(pattern, re.Pattern) and isinstance(pattern.pattern, str):
        compiled = pattern
    else:
        raise TypeError("Public path patterns must be str or a compiled str pattern")
    # The same text with other flags (re.IGNORECASE) matches other paths, so both count.
    for existing in _EXTENSION_PUBLIC_PATH_REGEXES:
        if (existing.pattern, existing.flags) == (compiled.pattern, compiled.flags):
            return existing
    _EXTENSION_PUBLIC_PATH_REGEXES.append(compiled)
    return compiled


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


def get_optional_user(request: HTTPConnection) -> Optional[AuthUser]:
    user = getattr(request.state, "auth_user", None)
    return user


def require_user(request: HTTPConnection) -> AuthUser:
    user = get_optional_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return user


def require_admin(request: HTTPConnection) -> AuthUser:
    user = require_user(request)
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="仅管理员可执行该操作")
    return user


def auth_middleware_exempt(path: str, method: Optional[str] = None) -> bool:
    """Return True if the path is public (no auth required).

    ``method`` narrows the fixed-path allowlist for entries listed in
    ``PUBLIC_PATH_METHODS``; when omitted the check is path-only.
    """
    if path in PUBLIC_PATHS:
        allowed = PUBLIC_PATH_METHODS.get(path)
        if method is None or allowed is None or method.upper() in allowed:
            return True
        return False
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return True
    if any(pattern.match(path) for pattern in PUBLIC_PATH_REGEXES):
        return True
    return any(pattern.fullmatch(path) for pattern in _EXTENSION_PUBLIC_PATH_REGEXES)


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


def _has_session_cookie(request: HTTPConnection) -> bool:
    session_id = request.cookies.get(SESSION_COOKIE_NAME)
    return bool(session_id and session_id.strip())


def _is_browser_like_request(request: HTTPConnection) -> bool:
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


def _read_api_token_from_request(request: HTTPConnection) -> Optional[str]:
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
        id=API_TOKEN_SERVICE_USER_ID,
        username=API_TOKEN_SERVICE_USER_ID,
        role="admin",
        status="active",
        must_reset_password=False,
    )


async def apply_auth_for_request(request: HTTPConnection):
    """Resolve auth for the HTTP request."""
    from flocks.plugin import PluginLoader

    # The same loader signal already stops Channel/tool execution. HTTP and
    # WebSocket ingress must also stop, including public mutation/webhook
    # routes that otherwise bypass authentication. Keep probes reachable.
    if (
        PluginLoader.has_runtime_critical_entrypoint_failure()
        and request.url.path not in {"/health", "/ready", "/api/health", "/api/ready"}
    ):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="关键扩展初始化失败，服务暂不可用",
        )
    auth_token = None

    try:
        result = await _apply_auth_for_request(request)
        _blocked, auth_token, user = result
        extension_context: dict[str, Any] = {}
        # Snapshot: an adapter may (re)register adapters while awaited, and
        # resuming iteration over the live dict would raise RuntimeError.
        for adapter in list(_auth_context_adapters.values()):
            supplied = await adapter(request, user)
            if isinstance(supplied, Mapping):
                extension_context.update(supplied)
        if extension_context:
            request.state.extension_context = extension_context
        return result
    except BaseException:
        if auth_token is not None:
            clear_auth_context(auth_token)
        raise


async def _apply_auth_for_request(request: HTTPConnection):
    """
    Resolve user from cookie and bind context var.
    Returns (response_if_blocked, token, user).

    Policy: every path is protected by default; only whitelisted public paths
    (see `PUBLIC_PATHS` / `PUBLIC_PREFIXES`) bypass auth.
    """
    if auth_middleware_exempt(request.url.path, request.scope.get("method")):
        bound = _bind_auth_context(None)
        return None, bound, None

    if _has_session_cookie(request):
        bootstrapped = await AuthService.has_users()
        if not bootstrapped:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="系统尚未初始化管理员账号，请先完成初始化",
            )

        session_id = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
        if not session_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

        user = await AuthService.get_user_by_session_id(session_id)
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")

        auth_user = user.to_auth_user()
        if auth_user.must_reset_password and not password_reset_exempt(request.url.path):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="当前账号必须先修改密码后才能继续使用",
            )
        request.state.auth_user = auth_user
        bound = _bind_auth_context(auth_user, session_id)
        return None, bound, auth_user

    # Non-browser clients must authenticate with an API token because
    # localhost is not a reliable auth boundary.
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
            bound = _bind_auth_context(token_user)
            return None, bound, token_user

        expected = _get_expected_api_token()
        if not expected:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"非浏览器请求需要 API Token，请先在 .secret.json 中配置 {API_TOKEN_SECRET_ID}",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="非浏览器请求鉴权失败，请在 Authorization 中携带 Bearer API Token",
        )

    bootstrapped = await AuthService.has_users()
    if not bootstrapped:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="系统尚未初始化管理员账号，请先完成初始化",
        )

    # A whitespace-only cookie is "no cookie" for _has_session_cookie(); keep
    # the same reading here so it lands on 请先登录, not 登录已过期.
    session_id = (request.cookies.get(SESSION_COOKIE_NAME) or "").strip()
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")

    user = await AuthService.get_user_by_session_id(session_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录已过期，请重新登录")

    auth_user = user.to_auth_user()
    if auth_user.must_reset_password and not password_reset_exempt(request.url.path):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号必须先修改密码后才能继续使用",
        )
    request.state.auth_user = auth_user
    bound = _bind_auth_context(auth_user, session_id)
    return None, bound, auth_user


class _AuthContextToken:
    """Both context values a request sets: who it is, and the login session it came with."""

    __slots__ = ("user", "session")

    def __init__(self, user, session) -> None:
        self.user = user
        self.session = session


def _bind_auth_context(user: Optional[AuthUser], session_id: Optional[str] = None) -> _AuthContextToken:
    return _AuthContextToken(set_current_auth_user(user), set_current_auth_session(session_id))


def clear_auth_context(token) -> None:
    if isinstance(token, _AuthContextToken):
        reset_current_auth_session(token.session)
        reset_current_auth_user(token.user)
        return
    reset_current_auth_user(token)
