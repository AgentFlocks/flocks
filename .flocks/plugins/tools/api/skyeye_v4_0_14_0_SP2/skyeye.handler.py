import base64
import hashlib
import json
import random
import re
import time
from typing import Any
from urllib.parse import urljoin

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks import security
from flocks.tool.registry import ToolContext, ToolResult


_SERVICE_ID = "skyeye_api"
_DEFAULT_USERNAME = "tapadmin"
_CLIENT_ID_SEED = "mNSLP9UJCtBHtegjDPJnK3v"
_CLIENT_SECRET_SEED = "3460681205014671737"
_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)


def _get_raw_service() -> dict[str, Any]:
    return ConfigWriter.get_api_service_raw(_SERVICE_ID) or {}


def _get_custom_setting(raw_service: dict[str, Any], key: str, default: Any = None) -> Any:
    custom_settings = raw_service.get("custom_settings", {})
    if not isinstance(custom_settings, dict):
        return default
    return custom_settings.get(key, default)


def _resolve_login_key(raw_service: dict[str, Any]) -> str:
    secret_manager = security.get_secret_manager()
    api_key_ref = raw_service.get("apiKey") or _get_custom_setting(raw_service, "login_key")

    if api_key_ref:
        resolved = security.resolve_value(api_key_ref)
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()

    for secret_name in ("skyeye_login_key", "skyeye_api_key"):
        value = secret_manager.get(secret_name)
        if value:
            return value.strip()

    for env_name in ("SKYEYE_LOGIN_KEY", "SKYEYE_API_KEY"):
        value = security.resolve_value(f"{{env:{env_name}}}")
        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def _resolve_base_url(raw_service: dict[str, Any]) -> str:
    base_url = raw_service.get("base_url") or _get_custom_setting(raw_service, "base_url")
    if base_url:
        resolved = security.resolve_value(base_url)
        if isinstance(resolved, str) and resolved.strip():
            return resolved.rstrip("/")

    secret_manager = security.get_secret_manager()
    host = secret_manager.get("skyeye_host") or security.resolve_value("{env:SKYEYE_HOST}")
    if isinstance(host, str) and host.strip():
        host = host.strip().rstrip("/")
        if host.startswith("http://") or host.startswith("https://"):
            return host
        return f"https://{host}:443"

    return ""


def _resolve_api_prefix(raw_service: dict[str, Any]) -> str:
    prefix = (
        raw_service.get("api_prefix")
        or _get_custom_setting(raw_service, "api_prefix")
        or security.resolve_value("{env:SKYEYE_API_PREFIX}")
        or ""
    )
    if not isinstance(prefix, str):
        return ""
    return prefix.strip().strip("/")


def _resolve_api_version(raw_service: dict[str, Any]) -> str:
    version = (
        raw_service.get("api_version")
        or _get_custom_setting(raw_service, "api_version")
        or security.resolve_value("{env:SKYEYE_API_VERSION}")
        or "v1"
    )
    if not isinstance(version, str) or not version.strip():
        return "v1"
    return version.strip().strip("/")


def _resolve_username(raw_service: dict[str, Any]) -> str:
    username = (
        raw_service.get("username")
        or _get_custom_setting(raw_service, "username")
        or security.get_secret_manager().get("skyeye_username")
        or security.resolve_value("{env:SKYEYE_USERNAME}")
        or _DEFAULT_USERNAME
    )
    if not isinstance(username, str) or not username.strip():
        return _DEFAULT_USERNAME
    return username.strip()


def _verify_ssl(raw_service: dict[str, Any]) -> bool:
    # "verify_ssl" is the canonical field; "ssl_verify" is accepted for backward compatibility
    raw_value = raw_service.get("verify_ssl")
    if raw_value is None:
        raw_value = raw_service.get("ssl_verify")
    if raw_value is None:
        raw_value = _get_custom_setting(raw_service, "verify_ssl", False)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


def _build_api_path(api_prefix: str, api_version: str, endpoint: str) -> str:
    parts = []
    if api_prefix:
        parts.append(api_prefix.strip("/"))
    if api_version:
        parts.append(api_version.strip("/"))
    parts.append(endpoint.strip("/"))
    return "/" + "/".join(parts)


def _build_signature(username: str, login_key: str, timestamp: str) -> tuple[str, str, str]:
    client_id = hashlib.sha256(f"{_CLIENT_ID_SEED}|{login_key}".encode("utf-8")).hexdigest()
    client_secret = hashlib.sha256(f"{_CLIENT_SECRET_SEED}|{login_key}".encode("utf-8")).hexdigest()
    payload = json.dumps(
        {"client_id": client_id, "username": username},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    signature = hashlib.sha256(f"{payload}{timestamp}{client_secret}".encode("utf-8")).hexdigest()
    return client_id, client_secret, signature


def _extract_csrf_token(html: str) -> str:
    patterns = [
        r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([0-9a-fA-F]{16,64})["\']',
        r'csrf-token["\']\s+content=["\']([0-9a-fA-F]{16,64})["\']',
        r'([0-9a-fA-F]{16,64})',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.IGNORECASE)
        if match:
            return match.group(1)
    return ""


def _default_time_range(days: int = 7) -> tuple[int, int]:
    end_time = int(time.time() * 1000)
    start_time = end_time - days * 24 * 60 * 60 * 1000
    return start_time, end_time


def _clean_params(params: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned


def _payload_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None

    status = payload.get("status")
    if status not in (None, 200, 1000, "200", "1000", "success"):
        return payload.get("message") or payload.get("msg") or f"API returned status {status}"

    data = payload.get("data")
    if isinstance(data, dict):
        data_status = data.get("status")
        if data_status not in (None, 200, 1000, "200", "1000", "success"):
            return data.get("message") or payload.get("message") or f"API returned data.status {data_status}"

    return None


async def _login(session: aiohttp.ClientSession) -> tuple[bool, dict[str, Any], str | None]:
    raw_service = _get_raw_service()
    base_url = _resolve_base_url(raw_service)
    login_key = _resolve_login_key(raw_service)
    username = _resolve_username(raw_service)
    api_prefix = _resolve_api_prefix(raw_service)
    api_version = _resolve_api_version(raw_service)
    verify_ssl = _verify_ssl(raw_service)

    if not base_url:
        return False, {}, (
            "SkyEye base URL is not configured. "
            "Please set api_services.skyeye_api.base_url or secret 'skyeye_host'."
        )
    if not login_key:
        return False, {}, (
            "SkyEye login key is not configured. "
            "Please set the service credential or secret 'skyeye_login_key'."
        )

    timestamp = str(int(time.time()))
    client_id, _, signature = _build_signature(username, login_key, timestamp)
    auth_path = _build_api_path(api_prefix, api_version, "admin/auth")
    auth_url = urljoin(f"{base_url}/", auth_path.lstrip("/"))

    headers = {
        "X-Authorization": signature,
        "X-Timestamp": timestamp,
    }
    form_data = {
        "client_id": client_id,
        "username": username,
    }

    try:
        async with session.post(auth_url, headers=headers, data=form_data, ssl=verify_ssl) as response:
            token_payload = await response.json(content_type=None)
            if response.status >= 400:
                return False, {}, f"SkyEye auth failed: HTTP {response.status}"
    except Exception as exc:
        return False, {}, f"SkyEye auth request failed: {exc}"

    access_token = token_payload.get("access_token")
    if not access_token:
        return False, {}, f"SkyEye auth failed: {token_payload}"

    try:
        async with session.get(auth_url, params={"token": access_token}, ssl=verify_ssl) as response:
            html = await response.text()
            if response.status >= 400:
                return False, {}, f"SkyEye login session init failed: HTTP {response.status}"
    except Exception as exc:
        return False, {}, f"SkyEye login session init failed: {exc}"

    csrf_token = _extract_csrf_token(html)
    if not csrf_token:
        return False, {}, "SkyEye login succeeded but csrf_token was not found in response HTML."

    return True, {
        "base_url": base_url,
        "api_prefix": api_prefix,
        "api_version": api_version,
        "csrf_token": csrf_token,
        "verify_ssl": verify_ssl,
    }, None


async def _request_json(endpoint: str, params: dict[str, Any], api_name: str) -> ToolResult:
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT, cookie_jar=jar) as session:
        ok, auth_info, error = await _login(session)
        if not ok:
            return ToolResult(success=False, error=error)

        request_params = _clean_params({
            **params,
            "csrf_token": auth_info["csrf_token"],
            "r": random.randint(100000, 999999),
        })
        request_path = _build_api_path(auth_info["api_prefix"], auth_info["api_version"], endpoint)
        request_url = urljoin(f"{auth_info['base_url']}/", request_path.lstrip("/"))

        try:
            async with session.get(
                request_url,
                params=request_params,
                ssl=auth_info["verify_ssl"],
            ) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    return ToolResult(
                        success=False,
                        error=f"SkyEye request failed: HTTP {response.status}",
                        output=payload,
                    )
        except Exception as exc:
            return ToolResult(success=False, error=f"SkyEye request failed: {exc}")

    payload_error = _payload_error(payload)
    if payload_error:
        return ToolResult(success=False, error=payload_error, output=payload)

    return ToolResult(
        success=True,
        output=payload,
        metadata={"source": _SERVICE_ID, "api": api_name},
    )


def _extract_filename(content_disposition: str | None) -> str | None:
    if not content_disposition:
        return None

    filename_matches = [
        re.search(r'filename\*=UTF-8\'\'([^;]+)', content_disposition, re.IGNORECASE),
        re.search(r'filename="([^"]+)"', content_disposition, re.IGNORECASE),
        re.search(r'filename=([^;]+)', content_disposition, re.IGNORECASE),
    ]
    for match in filename_matches:
        if match:
            return match.group(1).strip().strip('"')
    return None


def _normalize_binary_payload(content: bytes, content_type: str | None, content_disposition: str | None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "content_type": content_type or "application/octet-stream",
        "encoding": "base64",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "size": len(content),
    }
    filename = _extract_filename(content_disposition)
    if filename:
        payload["filename"] = filename
    if content_disposition:
        payload["content_disposition"] = content_disposition
    return payload


async def _request_bytes(endpoint: str, params: dict[str, Any], api_name: str) -> ToolResult:
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT, cookie_jar=jar) as session:
        ok, auth_info, error = await _login(session)
        if not ok:
            return ToolResult(success=False, error=error)

        request_params = _clean_params({
            **params,
            "csrf_token": auth_info["csrf_token"],
            "r": random.randint(100000, 999999),
        })
        request_path = _build_api_path(auth_info["api_prefix"], auth_info["api_version"], endpoint)
        request_url = urljoin(f"{auth_info['base_url']}/", request_path.lstrip("/"))

        try:
            async with session.get(
                request_url,
                params=request_params,
                ssl=auth_info["verify_ssl"],
            ) as response:
                content = await response.read()
                content_type = response.headers.get("Content-Type")
                content_disposition = response.headers.get("Content-Disposition")
                if response.status >= 400:
                    return ToolResult(
                        success=False,
                        error=f"SkyEye request failed: HTTP {response.status}",
                        output=_normalize_binary_payload(content, content_type, content_disposition),
                    )
        except Exception as exc:
            return ToolResult(success=False, error=f"SkyEye request failed: {exc}")

    if content_type and "json" in content_type.lower():
        try:
            payload = json.loads(content.decode("utf-8"))
        except Exception:
            payload = None
        if payload is not None:
            payload_error = _payload_error(payload)
            if payload_error:
                return ToolResult(success=False, error=payload_error, output=payload)
            return ToolResult(
                success=True,
                output=payload,
                metadata={"source": _SERVICE_ID, "api": api_name},
            )

    return ToolResult(
        success=True,
        output=_normalize_binary_payload(content, content_type, content_disposition),
        metadata={"source": _SERVICE_ID, "api": api_name},
    )


async def dashboard_view(
    ctx: ToolContext,
    name: str = "overall_view",
    interval_time: int = 7,
    start_time: int | None = None,
    end_time: int | None = None,
    keyword: str | None = None,
    page: int | None = None,
    limit: int | None = None,
    order: str | None = None,
) -> ToolResult:
    del ctx
    if start_time is None or end_time is None:
        default_start, default_end = _default_time_range(max(interval_time, 1))
        start_time = start_time or default_start
        end_time = end_time or default_end

    return await _request_json(
        "monitor-center/dashboard/view",
        {
            "name": name,
            "interval_time": interval_time,
            "start_time": start_time,
            "end_time": end_time,
            "keyword": keyword,
            "page": page,
            "limit": limit,
            "order": order,
        },
        "monitor_center_dashboard_view",
    )


async def alarm_params(
    ctx: ToolContext,
    data_source: int = 0,
) -> ToolResult:
    del ctx
    return await _request_json(
        "alarm/alarm/alarm-params",
        {"data_source": data_source},
        "alarm_alarm_params",
    )


async def alarm_list(
    ctx: ToolContext,
    start_time: int | None = None,
    end_time: int | None = None,
    threat_type: str | None = None,
    api_type_chain: str | None = None,
    hazard_level: str | None = None,
    status: str | None = None,
    serial_num: str | None = None,
    data_source: str | None = None,
    alarm_sip: str | None = None,
    attack_sip: str | None = None,
    ioc: str | None = None,
    threat_name: str | None = None,
    host: str | None = None,
    is_web_attack: str | None = None,
    x_forwarded_for: str | None = None,
    host_state: str | None = None,
    attack_stage: str | None = None,
    asset_group: str | None = None,
    status_http: str | None = None,
    attack_dimension: str | None = None,
    attck: str | None = None,
    attck_org: str | None = None,
    alarm_id: str | None = None,
    focus_label: str | None = None,
    file_md5: str | None = None,
    asset_ip: str | None = None,
    is_alarm_black_ip: int | None = None,
    is_white: int | None = None,
    marks: str | None = None,
    asset_mark: str | None = None,
    alarm_sip_asset_group: str | None = None,
    attack_sip_asset_group: str | None = None,
    offset: int = 1,
    limit: int = 20,
    order_by: str | None = None,
) -> ToolResult:
    del ctx
    if start_time is None or end_time is None:
        start_time, end_time = _default_time_range(7)

    return await _request_json(
        "alarm/alarm/list",
        {
            "start_time": start_time,
            "end_time": end_time,
            "threat_type": threat_type,
            "api_type_chain": api_type_chain,
            "hazard_level": hazard_level,
            "status": status,
            "serial_num": serial_num,
            "data_source": data_source,
            "alarm_sip": alarm_sip,
            "attack_sip": attack_sip,
            "ioc": ioc,
            "threat_name": threat_name,
            "host": host,
            "is_web_attack": is_web_attack,
            "x_forwarded_for": x_forwarded_for,
            "host_state": host_state,
            "attack_stage": attack_stage,
            "asset_group": asset_group,
            "status_http": status_http,
            "attack_dimension": attack_dimension,
            "attck": attck,
            "attck_org": attck_org,
            "alarm_id": alarm_id,
            "focus_label": focus_label,
            "file_md5": file_md5,
            "asset_ip": asset_ip,
            "is_alarm_black_ip": is_alarm_black_ip,
            "is_white": is_white,
            "marks": marks,
            "asset_mark": asset_mark,
            "alarm_sip_asset_group": alarm_sip_asset_group,
            "attack_sip_asset_group": attack_sip_asset_group,
            "offset": offset,
            "limit": limit,
            "order_by": order_by,
        },
        "alarm_alarm_list",
    )


async def download_uploadfile(
    ctx: ToolContext,
    alarm_id: str,
    start_time: int,
    end_time: int,
    xff: str | None = None,
    alarm_sip: str | None = None,
    attack_sip: str | None = None,
    skyeye_type: str | None = None,
    ioc: str | None = None,
    host_state: str | None = None,
    sip_ioc_dip: str | None = None,
    branch_id: str | None = None,
) -> ToolResult:
    del ctx
    return await _request_bytes(
        "alarm/alarm/info/uploadfile/download",
        {
            "alarm_id": alarm_id,
            "start_time": start_time,
            "end_time": end_time,
            "xff": xff,
            "alarm_sip": alarm_sip,
            "attack_sip": attack_sip,
            "skyeye_type": skyeye_type,
            "ioc": ioc,
            "host_state": host_state,
            "sip_ioc_dip": sip_ioc_dip,
            "branch_id": branch_id,
        },
        "alarm_alarm_info_uploadfile_download",
    )


async def download_pcap(
    ctx: ToolContext,
    alarm_id: str,
    start_time: int,
    end_time: int,
    alarm_sip: str | None = None,
    attack_sip: str | None = None,
    skyeye_type: str | None = None,
    ioc: str | None = None,
    type: str | None = None,
    branch_id: str | None = None,
    xff: str | None = None,
    host_state: str | None = None,
) -> ToolResult:
    del ctx
    return await _request_bytes(
        "alarm/alarm/info/pcap/download",
        {
            "alarm_id": alarm_id,
            "start_time": start_time,
            "end_time": end_time,
            "alarm_sip": alarm_sip,
            "attack_sip": attack_sip,
            "skyeye_type": skyeye_type,
            "ioc": ioc,
            "type": type,
            "branch_id": branch_id,
            "xff": xff,
            "host_state": host_state,
        },
        "alarm_alarm_info_pcap_download",
    )


async def download_alarm_report(
    ctx: ToolContext,
    alarm_id: str,
    export_type: str,
    start_time: int,
    end_time: int,
    asset_group: str | None = None,
    is_white: str | None = None,
) -> ToolResult:
    del ctx
    return await _request_bytes(
        "alarm/alarm/info/download",
        {
            "alarm_id": alarm_id,
            "asset_group": asset_group,
            "export_type": export_type,
            "is_white": is_white,
            "start_time": start_time,
            "end_time": end_time,
        },
        "alarm_alarm_info_download",
    )
