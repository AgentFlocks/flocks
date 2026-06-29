from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from typing import Any

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "onesig_v2_5_3_api"
DEFAULT_TIMEOUT = 60


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[len("{secret:") : -1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:") : -1])
    return value


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


def _ensure_scheme(value: str) -> str:
    if value and not value.startswith(("http://", "https://")):
        return "https://" + value
    return value


def _bool_value(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _runtime_config() -> tuple[str, str, str, int, bool]:
    raw = _service_config()
    base_url = _ensure_scheme(
        (
            _resolve_ref(raw.get("base_url"))
            or _resolve_ref(raw.get("baseUrl"))
            or os.getenv("ONESIG_V2_5_3_BASE_URL")
            or ""
        ).rstrip("/")
    )
    api_key = (
        _resolve_ref(raw.get("api_key"))
        or _resolve_ref(raw.get("apiKey"))
        or _get_secret_manager().get("onesig_v2_5_3_api_key")
        or os.getenv("ONESIG_V2_5_3_API_KEY")
        or ""
    )
    secret = (
        _resolve_ref(raw.get("secret"))
        or _get_secret_manager().get("onesig_v2_5_3_secret")
        or os.getenv("ONESIG_V2_5_3_SECRET")
        or ""
    )
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    verify_ssl = _bool_value(raw.get("verify_ssl"), False)
    custom_settings = raw.get("custom_settings")
    if isinstance(custom_settings, dict):
        verify_ssl = _bool_value(custom_settings.get("verify_ssl"), verify_ssl)
    return base_url, api_key, secret, timeout, verify_ssl


def _signed_query(api_key: str, secret: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    sign_data = f"{api_key}{timestamp}".encode()
    digest = hmac.new(secret.encode(), sign_data, hashlib.sha1).digest()
    return {
        "apikey": api_key,
        "timestamp": timestamp,
        "sign": base64.b64encode(digest).decode(),
    }


def _json_result(action: str, data: Any) -> ToolResult:
    metadata = {"source": "OneSIG Strategy API", "action": action}
    if isinstance(data, dict):
        code = data.get("response_code")
        if code is not None and code not in (0, "0"):
            return ToolResult(
                success=False,
                error=f"OneSIG Strategy API error (code={code}): {data.get('verbose_msg') or data}",
                metadata=metadata,
            )
        return ToolResult(success=True, output=data.get("data", data), metadata=metadata)
    return ToolResult(success=True, output=data, metadata=metadata)


def _payload_from_params(params: dict[str, Any]) -> dict[str, Any]:
    body = params.get("body")
    if isinstance(body, dict):
        return body
    return {
        key: value
        for key, value in params.items()
        if key not in {"action", "body"} and value is not None
    }


async def _request(
    action: str,
    method: str,
    path: str,
    params: dict[str, Any],
) -> ToolResult:
    base_url, api_key, secret, timeout, verify_ssl = _runtime_config()
    if not base_url:
        return ToolResult(success=False, error="OneSIG Strategy API base_url is not configured.")
    if not api_key or not secret:
        return ToolResult(success=False, error="OneSIG Strategy API ApiKey and Secret are required.")

    url = f"{base_url}{path}"
    query = _signed_query(api_key, secret)
    payload = _payload_from_params(params)
    headers = {"Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            request = session.request(
                method,
                url,
                params=query,
                json=payload if method != "GET" else None,
                headers=headers,
                ssl=verify_ssl,
            )
            async with request as resp:
                text = await resp.text()
                if resp.status >= 400:
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    return ToolResult(success=True, output=text, metadata={"source": "OneSIG Strategy API", "action": action})
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    return _json_result(action, data)


QUERY_ACTIONS: dict[str, tuple[str, str]] = {
    "platform_status": ("POST", "/api/v3/device/platformStatus"),
    "system_status": ("POST", "/api/v3/device/systemStatus"),
    "network_status": ("POST", "/api/v3/device/networkStatus"),
    "asset_group_list": ("GET", "/api/v3/asset/group"),
    "asset_list": ("POST", "/api/v3/asset/list"),
    "asset_type_list": ("GET", "/api/v3/asset/type"),
    "protection_policy_list": ("POST", "/api/v3/protection/policy"),
    "whitelist_list": ("POST", "/api/v3/globalWhitelist/list"),
    "blacklist_list": ("POST", "/api/v3/globalBlacklist/list"),
    "banned_whitelist_list": ("POST", "/api/v3/bannedWhitelist/list"),
    "http_blacklist_list": ("POST", "/api/v3/httpBlacklist/list"),
}

OPS_ACTIONS: dict[str, tuple[str, str]] = {
    "asset_group_create": ("POST", "/api/v3/asset/group/create"),
    "asset_group_update": ("POST", "/api/v3/asset/group/update"),
    "asset_group_delete": ("POST", "/api/v3/asset/group/delete"),
    "asset_create": ("POST", "/api/v3/asset/create"),
    "asset_update": ("POST", "/api/v3/asset/update"),
    "asset_delete": ("POST", "/api/v3/asset/delete"),
    "protection_policy_update": ("POST", "/api/v3/protection/policy/update"),
    "protection_policy_delete": ("POST", "/api/v3/protection/policy/delete"),
    "whitelist_create": ("POST", "/api/v3/globalWhitelist/create"),
    "whitelist_update": ("POST", "/api/v3/globalWhitelist/update"),
    "whitelist_delete": ("POST", "/api/v3/globalWhitelist/delete"),
    "whitelist_remove": ("POST", "/api/v3/globalWhitelist/remove"),
    "blacklist_create": ("POST", "/api/v3/globalBlacklist/create"),
    "blacklist_update": ("POST", "/api/v3/globalBlacklist/update"),
    "blacklist_delete": ("POST", "/api/v3/globalBlacklist/delete"),
    "blacklist_remove": ("POST", "/api/v3/globalBlacklist/remove"),
    "banned_whitelist_create": ("POST", "/api/v3/bannedWhitelist/create"),
    "banned_whitelist_update": ("POST", "/api/v3/bannedWhitelist/update"),
    "banned_whitelist_delete": ("POST", "/api/v3/bannedWhitelist/delete"),
    "http_blacklist_create": ("POST", "/api/v3/httpBlacklist/create"),
    "http_blacklist_update": ("POST", "/api/v3/httpBlacklist/update"),
    "http_blacklist_enable": ("POST", "/api/v3/httpBlacklist/enable"),
    "http_blacklist_delete": ("POST", "/api/v3/httpBlacklist/delete"),
}


async def query(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    spec = QUERY_ACTIONS.get(action)
    if not spec:
        return ToolResult(success=False, error=f"Unsupported OneSIG Strategy API query action: {action}")
    return await _request(action, spec[0], spec[1], params)


async def ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    spec = OPS_ACTIONS.get(action)
    if not spec:
        return ToolResult(success=False, error=f"Unsupported OneSIG Strategy API ops action: {action}")
    return await _request(action, spec[0], spec[1], params)
