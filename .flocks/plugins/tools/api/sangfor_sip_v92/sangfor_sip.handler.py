"""
Sangfor SIP (Security Intelligence Platform) third-party data pull API handler.

Auth flow:
  1. POST /sangforinter/v1/auth/party/login  with auth3 = sha1(rand+password+"sangfor3party"+userName+desc)
  2. Carry token= in all subsequent GET /sangforinter/v1/data/* requests.
  Token has an expiry; on 401/403 the handler re-authenticates automatically.

Endpoints covered (per 92 version SIP API spec):
  /sangforinter/v1/data/ipgroup                 - 受监控IP模块
  /sangforinter/v1/data/business                - 已配置服务器相关信息 (classfy1_id=1,6,9,10,11)
  /sangforinter/v1/data/terminal                - 资产信息-终端 (classfy1_id=2,7,8)
  /sangforinter/v1/data/riskbusiness            - 风险业务
  /sangforinter/v1/data/riskterminal            - 风险终端
  /sangforinter/v1/data/riskevent               - 安全事件
  /sangforinter/v1/data/weakpasswd              - 脆弱性-弱密码
  /sangforinter/v1/data/hole                    - 脆弱性-漏洞信息
  /sangforinter/v1/data/plaintexttransmission   - 脆弱性-明文传输信息
"""

from __future__ import annotations

import hashlib
import os
import random
import time
from typing import Any, Optional

import aiohttp

from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "sangfor_sip"
DEFAULT_PORT = 7443
DEFAULT_TIMEOUT = 60

# ── In-memory token cache keyed by (host, port, platform_name, username) ──────
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
# Conservative TTL: refresh token 10 min before actual expiry.
# SIP does not return expiry in the login response, so we use a safe default.
TOKEN_TTL_SECONDS = 3 * 3600  # 3 hours


class RuntimeConfig:
    def __init__(
        self,
        base_url: str,
        timeout: int,
        platform_name: str,
        username: str,
        password: str,
        verify_ssl: bool,
    ):
        self.base_url = base_url
        self.timeout = timeout
        self.platform_name = platform_name
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl

    @property
    def cache_key(self) -> str:
        return f"{self.base_url}|{self.platform_name}|{self.username}"


# ── Config helpers ────────────────────────────────────────────────────────────

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


def _resolve_runtime_config() -> RuntimeConfig:
    raw = _service_config()

    host = (
        _resolve_ref(raw.get("host"))
        or os.getenv("SANGFOR_SIP_HOST")
        or ""
    ).strip()

    port_raw = raw.get("port") or DEFAULT_PORT
    try:
        port = int(str(port_raw).strip())
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    base_url = f"https://{host}:{port}" if host else ""

    timeout_raw = raw.get("timeout", DEFAULT_TIMEOUT)
    try:
        timeout = int(timeout_raw)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT

    platform_name = (
        _resolve_ref(raw.get("platform_name"))
        or os.getenv("SANGFOR_SIP_PLATFORM_NAME")
        or ""
    ).strip()

    username = (
        _resolve_ref(raw.get("username"))
        or os.getenv("SANGFOR_SIP_USERNAME")
        or ""
    ).strip()

    secret_manager = _get_secret_manager()
    password = (
        _resolve_ref(raw.get("password"))
        or secret_manager.get("sangfor_sip_password")
        or secret_manager.get(f"{SERVICE_ID}_password")
        or os.getenv("SANGFOR_SIP_PASSWORD")
        or ""
    ).strip()

    verify_ssl_raw = raw.get("verify_ssl", "false")
    if isinstance(verify_ssl_raw, bool):
        verify_ssl = verify_ssl_raw
    else:
        verify_ssl = str(verify_ssl_raw).strip().lower() in {"1", "true", "yes", "on"}

    if not host:
        raise ValueError(
            "Sangfor SIP host not configured. Set api_services.sangfor_sip.host or SANGFOR_SIP_HOST."
        )
    if not platform_name:
        raise ValueError(
            "Sangfor SIP platform_name not configured. Set api_services.sangfor_sip.platform_name."
        )
    if not username:
        raise ValueError(
            "Sangfor SIP username not configured. Set api_services.sangfor_sip.username."
        )
    if not password:
        raise ValueError(
            "Sangfor SIP password not configured. Set sangfor_sip_password secret or SANGFOR_SIP_PASSWORD."
        )

    return RuntimeConfig(
        base_url=base_url,
        timeout=timeout,
        platform_name=platform_name,
        username=username,
        password=password,
        verify_ssl=verify_ssl,
    )


# ── auth3 signing ─────────────────────────────────────────────────────────────

def _auth3(username: str, password: str, desc: str, rand: int) -> str:
    """
    string auth3(userName, password, desc, rand):
        return sha1(rand + password + "sangfor3party" + userName + desc).toHexString()
    """
    raw = f"{rand}{password}sangfor3party{username}{desc}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


# ── Token management ──────────────────────────────────────────────────────────

def _get_cached_token(cfg: RuntimeConfig) -> Optional[str]:
    entry = _TOKEN_CACHE.get(cfg.cache_key)
    if entry:
        token, expires_at = entry
        if time.time() < expires_at:
            return token
    return None


def _cache_token(cfg: RuntimeConfig, token: str) -> None:
    _TOKEN_CACHE[cfg.cache_key] = (token, time.time() + TOKEN_TTL_SECONDS)


def _invalidate_token(cfg: RuntimeConfig) -> None:
    _TOKEN_CACHE.pop(cfg.cache_key, None)


async def _login(cfg: RuntimeConfig, session: aiohttp.ClientSession) -> str:
    rand = random.randint(0, 2**31 - 1)
    client_product = ""
    client_version = ""
    client_id = 0
    # auth3 desc = clientProduct + clientVersion + str(clientId)
    auth_desc = f"{client_product}{client_version}{client_id}"
    auth_str = _auth3(cfg.username, cfg.password, auth_desc, rand)
    payload = {
        "rand": rand,
        "userName": cfg.username,
        "clientProduct": client_product,
        "clientVersion": client_version,
        "clientId": client_id,
        "desc": auth_desc,
        "auth": auth_str,
        "platformName": cfg.platform_name,
    }
    login_url = f"{cfg.base_url}/sangforinter/v1/auth/party/login?verify=false"
    async with session.post(login_url, json=payload) as resp:
        try:
            data = await resp.json(content_type=None)
        except Exception as exc:
            text = await resp.text()
            raise RuntimeError(f"SIP login parse error (HTTP {resp.status}): {text[:500]}") from exc

    code = data.get("code")
    if code == 0:
        token = data.get("data", {}).get("token", "")
        if not token:
            raise RuntimeError("SIP login succeeded but token is empty.")
        _cache_token(cfg, token)
        return token
    elif code == 301:
        raise RuntimeError("SIP login failed (code 301): invalid argument or wrong credentials.")
    elif code == 13:
        raise RuntimeError("SIP login failed (code 13): permission denied – platform not found.")
    else:
        raise RuntimeError(f"SIP login failed: code={code}, message={data.get('message')}")


async def _get_token(cfg: RuntimeConfig, session: aiohttp.ClientSession) -> str:
    token = _get_cached_token(cfg)
    if token:
        return token
    return await _login(cfg, session)


# ── Generic data fetch ────────────────────────────────────────────────────────

async def _fetch_data(
    cfg: RuntimeConfig,
    session: aiohttp.ClientSession,
    endpoint: str,
    from_time: int,
    to_time: int,
    max_count: int,
    extra_params: Optional[dict[str, Any]] = None,
    is_vuln: bool = False,
) -> dict[str, Any]:
    """
    GET /sangforinter/v1/data/<endpoint>?token=...&fromActionTime=...&toActionTime=...&maxCount=...
    Auto-retries once on 401/403 by re-authenticating.
    Per spec: maxCount capped at 10000 (5000 for vulnerability modules).
    """
    url = f"{cfg.base_url}/sangforinter/v1/data/{endpoint}"

    max_limit = 5000 if is_vuln else 10000
    if max_count > max_limit:
        max_count = max_limit

    for attempt in range(2):
        token = await _get_token(cfg, session)
        params: dict[str, Any] = {
            "token": token,
            "fromActionTime": from_time,
            "toActionTime": to_time,
            "maxCount": max_count,
        }
        if extra_params:
            params.update(extra_params)

        async with session.get(url, params=params) as resp:
            if resp.status in (401, 403) and attempt == 0:
                _invalidate_token(cfg)
                continue

            try:
                data = await resp.json(content_type=None)
            except Exception as exc:
                text = await resp.text()
                raise RuntimeError(
                    f"SIP data parse error (HTTP {resp.status}): {text[:500]}"
                ) from exc

            code = data.get("code")
            if code == 0:
                return data.get("data", data)
            elif code == 13:
                if attempt == 0:
                    _invalidate_token(cfg)
                    continue
                raise PermissionError(f"SIP permission denied on {endpoint}: {data.get('message')}")
            elif code == 301:
                raise ValueError(f"SIP invalid argument on {endpoint}: {data.get('message')}")
            else:
                raise RuntimeError(f"SIP error on {endpoint}: code={code}, message={data.get('message')}")

    raise RuntimeError(f"SIP request to {endpoint} failed after retry.")


# ── Time helpers ──────────────────────────────────────────────────────────────

def _resolve_time_range(
    params: dict[str, Any],
    default_hours: int = 24,
) -> tuple[int, int]:
    """
    Resolve fromActionTime / toActionTime from params.
    Accepts integer timestamps or ISO8601 strings.
    """
    now = int(time.time())
    default_from = now - default_hours * 3600

    def _to_ts(v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, (int, float)):
            return int(v)
        s = str(v).strip()
        if s.isdigit():
            return int(s)
        # Try ISO8601
        from datetime import datetime, timezone
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(s, fmt)
                return int(dt.replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
        raise ValueError(f"Cannot parse time value: {v!r}")

    from_ts = _to_ts(params.get("from_time") or params.get("fromActionTime")) or default_from
    to_ts = _to_ts(params.get("to_time") or params.get("toActionTime")) or now
    return from_ts, to_ts


# ── Individual action handlers ────────────────────────────────────────────────

async def handle_ipgroup(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """受监控IP模块 – GET /sangforinter/v1/data/ipgroup"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=720)  # 30天，全量拉取
    max_count = int(params.get("max_count", params.get("maxCount", 10000)))
    return await _fetch_data(cfg, session, "ipgroup", from_ts, to_ts, max_count)


async def handle_server(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """已配置服务器相关信息 – GET /sangforinter/v1/data/business"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 10000)))
    return await _fetch_data(cfg, session, "business", from_ts, to_ts, max_count)


async def handle_terminal(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """资产信息-终端 – GET /sangforinter/v1/data/terminal"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 10000)))
    return await _fetch_data(cfg, session, "terminal", from_ts, to_ts, max_count)


async def handle_risk_business(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """风险业务 – GET /sangforinter/v1/data/riskbusiness"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 2000)))
    return await _fetch_data(cfg, session, "riskbusiness", from_ts, to_ts, max_count)


async def handle_risk_terminal(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """风险终端 – GET /sangforinter/v1/data/riskterminal"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 2000)))
    return await _fetch_data(cfg, session, "riskterminal", from_ts, to_ts, max_count)


async def handle_sec_event(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """安全事件 – GET /sangforinter/v1/data/riskevent"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 2000)))
    return await _fetch_data(cfg, session, "riskevent", from_ts, to_ts, max_count)


async def handle_weak_password(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """脆弱性-弱密码 – GET /sangforinter/v1/data/weakpasswd"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 5000)))
    return await _fetch_data(cfg, session, "weakpasswd", from_ts, to_ts, max_count, is_vuln=True)


async def handle_vuln_info(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """脆弱性-漏洞信息 – GET /sangforinter/v1/data/hole"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 5000)))
    return await _fetch_data(cfg, session, "hole", from_ts, to_ts, max_count, is_vuln=True)


async def handle_plain_text(cfg: RuntimeConfig, session: aiohttp.ClientSession, params: dict[str, Any]) -> dict[str, Any]:
    """脆弱性-明文传输信息 – GET /sangforinter/v1/data/plaintexttransmission"""
    from_ts, to_ts = _resolve_time_range(params, default_hours=24)
    max_count = int(params.get("max_count", params.get("maxCount", 5000)))
    return await _fetch_data(cfg, session, "plaintexttransmission", from_ts, to_ts, max_count, is_vuln=True)


# ── Dispatch map ──────────────────────────────────────────────────────────────

_ACTION_MAP = {
    "ipgroup": handle_ipgroup,
    "server": handle_server,
    "terminal": handle_terminal,
    "risk_business": handle_risk_business,
    "risk_terminal": handle_risk_terminal,
    "sec_event": handle_sec_event,
    "weak_password": handle_weak_password,
    "vuln_info": handle_vuln_info,
    "plain_text": handle_plain_text,
}


# ── Tool entry points ─────────────────────────────────────────────────────────

async def _run(action: str, params: dict[str, Any]) -> ToolResult:
    handler_fn = _ACTION_MAP.get(action)
    if handler_fn is None:
        valid = ", ".join(_ACTION_MAP.keys())
        return ToolResult(
            success=False,
            error=f"Unknown action '{action}'. Valid actions: {valid}",
        )
    try:
        cfg = _resolve_runtime_config()
        ssl_ctx = False if not cfg.verify_ssl else None
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        timeout_obj = aiohttp.ClientTimeout(total=cfg.timeout)
        async with aiohttp.ClientSession(connector=connector, timeout=timeout_obj) as session:
            data = await handler_fn(cfg, session, params)
        return ToolResult(success=True, data=data)
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))


async def run_assets(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "ipgroup")
    return await _run(action, params)


async def run_events(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "sec_event")
    return await _run(action, params)


async def run_risk(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "risk_business")
    return await _run(action, params)


async def run_vuln(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = params.pop("action", "vuln_info")
    return await _run(action, params)


# ── Registration ──────────────────────────────────────────────────────────────

def register(registry):
    registry.register("sangfor_sip_assets", run_assets)
    registry.register("sangfor_sip_events", run_events)
    registry.register("sangfor_sip_risk", run_risk)
    registry.register("sangfor_sip_vuln", run_vuln)
