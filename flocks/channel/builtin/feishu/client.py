"""
Lightweight async wrapper around the Feishu Open API.

Handles tenant_access_token refresh and basic HTTP calls.
Uses a persistent ``httpx.AsyncClient`` to reuse TCP connections.

Supports multiple accounts via per-account (app_id) token caches and
domain-aware API base URL (feishu vs lark).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx

from flocks.channel.builtin.feishu.config import resolve_api_base, resolve_token_url
from flocks.utils.log import Log

log = Log.create(service="channel.feishu.client")

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


# --- token cache (keyed by app_id) ---

_token_cache: Dict[str, tuple[str, float]] = {}
_token_lock = asyncio.Lock()


# Per-key locks to allow concurrent token refreshes for different app_ids
_per_key_locks: Dict[str, asyncio.Lock] = {}


class FeishuApiError(RuntimeError):
    """Structured Feishu API error with business code and retryability hints."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
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
    code: Optional[int],
    http_status: Optional[int],
    message: str,
) -> bool:
    msg = message.lower()
    if http_status in {408, 429, 500, 502, 503, 504}:
        return True
    if code in {99991663}:
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
    """Raise :class:`FeishuApiError` when a Feishu business response is not successful."""
    code = data.get("code")
    if code in (None, 0):
        return data
    msg = str(data.get("msg") or data.get("message") or f"code {code}")
    raise FeishuApiError(
        f"{context}: {msg}",
        code=code if isinstance(code, int) else None,
        http_status=http_status,
        retryable=_is_retryable_error(
            code=code if isinstance(code, int) else None,
            http_status=http_status,
            message=msg,
        ),
        response=data,
    )


async def get_tenant_token(app_id: str, app_secret: str, token_url: Optional[str] = None) -> str:
    """Obtain (or reuse cached) tenant_access_token.

    ``token_url`` may be passed explicitly to support domain switching
    (feishu vs lark). If omitted, the default feishu endpoint is used.
    """
    from flocks.channel.builtin.feishu.config import FEISHU_TOKEN_URL
    url = token_url or FEISHU_TOKEN_URL
    cache_key = f"{url}|{app_id}"

    # Fast path: read from cache without holding the refresh lock
    cached = _token_cache.get(cache_key)
    if cached:
        token, expires_at = cached
        if time.time() < expires_at - 60:
            return token

    # Slow path: per-key lock so different app_ids don't block each other
    async with _token_lock:
        if cache_key not in _per_key_locks:
            _per_key_locks[cache_key] = asyncio.Lock()
        key_lock = _per_key_locks[cache_key]

    async with key_lock:
        # Double-check after acquiring lock
        cached = _token_cache.get(cache_key)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at - 60:
                return token

        client = await _get_http_client()
        resp = await client.post(url, json={
            "app_id": app_id,
            "app_secret": app_secret,
        })
        resp.raise_for_status()
        data = resp.json()

        ensure_api_success(
            data,
            context="Feishu tenant token request failed",
            http_status=resp.status_code,
        )
        token = data.get("tenant_access_token")
        if not token:
            raise FeishuApiError(
                "Feishu tenant token request failed: missing tenant_access_token",
                http_status=resp.status_code,
                response=data,
            )
        expire = data.get("expire", 7200)
        _token_cache[cache_key] = (token, time.time() + expire)
        return token


def _resolve_account_credentials(
    config: dict,
    account_id: Optional[str],
) -> tuple[str, str]:
    """Extract (app_id, app_secret) for the given account.

    For the default (single-account) case ``account_id`` is None / "default".
    Named accounts are looked up under ``config["accounts"][account_id]``,
    falling back to top-level keys if the named account doesn't override them.
    """
    if account_id and account_id != "default":
        accounts = config.get("accounts", {})
        acc = accounts.get(account_id, {})
        app_id = acc.get("appId") or config.get("appId", "")
        app_secret = acc.get("appSecret") or config.get("appSecret", "")
        return app_id, app_secret
    return config.get("appId", ""), config.get("appSecret", "")


def _resolve_account_config(config: dict, account_id: Optional[str]) -> dict:
    """Merge top-level config with the named account's overrides.

    Fields like ``domain`` and ``connectionMode`` can be overridden per-account.
    """
    if not account_id or account_id == "default":
        return config
    accounts = config.get("accounts", {})
    acc = accounts.get(account_id, {})
    if not acc:
        return config
    merged = {**config, **acc}
    merged.pop("accounts", None)
    return merged


async def api_request(
    method: str,
    path: str,
    *,
    app_id: str,
    app_secret: str,
    config: Optional[dict] = None,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    account_id: Optional[str] = None,
) -> dict:
    """Send an authenticated request to the Feishu Open API.

    ``config`` is used to resolve the domain-aware API base URL and token URL.
    When omitted the default (domestic feishu) endpoints are used.
    """
    cfg = config or {}
    api_base = resolve_api_base(cfg)
    token_url = resolve_token_url(cfg)

    token = await get_tenant_token(app_id, app_secret, token_url)
    url = f"{api_base}{path}" if not path.startswith("http") else path

    client = await _get_http_client()
    resp = await client.request(
        method, url,
        params=params,
        json=json_body,
        headers={"Authorization": f"Bearer {token}"},
    )
    resp.raise_for_status()
    data = resp.json()
    return ensure_api_success(
        data,
        context=f"Feishu API request failed: {method} {path}",
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
    acc_config = _resolve_account_config(config, account_id)
    app_id, app_secret = _resolve_account_credentials(config, account_id)
    if not app_id or not app_secret:
        raise ValueError(
            "Feishu appId/appSecret not configured"
            + (f" for account '{account_id}'" if account_id else "")
        )
    return await api_request(
        method, path,
        app_id=app_id,
        app_secret=app_secret,
        config=acc_config,
        params=params,
        json_body=json_body,
        account_id=account_id,
    )
