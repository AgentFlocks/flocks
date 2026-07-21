from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from typing import Any, Callable, Optional

import requests

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "360_fw"
STORAGE_KEY = "360_fw_v5_5"
PRODUCT_VERSION = "5.5"
FW_SOFTWARE_VERSION = "V5.5"
FW_BUILD_VERSION = "V5.5R605P000B20240625"


class FwApiError(RuntimeError):
    pass


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout


ActionBuilder = Callable[[dict[str, Any]], Any]
ActionSpec = tuple[str, str, Optional[ActionBuilder]]


def _methods(*values: str) -> list[str]:
    return list(values)


DOCUMENTED_API_METHODS: dict[str, list[str]] = {
    "/login": _methods("POST"),
    "/sys_info": _methods("GET"),
    "/addressobj": _methods("GET", "POST", "PUT", "DELETE"),
    "/addressgroup": _methods("GET", "POST", "PUT", "DELETE"),
    "/serviceobj": _methods("GET", "POST", "PUT", "DELETE"),
    "/servicegroup": _methods("GET", "POST", "PUT", "DELETE"),
    "/predefined_service": _methods("GET"),
    "/dom_obj": _methods("GET", "POST", "DELETE"),
    "/dns_custom": _methods("GET", "POST", "PUT", "DELETE"),
    "/dns_group": _methods("GET", "POST", "PUT", "DELETE"),
    "/timeabsobj": _methods("GET", "POST", "DELETE"),
    "/timecycobj": _methods("GET", "POST", "PUT", "DELETE"),
    "/fwpolicy": _methods("GET", "POST", "PUT", "DELETE"),
    "/fwpolicy_state": _methods("PUT"),
    "/fwpolicy_move": _methods("PUT"),
    "/policy_group": _methods("GET", "POST", "DELETE"),
    "/app_policy": _methods("GET", "POST", "DELETE"),
    "/web_policy": _methods("GET", "POST", "DELETE"),
    "/interface": _methods("GET"),
    "/vlan": _methods("GET", "POST", "PUT", "DELETE"),
    "/vxlan": _methods("GET"),
    "/static_route": _methods("GET", "POST", "DELETE"),
    "/healthcheck_list": _methods("GET", "POST", "PUT", "DELETE"),
    "/link_health_check": _methods("GET", "POST", "PUT", "DELETE"),
    "/policy_route": _methods("GET", "POST", "DELETE"),
    "/sdwan_policy": _methods("GET", "POST", "DELETE"),
    "/sdwan_status": _methods("GET"),
    "/woc_policy_state": _methods("GET"),
    "/qos_line": _methods("GET", "POST", "PUT", "DELETE"),
    "/qos_policy": _methods("GET", "POST", "DELETE"),
    "/policy_qos_line": _methods("GET"),
    "/monitor_qos_policy": _methods("GET"),
    "/security_region": _methods("GET"),
    "/nat_pool": _methods("GET", "POST", "DELETE"),
    "/nat_rule_src": _methods("GET"),
    "/nat_rule_dst": _methods("GET"),
    "/nat_rule_static": _methods("GET"),
    "/autoike": _methods("GET", "POST", "DELETE"),
    "/phase2ike": _methods("POST", "DELETE"),
    "/ipsec_policy": _methods("GET", "POST", "DELETE"),
    "/ikesa": _methods("GET"),
    "/ipsecsa": _methods("GET"),
    "/tunnel_status_table": _methods("GET"),
    "/tunnel_status_line": _methods("GET"),
    "/tunnel_monitor": _methods("POST", "DELETE"),
    "/gre": _methods("GET", "POST", "PUT", "DELETE"),
    "/bgp_info": _methods("GET", "POST", "DELETE"),
    "/bgp_network": _methods("GET", "POST", "DELETE"),
    "/bgp_peer_group": _methods("GET", "POST", "DELETE"),
    "/bgp_neighbors": _methods("GET", "POST", "DELETE"),
    "/bgp_access_list": _methods("GET", "POST", "DELETE"),
    "/bgp_filter_list": _methods("GET", "POST", "DELETE"),
    "/bgp_route_map": _methods("GET", "POST", "DELETE"),
    "/bgp_map_list": _methods("GET", "POST", "DELETE"),
    "/bgp_prefix_list": _methods("GET", "POST", "DELETE"),
    "/bgp_prefix_policy": _methods("GET", "POST", "DELETE"),
    "/bgp_import_check": _methods("PUT"),
    "/bgp_reflector_switch": _methods("PUT"),
    "/bgp_timer": _methods("PUT"),
    "/bgp_route_reflector": _methods("GET", "POST", "DELETE"),
    "/app_obj": _methods("GET", "POST", "PUT", "DELETE"),
    "/app_group": _methods("GET", "POST", "DELETE"),
    "/getAppList": _methods("GET"),
    "/getAppDetail": _methods("GET"),
    "/user": _methods("GET", "POST", "DELETE"),
    "/user_group": _methods("GET", "POST", "PUT", "DELETE"),
    "/user_obj": _methods("GET"),
    "/radius": _methods("GET", "POST", "PUT", "DELETE"),
    "/ldap": _methods("GET", "POST", "DELETE"),
    "/black_list": _methods("GET", "POST", "DELETE"),
    "/white_list": _methods("GET", "POST", "DELETE"),
    "/blackList_group": _methods("GET", "POST", "DELETE"),
    "/blackListGroup_rename": _methods("PUT"),
    "/domainBlackList": _methods("GET"),
    "/domain_blacklist_export": _methods("GET"),
    "/multiple_ids": _methods("POST", "DELETE"),
    "/multiple_domains": _methods("POST", "DELETE"),
    "/protect_policy": _methods("GET", "POST", "DELETE"),
    "/protect_policy_enable": _methods("PUT"),
    "/vsys": _methods("POST", "PUT", "DELETE"),
    "/xml_av_profile": _methods("GET", "POST", "PUT", "DELETE"),
    "/signature_set": _methods("GET", "POST", "PUT", "DELETE"),
    "/cpu_state": _methods("GET"),
    "/memory_state": _methods("GET"),
    "/device_state": _methods("GET"),
    "/device_link_state": _methods("GET"),
    "/interface_flow_state": _methods("GET"),
    "/interface_flow_bar_state": _methods("GET"),
    "/user_flow_state": _methods("GET"),
    "/user_flow_bar_state": _methods("GET"),
    "/monitor_user": _methods("GET"),
    "/app_flow_state": _methods("GET"),
    "/app_flow_bar_state": _methods("GET"),
    "/url_state": _methods("GET"),
    "/url_bar_state": _methods("GET"),
    "/threaten_state": _methods("GET"),
    "/threaten_bar_state": _methods("GET"),
    "/interface_monitor": _methods("GET"),
    "/vxlan_monitor": _methods("GET"),
    "/lte_config": _methods("GET"),
    "/loopback": _methods("GET"),
    "/ha_config": _methods("GET"),
    "/ha_config_syn": _methods("GET"),
    "/ha_status_all": _methods("GET"),
    "/lte_info": _methods("GET"),
    "/ntp_config": _methods("GET"),
    "/v0.0.1/ntp_config": _methods("GET"),
    "/ntp_key": _methods("GET"),
    "/syslog_server": _methods("GET"),
    "/v0.0.1/syslog_server": _methods("GET", "POST", "DELETE"),
    "/logFilter": _methods("GET"),
    "/fw_policy_config": _methods("GET"),
    "/license_config": _methods("GET"),
    "/virtual_route_list": _methods("GET"),
    "/diagnose": _methods("GET"),
}

BLOCKED_HIGH_RISK_MUTATIONS: dict[str, set[str]] = {
    "/save_config": {"GET", "POST", "PUT", "DELETE"},
    "/change_password": {"GET", "POST", "PUT", "DELETE"},
    "/config_clear_common": {"GET", "POST", "PUT", "DELETE"},
    "/config_clear_interface": {"GET", "POST", "PUT", "DELETE"},
    "/restart": {"GET", "POST", "PUT", "DELETE"},
    "/restore": {"GET", "POST", "PUT", "DELETE"},
    "/library_upgrade": {"GET", "POST", "PUT", "DELETE"},
    "/software_update_now": {"GET", "POST", "PUT", "DELETE"},
    "/software_update_ontime": {"POST", "PUT", "DELETE"},
    "/system_upgrade": {"GET", "POST", "PUT", "DELETE"},
    "/license_config": {"POST", "PUT", "DELETE"},
    "/ha_config": {"POST", "PUT", "DELETE"},
    "/fw_policy_config": {"POST", "PUT", "DELETE"},
    "/global_domain_block_switch": {"PUT", "POST", "DELETE"},
    "/clearBalckDomainBingo": {"GET", "POST", "PUT", "DELETE"},
    "/domain_blacklist_import": {"GET", "POST", "PUT", "DELETE"},
    "/session_monitor": {"DELETE"},
    "/ispList": {"GET", "POST", "PUT", "DELETE"},
    "/isp_restore": {"GET", "POST", "PUT", "DELETE"},
    "/policy_group_move": {"GET", "POST", "PUT", "DELETE"},
    "/nat_rule_src_move": {"GET", "POST", "PUT", "DELETE"},
    "/nat_rule_dst_move": {"GET", "POST", "PUT", "DELETE"},
    "/nat_rule_static_move": {"GET", "POST", "PUT", "DELETE"},
    "/policy_route_move": {"GET", "POST", "PUT", "DELETE"},
    "/policy_route_state": {"GET", "POST", "PUT", "DELETE"},
    "/qos_policy_move": {"GET", "POST", "PUT", "DELETE"},
    "/sdwan_policy_move": {"GET", "POST", "PUT", "DELETE"},
    "/bgp_clear_bgp_route": {"GET", "POST", "PUT", "DELETE"},
    "/user_obj": {"POST", "PUT", "DELETE"},
    "/signature_event": {"POST", "PUT", "DELETE"},
}

KNOWN_PROBLEM_RESOURCES: dict[str, dict[str, dict[str, Any]]] = {
    "/domainBlackList": {"GET": {"http_status": 404, "code": 404, "message": "404 Not Found"}},
    "/global_domain_block_switch": {"GET": {"http_status": 404, "code": 404, "message": "404 Not Found"}},
    "/domain_blacklist_export": {"GET": {"http_status": 404, "code": 404, "message": "404 Not Found"}},
    "/radius": {"PUT": {"http_status": 400, "code": 103, "message": "输入的内容长度超过限制"}},
    "/multiple_ids": {"POST": {"http_status": 400, "code": 1111, "message": "不支持的csp联动协议"}},
    "/protect_policy": {"POST": {"http_status": 400, "code": 121, "message": "策略数量已达到最大限制"}},
    "/protect_policy_enable": {"PUT": {"http_status": 400, "code": 87, "message": "目标策略不存在"}},
    "/vsys": {
        "POST": {"http_status": 400, "code": 1087, "message": "虚拟路由器或虚拟系统不存在"},
        "PUT": {"http_status": 400, "code": 1087, "message": "虚拟路由器或虚拟系统不存在"},
    },
    "/bgp_route_reflector": {"POST": {"http_status": 400, "code": 484, "message": "对等体标志设置错误"}},
}


def _resolve_ref(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return get_secret_manager().get(value[len("{secret:") : -1]) or ""
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[len("{env:") : -1], "")
    return value


def _raw_service_config() -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    if not isinstance(raw, dict):
        raw = ConfigWriter.get_api_service_raw(STORAGE_KEY)
    return raw if isinstance(raw, dict) else {}


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
    return bool(value)


def _config_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if raw.get(key) is not None:
            return raw[key]
    custom_settings = raw.get("custom_settings")
    if isinstance(custom_settings, dict):
        for key in keys:
            if custom_settings.get(key) is not None:
                return custom_settings[key]
    return None


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    value = _config_value(raw, "verify_ssl", "ssl_verify")
    if value is None:
        value = os.getenv("FW_VERIFY_SSL")
    return _as_bool(value, False)


def _normalize_base_url(value: str) -> str:
    base_url = value.rstrip("/")
    if not base_url:
        return ""
    if not base_url.endswith("/API"):
        base_url = base_url + "/API"
    return base_url


def _load_runtime_config() -> RuntimeConfig:
    raw = _raw_service_config()
    sm = get_secret_manager()

    base_url = _normalize_base_url(
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or os.getenv("FW_BASE_URL", "")
    )
    username = (
        _resolve_ref(raw.get("username"))
        or sm.get("360_fw_v5_5_username")
        or sm.get("360_fw_username")
        or os.getenv("FW_USERNAME", "")
        or os.getenv("FW_USER", "")
    )
    password = (
        _resolve_ref(raw.get("password"))
        or sm.get("360_fw_v5_5_password")
        or sm.get("360_fw_password")
        or os.getenv("FW_PASSWORD", "")
        or os.getenv("FW_PASS", "")
    )
    timeout_value = raw.get("timeout") or os.getenv("FW_TIMEOUT") or 30
    try:
        timeout = int(timeout_value)
    except (TypeError, ValueError):
        timeout = 30

    if not base_url:
        raise FwApiError("360 FW base_url is required")
    if not username:
        raise FwApiError("360 FW username is required")
    if not password:
        raise FwApiError("360 FW password is required")

    return RuntimeConfig(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=_resolve_verify_ssl(raw),
        timeout=timeout,
    )


class FwClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.base_url = config.base_url
        self.username = config.username
        self.password = config.password
        self.verify_ssl = config.verify_ssl
        self.timeout = config.timeout
        self.session = requests.Session()
        self.session.verify = self.verify_ssl
        self.authorization: Optional[str] = None

    def login(self) -> dict[str, Any]:
        resp = self.session.request(
            "POST",
            f"{self.base_url}/login",
            json={"user": self.username, "pwd": self.password},
            timeout=self.timeout,
        )
        data = self._parse_response(resp, "POST", "/login")
        token = data.get("authorization") if isinstance(data, dict) else None
        if resp.status_code != 200 or data.get("result") is not True or not token:
            raise FwApiError(f"login failed: HTTP {resp.status_code}, {self._short(data)}")
        self.authorization = str(token)
        self.session.headers.update({"Authorization": self.authorization, "Content-Type": "application/json"})
        return self._redact_auth(data)

    def get(
        self,
        path: str,
        query: Optional[dict[str, Any]] = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        if not self.authorization:
            self.login()
        return self._request_with_retry("GET", path, query=query, body=None, retry=retry)

    def request(
        self,
        method: str,
        path: str,
        query: Optional[dict[str, Any]] = None,
        body: Optional[Any] = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        method = method.upper()
        if method == "GET":
            return self.get(path, query=query, retry=retry)
        if method not in {"POST", "PUT", "DELETE"}:
            raise FwApiError("method must be GET, POST, PUT, or DELETE")
        if not self.authorization:
            self.login()
        return self._request_with_retry(method, path, query=query, body=body, retry=retry)

    def _request_with_retry(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]],
        body: Optional[Any],
        retry: bool,
    ) -> dict[str, Any]:
        try:
            data = self._raw_request(method, path, query=query, body=body)
        except (requests.RequestException, FwApiError):
            if not retry:
                raise
            self.login()
            data = self._raw_request(method, path, query=query, body=body)
        if self._is_auth_error(data) and retry:
            self.login()
            data = self._raw_request(method, path, query=query, body=body)
        return data

    def _raw_request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[dict[str, Any]],
        body: Optional[Any],
    ) -> dict[str, Any]:
        request_path = build_path(path, query)
        resp = self.session.request(
            method,
            f"{self.base_url}{request_path}",
            json=body,
            timeout=self.timeout,
        )
        data = self._parse_response(resp, method, request_path)
        if resp.status_code < 200 or resp.status_code >= 300:
            raise FwApiError(f"HTTP {resp.status_code} from {method} {request_path}: {self._short(data)}")
        return data

    @staticmethod
    def _parse_response(resp: requests.Response, method: str, path: str) -> dict[str, Any]:
        text = resp.text or ""
        if not text.strip():
            return {"result": True, "data": None}
        try:
            data = resp.json()
        except Exception as exc:
            raise FwApiError(f"non-json response from {method} {path}: HTTP {resp.status_code}, {text[:200]}") from exc
        if not isinstance(data, dict):
            return {"result": True, "data": data}
        return data

    @staticmethod
    def _is_auth_error(data: dict[str, Any]) -> bool:
        code = data.get("code")
        msg = str(data.get("message") or data.get("msg") or data.get("error") or "").lower()
        return code in {401, "401"} or "authorization" in msg or "token" in msg

    @staticmethod
    def _redact_auth(data: dict[str, Any]) -> dict[str, Any]:
        output = dict(data)
        if "authorization" in output:
            output["authorization"] = "***"
        return output

    @staticmethod
    def _short(data: Any) -> str:
        return json.dumps(data, ensure_ascii=False)[:300]


_CLIENTS: dict[tuple[str, str, bool], FwClient] = {}


def _client_cache_key(config: RuntimeConfig) -> tuple[str, str, bool]:
    return (config.base_url, config.username, config.verify_ssl)


def get_client() -> FwClient:
    config = _load_runtime_config()
    key = _client_cache_key(config)
    client = _CLIENTS.get(key)
    if client is None or client.password != config.password:
        client = FwClient(config)
        _CLIENTS[key] = client
    return client


def ok(content: Any) -> ToolResult:
    return ToolResult(
        success=True,
        output=content,
        metadata={
            "source": "360 FW",
            "version": PRODUCT_VERSION,
            "fw_software_version": FW_SOFTWARE_VERSION,
            "version_software": FW_BUILD_VERSION,
        },
    )


def api_result(data: dict[str, Any]) -> ToolResult:
    if data.get("result") is False:
        raise FwApiError(error_text(data))
    code = data.get("code")
    if code not in (None, 0, "0"):
        raise FwApiError(error_text(data))
    return ok(data)


def error_text(data: dict[str, Any]) -> str:
    code = data.get("code")
    msg = data.get("message") or data.get("msg") or data.get("error")
    if code is not None or msg:
        return f"code={code} message={msg}"
    return json.dumps(data, ensure_ascii=False)[:300]


def require_int(value: Any, name: str, default: Optional[int] = None) -> int:
    if value in (None, "") and default is not None:
        return default
    try:
        return int(value)
    except Exception as exc:
        raise FwApiError(f"{name} must be an integer") from exc


def require_text(value: Any, name: str, default: Optional[str] = None) -> str:
    if value in (None, "") and default is not None:
        return default
    if value in (None, ""):
        raise FwApiError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise FwApiError(f"{name} is required")
    return text


def first_present(args: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = args.get(name)
        if value not in (None, ""):
            return value
    return None


def require_payload(value: Any, name: str = "body") -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise FwApiError(f"{name} must be valid JSON") from exc
    if not isinstance(value, (dict, list)):
        raise FwApiError(f"{name} must be a JSON object or array")
    return value


def optional_payload(value: Any, name: str = "body") -> Any:
    if value in (None, ""):
        return None
    return require_payload(value, name)


def payload_or(args: dict[str, Any], builder: ActionBuilder) -> Any:
    payload = optional_payload(args.get("body"))
    if payload is not None:
        return payload
    return builder(args)


def name_body(args: dict[str, Any], key: str = "name") -> dict[str, Any]:
    return {key: require_text(args.get(key) or args.get("name"), key)}


def address_prefix(value: str, obj_type: int) -> str:
    if value[:2] in {"0:", "1:", "8:"}:
        return value
    return f"{obj_type}:{value}"


def build_addressobj_body(args: dict[str, Any]) -> dict[str, Any]:
    obj_type = require_int(args.get("type", 0), "type")
    addr = require_text(args.get("addr"), "addr")
    return {
        "name": require_text(args.get("name"), "name"),
        "type": obj_type,
        "desc": str(args.get("desc") or ""),
        "item": [{"addr": address_prefix(addr, obj_type)}],
    }


def build_serviceobj_body(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": require_text(args.get("name"), "name"),
        "desc": str(args.get("desc") or ""),
        "item": [{"sev_str": require_text(args.get("sev_str"), "sev_str")}],
    }


def build_static_route_body(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "ip_vrf_name": str(args.get("ip_vrf_name") or "default"),
        "dst_ip": require_text(args.get("dst_ip"), "dst_ip"),
        "nh_type": str(args.get("nh_type") or "0"),
        "nh_ip": require_text(args.get("nh_ip"), "nh_ip"),
        "oif": str(args.get("oif") or ""),
        "weigh": str(args.get("weigh") or "1"),
        "distance": str(args.get("distance") or "255"),
        "monitor_name": str(args.get("monitor_name") or ""),
    }


def build_policy_group_body(args: dict[str, Any]) -> dict[str, Any]:
    return {"name": require_text(args.get("name"), "name"), "protocol": str(args.get("protocol") or "1")}


def build_fwpolicy_state_body(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "enable": require_int(args.get("enable", 0), "enable"),
        "id": require_int(args.get("id"), "id"),
        "protocol": require_int(args.get("protocol", 1), "protocol"),
    }


def build_delete_name_body(args: dict[str, Any]) -> dict[str, Any]:
    body = name_body(args)
    if args.get("type") not in (None, ""):
        body["type"] = require_int(args["type"], "type")
    if args.get("protocol") not in (None, ""):
        body["protocol"] = require_int(args["protocol"], "protocol")
    return body


def build_path(path: str, query: Optional[dict[str, Any]] = None) -> str:
    path = normalize_api_path(path)
    if query:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}{urllib.parse.urlencode(query, doseq=True)}"
    return path


def normalize_api_path(path: str) -> str:
    path = str(path or "").strip()
    if path.startswith("API/"):
        path = "/" + path
    if path.startswith("/API/"):
        path = path[len("/API") :]
    if path.startswith("API?"):
        path = "/" + path
    if not path.startswith("/"):
        path = "/" + path
    if path == "/API":
        path = "/"
    if not path or path == "/":
        raise FwApiError("path is required")
    return path


def resource_of(path: str) -> str:
    return normalize_api_path(path).split("?", 1)[0]


def reject_high_risk_mutation(method: str, path: str) -> None:
    resource = resource_of(path)
    methods = BLOCKED_HIGH_RISK_MUTATIONS.get(resource)
    if methods and method.upper() in methods:
        raise FwApiError(f"360 FW integration does not support high-risk FW operations: {method.upper()} {resource}")


def validate_documented_api(method: str, path: str) -> None:
    resource = resource_of(path)
    methods = DOCUMENTED_API_METHODS.get(resource)
    if methods is None:
        raise FwApiError(f"{resource} is not listed in the local FW API document")
    if method.upper() not in methods:
        raise FwApiError(f"{method.upper()} {resource} is not listed in the local FW API document")


def query_from_args(args: dict[str, Any], allowed: list[str]) -> dict[str, Any] | None:
    query = args.get("query")
    if query is not None:
        if not isinstance(query, dict):
            raise FwApiError("query must be an object")
        return query
    output = {key: args[key] for key in allowed if args.get(key) not in (None, "")}
    return output or None


GET_ACTIONS: dict[str, str] = {
    "fw_system_info_get": "/sys_info",
    "fw_interface_list": "/interface",
    "fw_interface_get": "/interface",
    "fw_ha_config_get": "/ha_config",
    "fw_ha_config_syn_get": "/ha_config_syn",
    "fw_ha_status_all_get": "/ha_status_all",
    "fw_lte_config_get": "/lte_config",
    "fw_lte_info_get": "/lte_info",
    "fw_loopback_list": "/loopback",
    "fw_ntp_config_get": "/ntp_config",
    "fw_v0_0_1_ntp_config_get": "/v0.0.1/ntp_config",
    "fw_ntp_key_get": "/ntp_key",
    "fw_syslog_server_get": "/syslog_server",
    "fw_v0_0_1_syslog_server_get": "/v0.0.1/syslog_server",
    "fw_log_filter_get": "/logFilter",
    "fw_fw_policy_config_get": "/fw_policy_config",
    "fw_license_config_get": "/license_config",
    "fw_virtual_route_list": "/virtual_route_list",
    "fw_diagnose_get": "/diagnose",
    "fw_addressobj_list": "/addressobj?page=1&length=100&flag=0",
    "fw_addressobj_get": "/addressobj",
    "fw_addressgroup_list": "/addressgroup?page=1&length=100&flag=0",
    "fw_addressgroup_get": "/addressgroup",
    "fw_serviceobj_list": "/serviceobj?page=1&length=100&flag=0",
    "fw_serviceobj_get": "/serviceobj",
    "fw_servicegroup_list": "/servicegroup?page=1&length=100&flag=0",
    "fw_servicegroup_get": "/servicegroup",
    "fw_predefined_service_list": "/predefined_service",
    "fw_dom_obj_list": "/dom_obj",
    "fw_dns_custom_list": "/dns_custom",
    "fw_dns_custom_get": "/dns_custom",
    "fw_dns_group_list": "/dns_group",
    "fw_dns_group_get": "/dns_group",
    "fw_timeabsobj_list": "/timeabsobj?flag=0",
    "fw_timeabsobj_get": "/timeabsobj",
    "fw_timecycobj_list": "/timecycobj?flag=0",
    "fw_timecycobj_get": "/timecycobj",
    "fw_app_obj_list": "/app_obj",
    "fw_app_obj_get": "/app_obj",
    "fw_app_group_list": "/app_group",
    "fw_app_group_get": "/app_group",
    "fw_get_app_list": "/getAppList",
    "fw_get_app_detail": "/getAppList",
    "fw_blackList_group_list": "/blackList_group",
    "fw_xml_av_profile_list": "/xml_av_profile",
    "fw_signature_set_list": "/signature_set",
    "fw_fwpolicy_list": "/fwpolicy?protocol=1&page=1&length=100&flag=0",
    "fw_fwpolicy_get": "/fwpolicy",
    "fw_policy_group_list": "/policy_group?protocol=1",
    "fw_app_policy_list": "/app_policy",
    "fw_web_policy_list": "/web_policy",
    "fw_black_list_list": "/black_list?page=1",
    "fw_white_list_list": "/white_list?page=1",
    "fw_protect_policy_list": "/protect_policy?protocol=1",
    "fw_vlan_list": "/vlan",
    "fw_vxlan_list": "/vxlan",
    "fw_static_route_list": "/static_route?protocol=1&ip_vrf_name=default",
    "fw_healthcheck_list": "/healthcheck_list",
    "fw_link_health_check_list": "/link_health_check",
    "fw_qos_line_list": "/qos_line",
    "fw_qos_policy_list": "/qos_policy",
    "fw_nat_pool_list": "/nat_pool",
    "fw_nat_rule_src_list": "/nat_rule_src?protocol=1",
    "fw_nat_rule_dst_list": "/nat_rule_dst?protocol=1",
    "fw_nat_rule_static_list": "/nat_rule_static?protocol=1",
    "fw_policy_route_list": "/policy_route?protocol_type=1",
    "fw_sdwan_policy_list": "/sdwan_policy",
    "fw_sdwan_status_get": "/sdwan_status",
    "fw_woc_policy_state_get": "/woc_policy_state",
    "fw_gre_list": "/gre?ip_vrf_name=default",
    "fw_autoike_list": "/autoike",
    "fw_ipsec_policy_list": "/ipsec_policy",
    "fw_ikesa_list": "/ikesa",
    "fw_ipsecsa_list": "/ipsecsa",
    "fw_tunnel_status_table": "/tunnel_status_table?page=1&length=10",
    "fw_tunnel_status_line": "/tunnel_status_line?status_type=1&period=1&page=1&length=10",
    "fw_bgp_info_get": "/bgp_info?ip_vrf_name=default",
    "fw_bgp_network_list": "/bgp_network?ip_vrf_name=default",
    "fw_bgp_peer_group_list": "/bgp_peer_group",
    "fw_bgp_neighbors_list": "/bgp_neighbors?ip_vrf_name=default",
    "fw_bgp_access_list_list": "/bgp_access_list",
    "fw_bgp_filter_list_list": "/bgp_filter_list",
    "fw_bgp_route_map_list": "/bgp_route_map",
    "fw_bgp_map_list_list": "/bgp_map_list",
    "fw_bgp_prefix_list_list": "/bgp_prefix_list",
    "fw_bgp_prefix_policy_list": "/bgp_prefix_policy",
    "fw_user_list": "/user?page=1&length=100",
    "fw_user_group_list": "/user_group?page=1&length=100",
    "fw_user_obj_list": "/user_obj?page=1&length=100",
    "fw_radius_list": "/radius",
    "fw_ldap_list": "/ldap",
    "fw_cpu_state": "/cpu_state?type=1&period=1",
    "fw_memory_state": "/memory_state?type=2&period=1",
    "fw_device_state": "/device_state?type=4&period=1",
    "fw_device_link_state": "/device_link_state?period=1",
    "fw_interface_flow_state": "/interface_flow_state?period=1&flow=3&inf_type=1",
    "fw_interface_flow_bar_state": "/interface_flow_bar_state?period=1&flow=3&inf_type=1",
    "fw_user_flow_state": "/user_flow_state?period=1&flow=3",
    "fw_user_flow_bar_state": "/user_flow_bar_state?period=1&flow=3",
    "fw_monitor_user": "/monitor_user?period=1&user_type=2",
    "fw_app_flow_state": "/app_flow_state?period=1&flow=3&stat_type=1",
    "fw_app_flow_bar_state": "/app_flow_bar_state?period=1&flow=3&stat_type=1",
    "fw_url_state": "/url_state?period=1&stat_type=1",
    "fw_url_bar_state": "/url_bar_state?period=1&stat_type=1",
    "fw_threaten_state": "/threaten_state?period=1&stat_type=1",
    "fw_threaten_bar_state": "/threaten_bar_state?period=1&stat_type=1",
    "fw_get_app_detail_monitor": "/getAppDetail?period=1&stat_type=2",
    "fw_interface_monitor": "/interface_monitor",
    "fw_interface_monitor_vlan": "/interface_monitor?inf_type=2",
    "fw_qos_monitor": "/monitor_qos_policy",
    "fw_vxlan_monitor": "/vxlan_monitor?period=1",
}

ACTION_SPECS: dict[str, ActionSpec] = {
    "fw_addressobj_create": ("POST", "/addressobj", lambda a: payload_or(a, build_addressobj_body)),
    "fw_addressobj_update": ("PUT", "/addressobj", lambda a: payload_or(a, build_addressobj_body)),
    "fw_addressobj_delete": ("DELETE", "/addressobj", lambda a: payload_or(a, lambda x: {"name": require_text(x.get("name"), "name"), "type": require_int(x.get("type", 0), "type")})),
    "fw_serviceobj_create": ("POST", "/serviceobj", lambda a: payload_or(a, build_serviceobj_body)),
    "fw_serviceobj_update": ("PUT", "/serviceobj", lambda a: payload_or(a, build_serviceobj_body)),
    "fw_serviceobj_delete": ("DELETE", "/serviceobj", lambda a: payload_or(a, build_delete_name_body)),
    "fw_policy_group_create": ("POST", "/policy_group", lambda a: payload_or(a, build_policy_group_body)),
    "fw_policy_group_delete": ("DELETE", "/policy_group", lambda a: payload_or(a, lambda x: {"name": require_text(x.get("name"), "name"), "protocol": str(x.get("protocol") or "1"), "del_act": str(x.get("del_act") or "0")})),
    "fw_fwpolicy_state_update": ("PUT", "/fwpolicy_state", lambda a: payload_or(a, build_fwpolicy_state_body)),
    "fw_static_route_create": ("POST", "/static_route?protocol=1", lambda a: payload_or(a, build_static_route_body)),
    "fw_static_route_delete": ("DELETE", "/static_route?protocol=1", lambda a: payload_or(a, build_static_route_body)),
}


def _add_raw_specs(actions: dict[str, tuple[str, str]]) -> None:
    for action, (method, path) in actions.items():
        ACTION_SPECS.setdefault(action, (method, path, lambda a: require_payload(a.get("body"))))


_add_raw_specs(
    {
        "fw_addressgroup_create": ("POST", "/addressgroup"),
        "fw_addressgroup_update": ("PUT", "/addressgroup"),
        "fw_addressgroup_delete": ("DELETE", "/addressgroup"),
        "fw_servicegroup_create": ("POST", "/servicegroup"),
        "fw_servicegroup_update": ("PUT", "/servicegroup"),
        "fw_servicegroup_delete": ("DELETE", "/servicegroup"),
        "fw_dom_obj_create": ("POST", "/dom_obj"),
        "fw_dom_obj_delete": ("DELETE", "/dom_obj"),
        "fw_dns_custom_create": ("POST", "/dns_custom"),
        "fw_dns_custom_update": ("PUT", "/dns_custom"),
        "fw_dns_custom_delete": ("DELETE", "/dns_custom"),
        "fw_dns_group_create": ("POST", "/dns_group"),
        "fw_dns_group_update": ("PUT", "/dns_group"),
        "fw_dns_group_delete": ("DELETE", "/dns_group"),
        "fw_timeabsobj_create": ("POST", "/timeabsobj"),
        "fw_timeabsobj_delete": ("DELETE", "/timeabsobj"),
        "fw_timecycobj_create": ("POST", "/timecycobj"),
        "fw_timecycobj_update": ("PUT", "/timecycobj"),
        "fw_timecycobj_delete": ("DELETE", "/timecycobj"),
        "fw_app_obj_create": ("POST", "/app_obj"),
        "fw_app_obj_update": ("PUT", "/app_obj"),
        "fw_app_obj_delete": ("DELETE", "/app_obj"),
        "fw_app_group_create": ("POST", "/app_group"),
        "fw_app_group_delete": ("DELETE", "/app_group"),
        "fw_blackList_group_create": ("POST", "/blackList_group"),
        "fw_blackList_group_delete": ("DELETE", "/blackList_group"),
        "fw_blackListGroup_rename": ("PUT", "/blackListGroup_rename"),
        "fw_xml_av_profile_create": ("POST", "/xml_av_profile"),
        "fw_xml_av_profile_update": ("PUT", "/xml_av_profile"),
        "fw_xml_av_profile_delete": ("DELETE", "/xml_av_profile"),
        "fw_signature_set_create": ("POST", "/signature_set"),
        "fw_signature_set_update": ("PUT", "/signature_set"),
        "fw_signature_set_delete": ("DELETE", "/signature_set"),
        "fw_fwpolicy_create": ("POST", "/fwpolicy"),
        "fw_fwpolicy_update": ("PUT", "/fwpolicy"),
        "fw_fwpolicy_delete": ("DELETE", "/fwpolicy"),
        "fw_fwpolicy_move": ("PUT", "/fwpolicy_move"),
        "fw_app_policy_create": ("POST", "/app_policy"),
        "fw_app_policy_delete": ("DELETE", "/app_policy"),
        "fw_web_policy_create": ("POST", "/web_policy"),
        "fw_web_policy_delete": ("DELETE", "/web_policy"),
        "fw_black_list_create": ("POST", "/black_list"),
        "fw_black_list_delete": ("DELETE", "/black_list"),
        "fw_white_list_create": ("POST", "/white_list"),
        "fw_white_list_delete": ("DELETE", "/white_list"),
        "fw_multiple_domains_create": ("POST", "/multiple_domains"),
        "fw_multiple_domains_delete": ("DELETE", "/multiple_domains"),
        "fw_multiple_ids_create": ("POST", "/multiple_ids"),
        "fw_multiple_ids_delete": ("DELETE", "/multiple_ids"),
        "fw_protect_policy_create": ("POST", "/protect_policy"),
        "fw_protect_policy_delete": ("DELETE", "/protect_policy"),
        "fw_protect_policy_enable_update": ("PUT", "/protect_policy_enable"),
        "fw_vsys_create": ("POST", "/vsys"),
        "fw_vsys_update": ("PUT", "/vsys"),
        "fw_vsys_delete": ("DELETE", "/vsys"),
        "fw_vlan_create": ("POST", "/vlan"),
        "fw_vlan_update": ("PUT", "/vlan"),
        "fw_vlan_delete": ("DELETE", "/vlan"),
        "fw_healthcheck_create": ("POST", "/healthcheck_list"),
        "fw_healthcheck_update": ("PUT", "/healthcheck_list"),
        "fw_healthcheck_delete": ("DELETE", "/healthcheck_list"),
        "fw_link_health_check_create": ("POST", "/link_health_check"),
        "fw_link_health_check_update": ("PUT", "/link_health_check"),
        "fw_link_health_check_delete": ("DELETE", "/link_health_check"),
        "fw_qos_line_create": ("POST", "/qos_line"),
        "fw_qos_line_update": ("PUT", "/qos_line"),
        "fw_qos_line_delete": ("DELETE", "/qos_line"),
        "fw_qos_policy_create": ("POST", "/qos_policy"),
        "fw_qos_policy_delete": ("DELETE", "/qos_policy"),
        "fw_nat_pool_create": ("POST", "/nat_pool"),
        "fw_nat_pool_delete": ("DELETE", "/nat_pool"),
        "fw_policy_route_create": ("POST", "/policy_route"),
        "fw_policy_route_delete": ("DELETE", "/policy_route"),
        "fw_sdwan_policy_create": ("POST", "/sdwan_policy"),
        "fw_sdwan_policy_delete": ("DELETE", "/sdwan_policy"),
        "fw_gre_create": ("POST", "/gre"),
        "fw_gre_update": ("PUT", "/gre"),
        "fw_gre_delete": ("DELETE", "/gre"),
        "fw_tunnel_monitor_create": ("POST", "/tunnel_monitor"),
        "fw_tunnel_monitor_delete": ("DELETE", "/tunnel_monitor"),
        "fw_autoike_create": ("POST", "/autoike"),
        "fw_autoike_delete": ("DELETE", "/autoike"),
        "fw_phase2ike_create": ("POST", "/phase2ike"),
        "fw_phase2ike_delete": ("DELETE", "/phase2ike"),
        "fw_ipsec_policy_create": ("POST", "/ipsec_policy"),
        "fw_ipsec_policy_delete": ("DELETE", "/ipsec_policy"),
        "fw_bgp_info_create": ("POST", "/bgp_info"),
        "fw_bgp_info_delete": ("DELETE", "/bgp_info"),
        "fw_bgp_network_create": ("POST", "/bgp_network"),
        "fw_bgp_network_delete": ("DELETE", "/bgp_network"),
        "fw_bgp_peer_group_create": ("POST", "/bgp_peer_group"),
        "fw_bgp_peer_group_delete": ("DELETE", "/bgp_peer_group"),
        "fw_bgp_neighbors_create": ("POST", "/bgp_neighbors"),
        "fw_bgp_neighbors_delete": ("DELETE", "/bgp_neighbors"),
        "fw_bgp_access_list_create": ("POST", "/bgp_access_list"),
        "fw_bgp_access_list_delete": ("DELETE", "/bgp_access_list"),
        "fw_bgp_filter_list_create": ("POST", "/bgp_filter_list"),
        "fw_bgp_filter_list_delete": ("DELETE", "/bgp_filter_list"),
        "fw_bgp_route_map_create": ("POST", "/bgp_route_map"),
        "fw_bgp_route_map_delete": ("DELETE", "/bgp_route_map"),
        "fw_bgp_map_list_create": ("POST", "/bgp_map_list"),
        "fw_bgp_map_list_delete": ("DELETE", "/bgp_map_list"),
        "fw_bgp_prefix_list_create": ("POST", "/bgp_prefix_list"),
        "fw_bgp_prefix_list_delete": ("DELETE", "/bgp_prefix_list"),
        "fw_bgp_prefix_policy_create": ("POST", "/bgp_prefix_policy"),
        "fw_bgp_prefix_policy_delete": ("DELETE", "/bgp_prefix_policy"),
        "fw_bgp_import_check_update": ("PUT", "/bgp_import_check"),
        "fw_bgp_reflector_switch_update": ("PUT", "/bgp_reflector_switch"),
        "fw_bgp_timer_update": ("PUT", "/bgp_timer"),
        "fw_bgp_route_reflector_create": ("POST", "/bgp_route_reflector"),
        "fw_bgp_route_reflector_delete": ("DELETE", "/bgp_route_reflector"),
        "fw_user_create": ("POST", "/user"),
        "fw_user_delete": ("DELETE", "/user"),
        "fw_user_group_create": ("POST", "/user_group"),
        "fw_user_group_update": ("PUT", "/user_group"),
        "fw_user_group_delete": ("DELETE", "/user_group"),
        "fw_radius_create": ("POST", "/radius"),
        "fw_radius_update": ("PUT", "/radius"),
        "fw_radius_delete": ("DELETE", "/radius"),
        "fw_ldap_create": ("POST", "/ldap"),
        "fw_ldap_delete": ("DELETE", "/ldap"),
        "fw_v0_0_1_syslog_server_create": ("POST", "/v0.0.1/syslog_server"),
        "fw_v0_0_1_syslog_server_delete": ("DELETE", "/v0.0.1/syslog_server"),
    }
)

GROUP_ACTIONS: dict[str, set[str]] = {
    "system": {
        "fw_check_login",
        *{k for k in GET_ACTIONS if k.startswith("fw_") and any(token in k for token in ("system", "interface", "ha_", "lte_", "loopback", "ntp", "syslog", "log_filter", "policy_config", "license", "virtual_route", "diagnose"))},
    },
    "objects": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("address", "service", "predefined", "dom_", "dns_", "time", "app_", "get_app", "blackList", "xml", "signature"))},
        *{k for k in ACTION_SPECS if any(token in k for token in ("address", "service", "dom_", "dns_", "time", "app_", "blackList", "xml", "signature"))},
        "fw_object_call",
    },
    "policy": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("fwpolicy", "policy_group", "app_policy", "web_policy", "black_list", "white_list", "protect_policy"))},
        *{k for k in ACTION_SPECS if any(token in k for token in ("fwpolicy", "policy_group", "app_policy", "web_policy", "black_list", "white_list", "multiple_", "protect_policy", "vsys"))},
        "fw_policy_call",
    },
    "network": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("interface", "vlan", "vxlan", "static_route", "health", "qos", "nat_", "policy_route", "sdwan", "woc", "gre"))},
        *{k for k in ACTION_SPECS if any(token in k for token in ("vlan", "static_route", "health", "qos", "nat_", "policy_route", "sdwan", "gre", "tunnel_monitor"))},
        "fw_network_call",
    },
    "vpn_bgp": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("autoike", "ipsec", "ikesa", "tunnel_status", "bgp_"))},
        *{k for k in ACTION_SPECS if any(token in k for token in ("autoike", "phase2ike", "ipsec", "bgp_"))},
        "fw_vpn_bgp_call",
    },
    "auth_security": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("user", "radius", "ldap", "syslog"))},
        *{k for k in ACTION_SPECS if any(token in k for token in ("user", "radius", "ldap", "syslog", "multiple_"))},
        "fw_auth_security_call",
    },
    "observability": {
        *{k for k in GET_ACTIONS if any(token in k for token in ("cpu", "memory", "device_", "flow", "monitor", "url_", "threaten", "tunnel_status", "vxlan"))},
        "fw_observability_call",
    },
    "api_readonly": {"fw_api_catalog", "fw_call_raw_readonly"},
    "api_mutation": {"fw_call_mutation", "fw_call_api"},
}

CONNECTIVITY_TEST_ACTIONS = {
    "system": "fw_check_login",
    "objects": "fw_addressobj_list",
    "policy": "fw_fwpolicy_list",
    "network": "fw_interface_list",
    "vpn_bgp": "fw_autoike_list",
    "auth_security": "fw_user_group_list",
    "observability": "fw_cpu_state",
    "api_readonly": "fw_api_catalog",
}


def fw_check_login(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().login())


def fw_api_catalog(args: dict[str, Any]) -> ToolResult:
    return ok(
        {
            "documented_rest_api_resources": DOCUMENTED_API_METHODS,
            "blocked_high_risk_resources": BLOCKED_HIGH_RISK_MUTATIONS,
            "known_problem_resources": KNOWN_PROBLEM_RESOURCES,
            "covered_by": {
                "GET": "fw_call_raw_readonly or fw_call_api",
                "POST_PUT_DELETE": "fw_call_mutation or fw_call_api",
                "grouped_tools": sorted(GROUP_ACTIONS),
            },
            "version": {
                "product_version": PRODUCT_VERSION,
                "fw_software_version": FW_SOFTWARE_VERSION,
                "version_software": FW_BUILD_VERSION,
            },
        }
    )


def call_get_action(action: str, args: dict[str, Any]) -> ToolResult:
    path = GET_ACTIONS[action]
    if action == "fw_interface_get":
        query = query_from_args(args, ["name"])
    elif action.endswith("_get") or action.endswith("_list"):
        query = query_from_args(args, ["name", "id", "custom_name", "app_id", "ip_vrf_name"])
    else:
        query = query_from_args(args, ["type", "period", "flow", "inf_type", "stat_type", "status_type", "page", "length", "user_type"])
    return api_result(get_client().get(path, query=query))


def call_action_spec(action: str, args: dict[str, Any]) -> ToolResult:
    method, path, builder = ACTION_SPECS[action]
    reject_high_risk_mutation(method, path)
    validate_documented_api(method, path)
    body = builder(args) if builder is not None else optional_payload(args.get("body"))
    return api_result(get_client().request(method, path, query=query_from_args(args, []), body=body))


def fw_call_raw_readonly(args: dict[str, Any]) -> ToolResult:
    path = normalize_api_path(require_text(args.get("path"), "path"))
    validate_documented_api("GET", path)
    query = args.get("query")
    if query is not None and not isinstance(query, dict):
        raise FwApiError("query must be an object")
    return api_result(get_client().get(path, query=query))


def fw_call_mutation(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "")).upper()
    path = normalize_api_path(require_text(args.get("path"), "path"))
    if method not in {"POST", "PUT", "DELETE"}:
        raise FwApiError("method must be POST, PUT, or DELETE")
    reject_high_risk_mutation(method, path)
    validate_documented_api(method, path)
    query = args.get("query")
    if query is not None and not isinstance(query, dict):
        raise FwApiError("query must be an object")
    return api_result(get_client().request(method, path, query=query, body=optional_payload(args.get("body"))))


def fw_call_api(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "GET")).upper()
    path = normalize_api_path(require_text(args.get("path"), "path"))
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        raise FwApiError("method must be GET, POST, PUT, or DELETE")
    if method == "GET":
        return fw_call_raw_readonly({**args, "path": path})
    return fw_call_mutation({**args, "method": method, "path": path})


def grouped_raw_call(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "GET")).upper()
    if method == "GET":
        return fw_call_raw_readonly(args)
    return fw_call_mutation(args)


_ACTION_MAP: dict[str, Callable[[dict[str, Any]], ToolResult]] = {
    "fw_check_login": fw_check_login,
    "fw_api_catalog": fw_api_catalog,
    "fw_call_raw_readonly": fw_call_raw_readonly,
    "fw_call_mutation": fw_call_mutation,
    "fw_call_api": fw_call_api,
    "fw_object_call": grouped_raw_call,
    "fw_policy_call": grouped_raw_call,
    "fw_network_call": grouped_raw_call,
    "fw_vpn_bgp_call": grouped_raw_call,
    "fw_auth_security_call": grouped_raw_call,
    "fw_observability_call": grouped_raw_call,
}

for _action_name in GET_ACTIONS:
    _ACTION_MAP.setdefault(_action_name, lambda args, action=_action_name: call_get_action(action, args))
for _action_name in ACTION_SPECS:
    _ACTION_MAP.setdefault(_action_name, lambda args, action=_action_name: call_action_spec(action, args))


async def unified_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    handler = _ACTION_MAP.get(action)
    if handler is None:
        available = ", ".join(sorted(_ACTION_MAP))
        return ToolResult(success=False, error=f"Unknown action: {action}. Available: {available}")
    try:
        return await asyncio.to_thread(handler, params)
    except FwApiError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "360 FW", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected 360 FW error: {exc}",
            metadata={"source": "360 FW", "version": PRODUCT_VERSION, "action": action},
        )


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    if action == "test":
        test_action = CONNECTIVITY_TEST_ACTIONS.get(group)
        if test_action:
            return await unified_ops(ctx, action=test_action, **params)
        return ToolResult(success=False, error=f"360 FW group {group} does not define a test probe")
    if action not in GROUP_ACTIONS[group]:
        available = ", ".join(sorted(GROUP_ACTIONS[group]))
        return ToolResult(success=False, error=f"Unsupported {group} action: {action}. Available: {available}")
    return await unified_ops(ctx, action=action, **params)


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "system", action, **params)


async def objects(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "objects", action, **params)


async def policy(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "policy", action, **params)


async def network(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "network", action, **params)


async def vpn_bgp(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "vpn_bgp", action, **params)


async def auth_security(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "auth_security", action, **params)


async def observability(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "observability", action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "api_readonly", action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "api_mutation", action, **params)
