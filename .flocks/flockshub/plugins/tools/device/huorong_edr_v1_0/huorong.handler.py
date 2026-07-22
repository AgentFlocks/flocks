from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.parse as up
from typing import Any, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult


DEFAULT_BASE_URL = "http://localhost:801"
DEFAULT_TIMEOUT = 30
SERVICE_ID = "huorong_api"


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[len("{secret:"):-1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:"):-1])
    return value


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    value = raw.get("verify_ssl")
    if value is None:
        value = raw.get("ssl_verify")
    if value is None:
        custom_settings = raw.get("custom_settings", {})
        if isinstance(custom_settings, dict):
            value = custom_settings.get("verify_ssl", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _resolve_runtime_config() -> tuple[str, int, str, str, bool]:
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or DEFAULT_BASE_URL
    ).rstrip("/")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    secret_manager = _get_secret_manager()

    secret_id = (
        _resolve_ref(raw.get("secretId"))
        or _resolve_ref(raw.get("secret_id"))
        or secret_manager.get("huorong_secret_id")
        or os.getenv("HUORONG_SECRET_ID")
    )
    secret_key = (
        _resolve_ref(raw.get("secretKey"))
        or _resolve_ref(raw.get("secret_key"))
        or secret_manager.get("huorong_secret_key")
        or os.getenv("HUORONG_SECRET_KEY")
    )
    if not secret_id or not secret_key:
        raise ValueError(
            "Huorong API credentials not found. Configure secretId and secretKey "
            "in the huorong_api service settings."
        )
    return base_url, timeout, secret_id, secret_key, _resolve_verify_ssl(raw)


def _build_auth_header(
    secret_id: str,
    secret_key: str,
    method: str,
    path: str,
    body: str,
) -> str:
    """
    Build Huorong HMAC-SHA1 Authorization header.

    Authorization = HRESS{secret_id}:{expires}:{url_encoded_sign}
    StringToSign  = {secret_id}\\n{expires}\\n{method}\\n{content_md5}\\n{canonical_resource}
    content_md5   = base64(md5(body))
    canonical_resource = path without leading "/"
    sign          = url_encode(base64(hmac-sha1(secret_key, string_to_sign)))
    """
    expires = int(time.time()) + 3600
    body_bytes = body.encode("utf-8") if isinstance(body, str) else body
    content_md5 = base64.b64encode(
        hashlib.md5(body_bytes).digest()
    ).decode("utf-8")
    canonical_resource = path.lstrip("/")
    string_to_sign = f"{secret_id}\n{expires}\n{method}\n{content_md5}\n{canonical_resource}"
    sign_bytes = hmac.new(
        secret_key.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        "sha1",
    ).digest()
    sign = up.quote(base64.b64encode(sign_bytes).decode("utf-8"))
    return f"HRESS{secret_id}:{expires}:{sign}"


async def _post(
    base_url: str,
    path: str,
    body: dict[str, Any],
    secret_id: str,
    secret_key: str,
    timeout: int,
    verify_ssl: bool,
) -> ToolResult:
    body_str = json.dumps(body, ensure_ascii=False)
    auth_header = _build_auth_header(secret_id, secret_key, "POST", path, body_str)
    url = f"{base_url}{path}"
    connector = aiohttp.TCPConnector(ssl=verify_ssl)
    async with aiohttp.ClientSession(connector=connector) as session:
        async with session.post(
            url,
            data=body_str.encode("utf-8"),
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "Authorization": auth_header,
            },
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            resp_text = await resp.text()
            try:
                resp_json = json.loads(resp_text)
            except Exception:
                resp_json = {"raw": resp_text}
            if resp.status >= 400:
                return ToolResult(
                    success=False,
                    output=resp_json,
                    error=f"HTTP {resp.status}: {resp_text[:200]}",
                )
            return ToolResult(success=True, output=resp_json)


def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: params[k] for k in keys if k in params and params[k] is not None}


# ---------------------------------------------------------------------------
# Public handler functions (one per tool YAML's `function` field)
# ---------------------------------------------------------------------------


async def group(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    base_url, timeout, secret_id, secret_key, verify_ssl = _resolve_runtime_config()

    if action == "group_list":
        return await _post(base_url, "/api/group/_list", {}, secret_id, secret_key, timeout, verify_ssl)

    if action == "group_create":
        body = _pick(params, "group_name", "parent_group")
        body.setdefault("parent_group", 0)
        return await _post(base_url, "/api/group/_create", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "group_rename":
        body = _pick(params, "group_id", "group_name")
        return await _post(base_url, "/api/group/_rename", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "group_delete":
        body = _pick(params, "group_id")
        return await _post(base_url, "/api/group/_delete", body, secret_id, secret_key, timeout, verify_ssl)

    return ToolResult(success=False, error=f"Unknown action: {action}")


async def clnts(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    base_url, timeout, secret_id, secret_key, verify_ssl = _resolve_runtime_config()

    if action == "clnts_online":
        body = _pick(params, "offset")
        body.setdefault("offset", 0)
        return await _post(base_url, "/api/clnts/_online", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_list":
        body = _pick(params, "offset")
        body.setdefault("offset", 0)
        return await _post(base_url, "/api/clnts/_list", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_info":
        body = _pick(params, "clients")
        return await _post(base_url, "/api/clnts/_info", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_info2":
        body = _pick(params, "clients")
        return await _post(base_url, "/api/clnts/_info2", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_rename":
        body = _pick(params, "client_id", "client_name")
        return await _post(base_url, "/api/clnts/_rename", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_group":
        body = _pick(params, "group_id", "clients")
        return await _post(base_url, "/api/clnts/_group", body, secret_id, secret_key, timeout, verify_ssl)

    if action == "clnts_leak":
        body = _pick(params, "clients")
        return await _post(base_url, "/api/clnts/_leak", body, secret_id, secret_key, timeout, verify_ssl)

    return ToolResult(success=False, error=f"Unknown action: {action}")


async def task(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    base_url, timeout, secret_id, secret_key, verify_ssl = _resolve_runtime_config()

    if action == "task_create":
        body = _pick(params, "offset")
        body.setdefault("offset", 0)
        return await _post(base_url, "/api/task/_create", body, secret_id, secret_key, timeout, verify_ssl)

    return ToolResult(success=False, error=f"Unknown action: {action}")
