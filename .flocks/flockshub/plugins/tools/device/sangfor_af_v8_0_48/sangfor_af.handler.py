"""
Sangfor AF (Application Firewall) v8.0.48 API Handler.

Authentication:
  - Session-based: POST /api/v1/namespaces/public/login → token
  - All subsequent requests: Cookie: token=<token>
  - Token expires after ~10 min of inactivity (keepalive resets timer)

API base URL: https://<device_ip>
Namespace:    /api/v1/namespaces/public/
Batch ops:    /api/batch/v1/namespaces/public/
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

# ── Constants ────────────────────────────────────────────────────────────────

SERVICE_ID = "sangfor_af_v8_0_48"
DEFAULT_BASE_URL = "https://192.168.1.1"
DEFAULT_TIMEOUT = 60
NAMESPACE = "public"

API_V1 = f"/api/v1/namespaces/{NAMESPACE}"
API_BATCH = f"/api/batch/v1/namespaces/{NAMESPACE}"

# In-process token cache: {base_url: token}
_TOKEN_CACHE: dict[str, str] = {}


# ── Secret / Config helpers ───────────────────────────────────────────────────

def _get_secret_manager():
    from flocks.security import get_secret_manager
    return get_secret_manager()


def _resolve_ref(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[len("{secret:"): -1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:"): -1])
    return value


def _service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    return raw if isinstance(raw, dict) else {}


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    """Read verify_ssl with the same priority as sangfor_sip / onesec:
    verify_ssl > ssl_verify > custom_settings.verify_ssl > False.
    AF devices commonly use self-signed certs, so default is False.
    """
    value = raw.get("verify_ssl")
    if value is None:
        value = raw.get("ssl_verify")
    if value is None:
        custom = raw.get("custom_settings")
        if isinstance(custom, dict):
            value = custom.get("verify_ssl")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return False


def _resolve_runtime_config() -> tuple[str, int, str, str, bool]:
    """Returns (base_url, timeout, username, password, verify_ssl)."""
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url")) or DEFAULT_BASE_URL
    ).rstrip("/")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    sm = _get_secret_manager()

    username = (
        _resolve_ref(raw.get("username"))
        or sm.get("sangfor_af_v8_0_48_username")
        or os.getenv("AF_USERNAME")
    )
    password = (
        _resolve_ref(raw.get("password"))
        or sm.get("sangfor_af_v8_0_48_password")
        or os.getenv("AF_PASSWORD")
    )

    if not username or not password:
        raise ValueError(
            "AF API credentials not configured. "
            "Please set username and password in the service configuration."
        )
    return base_url, timeout, username, password, _resolve_verify_ssl(raw)


# ── Session / Token management ────────────────────────────────────────────────

async def _login(
    session: aiohttp.ClientSession,
    base_url: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> tuple[Optional[str], Optional[str]]:
    """Login and return (token, error_message)."""
    url = f"{base_url}{API_V1}/login"
    try:
        async with session.post(
            url,
            json={"name": username, "password": password},
            ssl=verify_ssl,
        ) as resp:
            data = await resp.json(content_type=None)
    except aiohttp.ClientError as exc:
        return None, f"AF login request failed: {exc}"

    code = data.get("code")
    if code != 0:
        msg = data.get("message", "Unknown error")
        return None, f"AF login failed (code={code}): {msg}"

    token = (
        data.get("data", {}).get("loginResult", {}).get("token")
    )
    if not token:
        return None, "AF login succeeded but no token returned"
    return token, None


async def _get_token(
    session: aiohttp.ClientSession,
    base_url: str,
    username: str,
    password: str,
    verify_ssl: bool,
) -> tuple[Optional[str], Optional[str]]:
    """Return cached token or obtain a new one."""
    cached = _TOKEN_CACHE.get(base_url)
    if cached:
        # Validate by keepalive
        try:
            async with session.get(
                f"{base_url}{API_V1}/keepalive",
                headers={"Cookie": f"token={cached}"},
                ssl=verify_ssl,
            ) as resp:
                ka_data = await resp.json(content_type=None)
            if ka_data.get("code") == 0:
                return cached, None
        except Exception:
            pass

    token, err = await _login(session, base_url, username, password, verify_ssl)
    if err:
        return None, err
    _TOKEN_CACHE[base_url] = token
    return token, None


# ── Low-level HTTP ────────────────────────────────────────────────────────────

def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: params[k] for k in keys if k in params and params[k] is not None}


def _af_result(action: str, payload: Any) -> ToolResult:
    metadata = {"source": "Sangfor AF", "api": action, "version": "8.0.48"}
    if isinstance(payload, dict):
        code = payload.get("code")
        if code not in (None, 0):
            msg = payload.get("message", "Unknown error")
            return ToolResult(
                success=False,
                error=f"AF API error (code={code}): {msg}",
                metadata=metadata,
            )
        return ToolResult(
            success=True,
            output=payload.get("data", payload),
            metadata=metadata,
        )
    return ToolResult(success=True, output=payload, metadata=metadata)


async def _call(
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    json: Optional[Any] = None,
    action: str = "",
) -> ToolResult:
    """Execute an authenticated AF API request."""
    try:
        base_url, timeout, username, password, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    headers = {"Content-Type": "application/json"}

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        token, err = await _get_token(session, base_url, username, password, verify_ssl)
        if err:
            return ToolResult(success=False, error=err)

        headers["Cookie"] = f"token={token}"
        url = f"{base_url}{path}"

        try:
            async with session.request(
                method.upper(),
                url,
                params=params,
                json=json,
                headers=headers,
                ssl=verify_ssl,
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return ToolResult(
                        success=False,
                        error=f"HTTP {resp.status}: {text[:500]}",
                    )
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            return ToolResult(success=False, error=f"Request failed: {exc}")
        except Exception as exc:
            return ToolResult(success=False, error=f"Unexpected error: {exc}")

    return _af_result(action or path.rsplit("/", 1)[-1], data)


# ── Action specs ─────────────────────────────────────────────────────────────

class ActionSpec:
    def __init__(
        self,
        method: str,
        path_template: str,
        param_builder: Callable[[dict[str, Any]], tuple[
            Optional[dict], Optional[Any]
        ]],
        required: tuple[str, ...] = (),
    ) -> None:
        self.method = method
        self.path_template = path_template
        self.param_builder = param_builder
        self.required = required

    def build_path(self, params: dict[str, Any]) -> str:
        try:
            return self.path_template.format(**params)
        except KeyError:
            return self.path_template


# ── Auth actions ──────────────────────────────────────────────────────────────

async def _do_login(ctx: ToolContext, **params: Any) -> ToolResult:
    """Explicitly login and refresh the cached token."""
    del ctx
    try:
        base_url, timeout, username, password, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        token, err = await _login(session, base_url, username, password, verify_ssl)
        if err:
            return ToolResult(success=False, error=err)
        _TOKEN_CACHE[base_url] = token
        return ToolResult(
            success=True,
            output={"token": token, "message": "Login successful"},
            metadata={"source": "Sangfor AF", "api": "login", "version": "8.0.48"},
        )


async def _do_logout(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    result = await _call("POST", f"{API_V1}/logout", action="logout")
    try:
        base_url, *_ = _resolve_runtime_config()
        _TOKEN_CACHE.pop(base_url, None)
    except ValueError:
        pass
    return result


async def _do_keepalive(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/keepalive", action="keepalive")


# ── Objects actions ──────────────────────────────────────────────────────────

async def _do_get_ipgroups(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(
        params,
        "_start", "_length", "businessType", "__nameprefix", "important",
        "_search", "_order", "_sortby", "addressType",
    )
    return await _call("GET", f"{API_V1}/ipgroups", params=query, action="get_ipgroups")


async def _do_get_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    uuid = params.get("uuid", "")
    return await _call("GET", f"{API_V1}/ipgroups/{uuid}", action="get_ipgroup")


async def _do_create_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(
        params,
        "name", "businessType", "description", "addressType", "important",
        "ipRanges", "creator",
    )
    return await _call("POST", f"{API_V1}/ipgroups", json={"obj": body}, action="create_ipgroup")


async def _do_update_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    uuid = params.get("uuid", "")
    body = _pick(
        params,
        "name", "businessType", "description", "addressType", "important",
        "ipRanges", "creator",
    )
    return await _call("PATCH", f"{API_V1}/ipgroups/{uuid}", json={"obj": body}, action="update_ipgroup")


async def _do_delete_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    uuid = params.get("uuid", "")
    return await _call("DELETE", f"{API_V1}/ipgroups/{uuid}", action="delete_ipgroup")


async def _do_get_services(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_search", "_order", "_sortby", "serviceType")
    return await _call("GET", f"{API_V1}/services", params=query, action="get_services")


async def _do_get_service(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    uuid = params.get("uuid", "")
    return await _call("GET", f"{API_V1}/services/{uuid}", action="get_service")


# ── Operations center actions ─────────────────────────────────────────────────

async def _do_get_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "type", "_start", "_length", "_search", "_order", "description")
    return await _call("GET", f"{API_V1}/whiteblacklist", params=query, action="get_blackwhitelist")


async def _do_add_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "url", "type", "enable", "description", "domain")
    return await _call("POST", f"{API_V1}/whiteblacklist", json={"obj": body}, action="add_blackwhitelist")


async def _do_batch_add_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    items = params.get("items", [])
    return await _call(
        "POST",
        f"{API_BATCH}/whiteblacklist",
        json=items,
        action="batch_add_blackwhitelist",
    )


async def _do_delete_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    url_param = params.get("url", "")
    list_type = params.get("type", "")
    query = {"type": list_type} if list_type else None
    return await _call(
        "DELETE",
        f"{API_V1}/whiteblacklist/{url_param}",
        params=query,
        action="delete_blackwhitelist",
    )


async def _do_batch_delete_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    items = params.get("items", [])
    return await _call(
        "POST",
        f"{API_BATCH}/whiteblacklist",
        params={"_method": "DELETE"},
        json=items,
        action="batch_delete_blackwhitelist",
    )


async def _do_get_blockip_list(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_sortby", "_order", "creator", "fuzzyIP")
    return await _call("GET", f"{API_V1}/blockip", params=query, action="get_blockip_list")


async def _do_batch_add_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    items = params.get("items", [])
    query = _pick(params, "aifwType")
    return await _call(
        "POST",
        f"{API_BATCH}/blockip",
        params=query or None,
        json=items,
        action="batch_add_blockip",
    )


async def _do_batch_delete_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    items = params.get("items", [])
    return await _call(
        "POST",
        f"{API_BATCH}/blockip",
        params={"_method": "DELETE"},
        json=items,
        action="batch_delete_blockip",
    )


async def _do_clear_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "creator")
    return await _call(
        "DELETE",
        f"{API_V1}/blockip",
        params=query or None,
        action="clear_blockip",
    )


async def _do_get_blockip_auto_config(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/blockip/autoconfig", action="get_blockip_auto_config")


async def _do_set_blockip_auto_config(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "blockTime")
    return await _call(
        "PUT",
        f"{API_V1}/blockip/autoconfig",
        json={"obj": body},
        action="set_blockip_auto_config",
    )


# ── Status / device info actions ──────────────────────────────────────────────

async def _do_get_memory_usage(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/memoryusage", action="get_memory_usage")


async def _do_get_cpu_usage(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/cpuusage", action="get_cpu_usage")


async def _do_get_disk_usage(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/diskusage", action="get_disk_usage")


async def _do_get_system_version(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "filter")
    return await _call(
        "GET", f"{API_V1}/systemversion",
        params=query or None,
        action="get_system_version",
    )


async def _do_get_interface_status(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    # AF8.0.x: /interfacestatus returns 1002; use /interfaces (list) or
    # /interfaces/status?interfaceName=<name> (single interface query).
    iface = params.get("interfaceNames") or params.get("interfaceName") or ""
    if iface:
        return await _call(
            "GET", f"{API_V1}/interfaces/status",
            params={"interfaceName": iface},
            action="get_interface_status",
        )
    return await _call("GET", f"{API_V1}/interfaces", action="get_interface_status")


async def _do_get_runtime_status(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/runtimestatus", action="get_runtime_status")


async def _do_get_current_time(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/currenttime", action="get_current_time")


# ── Network / routing actions ─────────────────────────────────────────────────

async def _do_get_routes(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "routeType", "_search")
    return await _call("GET", f"{API_V1}/routes", params=query or None, action="get_routes")


async def _do_get_routes_ipv6(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "routeType", "_search")
    return await _call("GET", f"{API_V1}/routes/ipv6", params=query or None, action="get_routes_ipv6")


# ── Admin account actions ─────────────────────────────────────────────────────

async def _do_get_accounts(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_search", "enable")
    return await _call("GET", f"{API_V1}/account", params=query or None, action="get_accounts")


async def _do_get_account(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    name = params.get("name", "")
    return await _call("GET", f"{API_V1}/account/{name}", action="get_account")


# ── Action dispatch ───────────────────────────────────────────────────────────

_ACTION_MAP: dict[str, Callable] = {
    # Auth
    "login": _do_login,
    "logout": _do_logout,
    "keepalive": _do_keepalive,
    # Objects
    "get_ipgroups": _do_get_ipgroups,
    "get_ipgroup": _do_get_ipgroup,
    "create_ipgroup": _do_create_ipgroup,
    "update_ipgroup": _do_update_ipgroup,
    "delete_ipgroup": _do_delete_ipgroup,
    "get_services": _do_get_services,
    "get_service": _do_get_service,
    # Operations center - blacklist/whitelist
    "get_blackwhitelist": _do_get_blackwhitelist,
    "add_blackwhitelist": _do_add_blackwhitelist,
    "batch_add_blackwhitelist": _do_batch_add_blackwhitelist,
    "delete_blackwhitelist": _do_delete_blackwhitelist,
    "batch_delete_blackwhitelist": _do_batch_delete_blackwhitelist,
    # Operations center - blocked IPs
    "get_blockip_list": _do_get_blockip_list,
    "batch_add_blockip": _do_batch_add_blockip,
    "batch_delete_blockip": _do_batch_delete_blockip,
    "clear_blockip": _do_clear_blockip,
    "get_blockip_auto_config": _do_get_blockip_auto_config,
    "set_blockip_auto_config": _do_set_blockip_auto_config,
    # Status
    "get_memory_usage": _do_get_memory_usage,
    "get_cpu_usage": _do_get_cpu_usage,
    "get_disk_usage": _do_get_disk_usage,
    "get_system_version": _do_get_system_version,
    "get_interface_status": _do_get_interface_status,
    "get_runtime_status": _do_get_runtime_status,
    "get_current_time": _do_get_current_time,
    # Network
    "get_routes": _do_get_routes,
    "get_routes_ipv6": _do_get_routes_ipv6,
    # System
    "get_accounts": _do_get_accounts,
    "get_account": _do_get_account,
}

GROUP_ACTIONS: dict[str, set[str]] = {
    "auth": {"login", "logout", "keepalive"},
    "objects": {"get_ipgroups", "get_ipgroup", "create_ipgroup", "update_ipgroup", "delete_ipgroup", "get_services", "get_service"},
    "ops": {
        "get_blackwhitelist", "add_blackwhitelist", "batch_add_blackwhitelist",
        "delete_blackwhitelist", "batch_delete_blackwhitelist",
        "get_blockip_list", "batch_add_blockip", "batch_delete_blockip",
        "clear_blockip", "get_blockip_auto_config", "set_blockip_auto_config",
    },
    "status": {
        "get_memory_usage", "get_cpu_usage", "get_disk_usage",
        "get_system_version", "get_interface_status",
        "get_runtime_status", "get_current_time",
    },
    "network": {"get_routes", "get_routes_ipv6"},
    "system": {"get_accounts", "get_account"},
}

_CONNECTIVITY_TEST_ACTIONS: dict[str, str] = {
    "auth": "keepalive",
    "objects": "get_ipgroups",
    "ops": "get_blackwhitelist",
    "status": "get_system_version",
    "network": "get_routes",
    "system": "get_accounts",
}


async def unified_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    handler = _ACTION_MAP.get(action)
    if handler is None:
        available = ", ".join(sorted(_ACTION_MAP))
        return ToolResult(
            success=False,
            error=f"Unknown action: {action}. Available: {available}",
        )
    return await handler(ctx, **params)


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    if action == "test":
        test_action = _CONNECTIVITY_TEST_ACTIONS.get(group, "get_system_version")
        return await unified_ops(ctx, action=test_action, **params)
    if action not in GROUP_ACTIONS[group]:
        available = ", ".join(sorted(GROUP_ACTIONS[group]))
        return ToolResult(
            success=False,
            error=f"Unsupported {group} action: {action}. Available: {available}",
        )
    return await unified_ops(ctx, action=action, **params)


async def auth(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "auth", action, **params)


async def objects(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "objects", action, **params)


async def ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "ops", action, **params)


async def status(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "status", action, **params)


async def network(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "network", action, **params)


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "system", action, **params)


def _make_action_function(action: str):
    async def _tool(ctx: ToolContext, **kwargs: Any) -> ToolResult:
        return await unified_ops(ctx, action=action, **kwargs)
    _tool.__name__ = action
    return _tool


for _action_name in _ACTION_MAP:
    globals()[_action_name] = _make_action_function(_action_name)

del _action_name
