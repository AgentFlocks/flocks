"""Standalone Sangfor EDR HTTP authentication and auth-pair storage."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

import requests
import urllib3

from flocks.config.config_writer import ConfigWriter

SERVICE_ID = "sangfor_edr_v1_0_0"
LEGACY_SERVICE_ID = "sangfor_edr"
USERNAME_SECRET_ID = "sangfor_edr_username"
PASSWORD_SECRET_ID = "sangfor_edr_password"
TOKEN_SECRET_ID = "sangfor_edr_token"
TOKEN_BUNDLE_SECRET_ID = "sangfor_edr_token_bundle"
DEFAULT_AUTH_STATE_PATH = "~/.flocks/browser/sangfor-edr/auth-state.json"
DEFAULT_LOGIN_PATH = "/ui/login.php"
DEFAULT_TIMEOUT = 25
MAX_HTTP_LOGIN_ATTEMPTS = 3
PUBLIC_EXPONENT = 0x10001
CONFIG_KEYS = (
    "base_url",
    "auth_state_path",
    "auto_ocr_code",
    "max_captcha_retry",
    "login_path",
)

BrowserLoginFallback = Callable[["RuntimeConfig", str], dict[str, Any]]
_browser_login_fallback: Optional[BrowserLoginFallback] = None


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        auth_state_path: Path,
        username: str,
        password: str,
        login_path: str,
        timeout: int,
        auto_ocr_code: bool,
        max_captcha_retry: int,
    ) -> None:
        self.base_url = base_url
        self.auth_state_path = auth_state_path
        self.username = username
        self.password = password
        self.login_path = login_path
        self.timeout = timeout
        self.auto_ocr_code = auto_ocr_code
        self.max_captcha_retry = max_captcha_retry


def _get_secret_manager():
    from flocks.security import get_secret_manager

    return get_secret_manager()


def register_browser_login_fallback(callback: Optional[BrowserLoginFallback]) -> None:
    global _browser_login_fallback
    _browser_login_fallback = callback


def _resolve_ref(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        return str(value)
    if value.startswith("{secret:") and value.endswith("}"):
        return _get_secret_manager().get(value[8:-1])
    if value.startswith("{env:") and value.endswith("}"):
        return os.getenv(value[5:-1])
    return value


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _normalise_base_url(value: str) -> str:
    candidate = value.strip()
    if not candidate:
        raise ValueError("Sangfor EDR base_url is required.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"Invalid Sangfor EDR base_url: {value!r}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}".rstrip("/")


def _has_device_context() -> bool:
    try:
        from flocks.tool.credential_context import get_active_device_id

        return bool(get_active_device_id())
    except Exception:
        return False


def _load_service_config() -> dict[str, Any]:
    primary = ConfigWriter.get_api_service_raw(SERVICE_ID)
    primary = dict(primary) if isinstance(primary, dict) else {}
    if _has_device_context():
        return primary
    legacy = ConfigWriter.list_api_services_raw()
    fallback = legacy.get(LEGACY_SERVICE_ID) if isinstance(legacy, dict) else {}
    for key, value in (fallback or {}).items():
        if primary.get(key) in (None, "") and value not in (None, ""):
            primary[key] = value
    return primary


def _save_params_to_service(params: dict[str, Any]) -> dict[str, Any]:
    service = _load_service_config()
    persist = _coerce_bool(params.get("persist_credentials"), default=True)
    for key in CONFIG_KEYS:
        if params.get(key) not in (None, ""):
            service[key] = params[key]
    for key, secret_id in (("username", USERNAME_SECRET_ID), ("password", PASSWORD_SECRET_ID)):
        value = params.get(key)
        if isinstance(value, str) and value:
            if persist:
                _get_secret_manager().set(secret_id, value)
                service[key] = f"{{secret:{secret_id}}}"
            else:
                service[key] = value
    if persist and any(params.get(key) not in (None, "") for key in ("base_url", "auth_state_path", "username", "password")):
        ConfigWriter.set_api_service(SERVICE_ID, service)
    return service


def resolve_runtime_config(params: dict[str, Any]) -> RuntimeConfig:
    raw = _save_params_to_service(params)
    secrets_store = _get_secret_manager()
    base_url = _normalise_base_url(
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("host"))
        or os.getenv("SANGFOR_EDR_BASE_URL")
        or ""
    )
    state_path = Path(
        _resolve_ref(raw.get("auth_state_path"))
        or os.getenv("SANGFOR_EDR_AUTH_STATE")
        or DEFAULT_AUTH_STATE_PATH
    ).expanduser()
    username = (
        _resolve_ref(raw.get("username"))
        or secrets_store.get(USERNAME_SECRET_ID)
        or secrets_store.get(f"{SERVICE_ID}_username")
        or secrets_store.get(f"{LEGACY_SERVICE_ID}_username")
        or os.getenv("SANGFOR_EDR_USERNAME")
        or ""
    ).strip()
    password = (
        _resolve_ref(raw.get("password"))
        or secrets_store.get(PASSWORD_SECRET_ID)
        or secrets_store.get(f"{SERVICE_ID}_password")
        or secrets_store.get(f"{LEGACY_SERVICE_ID}_password")
        or os.getenv("SANGFOR_EDR_PASSWORD")
        or ""
    ).strip()
    return RuntimeConfig(
        base_url=base_url,
        auth_state_path=state_path,
        username=username,
        password=password,
        login_path=str(raw.get("login_path") or DEFAULT_LOGIN_PATH),
        timeout=max(5, _coerce_int(raw.get("timeout"), DEFAULT_TIMEOUT)),
        auto_ocr_code=_coerce_bool(raw.get("auto_ocr_code"), default=True),
        max_captcha_retry=max(1, min(10, _coerce_int(raw.get("max_captcha_retry"), 5))),
    )


def _url(cfg: RuntimeConfig, path: str) -> str:
    return urljoin(cfg.base_url + "/", path.lstrip("/"))


def _now_ms() -> str:
    return str(int(time.time() * 1000))


def _query_id() -> str:
    return f"Query_{_now_ms()}"


def _safe_error(exc: Exception, *sensitive_values: str) -> str:
    message = str(exc)
    for value in sensitive_values:
        if value:
            message = message.replace(value, "<redacted>")
    return message


def _http_headers(cfg: RuntimeConfig, *, image: bool = False) -> dict[str, str]:
    headers = {
        "Pragma": "no-cache",
        "Cache-Control": "no-cache",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": _url(cfg, cfg.login_path) if image else _url(cfg, "/ui/"),
    }
    if image:
        headers["Accept"] = "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8"
    else:
        headers.update({"Accept": "application/json, text/plain, */*", "Content-Type": "application/json", "Origin": cfg.base_url})
    return headers


def http_session(cfg: RuntimeConfig) -> requests.Session:
    session = requests.Session()
    session.verify = False
    session.headers.update({"Accept-Language": "zh-CN,zh;q=0.9"})
    session.cookies.set("hadSetUkey", "0", domain=urlparse(cfg.base_url).hostname, path="/")
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def _cookie_state(cookies: CookieJar, cfg: RuntimeConfig) -> list[dict[str, Any]]:
    domain = urlparse(cfg.base_url).hostname or ""
    result = []
    for cookie in cookies:
        item = {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain or domain,
            "path": cookie.path or "/",
            "secure": bool(cookie.secure),
            "httpOnly": bool(cookie.has_nonstandard_attr("HttpOnly")),
            "sameSite": "Lax",
        }
        if cookie.expires and cookie.expires > 0:
            item["expires"] = float(cookie.expires)
        result.append(item)
    return result


def _cookie_fingerprint(cookies: list[dict[str, Any]], base_url: str) -> str:
    normalized = sorted(
        (
            str(cookie.get("name") or ""),
            str(cookie.get("value") or ""),
            str(cookie.get("domain") or ""),
            str(cookie.get("path") or "/"),
        )
        for cookie in cookies
        if isinstance(cookie, dict) and cookie.get("name")
    )
    raw = json.dumps({"base_url": _normalise_base_url(base_url), "cookies": normalized}, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _read_auth_state(path: Path) -> dict[str, Any]:
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("cookies"), list):
        raise ValueError("EDR auth-state does not contain a cookie list.")
    return state


def _save_auth_pair(cfg: RuntimeConfig, state: dict[str, Any], token: str) -> dict[str, Any]:
    cookies = state.get("cookies")
    if not isinstance(cookies, list) or not cookies or not token:
        raise ValueError("EDR login did not return a complete cookie/token pair.")
    cfg.auth_state_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = cfg.auth_state_path.with_name(f"{cfg.auth_state_path.name}.tmp")
    temp_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(cfg.auth_state_path)
    fingerprint = _cookie_fingerprint(cookies, cfg.base_url)
    manager = _get_secret_manager()
    manager.set(TOKEN_SECRET_ID, token)
    manager.set(TOKEN_BUNDLE_SECRET_ID, json.dumps({"token": token, "base_url": cfg.base_url, "cookie_fingerprint": fingerprint}, separators=(",", ":")))
    return {"auth_state_path": str(cfg.auth_state_path), "cookie_count": len(cookies), "pair_verified": True}


def load_verified_auth_pair(cfg: RuntimeConfig) -> tuple[dict[str, Any], str]:
    state = _read_auth_state(cfg.auth_state_path)
    raw_bundle = _get_secret_manager().get(TOKEN_BUNDLE_SECRET_ID)
    if not raw_bundle:
        raise RuntimeError("EDR cookie/token pair metadata is missing; refresh authentication.")
    try:
        bundle = json.loads(raw_bundle)
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError("EDR cookie/token pair metadata is invalid; refresh authentication.") from exc
    token = str(bundle.get("token") or "")
    if (
        not token
        or _normalise_base_url(str(bundle.get("base_url") or "")) != cfg.base_url
        or not secrets.compare_digest(str(bundle.get("cookie_fingerprint") or ""), _cookie_fingerprint(state["cookies"], cfg.base_url))
    ):
        raise RuntimeError("EDR cookies and login_token are not from the same login; refresh authentication.")
    return state, token


def dashboard_session(cfg: RuntimeConfig, state: dict[str, Any]) -> requests.Session:
    session = requests.Session()
    session.verify = False
    host = urlparse(cfg.base_url).hostname or ""
    for cookie in state.get("cookies", []):
        if isinstance(cookie, dict) and cookie.get("name"):
            session.cookies.set(str(cookie["name"]), str(cookie.get("value") or ""), domain=str(cookie.get("domain") or host), path=str(cookie.get("path") or "/"))
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return session


def _unix_date_range(days: int = 7) -> dict[str, int]:
    end = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=max(1, days) - 1)).replace(hour=0, minute=0, second=0)
    return {"start": int(start.timestamp()), "end": int(end.timestamp())}


def _response_contains_agent_overview(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("success")) and any(
        key in payload and payload.get(key) is not None
        for key in ("data", "result", "agent_overview", "agent_total", "total", "count")
    )


def probe_auth_pair(cfg: RuntimeConfig) -> dict[str, Any]:
    try:
        state, token = load_verified_auth_pair(cfg)
    except Exception as exc:
        return {"valid": False, "reason": "auth_pair_missing_or_mismatched", "error": str(exc)}
    session = dashboard_session(cfg, state)
    try:
        response = session.post(
            _url(cfg, f"/launch.php?s={token}&opr=get_agent_overview"),
            headers=_http_headers(cfg),
            json={"app_args": {"name": "app.web.event_center.head", "options": {}}, "auto": 1, "opr": "get_agent_overview", "date_range": _unix_date_range(), "query_id": _query_id()},
            timeout=cfg.timeout,
            allow_redirects=False,
        )
    except Exception as exc:
        return {"valid": False, "reason": "auth_probe_request_failed", "error": _safe_error(exc, token)}
    if response.status_code in {301, 302, 303, 307, 308}:
        return {"valid": False, "reason": "auth_probe_redirected", "http_status": response.status_code, "location": str(response.headers.get("Location") or "")}
    if response.status_code in {401, 403}:
        return {"valid": False, "reason": "auth_probe_unauthorized", "http_status": response.status_code}
    if response.status_code != 200:
        return {"valid": False, "reason": "auth_probe_http_error", "http_status": response.status_code}
    try:
        payload = response.json()
    except Exception as exc:
        return {"valid": False, "reason": "auth_probe_invalid_json", "error": str(exc)}
    if not isinstance(payload, dict) or not payload.get("success"):
        return {"valid": False, "reason": "auth_probe_rejected"}
    if not _response_contains_agent_overview(payload):
        return {"valid": False, "reason": "auth_probe_expected_agent_data_missing"}
    return {"valid": True, "reason": "auth_probe_succeeded", "http_status": 200, "agent_overview_verified": True}


def _rsa_encrypt_password(rsa_key_hex: str, password: str) -> str:
    modulus = int(rsa_key_hex.strip().lower().removeprefix("0x"), 16)
    key_size = (modulus.bit_length() + 7) // 8
    message = password.encode("utf-8")
    if key_size < len(message) + 11:
        raise ValueError("EDR password is too long for the login RSA key.")
    padding = bytearray()
    while len(padding) < key_size - len(message) - 3:
        padding.extend(byte for byte in secrets.token_bytes(key_size) if byte)
    encoded = b"\x00\x02" + bytes(padding[: key_size - len(message) - 3]) + b"\x00" + message
    return f"{pow(int.from_bytes(encoded, 'big'), PUBLIC_EXPONENT, modulus):0{key_size * 2}x}"


def _ocr_verify_code(image: bytes) -> str:
    try:
        import ddddocr
    except Exception as exc:
        raise RuntimeError("ddddocr is required for automatic Sangfor EDR captcha recognition.") from exc
    return ddddocr.DdddOcr(show_ad=False).classification(image).strip()[:4]


def _is_captcha_failure(result: dict[str, Any]) -> bool:
    message = str(result.get("msg") or "")
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return "验证码" in message or data.get("code") is True


def _http_login(cfg: RuntimeConfig, captcha_code: str = "") -> dict[str, Any]:
    missing = [key for key, value in (("username", cfg.username), ("password", cfg.password)) if not value]
    if missing:
        return {"success": False, "status": "http_login_credentials_required", "reason": "missing_http_login_credentials", "missing": missing}
    session = http_session(cfg)
    phase = "session_init"
    try:
        phase = "login_page"
        session.get(_url(cfg, cfg.login_path), headers=_http_headers(cfg), timeout=cfg.timeout).raise_for_status()
        phase = "rsakey"
        rsa_response = session.post(_url(cfg, "/login"), headers=_http_headers(cfg), json={"opr": "rsakey"}, timeout=cfg.timeout)
        rsa_response.raise_for_status()
        rsa_result = rsa_response.json()
        rsa_key = str(rsa_result.get("key") or "")
        if not rsa_result.get("success") or not rsa_key:
            raise RuntimeError("EDR RSA key request failed.")
        last_error = ""
        for attempt in range(1, cfg.max_captcha_retry + 1):
            code = captcha_code.strip()
            if not code:
                if not cfg.auto_ocr_code:
                    return {"success": False, "status": "http_login_captcha_required", "reason": "captcha_code_required"}
                phase = "captcha"
                captcha = session.get(_url(cfg, f"/ui/randcode.php?{_now_ms()}"), headers=_http_headers(cfg, image=True), timeout=cfg.timeout)
                captcha.raise_for_status()
                code = _ocr_verify_code(captcha.content)
            phase = "dlogin"
            login_response = session.post(
                _url(cfg, "/login"),
                headers=_http_headers(cfg),
                json={"opr": "dlogin", "data": {"auth_type": "pwd", "user_name": cfg.username, "code": code, "pwd": _rsa_encrypt_password(rsa_key, cfg.password)}},
                timeout=cfg.timeout,
            )
            login_response.raise_for_status()
            login_result = login_response.json()
            if not login_result.get("success") or login_result.get("key") in (None, ""):
                last_error = str(login_result.get("msg") or "EDR dlogin failed.")
                if captcha_code or not _is_captcha_failure(login_result):
                    raise RuntimeError(last_error)
                continue
            phase = "launch_login"
            launch_response = session.post(
                _url(cfg, "/launch_login.php"),
                headers=_http_headers(cfg),
                json={"opr": "dlogin", "app_args": {"name": "app.web.auth.login", "options": {}}, "data": {"key": login_result["key"], "user_aggreement_status": "true"}},
                timeout=cfg.timeout,
            )
            launch_response.raise_for_status()
            launch_result = launch_response.json()
            token = str((launch_result.get("data") or {}).get("token") or "")
            if launch_result.get("success") and token:
                phase = "ui"
                session.get(
                    _url(cfg, "/ui"),
                    headers=_http_headers(cfg),
                    timeout=cfg.timeout,
                ).raise_for_status()
            cookies = _cookie_state(session.cookies, cfg)
            if launch_result.get("success") and token and any(cookie["name"].lower() == "sessionid" for cookie in cookies):
                saved = _save_auth_pair(cfg, {"cookies": cookies, "origins": []}, token)
                return {"success": True, "valid": True, "status": "http_login_refreshed_auth_state", "attempt": attempt, "token_saved": True, "saved": saved}
            last_error = str(launch_result.get("msg") or "incomplete EDR cookie/token response")
            if captcha_code:
                break
        raise RuntimeError(last_error or "EDR HTTP login retry limit exceeded.")
    except Exception as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        suffix = f"; http_status={status}" if status is not None else ""
        return {"success": False, "valid": False, "status": "http_login_failed", "reason": "http_login_failed", "error": f"phase={phase}{suffix}; detail={_safe_error(exc, cfg.username, cfg.password)}", "phase": phase}


def ensure_http_auth_pair(cfg: RuntimeConfig, captcha_code: str = "") -> dict[str, Any]:
    probe = probe_auth_pair(cfg)
    if probe.get("valid"):
        return {"success": True, "valid": True, "status": "http_auth_pair_reused", "login_skipped": True, "probe": probe}

    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] = {
        "success": False,
        "valid": False,
        "status": "http_login_failed",
        "reason": "http_login_failed",
    }
    for attempt in range(1, MAX_HTTP_LOGIN_ATTEMPTS + 1):
        result = _http_login(cfg, captcha_code=captcha_code)
        result["http_login_attempt"] = attempt
        if result.get("success"):
            confirmation = probe_auth_pair(cfg)
            result["probe"] = confirmation
            if confirmation.get("valid"):
                result.update(
                    {
                        "success": True,
                        "valid": True,
                        "previous_probe": probe,
                        "http_login_attempts": attempts,
                    }
                )
                return result
            result.update(
                {
                    "success": False,
                    "valid": False,
                    "status": "http_login_probe_failed",
                    "reason": str(confirmation.get("reason") or "http_login_probe_failed"),
                }
            )

        attempts.append(
            {
                key: result.get(key)
                for key in ("http_login_attempt", "status", "reason", "phase", "error")
                if result.get(key) not in (None, "")
            }
        )
        last_result = result
        if result.get("status") in {"http_login_credentials_required", "http_login_captcha_required"}:
            last_result.update({"previous_probe": probe, "http_login_attempts": attempts})
            return last_result

    if _browser_login_fallback is None:
        last_result.update(
            {
                "previous_probe": probe,
                "http_login_attempts": attempts,
                "browser_fallback_attempted": False,
                "browser_fallback_unavailable": True,
            }
        )
        return last_result

    try:
        fallback = _browser_login_fallback(cfg, captcha_code)
    except Exception as exc:
        fallback = {
            "success": False,
            "valid": False,
            "status": "browser_cdp_login_failed",
            "reason": "browser_cdp_login_failed",
            "error": _safe_error(exc, cfg.username, cfg.password),
        }
    fallback.update(
        {
            "previous_probe": probe,
            "http_login_attempts": attempts,
            "browser_fallback_attempted": True,
        }
    )
    if fallback.get("success"):
        confirmation = probe_auth_pair(cfg)
        fallback["probe"] = confirmation
        if confirmation.get("valid"):
            fallback.update({"valid": True, "login_skipped": False})
            return fallback
        fallback.update(
            {
                "success": False,
                "valid": False,
                "status": "manual_login_required",
                "reason": str(confirmation.get("reason") or "browser_cdp_login_probe_failed"),
                "browser_left_open": True,
                "next_action": "complete the login in the open browser, then call complete_manual_login",
            }
        )
    return fallback


def status_auth_state(params: dict[str, Any]) -> dict[str, Any]:
    service = _save_params_to_service({**params, "persist_credentials": False})
    manager = _get_secret_manager()
    base_url = _resolve_ref(service.get("base_url")) or os.getenv("SANGFOR_EDR_BASE_URL") or ""
    path = Path(_resolve_ref(service.get("auth_state_path")) or os.getenv("SANGFOR_EDR_AUTH_STATE") or DEFAULT_AUTH_STATE_PATH).expanduser()
    username = _resolve_ref(service.get("username")) or manager.get(USERNAME_SECRET_ID) or ""
    password = _resolve_ref(service.get("password")) or manager.get(PASSWORD_SECRET_ID) or ""
    probe = None
    if base_url and path.exists():
        try:
            probe = probe_auth_pair(resolve_runtime_config({**params, "persist_credentials": False}))
        except Exception as exc:
            probe = {"valid": False, "reason": "auth_probe_failed", "error": str(exc)}
    return {
        "auth_state_path": str(path),
        "auth_state_exists": path.exists(),
        "has_base_url": bool(str(base_url).strip()),
        "has_saved_username": bool(str(username).strip()),
        "has_saved_password": bool(str(password).strip()),
        "has_saved_token": bool(manager.get(TOKEN_SECRET_ID)),
        "has_verified_auth_pair": bool(probe and probe.get("valid")),
        "auth_probe": probe,
        "can_auto_refresh": bool(str(base_url).strip() and str(username).strip() and str(password).strip()),
    }


def run_auth_action(params: dict[str, Any]) -> dict[str, Any]:
    action = str(params.get("action") or "ensure_auth_state")
    if action == "status_auth_state":
        status = status_auth_state(params)
        return {
            "success": True,
            "status": "saved_auto_login_status",
            **status,
            "validation": status.get("auth_probe"),
        }
    if action not in {"ensure_auth_state", "refresh_auth_state", "http_login"}:
        raise ValueError(f"Unsupported HTTP EDR auth action: {action}")
    cfg = resolve_runtime_config(params)
    return ensure_http_auth_pair(cfg, captcha_code=str(params.get("captcha_code") or ""))
