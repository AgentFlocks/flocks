"""NGSOC (Qi-Anxin NGSOC-BD / NGSOC-LV) HTTP API handler.

Aligned with R4.15.1 of the official ``奇安信网神安全分析与管理系统V4.0
(NGSOC-BD_AND_NGSOC-LV)_R4.15.1_API接口手册_V1.0`` manual.

URL composition follows manual §2.1::

    https://<base_url>/<api_prefix>/<api-url>

with ``api_prefix`` defaulting to ``/api/v1`` (the value the manual
prints in every example). Authentication uses the static
``NGSOC-Access-Token`` header issued by the device admin under
``系统管理 > 基础配置 > 安全性 > 凭据管理`` (manual §2.2).

The dispatch architecture mirrors the existing ``onesig`` plugin: each
endpoint becomes an :class:`ActionSpec`; tools are grouped by manual
section (alarms, assets, vuls, risks, users, workorders, bigscreens,
storage); a single async ``request()`` performs the call and the
``_envelope_to_result`` helper unwraps the standard NGSOC envelope into a
``ToolResult``. Binary endpoints (e.g. ``/storage/download``) are saved
under ``~/.flocks/workspace/outputs/<today>/ngsoc_*``.
"""
from __future__ import annotations

import asyncio
import os
import re
import ssl
from typing import Any, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult


SERVICE_ID = "ngsoc_api"
PRODUCT_VERSION = "R4.15.1"

# Manual §2.1 prints the canonical URL as ``https://ngsoc/api/v1/<api-url>``,
# so ``/api/v1`` is the open-box default. Reverse-proxy deployments that
# expose ``/<some-prefix>/api/v1/...`` should override via ``api_prefix``.
DEFAULT_API_PREFIX = "/api/v1"
DEFAULT_TIMEOUT = 60
DEFAULT_VERIFY_SSL = False

# Manual §2.2 — header carries the static token returned by the credentials
# manager. Header name is case-sensitive in some upstreams; the device used
# during integration testing accepts the exact case below.
TOKEN_HEADER = "NGSOC-Access-Token"

_RESPONSE_CODE_OK = 0


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


def _coerce_bool(value: Any, default: bool = False) -> bool:
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


def _resolve_verify_ssl(raw: dict[str, Any]) -> bool:
    """Resolve the SSL verification toggle.

    Mirrors the cross-handler convention used by onesec / onesig / qingteng:

      1. ``raw["verify_ssl"]``                    - canonical
      2. ``raw["ssl_verify"]``                    - snake_case alias
      3. ``raw["verifySsl"]``                     - camelCase alias
      4. ``raw["custom_settings"]["verify_ssl"]`` - WebUI generic toggle
      5. ``NGSOC_VERIFY_SSL`` env var             - CLI / container override
      6. fallback to ``DEFAULT_VERIFY_SSL``       - default ``False`` because
                                                    NGSOC is overwhelmingly
                                                    deployed as a private
                                                    appliance with a self-
                                                    signed cert.
    """
    candidates: list[Any] = [
        raw.get("verify_ssl"),
        raw.get("ssl_verify"),
        raw.get("verifySsl"),
    ]
    custom = raw.get("custom_settings")
    if isinstance(custom, dict):
        candidates.append(custom.get("verify_ssl"))
    candidates.append(os.getenv("NGSOC_VERIFY_SSL"))

    for value in candidates:
        if value is None:
            continue
        return _coerce_bool(value, default=DEFAULT_VERIFY_SSL)
    return DEFAULT_VERIFY_SSL


class NGSOCRuntimeConfig:
    """Resolved runtime configuration for a single NGSOC service entry."""

    def __init__(
        self,
        *,
        base_url: str,
        api_prefix: str,
        access_token: str,
        verify_ssl: bool,
        timeout: int,
    ) -> None:
        self.base_url = base_url
        self.api_prefix = api_prefix
        self.access_token = access_token
        self.verify_ssl = verify_ssl
        self.timeout = timeout

    @property
    def session_key(self) -> str:
        # Token only appears in the session key as a short fingerprint to
        # avoid leaking it via in-process cache iteration / logs while still
        # rotating the cached aiohttp session if the operator regenerates
        # the credential.
        token_fp = (self.access_token or "")[-6:]
        return f"{self.base_url}|{token_fp}"

    def build_url(self, path: str) -> str:
        path = path if path.startswith("/") else "/" + path
        prefix = self.api_prefix.rstrip("/")
        return f"{self.base_url}{prefix}{path}"


def _resolve_runtime_config() -> NGSOCRuntimeConfig:
    raw = _service_config()
    base_url = (
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("baseUrl"))
        or os.getenv("NGSOC_BASE_URL")
    )
    if not base_url:
        raise ValueError(
            "NGSOC base_url not configured. "
            "Set api_services.ngsoc_api.base_url or NGSOC_BASE_URL."
        )
    base_url = base_url.rstrip("/")

    api_prefix = (
        _resolve_ref(raw.get("api_prefix"))
        or _resolve_ref(raw.get("apiPrefix"))
        or os.getenv("NGSOC_API_PREFIX")
        or DEFAULT_API_PREFIX
    )
    if api_prefix and not api_prefix.startswith("/"):
        api_prefix = "/" + api_prefix
    api_prefix = api_prefix.rstrip("/")

    secret_manager = _get_secret_manager()
    access_token = (
        _resolve_ref(raw.get("access_token"))
        or _resolve_ref(raw.get("accessToken"))
        or _resolve_ref(raw.get("token"))
        or secret_manager.get("ngsoc_access_token")
        or secret_manager.get(f"{SERVICE_ID}_access_token")
        or os.getenv("NGSOC_ACCESS_TOKEN")
    )
    if not access_token:
        raise ValueError(
            "NGSOC access_token not configured. Save it as the "
            "ngsoc_access_token secret (manual §2.2: 凭据管理 > 凭据授权码) "
            "or set the NGSOC_ACCESS_TOKEN env var."
        )

    verify_ssl = _resolve_verify_ssl(raw)

    timeout_raw = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    return NGSOCRuntimeConfig(
        base_url=base_url,
        api_prefix=api_prefix,
        access_token=access_token,
        verify_ssl=verify_ssl,
        timeout=timeout,
    )


def _ssl_context(verify_ssl: bool) -> Any:
    if verify_ssl:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
class NGSOCSession:
    """Plain bearer-token HTTP session for a single NGSOC device.

    NGSOC is *much* simpler than OneSIG: there is no captcha / pubkey /
    cookie negotiation, just ``NGSOC-Access-Token`` on every request. We
    keep the same one-session-per-device pattern so connection pooling
    works and so callers don't pay the TLS handshake on every dispatch.
    """

    def __init__(self, config: NGSOCRuntimeConfig) -> None:
        self.config = config
        self._session: Optional[aiohttp.ClientSession] = None
        self._lock = asyncio.Lock()

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            async with self._lock:
                if self._session is None or self._session.closed:
                    self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    def _path_with_rest(self, path: str, rest: dict[str, Any]) -> str:
        """Substitute ``{name}`` placeholders in ``path`` from ``rest``.

        Raises ``ValueError`` if any placeholder remains unresolved — we
        prefer this over silently sending ``/.../{ticket-id}`` literals
        through to the device, which usually surfaces as 404 deep in the
        gateway logs. Validation always runs (even when ``rest`` is empty)
        so a developer who registers an ActionSpec with a placeholder but
        forgets to add the key to ``rest_keys`` / ``required`` gets a
        clear error instead of an opaque 404.
        """
        out = path
        if rest:
            for key, value in rest.items():
                placeholder = "{" + key + "}"
                if placeholder in out:
                    out = out.replace(placeholder, str(value))
        missing = re.findall(r"\{([^{}]+)\}", out)
        if missing:
            raise ValueError(
                f"NGSOC URL {path} 缺少 REST 参数: {', '.join(missing)}"
            )
        return out

    async def request(
        self,
        method: str,
        path: str,
        *,
        rest: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        accept: Optional[str] = None,
    ) -> tuple[int, dict[str, Any], bytes, str]:
        """Issue an authenticated request.

        Returns ``(status, json_envelope, body_bytes, content_type)`` where
        ``json_envelope`` is the parsed dict for JSON responses and
        ``body_bytes`` carries raw bytes for binary responses (e.g.
        ``/storage/download`` or PCAP exports).
        """
        session = await self._ensure_session()
        full_path = self._path_with_rest(path, rest or {})
        url = self.config.build_url(full_path)

        headers: dict[str, str] = {TOKEN_HEADER: self.config.access_token}
        if accept:
            headers["Accept"] = accept

        request_params: Optional[dict[str, Any]] = None
        if params:
            # Booleans must be lowercased: Python's ``str(True)`` -> "True",
            # but the Java/Spring stack behind NGSOC parses query strings
            # with case-sensitive boolean coercion and silently treats
            # "True" as false. We coerce here (rather than in build_request)
            # so the body / JSON path keeps native Python booleans.
            request_params = {
                k: ("true" if v is True else "false" if v is False else v)
                for k, v in params.items()
                if v is not None
            }
            if not request_params:
                request_params = None

        kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": aiohttp.ClientTimeout(total=self.config.timeout),
            "ssl": _ssl_context(self.config.verify_ssl),
        }
        if request_params is not None:
            kwargs["params"] = request_params
        if json_body is not None:
            kwargs["json"] = json_body
            kwargs["headers"] = {**headers, "Content-Type": "application/json"}

        async with session.request(method.upper(), url, **kwargs) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "") or ""
            if "application/json" in content_type.lower():
                envelope = await resp.json(content_type=None)
                body_bytes = b""
            else:
                body_bytes = await resp.read()
                # Some NGSOC builds return JSON with a non-JSON Content-Type
                # (e.g. text/plain); attempt a salvage parse so dispatch
                # still gets a usable envelope.
                envelope = {}
                if body_bytes and len(body_bytes) < 1_000_000:
                    try:
                        import json as _json

                        decoded = body_bytes.decode("utf-8", errors="replace")
                        candidate = _json.loads(decoded)
                        if isinstance(candidate, dict):
                            envelope = candidate
                            body_bytes = b""
                    except Exception:
                        envelope = {}

        envelope_dict = envelope if isinstance(envelope, dict) else {"data": envelope}
        return status, envelope_dict, body_bytes, content_type


_SESSIONS: dict[str, NGSOCSession] = {}
_SESSIONS_LOCK = asyncio.Lock()


async def _get_session(config: NGSOCRuntimeConfig) -> NGSOCSession:
    async with _SESSIONS_LOCK:
        sess = _SESSIONS.get(config.session_key)
        if sess is None:
            sess = NGSOCSession(config)
            _SESSIONS[config.session_key] = sess
        else:
            sess.config = config
        return sess


# ---------------------------------------------------------------------------
# Action specifications
# ---------------------------------------------------------------------------
_RESERVED_PARAM_KEYS = frozenset({"action"})


class ActionSpec:
    """Declarative spec for a single NGSOC endpoint action."""

    def __init__(
        self,
        method: str,
        path: str,
        *,
        rest_keys: Optional[list[str]] = None,
        query_keys: Optional[list[str]] = None,
        body_keys: Optional[list[str]] = None,
        passthrough_body: bool = False,
        passthrough_query: bool = False,
        binary: bool = False,
        accept: Optional[str] = None,
        required: Optional[list[str]] = None,
    ) -> None:
        self.method = method.upper()
        self.path = path
        self.rest_keys = rest_keys or []
        self.query_keys = query_keys or []
        self.body_keys = body_keys or []
        # passthrough_body=True forwards every non-reserved, non-query,
        # non-rest parameter into the JSON body. Useful for query / list
        # endpoints with rich nested filter objects (e.g. /alarms/list).
        self.passthrough_body = passthrough_body
        # passthrough_query mirrors the same idea for GET endpoints whose
        # filter set is large (e.g. /workorders/work-orders).
        self.passthrough_query = passthrough_query
        self.binary = binary
        self.accept = accept
        self.required = required or []

    def build_request(
        self, params: dict[str, Any]
    ) -> tuple[dict[str, Any], Optional[dict[str, Any]], Optional[Any]]:
        rest = {k: params[k] for k in self.rest_keys if params.get(k) is not None}

        query: dict[str, Any] = {}
        for key in self.query_keys:
            if params.get(key) is not None:
                query[key] = params[key]

        # passthrough_query is METHOD-agnostic. NGSOC §5.4.1
        # ``POST /risks/asset/asset-risks`` is a real endpoint that uses
        # both a JSON body (groupIds / networkSegmentId / domainId) AND
        # ~18 QUERY string filters (page / size / viewId / riskLevel / ...).
        # If we gated this behind ``method == "GET"`` like the previous
        # version did, the user's ``viewId`` (manual marks 是) and every
        # other filter would be silently dropped — the server would then
        # 4xx with "viewId is required" and the operator would have no clue
        # the SDK ever saw the param. Hoisting the loop here also lets
        # future PUT / DELETE endpoints that mix body + query just work.
        if self.passthrough_query:
            for k, v in params.items():
                if v is None:
                    continue
                if k in _RESERVED_PARAM_KEYS or k in self.rest_keys:
                    continue
                if k in self.query_keys:
                    continue
                # Skip body_keys so the same field isn't double-shipped
                # both in the JSON body and the query string.
                if k in self.body_keys:
                    continue
                query[k] = v

        body: Optional[Any] = None

        if self.method != "GET":
            if self.passthrough_body:
                body = {
                    k: v
                    for k, v in params.items()
                    if v is not None
                    and k not in _RESERVED_PARAM_KEYS
                    and k not in self.rest_keys
                    and k not in self.query_keys
                }
            else:
                body = {
                    k: params[k]
                    for k in self.body_keys
                    if params.get(k) is not None
                }
                if not body:
                    body = None

        return rest, (query or None), body


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def _validate_required(
    spec: ActionSpec, action: str, params: dict[str, Any]
) -> Optional[str]:
    missing = [k for k in spec.required if not _has_value(params.get(k))]
    if missing:
        return f"Missing required parameters for {action}: {', '.join(missing)}"
    return None


# ---------------------------------------------------------------------------
# Action specs per module (manual §5)
# ---------------------------------------------------------------------------
# §5.1 威胁告警 (alarms) ----------------------------------------------------
ALARMS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.1.1 告警工单状态变更 — PUT /alarms/ticket-state/{ticket-id}
    "alarm_ticket_state_update": ActionSpec(
        "PUT",
        "/alarms/ticket-state/{ticket-id}",
        rest_keys=["ticket-id"],
        body_keys=["state"],
        required=["ticket-id", "state"],
    ),
    # 5.1.2 告警 PCAP 下载 — POST /alarms/kvstore/export-file
    "alarm_pcap_export": ActionSpec(
        "POST",
        "/alarms/kvstore/export-file",
        passthrough_body=True,
        required=["fileName", "messages", "uuid"],
    ),
    # 5.1.3 告警相关日志展示 — GET /alarms/{raw-alarm-id}/evidence
    "alarm_evidence_get": ActionSpec(
        "GET",
        "/alarms/{raw-alarm-id}/evidence",
        rest_keys=["raw-alarm-id"],
        passthrough_query=True,
        required=["raw-alarm-id", "type", "timestamp", "ruleId"],
    ),
    # 5.1.4 获取告警 — POST /alarms/list-data
    "alarm_list_data": ActionSpec(
        "POST",
        "/alarms/list-data",
        passthrough_body=True,
        required=["queryParams"],
    ),
    # 5.1.5 告警详情字段详情 — GET /alarms/detail/summary/{uuid}
    "alarm_detail_summary": ActionSpec(
        "GET",
        "/alarms/detail/summary/{uuid}",
        rest_keys=["uuid"],
        required=["uuid"],
    ),
    # 5.1.6 批量处置告警 — PUT /alarms/dispose-state
    "alarm_dispose_state": ActionSpec(
        "PUT",
        "/alarms/dispose-state",
        passthrough_body=True,
        required=["alarmIds", "state", "endTime"],
    ),
    # 5.1.7 获取/筛选告警 — POST /alarms/list
    "alarm_list": ActionSpec(
        "POST",
        "/alarms/list",
        passthrough_body=True,
        required=["queryParams"],
    ),
    # 5.1.8 获取告警对应的原始告警列表 — GET /alarms/{alarm-id}/raw-alarms
    "alarm_raw_alarms": ActionSpec(
        "GET",
        "/alarms/{alarm-id}/raw-alarms",
        rest_keys=["alarm-id"],
        passthrough_query=True,
        required=["alarm-id", "timestamp", "latestTimestamp"],
    ),
    # 5.1.9 获取告警总数 — POST /alarms/list-total
    "alarm_list_total": ActionSpec(
        "POST",
        "/alarms/list-total",
        passthrough_body=True,
        required=["queryParams"],
    ),
    # 5.1.10 告警 PCAP 下载进度 — GET /alarms/kvstore/export-file/status
    "alarm_pcap_export_status": ActionSpec(
        "GET",
        "/alarms/kvstore/export-file/status",
        query_keys=["token"],
        required=["token"],
    ),
    # 5.1.11 告警 AI 研判 — GET /app-ai-alarm-judgment/judge/{merge-alarm-id}/{judge-type}
    # NOTE: SSE endpoint (text/event-stream) — we surface the raw text body so
    # callers can stream / parse it themselves.
    "alarm_ai_judge": ActionSpec(
        "GET",
        "/app-ai-alarm-judgment/judge/{merge-alarm-id}/{judge-type}",
        rest_keys=["merge-alarm-id", "judge-type"],
        accept="text/event-stream",
        required=["merge-alarm-id", "judge-type"],
    ),
    # 5.1.12 获取研判结果 — GET /app-ai-alarm-judgment/result/{merge-alarm-id}
    "alarm_ai_judge_result": ActionSpec(
        "GET",
        "/app-ai-alarm-judgment/result/{merge-alarm-id}",
        rest_keys=["merge-alarm-id"],
        required=["merge-alarm-id"],
    ),
    # 5.1.13 修改告警智能研判结论 — POST /alarms/judgment-record
    # Manual mis-labels ``alarmId`` / ``latestTimestamp`` under "REST 参数"
    # but the URL ``/alarms/judgment-record`` has NO placeholder, so they
    # cannot actually be REST values. We forward them as query string to
    # mirror §5.1.14 (the GET twin) which lists the same identifiers and
    # also has no placeholder. The body carries the actual triage payload.
    "alarm_judgment_record_update": ActionSpec(
        "POST",
        "/alarms/judgment-record",
        query_keys=["alarmId", "latestTimestamp"],
        body_keys=[
            "triageResult",
            "judgmentReason",
            "type",
            "creatorId",
            "creatorName",
            "recordId",
        ],
        required=["alarmId", "triageResult"],
    ),
    # 5.1.14 获取智能研判结论变更记录 — GET /alarms/judgment-record
    "alarm_judgment_record_list": ActionSpec(
        "GET",
        "/alarms/judgment-record",
        query_keys=["alarmId", "latestTimestamp"],
        required=["alarmId"],
    ),
}

# §5.2 资产中心 (assets) ----------------------------------------------------
ASSETS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.2.1 获取单资产详情 — GET /assets/asset/{id}
    "asset_get": ActionSpec(
        "GET",
        "/assets/asset/{id}",
        rest_keys=["id"],
        required=["id"],
    ),
    # 5.2.2 获取资产组下所有资产 id — GET /assets/id-list
    "asset_id_list": ActionSpec(
        "GET",
        "/assets/id-list",
        query_keys=["groupId"],
        required=["groupId"],
    ),
    # 5.2.3 获取资产组列表 — GET /assets/asset-group-list
    "asset_group_list": ActionSpec(
        "GET",
        "/assets/asset-group-list",
        query_keys=["viewId", "domainIds", "showNative"],
    ),
}

# §5.3 漏洞管理 (vuls) ------------------------------------------------------
VULS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.3.1 获取单资产漏洞列表 — GET /vuls/vuls-by-assetid
    "vuls_by_assetid": ActionSpec(
        "GET",
        "/vuls/vuls-by-assetid",
        passthrough_query=True,
        required=["page", "size", "assetId"],
    ),
    # 5.3.2 获取单资产配置核查列表 — GET /vuls/config/vuls-by-assetid
    "vuls_config_by_assetid": ActionSpec(
        "GET",
        "/vuls/config/vuls-by-assetid",
        passthrough_query=True,
        required=["page", "size", "assetId"],
    ),
    # 5.3.3 获取漏洞视角中漏洞列表 — POST /vuls/raw-data-vul-list
    "vuls_raw_data_list": ActionSpec(
        "POST",
        "/vuls/raw-data-vul-list",
        passthrough_body=True,
        required=["page", "size", "cnnvdOrder", "domainId"],
    ),
    # 5.3.4 获取 web 漏洞列表 — POST /vuls/web-raw-data
    "vuls_web_raw_data": ActionSpec(
        "POST",
        "/vuls/web-raw-data",
        passthrough_body=True,
        required=["page", "size"],
    ),
    # 5.3.5 获取弱口令列表 — POST /vuls/pwd/raw-data
    "vuls_pwd_raw_data": ActionSpec(
        "POST",
        "/vuls/pwd/raw-data",
        passthrough_body=True,
        required=["page", "size"],
    ),
}

# §5.4 风险管理 (risks) -----------------------------------------------------
RISKS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.4.1 单资产风险列表 — POST /risks/asset/asset-risks
    # Body carries the org/group filters; query carries pagination + sort.
    # Per manual §5.4.1: viewId is required (default 1). page/size accept
    # short ints; we don't coerce — the device echoes back the literal you
    # send so allow callers to mirror UI defaults exactly.
    "asset_risks_list": ActionSpec(
        "POST",
        "/risks/asset/asset-risks",
        body_keys=["groupIds", "networkSegmentId", "domainId"],
        passthrough_query=True,
        required=["viewId"],
    ),
    # 5.4.2 获取单资产风险详情 — GET /risks/asset/{assetId}/asset-risk
    # NB: manual §5.4.2 reuses the title "获取单资产配置核查列表" but the
    # endpoint actually returns a single asset's risk score (manual response
    # body fields: risk / riskLevel / compromiseState).
    "asset_risk_get": ActionSpec(
        "GET",
        "/risks/asset/{assetId}/asset-risk",
        rest_keys=["assetId"],
        required=["assetId"],
    ),
}

# §5.5 用户管理 (users) -----------------------------------------------------
USERS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.5.1 获取用户名列表 — GET /users/accounts/nicknames
    "user_nicknames": ActionSpec(
        "GET",
        "/users/accounts/nicknames",
        query_keys=["orgId", "hiddenList"],
    ),
}

# §5.6 工单管理 (workorders) -----------------------------------------------
WORKORDERS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.6.1 修改工单状态 — PUT /workorders/list/status
    "work_order_status_update": ActionSpec(
        "PUT",
        "/workorders/list/status",
        body_keys=["workOrderIds", "status"],
        required=["workOrderIds", "status"],
    ),
    # 5.6.2 获取工单列表 — GET /workorders/work-orders
    "work_order_list": ActionSpec(
        "GET",
        "/workorders/work-orders",
        passthrough_query=True,
        required=["page", "size"],
    ),
    # 5.6.3 获取工单详情 — GET /workorders/work-order/{workOrderId}
    "work_order_get": ActionSpec(
        "GET",
        "/workorders/work-order/{workOrderId}",
        rest_keys=["workOrderId"],
        required=["workOrderId"],
    ),
}

# §5.7 态势大屏 (bigscreens) -----------------------------------------------
BIGSCREENS_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.7.1 被利用漏洞 TOP5 — GET /bigscreens/vulnerability/getleakusedtop
    "vulnerability_leakused_top": ActionSpec(
        "GET",
        "/bigscreens/vulnerability/getleakusedtop",
        query_keys=["parentId", "severity"],
        required=["parentId", "severity"],
    ),
    # 5.7.2 受害 IP TOP5 — GET /bigscreens/bigscreen/getattackassettopX
    "attack_asset_top": ActionSpec(
        "GET",
        "/bigscreens/bigscreen/getattackassettopX",
        query_keys=["type"],
    ),
    # 5.7.3 外部威胁类型 TOP5 — GET /bigscreens/bigscreen/getthreattype
    "threat_type_top": ActionSpec(
        "GET",
        "/bigscreens/bigscreen/getthreattype",
        query_keys=["type"],
    ),
    # 5.7.4 外部攻击 IP TOP5 — GET /bigscreens/bigscreen/getattacksourceiptopX
    "attack_source_ip_top": ActionSpec(
        "GET",
        "/bigscreens/bigscreen/getattacksourceiptopX",
        query_keys=["type"],
    ),
    # 5.7.5 攻击列表（外部威胁类型 TOP5 下钻）— GET /bigscreens/bigscreen/getattacklist
    "attack_list": ActionSpec(
        "GET",
        "/bigscreens/bigscreen/getattacklist",
        query_keys=["type", "page", "size", "assetId"],
        required=["page", "size"],
    ),
    # 5.7.6 受害者 IP 数 — GET /bigscreens/attacker-situation/victim-survey
    "victim_survey": ActionSpec(
        "GET",
        "/bigscreens/attacker-situation/victim-survey",
        query_keys=["dateType"],
    ),
}

# §5.8 存储管理 (storage) ---------------------------------------------------
STORAGE_ACTION_SPECS: dict[str, ActionSpec] = {
    # 5.8.1 下载文件 — GET /storage/download
    # binary=True so the body bytes are saved under
    # ~/.flocks/workspace/outputs/<today>/ngsoc_*.
    "storage_download": ActionSpec(
        "GET",
        "/storage/download",
        query_keys=["serviceName", "fileName", "uuidName", "mode", "clusterId"],
        binary=True,
        required=["uuidName"],
    ),
}


GROUP_SPECS: dict[str, dict[str, ActionSpec]] = {
    "alarms": ALARMS_ACTION_SPECS,
    "assets": ASSETS_ACTION_SPECS,
    "vuls": VULS_ACTION_SPECS,
    "risks": RISKS_ACTION_SPECS,
    "users": USERS_ACTION_SPECS,
    "workorders": WORKORDERS_ACTION_SPECS,
    "bigscreens": BIGSCREENS_ACTION_SPECS,
    "storage": STORAGE_ACTION_SPECS,
}

# Lightweight read-only actions used by ``action="test"`` for connectivity
# probes that don't require any input parameter.
_CONNECTIVITY_TEST_ACTIONS: dict[str, str] = {
    "users": "user_nicknames",
    "bigscreens": "victim_survey",
    # ``asset_group_list`` has no required params and is a cheap read-only
    # endpoint suitable as a connectivity probe.
    "assets": "asset_group_list",
}


# ---------------------------------------------------------------------------
# Output handling
# ---------------------------------------------------------------------------
def _outputs_dir() -> str:
    import datetime
    from pathlib import Path

    try:
        from flocks.workspace.manager import WorkspaceManager

        ws = WorkspaceManager.get_instance()
        base = Path(ws.get_workspace_dir()) / "outputs" / datetime.date.today().isoformat()
    except Exception:
        base = (
            Path.home()
            / ".flocks"
            / "workspace"
            / "outputs"
            / datetime.date.today().isoformat()
        )
    base.mkdir(parents=True, exist_ok=True)
    return str(base)


_FS_SAFE_RE = re.compile(r"[^A-Za-z0-9._\-]+")


def _sanitize_filename(name: str) -> str:
    """Strip directory traversal and replace filesystem-unsafe chars.

    Preserves the original extension so a user-provided ``evidence.pcap``
    saves as ``...evidence.pcap`` rather than getting a synthetic ``.bin``
    suffix derived from Content-Type.
    """
    from pathlib import PurePosixPath

    # ``PurePosixPath().name`` strips parent dirs whether the user wrote
    # ``../../etc/passwd`` or ``C:\evil\file``.
    base = PurePosixPath(name.replace("\\", "/")).name
    safe = _FS_SAFE_RE.sub("_", base).strip("._-")
    return safe or "download"


def _save_binary(
    path: str,
    body: bytes,
    content_type: str,
    *,
    preferred_name: Optional[str] = None,
) -> str:
    """Persist a binary download under the daily outputs directory.

    Naming precedence (most informative first):
      1. ``preferred_name`` from the user's ``fileName`` query param
         (manual §5.8.1) — preserves the operator's intent and the
         original file extension (e.g. ``evidence.pcap``).
      2. URL path with Content-Type-derived extension fallback
         (e.g. ``ngsoc_storage_download_20260429T101530.bin``).
    """
    import datetime
    from pathlib import Path

    timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")

    if preferred_name:
        safe = _sanitize_filename(preferred_name)
        # Avoid collisions when the same user-supplied name is downloaded
        # multiple times within the same day by prefixing the timestamp.
        target = Path(_outputs_dir()) / f"ngsoc_{timestamp}_{safe}"
        target.write_bytes(body)
        return str(target)

    safe_name = path.strip("/").replace("/", "_") or "download"
    ext = ""
    ct = (content_type or "").lower()
    if "csv" in ct:
        ext = ".csv"
    elif "excel" in ct or "spreadsheet" in ct or "xlsx" in ct:
        ext = ".xlsx"
    elif "zip" in ct:
        ext = ".zip"
    elif "pdf" in ct:
        ext = ".pdf"
    elif "json" in ct:
        ext = ".json"
    elif "octet-stream" in ct:
        ext = ".bin"
    target = Path(_outputs_dir()) / f"ngsoc_{safe_name}_{timestamp}{ext}"
    target.write_bytes(body)
    return str(target)


def _envelope_to_result(action: str, envelope: dict[str, Any]) -> ToolResult:
    metadata = {"source": "NGSOC", "api": action, "version": PRODUCT_VERSION}
    err_code = envelope.get("errCode")
    if err_code is not None and err_code != _RESPONSE_CODE_OK:
        msg = (
            envelope.get("errMsg")
            or envelope.get("errDetail")
            or "Unknown error"
        )
        return ToolResult(
            success=False,
            error=f"NGSOC API error (errCode={err_code}): {msg}",
            output=envelope,
            metadata=metadata,
        )
    if "data" in envelope:
        return ToolResult(success=True, output=envelope.get("data"), metadata=metadata)
    return ToolResult(success=True, output=envelope, metadata=metadata)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------
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

    session = await _get_session(config)
    try:
        rest, query, body = spec.build_request(params)
        status, envelope, body_bytes, content_type = await session.request(
            spec.method,
            spec.path,
            rest=rest,
            params=query,
            json_body=body,
            accept=spec.accept,
        )
    except ValueError as exc:
        return ToolResult(success=False, error=str(exc))
    except aiohttp.ClientError as exc:
        return ToolResult(success=False, error=f"Request failed: {exc}")
    except Exception as exc:  # pragma: no cover - defensive
        return ToolResult(success=False, error=f"Unexpected error: {exc}")

    metadata: dict[str, Any] = {
        "source": "NGSOC",
        "api": action,
        "version": PRODUCT_VERSION,
        "method": spec.method,
        "path": spec.path,
        "http_status": status,
    }
    if isinstance(envelope, dict) and "errCode" in envelope:
        metadata["err_code"] = envelope.get("errCode")
        if envelope.get("errMsg"):
            metadata["err_msg"] = envelope.get("errMsg")

    if spec.binary or (body_bytes and not envelope):
        if status >= 400:
            return ToolResult(
                success=False,
                error=f"HTTP {status} from {spec.path}",
                metadata=metadata,
            )
        # Prefer the user-supplied filename when available (e.g. /storage/download
        # accepts ``fileName`` as a query param, manual §5.8.1) so artifacts are
        # saved with their original name + extension.
        preferred = None
        if isinstance(query, dict):
            candidate = query.get("fileName") or query.get("filename")
            if isinstance(candidate, str) and candidate.strip():
                preferred = candidate.strip()
        saved_path = _save_binary(
            spec.path, body_bytes, content_type, preferred_name=preferred
        )
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

    # SSE / text/event-stream responses for the AI-judge endpoint don't
    # contain an NGSOC envelope; surface the body bytes as a string.
    if (
        spec.accept == "text/event-stream"
        and not envelope
        and body_bytes
    ):
        return ToolResult(
            success=True,
            output={"event_stream": body_bytes.decode("utf-8", errors="replace")},
            metadata=metadata,
        )

    result = _envelope_to_result(action, envelope or {})
    merged = dict(result.metadata or {})
    merged.update(metadata)
    result.metadata = merged
    return result


async def _dispatch_group(
    ctx: ToolContext,
    group: str,
    action: str,
    **params: Any,
) -> ToolResult:
    del ctx
    spec_map = GROUP_SPECS[group]

    if action == "test":
        test_action = _CONNECTIVITY_TEST_ACTIONS.get(group)
        if test_action:
            return await _execute_action(group, test_action, params)
        return ToolResult(
            success=False,
            error=(
                f"NGSOC group {group} 没有定义无参连通性测试动作；"
                "请显式传入 action 与必填参数。"
            ),
        )

    if action not in spec_map:
        available = ", ".join(sorted(spec_map))
        return ToolResult(
            success=False,
            error=(
                f"Unsupported {group} action: {action}. "
                f"Available actions: {available}"
            ),
        )
    return await _execute_action(group, action, params)


# ---------------------------------------------------------------------------
# Public group entry points (referenced from YAML handler stanzas)
# ---------------------------------------------------------------------------
async def alarms(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "alarms", action, **params)


async def assets(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "assets", action, **params)


async def vuls(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "vuls", action, **params)


async def risks(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "risks", action, **params)


async def users(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "users", action, **params)


async def workorders(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "workorders", action, **params)


async def bigscreens(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "bigscreens", action, **params)


async def storage(ctx: ToolContext, action: str, **params: Any) -> ToolResult:
    return await _dispatch_group(ctx, "storage", action, **params)
