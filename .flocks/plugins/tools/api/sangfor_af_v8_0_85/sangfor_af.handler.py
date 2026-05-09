"""
Sangfor AF (Application Firewall) v8.0.85 API Handler.

Extends v8.0.48 with additional monitoring APIs:
  - Session monitoring (traffic ranking, session counts, session list)
  - Statistics (packet loss, buffer, hash table, etc.)
  - Log/alarm settings

Authentication: same as v8.0.48 (session-based Cookie token).
API base URL: https://<device_ip>
Namespace: /api/v1/namespaces/public/
Batch ops:  /api/batch/v1/namespaces/public/
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

# ── Constants ────────────────────────────────────────────────────────────────

SERVICE_ID = "sangfor_af_v8_0_85"
DEFAULT_BASE_URL = "https://192.168.1.1"
DEFAULT_TIMEOUT = 60
NAMESPACE = "public"

API_V1 = f"/api/v1/namespaces/{NAMESPACE}"
API_BATCH = f"/api/batch/v1/namespaces/{NAMESPACE}"

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
    raw = _service_config()
    base_url = (_resolve_ref(raw.get("base_url")) or DEFAULT_BASE_URL).rstrip("/")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    sm = _get_secret_manager()
    username = (
        _resolve_ref(raw.get("username"))
        or sm.get("sangfor_af_v8_0_85_username")
        or os.getenv("AF_USERNAME")
    )
    password = (
        _resolve_ref(raw.get("password"))
        or sm.get("sangfor_af_v8_0_85_password")
        or os.getenv("AF_PASSWORD")
    )
    if not username or not password:
        raise ValueError(
            "AF API credentials not configured. "
            "Please set username and password in the sangfor_af_v8_0_85 service configuration."
        )
    return base_url, timeout, username, password, _resolve_verify_ssl(raw)


# ── Session / Token management ────────────────────────────────────────────────

async def _login(session, base_url, username, password, verify_ssl):
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
        return None, f"AF login failed (code={code}): {data.get('message', 'Unknown error')}"
    token = data.get("data", {}).get("loginResult", {}).get("token")
    if not token:
        return None, "AF login succeeded but no token returned"
    return token, None


async def _get_token(session, base_url, username, password, verify_ssl):
    cached = _TOKEN_CACHE.get(base_url)
    if cached:
        try:
            async with session.get(
                f"{base_url}{API_V1}/keepalive",
                headers={"Cookie": f"token={cached}"},
                ssl=verify_ssl,
            ) as resp:
                ka = await resp.json(content_type=None)
            if ka.get("code") == 0:
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


def _af_result(action: str, payload: Any, version: str = "8.0.85") -> ToolResult:
    metadata = {"source": "Sangfor AF", "api": action, "version": version}
    if isinstance(payload, dict):
        code = payload.get("code")
        if code not in (None, 0):
            msg = payload.get("message", "Unknown error")
            return ToolResult(success=False, error=f"AF API error (code={code}): {msg}", metadata=metadata)
        return ToolResult(success=True, output=payload.get("data", payload), metadata=metadata)
    return ToolResult(success=True, output=payload, metadata=metadata)


async def _call(
    method: str,
    path: str,
    params: Optional[dict[str, Any]] = None,
    json: Optional[Any] = None,
    action: str = "",
) -> ToolResult:
    try:
        base_url, timeout, username, password, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    headers = {"Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        token, err = await _get_token(session, base_url, username, password, verify_ssl)
        if err:
            return ToolResult(success=False, error=err)
        headers["Cookie"] = f"token={token}"
        url = f"{base_url}{path}"
        try:
            async with session.request(
                method.upper(), url, params=params, json=json, headers=headers, ssl=verify_ssl,
            ) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    return ToolResult(success=False, error=f"HTTP {resp.status}: {text[:500]}")
                data = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            return ToolResult(success=False, error=f"Request failed: {exc}")
        except Exception as exc:
            return ToolResult(success=False, error=f"Unexpected error: {exc}")
    return _af_result(action or path.rsplit("/", 1)[-1], data)


# ── Auth actions ──────────────────────────────────────────────────────────────

async def _do_login(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    try:
        base_url, timeout, username, password, verify_ssl = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        token, err = await _login(session, base_url, username, password, verify_ssl)
        if err:
            return ToolResult(success=False, error=err)
        _TOKEN_CACHE[base_url] = token
        return ToolResult(
            success=True,
            output={"token": token, "message": "Login successful"},
            metadata={"source": "Sangfor AF", "api": "login", "version": "8.0.85"},
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
    query = _pick(params, "_start", "_length", "businessType", "__nameprefix", "important", "_search", "_order", "_sortby", "addressType")
    return await _call("GET", f"{API_V1}/ipgroups", params=query, action="get_ipgroups")


async def _do_get_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/ipgroups/{params.get('uuid', '')}", action="get_ipgroup")


async def _do_create_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "name", "businessType", "description", "addressType", "important", "ipRanges", "creator")
    return await _call("POST", f"{API_V1}/ipgroups", json={"obj": body}, action="create_ipgroup")


async def _do_update_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "name", "businessType", "description", "addressType", "important", "ipRanges")
    return await _call("PATCH", f"{API_V1}/ipgroups/{params.get('uuid', '')}", json={"obj": body}, action="update_ipgroup")


async def _do_delete_ipgroup(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("DELETE", f"{API_V1}/ipgroups/{params.get('uuid', '')}", action="delete_ipgroup")


async def _do_get_services(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_search", "_order", "_sortby", "serviceType")
    return await _call("GET", f"{API_V1}/services", params=query, action="get_services")


async def _do_get_service(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/services/{params.get('uuid', '')}", action="get_service")


# ── Monitoring actions (new in v8.0.85) ──────────────────────────────────────

async def _do_get_user_traffic_rank(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "topNumber", "vsys", "line", "applicationType", "filterObject")
    return await _call(
        "POST",
        f"{API_V1}/topusertraffics",
        params={"_method": "GET"},
        json=body or {},
        action="get_user_traffic_rank",
    )


async def _do_get_ip_traffic_trend(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    # /iptraffics is not a paged endpoint; _start/_length must not be sent.
    # topNumber must be int — AF returns code=1001 for any non-int value.
    query = _pick(params, "vsys", "topNumber", "unit", "minutes")
    if "topNumber" in query:
        try:
            query["topNumber"] = int(query["topNumber"])
        except (TypeError, ValueError):
            pass
    return await _call("GET", f"{API_V1}/iptraffics", params=query or None, action="get_ip_traffic_trend")


async def _do_get_app_traffic_rank(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "vsys", "line", "topNumber")
    if "topNumber" in query:
        try:
            query["topNumber"] = int(query["topNumber"])
        except (TypeError, ValueError):
            pass
    return await _call("GET", f"{API_V1}/apptrafficrank", params=query or None, action="get_app_traffic_rank")


async def _do_get_session_dailys(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "vsys", "ip")
    return await _call("GET", f"{API_V1}/sessiondailys", params=query or None, action="get_session_dailys")


async def _do_get_session_details(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    # Endpoint needs explicit filters; without them AF returns 1004 "没有返回值".
    query = _pick(params, "vsys", "srcIP", "dstIP", "protocol", "srcPort", "dstPort")
    return await _call("GET", f"{API_V1}/sessiondetails", params=query or None, action="get_session_details")


async def _do_get_session_count_trend(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "vsys", "minutes")
    return await _call("GET", f"{API_V1}/sessioncounttrend", params=query or None, action="get_session_count_trend")


async def _do_get_session_src_ip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    # srcIP is required; AF returns 1004 "没有返回值" when omitted.
    query = _pick(params, "vsys", "srcIP")
    return await _call("GET", f"{API_V1}/sessionsrcip", params=query or None, action="get_session_src_ip")


async def _do_get_session_count_rank(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "vsys", "topNumber")
    return await _call("GET", f"{API_V1}/sessioncountrank", params=query or None, action="get_session_count_rank")


async def _do_get_session_summary(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "vsys")
    return await _call("GET", f"{API_V1}/sessionsummary", params=query or None, action="get_session_summary")


async def _do_get_monitor_ips(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length")
    return await _call("GET", f"{API_V1}/monitorips", params=query or None, action="get_monitor_ips")


async def _do_get_sessions(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    body = _pick(params, "_start", "_length", "vsys", "srcIP", "dstIP", "protocol", "srcPort", "dstPort")
    # AF8.0.x requires POST + ?_method=GET for /sessions; plain GET returns 1002.
    return await _call("POST", f"{API_V1}/sessions", params={"_method": "GET"}, json=body or {}, action="get_sessions")


# Statistics (monitoring sub-section)
async def _do_get_packet_drop_stats(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length")
    return await _call("GET", f"{API_V1}/mbufdroppointstatistics", params=query or None, action="get_packet_drop_stats")


async def _do_clear_packet_drop_stats(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("DELETE", f"{API_V1}/mbufdroppointstatistics", action="clear_packet_drop_stats")


async def _do_get_mbuf_stats(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/mbufstatistics", action="get_mbuf_stats")


async def _do_get_hash_table_stats(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length")
    return await _call("GET", f"{API_V1}/hashtablestatistics", params=query or None, action="get_hash_table_stats")


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
    return await _call("POST", f"{API_BATCH}/whiteblacklist", json=params.get("items", []), action="batch_add_blackwhitelist")


async def _do_delete_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    url_param = params.get("url", "")
    list_type = params.get("type", "")
    query = {"type": list_type} if list_type else None
    return await _call("DELETE", f"{API_V1}/whiteblacklist/{url_param}", params=query, action="delete_blackwhitelist")


async def _do_batch_delete_blackwhitelist(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("POST", f"{API_BATCH}/whiteblacklist", params={"_method": "DELETE"}, json=params.get("items", []), action="batch_delete_blackwhitelist")


async def _do_get_blockip_list(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_sortby", "_order", "creator", "fuzzyIP")
    return await _call("GET", f"{API_V1}/blockip", params=query, action="get_blockip_list")


async def _do_batch_add_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "aifwType")
    return await _call("POST", f"{API_BATCH}/blockip", params=query or None, json=params.get("items", []), action="batch_add_blockip")


async def _do_batch_delete_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("POST", f"{API_BATCH}/blockip", params={"_method": "DELETE"}, json=params.get("items", []), action="batch_delete_blockip")


async def _do_clear_blockip(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "creator")
    return await _call("DELETE", f"{API_V1}/blockip", params=query or None, action="clear_blockip")


async def _do_get_blockip_auto_config(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/blockip/autoconfig", action="get_blockip_auto_config")


async def _do_set_blockip_auto_config(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("PUT", f"{API_V1}/blockip/autoconfig", json={"obj": _pick(params, "blockTime")}, action="set_blockip_auto_config")


# ── Status actions ────────────────────────────────────────────────────────────

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
    return await _call("GET", f"{API_V1}/systemversion", params=query or None, action="get_system_version")


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


# ── Network actions ───────────────────────────────────────────────────────────

async def _do_get_routes(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "routeType", "_search")
    return await _call("GET", f"{API_V1}/routes", params=query or None, action="get_routes")


async def _do_get_routes_ipv6(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "routeType", "_search")
    return await _call("GET", f"{API_V1}/routes/ipv6", params=query or None, action="get_routes_ipv6")


# ── System actions ────────────────────────────────────────────────────────────

async def _do_get_accounts(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    query = _pick(params, "_start", "_length", "_search", "enable")
    return await _call("GET", f"{API_V1}/account", params=query or None, action="get_accounts")


async def _do_get_account(ctx: ToolContext, **params: Any) -> ToolResult:
    del ctx
    return await _call("GET", f"{API_V1}/account/{params.get('name', '')}", action="get_account")


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
    # Monitoring (new in v8.0.85)
    "get_user_traffic_rank": _do_get_user_traffic_rank,
    "get_ip_traffic_trend": _do_get_ip_traffic_trend,
    "get_app_traffic_rank": _do_get_app_traffic_rank,
    "get_session_dailys": _do_get_session_dailys,
    "get_session_details": _do_get_session_details,
    "get_session_count_trend": _do_get_session_count_trend,
    "get_session_src_ip": _do_get_session_src_ip,
    "get_session_count_rank": _do_get_session_count_rank,
    "get_session_summary": _do_get_session_summary,
    "get_monitor_ips": _do_get_monitor_ips,
    "get_sessions": _do_get_sessions,
    "get_packet_drop_stats": _do_get_packet_drop_stats,
    "clear_packet_drop_stats": _do_clear_packet_drop_stats,
    "get_mbuf_stats": _do_get_mbuf_stats,
    "get_hash_table_stats": _do_get_hash_table_stats,
    # Operations center
    "get_blackwhitelist": _do_get_blackwhitelist,
    "add_blackwhitelist": _do_add_blackwhitelist,
    "batch_add_blackwhitelist": _do_batch_add_blackwhitelist,
    "delete_blackwhitelist": _do_delete_blackwhitelist,
    "batch_delete_blackwhitelist": _do_batch_delete_blackwhitelist,
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
    "monitor": {
        "get_user_traffic_rank", "get_ip_traffic_trend", "get_app_traffic_rank",
        "get_session_dailys", "get_session_details", "get_session_count_trend",
        "get_session_src_ip", "get_session_count_rank", "get_session_summary",
        "get_monitor_ips", "get_sessions",
        "get_packet_drop_stats", "clear_packet_drop_stats",
        "get_mbuf_stats", "get_hash_table_stats",
    },
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
    "monitor": "get_session_summary",
    "ops": "get_blackwhitelist",
    "status": "get_system_version",
    "network": "get_routes",
    "system": "get_accounts",
}


async def unified_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    handler = _ACTION_MAP.get(action)
    if handler is None:
        available = ", ".join(sorted(_ACTION_MAP))
        return ToolResult(success=False, error=f"Unknown action: {action}. Available: {available}")
    return await handler(ctx, **params)


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    if action == "test":
        return await unified_ops(ctx, action=_CONNECTIVITY_TEST_ACTIONS.get(group, "get_system_version"), **params)
    if action not in GROUP_ACTIONS[group]:
        available = ", ".join(sorted(GROUP_ACTIONS[group]))
        return ToolResult(success=False, error=f"Unsupported {group} action: {action}. Available: {available}")
    return await unified_ops(ctx, action=action, **params)


async def auth(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "auth", action, **params)


async def objects(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "objects", action, **params)


async def monitor(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "monitor", action, **params)


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
