import hashlib
import json
import os
import ssl
import time
import urllib.parse
import http.client as httplib
from typing import Any, Callable, Optional

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "qingteng"


def _resolve_ref(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        resolved = get_secret_manager().get(value[len("{secret:") : -1])
        return resolved or ""
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:") : -1], "")
    return value


def _resolve_base_url() -> str:
    raw_service = ConfigWriter.get_api_service_raw(SERVICE_ID) or {}
    base_url = raw_service.get("base_url") or raw_service.get("baseUrl")
    if isinstance(base_url, str) and base_url.strip():
        normalized = base_url.strip()
        if not normalized.startswith(("http://", "https://")):
            normalized = f"http://{normalized}"
        return normalized.rstrip("/")

    sm = get_secret_manager()
    host = sm.get("qingteng_host")
    if isinstance(host, str) and host.strip():
        return f"http://{host.strip()}:80"

    return ""


def _resolve_verify_ssl(raw_service: dict[str, Any]) -> bool:
    value = raw_service.get("verify_ssl")
    if value is None:
        value = raw_service.get("ssl_verify")
    if value is None:
        custom_settings = raw_service.get("custom_settings", {})
        if isinstance(custom_settings, dict):
            value = custom_settings.get("verify_ssl", False)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _split_connection_target(
    base_url: str,
) -> tuple[type[httplib.HTTPConnection], str, int, str, bool]:
    parsed = urllib.parse.urlparse(base_url)
    scheme = (parsed.scheme or "http").lower()
    if scheme not in {"http", "https"}:
        raise ValueError(f"Unsupported Qingteng URL scheme: {scheme}")

    host = parsed.hostname
    if not host:
        raise ValueError("Invalid Qingteng URL: missing host")

    port = parsed.port or (443 if scheme == "https" else 80)
    base_path = parsed.path.rstrip("/")
    conn_cls = httplib.HTTPSConnection if scheme == "https" else httplib.HTTPConnection
    return conn_cls, host, port, base_path, scheme == "https"


def _build_request_path(base_path: str, path: str) -> str:
    if not base_path:
        return path
    return f"{base_path}{path}"


def _load_runtime_config() -> tuple[type[httplib.HTTPConnection], str, int, str, str, str, bool, bool] | None:
    raw_service = ConfigWriter.get_api_service_raw(SERVICE_ID) or {}
    sm = get_secret_manager()
    username = _resolve_ref(raw_service.get("username")) or sm.get("qingteng_username")
    password = _resolve_ref(raw_service.get("password")) or sm.get("qingteng_password")
    base_url = _resolve_base_url()
    if not base_url or not username or not password:
        return None
    conn_cls, host, port, base_path, is_https = _split_connection_target(base_url)
    return (
        conn_cls,
        host,
        port,
        base_path,
        username,
        password,
        _resolve_verify_ssl(raw_service),
        is_https,
    )


def _open_connection(
    conn_cls: type[httplib.HTTPConnection],
    host: str,
    port: int,
    *,
    verify_ssl: bool,
    is_https: bool,
):
    if is_https and not verify_ssl:
        return conn_cls(host, port, context=ssl._create_unverified_context())
    return conn_cls(host, port)


def _parse_json_response(response: Any) -> dict[str, Any]:
    payload = response.read()
    if not payload:
        return {}
    return json.loads(payload)


def _serialize_body(body: Optional[dict[str, Any]]) -> str:
    return json.dumps(body or {}, ensure_ascii=False, separators=(",", ":"))


def _normalize_query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple, set)):
        return ",".join(_normalize_query_value(item) for item in value)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _normalize_query_params(params: Optional[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in (params or {}).items():
        if value is None:
            continue
        result[key] = _normalize_query_value(value)
    return result


def _sha1(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _build_get_sign(com_id: str, query: dict[str, str], timestamp: int, sign_key: str) -> str:
    pieces = [f"{key}{query[key]}" for key in sorted(query.keys())]
    return _sha1(f"{com_id}{''.join(pieces)}{timestamp}{sign_key}")


def _build_body_sign(com_id: str, body_json: str, timestamp: int, sign_key: str) -> str:
    return _sha1(f"{com_id}{body_json}{timestamp}{sign_key}")


def _login_request(
    conn_cls: type[httplib.HTTPConnection],
    host: str,
    port: int,
    base_path: str,
    username: str,
    password: str,
    *,
    verify_ssl: bool,
    is_https: bool,
) -> tuple[bool, dict[str, Any] | str, dict[str, Any] | None]:
    conn = _open_connection(
        conn_cls, host, port, verify_ssl=verify_ssl, is_https=is_https
    )
    try:
        body = json.dumps({"username": username, "password": password})
        conn.request(
            method="POST",
            url=_build_request_path(base_path, "/v1/api/auth"),
            body=body,
            headers={"Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = _parse_json_response(response)

        if response.status != 200:
            return False, payload.get("message") or payload.get("msg") or "Login failed", payload
        if payload.get("success") is False:
            return False, payload.get("message") or payload.get("msg") or "Login failed", payload
        if "code" in payload and payload.get("code") not in (0, 200):
            return False, payload.get("message") or payload.get("msg") or "Login failed", payload

        data = payload.get("data") or {}
        sign_key = data.get("signKey")
        jwt = data.get("jwt")
        com_id = data.get("comId")
        if not sign_key or not jwt or com_id is None:
            return False, "Invalid login response: missing signKey, jwt, or comId", payload

        return True, {"signKey": sign_key, "jwt": jwt, "comId": str(com_id)}, payload
    except Exception as exc:
        return False, str(exc), None
    finally:
        conn.close()


def _error_message(payload: Any, default: str) -> str:
    if isinstance(payload, dict):
        for key in ("errorMessage", "errorDesc", "message", "msg", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return default


def _pick_output(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    if "data" in payload and any(key in payload for key in ("success", "code", "message", "msg")):
        return payload["data"]
    return payload


def _json_result(action: str, path: str, status: int, payload: Any) -> ToolResult:
    metadata = {"source": "Qingteng", "api": action, "path": path}
    if status != 200:
        return ToolResult(success=False, error=_error_message(payload, f"HTTP {status}"), output=payload, metadata=metadata)
    if isinstance(payload, dict):
        if payload.get("success") is False:
            return ToolResult(success=False, error=_error_message(payload, "Request failed"), output=payload, metadata=metadata)
        if payload.get("errorCode") not in (None, 0, 200):
            return ToolResult(success=False, error=_error_message(payload, "Request failed"), output=payload, metadata=metadata)
        if payload.get("code") not in (None, 0, 200):
            return ToolResult(success=False, error=_error_message(payload, "Request failed"), output=payload, metadata=metadata)
    return ToolResult(success=True, output=_pick_output(payload), metadata=metadata)


def _request_signed_json(
    method: str,
    path: str,
    *,
    query: Optional[dict[str, Any]] = None,
    body: Optional[dict[str, Any]] = None,
    action: str,
) -> ToolResult:
    config = _load_runtime_config()
    if not config:
        return ToolResult(
            success=False,
            error="Missing configuration: qingteng base_url/qingteng_host, qingteng_username, qingteng_password",
        )

    conn_cls, host, port, base_path, username, password, verify_ssl, is_https = config
    ok, auth_result, login_payload = _login_request(
        conn_cls,
        host,
        port,
        base_path,
        username,
        password,
        verify_ssl=verify_ssl,
        is_https=is_https,
    )
    if not ok:
        return ToolResult(success=False, error=str(auth_result), output=login_payload)

    auth = auth_result
    assert isinstance(auth, dict)

    method = method.upper()
    query_params = _normalize_query_params(query)
    body_payload = body or {}
    timestamp = int(time.time())

    if method == "GET":
        sign = _build_get_sign(auth["comId"], query_params, timestamp, auth["signKey"])
        body_json = None
    else:
        body_json = _serialize_body(body_payload)
        sign = _build_body_sign(auth["comId"], body_json, timestamp, auth["signKey"])

    url = path
    if query_params:
        url = f"{path}?{urllib.parse.urlencode(query_params)}"

    headers = {
        "Content-Type": "application/json",
        "comId": auth["comId"],
        "timestamp": str(timestamp),
        "sign": sign,
        "Authorization": f"Bearer {auth['jwt']}",
    }

    conn = _open_connection(
        conn_cls, host, port, verify_ssl=verify_ssl, is_https=is_https
    )
    try:
        conn.request(
            method=method,
            url=_build_request_path(base_path, url),
            body=body_json,
            headers=headers,
        )
        response = conn.getresponse()
        payload = _parse_json_response(response)
        return _json_result(action, path, response.status, payload)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))
    finally:
        conn.close()


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _require_fields(params: dict[str, Any], *fields: str) -> list[str]:
    return [field for field in fields if not _has_value(params.get(field))]


def _dict_param(params: dict[str, Any], key: str) -> dict[str, Any]:
    value = params.get(key)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _merge_dicts(*chunks: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for chunk in chunks:
        for key, value in chunk.items():
            if value is not None:
                result[key] = value
    return result


def _common_query(params: dict[str, Any]) -> dict[str, Any]:
    query = _dict_param(params, "query")
    for key in ("page", "size", "sorts", "groups", "time_range", "keyword", "fuzzy", "begin_time", "end_time"):
        if params.get(key) is not None:
            query[key] = params[key]
    return query


def _common_body(params: dict[str, Any]) -> dict[str, Any]:
    return _dict_param(params, "body")


def _paged_query(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    query = _dict_param(params, "query")
    for key in ("page", "size", "sorts"):
        if params.get(key) is not None:
            query[key] = params[key]
    for key in keys:
        if params.get(key) is not None:
            query[key] = params[key]
    return query


def _query_with_fields(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return _merge_dicts(_common_query(params), _pick(params, *keys))


def _body_with_fields(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return _merge_dicts(_common_body(params), _pick(params, *keys))


def _assets_resource_path(params: dict[str, Any]) -> str:
    return f"/external/api/assets/{params['resource']}/{params['os_type']}"


def _quote_param(params: dict[str, Any], key: str) -> str:
    return urllib.parse.quote(str(params[key]), safe="")


def _system_audit_query(params: dict[str, Any]) -> dict[str, Any]:
    query = _paged_query(params)
    if params.get("eventName") is not None:
        query["eventName"] = params["eventName"]
    if params.get("userName") is not None:
        query["userName"] = params["userName"]
    return query


def _pick(params: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: params[key] for key in keys if params.get(key) is not None}


def _assets_list_query(params: dict[str, Any]) -> dict[str, Any]:
    resource = str(params.get("resource", "")).lower()
    base_fields = ("groups", "keyword", "hostId", "hostname", "ip", "agentId", "businessGroupId", "status")
    resource_fields: dict[str, tuple[str, ...]] = {
        "host": (),
        "process": ("processName", "processPath", "processPid"),
        "account": ("accountName", "accountUid", "accountGroup"),
        "accountgroup": ("accountGroup",),
        "port": ("portNumber", "portProtocol"),
        "service": ("serviceName", "serviceState", "serviceStartType"),
        "dbinfo": ("dbName", "dbType"),
        "website": ("websiteName", "websiteDomain"),
        "webapp": ("websiteName", "websiteDomain"),
        "app": ("packageName", "packageVersion"),
        "pkg": ("packageName", "packageVersion"),
        "jar_pkg": ("packageName", "packageVersion"),
        "webframe": ("packageName", "packageVersion"),
        "task": ("taskName",),
        "env": (),
        "kernelmodule": ("packageName",),
    }
    return _paged_query(params, *(base_fields + resource_fields.get(resource, ())))


ASSET_RESOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "host": (),
    "process": ("processName", "processPath", "processPid"),
    "account": ("accountName", "accountUid", "accountGroup"),
    "accountgroup": ("accountGroup",),
    "port": ("portNumber", "portProtocol"),
    "service": ("serviceName", "serviceState", "serviceStartType"),
    "dbinfo": ("dbName", "dbType"),
    "website": ("websiteName", "websiteDomain"),
    "webapp": ("websiteName", "websiteDomain"),
    "app": ("packageName", "packageVersion"),
    "pkg": ("packageName", "packageVersion"),
    "jar_pkg": ("packageName", "packageVersion"),
    "webframe": ("packageName", "packageVersion"),
    "task": ("taskName",),
    "env": (),
    "kernelmodule": ("packageName",),
}


def _invalid_asset_resource_fields(params: dict[str, Any]) -> list[str]:
    resource = str(params.get("resource", "")).lower()
    if resource not in ASSET_RESOURCE_FIELDS:
        return []

    base_fields = {"groups", "keyword", "hostId", "hostname", "ip", "agentId", "businessGroupId", "status"}
    allowed = base_fields | set(ASSET_RESOURCE_FIELDS[resource])
    resource_specific_fields = {
        field
        for fields in ASSET_RESOURCE_FIELDS.values()
        for field in fields
    }
    provided_invalid: list[str] = []
    for field in sorted(resource_specific_fields - allowed):
        if _has_value(params.get(field)):
            provided_invalid.append(field)
    return provided_invalid


RISK_LIST_ALLOWED_FIELDS: dict[str, tuple[str, ...]] = {
    "patch_list": ("severity", "status", "hostId", "groups", "patch_name", "cve"),
    "risk_list": ("severity", "status", "hostId", "groups", "risk_name"),
    "weakpwd_list": ("severity", "status", "hostId", "groups", "accountName"),
    "weakfile_list": ("severity", "status", "hostId", "groups", "file_path"),
    "poc_list": ("severity", "status", "hostId", "groups", "cve", "ruleIds"),
}


DETECT_LIST_ALLOWED_FIELDS: dict[str, tuple[str, ...]] = {
    "brutecrack_list": ("hostId", "status", "ip", "account", "begin_time", "end_time"),
    "abnormallogin_list": ("keyword", "hostId", "ip", "account", "begin_time", "end_time"),
    "webshell_list": ("severity", "status", "hostId", "groups", "file_path"),
    "backdoor_list": ("severity", "status", "hostId", "groups"),
}


BASELINE_LIST_ALLOWED_FIELDS: dict[str, tuple[str, ...]] = {
    "job_list": ("name", "status", "group_ids", "auth_id"),
    "spec_check_result": ("specId", "status", "hostId"),
    "spec_failed_host": ("specId",),
    "auth_list": ("name", "auth_id"),
}


def _invalid_action_fields(
    action: str,
    params: dict[str, Any],
    allowed_map: dict[str, tuple[str, ...]],
) -> list[str]:
    allowed = set(allowed_map.get(action, ()))
    candidate_fields = {field for fields in allowed_map.values() for field in fields}
    provided_invalid: list[str] = []
    for field in sorted(candidate_fields - allowed):
        if _has_value(params.get(field)):
            provided_invalid.append(field)
    return provided_invalid


def _patch_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "patch_name", "cve")


def _risk_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "risk_name")


def _weakpwd_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "accountName")


def _weakfile_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "file_path")


def _poc_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "cve", "ruleIds")


def _brutecrack_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "hostId", "status", "ip", "account", "begin_time", "end_time")


def _abnormallogin_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "keyword", "hostId", "ip", "account", "begin_time", "end_time")


def _webshell_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups", "file_path")


def _backdoor_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "severity", "status", "hostId", "groups")


def _baseline_job_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "name", "status", "group_ids", "auth_id")


def _baseline_check_result_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "specId", "status", "hostId")


def _baseline_auth_list_query(params: dict[str, Any]) -> dict[str, Any]:
    return _paged_query(params, "name", "auth_id")


class ActionSpec:
    def __init__(
        self,
        method: str,
        path_builder: Callable[[dict[str, Any]], str] | str,
        *,
        query_builder: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        body_builder: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
    ) -> None:
        self.method = method
        self.path_builder = path_builder
        self.query_builder = query_builder
        self.body_builder = body_builder

    def path(self, params: dict[str, Any]) -> str:
        if isinstance(self.path_builder, str):
            return self.path_builder
        return self.path_builder(params)


ASSET_ACTIONS: dict[str, ActionSpec] = {
    "list": ActionSpec(
        "GET",
        _assets_resource_path,
        query_builder=_assets_list_query,
    ),
    "refresh": ActionSpec(
        "POST",
        lambda p: f"{_assets_resource_path(p)}/refresh",
        body_builder=lambda p: _body_with_fields(p, "hostIds", "groups", "force", "taskName"),
    ),
    "refresh_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/assets/refreshjob/{_quote_param(p, 'jobId')}",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
    "host_info_sync": ActionSpec(
        "POST",
        "/external/api/assets/host/hostInfoSync",
        body_builder=lambda p: _body_with_fields(p, "hostInfoList", "source", "syncMode"),
    ),
    "delete_host": ActionSpec(
        "POST",
        lambda p: f"/external/api/assets/hostoperation/deletehost/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "hostIds", "agentIds", "reason", "password", "allOffline"),
    ),
    "batch_create_group": ActionSpec(
        "POST",
        lambda p: f"/external/api/assets/group/batch_create_group/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "groupNames", "parentGroupId"),
    ),
    "refresh_all": ActionSpec(
        "POST",
        lambda p: f"/external/api/assets/{p['os_type']}/refresh",
        body_builder=lambda p: _body_with_fields(p, "groups", "force", "taskName"),
    ),
    "batch_move_host": ActionSpec(
        "POST",
        "/external/api/assets/host/batch_move_host",
        body_builder=lambda p: _body_with_fields(p, "hostIds", "targetGroupId", "sourceGroupId"),
    ),
}


RISK_ACTIONS: dict[str, ActionSpec] = {
    "patch_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/patch/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "time_range", "taskName"),
    ),
    "patch_scan_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/patch/{p['os_type']}/check/status",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
    "patch_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/patch/{p['os_type']}/list",
        query_builder=_patch_list_query,
    ),
    "patch_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/patch/{p['os_type']}/{_quote_param(p, 'id')}",
        query_builder=_common_query,
    ),
    "patch_business_impact_check": ActionSpec(
        "POST",
        "/external/api/vul/patch/linux/business_impact/check",
        body_builder=lambda p: _body_with_fields(p, "patchIds", "hostIds", "groups"),
    ),
    "patch_business_impact_status": ActionSpec(
        "GET", "/external/api/vul/patch/linux/business_impact/check/status", query_builder=_common_query
    ),
    "patch_business_impact_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/patch/linux/business_impact/{_quote_param(p, 'patchId')}",
        query_builder=_common_query,
    ),
    "risk_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/{p['risk_type']}/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "time_range", "taskName"),
    ),
    "risk_scan_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/{p['risk_type']}/{p['os_type']}/check/status",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
    "risk_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/{p['risk_type']}/{p['os_type']}/list",
        query_builder=_risk_list_query,
    ),
    "risk_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/{p['risk_type']}/{p['os_type']}/{_quote_param(p, 'id')}",
        query_builder=_common_query,
    ),
    "weakpwd_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/weakpwd/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "taskName"),
    ),
    "weakpwd_scan_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/weakpwd/{p['os_type']}/check/status",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
    "weakpwd_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/weakpwd/{p['os_type']}/list",
        query_builder=_weakpwd_list_query,
    ),
    "weakpwd_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/weakpwd/{p['os_type']}/{_quote_param(p, 'id')}",
        query_builder=_common_query,
    ),
    "weakfile_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/websecurity/weakfile/{p['os_type']}",
        query_builder=_weakfile_list_query,
    ),
    "weakfile_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/websecurity/weakfile/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "taskName"),
    ),
    "weakfile_scan_status": ActionSpec(
        "GET", lambda p: f"/external/api/websecurity/weakfile/{p['os_type']}/check/status", query_builder=_common_query
    ),
    "weakfile_download_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/websecurity/weakfile/{p['os_type']}/download",
        body_builder=lambda p: _body_with_fields(p, "ids", "filters", "fileName"),
    ),
    "weakfile_download": ActionSpec(
        "GET",
        lambda p: f"/external/api/websecurity/weakfile/{p['os_type']}/download/{_quote_param(p, 'download_id')}",
        query_builder=_common_query,
    ),
    "poc_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/poc/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "ruleIds", "taskName"),
    ),
    "poc_scan_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/poc/{p['os_type']}/check/status",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
    "poc_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/poc/{p['os_type']}/list",
        query_builder=_poc_list_query,
    ),
    "poc_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/vul/poc/{p['os_type']}/{_quote_param(p, 'recordId')}",
        query_builder=_common_query,
    ),
    "poc_job_rule_list": ActionSpec(
        "GET",
        lambda p: (
            f"/external/api/vul/poc/job/{p['os_type']}/rule_list"
            if p.get("os_type") == "win"
            else "/external/api/vul/poc/job/rule_list"
        ),
        query_builder=_common_query,
    ),
    "poc_job_add": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/poc/job/{p['os_type']}/add",
        body_builder=lambda p: _body_with_fields(p, "jobName", "ruleIds", "groups", "hostIds", "schedule"),
    ),
    "poc_job_fix": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/poc/job/{p['os_type']}/fix",
        body_builder=lambda p: _body_with_fields(p, "jobId", "jobName", "ruleIds", "groups", "hostIds", "schedule"),
    ),
    "poc_job_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/vul/poc/job/{p['os_type']}/{_quote_param(p, 'jobId')}",
        body_builder=_common_body,
    ),
    "poc_job_execute": ActionSpec(
        "POST",
        lambda p: f"/external/api/vul/poc/job/{p['os_type']}/execute",
        body_builder=lambda p: _body_with_fields(p, "jobId", "jobIds"),
    ),
    "poc_job_status": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/status", body_builder=_common_body
    ),
    "poc_job_error_host": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/error_host", body_builder=_common_body
    ),
    "poc_job_list": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/list", body_builder=_common_body
    ),
    "poc_job_tasks": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/tasks", body_builder=_common_body
    ),
    "poc_job_stats": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/stats", body_builder=_common_body
    ),
    "poc_job_result_detail": ActionSpec(
        "POST", lambda p: f"/external/api/vul/poc/job/{p['os_type']}/result_detail", body_builder=_common_body
    ),
    "linux_all_scan": ActionSpec(
        "POST",
        "/external/api/vul/linux/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "taskName"),
    ),
    "linux_all_scan_status": ActionSpec(
        "GET",
        "/external/api/vul/linux/check/status",
        query_builder=lambda p: _query_with_fields(p, "status"),
    ),
}


DETECT_ACTIONS: dict[str, ActionSpec] = {
    "shelllog_list": ActionSpec("GET", "/external/api/detect/shelllog/linux", query_builder=_common_query),
    "brutecrack_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/detect/brutecrack/{p['os_type']}",
        query_builder=_brutecrack_list_query,
    ),
    "brutecrack_log": ActionSpec(
        "GET",
        lambda p: f"/external/api/detect/brutecrack/{p['os_type']}/log",
        query_builder=lambda p: _query_with_fields(p, "hostId", "ip", "account"),
    ),
    "brutecrack_block": ActionSpec(
        "POST",
        lambda p: f"/external/api/detect/brutecrack/{p['os_type']}/block",
        body_builder=lambda p: _body_with_fields(p, "ip", "account", "block", "duration", "reason"),
    ),
    "abnormallogin_list": ActionSpec(
        "GET", lambda p: f"/external/api/detect/abnormallogin/{p['os_type']}", query_builder=_abnormallogin_list_query
    ),
    "bounceshell_list": ActionSpec("GET", lambda p: f"/external/api/detect/bounceshell/{p.get('os_type', 'linux')}", query_builder=_common_query),
    "localrights_list": ActionSpec("GET", "/external/api/detect/localrights/linux", query_builder=_common_query),
    "abnormallogin_rule_set": ActionSpec(
        "POST",
        lambda p: f"/external/api/detect/abnormallogin/{p['os_type']}/rule",
        body_builder=lambda p: _body_with_fields(
            p, "id", "rule_name", "source_ips", "target_ips", "users", "ports", "enabled"
        ),
    ),
    "abnormallogin_rule_get": ActionSpec(
        "GET",
        lambda p: f"/external/api/detect/abnormallogin/{p['os_type']}/rule/{_quote_param(p, 'id')}",
        query_builder=_common_query,
    ),
    "abnormallogin_rule_list": ActionSpec(
        "GET", lambda p: f"/external/api/detect/abnormallogin/{p['os_type']}/rule", query_builder=_common_query
    ),
    "abnormallogin_rule_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/detect/abnormallogin/{p['os_type']}/rule/{_quote_param(p, 'id')}",
        body_builder=_common_body,
    ),
    "webshell_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/websecurity/webshell/{p['os_type']}",
        query_builder=_webshell_list_query,
    ),
    "webshell_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/websecurity/webshell/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "taskName"),
    ),
    "webshell_scan_status": ActionSpec(
        "GET", lambda p: f"/external/api/websecurity/webshell/{p['os_type']}/check/status", query_builder=_common_query
    ),
    "webshell_download": ActionSpec(
        "GET",
        lambda p: f"/external/api/websecurity/webshell/{p['os_type']}/download/{_quote_param(p, 'download_id')}",
        query_builder=_common_query,
    ),
    "backdoor_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/detect/backdoor/{p['os_type']}",
        query_builder=_backdoor_list_query,
    ),
    "backdoor_scan": ActionSpec(
        "POST",
        lambda p: f"/external/api/detect/backdoor/{p['os_type']}/check",
        body_builder=lambda p: _body_with_fields(p, "groups", "hostIds", "taskName"),
    ),
    "backdoor_scan_status": ActionSpec(
        "GET", lambda p: f"/external/api/detect/backdoor/{p['os_type']}/check/status", query_builder=_common_query
    ),
    "honeypot_list": ActionSpec(
        "GET", lambda p: f"/external/api/detect/honeypot/{p['os_type']}", query_builder=_common_query
    ),
    "honeypot_rule_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule",
        body_builder=lambda p: _body_with_fields(p, "name", "port", "protocol", "enabled", "rule"),
    ),
    "honeypot_rules_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rules",
        body_builder=lambda p: _body_with_fields(p, "rules"),
    ),
    "honeypot_rule_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule/{_quote_param(p, 'id')}",
        body_builder=_common_body,
    ),
    "honeypot_rules_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rules",
        body_builder=lambda p: _body_with_fields(p, "ids"),
    ),
    "honeypot_rule_list": ActionSpec(
        "GET", lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule", query_builder=_common_query
    ),
    "honeypot_rule_get": ActionSpec(
        "GET",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule/{_quote_param(p, 'id')}",
        query_builder=_common_query,
    ),
    "honeypot_rule_update": ActionSpec(
        "PUT",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule",
        body_builder=lambda p: _body_with_fields(p, "id", "name", "port", "protocol", "enabled", "rule"),
    ),
    "honeypot_rule_enable": ActionSpec(
        "PUT",
        lambda p: f"/external/api/detect/honeypot/{p['os_type']}/rule/enable",
        body_builder=lambda p: _body_with_fields(p, "id", "enabled"),
    ),
}


BASELINE_ACTIONS: dict[str, ActionSpec] = {
    "job_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/baseline/job/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "name", "group_ids", "host_ids", "rule_ids", "cron", "auth_id"),
    ),
    "job_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/baseline/job/{p['os_type']}/{_quote_param(p, 'specId')}",
        body_builder=_common_body,
    ),
    "job_update": ActionSpec(
        "PUT",
        lambda p: f"/external/api/baseline/job/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(
            p, "specId", "name", "group_ids", "host_ids", "rule_ids", "cron", "auth_id"
        ),
    ),
    "job_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/baseline/job/{p['os_type']}",
        query_builder=_baseline_job_list_query,
    ),
    "job_execute": ActionSpec(
        "POST",
        lambda p: f"/external/api/baseline/job/{p['os_type']}/execute",
        body_builder=lambda p: _body_with_fields(p, "specId", "specIds"),
    ),
    "job_status": ActionSpec(
        "GET",
        lambda p: f"/external/api/baseline/job/{p['os_type']}/getStatus/{_quote_param(p, 'specId')}",
        query_builder=_common_query,
    ),
    "spec_rule_list": ActionSpec(
        "GET", lambda p: f"/external/api/baseline/spec/rule/{p['os_type']}", query_builder=_common_query
    ),
    "spec_check_result": ActionSpec(
        "GET",
        lambda p: f"/external/api/baseline/spec/checkResult/{p['os_type']}",
        query_builder=_baseline_check_result_query,
    ),
    "spec_failed_host": ActionSpec(
        "GET",
        lambda p: f"/external/api/baseline/spec/failedHost/{p['os_type']}",
        query_builder=lambda p: _query_with_fields(p, "specId"),
    ),
    "auth_login": ActionSpec(
        "GET", lambda p: f"/external/api/baseline/auth/login/{p['os_type']}", query_builder=_common_query
    ),
    "auth_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/baseline/auth/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "name", "username", "password", "port", "hosts"),
    ),
    "auth_update": ActionSpec(
        "PUT",
        lambda p: f"/external/api/baseline/auth/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "auth_id", "name", "username", "password", "port", "hosts"),
    ),
    "auth_delete": ActionSpec(
        "DELETE", lambda p: f"/external/api/baseline/auth/{p['os_type']}", body_builder=_common_body
    ),
    "auth_list": ActionSpec(
        "GET",
        lambda p: f"/external/api/baseline/auth/list/{p['os_type']}",
        query_builder=_baseline_auth_list_query,
    ),
    "job_batch_create": ActionSpec(
        "POST",
        lambda p: f"/external/api/baseline/job/{p['os_type']}",
        body_builder=lambda p: _body_with_fields(p, "jobs"),
    ),
}


FASTJOB_ACTIONS: dict[str, ActionSpec] = {
    "task_list": ActionSpec(
        "GET",
        "/external/api/fastjob/task/list",
        query_builder=lambda p: _paged_query(p, "osType", "ids", "name", "categories", "updatedTimeRange"),
    ),
    "task_detail": ActionSpec(
        "GET",
        lambda p: f"/external/api/fastjob/task/{_quote_param(p, 'taskId')}",
        query_builder=_common_query,
    ),
    "job_create": ActionSpec(
        "POST",
        "/external/api/fastjob/job",
        body_builder=lambda p: _body_with_fields(
            p, "name", "osType", "description", "realm", "realmName", "taskType", "taskId", "taskParams", "cron", "cronEnable"
        ),
    ),
    "job_update": ActionSpec(
        "PUT",
        lambda p: f"/external/api/fastjob/job/{_quote_param(p, 'jobId')}",
        body_builder=lambda p: _body_with_fields(
            p, "name", "osType", "description", "realm", "realmName", "taskType", "taskId", "taskParams", "cron", "cronEnable"
        ),
    ),
    "job_delete": ActionSpec(
        "DELETE",
        lambda p: f"/external/api/fastjob/job/{_quote_param(p, 'jobId')}",
        body_builder=_common_body,
    ),
    "job_list": ActionSpec(
        "GET",
        "/external/api/fastjob/job",
        query_builder=lambda p: _paged_query(p, "osType"),
    ),
    "job_execute": ActionSpec(
        "POST",
        lambda p: f"/external/api/fastjob/job/execute/{_quote_param(p, 'id')}",
        body_builder=_common_body,
    ),
    "job_execute_list": ActionSpec(
        "GET",
        "/external/api/fastjob/job/execute",
        query_builder=lambda p: _paged_query(p, "osType"),
    ),
    "task_result": ActionSpec(
        "GET",
        lambda p: f"/external/api/fastjob/job/task/result/{_quote_param(p, 'taskRecordId')}",
        query_builder=_common_query,
    ),
    "task_error": ActionSpec(
        "GET",
        lambda p: f"/external/api/fastjob/job/task/error/{_quote_param(p, 'taskRecordId')}",
        query_builder=_common_query,
    ),
}


ASSETDISCOVERY_ACTIONS: dict[str, ActionSpec] = {
    "discovered_host_list": ActionSpec(
        "GET",
        "/external/api/discoveredhost/list",
        query_builder=_common_query,
    ),
    "job_create": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/create",
        body_builder=lambda p: _body_with_fields(
            p, "name", "kind", "values", "cronExpression", "osDetection", "ipList", "advanceConfigs"
        ),
    ),
    "job_delete": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/delete",
        body_builder=lambda p: _body_with_fields(p, "specId"),
    ),
    "job_find": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/find",
        body_builder=lambda p: _body_with_fields(p, "specId"),
    ),
    "job_update": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/update",
        body_builder=lambda p: _body_with_fields(
            p, "specId", "name", "kind", "values", "osDetection", "ipList", "advanceConfigs"
        ),
    ),
    "job_list": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/list",
        body_builder=lambda p: _body_with_fields(p, "name", "scanType"),
    ),
    "job_execute": ActionSpec(
        "POST",
        "/external/api/assetdiscovery/job/execute",
        body_builder=lambda p: _body_with_fields(p, "specId"),
    ),
}


MICROSEG_ACTIONS: dict[str, ActionSpec] = {
    "seg_list": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/segmentation/list",
        query_builder=lambda p: _paged_query(p, "groups"),
    ),
    "seg_detail": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/segmentation/detail",
        query_builder=lambda p: _query_with_fields(p, "agentId"),
    ),
    "seg_create": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/segmentation/create",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "remark", "direction", "ipList", "portList"),
    ),
    "seg_edit": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/segmentation/edit",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "remark", "direction", "ipList", "portList"),
    ),
    "seg_delete": ActionSpec(
        "DELETE",
        "/external/api/ms-srv/api/segmentation/del",
        body_builder=lambda p: _body_with_fields(p, "agentIds"),
    ),
    "seg_real_delete": ActionSpec(
        "DELETE",
        "/external/api/ms-srv/api/segmentation/realDel",
        body_builder=lambda p: _body_with_fields(p, "agentIds"),
    ),
    "seg_retry": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/segmentation/retry",
        body_builder=lambda p: _body_with_fields(p, "agentIds"),
    ),
    "host_list": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/hosts/list",
        query_builder=lambda p: _paged_query(p, "groups"),
    ),
    "host_ms_enable": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/ms-enable",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "enabled"),
    ),
    "host_access_control_mode": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/access-control-mode",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "accessControlModeSetting"),
    ),
    "host_run_status": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/run-status",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "runStatusSetting"),
    ),
    "host_protect_status": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/protect-status",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "runStatusSetting"),
    ),
    "host_limit_out": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/limit-out",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "limitOutSetting"),
    ),
    "host_black_strategy_enable": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/hosts/black-strategy-enable",
        body_builder=lambda p: _body_with_fields(p, "agentIds", "blackStrategyEnableSetting"),
    ),
    "black_list": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/black-strategy/list",
        query_builder=lambda p: _paged_query(p, "groups"),
    ),
    "black_create": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/black-strategy/create",
        body_builder=lambda p: _body_with_fields(
            p, "strategyName", "remark", "ports", "protos", "displayPort", "switchStatus",
            "dstTagIds", "dstGroupIds", "dstRealmType", "dstIpList",
            "srcTagIds", "srcGroupIds", "srcRealmType", "srcAgentIds", "srcIpList",
        ),
    ),
    "black_update": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/black-strategy/update",
        body_builder=lambda p: _body_with_fields(
            p, "id", "strategyName", "remark", "ports", "protos", "displayPort", "switchStatus",
            "dstTagIds", "dstGroupIds", "dstRealmType", "dstIpList",
            "srcTagIds", "srcGroupIds", "srcRealmType", "srcAgentIds", "srcIpList",
        ),
    ),
    "black_update_switch": ActionSpec(
        "POST",
        "/external/api/ms-srv/api/black-strategy/update-switch",
        body_builder=lambda p: _body_with_fields(p, "ids", "switchStatus"),
    ),
    "black_delete": ActionSpec(
        "DELETE",
        "/external/api/ms-srv/api/black-strategy/delete",
        body_builder=lambda p: _body_with_fields(p, "ids"),
    ),
    "black_host_list": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/black-strategy/strategy-host-list",
        query_builder=lambda p: _paged_query(p, "ip", "msSwitchStatus", "strategyId", "strategyHostType"),
    ),
    "black_detail": ActionSpec(
        "GET",
        "/external/api/ms-srv/api/black-strategy/detail",
        query_builder=lambda p: _query_with_fields(p, "id"),
    ),
}


GROUP_ACTIONS = {
    "assets": ASSET_ACTIONS,
    "risk": RISK_ACTIONS,
    "detect": DETECT_ACTIONS,
    "baseline": BASELINE_ACTIONS,
    "fastjob": FASTJOB_ACTIONS,
    "assetdiscovery": ASSETDISCOVERY_ACTIONS,
    "microseg": MICROSEG_ACTIONS,
}


def _validate_assets(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action in {"list", "refresh"}:
        missing.extend(_require_fields(params, "resource", "os_type"))
    if action == "list":
        invalid_fields = _invalid_asset_resource_fields(params)
        if invalid_fields:
            resource = str(params.get("resource", ""))
            return (
                f"Unsupported filters for assets.list resource={resource}: "
                + ", ".join(invalid_fields)
            )
    elif action == "refresh_status":
        missing.extend(_require_fields(params, "jobId"))
    elif action in {"delete_host", "batch_create_group", "refresh_all"}:
        missing.extend(_require_fields(params, "os_type"))
    elif action == "host_info_sync":
        if not _has_value(params.get("hostInfoList")) and not _has_value(_dict_param(params, "body")):
            missing.append("hostInfoList/body")
    elif action == "batch_move_host":
        if not _has_value(params.get("hostIds")) and not _has_value(_dict_param(params, "body")):
            missing.append("hostIds/body")
        if not _has_value(params.get("targetGroupId")) and not _has_value(_dict_param(params, "body")):
            missing.append("targetGroupId/body")
    if missing:
        return f"Missing required parameters for assets.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_risk(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action.startswith("patch_") and action not in {
        "patch_business_impact_check",
        "patch_business_impact_status",
        "patch_business_impact_detail",
    }:
        missing.extend(_require_fields(params, "os_type"))
    if action == "patch_detail":
        missing.extend(_require_fields(params, "id"))
    if action == "patch_business_impact_detail":
        missing.extend(_require_fields(params, "patchId"))
    if action in {"risk_scan", "risk_scan_status", "risk_list", "risk_detail"}:
        missing.extend(_require_fields(params, "risk_type", "os_type"))
    if action == "risk_detail":
        missing.extend(_require_fields(params, "id"))
    if action in {"weakpwd_scan", "weakpwd_scan_status", "weakpwd_list", "weakpwd_detail"}:
        missing.extend(_require_fields(params, "os_type"))
    if action == "weakpwd_detail":
        missing.extend(_require_fields(params, "id"))
    if action in {"weakfile_list", "weakfile_scan", "weakfile_scan_status", "weakfile_download_create", "weakfile_download"}:
        missing.extend(_require_fields(params, "os_type"))
    if action == "weakfile_download":
        missing.extend(_require_fields(params, "download_id"))
    if action in {
        "poc_scan",
        "poc_scan_status",
        "poc_list",
        "poc_detail",
        "poc_job_add",
        "poc_job_fix",
        "poc_job_delete",
        "poc_job_execute",
        "poc_job_status",
        "poc_job_error_host",
        "poc_job_list",
        "poc_job_tasks",
        "poc_job_stats",
        "poc_job_result_detail",
    }:
        missing.extend(_require_fields(params, "os_type"))
    if action == "poc_job_rule_list":
        pass  # os_type 可选：win 时调用 /win/rule_list，否则调用通用 /rule_list（Linux）
    if action == "poc_detail":
        missing.extend(_require_fields(params, "recordId"))
    if action == "poc_job_delete":
        missing.extend(_require_fields(params, "jobId"))
    if action in {"poc_job_add", "poc_job_fix"}:
        if not _has_value(params.get("jobName")) and not _has_value(_dict_param(params, "body")):
            missing.append("jobName/body")
    if action == "poc_job_execute":
        if not _has_value(params.get("jobId")) and not _has_value(params.get("jobIds")) and not _has_value(_dict_param(params, "body")):
            missing.append("jobId/jobIds/body")
    invalid_fields = _invalid_action_fields(action, params, RISK_LIST_ALLOWED_FIELDS)
    if invalid_fields:
        return f"Unsupported filters for risk.{action}: {', '.join(invalid_fields)}"
    if missing:
        return f"Missing required parameters for risk.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_detect(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action in {
        "brutecrack_list",
        "brutecrack_log",
        "brutecrack_block",
        "abnormallogin_list",
        "abnormallogin_rule_set",
        "abnormallogin_rule_get",
        "abnormallogin_rule_list",
        "abnormallogin_rule_delete",
        "webshell_list",
        "webshell_scan",
        "webshell_scan_status",
        "webshell_download",
        "backdoor_list",
        "backdoor_scan",
        "backdoor_scan_status",
        "honeypot_list",
        "honeypot_rule_create",
        "honeypot_rules_create",
        "honeypot_rule_delete",
        "honeypot_rules_delete",
        "honeypot_rule_list",
        "honeypot_rule_get",
        "honeypot_rule_update",
        "honeypot_rule_enable",
    }:
        missing.extend(_require_fields(params, "os_type"))
    if action in {"abnormallogin_rule_get", "abnormallogin_rule_delete", "honeypot_rule_delete", "honeypot_rule_get"}:
        missing.extend(_require_fields(params, "id"))
    if action == "webshell_download":
        missing.extend(_require_fields(params, "download_id"))
    if action == "brutecrack_block":
        if not _has_value(params.get("ip")) and not _has_value(_dict_param(params, "body")):
            missing.append("ip/body")
    if action in {"honeypot_rule_update", "honeypot_rule_enable"}:
        if not _has_value(params.get("id")) and not _has_value(_dict_param(params, "body")):
            missing.append("id/body")
    invalid_fields = _invalid_action_fields(action, params, DETECT_LIST_ALLOWED_FIELDS)
    if invalid_fields:
        return f"Unsupported filters for detect.{action}: {', '.join(invalid_fields)}"
    if missing:
        return f"Missing required parameters for detect.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_baseline(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action in {
        "job_create",
        "job_delete",
        "job_update",
        "job_list",
        "job_execute",
        "job_status",
        "spec_rule_list",
        "spec_check_result",
        "spec_failed_host",
        "auth_login",
        "auth_create",
        "auth_update",
        "auth_delete",
        "auth_list",
        "job_batch_create",
    }:
        missing.extend(_require_fields(params, "os_type"))
    if action in {"job_delete", "job_status"}:
        missing.extend(_require_fields(params, "specId"))
    if action in {"job_create", "job_update"}:
        if not _has_value(params.get("name")) and not _has_value(_dict_param(params, "body")):
            missing.append("name/body")
    if action == "job_execute":
        if not _has_value(params.get("specId")) and not _has_value(params.get("specIds")) and not _has_value(_dict_param(params, "body")):
            missing.append("specId/specIds/body")
    invalid_fields = _invalid_action_fields(action, params, BASELINE_LIST_ALLOWED_FIELDS)
    if invalid_fields:
        return f"Unsupported filters for baseline.{action}: {', '.join(invalid_fields)}"
    if missing:
        return f"Missing required parameters for baseline.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_fastjob(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action == "task_detail":
        missing.extend(_require_fields(params, "taskId"))
    if action in {"job_update", "job_delete"}:
        missing.extend(_require_fields(params, "jobId"))
    if action == "job_execute":
        missing.extend(_require_fields(params, "id"))
    if action in {"task_result", "task_error"}:
        missing.extend(_require_fields(params, "taskRecordId"))
    if action == "job_create":
        if not _has_value(params.get("name")) and not _has_value(_dict_param(params, "body")):
            missing.append("name/body")
    if missing:
        return f"Missing required parameters for fastjob.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_assetdiscovery(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    if action in {"job_delete", "job_find", "job_execute"}:
        if not _has_value(params.get("specId")) and not _has_value(_dict_param(params, "body")):
            missing.append("specId/body")
    if action == "job_update":
        if not _has_value(params.get("specId")) and not _has_value(_dict_param(params, "body")):
            missing.append("specId/body")
    if action == "job_create":
        if not _has_value(params.get("name")) and not _has_value(_dict_param(params, "body")):
            missing.append("name/body")
    if missing:
        return f"Missing required parameters for assetdiscovery.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


def _validate_microseg(action: str, params: dict[str, Any]) -> Optional[str]:
    missing: list[str] = []
    seg_write_actions = {
        "seg_create", "seg_edit", "seg_delete", "seg_real_delete", "seg_retry",
        "host_ms_enable", "host_access_control_mode", "host_run_status",
        "host_protect_status", "host_limit_out", "host_black_strategy_enable",
    }
    if action in seg_write_actions:
        if not _has_value(params.get("agentIds")) and not _has_value(_dict_param(params, "body")):
            missing.append("agentIds/body")
    if action == "seg_detail":
        if not _has_value(params.get("agentId")) and not _has_value(_dict_param(params, "query")):
            missing.append("agentId/query")
    if action == "black_detail":
        if not _has_value(params.get("id")) and not _has_value(_dict_param(params, "query")):
            missing.append("id/query")
    if action in {"black_update_switch", "black_delete"}:
        if not _has_value(params.get("ids")) and not _has_value(_dict_param(params, "body")):
            missing.append("ids/body")
    if action in {"black_create", "black_update"}:
        if not _has_value(params.get("strategyName")) and not _has_value(_dict_param(params, "body")):
            missing.append("strategyName/body")
    if action == "black_update":
        if not _has_value(params.get("id")) and not _has_value(_dict_param(params, "body")):
            missing.append("id/body")
    if missing:
        return f"Missing required parameters for microseg.{action}: {', '.join(dict.fromkeys(missing))}"
    return None


VALIDATORS = {
    "assets": _validate_assets,
    "risk": _validate_risk,
    "detect": _validate_detect,
    "baseline": _validate_baseline,
    "fastjob": _validate_fastjob,
    "assetdiscovery": _validate_assetdiscovery,
    "microseg": _validate_microseg,
}


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    del ctx
    specs = GROUP_ACTIONS[group]
    spec = specs.get(action)
    if spec is None:
        return ToolResult(
            success=False,
            error=f"Unsupported {group} action: {action}. Available actions: {', '.join(sorted(specs))}",
        )

    validation_error = VALIDATORS[group](action, params)
    if validation_error:
        return ToolResult(success=False, error=validation_error)

    path = spec.path(params)
    query = spec.query_builder(params) if spec.query_builder else None
    body = spec.body_builder(params) if spec.body_builder else None
    return _request_signed_json(spec.method, path, query=query, body=body, action=f"{group}.{action}")


async def login(ctx: ToolContext, **kwargs: Any) -> ToolResult:
    del ctx, kwargs
    config = _load_runtime_config()
    if not config:
        return ToolResult(
            success=False,
            error="Missing configuration: qingteng base_url/qingteng_host, qingteng_username, qingteng_password",
        )
    conn_cls, host, port, base_path, username, password = config
    ok, result, payload = _login_request(conn_cls, host, port, base_path, username, password)
    if not ok:
        return ToolResult(success=False, error=str(result), output=payload)
    return ToolResult(success=True, output=result, metadata={"source": "Qingteng", "api": "login", "path": "/v1/api/auth"})


async def system_audit(
    ctx: ToolContext,
    eventName: str | None = None,
    userName: str | None = None,
    page: int = 0,
    size: int = 20,
    sorts: str | None = None,
    query: Optional[dict[str, Any]] = None,
    **kwargs: Any,
) -> ToolResult:
    del ctx
    params = {"eventName": eventName, "userName": userName, "page": page, "size": size, "sorts": sorts, "query": query}
    params.update(kwargs)
    return _request_signed_json(
        "GET",
        "/external/api/system/audit",
        query=_system_audit_query(params),
        action="system.audit",
    )


async def assets(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "assets", action, **params)


async def risk(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "risk", action, **params)


async def detect(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "detect", action, **params)


async def baseline(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "baseline", action, **params)


async def fastjob(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "fastjob", action, **params)


async def assetdiscovery(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "assetdiscovery", action, **params)


async def microseg(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "microseg", action, **params)
