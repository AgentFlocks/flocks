from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import urllib.parse
import uuid
from http.client import HTTPConnection, HTTPSConnection, RemoteDisconnected
from pathlib import Path
from typing import Any, Optional

from flocks.config.config_writer import ConfigWriter
from flocks.security import get_secret_manager
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "360_waf"
STORAGE_KEY = "360_waf_v5_5"
PRODUCT_VERSION = "5.5"

BLOCKED_DEVICE_STATE_MUTATIONS: dict[str, set[str]] = {
    "/rest/api/reboot_system": {"POST"},
    "/rest/api/mgmt_image": {"POST", "DELETE"},
    "/rest/api/signature": {"POST", "PUT"},
    "/rest/api/configfile": {"POST", "DELETE"},
    "/rest/api/waf_deploy_mode": {"PUT"},
    "/rest/api/licenseManagementAgent": {"PUT"},
    "/rest/api/interface": {"POST", "PUT", "DELETE"},
    "/rest/api/zone": {"POST", "PUT", "DELETE"},
}

BLOCKED_FILE_MUTATIONS: dict[str, set[str]] = {
    "/rest/file/signature_import": {"POST"},
    "/rest/file/mgmt_import": {"POST"},
    "/rest/file/admind_image_upgrade": {"POST"},
    "/rest/file?fileName=tmp": {"DELETE"},
}

DOCUMENTED_API_METHODS: dict[str, list[str]] = {
    "/rest/api/login": ["DELETE", "GET", "POST"],
    "/rest/api/waf_attack_source_client_ip": ["GET"],
    "/rest/api/waf_attack_source_map": ["GET"],
    "/rest/api/waf_protection_type": ["GET"],
    "/rest/api/waf_site_attack": ["GET"],
    "/rest/api/website": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/wafacpolicy": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/blacklist": ["DELETE", "GET", "POST"],
    "/rest/api/exceptionlist": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/site_global_blacklist": ["DELETE", "GET", "POST"],
    "/rest/api/site_global_whitelist": ["DELETE", "GET", "POST"],
    "/rest/api/wafpolicy": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/whitelist": ["DELETE", "GET", "POST"],
    "/rest/api/configurationlog": ["GET", "POST"],
    "/rest/api/loggerconfiguration": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/websecuritylog": ["GET"],
    "/rest/api/ad": ["GET", "POST", "PUT"],
    "/rest/api/interface": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/zone": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/configfile": ["DELETE", "GET", "POST"],
    "/rest/api/licenseManagementAgent": ["GET", "PUT"],
    "/rest/api/mgmt_image": ["DELETE", "GET", "POST"],
    "/rest/api/sysinfo": ["GET"],
    "/rest/api/waf_custom_error_page": ["DELETE", "GET", "POST", "PUT"],
    "/rest/api/waf_deploy_mode": ["GET", "PUT"],
    "/rest/api/signature": ["GET", "POST", "PUT"],
    "/rest/api/capacity": ["GET"],
    "/rest/api/disk_usage": ["GET"],
    "/rest/api/reboot_system": ["POST"],
    "/rest/api/file_exists_on_device": ["GET"],
}

DOCUMENTED_FILE_ENDPOINTS: dict[str, list[str]] = {
    "/rest/file/signature_import": ["POST"],
    "/rest/file/mgmt_import": ["POST"],
    "/rest/file/wafd_error_page": ["POST"],
    "/rest/file/admind_image_upgrade": ["POST"],
    "/rest/file?fileName=tmp": ["DELETE"],
}


class WafApiError(RuntimeError):
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
        if key in raw and raw[key] is not None:
            return raw[key]
    custom_settings = raw.get("custom_settings")
    if isinstance(custom_settings, dict):
        for key in keys:
            if key in custom_settings and custom_settings[key] is not None:
                return custom_settings[key]
    return None


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    value = _config_value(raw, "verify_ssl", "ssl_verify")
    if value is None:
        value = os.getenv("WAF_VERIFY_SSL")
    return _as_bool(value, False)


def _load_runtime_config() -> RuntimeConfig:
    raw = _raw_service_config()
    sm = get_secret_manager()

    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or os.getenv("WAF_BASE_URL", "")
    ).rstrip("/")
    username = (
        _resolve_ref(raw.get("username"))
        or sm.get("360_waf_v5_5_username")
        or sm.get("360_waf_username")
        or os.getenv("WAF_USERNAME", "")
    )
    password = (
        _resolve_ref(raw.get("password"))
        or sm.get("360_waf_v5_5_password")
        or sm.get("360_waf_password")
        or os.getenv("WAF_PASSWORD", "")
    )
    timeout_value = raw.get("timeout") or os.getenv("WAF_TIMEOUT") or 30
    try:
        timeout = int(timeout_value)
    except (TypeError, ValueError):
        timeout = 30

    if not base_url:
        raise WafApiError("360 WAF base_url is required")
    if not username:
        raise WafApiError("360 WAF username is required")
    if not password:
        raise WafApiError("360 WAF password is required")

    return RuntimeConfig(
        base_url=base_url,
        username=username,
        password=password,
        verify_ssl=_resolve_verify_ssl(raw),
        timeout=timeout,
    )


class WafClient:
    def __init__(self, config: RuntimeConfig) -> None:
        self.config = config
        self.base_url = config.base_url
        self.username = config.username
        self.password = config.password
        self.verify_ssl = config.verify_ssl
        self.timeout = config.timeout
        self._session: Optional[dict[str, Any]] = None

        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise WafApiError("360 WAF base_url must be like https://YOUR_360_WAF_HOST")
        self.scheme = parsed.scheme
        self.host = parsed.hostname
        self.port = parsed.port or (443 if parsed.scheme == "https" else 80)

        if self.scheme == "https":
            if self.verify_ssl:
                self.ssl_context = ssl.create_default_context()
            else:
                self.ssl_context = ssl._create_unverified_context()
                try:
                    self.ssl_context.set_ciphers("DEFAULT:@SECLEVEL=0")
                except Exception:
                    pass
        else:
            self.ssl_context = None

    def login(self) -> dict[str, Any]:
        body = {
            "lang": "zh_CN",
            "username": self._b64(self.username),
            "password": self._b64(self.password),
        }
        data = self._raw_request("POST", "/rest/api/login", body=body, cookie=None)
        if data.get("success") is not True:
            raise WafApiError(self._format_exception("login failed", data))
        result = data.get("result")
        if not isinstance(result, list) or not result:
            raise WafApiError("login response did not contain result[0]")
        session = result[0]
        for key in ("token", "fromrootvsys", "role", "vsysId"):
            if key not in session or session[key] in (None, ""):
                raise WafApiError(f"login response missing {key}")
        self._session = session
        return self._public_session(session)

    def check_login(self) -> dict[str, Any]:
        return self.get("/rest/api/login")

    def logout(self) -> dict[str, Any]:
        if not self._session:
            self.login()
        body = {
            "username": self.username,
            "protocol": self.scheme,
            "token": self._session["token"],
            "role": self._session["role"],
        }
        data = self._raw_request(
            "DELETE", "/rest/api/login", body=body, cookie=self._cookie_header()
        )
        self._session = None
        return data

    def get(
        self, path: str, query: Optional[dict[str, Any]] = None, retry: bool = True
    ) -> dict[str, Any]:
        if not self._session:
            self.login()
        request_path = self._build_path(path, query)
        try:
            data = self._raw_request("GET", request_path, cookie=self._cookie_header())
        except (RemoteDisconnected, ConnectionError, OSError):
            if not retry:
                raise
            self.login()
            data = self._raw_request("GET", request_path, cookie=self._cookie_header())

        if self._is_invalid_login(data) and retry:
            self.login()
            data = self._raw_request("GET", request_path, cookie=self._cookie_header())
        return data

    def call_readonly(
        self, resource: str, query: Optional[dict[str, Any]] = None
    ) -> dict[str, Any]:
        if not resource.startswith("/rest/api/"):
            raise WafApiError("only /rest/api/... resources are allowed")
        return self.get(resource, query=query)

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
            raise WafApiError("method must be GET, POST, PUT, or DELETE")
        if not self._session:
            self.login()
        request_path = self._build_path(path, query)
        data = self._raw_request(
            method, request_path, body=body, cookie=self._cookie_header()
        )
        if self._is_invalid_login(data) and retry:
            self.login()
            data = self._raw_request(
                method, request_path, body=body, cookie=self._cookie_header()
            )
        return data

    def upload_file(
        self,
        path: str,
        file_path: str,
        fields: Optional[dict[str, Any]] = None,
        retry: bool = True,
    ) -> dict[str, Any]:
        if not self._session:
            self.login()
        if not path.startswith("/rest/file/"):
            raise WafApiError("file upload path must start with /rest/file/")
        data = self._raw_file_upload(
            path, file_path, fields=fields or {}, cookie=self._cookie_header()
        )
        if self._is_invalid_login(data) and retry:
            self.login()
            data = self._raw_file_upload(
                path, file_path, fields=fields or {}, cookie=self._cookie_header()
            )
        return data

    def file_request(self, method: str, path: str, retry: bool = True) -> dict[str, Any]:
        method = method.upper()
        if method not in {"GET", "DELETE"}:
            raise WafApiError("file request method must be GET or DELETE")
        if not path.startswith("/rest/file"):
            raise WafApiError("file request path must start with /rest/file")
        if not self._session:
            self.login()
        data = self._raw_request(method, path, cookie=self._cookie_header())
        if self._is_invalid_login(data) and retry:
            self.login()
            data = self._raw_request(method, path, cookie=self._cookie_header())
        return data

    def download_file(self, path: str, save_path: str, retry: bool = True) -> dict[str, Any]:
        if not path.startswith("/download/"):
            raise WafApiError("download path must start with /download/")
        if not self._session:
            self.login()
        status, headers, raw = self._raw_binary_request(
            "GET", path, cookie=self._cookie_header()
        )
        if status in {401, 403} and retry:
            self.login()
            status, headers, raw = self._raw_binary_request(
                "GET", path, cookie=self._cookie_header()
            )
        if status < 200 or status >= 300:
            text = raw.decode("utf-8", errors="replace")
            raise WafApiError(f"HTTP {status} from GET {path}: {text[:300]}")
        target = Path(save_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {
            "success": True,
            "result": {
                "path": str(target),
                "bytes": len(raw),
                "content_type": headers.get("content-type"),
            },
        }

    def _raw_request(
        self, method: str, path: str, body: Optional[Any] = None, cookie: Optional[str] = None
    ) -> dict[str, Any]:
        headers = {
            "Host": self.host,
            "Accept": "application/json",
            "Connection": "close",
        }
        payload = None
        if cookie:
            headers["Cookie"] = cookie
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload))

        if self.scheme == "https":
            conn = HTTPSConnection(
                self.host, self.port, context=self.ssl_context, timeout=self.timeout
            )
        else:
            conn = HTTPConnection(self.host, self.port, timeout=self.timeout)

        try:
            conn.request(method, path, body=payload, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
        finally:
            conn.close()

        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise WafApiError(
                f"non-json response from {method} {path}: HTTP {resp.status}, {text[:200]}"
            ) from exc

        if resp.status < 200 or resp.status >= 300:
            raise WafApiError(f"HTTP {resp.status} from {method} {path}: {text[:300]}")
        return data

    def _raw_file_upload(
        self,
        path: str,
        file_path: str,
        fields: dict[str, Any],
        cookie: Optional[str] = None,
    ) -> dict[str, Any]:
        source = Path(file_path).expanduser().resolve()
        if not source.is_file():
            raise WafApiError(f"upload file not found: {source}")
        boundary = "----wafmcp-" + uuid.uuid4().hex
        parts: list[bytes] = []
        for key, value in fields.items():
            if value is None:
                continue
            parts.append(f"--{boundary}\r\n".encode("utf-8"))
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"))
            parts.append(str(value).encode("utf-8"))
            parts.append(b"\r\n")
        upload_name = str(fields.get("filename") or fields.get("clientFileName") or source.name)
        parts.append(f"--{boundary}\r\n".encode("utf-8"))
        parts.append(
            (
                f'Content-Disposition: form-data; name="upload"; filename="{upload_name}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
        )
        parts.append(source.read_bytes())
        parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode("utf-8"))
        payload = b"".join(parts)

        headers = {
            "Host": self.host,
            "Accept": "application/json",
            "Connection": "close",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(payload)),
        }
        if cookie:
            headers["Cookie"] = cookie
        status, _, raw = self._send_raw("POST", path, payload=payload, headers=headers)
        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text) if text else {}
        except json.JSONDecodeError as exc:
            raise WafApiError(
                f"non-json response from POST {path}: HTTP {status}, {text[:200]}"
            ) from exc
        if status < 200 or status >= 300:
            raise WafApiError(f"HTTP {status} from POST {path}: {text[:300]}")
        return data

    def _raw_binary_request(
        self, method: str, path: str, cookie: Optional[str] = None
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {
            "Host": self.host,
            "Accept": "*/*",
            "Connection": "close",
        }
        if cookie:
            headers["Cookie"] = cookie
        return self._send_raw(method, path, headers=headers)

    def _send_raw(
        self,
        method: str,
        path: str,
        payload: Optional[bytes] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[int, dict[str, str], bytes]:
        if self.scheme == "https":
            conn = HTTPSConnection(
                self.host, self.port, context=self.ssl_context, timeout=self.timeout
            )
        else:
            conn = HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            conn.request(method, path, body=payload, headers=headers or {})
            resp = conn.getresponse()
            raw = resp.read()
            resp_headers = {key.lower(): value for key, value in resp.getheaders()}
            status = resp.status
        finally:
            conn.close()
        return status, resp_headers, raw

    def _cookie_header(self) -> str:
        if not self._session:
            raise WafApiError("not logged in")
        session = self._session
        pairs = {
            "username": self.username,
            "token": session["token"],
            "fromrootvsys": session["fromrootvsys"],
            "role": session["role"],
            "vsysId": session["vsysId"],
        }
        if session.get("platform"):
            pairs["platform"] = session["platform"]
        return "; ".join(f"{key}={value}" for key, value in pairs.items())

    def _build_path(self, path: str, query: Optional[dict[str, Any]]) -> str:
        if not path.startswith("/"):
            path = "/" + path
        if not path.startswith("/rest/api/"):
            raise WafApiError("only /rest/api/... paths are allowed")
        if query is None:
            return path
        sep = "&" if "?" in path else "?"
        encoded = urllib.parse.quote(
            json.dumps(query, ensure_ascii=False, separators=(",", ":")), safe=""
        )
        return f"{path}{sep}query={encoded}"

    @staticmethod
    def _b64(value: str) -> str:
        return base64.b64encode(value.encode("utf-8")).decode("ascii")

    @staticmethod
    def _is_invalid_login(data: dict[str, Any]) -> bool:
        exception = data.get("exception")
        if isinstance(exception, dict):
            code = str(exception.get("code", ""))
            msg = str(exception.get("message", "")).lower()
            return code in {"400000005", "loginError_1002"} or "invalid login" in msg
        return False

    @staticmethod
    def _format_exception(prefix: str, data: dict[str, Any]) -> str:
        exception = data.get("exception")
        if exception:
            return f"{prefix}: {exception}"
        return f"{prefix}: {data}"

    @staticmethod
    def _public_session(session: dict[str, Any]) -> dict[str, Any]:
        token = str(session.get("token", ""))
        masked = token[:6] + "..." + token[-4:] if len(token) > 10 else "***"
        return {
            "token": masked,
            "fromrootvsys": session.get("fromrootvsys"),
            "vsysId": session.get("vsysId"),
            "vsysName": session.get("vsysName"),
            "role": session.get("role"),
            "isLocalAuth": session.get("isLocalAuth"),
        }


_CLIENTS: dict[tuple[str, str, bool], WafClient] = {}


def _client_cache_key(config: RuntimeConfig) -> tuple[str, str, bool]:
    return (config.base_url, config.username, config.verify_ssl)


def get_client() -> WafClient:
    config = _load_runtime_config()
    key = _client_cache_key(config)
    client = _CLIENTS.get(key)
    if client is None or client.password != config.password:
        client = WafClient(config)
        _CLIENTS[key] = client
    return client


def ok(content: Any) -> ToolResult:
    return ToolResult(
        success=True,
        output=content,
        metadata={"source": "360 WAF", "version": PRODUCT_VERSION},
    )


def require_int(value: Any, name: str) -> int:
    try:
        return int(value)
    except Exception as exc:
        raise WafApiError(f"{name} must be an integer") from exc


def build_conditions(args: dict[str, Any], allowed: dict[str, str]) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for arg_name, field_name in allowed.items():
        if arg_name in args and args[arg_name] not in (None, ""):
            conditions.append({"field": field_name, "operator": 0, "value": args[arg_name]})
    return conditions


def add_paging(query: dict[str, Any], args: dict[str, Any], default_limit: int = 50) -> None:
    query["start"] = require_int(args.get("start", 0), "start")
    query["limit"] = require_int(args.get("limit", default_limit), "limit")
    if query["limit"] > 500:
        raise WafApiError("limit must be <= 500")


def waf_check_login(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().check_login())


def waf_system_info_get(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/sysinfo"))


def waf_site_list(args: dict[str, Any]) -> ToolResult:
    query: Optional[dict[str, Any]] = None
    conditions = []
    if args.get("id") not in (None, ""):
        conditions.append({"field": "id", "value": str(args["id"])})
    if args.get("name") not in (None, ""):
        conditions.append({"field": "name", "operator": 6, "value": args["name"]})
    if conditions:
        query = {"conditions": conditions}
    return ok(get_client().get("/rest/api/website", query=query))


def waf_policy_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/wafpolicy"))


def waf_ac_policy_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/wafacpolicy"))


def waf_interface_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/interface"))


def waf_zone_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/zone"))


def waf_blacklist_list(args: dict[str, Any]) -> ToolResult:
    site_id = require_int(args.get("siteId"), "siteId")
    list_type = require_int(args.get("type"), "type")
    query = {
        "conditions": [
            {"field": "siteId", "value": site_id},
            {"field": "type", "value": list_type},
        ]
    }
    return ok(get_client().get("/rest/api/blacklist", query=query))


def waf_whitelist_list(args: dict[str, Any]) -> ToolResult:
    site_id = require_int(args.get("id"), "id")
    query: dict[str, Any] = {"conditions": [{"field": "id", "value": site_id}]}
    if args.get("type") not in (None, ""):
        query["conditions"].append({"field": "type", "value": require_int(args["type"], "type")})
    return ok(get_client().get("/rest/api/whitelist", query=query))


def waf_whitelist_check_ip(args: dict[str, Any]) -> ToolResult:
    site_id = require_int(args.get("id"), "id")
    ip = args.get("ip")
    if not ip:
        raise WafApiError("ip is required")
    query = {
        "conditions": [
            {"field": "id", "value": site_id},
            {"field": "is_ip_in_whitelist.ip", "value": ip},
        ]
    }
    return ok(get_client().get("/rest/api/whitelist", query=query))


def waf_security_log_search(args: dict[str, Any]) -> ToolResult:
    allowed_intervals = {"realtime", "hour", "day", "week", "month"}
    has_custom_time = args.get("time_start") not in (None, "") or args.get("time_end") not in (None, "")
    interval = args.get("interval", None if has_custom_time else "hour")
    conditions: list[dict[str, Any]] = []
    if interval:
        if interval not in allowed_intervals:
            raise WafApiError(f"interval must be one of {sorted(allowed_intervals)}")
        conditions.append({"field": "interval", "operator": 0, "value": interval})

    for arg_name, field_name in (("time_start", "time_start"), ("time_end", "time_end")):
        if args.get(arg_name) not in (None, ""):
            conditions.append({"field": field_name, "operator": 0, "value": args[arg_name]})

    if args.get("severity") not in (None, ""):
        conditions.append(
            {"field": "severity", "operator": 0, "value": require_int(args["severity"], "severity")}
        )

    conditions.extend(
        build_conditions(
            args,
            {
                "client_ip": "client_ip",
                "server_ip": "server_ip",
                "site_name": "site_name",
                "policy_name": "policy_name",
                "domain_name": "domain_name",
                "http_url": "http_url",
                "http_method": "http_method",
                "rule_id": "rule_id",
                "protection_type": "protection_type",
                "protection_sub_type": "protection_sub_type",
            },
        )
    )
    action_filter = first_present(args, "action_filter", "log_action")
    if action_filter not in (None, ""):
        conditions.append({"field": "action", "operator": 0, "value": action_filter})
    query: dict[str, Any] = {"conditions": conditions}
    add_paging(query, args, default_limit=50)
    return ok(get_client().get("/rest/api/websecuritylog", query=query))


def waf_configuration_log_search(args: dict[str, Any]) -> ToolResult:
    query: dict[str, Any] = {}
    time_start = args.get("time_start")
    time_end = args.get("time_end")
    if time_start not in (None, "") or time_end not in (None, ""):
        life_time: dict[str, Any] = {"interval": "custom"}
        if time_start not in (None, ""):
            life_time["start"] = time_start
        if time_end not in (None, ""):
            life_time["end"] = time_end
        query["lifeTime"] = life_time
    elif args.get("interval") not in (None, ""):
        query["lifeTime"] = {"interval": args["interval"]}

    conditions = build_conditions(args, {"msg": "msg"})
    if conditions:
        query["conditions"] = conditions
    add_paging(query, args, default_limit=50)
    return ok(get_client().get("/rest/api/configurationlog", query=query))


def waf_dashboard_stats(args: dict[str, Any]) -> ToolResult:
    kind = args.get("kind")
    mapping = {
        "attack_source_ip": "/rest/api/waf_attack_source_client_ip",
        "attack_source_country": "/rest/api/waf_attack_source_map",
        "threat_category": "/rest/api/waf_protection_type",
        "site_attack": "/rest/api/waf_site_attack",
    }
    if kind not in mapping:
        raise WafApiError(f"kind must be one of {sorted(mapping)}")
    interval = args.get("interval")
    query = None
    if interval:
        query = {"conditions": [{"field": "interval", "value": interval}]}
    return ok(get_client().get(mapping[kind], query=query))


def waf_configfile_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/configfile"))


def waf_signature_status(args: dict[str, Any]) -> ToolResult:
    query_status = bool(args.get("queryStatus", False))
    path = "/rest/api/signature?query=%7B%22conditions%22%3A%5B%7B%22field%22%3A%22index%22%2C%22value%22%3A9%7D%5D%7D"
    if query_status:
        path += "&queryStatus=1"
    return ok(get_client().get(path))


def waf_deploy_mode_get(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/waf_deploy_mode"))


def waf_license_get(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/licenseManagementAgent"))


def waf_custom_error_page_list(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/waf_custom_error_page"))


def waf_mgmt_image_get(args: dict[str, Any]) -> ToolResult:
    version = require_int(args.get("version", 1), "version")
    query = {"conditions": [{"field": "version", "value": version}]}
    return ok(get_client().get("/rest/api/mgmt_image", query=query))


def waf_disk_usage_get(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/disk_usage"))


def waf_capacity_get(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().get("/rest/api/capacity"))


def waf_logout(args: dict[str, Any]) -> ToolResult:
    return ok(get_client().logout())


def require_text(value: Any, name: str) -> str:
    if value in (None, ""):
        raise WafApiError(f"{name} is required")
    text = str(value).strip()
    if not text:
        raise WafApiError(f"{name} is required")
    if len(text) > 127:
        raise WafApiError(f"{name} must be <= 127 characters")
    return text


def require_uri_path(value: Any) -> str:
    uri_path = require_text(value, "uri_path")
    if not uri_path.startswith("/"):
        raise WafApiError("uri_path must start with '/'")
    if "://" in uri_path:
        raise WafApiError("uri_path must be a path, not a full URL")
    return uri_path


def require_policy_id(value: Any, name: str) -> str:
    if value in (None, ""):
        raise WafApiError(f"{name} is required")
    policy_id = str(value).strip()
    if not policy_id.isdigit():
        raise WafApiError(f"{name} must be a numeric id")
    return policy_id


def require_status_code(value: Any) -> int:
    status_code = require_int(value, "status_code")
    allowed = {400, 403, 404, 405, 500, 501, 505}
    if status_code not in allowed:
        raise WafApiError(f"status_code must be one of {sorted(allowed)}")
    return status_code


def require_flag(value: Any, name: str) -> int:
    flag = require_int(value, name)
    if flag not in {0, 1}:
        raise WafApiError(f"{name} must be 0 or 1")
    return flag


def first_present(args: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = args.get(name)
        if value not in (None, ""):
            return value
    return None


def require_choice_int(value: Any, name: str, allowed: set[int]) -> int:
    number = require_int(value, name)
    if number not in allowed:
        raise WafApiError(f"{name} must be one of {sorted(allowed)}")
    return number


def require_payload(value: Any, name: str = "body") -> Any:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise WafApiError(f"{name} must be valid JSON") from exc
    if not isinstance(value, (dict, list)):
        raise WafApiError(f"{name} must be a JSON object or array")
    return value


def optional_payload(value: Any, name: str = "body") -> Any:
    if value in (None, ""):
        return None
    return require_payload(value, name)


def infer_ip_version(ip_start: str) -> int:
    return 1 if ":" in ip_start else 0


def infer_blacklist_type(content: str) -> int:
    if "-" in content:
        return 3
    if "/" in content:
        return 4
    return 1


def require_block_time(value: Any) -> int:
    block_time = require_int(value, "block_time")
    if block_time < 1 or block_time > 1440:
        raise WafApiError("block_time must be between 1 and 1440 minutes")
    return block_time


def blacklist_body(args: dict[str, Any], *, include_site: bool, include_is_permanent: bool) -> list[dict[str, Any]]:
    content = require_text(first_present(args, "content", "ip"), "content")
    list_type = require_choice_int(args.get("type", infer_blacklist_type(content)), "type", {1, 3, 4})
    entry: dict[str, Any] = {"type": list_type, "content": content}
    if include_site:
        entry["siteId"] = require_int(first_present(args, "siteId", "site_id"), "siteId")
    if include_is_permanent:
        is_permanent = str(require_flag(args.get("is_permanent", 1), "is_permanent"))
        entry["is_permanent"] = is_permanent
        if is_permanent == "0" and args.get("block_time") not in (None, ""):
            entry["block_time"] = require_block_time(args["block_time"])
    if include_site:
        return [{"siteId": entry.pop("siteId"), **entry}]
    return [entry]


def whitelist_ip_entry(args: dict[str, Any], *, include_site: bool, for_delete: bool) -> dict[str, Any]:
    ip_start = require_text(first_present(args, "ip_start", "ip"), "ip_start")
    ip_ver = require_choice_int(args.get("ip_ver", infer_ip_version(ip_start)), "ip_ver", {0, 1})
    list_type = require_choice_int(args.get("type", 0), "type", {0, 1, 2})
    entry: dict[str, Any] = {
        "ip_ver": str(ip_ver),
        "type": str(list_type),
        "ip_start": ip_start,
    }
    if list_type == 1:
        default_netmask = 128 if ip_ver == 1 else 32
        entry["netmask"] = require_int(args.get("netmask", default_netmask), "netmask")
    elif args.get("netmask") not in (None, ""):
        entry["netmask"] = require_int(args["netmask"], "netmask")

    if list_type == 2:
        entry["ip_end"] = require_text(args.get("ip_end"), "ip_end")
    elif args.get("ip_end") not in (None, ""):
        entry["ip_end"] = require_text(args.get("ip_end"), "ip_end")

    if for_delete:
        entry.setdefault("ip_end", "::" if ip_ver == 1 else "0")
        entry.setdefault("netmask", 128 if ip_ver == 1 else 32)
    elif args.get("desc") not in (None, ""):
        entry["desc"] = str(args["desc"])

    if not include_site:
        return entry
    site_id = require_int(first_present(args, "id", "site_id", "siteId"), "id")
    return {"id": site_id, "ip_whitelist": entry}


def whitelist_body(args: dict[str, Any], *, for_delete: bool) -> dict[str, Any]:
    return whitelist_ip_entry(args, include_site=True, for_delete=for_delete)


def global_whitelist_body(args: dict[str, Any], *, for_delete: bool) -> list[dict[str, Any]]:
    return [whitelist_ip_entry(args, include_site=False, for_delete=for_delete)]


def build_deny_uri_policy_body(
    args: dict[str, Any],
    policy_name: str,
    uri_path: str,
    status_code: int,
) -> list[dict[str, Any]]:
    operator = str(args.get("operator", "location"))
    if operator not in {"location", "rx"}:
        raise WafApiError("operator must be 'location' or 'rx'")
    body: dict[str, Any] = {
        "name": policy_name,
        "action": "deny",
        "status_code": status_code,
        "description": str(args.get("description") or "Created by 360_waf Flocks integration"),
        "capture_pkt": require_flag(args.get("capture_pkt", 1), "capture_pkt"),
        "log": require_flag(args.get("log", 1), "log"),
        "uri_path_list": {
            "enable": 1,
            "operator": operator,
            "is_negative": 0,
            "encode": str(args.get("encode", "UTF-8")),
            "no_case": require_flag(args.get("no_case", 1), "no_case"),
            "uri_path": [{"pattern": uri_path}],
        },
    }
    http_method = args.get("http_method")
    if http_method not in (None, ""):
        body["http_method"] = {"enable": 1, "is_negative": 0, "pattern": str(http_method).lower()}
    return [body]


def result_as_list(data: dict[str, Any]) -> list[Any]:
    result = data.get("result")
    if isinstance(result, list):
        return result
    if isinstance(result, dict):
        return [result]
    return []


def ensure_api_success(label: str, data: dict[str, Any]) -> None:
    if data.get("success") is not True:
        raise WafApiError(f"{label} failed: {json.dumps(data, ensure_ascii=False)}")


def create_deny_uri_policy(
    client_obj: WafClient, body: list[dict[str, Any]], policy_name: str
) -> dict[str, Any]:
    post_result = client_obj.request("POST", "/rest/api/wafacpolicy", body=body)
    ensure_api_success("POST /rest/api/wafacpolicy", post_result)
    result_items = result_as_list(post_result)
    policy = result_items[0] if result_items else None
    if not isinstance(policy, dict) or not policy.get("id"):
        policy = find_ac_policy_by_name(client_obj, policy_name)
    if not isinstance(policy, dict) or not policy.get("id"):
        raise WafApiError("POST /rest/api/wafacpolicy returned success but no created policy id was found")
    policy_id = require_policy_id(policy.get("id"), "created policy id")
    verify = client_obj.get("/rest/api/wafacpolicy", query={"conditions": [{"field": "id", "value": policy_id}]})
    return {"policy_id": policy_id, "policy": policy, "post": post_result, "verify": verify}


def resolve_site(client_obj: WafClient, args: dict[str, Any]) -> dict[str, Any]:
    conditions: list[dict[str, Any]] = []
    if args.get("site_id") not in (None, ""):
        conditions.append({"field": "id", "value": str(args["site_id"])})
    else:
        site_name = str(args.get("site_name") or "default")
        conditions.append({"field": "name", "operator": 0, "value": site_name})
    data = client_obj.get("/rest/api/website", query={"conditions": conditions})
    result_items = result_as_list(data)
    if not result_items:
        raise WafApiError(f"site not found for conditions: {conditions}")
    site = result_items[0]
    if not isinstance(site, dict) or not site.get("id"):
        raise WafApiError("site lookup did not return a valid site object")
    return site


def bind_ac_policy_to_site(
    client_obj: WafClient,
    site: dict[str, Any],
    policy_id: str,
    position: str = "append",
) -> dict[str, Any]:
    before_ids = site_ac_policy_ids(site)
    after_ids = insert_policy_id(before_ids, policy_id, position)
    if after_ids == before_ids:
        return {"changed": False, "before": before_ids, "after": after_ids, "verify": site}
    put_result = update_site_ac_policy(client_obj, site, after_ids)
    return {"changed": True, "before": before_ids, "after": after_ids, **put_result}


def unbind_ac_policy_from_site(client_obj: WafClient, site: dict[str, Any], policy_id: str) -> dict[str, Any]:
    before_ids = site_ac_policy_ids(site)
    after_ids = [item for item in before_ids if item != policy_id]
    if after_ids == before_ids:
        return {"changed": False, "before": before_ids, "after": after_ids, "verify": site}
    put_result = update_site_ac_policy(client_obj, site, after_ids)
    return {"changed": True, "before": before_ids, "after": after_ids, **put_result}


def update_site_ac_policy(client_obj: WafClient, site: dict[str, Any], policy_ids: list[str]) -> dict[str, Any]:
    site_id = str(site.get("id"))
    body = {
        "id": site_id,
        "name": str(site.get("name") or ""),
        "ac_policy": ";".join(policy_ids),
    }
    put_result = client_obj.request("PUT", "/rest/api/website", body=body)
    ensure_api_success("PUT /rest/api/website", put_result)
    verify = client_obj.get("/rest/api/website", query={"conditions": [{"field": "id", "value": site_id}]})
    return {"body": body, "put": put_result, "verify": verify}


def site_ac_policy_ids(site: dict[str, Any]) -> list[str]:
    raw = site.get("ac_policy")
    ids: list[str] = []
    if raw in (None, ""):
        return ids
    if isinstance(raw, str):
        for item in raw.replace(",", ";").split(";"):
            add_policy_id(ids, item)
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                add_policy_id(ids, item.get("id"))
            else:
                add_policy_id(ids, item)
    else:
        add_policy_id(ids, raw)
    return ids


def add_policy_id(ids: list[str], value: Any) -> None:
    if value in (None, ""):
        return
    policy_id = str(value).strip()
    if policy_id and policy_id not in ids:
        ids.append(policy_id)


def insert_policy_id(values: list[str], value: str, position: str) -> list[str]:
    if position not in {"append", "prepend"}:
        raise WafApiError("position must be 'append' or 'prepend'")
    output = [item for item in values if item != value]
    if position == "prepend":
        output.insert(0, value)
    else:
        output.append(value)
    return output


def find_ac_policy_by_name(client_obj: WafClient, policy_name: str) -> Optional[dict[str, Any]]:
    data = client_obj.get("/rest/api/wafacpolicy")
    for item in result_as_list(data):
        if isinstance(item, dict) and item.get("name") == policy_name:
            return item
    return None


def policy_is_deny_uri(policy: dict[str, Any], uri_path: str) -> bool:
    if str(policy.get("action", "")).lower() != "deny":
        return False
    uri_path_list = policy.get("uri_path_list")
    if not isinstance(uri_path_list, dict):
        return False
    if str(uri_path_list.get("enable", "0")) != "1":
        return False
    uri_entries = uri_path_list.get("uri_path")
    if isinstance(uri_entries, dict):
        return uri_entries.get("pattern") == uri_path
    if isinstance(uri_entries, list):
        return any(isinstance(item, dict) and item.get("pattern") == uri_path for item in uri_entries)
    return False


def delete_ac_policy(client_obj: WafClient, policy_id: str) -> dict[str, Any]:
    data = client_obj.request("DELETE", "/rest/api/wafacpolicy", body=[{"id": policy_id}])
    ensure_api_success("DELETE /rest/api/wafacpolicy", data)
    return data


def default_deny_policy_name(site_name: str, uri_path: str) -> str:
    raw = f"{site_name}_{uri_path.strip('/') or 'root'}".lower()
    chars = []
    for char in raw:
        if char.isalnum():
            chars.append(char)
        else:
            chars.append("_")
    compact = "_".join(part for part in "".join(chars).split("_") if part)
    return ("mcp_deny_" + compact)[:127]


def waf_ac_policy_create_deny_uri(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    policy_name = require_text(args.get("name"), "name")
    uri_path = require_uri_path(args.get("uri_path"))
    status_code = require_status_code(args.get("status_code", 403))
    body = build_deny_uri_policy_body(args, policy_name, uri_path, status_code)
    created = create_deny_uri_policy(client_obj, body, policy_name)
    return ok(created)


def waf_site_bind_ac_policy(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    policy_id = require_policy_id(args.get("policy_id"), "policy_id")
    site = resolve_site(client_obj, args)
    position = str(args.get("position") or "append")
    return ok(bind_ac_policy_to_site(client_obj, site, policy_id, position=position))


def waf_site_unbind_ac_policy(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    policy_id = require_policy_id(args.get("policy_id"), "policy_id")
    site = resolve_site(client_obj, args)
    return ok(unbind_ac_policy_from_site(client_obj, site, policy_id))


def waf_ac_policy_delete(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    policy_id = require_policy_id(args.get("policy_id"), "policy_id")
    if policy_id == "1":
        raise WafApiError("refusing to delete built-in access-control policy id=1")
    delete_result = delete_ac_policy(client_obj, policy_id)
    verify = client_obj.get("/rest/api/wafacpolicy", query={"conditions": [{"field": "id", "value": policy_id}]})
    return ok({"policy_id": policy_id, "delete": delete_result, "verify": verify})


def waf_blacklist_create(args: dict[str, Any]) -> ToolResult:
    body = blacklist_body(args, include_site=True, include_is_permanent=True)
    data = get_client().request("POST", "/rest/api/blacklist", body=body)
    ensure_api_success("POST /rest/api/blacklist", data)
    return ok(data)


def waf_blacklist_delete(args: dict[str, Any]) -> ToolResult:
    body = blacklist_body(args, include_site=True, include_is_permanent=False)
    data = get_client().request("DELETE", "/rest/api/blacklist", body=body)
    ensure_api_success("DELETE /rest/api/blacklist", data)
    return ok(data)


def waf_site_global_blacklist_create(args: dict[str, Any]) -> ToolResult:
    body = blacklist_body(args, include_site=False, include_is_permanent=True)
    data = get_client().request("POST", "/rest/api/site_global_blacklist", body=body)
    ensure_api_success("POST /rest/api/site_global_blacklist", data)
    return ok(data)


def waf_site_global_blacklist_delete(args: dict[str, Any]) -> ToolResult:
    body = blacklist_body(args, include_site=False, include_is_permanent=False)
    data = get_client().request("DELETE", "/rest/api/site_global_blacklist", body=body)
    ensure_api_success("DELETE /rest/api/site_global_blacklist", data)
    return ok(data)


def waf_whitelist_create(args: dict[str, Any]) -> ToolResult:
    body = whitelist_body(args, for_delete=False)
    data = get_client().request("POST", "/rest/api/whitelist", body=body)
    ensure_api_success("POST /rest/api/whitelist", data)
    return ok(data)


def waf_whitelist_delete(args: dict[str, Any]) -> ToolResult:
    body = whitelist_body(args, for_delete=True)
    data = get_client().request("DELETE", "/rest/api/whitelist", body=body)
    ensure_api_success("DELETE /rest/api/whitelist", data)
    return ok(data)


def waf_site_global_whitelist_create(args: dict[str, Any]) -> ToolResult:
    body = global_whitelist_body(args, for_delete=False)
    data = get_client().request("POST", "/rest/api/site_global_whitelist", body=body)
    ensure_api_success("POST /rest/api/site_global_whitelist", data)
    return ok(data)


def waf_site_global_whitelist_delete(args: dict[str, Any]) -> ToolResult:
    body = global_whitelist_body(args, for_delete=True)
    data = get_client().request("DELETE", "/rest/api/site_global_whitelist", body=body)
    ensure_api_success("DELETE /rest/api/site_global_whitelist", data)
    return ok(data)


def waf_exception_rule_create(args: dict[str, Any]) -> ToolResult:
    body = require_payload(args.get("body"))
    data = get_client().request("POST", "/rest/api/exceptionlist", body=body)
    ensure_api_success("POST /rest/api/exceptionlist", data)
    return ok(data)


def waf_exception_rule_update(args: dict[str, Any]) -> ToolResult:
    body = require_payload(args.get("body"))
    data = get_client().request("PUT", "/rest/api/exceptionlist", body=body)
    ensure_api_success("PUT /rest/api/exceptionlist", data)
    return ok(data)


def waf_exception_rule_delete(args: dict[str, Any]) -> ToolResult:
    body = require_payload(args.get("body"))
    data = get_client().request("DELETE", "/rest/api/exceptionlist", body=body)
    ensure_api_success("DELETE /rest/api/exceptionlist", data)
    return ok(data)


def waf_uri_block_on_site(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    uri_path = require_uri_path(args.get("uri_path"))
    site = resolve_site(client_obj, args)
    site_name = str(site.get("name") or args.get("site_name") or "site")
    policy_name = str(args.get("policy_name") or default_deny_policy_name(site_name, uri_path))
    status_code = require_status_code(args.get("status_code", 403))
    reuse_existing = bool(args.get("reuse_existing", True))

    existing = find_ac_policy_by_name(client_obj, policy_name) if reuse_existing else None
    if existing is not None and not policy_is_deny_uri(existing, uri_path):
        raise WafApiError(f"access-control policy named {policy_name!r} exists but is not a deny policy for {uri_path}")
    created_new = existing is None
    if existing is None:
        body = build_deny_uri_policy_body(args, policy_name, uri_path, status_code)
        created = create_deny_uri_policy(client_obj, body, policy_name)
        policy = created["policy"]
    else:
        created = {"policy_id": str(existing["id"]), "policy": existing, "post": None, "verify": None}
        policy = existing

    policy_id = require_policy_id(policy.get("id"), "created policy id")
    try:
        bind_result = bind_ac_policy_to_site(client_obj, site, policy_id, position="prepend")
    except Exception:
        if created_new:
            try:
                delete_ac_policy(client_obj, policy_id)
            except Exception:
                pass
        raise

    return ok(
        {
            "site": {"id": str(site.get("id")), "name": site.get("name")},
            "uri_path": uri_path,
            "policy_id": policy_id,
            "policy_name": policy_name,
            "created_new_policy": created_new,
            "create": created,
            "bind": bind_result,
        }
    )


def waf_uri_unblock_on_site(args: dict[str, Any]) -> ToolResult:
    client_obj = get_client()
    site = resolve_site(client_obj, args)
    policy_id = args.get("policy_id")
    if policy_id in (None, ""):
        uri_path = require_uri_path(args.get("uri_path"))
        site_name = str(site.get("name") or args.get("site_name") or "site")
        policy_name = str(args.get("policy_name") or default_deny_policy_name(site_name, uri_path))
        policy = find_ac_policy_by_name(client_obj, policy_name)
        if policy is None:
            raise WafApiError(f"access-control policy not found by name: {policy_name}")
        if not policy_is_deny_uri(policy, uri_path):
            raise WafApiError(f"access-control policy named {policy_name!r} is not a deny policy for {uri_path}")
        policy_id = policy.get("id")
    policy_id = require_policy_id(policy_id, "policy_id")
    unbind_result = unbind_ac_policy_from_site(client_obj, site, policy_id)
    delete_policy = bool(args.get("delete_policy", True))
    delete_result = None
    if delete_policy:
        if policy_id == "1":
            raise WafApiError("refusing to delete built-in access-control policy id=1")
        delete_result = delete_ac_policy(client_obj, policy_id)
    verify = client_obj.get("/rest/api/wafacpolicy", query={"conditions": [{"field": "id", "value": policy_id}]})
    return ok({"policy_id": policy_id, "unbind": unbind_result, "delete": delete_result, "verify_policy": verify})


def waf_api_catalog(args: dict[str, Any]) -> ToolResult:
    return ok(
        {
            "documented_rest_api_resources": DOCUMENTED_API_METHODS,
            "covered_by": {
                "GET": "waf_call_raw_readonly or waf_call_api",
                "POST_PUT_DELETE": "waf_call_mutation or waf_call_api",
            },
            "file_upload_endpoints": DOCUMENTED_FILE_ENDPOINTS,
            "file_tools": {
                "POST rest/file/...": "waf_file_upload",
                "DELETE rest/file?fileName=tmp": "waf_file_request",
                "GET /download/...": "waf_download_file",
            },
            "specialized_mutation_tools": {
                "deny URI policy": "waf_ac_policy_create_deny_uri",
                "bind access-control policy to site": "waf_site_bind_ac_policy",
                "unbind access-control policy from site": "waf_site_unbind_ac_policy",
                "delete access-control policy": "waf_ac_policy_delete",
                "block URI on site in one step": "waf_uri_block_on_site",
                "unblock URI on site in one step": "waf_uri_unblock_on_site",
                "site blacklist": "waf_blacklist_create / waf_blacklist_delete",
                "global blacklist": "waf_site_global_blacklist_create / waf_site_global_blacklist_delete",
                "site whitelist": "waf_whitelist_create / waf_whitelist_delete",
                "global whitelist": "waf_site_global_whitelist_create / waf_site_global_whitelist_delete",
                "exception rules": "waf_exception_rule_create / waf_exception_rule_update / waf_exception_rule_delete",
            },
        }
    )


def waf_call_raw_readonly(args: dict[str, Any]) -> ToolResult:
    path = args.get("path")
    if not path:
        raise WafApiError("path is required")
    api_path = normalize_api_path(str(path))
    validate_documented_api("GET", api_path)
    query = args.get("query")
    if query is not None and not isinstance(query, dict):
        raise WafApiError("query must be an object")
    return ok(get_client().call_readonly(api_path, query=query))


def waf_call_mutation(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "")).upper()
    path = normalize_api_path(str(args.get("path", "")))
    if method not in {"POST", "PUT", "DELETE"}:
        raise WafApiError("method must be POST, PUT, or DELETE")
    validate_documented_api(method, path)
    reject_blocked_device_state_mutation(method, path)
    query = args.get("query")
    if query is not None and not isinstance(query, dict):
        raise WafApiError("query must be an object")
    body = optional_payload(args.get("body"))
    return ok(get_client().request(method, path, query=query, body=body))


def waf_call_api(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "GET")).upper()
    path = normalize_api_path(str(args.get("path", "")))
    if method not in {"GET", "POST", "PUT", "DELETE"}:
        raise WafApiError("method must be GET, POST, PUT, or DELETE")
    validate_documented_api(method, path)
    query = args.get("query")
    if query is not None and not isinstance(query, dict):
        raise WafApiError("query must be an object")
    if method == "GET":
        return ok(get_client().get(path, query=query))
    return waf_call_mutation(args)


def waf_file_upload(args: dict[str, Any]) -> ToolResult:
    path = normalize_file_path(str(args.get("path", "")))
    validate_documented_file_api("POST", path)
    reject_blocked_file_mutation("POST", path)
    file_path = str(args.get("file_path", ""))
    fields = args.get("fields") or {}
    if not file_path:
        raise WafApiError("file_path is required")
    if not isinstance(fields, dict):
        raise WafApiError("fields must be an object")
    return ok(get_client().upload_file(path, file_path, fields=fields))


def waf_file_request(args: dict[str, Any]) -> ToolResult:
    method = str(args.get("method", "")).upper()
    path = normalize_file_path(str(args.get("path", "")))
    validate_documented_file_api(method, path)
    reject_blocked_file_mutation(method, path)
    return ok(get_client().file_request(method, path))


def waf_download_file(args: dict[str, Any]) -> ToolResult:
    path = normalize_download_path(str(args.get("path", "")))
    save_path = str(args.get("save_path", ""))
    if not save_path:
        raise WafApiError("save_path is required")
    return ok(get_client().download_file(path, save_path))


def normalize_api_path(path: str) -> str:
    if path.startswith("rest/api/"):
        path = "/" + path
    if not path.startswith("/rest/api/"):
        raise WafApiError("path must start with rest/api/ or /rest/api/")
    return path


def normalize_file_path(path: str) -> str:
    if path.startswith("rest/file"):
        path = "/" + path
    if not path.startswith("/rest/file"):
        raise WafApiError("path must start with rest/file or /rest/file")
    return path


def normalize_download_path(path: str) -> str:
    if path.startswith("download/"):
        path = "/" + path
    if not path.startswith("/download/"):
        raise WafApiError("path must start with download/ or /download/")
    return path


def reject_blocked_device_state_mutation(method: str, path: str) -> None:
    resource = path.split("?", 1)[0]
    if method.upper() in BLOCKED_DEVICE_STATE_MUTATIONS.get(resource, set()):
        raise WafApiError(
            "360 WAF integration does not support modifying WAF device state "
            f"through raw mutation tools: {method.upper()} {resource}"
        )


def reject_blocked_file_mutation(method: str, path: str) -> None:
    if path == "/rest/file" and method.upper() == "DELETE":
        path = "/rest/file?fileName=tmp"
    if method.upper() in BLOCKED_FILE_MUTATIONS.get(path, set()):
        raise WafApiError(
            "360 WAF integration does not support WAF upgrade or import file operations: "
            f"{method.upper()} {path}"
        )


def validate_documented_api(method: str, path: str) -> None:
    resource = path.split("?", 1)[0]
    methods = DOCUMENTED_API_METHODS.get(resource)
    if methods is None:
        raise WafApiError(f"{resource} is not listed in the local WAF API document")
    if method.upper() not in methods:
        raise WafApiError(f"{method.upper()} {resource} is not listed in the local WAF API document")


def validate_documented_file_api(method: str, path: str) -> None:
    if path == "/rest/file" and method.upper() == "DELETE":
        path = "/rest/file?fileName=tmp"
    methods = DOCUMENTED_FILE_ENDPOINTS.get(path)
    if methods is None:
        raise WafApiError(f"{path} is not listed as a WAF file helper endpoint in the local document")
    if method.upper() not in methods:
        raise WafApiError(f"{method.upper()} {path} is not listed in the local WAF API document")


_ACTION_MAP = {
    "waf_check_login": waf_check_login,
    "waf_system_info_get": waf_system_info_get,
    "waf_site_list": waf_site_list,
    "waf_policy_list": waf_policy_list,
    "waf_ac_policy_list": waf_ac_policy_list,
    "waf_ac_policy_create_deny_uri": waf_ac_policy_create_deny_uri,
    "waf_site_bind_ac_policy": waf_site_bind_ac_policy,
    "waf_site_unbind_ac_policy": waf_site_unbind_ac_policy,
    "waf_ac_policy_delete": waf_ac_policy_delete,
    "waf_blacklist_create": waf_blacklist_create,
    "waf_blacklist_delete": waf_blacklist_delete,
    "waf_site_global_blacklist_create": waf_site_global_blacklist_create,
    "waf_site_global_blacklist_delete": waf_site_global_blacklist_delete,
    "waf_whitelist_create": waf_whitelist_create,
    "waf_whitelist_delete": waf_whitelist_delete,
    "waf_site_global_whitelist_create": waf_site_global_whitelist_create,
    "waf_site_global_whitelist_delete": waf_site_global_whitelist_delete,
    "waf_exception_rule_create": waf_exception_rule_create,
    "waf_exception_rule_update": waf_exception_rule_update,
    "waf_exception_rule_delete": waf_exception_rule_delete,
    "waf_uri_block_on_site": waf_uri_block_on_site,
    "waf_uri_unblock_on_site": waf_uri_unblock_on_site,
    "waf_security_log_search": waf_security_log_search,
    "waf_configuration_log_search": waf_configuration_log_search,
    "waf_dashboard_stats": waf_dashboard_stats,
    "waf_interface_list": waf_interface_list,
    "waf_zone_list": waf_zone_list,
    "waf_blacklist_list": waf_blacklist_list,
    "waf_whitelist_list": waf_whitelist_list,
    "waf_whitelist_check_ip": waf_whitelist_check_ip,
    "waf_configfile_list": waf_configfile_list,
    "waf_signature_status": waf_signature_status,
    "waf_deploy_mode_get": waf_deploy_mode_get,
    "waf_license_get": waf_license_get,
    "waf_custom_error_page_list": waf_custom_error_page_list,
    "waf_mgmt_image_get": waf_mgmt_image_get,
    "waf_disk_usage_get": waf_disk_usage_get,
    "waf_capacity_get": waf_capacity_get,
    "waf_api_catalog": waf_api_catalog,
    "waf_call_raw_readonly": waf_call_raw_readonly,
    "waf_call_mutation": waf_call_mutation,
    "waf_call_api": waf_call_api,
    "waf_file_upload": waf_file_upload,
    "waf_file_request": waf_file_request,
    "waf_download_file": waf_download_file,
    "waf_logout": waf_logout,
}

GROUP_ACTIONS: dict[str, set[str]] = {
    "system": {
        "waf_check_login",
        "waf_system_info_get",
        "waf_interface_list",
        "waf_zone_list",
        "waf_configfile_list",
        "waf_signature_status",
        "waf_deploy_mode_get",
        "waf_license_get",
        "waf_custom_error_page_list",
        "waf_mgmt_image_get",
        "waf_disk_usage_get",
        "waf_capacity_get",
        "waf_logout",
    },
    "site": {
        "waf_site_list",
        "waf_blacklist_list",
        "waf_whitelist_list",
        "waf_whitelist_check_ip",
    },
    "policy_ops": {
        "waf_policy_list",
        "waf_ac_policy_list",
        "waf_ac_policy_create_deny_uri",
        "waf_site_bind_ac_policy",
        "waf_site_unbind_ac_policy",
        "waf_ac_policy_delete",
        "waf_blacklist_create",
        "waf_blacklist_delete",
        "waf_site_global_blacklist_create",
        "waf_site_global_blacklist_delete",
        "waf_whitelist_create",
        "waf_whitelist_delete",
        "waf_site_global_whitelist_create",
        "waf_site_global_whitelist_delete",
        "waf_exception_rule_create",
        "waf_exception_rule_update",
        "waf_exception_rule_delete",
        "waf_uri_block_on_site",
        "waf_uri_unblock_on_site",
    },
    "observability": {
        "waf_security_log_search",
        "waf_configuration_log_search",
        "waf_dashboard_stats",
    },
    "api_readonly": {
        "waf_api_catalog",
        "waf_call_raw_readonly",
    },
    "api_mutation": {
        "waf_call_mutation",
        "waf_call_api",
    },
    "file_ops": {
        "waf_file_upload",
        "waf_file_request",
        "waf_download_file",
    },
}

_CONNECTIVITY_TEST_ACTIONS = {
    "system": "waf_check_login",
    "site": "waf_site_list",
    "policy_ops": "waf_policy_list",
    "observability": "waf_security_log_search",
    "api_readonly": "waf_api_catalog",
}


async def unified_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    del ctx
    handler = _ACTION_MAP.get(action)
    if handler is None:
        available = ", ".join(sorted(_ACTION_MAP))
        return ToolResult(success=False, error=f"Unknown action: {action}. Available: {available}")
    try:
        return await asyncio.to_thread(handler, params)
    except WafApiError as exc:
        return ToolResult(
            success=False,
            error=str(exc),
            metadata={"source": "360 WAF", "version": PRODUCT_VERSION, "action": action},
        )
    except Exception as exc:
        return ToolResult(
            success=False,
            error=f"Unexpected 360 WAF error: {exc}",
            metadata={"source": "360 WAF", "version": PRODUCT_VERSION, "action": action},
        )


async def _dispatch_group(ctx: ToolContext, group: str, action: str, **params: Any) -> ToolResult:
    if action == "test":
        test_action = _CONNECTIVITY_TEST_ACTIONS.get(group)
        if test_action:
            return await unified_ops(ctx, action=test_action, **params)
        return ToolResult(
            success=False,
            error=(
                f"360 WAF group {group} does not define a zero-argument "
                "connectivity probe; pass an explicit action and parameters."
            ),
        )
    if action not in GROUP_ACTIONS[group]:
        available = ", ".join(sorted(GROUP_ACTIONS[group]))
        return ToolResult(success=False, error=f"Unsupported {group} action: {action}. Available: {available}")
    return await unified_ops(ctx, action=action, **params)


async def system(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "system", action, **params)


async def site(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "site", action, **params)


async def policy_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "policy_ops", action, **params)


async def observability(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "observability", action, **params)


async def api_readonly(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "api_readonly", action, **params)


async def api_mutation(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "api_mutation", action, **params)


async def file_ops(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "file_ops", action, **params)
