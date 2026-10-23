"""
Lightweight async wrapper around the DingTalk OAPI (enterprise app robot).

Handles ``access_token`` refresh and basic HTTP calls.  Uses a persistent
``httpx.AsyncClient`` to reuse TCP connections, mirroring the Feishu client.

Multi-account is supported via per-account (``appKey``) token caches.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx

from flocks.channel.builtin.dingtalk.config import (
    DINGTALK_API_BASE,
    DINGTALK_TOKEN_URL,
    resolve_account_config,
    resolve_account_credentials,
)
from flocks.utils.log import Log

log = Log.create(service="channel.dingtalk.client")

# --- persistent HTTP client ---

_http_client: Optional[httpx.AsyncClient] = None
_http_lock = asyncio.Lock()


async def _get_http_client() -> httpx.AsyncClient:
    """Return (and lazily create) the shared persistent HTTP client."""
    global _http_client
    if _http_client is not None and not _http_client.is_closed:
        return _http_client
    async with _http_lock:
        if _http_client is not None and not _http_client.is_closed:
            return _http_client
        _http_client = httpx.AsyncClient(
            timeout=30,
            limits=httpx.Limits(
                max_connections=20,
                max_keepalive_connections=10,
            ),
        )
        return _http_client


async def close_http_client() -> None:
    """Close the persistent HTTP client (call during shutdown)."""
    global _http_client
    if _http_client is not None:
        try:
            await _http_client.aclose()
        except Exception:
            pass
        _http_client = None


# --- token cache (keyed by appKey) ---

_token_cache: Dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()
_per_key_locks: Dict[str, asyncio.Lock] = {}


class DingTalkApiError(RuntimeError):
    """Structured DingTalk API error with business code and retryability hints."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[str] = None,
        http_status: Optional[int] = None,
        retryable: bool = False,
        response: Optional[dict] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.retryable = retryable
        self.response = response or {}


def _is_retryable_error(
    *,
    code: Optional[str],
    http_status: Optional[int],
    message: str,
) -> bool:
    msg = message.lower()
    if http_status in {408, 429, 500, 502, 503, 504}:
        return True
    if code and code.lower() in {
        "internalerror",
        "throttling.api",
        "throttling.user",
        "throttling",
    }:
        return True
    return (
        "rate limit" in msg
        or "too many requests" in msg
        or "timeout" in msg
        or "temporarily unavailable" in msg
    )


def ensure_api_success(
    data: dict,
    *,
    context: str,
    http_status: Optional[int] = None,
) -> dict:
    """Raise :class:`DingTalkApiError` when a response indicates failure.

    The OAPI v1.0 endpoints typically return ``{"code": "...", "message": "..."}``
    on error and a domain payload on success.  Some legacy endpoints return
    ``{"errcode": 0, "errmsg": "ok", ...}`` instead, so both are handled.
    """
    # Legacy /robot/send style payload
    if "errcode" in data:
        errcode = data.get("errcode")
        if errcode in (0, "0", None):
            return data
        msg = str(data.get("errmsg") or f"errcode {errcode}")
        raise DingTalkApiError(
            f"{context}: {msg}",
            code=str(errcode),
            http_status=http_status,
            retryable=_is_retryable_error(
                code=str(errcode),
                http_status=http_status,
                message=msg,
            ),
            response=data,
        )

    # OAPI v1.0 style error payload
    code = data.get("code")
    if code is None or code == "":
        return data
    msg = str(data.get("message") or data.get("msg") or f"code {code}")
    raise DingTalkApiError(
        f"{context}: {msg}",
        code=str(code),
        http_status=http_status,
        retryable=_is_retryable_error(
            code=str(code),
            http_status=http_status,
            message=msg,
        ),
        response=data,
    )


async def get_access_token(app_key: str, app_secret: str) -> str:
    """Obtain (or reuse cached) DingTalk OAPI access_token.

    The v1.0 endpoint returns ``{"accessToken": "...", "expireIn": 7200}``.
    """
    cache_key = f"{DINGTALK_TOKEN_URL}|{app_key}"

    cached = _token_cache.get(cache_key)
    if cached:
        token, expires_at = cached
        if time.time() < expires_at - 60:
            return token

    async with _token_lock:
        if cache_key not in _per_key_locks:
            _per_key_locks[cache_key] = asyncio.Lock()
        key_lock = _per_key_locks[cache_key]

    async with key_lock:
        cached = _token_cache.get(cache_key)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at - 60:
                return token

        client = await _get_http_client()
        resp = await client.post(DINGTALK_TOKEN_URL, json={
            "appKey": app_key,
            "appSecret": app_secret,
        })
        resp.raise_for_status()
        data = resp.json()

        ensure_api_success(
            data,
            context="DingTalk access token request failed",
            http_status=resp.status_code,
        )
        token = data.get("accessToken")
        if not token:
            raise DingTalkApiError(
                "DingTalk access token request failed: missing accessToken",
                http_status=resp.status_code,
                response=data,
            )
        expire = int(data.get("expireIn") or 7200)
        _token_cache[cache_key] = (token, time.time() + expire)
        return token


async def api_request(
    method: str,
    path: str,
    *,
    app_key: str,
    app_secret: str,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> dict:
    """Send an authenticated request to the DingTalk OAPI v1.0 endpoints."""
    token = await get_access_token(app_key, app_secret)
    url = f"{DINGTALK_API_BASE}{path}" if not path.startswith("http") else path
    client = await _get_http_client()
    resp = await client.request(
        method, url,
        params=params,
        json=json_body,
        headers={
            "x-acs-dingtalk-access-token": token,
            "Content-Type": "application/json",
        },
    )
    # OAPI v1.0 returns a non-2xx status for most business errors; surface the
    # body before raising so we get the structured ``code`` / ``message`` instead
    # of an opaque ``HTTPStatusError``.
    try:
        data = resp.json()
    except ValueError:
        resp.raise_for_status()
        return {}

    if resp.status_code >= 400 and isinstance(data, dict):
        ensure_api_success(
            data,
            context=f"DingTalk API request failed: {method} {path}",
            http_status=resp.status_code,
        )

    resp.raise_for_status()
    return ensure_api_success(
        data if isinstance(data, dict) else {},
        context=f"DingTalk API request failed: {method} {path}",
        http_status=resp.status_code,
    )


async def api_request_for_account(
    method: str,
    path: str,
    *,
    config: dict,
    account_id: Optional[str] = None,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
) -> dict:
    """Convenience wrapper that resolves credentials from config + account_id."""
    _ = resolve_account_config(config, account_id)
    app_key, app_secret, _robot_code = resolve_account_credentials(config, account_id)
    if not app_key or not app_secret:
        raise ValueError(
            "DingTalk appKey/appSecret not configured"
            + (f" for account '{account_id}'" if account_id else "")
        )
    return await api_request(
        method, path,
        app_key=app_key, app_secret=app_secret,
        params=params, json_body=json_body,
    )
