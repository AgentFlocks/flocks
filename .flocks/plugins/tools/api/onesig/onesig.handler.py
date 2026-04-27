from __future__ import annotations

import asyncio
import base64
import os
import ssl
from typing import Any, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "onesig_api"

DEFAULT_API_PREFIX = "/api"
DEFAULT_OAEP_HASH = "sha1"
DEFAULT_TIMEOUT = 60
DEFAULT_VERIFY_SSL = True

_RESPONSE_CODE_OK = 0
_RESPONSE_CODE_TOTP_REQUIRED = 1012
_RESPONSE_CODE_DEFAULT_PWD = 1010
_RESPONSE_CODE_PWD_EXPIRED = 1011

_SESSION_EXPIRED_RESPONSE_CODES = frozenset({1019, 1020, 1021, 1022})
_SESSION_EXPIRED_HTTP_STATUSES = frozenset({401, 403})


def _get_secret_manager() -> Any:
    from flocks.security import get_secret_manager

    return get_secret_manager()


def _resolve_ref(value: Any) -> Optional[str]:
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


def _coerce_bool(value: Any, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


class OneSIGRuntimeConfig:
    """Resolved runtime configuration for a single OneSIG service entry."""

    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str,
        username: str,
        password: str,
        oaep_hash: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.api_prefix = api_prefix
        self.username = username
        self.password = password
        self.oaep_hash = oaep_hash
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    @property
    def session_key(self) -> str:
        return f"{self.base_url}|{self.username}"

    def build_url(self, path: str) -> str:
        path = path if path.startswith("/") else "/" + path
        prefix = self.api_prefix.rstrip("/")
        return f"{self.base_url}{prefix}{path}"


def _resolve_runtime_config() -> OneSIGRuntimeConfig:
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or os.getenv("ONESIG_BASE_URL")
    )
    if not base_url:
        raise ValueError(
            "OneSIG base_url not configured. Set api_services.onesig_api.base_url or ONESIG_BASE_URL."
        )
    base_url = base_url.rstrip("/")

    api_prefix = (
        _resolve_ref(raw.get("api_prefix"))
        or _resolve_ref(raw.get("apiPrefix"))
        or os.getenv("ONESIG_API_PREFIX")
        or DEFAULT_API_PREFIX
    )
    if api_prefix and not api_prefix.startswith("/"):
        api_prefix = "/" + api_prefix
    api_prefix = api_prefix.rstrip("/")

    username = (
        _resolve_ref(raw.get("username"))
        or _resolve_ref(raw.get("user"))
        or os.getenv("ONESIG_USERNAME")
    )
    if not username:
        raise ValueError(
            "OneSIG username not configured. Set api_services.onesig_api.username or ONESIG_USERNAME."
        )

    secret_manager = _get_secret_manager()
    password = (
        _resolve_ref(raw.get("password"))
        or secret_manager.get("onesig_password")
        or secret_manager.get(f"{SERVICE_ID}_password")
        or os.getenv("ONESIG_PASSWORD")
    )
    if not password:
        raise ValueError(
            "OneSIG password not configured. Save it as the onesig_password secret or set ONESIG_PASSWORD."
        )

    oaep_hash = (
        _resolve_ref(raw.get("oaep_hash"))
        or _resolve_ref(raw.get("oaepHash"))
        or os.getenv("ONESIG_OAEP_HASH")
        or DEFAULT_OAEP_HASH
    ).lower()
    if oaep_hash not in {"sha1", "sha256"}:
        oaep_hash = DEFAULT_OAEP_HASH

    verify_ssl = _coerce_bool(
        raw.get("verify_ssl", raw.get("verifySsl", os.getenv("ONESIG_VERIFY_SSL"))),
        default=DEFAULT_VERIFY_SSL,
    )

    timeout_raw = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return OneSIGRuntimeConfig(
        base_url=base_url,
        api_prefix=api_prefix,
        username=username,
        password=password,
        oaep_hash=oaep_hash,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


def _rsa_oaep_encrypt(pem_pubkey: str, plain: str, oaep_hash: str) -> str:
    """Encrypt `plain` with RSA-OAEP using the provided PEM public key."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    hash_alg = hashes.SHA1() if oaep_hash == "sha1" else hashes.SHA256()
    pub = serialization.load_pem_public_key(
        pem_pubkey.encode("utf-8"), backend=default_backend()
    )
    cipher = pub.encrypt(
        plain.encode("utf-8"),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hash_alg),
            algorithm=hash_alg,
            label=None,
        ),
    )
    return base64.b64encode(cipher).decode("ascii")


def _ssl_context(verify_ssl: bool) -> Any:
    if verify_ssl:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class OneSIGSession:
    """Cookie-based session for a OneSIG device.

    A single instance owns an aiohttp ClientSession with its own cookie jar and
    handles the captcha → pubkey → login flow. Concurrent callers reuse the
    same logged-in session and share the auto-relogin lock.
    """

    def __init__(self, config: OneSIGRuntimeConfig) -> None:
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._logged_in = False
        self._login_lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            jar = aiohttp.CookieJar(unsafe=True)
            self._session = aiohttp.ClientSession(cookie_jar=jar)
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None
        self._logged_in = False

    async def login(
        self,
        *,
        captcha: Optional[str] = None,
        totp: Optional[str] = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """Run the captcha → pubkey → /v3/login → optional /v3/login/totp flow.

        Returns the final account info on success. Raises ValueError on failure
        with a human-readable explanation.
        """
        async with self._login_lock:
            if self._logged_in and not force:
                account = await self._raw_request_json("GET", "/v3/account")
                if isinstance(account, dict) and account.get("responseCode") == _RESPONSE_CODE_OK:
                    return account.get("data", {}) or {}
                self._logged_in = False

            captcha_info = await self._raw_request_json("GET", "/v3/captcha")
            captcha_data = (
                captcha_info.get("data", {}) if isinstance(captcha_info, dict) else {}
            )
            enable_captcha = bool(captcha_data.get("enableCaptcha"))
            enable_totp_inline = bool(captcha_data.get("enableTotp"))
            if enable_captcha and not captcha:
                raise ValueError(
                    "OneSIG 设备已启用图形验证码，请通过 onesig_login(action='login', captcha='...') 提供。"
                )
            if enable_totp_inline and not totp:
                # Inline mode: the captcha endpoint already advertises
                # `enableTotp=true`, meaning the device wants the TOTP code on
                # the same login form (the `checksum` field) rather than the
                # post-login QR-scan flow. Refuse early so the caller does not
                # see an opaque 1017 / "checksum 不能为空" later.
                raise ValueError(
                    "OneSIG 设备已启用 inline TOTP（同屏「用户口令」），请通过 "
                    "onesig_login(action='login', totp='...') 提供动态口令或恢复码。"
                )

            pubkey_info = await self._raw_request_json("GET", "/v3/pubkey")
            pubkey_data = (
                pubkey_info.get("data", {}) if isinstance(pubkey_info, dict) else {}
            )
            pubkey = pubkey_data.get("pubkey")
            if not pubkey:
                raise ValueError(
                    f"无法从 /v3/pubkey 获取 RSA 公钥：{pubkey_info!r}"
                )

            try:
                encrypted_password = _rsa_oaep_encrypt(
                    pubkey, self.config.password, self.config.oaep_hash
                )
            except Exception as exc:
                raise ValueError(
                    f"使用 OAEP({self.config.oaep_hash}) 加密密码失败：{exc}"
                ) from exc

            payload: dict[str, Any] = {
                "username": self.config.username,
                "password": encrypted_password,
            }
            if enable_captcha and captcha:
                payload["captcha"] = captcha
            if enable_totp_inline and totp:
                payload["checksum"] = totp

            login_resp = await self._raw_request_json(
                "POST", "/v3/login", json_body=payload
            )
            if not isinstance(login_resp, dict):
                raise ValueError(f"登录返回非 JSON：{login_resp!r}")

            response_code = login_resp.get("responseCode")
            if response_code == _RESPONSE_CODE_TOTP_REQUIRED:
                if not totp:
                    raise ValueError(
                        "OneSIG 设备要求扫码 TOTP 二次验证，请在 login 调用中传入 `totp` 参数。"
                    )
                totp_resp = await self._raw_request_json(
                    "POST",
                    "/v3/login/totp",
                    json_body={"checksum": totp},
                )
                if (
                    not isinstance(totp_resp, dict)
                    or totp_resp.get("responseCode") != _RESPONSE_CODE_OK
                ):
                    raise ValueError(
                        f"TOTP 二次验证失败：{(totp_resp or {}).get('verboseMsg', totp_resp)}"
                    )
            elif response_code in (_RESPONSE_CODE_DEFAULT_PWD, _RESPONSE_CODE_PWD_EXPIRED):
                raise ValueError(
                    f"OneSIG 要求修改密码（responseCode={response_code}：{login_resp.get('verboseMsg')})。"
                    " 请先通过控制台或 `onesig_login(action='change_password', ...)` 修改密码。"
                )
            elif response_code != _RESPONSE_CODE_OK:
                raise ValueError(
                    f"OneSIG 登录失败（responseCode={response_code}）：{login_resp.get('verboseMsg')}"
                )

            self._logged_in = True
            account_resp = await self._raw_request_json("GET", "/v3/account")
            if (
                isinstance(account_resp, dict)
                and account_resp.get("responseCode") == _RESPONSE_CODE_OK
            ):
                return account_resp.get("data", {}) or {}
            return {}

    async def logout(self) -> dict[str, Any]:
        try:
            resp = await self._raw_request_json("POST", "/v3/logout", json_body={})
        finally:
            self._logged_in = False
        if isinstance(resp, dict):
            return resp
        return {}

    async def _raw_request_json(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
    ) -> Any:
        """Issue a raw request and parse the JSON envelope. Used for login/logout/account."""
        session = await self._ensure_session()
        url = self.config.build_url(path)
        request_params = {"lang": "zh"}
        if params:
            request_params.update({k: v for k, v in params.items() if v is not None})
        kwargs: dict[str, Any] = {
            "params": request_params,
            "timeout": aiohttp.ClientTimeout(total=self.config.timeout),
            "ssl": _ssl_context(self.config.verify_ssl),
        }
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {"Content-Type": "application/json"}
        async with session.request(method.upper(), url, **kwargs) as resp:
            text = await resp.text()
            try:
                return await resp.json(content_type=None)
            except Exception:
                return {"_status": resp.status, "_text": text[:500]}

    async def encrypt_with_pubkey(self, plain: str) -> str:
        """Fetch the latest /v3/pubkey and RSA-OAEP encrypt the given plaintext.

        Mirrors the front-end "clearRSACache → fresh pubkey → encrypt" pattern
        (`@/util/rsa.js`) used for sensitive write operations.
        """
        pubkey_resp = await self._raw_request_json("GET", "/v3/pubkey")
        pubkey = (pubkey_resp or {}).get("data", {}).get("pubkey")
        if not pubkey:
            raise ValueError(
                f"无法从 /v3/pubkey 获取 RSA 公钥用于字段加密：{pubkey_resp!r}"
            )
        return _rsa_oaep_encrypt(pubkey, plain, self.config.oaep_hash)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        form_data: Optional["aiohttp.FormData"] = None,
        captcha: Optional[str] = None,
        totp: Optional[str] = None,
        _retry: bool = True,
    ) -> tuple[int, dict[str, Any], bytes, str]:
        """Issue an authenticated request, auto-relogin on session expiry.

        Pass either ``json_body`` (JSON request) or ``form_data``
        (``multipart/form-data`` upload). Passing both raises ValueError.

        Returns ``(status, json_envelope, body_bytes, content_type)``. When the
        response is JSON, ``json_envelope`` contains the parsed payload and
        ``body_bytes`` is empty. When the response is binary, ``body_bytes``
        carries the raw bytes for the caller to persist.
        """
        if json_body is not None and form_data is not None:
            raise ValueError("request() cannot accept both json_body and form_data")

        if not self._logged_in:
            await self.login(captcha=captcha, totp=totp)

        session = await self._ensure_session()
        url = self.config.build_url(path)
        request_params: dict[str, Any] = {"lang": "zh"}
        if params:
            request_params.update({k: v for k, v in params.items() if v is not None})

        kwargs: dict[str, Any] = {
            "params": request_params,
            "timeout": aiohttp.ClientTimeout(total=self.config.timeout),
            "ssl": _ssl_context(self.config.verify_ssl),
        }
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {"Content-Type": "application/json"}
        elif form_data is not None:
            # aiohttp sets Content-Type with the boundary for FormData
            # automatically; do not override.
            kwargs["data"] = form_data

        async with session.request(method.upper(), url, **kwargs) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "") or ""
            if "application/json" in content_type:
                envelope = await resp.json(content_type=None)
                body_bytes = b""
            else:
                body_bytes = await resp.read()
                envelope = {}

        envelope_dict = envelope if isinstance(envelope, dict) else {"data": envelope}
        response_code = envelope_dict.get("responseCode") if envelope_dict else None
        session_expired = (
            status in _SESSION_EXPIRED_HTTP_STATUSES
            or response_code in _SESSION_EXPIRED_RESPONSE_CODES
        )
        if session_expired and _retry:
            self._logged_in = False
            await self.login(captcha=captcha, totp=totp, force=True)
            # form_data is single-shot; caller must rebuild on retry. We can
            # only retransmit JSON requests automatically.
            if form_data is not None:
                return status, envelope_dict, body_bytes, content_type
            return await self.request(
                method,
                path,
                params=params,
                json_body=json_body,
                captcha=captcha,
                totp=totp,
                _retry=False,
            )
        return status, envelope_dict, body_bytes, content_type


_SESSIONS: dict[str, OneSIGSession] = {}
_SESSIONS_LOCK = asyncio.Lock()


async def _get_session(config: OneSIGRuntimeConfig) -> OneSIGSession:
    async with _SESSIONS_LOCK:
        sess = _SESSIONS.get(config.session_key)
        if sess is None:
            sess = OneSIGSession(config)
            _SESSIONS[config.session_key] = sess
        else:
            sess.config = config
        return sess


# ---------------------------------------------------------------------------
# Action specifications
# ---------------------------------------------------------------------------


_RESERVED_PARAM_KEYS = frozenset({"action", "captcha", "totp", "file_path"})


class ActionSpec:
    """Declarative spec for a single OneSIG endpoint action."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        body_keys: Optional[list[str]] = None,
        query_keys: Optional[list[str]] = None,
        passthrough_body: bool = False,
        binary: bool = False,
        required: Optional[list[str]] = None,
        multipart: bool = False,
        multipart_file_field: str = "file",
        encrypt_fields: Optional[tuple[str, ...]] = None,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.body_keys = body_keys or []
        self.query_keys = query_keys or []
        self.passthrough_body = passthrough_body
        self.binary = binary
        self.required = required or []
        # multipart=True: send the body as multipart/form-data; the local file
        # path comes from the reserved `file_path` param and is uploaded under
        # `multipart_file_field` (default `file`, per OneSIG docs).
        self.multipart = multipart
        self.multipart_file_field = multipart_file_field
        # Body fields whose plaintext value must be RSA-OAEP encrypted with the
        # current /v3/pubkey before being sent (typical: ("password",) or
        # ("password", "dupPassword")).
        self.encrypt_fields = tuple(encrypt_fields or ())

    def build_request(
        self, params: dict[str, Any]
    ) -> tuple[Optional[dict[str, Any]], Optional[Any]]:
        query = {k: params[k] for k in self.query_keys if params.get(k) is not None}

        body: Optional[Any] = None
        if self.method == "GET" and not self.multipart:
            for key in self.body_keys:
                if params.get(key) is not None:
                    query[key] = params[key]
        else:
            if self.passthrough_body:
                body = {
                    k: v
                    for k, v in params.items()
                    if k not in _RESERVED_PARAM_KEYS
                    and k not in self.query_keys
                    and v is not None
                }
            else:
                body = {k: params[k] for k in self.body_keys if params.get(k) is not None}
        return (query or None), body


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _validate_required(spec: ActionSpec, action: str, params: dict[str, Any]) -> Optional[str]:
    missing = [k for k in spec.required if not _has_value(params.get(k))]
    if missing:
        return f"Missing required parameters for {action}: {', '.join(missing)}"
    return None


# Login / Account / Password ------------------------------------------------

LOGIN_ACTION_SPECS: dict[str, ActionSpec] = {
    "get_captcha": ActionSpec("GET", "/v3/captcha"),
    "get_pubkey": ActionSpec("GET", "/v3/pubkey"),
    "get_account": ActionSpec("GET", "/v3/account"),
    "get_recovery_code": ActionSpec("GET", "/v3/user/recoveryCode"),
    "regenerate_recovery_code": ActionSpec("PUT", "/v3/user/recoveryCode"),
    "get_product_news": ActionSpec("GET", "/v3/product/news"),
    "mark_product_news_read": ActionSpec(
        "PUT", "/v3/product/news", passthrough_body=True
    ),
}

# Monitoring ---------------------------------------------------------------

MONITORING_ACTION_SPECS: dict[str, ActionSpec] = {
    # dashboard
    "dashboard_overview": ActionSpec("POST", "/v3/dashboard/overview", passthrough_body=True),
    "dashboard_outbound": ActionSpec("POST", "/v3/dashboard/outbound", passthrough_body=True),
    "dashboard_inbound": ActionSpec("POST", "/v3/dashboard/inbound", passthrough_body=True),
    "dashboard_zeroday": ActionSpec("POST", "/v3/dashboard/zeroday", passthrough_body=True),
    "dashboard_status": ActionSpec("GET", "/v3/dashboard/status"),
    "dashboard_ioc_type_sum": ActionSpec("GET", "/v3/dashboard/ioctypesum"),
    "set_custom_config": ActionSpec("PUT", "/v3/setting/customConfig", passthrough_body=True),
    # overview
    "common_threat_type_list": ActionSpec("GET", "/v3/common/threatTypeList"),
    "overview_event_inbound": ActionSpec("POST", "/v3/overview/eventInbound", passthrough_body=True),
    "overview_event_outbound": ActionSpec("POST", "/v3/overview/eventOutbound", passthrough_body=True),
    "overview_export_event_inbound": ActionSpec(
        "POST", "/v3/overview/exportEventInbound", passthrough_body=True, binary=True
    ),
    "overview_export_event_outbound": ActionSpec(
        "POST", "/v3/overview/exportEventOutbound", passthrough_body=True, binary=True
    ),
    "overview_asset_brief": ActionSpec("POST", "/v3/overview/assetBrief", passthrough_body=True),
    "overview_asset_top": ActionSpec("POST", "/v3/overview/assetTop", passthrough_body=True),
    "overview_event_inbound_agg": ActionSpec("POST", "/v3/overview/eventInboundAgg", passthrough_body=True),
    "overview_export_event_inbound_agg": ActionSpec(
        "POST", "/v3/overview/exportEventInboundAgg", passthrough_body=True, binary=True
    ),
    "overview_event_outbound_agg": ActionSpec("POST", "/v3/overview/eventOutboundAgg", passthrough_body=True),
    "overview_export_event_outbound_agg": ActionSpec(
        "POST", "/v3/overview/exportEventOutboundAgg", passthrough_body=True, binary=True
    ),
    "overview_event_recent_agg": ActionSpec("POST", "/v3/overview/eventRecentAgg", passthrough_body=True),
    "overview_event_trend": ActionSpec("POST", "/v3/overview/eventTrend", passthrough_body=True),
    "overview_traffic_trend": ActionSpec("POST", "/v3/overview/trafficTrend", passthrough_body=True),
    "overview_stat": ActionSpec("POST", "/v3/overview/stat", passthrough_body=True),
    "overview_threat_type_proportion": ActionSpec(
        "POST", "/v3/overview/threatTypeProportion", passthrough_body=True
    ),
    "overview_ioc_type_proportion": ActionSpec(
        "POST", "/v3/overview/iocTypeProportion", passthrough_body=True
    ),
    "overview_export_threat_type_proportion": ActionSpec(
        "POST", "/v3/overview/exportThreatTypeProportion", passthrough_body=True, binary=True
    ),
    "overview_export_ioc_type_proportion": ActionSpec(
        "POST", "/v3/overview/exportIocTypeProportion", passthrough_body=True, binary=True
    ),
    "get_overview_config": ActionSpec("GET", "/v3/setting/overviewConfig"),
    "set_overview_config": ActionSpec("PUT", "/v3/setting/overviewConfig", passthrough_body=True),
    # status
    "device_platform_status": ActionSpec("GET", "/v3/device/platformStatus"),
    "device_system_status": ActionSpec("GET", "/v3/device/systemStatus"),
    "device_network_status": ActionSpec("GET", "/v3/device/networkStatus"),
    "common_interface_list": ActionSpec("GET", "/v3/common/interfaceList"),
    "basic_cpu_attr": ActionSpec("GET", "/v3/basic/cpuAttr"),
    # alert hosts
    "alert_host_stat": ActionSpec(
        "GET", "/v3/alertHost/stat", body_keys=["startTime", "endTime", "assetGroup"]
    ),
    "alert_host_tree": ActionSpec("POST", "/v3/alertHost/tree", passthrough_body=True),
    "alert_host_list": ActionSpec(
        "POST",
        "/v3/alertHost/list",
        passthrough_body=True,
        required=["startTime", "endTime"],
    ),
    "alert_host_export": ActionSpec(
        "POST", "/v3/alertHost/export", passthrough_body=True, binary=True
    ),
    "alert_host_detail": ActionSpec("POST", "/v3/alertHost/detail", passthrough_body=True),
    "alert_host_detail_list": ActionSpec(
        "POST", "/v3/alertHost/detail/list", passthrough_body=True
    ),
    "alert_host_detail_export": ActionSpec(
        "POST", "/v3/alertHost/detail/export", passthrough_body=True, binary=True
    ),
    "common_asset_type_list": ActionSpec("GET", "/v3/common/assetTypeList"),
    # inbound / outbound threats
    "event_inbound_stat": ActionSpec(
        "GET", "/v3/event/inbound/stat", body_keys=["startTime", "endTime", "assetGroup"]
    ),
    "event_inbound_list": ActionSpec(
        "POST",
        "/v3/event/inbound/list",
        passthrough_body=True,
        required=["startTime", "endTime"],
    ),
    "event_inbound_export": ActionSpec(
        "POST", "/v3/event/inbound/export", passthrough_body=True, binary=True
    ),
    "event_inbound_detail": ActionSpec("POST", "/v3/event/inbound/detail", passthrough_body=True),
    "event_inbound_detail_trend": ActionSpec(
        "POST", "/v3/event/inbound/detail/trend", passthrough_body=True
    ),
    "event_inbound_detail_list": ActionSpec(
        "POST", "/v3/event/inbound/detail/list", passthrough_body=True
    ),
    "event_inbound_detail_export": ActionSpec(
        "POST", "/v3/event/inbound/detail/export", passthrough_body=True, binary=True
    ),
    "port_protect_group_list": ActionSpec(
        "POST", "/v3/portProtectGroup/list", passthrough_body=True
    ),
    "web_custom_column_set": ActionSpec(
        "PUT", "/v3/webCustomColumn/set", passthrough_body=True
    ),
    "event_outbound_stat": ActionSpec(
        "GET", "/v3/event/outbound/stat", body_keys=["startTime", "endTime", "assetGroup"]
    ),
    "event_outbound_list": ActionSpec(
        "POST",
        "/v3/event/outbound/list",
        passthrough_body=True,
        required=["startTime", "endTime"],
    ),
    "event_outbound_export": ActionSpec(
        "POST", "/v3/event/outbound/export", passthrough_body=True, binary=True
    ),
    "event_outbound_detail": ActionSpec("POST", "/v3/event/outbound/detail", passthrough_body=True),
    "event_outbound_detail_trend": ActionSpec(
        "POST", "/v3/event/outbound/detail/trend", passthrough_body=True
    ),
    "event_outbound_detail_list": ActionSpec(
        "POST", "/v3/event/outbound/detail/list", passthrough_body=True
    ),
    "event_outbound_detail_export": ActionSpec(
        "POST", "/v3/event/outbound/detail/export", passthrough_body=True, binary=True
    ),
    "set_dnslog_config": ActionSpec(
        "PUT", "/v3/setting/dnslogConfig", passthrough_body=True
    ),
    # report
    "get_notice_config": ActionSpec("GET", "/v3/setting/noticeConfig"),
    "report_form_create": ActionSpec("POST", "/v3/report/form", passthrough_body=True),
    "report_form_list": ActionSpec(
        "POST", "/v3/report/form/list", passthrough_body=True
    ),
    "report_form_download": ActionSpec(
        "GET",
        "/v3/report/form/download",
        body_keys=["uniqueId", "fileName"],
        binary=True,
    ),
    "report_form_delete": ActionSpec("DELETE", "/v3/report/form", passthrough_body=True),
    "report_task_list": ActionSpec(
        "POST", "/v3/report/task/list", passthrough_body=True
    ),
    "report_task_create": ActionSpec("POST", "/v3/report/task", passthrough_body=True),
    "report_task_update": ActionSpec("PUT", "/v3/report/task", passthrough_body=True),
    "report_task_delete": ActionSpec("DELETE", "/v3/report/task", passthrough_body=True),
    "report_task_test": ActionSpec("POST", "/v3/report/task/test", passthrough_body=True),
    # shared monitoring
    "common_asset_group_tree": ActionSpec("GET", "/v3/common/assetGroupTree"),
    "ips_rule_create": ActionSpec("POST", "/v3/ips/rule", passthrough_body=True),
    "ips_rule_apply": ActionSpec("POST", "/v3/ips/rule/apply", passthrough_body=True),
    "ips_ruleset_namelist": ActionSpec("POST", "/v3/ips/ruleset/namelist", passthrough_body=True),
    "ips_ruleset_referred": ActionSpec("POST", "/v3/ips/ruleset/referred", passthrough_body=True),
    "logaccess_stat": ActionSpec("GET", "/v3/logAccess/stat"),
    "get_dnslog_config": ActionSpec("GET", "/v3/setting/dnslogConfig"),
}

# Strategy ----------------------------------------------------------------

STRATEGY_ACTION_SPECS: dict[str, ActionSpec] = {
    # whitelist
    "whitelist_add": ActionSpec("POST", "/v3/globalWhitelist", passthrough_body=True),
    "whitelist_update": ActionSpec("PUT", "/v3/globalWhitelist", passthrough_body=True),
    "whitelist_delete": ActionSpec("DELETE", "/v3/globalWhitelist", passthrough_body=True),
    "whitelist_export": ActionSpec(
        "POST", "/v3/globalWhitelist/export", passthrough_body=True, binary=True
    ),
    "whitelist_import": ActionSpec(
        "POST", "/v3/globalWhitelist/import", passthrough_body=True
    ),
    "whitelist_template": ActionSpec(
        "GET", "/v3/globalWhitelist/template", binary=True
    ),
    "whitelist_list": ActionSpec("POST", "/v3/globalWhitelist/list", passthrough_body=True),
    "whitelist_remove_batch": ActionSpec(
        "DELETE", "/v3/globalWhitelist/remove", passthrough_body=True
    ),
    # blacklist
    "blacklist_location_options": ActionSpec("GET", "/v3/blacklist/location"),
    "blacklist_add": ActionSpec("POST", "/v3/globalBlacklist", passthrough_body=True),
    "blacklist_update": ActionSpec("PUT", "/v3/globalBlacklist", passthrough_body=True),
    "blacklist_delete": ActionSpec("DELETE", "/v3/globalBlacklist", passthrough_body=True),
    "blacklist_check": ActionSpec("POST", "/v3/globalBlacklist/check", passthrough_body=True),
    "blacklist_export": ActionSpec(
        "POST", "/v3/globalBlacklist/export", passthrough_body=True, binary=True
    ),
    "blacklist_import": ActionSpec("POST", "/v3/globalBlacklist/import", passthrough_body=True),
    "blacklist_template": ActionSpec("GET", "/v3/globalBlacklist/template", binary=True),
    "blacklist_list": ActionSpec("POST", "/v3/globalBlacklist/list", passthrough_body=True),
    "blacklist_remove_batch": ActionSpec(
        "DELETE", "/v3/globalBlacklist/remove", passthrough_body=True
    ),
    # multi-block
    "multiblock_executelog_list": ActionSpec(
        "POST", "/v3/multiblock/executelog", passthrough_body=True
    ),
    "multiblock_executelog_export": ActionSpec(
        "POST", "/v3/multiblock/executelog/export", passthrough_body=True, binary=True
    ),
    "multiblock_rule_delete": ActionSpec(
        "DELETE", "/v3/multiblock/rule", passthrough_body=True
    ),
    "multiblock_rule_active": ActionSpec(
        "POST", "/v3/multiblock/rule/active", passthrough_body=True
    ),
    "multiblock_rule_dict": ActionSpec(
        "POST", "/v3/multiblock/rule/dict", passthrough_body=True
    ),
    "multiblock_rule_list": ActionSpec(
        "POST", "/v3/multiblock/rule/list", passthrough_body=True
    ),
    "multiblock_rule_get": ActionSpec(
        "POST", "/v3/multiblock/rule/get", passthrough_body=True
    ),
    "multiblock_rule_preview": ActionSpec(
        "POST", "/v3/multiblock/rule/preview", passthrough_body=True
    ),
    "multiblock_rule_create": ActionSpec(
        "POST", "/v3/multiblock/rule", passthrough_body=True
    ),
    "multiblock_rule_update": ActionSpec(
        "PUT", "/v3/multiblock/rule", passthrough_body=True
    ),
    # api keys
    "apikey_delete": ActionSpec("DELETE", "/v3/apikey", passthrough_body=True),
    "apikey_update": ActionSpec("PUT", "/v3/apikey", passthrough_body=True),
    "apikey_create": ActionSpec("POST", "/v3/apikey", passthrough_body=True),
    "apikey_list": ActionSpec("GET", "/v3/apikey/list"),
    "apikey_secret": ActionSpec("GET", "/v3/apikey/secret", body_keys=["uniqueId"]),
    # syslog auto-blacklist
    "auto_blacklist_delete": ActionSpec(
        "DELETE", "/v3/autoBlacklist", passthrough_body=True
    ),
    "auto_blacklist_check": ActionSpec(
        "POST", "/v3/autoBlacklist/check", passthrough_body=True
    ),
    "auto_blacklist_create": ActionSpec(
        "POST", "/v3/autoBlacklist", passthrough_body=True
    ),
    "auto_blacklist_update": ActionSpec(
        "PUT", "/v3/autoBlacklist", passthrough_body=True
    ),
    "auto_blacklist_list": ActionSpec(
        "POST", "/v3/autoBlacklist/list", passthrough_body=True
    ),
    "auto_blacklist_trend": ActionSpec(
        "GET", "/v3/autoBlacklist/trend", body_keys=["startTime", "endTime"]
    ),
    "auto_blacklist_sample": ActionSpec(
        "POST", "/v3/autoBlacklist/sample", passthrough_body=True
    ),
    # ftp/sftp linkage
    "linkage_delete": ActionSpec("DELETE", "/v3/linkage", passthrough_body=True),
    "linkage_create": ActionSpec("POST", "/v3/linkage", passthrough_body=True),
    "linkage_update": ActionSpec("PUT", "/v3/linkage", passthrough_body=True),
    "linkage_enable": ActionSpec("POST", "/v3/linkage/enable", passthrough_body=True),
    "linkage_info": ActionSpec("GET", "/v3/linkage/info", body_keys=["uniqueId"]),
    "linkage_list": ActionSpec("POST", "/v3/linkage/list", passthrough_body=True),
    "linkage_template": ActionSpec("GET", "/v3/linkage/template", binary=True),
    "linkage_test": ActionSpec("POST", "/v3/linkage/test", passthrough_body=True),
    # IPS
    "ips_rule_create": ActionSpec("POST", "/v3/ips/rule", passthrough_body=True),
    "ips_rule_all": ActionSpec("POST", "/v3/ips/rule/all", passthrough_body=True),
    "ips_rule_apply": ActionSpec("POST", "/v3/ips/rule/apply", passthrough_body=True),
    "ips_rule_list": ActionSpec("POST", "/v3/ips/rule/list", passthrough_body=True),
    "ips_ruleset_create": ActionSpec("POST", "/v3/ips/ruleset", passthrough_body=True),
    "ips_ruleset_update": ActionSpec("PUT", "/v3/ips/ruleset", passthrough_body=True),
    "ips_ruleset_delete": ActionSpec("DELETE", "/v3/ips/ruleset", passthrough_body=True),
    "ips_ruleset_info": ActionSpec("POST", "/v3/ips/ruleset/info", passthrough_body=True),
    "ips_ruleset_list": ActionSpec("POST", "/v3/ips/ruleset/list", passthrough_body=True),
    "ips_ruleset_namelist": ActionSpec(
        "POST", "/v3/ips/ruleset/namelist", passthrough_body=True
    ),
    "ips_threat_types": ActionSpec("POST", "/v3/ips/threatTypes", passthrough_body=True),
    # HTTP protect (HTTP blacklist)
    "http_blacklist_delete": ActionSpec(
        "DELETE", "/v3/httpBlacklist", passthrough_body=True
    ),
    "http_blacklist_enable": ActionSpec(
        "POST", "/v3/httpBlacklist/enable", passthrough_body=True
    ),
    "http_blacklist_export": ActionSpec(
        "POST", "/v3/httpBlacklist/export", passthrough_body=True, binary=True
    ),
    "http_blacklist_list": ActionSpec(
        "POST", "/v3/httpBlacklist/list", passthrough_body=True
    ),
    "http_blacklist_create": ActionSpec(
        "POST", "/v3/httpBlacklist", passthrough_body=True
    ),
    "http_blacklist_update": ActionSpec(
        "PUT", "/v3/httpBlacklist", passthrough_body=True
    ),
    "get_advanced_config": ActionSpec("GET", "/v3/setting/advancedConfig"),
    "set_advanced_config": ActionSpec("PUT", "/v3/setting/advancedConfig", passthrough_body=True),
    "get_xff_config": ActionSpec("GET", "/v3/setting/xffConfig"),
    "set_xff_config": ActionSpec("PUT", "/v3/setting/xffConfig", passthrough_body=True),
    # port protect groups
    "port_protect_group_delete": ActionSpec(
        "DELETE", "/v3/portProtectGroup", passthrough_body=True
    ),
    "port_protect_group_create": ActionSpec(
        "POST", "/v3/portProtectGroup", passthrough_body=True
    ),
    "port_protect_group_update": ActionSpec(
        "PUT", "/v3/portProtectGroup", passthrough_body=True
    ),
    "port_protect_group_clone": ActionSpec(
        "POST", "/v3/portProtectGroup/clone", passthrough_body=True
    ),
    "port_protect_group_default_info": ActionSpec(
        "GET", "/v3/portProtectGroup/defaultInfo"
    ),
    "port_protect_group_list_full": ActionSpec(
        "POST", "/v3/portProtectGroup/list", passthrough_body=True
    ),
    "port_protect_port_delete": ActionSpec(
        "DELETE", "/v3/portProtectGroup/port", passthrough_body=True
    ),
    "port_protect_port_create": ActionSpec(
        "POST", "/v3/portProtectGroup/port", passthrough_body=True
    ),
    "port_protect_port_update": ActionSpec(
        "PUT", "/v3/portProtectGroup/port", passthrough_body=True
    ),
    "port_protect_port_export": ActionSpec(
        "POST", "/v3/portProtectGroup/port/export", passthrough_body=True, binary=True
    ),
    "port_protect_port_list": ActionSpec(
        "POST", "/v3/portProtectGroup/port/list", passthrough_body=True
    ),
    "port_protect_port_onekey_import": ActionSpec(
        "POST", "/v3/portProtectGroup/port/onekeyImport", passthrough_body=True
    ),
    "port_protect_port_onekey_status": ActionSpec(
        "GET", "/v3/portProtectGroup/port/onekeyImport"
    ),
    "port_protect_portinfo": ActionSpec(
        "POST", "/v3/portProtectGroup/portinfo", passthrough_body=True
    ),
    # strategy page (custom protection policy)
    "device_onekey_bypass": ActionSpec(
        "POST", "/v3/device/onekeyBypass", passthrough_body=True
    ),
    "protection_policy_delete": ActionSpec(
        "DELETE", "/v3/protection/policy", passthrough_body=True
    ),
    "protection_policy_update": ActionSpec(
        "PUT", "/v3/protection/policy", passthrough_body=True
    ),
    "protection_policy_get": ActionSpec(
        "GET", "/v3/protection/policy", body_keys=["uniqueId"]
    ),
    "protection_policy_tree": ActionSpec("GET", "/v3/protection/policy/tree"),
    "set_scan_config": ActionSpec("PUT", "/v3/setting/scanConfig", passthrough_body=True),
}

# Assets ------------------------------------------------------------------

ASSETS_ACTION_SPECS: dict[str, ActionSpec] = {
    "asset_delete": ActionSpec("DELETE", "/v3/asset", passthrough_body=True),
    "asset_create": ActionSpec("POST", "/v3/asset", passthrough_body=True),
    "asset_update": ActionSpec("PUT", "/v3/asset", passthrough_body=True),
    "asset_export": ActionSpec("POST", "/v3/asset/export", passthrough_body=True, binary=True),
    "asset_group_delete": ActionSpec("DELETE", "/v3/asset/group", passthrough_body=True),
    "asset_group_get": ActionSpec("GET", "/v3/asset/group"),
    "asset_group_create": ActionSpec("POST", "/v3/asset/group", passthrough_body=True),
    "asset_group_update": ActionSpec("PUT", "/v3/asset/group", passthrough_body=True),
    "asset_import": ActionSpec(
        "POST", "/v3/asset/import", passthrough_body=True, multipart=True
    ),
    "asset_template": ActionSpec("GET", "/v3/asset/template", binary=True),
    "asset_list": ActionSpec("POST", "/v3/asset/list", passthrough_body=True),
    "asset_type_delete": ActionSpec("DELETE", "/v3/asset/type", passthrough_body=True),
    "asset_type_get": ActionSpec("GET", "/v3/asset/type"),
    "asset_type_create": ActionSpec("POST", "/v3/asset/type", passthrough_body=True),
    "common_asset_group_tree": ActionSpec("GET", "/v3/common/assetGroupTree"),
}

# Device ------------------------------------------------------------------

DEVICE_ACTION_SPECS: dict[str, ActionSpec] = {
    # alert policy
    "alert_policy_list": ActionSpec(
        "POST", "/v3/alert/policy/list", passthrough_body=True
    ),
    "alert_policy_enable": ActionSpec(
        "POST", "/v3/alert/policy/enable", passthrough_body=True
    ),
    "alert_policy_delete": ActionSpec(
        "DELETE", "/v3/alert/policy", passthrough_body=True
    ),
    "alert_policy_create": ActionSpec(
        "POST", "/v3/alert/policy", passthrough_body=True
    ),
    "alert_policy_update": ActionSpec(
        "PUT", "/v3/alert/policy", passthrough_body=True
    ),
    "alert_policy_export": ActionSpec(
        "POST", "/v3/alert/policy/export", passthrough_body=True, binary=True
    ),
    "alert_policy_find_by_config": ActionSpec(
        "POST", "/v3/alert/policy/findByConfig", passthrough_body=True
    ),
    "alert_policy_object": ActionSpec(
        "POST", "/v3/alert/policy/object", passthrough_body=True
    ),
    "get_notice_config": ActionSpec("GET", "/v3/setting/noticeConfig"),
    "set_notice_config": ActionSpec("PUT", "/v3/setting/noticeConfig", passthrough_body=True),
    "get_notice_send_key": ActionSpec("GET", "/v3/setting/noticeConfig/sendKey"),
    "test_email": ActionSpec("POST", "/v3/test/email", passthrough_body=True),
    "test_syslog": ActionSpec("POST", "/v3/test/syslog", passthrough_body=True),
    "test_webhook": ActionSpec("POST", "/v3/test/webhook", passthrough_body=True),
    # audit logs
    "aclog_stat": ActionSpec("GET", "/v3/aclog/stat", body_keys=["startTime", "endTime"]),
    "aclog_list": ActionSpec("POST", "/v3/aclog/list", passthrough_body=True),
    "aclog_export": ActionSpec(
        "POST", "/v3/aclog/export", passthrough_body=True, binary=True
    ),
    "aclog_delete": ActionSpec(
        "DELETE",
        "/v3/aclog",
        passthrough_body=True,
        encrypt_fields=("password",),
    ),
    "get_clean_config": ActionSpec("GET", "/v3/setting/cleanConfig"),
    "set_clean_config": ActionSpec("PUT", "/v3/setting/cleanConfig", passthrough_body=True),
    # users / login mgmt
    "user_list": ActionSpec("POST", "/v3/user/list", passthrough_body=True),
    "user_export": ActionSpec(
        "POST", "/v3/user/export", passthrough_body=True, binary=True
    ),
    "user_delete": ActionSpec(
        "DELETE",
        "/v3/user",
        passthrough_body=True,
        encrypt_fields=("password",),
    ),
    "user_secret_reset": ActionSpec(
        "PUT",
        "/v3/user/secret/reset",
        passthrough_body=True,
        encrypt_fields=("password",),
    ),
    "user_create": ActionSpec(
        "POST",
        "/v3/user",
        passthrough_body=True,
        encrypt_fields=("password", "dupPassword"),
    ),
    "user_update": ActionSpec("PUT", "/v3/user", passthrough_body=True),
    "get_login_config": ActionSpec("GET", "/v3/setting/loginConfig"),
    "set_login_config": ActionSpec("PUT", "/v3/setting/loginConfig", passthrough_body=True),
    # HTTPS decryption
    "get_decrypt_config": ActionSpec("GET", "/v3/setting/decryptConfig"),
    "set_decrypt_config": ActionSpec("PUT", "/v3/setting/decryptConfig", passthrough_body=True),
    "get_detect_config": ActionSpec("GET", "/v3/setting/detectConfig"),
    "set_detect_config": ActionSpec("PUT", "/v3/setting/detectConfig", passthrough_body=True),
    "tls_decrypt_policy_list": ActionSpec(
        "POST", "/v3/tls/decrypt/policy/list", passthrough_body=True
    ),
    "tls_decrypt_policy_create": ActionSpec(
        "POST", "/v3/tls/decrypt/policy", passthrough_body=True
    ),
    "tls_decrypt_policy_update": ActionSpec(
        "PUT", "/v3/tls/decrypt/policy", passthrough_body=True
    ),
    "tls_decrypt_policy_enable": ActionSpec(
        "POST", "/v3/tls/decrypt/policy/enable", passthrough_body=True
    ),
    "tls_decrypt_policy_delete": ActionSpec(
        "DELETE", "/v3/tls/decrypt/policy", passthrough_body=True
    ),
    "tls_decrypt_policy_batch": ActionSpec(
        "POST", "/v3/tls/decrypt/policy/batch", passthrough_body=True
    ),
    "tls_cert_list": ActionSpec("POST", "/v3/tls/cert/list", passthrough_body=True),
    "tls_cert_create": ActionSpec(
        "POST", "/v3/tls/cert", passthrough_body=True, multipart=True,
        multipart_file_field="certFile",
    ),
    "tls_cert_update": ActionSpec(
        "PUT", "/v3/tls/cert", passthrough_body=True, multipart=True,
        multipart_file_field="certFile",
    ),
    "tls_cert_delete": ActionSpec("DELETE", "/v3/tls/cert", passthrough_body=True),
    "tls_cert_set_default": ActionSpec(
        "POST", "/v3/tls/cert/set_default", passthrough_body=True
    ),
    "tls_detect_list": ActionSpec("POST", "/v3/tls/detect/list", passthrough_body=True),
    "tls_detect_list_detail": ActionSpec(
        "POST", "/v3/tls/detect/list/detail", passthrough_body=True
    ),
    "tls_detect_delete": ActionSpec("DELETE", "/v3/tls/detect", passthrough_body=True),
    "tls_detect_group": ActionSpec("POST", "/v3/tls/detect/group", passthrough_body=True),
    "tls_detect_group_export": ActionSpec(
        "POST", "/v3/tls/detect/group/export", passthrough_body=True, binary=True
    ),
    "tls_detect_list_export": ActionSpec(
        "POST", "/v3/tls/detect/list/export", passthrough_body=True, binary=True
    ),
    # deploy guide / interface
    "interface_list": ActionSpec("GET", "/v3/interface/list"),
    "interface_update": ActionSpec(
        "PUT",
        "/v3/interface",
        passthrough_body=True,
        encrypt_fields=("password",),
    ),
    "interface_check_loop": ActionSpec(
        "POST", "/v3/interface/check/loop", passthrough_body=True
    ),
    "interface_relation_list": ActionSpec("GET", "/v3/interface/relation/list"),
    "interface_select_list": ActionSpec("GET", "/v3/interface/select/list"),
    "interface_virtual_line_create": ActionSpec(
        "POST", "/v3/interface/virtualLine", passthrough_body=True
    ),
    "interface_virtual_line_update": ActionSpec(
        "PUT", "/v3/interface/virtualLine", passthrough_body=True
    ),
    "interface_virtual_line_delete": ActionSpec(
        "DELETE", "/v3/interface/virtualLine", passthrough_body=True
    ),
    "interface_listen_create": ActionSpec(
        "POST", "/v3/interface/listen", passthrough_body=True
    ),
    "interface_listen_update": ActionSpec(
        "PUT", "/v3/interface/listen", passthrough_body=True
    ),
    "interface_listen_delete": ActionSpec(
        "DELETE", "/v3/interface/listen", passthrough_body=True
    ),
    "interface_bridge_create": ActionSpec(
        "POST", "/v3/interface/bridge", passthrough_body=True
    ),
    "interface_bridge_update": ActionSpec(
        "PUT", "/v3/interface/bridge", passthrough_body=True
    ),
    "interface_bridge_delete": ActionSpec(
        "DELETE", "/v3/interface/bridge", passthrough_body=True
    ),
    # routes
    "route_outif_list": ActionSpec("GET", "/v3/route/outIf/list"),
    "route_static_list": ActionSpec("POST", "/v3/route/static/list", passthrough_body=True),
    "route_static_create": ActionSpec("POST", "/v3/route/static", passthrough_body=True),
    "route_static_update": ActionSpec("PUT", "/v3/route/static", passthrough_body=True),
    "route_static_delete": ActionSpec(
        "DELETE", "/v3/route/static", passthrough_body=True
    ),
    "route_table_list": ActionSpec("POST", "/v3/route/table/list", passthrough_body=True),
    "ipv6_route_static_list": ActionSpec(
        "POST", "/v3/ipv6Route/static/list", passthrough_body=True
    ),
    "ipv6_route_static_create": ActionSpec(
        "POST", "/v3/ipv6Route/static", passthrough_body=True
    ),
    "ipv6_route_static_update": ActionSpec(
        "PUT", "/v3/ipv6Route/static", passthrough_body=True
    ),
    "ipv6_route_static_delete": ActionSpec(
        "DELETE", "/v3/ipv6Route/static", passthrough_body=True
    ),
    "ipv6_route_table_list": ActionSpec(
        "POST", "/v3/ipv6Route/table/list", passthrough_body=True
    ),
    # DNS config
    "get_dns_config": ActionSpec("GET", "/v3/setting/dnsConfig"),
    "set_dns_config": ActionSpec("PUT", "/v3/setting/dnsConfig", passthrough_body=True),
    "hosts_get": ActionSpec("GET", "/v3/setting/hosts"),
    "hosts_create": ActionSpec("POST", "/v3/setting/hosts", passthrough_body=True),
    "hosts_update": ActionSpec("PUT", "/v3/setting/hosts", passthrough_body=True),
    "hosts_delete": ActionSpec("DELETE", "/v3/setting/hosts", passthrough_body=True),
    "test_network": ActionSpec("GET", "/v3/test/network"),
    # proxy / agent
    "get_proxy_config": ActionSpec("GET", "/v3/setting/proxyConfig"),
    "set_proxy_config": ActionSpec("PUT", "/v3/setting/proxyConfig", passthrough_body=True),
    "test_proxy": ActionSpec("POST", "/v3/test/proxy", passthrough_body=True),
    # HA
    "ha_status": ActionSpec("GET", "/v3/ha/status"),
    "get_ha_config": ActionSpec("GET", "/v3/setting/haConfig"),
    "set_ha_config": ActionSpec("PUT", "/v3/setting/haConfig", passthrough_body=True),
    "ha_module_list": ActionSpec("GET", "/v3/ha/moduleList"),
    "ha_compare_config": ActionSpec("POST", "/v3/ha/compareConfig", passthrough_body=True),
    "ha_switching": ActionSpec("PUT", "/v3/ha/switching", passthrough_body=True),
    "ha_sync_config": ActionSpec("POST", "/v3/ha/syncConfig", passthrough_body=True),
    "ha_sync_status": ActionSpec("GET", "/v3/ha/syncStatus"),
    # centralized control (OneCC)
    "onecc_status": ActionSpec("GET", "/v3/setting/oneccConfig/status"),
    "get_onecc_config": ActionSpec("GET", "/v3/setting/oneccConfig"),
    "set_onecc_config": ActionSpec("PUT", "/v3/setting/oneccConfig", passthrough_body=True),
    "set_onecc_status": ActionSpec("PUT", "/v3/setting/oneccConfig/status", passthrough_body=True),
    "test_onecc": ActionSpec("POST", "/v3/test/onecc", passthrough_body=True),
    # device config
    "device_quick_bypass": ActionSpec(
        "POST", "/v3/device/quickBypass", passthrough_body=True
    ),
    "device_upgrade_record_list": ActionSpec(
        "POST", "/v3/device/upgradeRecord/list", passthrough_body=True
    ),
    "get_upgrade_config": ActionSpec("GET", "/v3/setting/upgradeConfig"),
    "set_upgrade_config": ActionSpec(
        "PUT", "/v3/setting/upgradeConfig", passthrough_body=True
    ),
    "basic_version": ActionSpec("GET", "/v3/basic/version"),
    "device_upgrade_info": ActionSpec("GET", "/v3/device/upgradeInfo"),
    "device_download_package": ActionSpec(
        "POST", "/v3/device/downloadPackage", passthrough_body=True
    ),
    # /v3/device/upgrade has two flavors:
    #   * 已下载包升级: JSON body with `name` (Query) + `password` (RSA);
    #   * 本地上传包: multipart with file + `password`.
    # Default to JSON; supply `file_path` to use multipart.
    "device_upgrade": ActionSpec(
        "POST",
        "/v3/device/upgrade",
        passthrough_body=True,
        encrypt_fields=("password",),
    ),
    "device_upgrade_upload": ActionSpec(
        "POST",
        "/v3/device/upgrade",
        passthrough_body=True,
        multipart=True,
        encrypt_fields=("password",),
    ),
    "system_upgrade": ActionSpec(
        "POST", "/v3/system/upgrade", passthrough_body=True, multipart=True
    ),
    "device_custom_get": ActionSpec("GET", "/v3/device/custom"),
    "device_custom_set": ActionSpec("PUT", "/v3/device/custom", passthrough_body=True),
    "device_reboot": ActionSpec("POST", "/v3/device/reboot", passthrough_body=True),
    "device_shutdown": ActionSpec("POST", "/v3/device/shutdown", passthrough_body=True),
    "device_reinit": ActionSpec("POST", "/v3/device/reinit", passthrough_body=True),
    "device_system_timezone": ActionSpec("GET", "/v3/device/systemTimeZone"),
    "device_system_time_get": ActionSpec("GET", "/v3/device/systemTime"),
    "device_system_time_set": ActionSpec("PUT", "/v3/device/systemTime", passthrough_body=True),
    "get_storage_config": ActionSpec("GET", "/v3/setting/storageConfig"),
    "set_storage_config": ActionSpec("PUT", "/v3/setting/storageConfig", passthrough_body=True),
    "backup_recover_progress": ActionSpec("GET", "/v3/backup/recover/progress"),
    "backup_list": ActionSpec("POST", "/v3/backup/list", passthrough_body=True),
    "backup_create": ActionSpec("POST", "/v3/backup", passthrough_body=True),
    "backup_download": ActionSpec(
        "GET", "/v3/backup/download", body_keys=["uniqueId"], binary=True
    ),
    "backup_recover": ActionSpec("POST", "/v3/backup/recover", passthrough_body=True),
    "backup_delete": ActionSpec("DELETE", "/v3/backup", passthrough_body=True),
    "backup_update": ActionSpec("PUT", "/v3/backup", passthrough_body=True),
    "backup_import": ActionSpec(
        "POST", "/v3/backup/import", passthrough_body=True, multipart=True
    ),
    "logaccess_list": ActionSpec("POST", "/v3/logAccess/list", passthrough_body=True),
    "logaccess_delete": ActionSpec("DELETE", "/v3/logAccess", passthrough_body=True),
    "logaccess_create": ActionSpec("POST", "/v3/logAccess", passthrough_body=True),
    "logaccess_update": ActionSpec("PUT", "/v3/logAccess", passthrough_body=True),
    "logaccess_sample": ActionSpec("POST", "/v3/logAccess/sample", passthrough_body=True),
    "logaccess_test": ActionSpec("POST", "/v3/logAccess/test", passthrough_body=True),
    "logaccess_check": ActionSpec("GET", "/v3/logAccess/check", body_keys=["name"]),
    # system info
    "basic_license_get": ActionSpec("GET", "/v3/basic/license"),
    "basic_connect_status": ActionSpec("GET", "/v3/basic/connectStatus"),
    "basic_information": ActionSpec("GET", "/v3/basic/information"),
    "basic_information_enable": ActionSpec(
        "POST", "/v3/basic/information/enable", passthrough_body=True
    ),
    "basic_information_import": ActionSpec(
        "POST", "/v3/basic/information", passthrough_body=True, multipart=True
    ),
    "basic_license_upload": ActionSpec(
        "POST", "/v3/basic/license", passthrough_body=True, multipart=True
    ),
    "mdr_service_status": ActionSpec("GET", "/v3/mdrService/status"),
    "mdr_service_enable": ActionSpec(
        "PUT", "/v3/mdrService/enable", passthrough_body=True
    ),
    # system diagnosis
    "device_coredump_list": ActionSpec("GET", "/v3/device/coredump"),
    "device_coredump_download": ActionSpec(
        "POST", "/v3/device/coredumpDownload", passthrough_body=True, binary=True
    ),
    "device_coredump_delete": ActionSpec(
        "DELETE", "/v3/device/coredump", passthrough_body=True
    ),
    "device_pcap_get": ActionSpec("GET", "/v3/device/pcap"),
    "device_pcap_set": ActionSpec("PUT", "/v3/device/pcap", passthrough_body=True),
    "device_pcap_file_list": ActionSpec("GET", "/v3/device/pcapFile"),
    "device_pcap_download": ActionSpec(
        "POST", "/v3/device/pcapDownload", passthrough_body=True, binary=True
    ),
    "device_pcap_file_delete": ActionSpec(
        "DELETE", "/v3/device/pcapFile", passthrough_body=True
    ),
}

# Helper ------------------------------------------------------------------

HELPER_ACTION_SPECS: dict[str, ActionSpec] = {
    "document_list": ActionSpec("POST", "/v3/document/list", passthrough_body=True),
    "document_preview": ActionSpec(
        "GET", "/v3/document/preview", body_keys=["fileName"]
    ),
    "product_news_get": ActionSpec("GET", "/v3/product/news"),
    "product_news_mark_read": ActionSpec(
        "PUT", "/v3/product/news", passthrough_body=True
    ),
    "product_version": ActionSpec("GET", "/v3/product/version"),
    "product_issue": ActionSpec("POST", "/v3/product/issue", passthrough_body=True),
}


GROUP_SPECS: dict[str, dict[str, ActionSpec]] = {
    "login": LOGIN_ACTION_SPECS,
    "monitoring": MONITORING_ACTION_SPECS,
    "strategy": STRATEGY_ACTION_SPECS,
    "assets": ASSETS_ACTION_SPECS,
    "device": DEVICE_ACTION_SPECS,
    "helper": HELPER_ACTION_SPECS,
}

# Lightweight read-only actions used by `action="test"` for connectivity check.
_CONNECTIVITY_TEST_ACTIONS: dict[str, str] = {
    "login": "get_account",
    "monitoring": "common_threat_type_list",
    "strategy": "blacklist_location_options",
    "assets": "common_asset_group_tree",
    "device": "basic_version",
    "helper": "product_version",
}


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------


def _outputs_dir() -> str:
    """Resolve the daily outputs directory used to persist binary downloads."""
    import datetime
    from pathlib import Path

    try:
        from flocks.workspace.manager import WorkspaceManager

        ws = WorkspaceManager.get_instance()
        base = Path(ws.get_workspace_dir()) / "outputs" / datetime.date.today().isoformat()
    except Exception:
        base = Path.home() / ".flocks" / "workspace" / "outputs" / datetime.date.today().isoformat()
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


def _save_binary(path: str, body: bytes, content_type: str) -> str:
    import datetime
    from pathlib import Path

    safe_name = path.strip("/").replace("/", "_") or "download"
    ext = ""
    ct = content_type.lower()
    if "csv" in ct:
        ext = ".csv"
    elif "excel" in ct or "spreadsheet" in ct or "xlsx" in ct:
        ext = ".xlsx"
    elif "zip" in ct:
        ext = ".zip"
    elif "pdf" in ct:
        ext = ".pdf"
    elif "octet-stream" in ct:
        ext = ".bin"
    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    target = Path(_outputs_dir()) / f"onesig_{safe_name}_{timestamp}{ext}"
    target.write_bytes(body)
    return str(target)


def _envelope_to_result(action: str, envelope: dict[str, Any]) -> ToolResult:
    metadata = {"source": "OneSIG", "api": action}
    response_code = envelope.get("responseCode")
    if response_code is not None and response_code != _RESPONSE_CODE_OK:
        msg = envelope.get("verboseMsg") or envelope.get("verbose_msg") or "Unknown error"
        return ToolResult(
            success=False,
            error=f"OneSIG API error (responseCode={response_code}): {msg}",
            output=envelope,
            metadata=metadata,
        )
    if "data" in envelope:
        return ToolResult(success=True, output=envelope.get("data"), metadata=metadata)
    return ToolResult(success=True, output=envelope, metadata=metadata)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def _encrypt_body_fields(
    session: "OneSIGSession",
    spec: ActionSpec,
    body: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Replace each plaintext field listed in ``spec.encrypt_fields`` with its
    RSA-OAEP ciphertext (Base64). A fresh pubkey is fetched per call to match
    the front-end's ``clearRSACache`` behavior. No-op if body is None."""
    if not spec.encrypt_fields or body is None or not isinstance(body, dict):
        return body
    has_target = any(_has_value(body.get(f)) for f in spec.encrypt_fields)
    if not has_target:
        return body
    pubkey_resp = await session._raw_request_json("GET", "/v3/pubkey")
    pubkey = (pubkey_resp or {}).get("data", {}).get("pubkey")
    if not pubkey:
        raise ValueError(
            f"无法从 /v3/pubkey 获取 RSA 公钥用于加密字段 {list(spec.encrypt_fields)}: {pubkey_resp!r}"
        )
    out = dict(body)
    for field in spec.encrypt_fields:
        plain = out.get(field)
        if isinstance(plain, str) and plain:
            out[field] = _rsa_oaep_encrypt(pubkey, plain, session.config.oaep_hash)
    return out


def _build_form_data(
    spec: ActionSpec,
    body: Optional[dict[str, Any]],
    file_path: Optional[str],
) -> "aiohttp.FormData":
    """Assemble a multipart/form-data payload: the file under
    ``spec.multipart_file_field`` plus any non-file fields from ``body``."""
    if not file_path:
        raise ValueError(
            f"multipart 接口 {spec.path} 需要 `file_path` 参数指向待上传的本地文件"
        )
    from pathlib import Path as _Path

    fp = _Path(file_path).expanduser()
    if not fp.is_file():
        raise ValueError(f"file_path 指向的文件不存在或不是常规文件：{fp}")

    form = aiohttp.FormData()
    fname = fp.name
    # Stream the file via an open handle; aiohttp closes it on send completion.
    form.add_field(
        spec.multipart_file_field,
        fp.open("rb"),
        filename=fname,
        content_type="application/octet-stream",
    )
    if isinstance(body, dict):
        for k, v in body.items():
            if v is None:
                continue
            if isinstance(v, (dict, list, tuple)):
                import json as _json

                form.add_field(k, _json.dumps(v, ensure_ascii=False))
            elif isinstance(v, bool):
                form.add_field(k, "true" if v else "false")
            else:
                form.add_field(k, str(v))
    return form


async def _execute_action(
    group: str,
    action: str,
    params: dict[str, Any],
) -> ToolResult:
    spec_map = GROUP_SPECS[group]
    spec = spec_map[action]

    validation_error = _validate_required(spec, action, params)
    if validation_error:
        return ToolResult(success=False, error=validation_error)

    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))

    captcha = params.get("captcha")
    totp = params.get("totp")

    session = await _get_session(config)
    try:
        query, body = spec.build_request(params)
        body = await _encrypt_body_fields(session, spec, body)

        if spec.multipart:
            form = _build_form_data(spec, body, params.get("file_path"))
            status, envelope, body_bytes, content_type = await session.request(
                spec.method,
                spec.path,
                params=query,
                form_data=form,
                captcha=captcha,
                totp=totp,
            )
        else:
            status, envelope, body_bytes, content_type = await session.request(
                spec.method,
                spec.path,
                params=query,
                json_body=body,
                captcha=captcha,
                totp=totp,
            )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return ToolResult(success=False, error=f"Unexpected error: {exc}")

    metadata: dict[str, Any] = {
        "source": "OneSIG",
        "api": action,
        "method": spec.method,
        "path": spec.path,
        "http_status": status,
    }
    if isinstance(envelope, dict):
        if "responseCode" in envelope:
            metadata["response_code"] = envelope.get("responseCode")
        verbose_msg = envelope.get("verboseMsg") or envelope.get("verbose_msg")
        if verbose_msg:
            metadata["verbose_msg"] = verbose_msg

    if spec.binary or (body_bytes and not envelope):
        if status >= 400:
            return ToolResult(
                success=False,
                error=f"HTTP {status} from {spec.path}",
                metadata=metadata,
            )
        saved_path = _save_binary(spec.path, body_bytes, content_type)
        metadata["saved_path"] = saved_path
        metadata["binary_size"] = len(body_bytes)
        metadata["content_type"] = content_type
        return ToolResult(
            success=True,
            output={
                "saved_path": saved_path,
                "size": len(body_bytes),
                "content_type": content_type,
            },
            metadata=metadata,
        )

    if status >= 400 and not envelope:
        return ToolResult(
            success=False,
            error=f"HTTP {status} from {spec.path}",
            metadata=metadata,
        )

    result = _envelope_to_result(action, envelope or {})
    merged_meta = dict(result.metadata or {})
    merged_meta.update(metadata)
    result.metadata = merged_meta
    return result


async def _dispatch_group(
    ctx: ToolContext,
    group: str,
    action: str,
    **params: Any,
) -> ToolResult:
    del ctx
    if action == "test":
        test_action = _CONNECTIVITY_TEST_ACTIONS.get(group)
        if test_action:
            return await _execute_action(group, test_action, params)
    spec_map = GROUP_SPECS[group]
    if action not in spec_map and action not in {"login", "logout", "change_password"}:
        available = ", ".join(sorted(spec_map))
        return ToolResult(
            success=False,
            error=f"Unsupported {group} action: {action}. Available actions: {available}",
        )
    if group == "login":
        if action == "login":
            return await _login_action(params)
        if action == "logout":
            return await _logout_action()
        if action == "change_password":
            return await _change_password_action(params)
    return await _execute_action(group, action, params)


async def _login_action(params: dict[str, Any]) -> ToolResult:
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    session = await _get_session(config)
    try:
        account = await session.login(
            captcha=params.get("captcha"),
            totp=params.get("totp"),
            force=True,
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    return ToolResult(
        success=True,
        output={"account": account, "message": "Logged in to OneSIG"},
        metadata={"source": "OneSIG", "api": "login"},
    )


async def _logout_action() -> ToolResult:
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    session = await _get_session(config)
    try:
        envelope = await session.logout()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    finally:
        await session.close()
        async with _SESSIONS_LOCK:
            _SESSIONS.pop(config.session_key, None)
    return _envelope_to_result("logout", envelope or {})


async def _change_password_action(params: dict[str, Any]) -> ToolResult:
    """Change the current user's password (RSA-OAEP encrypts each field)."""
    new_password = params.get("new_password") or params.get("newPassword")
    old_password = params.get("old_password") or params.get("oldPassword")
    dup_password = params.get("dup_password") or params.get("dupPassword") or new_password
    if not new_password:
        return ToolResult(
            success=False,
            error="change_password 缺少 new_password 参数",
        )
    try:
        config = _resolve_runtime_config()
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    session = await _get_session(config)
    try:
        if not session._logged_in:
            await session.login(captcha=params.get("captcha"), totp=params.get("totp"))
        pubkey_resp = await session._raw_request_json("GET", "/v3/pubkey")
        pubkey = (pubkey_resp or {}).get("data", {}).get("pubkey")
        if not pubkey:
            return ToolResult(
                success=False,
                error=f"无法获取 RSA 公钥用于改密：{pubkey_resp!r}",
            )
        body: dict[str, Any] = {
            "username": params.get("username") or config.username,
            "newPassword": _rsa_oaep_encrypt(pubkey, new_password, config.oaep_hash),
            "dupPassword": _rsa_oaep_encrypt(pubkey, dup_password, config.oaep_hash),
        }
        if old_password:
            body["oldPassword"] = _rsa_oaep_encrypt(pubkey, old_password, config.oaep_hash)
        status, envelope, _, _ = await session.request(
            "PUT", "/v3/user/password", json_body=body
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    if status >= 400 and not envelope:
        return ToolResult(success=False, error=f"HTTP {status} from /v3/user/password")
    return _envelope_to_result("change_password", envelope or {})


# ---------------------------------------------------------------------------
# Public group entry points (referenced from YAML handler stanzas)
# ---------------------------------------------------------------------------


async def login(ctx: ToolContext, action: str = "login", **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "login", action, **params)


async def monitoring(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "monitoring", action, **params)


async def strategy(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "strategy", action, **params)


async def assets(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "assets", action, **params)


async def device(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "device", action, **params)


async def helper(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "helper", action, **params)
