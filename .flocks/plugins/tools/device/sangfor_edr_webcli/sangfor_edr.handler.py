"""Sangfor EDR browser-state authentication helper.

EDR has no stable Open API in this integration.  This handler follows the same
browser workflow used by TDP / OneSEC / SkyEye / Qingteng skills:

1. try to load the saved browser auth-state;
2. if it is still valid, reuse it;
3. if it is missing or expired, open the real EDR login page through CDP;
4. fill username, password and captcha in the browser page;
5. after login succeeds, save the full browser auth-state again.

The handler only replaces the "wait for the user to log in manually" step with
CDP-assisted form login.  It does not implement EDR business APIs.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from flocks.browser import helpers
from flocks.browser.admin import ensure_daemon
from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "sangfor_edr_v1_0_0"
LEGACY_SERVICE_ID = "sangfor_edr"
USERNAME_SECRET_ID = "sangfor_edr_username"
PASSWORD_SECRET_ID = "sangfor_edr_password"
TOKEN_SECRET_ID = "sangfor_edr_token"
DEFAULT_AUTH_STATE_PATH = "~/.flocks/browser/sangfor-edr/auth-state.json"
DEFAULT_LOGIN_PATH = "/ui/login.php"
DEFAULT_INDEX_PATH = "/ui/#/index"
DEFAULT_TIMEOUT = 25
MAX_LOCAL_STORAGE_VALUE_BYTES = 100 * 1024
CONFIG_KEYS = (
    "base_url",
    "auth_state_path",
    "auto_ocr_code",
    "max_captcha_retry",
    "login_path",
    "index_path",
    "username_selector",
    "password_selector",
    "captcha_selector",
    "agreement_selector",
    "submit_selector",
)


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        auth_state_path: Path,
        username: str,
        password: str,
        login_path: str,
        index_path: str,
        timeout: int,
        auto_ocr_code: bool,
        max_captcha_retry: int,
        username_selector: str,
        password_selector: str,
        captcha_selector: str,
        agreement_selector: str,
        submit_selector: str,
    ) -> None:
        self.base_url = base_url
        self.auth_state_path = auth_state_path
        self.username = username
        self.password = password
        self.login_path = login_path
        self.index_path = index_path
        self.timeout = timeout
        self.auto_ocr_code = auto_ocr_code
        self.max_captcha_retry = max_captcha_retry
        self.username_selector = username_selector
        self.password_selector = password_selector
        self.captcha_selector = captcha_selector
        self.agreement_selector = agreement_selector
        self.submit_selector = submit_selector


# ── Config / secret helpers ──────────────────────────────────────────────────

def _get_secret_manager():
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
    if not parsed.hostname:
        raise ValueError(f"Invalid Sangfor EDR base_url: {value!r}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{host}{port}".rstrip("/")


def _direct_api_service(service_id: str) -> dict[str, Any]:
    services = ConfigWriter.list_api_services_raw()
    service = services.get(service_id) if isinstance(services, dict) else None
    return dict(service) if isinstance(service, dict) else {}


def _has_device_context() -> bool:
    try:
        from flocks.tool.credential_context import get_active_device_id

        return bool(get_active_device_id())
    except Exception:
        return False


def _merge_missing(primary: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    for key, value in fallback.items():
        # Keep versioned/device values first, but let legacy fill missing fields.
        if merged.get(key) in (None, "") and value not in (None, ""):
            merged[key] = value
    return merged


def _load_service_config() -> dict[str, Any]:
    versioned_or_override = ConfigWriter.get_api_service_raw(SERVICE_ID)
    primary = dict(versioned_or_override) if isinstance(versioned_or_override, dict) else {}
    if _has_device_context():
        # Device-scoped calls must not borrow global legacy fields from another EDR.
        return primary
    return _merge_missing(primary, _direct_api_service(LEGACY_SERVICE_ID))


def _save_params_to_service(params: dict[str, Any]) -> dict[str, Any]:
    service = _load_service_config()
    persist_credentials = _coerce_bool(params.get("persist_credentials"), default=True)

    for key in CONFIG_KEYS:
        value = params.get(key)
        if value not in (None, ""):
            service[key] = value

    username = params.get("username")
    if isinstance(username, str) and username:
        if persist_credentials:
            _get_secret_manager().set(USERNAME_SECRET_ID, username)
            service["username"] = f"{{secret:{USERNAME_SECRET_ID}}}"
        else:
            service["username"] = username

    password = params.get("password")
    if isinstance(password, str) and password:
        if persist_credentials:
            _get_secret_manager().set(PASSWORD_SECRET_ID, password)
            service["password"] = f"{{secret:{PASSWORD_SECRET_ID}}}"
        else:
            service["password"] = password

    if persist_credentials and any(
        params.get(key) not in (None, "")
        for key in ("base_url", "auth_state_path", "username", "password")
    ):
        ConfigWriter.set_api_service(SERVICE_ID, service)

    return service


def _saved_auto_login_status(params: dict[str, Any]) -> dict[str, Any]:
    """Return non-sensitive information about saved EDR auto-login inputs."""
    service = _save_params_to_service({**params, "persist_credentials": False})
    secrets = _get_secret_manager()

    base_url = (
        _resolve_ref(service.get("base_url"))
        or _resolve_ref(service.get("host"))
        or os.getenv("SANGFOR_EDR_BASE_URL")
        or ""
    )
    auth_state_path = Path(
        _resolve_ref(service.get("auth_state_path"))
        or os.getenv("SANGFOR_EDR_AUTH_STATE")
        or DEFAULT_AUTH_STATE_PATH
    ).expanduser()
    username = (
        _resolve_ref(service.get("username"))
        or secrets.get(USERNAME_SECRET_ID)
        or secrets.get(f"{SERVICE_ID}_username")
        or secrets.get(f"{LEGACY_SERVICE_ID}_username")
        or os.getenv("SANGFOR_EDR_USERNAME")
        or ""
    )
    password = (
        _resolve_ref(service.get("password"))
        or secrets.get(PASSWORD_SECRET_ID)
        or secrets.get(f"{SERVICE_ID}_password")
        or secrets.get(f"{LEGACY_SERVICE_ID}_password")
        or os.getenv("SANGFOR_EDR_PASSWORD")
        or ""
    )
    has_base_url = bool(str(base_url or "").strip())
    has_username = bool(str(username or "").strip())
    has_password = bool(str(password or "").strip())
    return {
        "auth_state_path": str(auth_state_path),
        "auth_state_exists": auth_state_path.exists(),
        "has_base_url": has_base_url,
        "has_saved_username": has_username,
        "has_saved_password": has_password,
        "has_saved_token": bool(secrets.get(TOKEN_SECRET_ID)),
        "can_auto_refresh": has_base_url and has_username and has_password,
    }


def _resolve_runtime_config(params: dict[str, Any]) -> RuntimeConfig:
    raw = _save_params_to_service(params)
    secrets = _get_secret_manager()

    base_url = _normalise_base_url(
        _resolve_ref(raw.get("base_url"))
        or _resolve_ref(raw.get("host"))
        or os.getenv("SANGFOR_EDR_BASE_URL")
        or ""
    )
    auth_state_path = Path(
        _resolve_ref(raw.get("auth_state_path"))
        or os.getenv("SANGFOR_EDR_AUTH_STATE")
        or DEFAULT_AUTH_STATE_PATH
    ).expanduser()

    username = (
        _resolve_ref(raw.get("username"))
        or secrets.get(USERNAME_SECRET_ID)
        or secrets.get(f"{SERVICE_ID}_username")
        or secrets.get(f"{LEGACY_SERVICE_ID}_username")
        or os.getenv("SANGFOR_EDR_USERNAME")
        or ""
    ).strip()
    password = (
        _resolve_ref(raw.get("password"))
        or secrets.get(PASSWORD_SECRET_ID)
        or secrets.get(f"{SERVICE_ID}_password")
        or secrets.get(f"{LEGACY_SERVICE_ID}_password")
        or os.getenv("SANGFOR_EDR_PASSWORD")
        or ""
    ).strip()

    return RuntimeConfig(
        base_url=base_url,
        auth_state_path=auth_state_path,
        username=username,
        password=password,
        login_path=str(raw.get("login_path") or DEFAULT_LOGIN_PATH),
        index_path=str(raw.get("index_path") or DEFAULT_INDEX_PATH),
        timeout=max(5, _coerce_int(raw.get("timeout"), DEFAULT_TIMEOUT)),
        auto_ocr_code=_coerce_bool(raw.get("auto_ocr_code"), default=True),
        max_captcha_retry=max(1, _coerce_int(raw.get("max_captcha_retry"), 5)),
        username_selector=str(raw.get("username_selector") or ""),
        password_selector=str(raw.get("password_selector") or ""),
        captcha_selector=str(raw.get("captcha_selector") or ""),
        agreement_selector=str(raw.get("agreement_selector") or ""),
        submit_selector=str(raw.get("submit_selector") or ""),
    )


# ── Browser state workflow ───────────────────────────────────────────────────

def _url(cfg: RuntimeConfig, path: str) -> str:
    return urljoin(cfg.base_url + "/", path.lstrip("/"))


def _now_ms() -> str:
    return str(int(time.time() * 1000))


def _login_url(cfg: RuntimeConfig) -> str:
    return _url(cfg, cfg.login_path)


def _index_url(cfg: RuntimeConfig) -> str:
    return _url(cfg, cfg.index_path)


def _open_page(url: str) -> None:
    try:
        helpers.open_or_attach_tab(url)
    except Exception:
        helpers.goto_url(url)
    helpers.wait_for_load(timeout=15)


def _ensure_browser_daemon() -> None:
    """Start the browser daemon or replace a stale/incompatible instance."""
    ensure_daemon(wait=15.0, _open_inspect=False)


def _browser_daemon_result(exc: Exception, auth_state_path: Path) -> dict[str, Any]:
    return {
        "success": False,
        "valid": False,
        "status": "browser_daemon_not_ready",
        "reason": "browser_daemon_not_ready",
        "error": str(exc),
        "next_action": "run `flocks browser --setup`, then `flocks browser --doctor`, then retry",
        "auth_state_path": str(auth_state_path),
    }


def _browser_page_open_result(exc: Exception, cfg: RuntimeConfig) -> dict[str, Any]:
    return {
        "success": False,
        "valid": False,
        "status": "browser_login_page_open_failed",
        "reason": "browser_login_page_open_failed",
        "error": str(exc),
        "next_action": "verify base_url and EDR connectivity, then retry",
        "login_url": _login_url(cfg),
        "auth_state_path": str(cfg.auth_state_path),
    }


def _manual_login_result(cfg: RuntimeConfig, reason: str, error: str = "") -> dict[str, Any]:
    return {
        "success": False,
        "valid": False,
        "status": "manual_login_required",
        "reason": reason,
        "error": error,
        "login_url": _login_url(cfg),
        "auth_state_path": str(cfg.auth_state_path),
        "browser_left_open": True,
        "next_action": "complete login in the opened browser, then call `complete_manual_login`",
    }


def _install_login_token_capture() -> bool:
    script = r"""(() => {
  const key = "__flocks_sangfor_edr_token";
  sessionStorage.removeItem(key);
  if (window.__flocksSangforEdrTokenHooked) return true;
  window.__flocksSangforEdrTokenHooked = true;
  const capture = (text) => {
    try {
      const token = JSON.parse(text)?.data?.token;
      if (token !== undefined && token !== null && String(token)) sessionStorage.setItem(key, String(token));
    } catch (_) {}
  };
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this, args);
    const url = String(args[0]?.url || args[0] || "");
    if (url.includes("/launch_login.php")) {
      try { capture(await response.clone().text()); } catch (_) {}
    }
    return response;
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(method, url, ...rest) {
    this.__flocksEdrLogin = String(url || "").includes("/launch_login.php");
    return originalOpen.call(this, method, url, ...rest);
  };
  const originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function(...args) {
    if (this.__flocksEdrLogin) this.addEventListener("load", () => {
      try { capture(this.responseType && this.responseType !== "text" ? JSON.stringify(this.response) : this.responseText); }
      catch (_) {}
    }, {once: true});
    return originalSend.apply(this, args);
  };
  return true;
})()"""
    try:
        return bool(helpers.js(script))
    except Exception:
        return False


def _save_captured_login_token(timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            token = str(helpers.js("sessionStorage.getItem('__flocks_sangfor_edr_token') || ''") or "").strip()
            if token:
                _get_secret_manager().set(TOKEN_SECRET_ID, token)
                return True
        except Exception:
            return False
        time.sleep(0.1)
    return False


def _page_text() -> str:
    try:
        return str(helpers.js("document.body ? document.body.innerText : ''") or "")
    except Exception:
        return ""


def _looks_like_login_page(text: str, url: str) -> bool:
    haystack = f"{url}\n{text}".lower()
    markers = ("login.php", "user_name", "password", "randcode", "captcha", "验证码", "登录")
    return any(marker.lower() in haystack for marker in markers)


def _has_session_cookie(cfg: RuntimeConfig) -> bool:
    try:
        result = helpers.cdp("Network.getCookies", urls=[cfg.base_url])
    except Exception:
        return False
    cookies = result.get("cookies", [])
    if not isinstance(cookies, list):
        return False
    auth_cookie_names = {"sessionid", "jsessionid", "phpsessid", "ssid", "sid", "token"}
    return any(
        str(cookie.get("name") or "").lower() in auth_cookie_names and cookie.get("value")
        for cookie in cookies
        if isinstance(cookie, dict)
    )


def _has_logged_in_dom_marker() -> bool:
    script = """(() => {
  const selectors = [
    ".top-nav", ".navbar", ".header", ".main-header", ".layout-header",
    ".sidebar", ".side-menu", ".left-menu", ".main-menu", ".nav-menu",
    ".user-info", ".user-name", ".account-info", ".logout", "[href*='logout']",
    "#app .router-view", "#app [class*='dashboard']", "[class*='dashboard']"
  ];
  if (selectors.some((selector) => document.querySelector(selector))) {
    return true;
  }
  const text = (document.body && document.body.innerText || "").slice(0, 4000);
  return /终端概况|受管控终端|威胁资产|已失陷|设备状态|安全概况|退出登录|系统管理/.test(text);
})()"""
    try:
        return bool(helpers.js(script))
    except Exception:
        return False


def _is_logged_in(cfg: RuntimeConfig) -> bool:
    info = helpers.page_info()
    current_url = str(info.get("url") or "")
    if _looks_like_login_page(_page_text(), current_url):
        return False
    if _has_session_cookie(cfg):
        return True
    # Avoid treating blank/loading/error pages on the same host as authenticated.
    return cfg.base_url.rstrip("/") in current_url and _has_logged_in_dom_marker()


def _validate_auth_state(cfg: RuntimeConfig) -> dict[str, Any]:
    if not cfg.auth_state_path.exists():
        return {
            "valid": False,
            "reason": "auth_state_not_found",
            "auth_state_path": str(cfg.auth_state_path),
        }
    try:
        _ensure_browser_daemon()
    except Exception as exc:
        result = _browser_daemon_result(exc, cfg.auth_state_path)
        result["reason"] = "auth_state_load_failed_browser_daemon_not_ready"
        return result

    try:
        loaded = helpers.load_state(cfg.auth_state_path, url=_index_url(cfg))
        helpers.wait_for_load(timeout=15)
        if _is_logged_in(cfg):
            return {
                "valid": True,
                "reason": "browser_state_loaded",
                "auth_state_path": str(cfg.auth_state_path),
                "loaded": loaded,
            }
        return {
            "valid": False,
            "reason": "auth_state_expired_or_login_page",
            "auth_state_path": str(cfg.auth_state_path),
            "loaded": loaded,
        }
    except Exception as exc:
        return {
            "valid": False,
            "reason": "auth_state_load_failed",
            "error": str(exc),
            "auth_state_path": str(cfg.auth_state_path),
        }


# ── CDP login workflow ───────────────────────────────────────────────────────

def _selector_list(custom: str, defaults: tuple[str, ...]) -> list[str]:
    values = [item.strip() for item in custom.split(",") if item.strip()] if custom else []
    values.extend(defaults)
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def _login_selectors(cfg: RuntimeConfig) -> dict[str, list[str]]:
    return {
        "username": _selector_list(
            cfg.username_selector,
            (
                "#user",
                "input[name='user']",
                ".username-input",
                "#user_name",
                "#username",
                "input[name='user_name']",
                "input[name='username']",
                "input[type='text']",
            ),
        ),
        "password": _selector_list(
            cfg.password_selector,
            (
                "#password",
                "input[name='password']",
                ".input_text_password",
                "input[name='pwd']",
                "input[type='password']",
            ),
        ),
        "captcha": _selector_list(
            cfg.captcha_selector,
            (
                "#code",
                "input[name='code']",
                ".code_input_text",
                "#randcode",
                "#captcha",
                "input[name='randcode']",
                "input[name='captcha']",
                "input[name='verify_code']",
            ),
        ),
        "agreement": _selector_list(
            cfg.agreement_selector,
            (
                ".user-protocol-check input[type='checkbox']",
                ".sfedr-checkbox-input",
                "input[type='checkbox'][true-value='1']",
            ),
        ),
        "submit": _selector_list(
            cfg.submit_selector,
            (
                "#button",
                "input[name='button']",
                ".login-opr-btn",
                "#login",
                "#submit",
                ".login-btn",
                ".btn-login",
                "button[type='submit']",
                "input[type='submit']",
            ),
        ),
    }


def _login_dom_summary() -> Any:
    script = """(() => {
  return Array.from(document.querySelectorAll("input,button,a,img,iframe"))
    .slice(0, 80)
    .map((el) => ({
      tag: el.tagName,
      id: el.id || "",
      name: el.getAttribute("name") || "",
      type: el.getAttribute("type") || "",
      placeholder: el.getAttribute("placeholder") || "",
      value: el.tagName === "INPUT" && el.type !== "password" ? (el.value || "") : "",
      text: (el.innerText || el.value || el.getAttribute("alt") || "").trim().slice(0, 80),
      className: String(el.className || ""),
      src: el.getAttribute("src") || "",
      href: el.getAttribute("href") || ""
    }));
})()"""
    try:
        return helpers.js(script)
    except Exception as exc:
        return {"error": str(exc)}


def _wait_for_login_form_ready(cfg: RuntimeConfig) -> dict[str, bool]:
    selectors = _login_selectors(cfg)
    payload = {
        "usernameSelectors": selectors["username"],
        "passwordSelectors": selectors["password"],
        "captchaSelectors": selectors["captcha"],
        "submitSelectors": selectors["submit"],
    }
    deadline = time.time() + cfg.timeout
    last_state: dict[str, bool] = {}
    while time.time() < deadline:
        state = helpers.js(
            f"""(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=False)};
  const exists = (selectors) => selectors.some((selector) => Boolean(document.querySelector(selector)));
  return {{
    username: exists(cfg.usernameSelectors),
    password: exists(cfg.passwordSelectors),
    captcha: exists(cfg.captchaSelectors),
    submit: exists(cfg.submitSelectors)
  }};
}})()"""
        )
        last_state = state if isinstance(state, dict) else {}
        if last_state.get("username") and last_state.get("password") and last_state.get("submit"):
            return {key: bool(value) for key, value in last_state.items()}
        time.sleep(0.5)
    raise RuntimeError(
        "EDR login form was not rendered before timeout: "
        + json.dumps({"state": last_state, "dom": _login_dom_summary()}, ensure_ascii=False)
    )


def _captcha_image_data_url_from_dom() -> str:
    script = """(async () => {
  const srcAttrs = ["src", "currentSrc", "data-src", "data-url", "data-original"];
  const images = Array.from(document.querySelectorAll("img"));
  const candidates = images
    .map((img) => {
      const values = srcAttrs
        .map((attr) => attr === "currentSrc" ? img.currentSrc : img.getAttribute(attr))
        .filter(Boolean);
      const hint = [
        img.id || "",
        String(img.className || ""),
        img.alt || "",
        img.title || "",
        values.join(" ")
      ].join(" ").toLowerCase();
      return {values, hint};
    })
    .filter((item) => item.values.length)
    .sort((a, b) => {
      const score = (item) => /captcha|verify|vcode|randcode|code|验证码/.test(item.hint) ? 0 : 1;
      return score(a) - score(b);
    });

  for (const item of candidates) {
    for (const raw of item.values) {
      const url = new URL(raw, window.location.href).href;
      if (url.startsWith("data:image/")) {
        return url;
      }
      try {
        const response = await fetch(url, {credentials: "include", cache: "no-store"});
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!String(blob.type || "").startsWith("image/")) continue;
        return await new Promise((resolve, reject) => {
          const reader = new FileReader();
          reader.onerror = () => reject(reader.error || new Error("captcha image read failed"));
          reader.onload = () => resolve(String(reader.result));
          reader.readAsDataURL(blob);
        });
      } catch (_err) {
      }
    }
  }
  return "";
})()"""
    try:
        return str(helpers.js(script) or "")
    except Exception:
        return ""


def _captcha_image_from_browser(cfg: RuntimeConfig) -> bytes:
    # Prefer the live captcha image URL so versioned/customized EDR login pages work.
    data_url = _captcha_image_data_url_from_dom()
    if not data_url:
        captcha_url = _url(cfg, f"/ui/randcode.php?{_now_ms()}")
        script = f"""(async () => {{
  const response = await fetch({json.dumps(captcha_url)}, {{
    credentials: "include",
    cache: "no-store"
  }});
  if (!response.ok) {{
    throw new Error("captcha request failed: " + response.status);
  }}
  const blob = await response.blob();
  return await new Promise((resolve, reject) => {{
    const reader = new FileReader();
    reader.onerror = () => reject(reader.error || new Error("captcha read failed"));
    reader.onload = () => resolve(String(reader.result));
    reader.readAsDataURL(blob);
  }});
}})()"""
        data_url = str(helpers.js(script) or "")
    if "," not in data_url:
        raise RuntimeError("EDR captcha fetch did not return a data URL.")
    return base64.b64decode(data_url.split(",", 1)[1])


def _ocr_verify_code(image_bytes: bytes) -> str:
    try:
        import ddddocr
    except ImportError as exc:
        raise RuntimeError(
            "ddddocr is required for automatic Sangfor EDR captcha recognition."
        ) from exc
    return str(ddddocr.DdddOcr(show_ad=False).classification(image_bytes)).strip()[:4]


def _filter_large_local_storage_items(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"filtered": False, "reason": "state_json_unreadable", "error": str(exc)}
    if not isinstance(state, dict):
        return {"filtered": False, "reason": "state_not_object"}
    origins = state.get("origins")
    if not isinstance(origins, list):
        return {"filtered": False, "reason": "origins_not_list"}

    before = 0
    after = 0
    dropped = 0
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        entries = origin.get("localStorage")
        if not isinstance(entries, list):
            continue
        before += len(entries)
        kept = []
        for entry in entries:
            value = entry.get("value") if isinstance(entry, dict) else ""
            if len(str(value).encode("utf-8")) > MAX_LOCAL_STORAGE_VALUE_BYTES:
                dropped += 1
                continue
            kept.append(entry)
        after += len(kept)
        origin["localStorage"] = kept

    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "filtered": True,
        "localStorageItemsBefore": before,
        "localStorageItemsAfter": after,
        "localStorageItemsDropped": dropped,
        "maxValueBytes": MAX_LOCAL_STORAGE_VALUE_BYTES,
    }


def _save_auth_state(cfg: RuntimeConfig) -> dict[str, Any]:
    saved = helpers.save_state(cfg.auth_state_path, url=_index_url(cfg))
    # EDR does not currently need known huge localStorage values for auth.
    return {**saved, "filter": _filter_large_local_storage_items(cfg.auth_state_path)}


def _set_login_form_values(cfg: RuntimeConfig, code: str) -> dict[str, Any]:
    selectors = _login_selectors(cfg)
    payload = {
        "username": cfg.username,
        "password": cfg.password,
        "code": code,
        "usernameSelectors": selectors["username"],
        "passwordSelectors": selectors["password"],
        "captchaSelectors": selectors["captcha"],
        "agreementSelectors": selectors["agreement"],
        "submitSelectors": selectors["submit"],
    }
    script = f"""(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=False)};
  function first(selectors) {{
    for (const selector of selectors) {{
      const el = document.querySelector(selector);
      if (el) return el;
    }}
    return null;
  }}
  function setValue(el, value) {{
    if (!el) return false;
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", {{bubbles: true}}));
    el.dispatchEvent(new Event("change", {{bubbles: true}}));
    el.dispatchEvent(new KeyboardEvent("keyup", {{bubbles: true}}));
    return true;
  }}
  const username = first(cfg.usernameSelectors);
  const password = first(cfg.passwordSelectors);
  const captcha = first(cfg.captchaSelectors);
  const agreement = first(cfg.agreementSelectors);
  const filled = {{
    username: setValue(username, cfg.username),
    password: setValue(password, cfg.password),
    captcha: captcha ? setValue(captcha, cfg.code) : false,
    agreement: false
  }};
  if (!filled.username || !filled.password) {{
    throw new Error("missing EDR login username/password input");
  }}
  if (agreement) {{
    if (!agreement.checked) {{
      agreement.click();
    }}
    agreement.dispatchEvent(new Event("input", {{bubbles: true}}));
    agreement.dispatchEvent(new Event("change", {{bubbles: true}}));
    filled.agreement = Boolean(agreement.checked);
  }}
  const submit = first(cfg.submitSelectors)
    || Array.from(document.querySelectorAll("button,input[type='button'],input[type='submit'],a"))
      .find((el) => /登录|登 录|login/i.test((el.innerText || el.value || el.textContent || "").trim()));
  if (submit) {{
    submit.click();
  }} else {{
    const form = username.closest("form") || password.closest("form");
    if (!form) throw new Error("missing EDR login submit button");
    form.dispatchEvent(new Event("submit", {{bubbles: true, cancelable: true}}));
    if (typeof form.submit === "function") form.submit();
  }}
  return filled;
}})()"""
    result = helpers.js(script)
    return result if isinstance(result, dict) else {"result": result}


def _wait_for_login_success(cfg: RuntimeConfig) -> bool:
    deadline = time.time() + cfg.timeout
    while time.time() < deadline:
        try:
            if _is_logged_in(cfg):
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _manual_login_reason() -> str:
    text = _page_text().lower()
    if any(marker in text for marker in ("动态口令", "短信验证码", "二次认证", "mfa", "otp")):
        return "mfa_required"
    if any(marker in text for marker in ("ukey", "usb key", "证书登录")):
        return "certificate_login_required"
    return "login_form_not_ready"


def _missing_login_inputs(cfg: RuntimeConfig) -> list[str]:
    missing = []
    if not cfg.username:
        missing.append("username")
    if not cfg.password:
        missing.append("password")
    return missing


def _refresh_auth_state_with_cdp_login(cfg: RuntimeConfig, captcha_code: str = "") -> dict[str, Any]:
    missing = _missing_login_inputs(cfg)
    try:
        _ensure_browser_daemon()
    except Exception as exc:
        return _browser_daemon_result(exc, cfg.auth_state_path)
    try:
        _open_page(_login_url(cfg))
    except Exception as exc:
        return _browser_page_open_result(exc, cfg)
    _install_login_token_capture()
    if missing:
        result = _manual_login_result(cfg, "missing_cdp_login_credentials")
        result["missing"] = missing
        return result
    try:
        form_state = _wait_for_login_form_ready(cfg)
    except Exception as exc:
        return _manual_login_result(cfg, _manual_login_reason(), str(exc))
    last_error = ""
    for attempt in range(1, cfg.max_captcha_retry + 1):
        try:
            _install_login_token_capture()
            code = captcha_code.strip()
            if not code:
                if not cfg.auto_ocr_code:
                    return _manual_login_result(cfg, "captcha_code_required")
                code = _ocr_verify_code(_captcha_image_from_browser(cfg))

            filled = _set_login_form_values(cfg, code)
            if _wait_for_login_success(cfg):
                token_saved = _save_captured_login_token()
                saved = _save_auth_state(cfg)
                return {
                    "success": True,
                    "status": "browser_cdp_login_refreshed_auth_state",
                    "auth_state_path": str(cfg.auth_state_path),
                    "attempt": attempt,
                    "form": form_state,
                    "filled": {key: bool(value) for key, value in filled.items()},
                    "saved": saved,
                    "token_saved": token_saved,
                }
            last_error = "login_success_check_timeout"
        except Exception as exc:
            last_error = str(exc)

        if captcha_code:
            break
        try:
            helpers.goto_url(_login_url(cfg))
            helpers.wait_for_load(timeout=10)
        except Exception:
            pass

    return _manual_login_result(cfg, "browser_cdp_login_failed", last_error)


def _complete_manual_login(cfg: RuntimeConfig) -> dict[str, Any]:
    try:
        _ensure_browser_daemon()
    except Exception as exc:
        return _browser_daemon_result(exc, cfg.auth_state_path)
    try:
        info = helpers.page_info()
        if urlparse(str(info.get("url") or "")).netloc != urlparse(cfg.base_url).netloc:
            _open_page(_index_url(cfg))
        if not _is_logged_in(cfg):
            return _manual_login_result(cfg, "manual_login_not_completed")
        token_saved = _save_captured_login_token()
        return {
            "success": True,
            "valid": True,
            "status": "manual_login_captured_auth_state",
            "auth_state_path": str(cfg.auth_state_path),
            "saved": _save_auth_state(cfg),
            "token_saved": token_saved,
        }
    except Exception as exc:
        return {
            "success": False,
            "valid": False,
            "status": "manual_login_capture_failed",
            "reason": "manual_login_capture_failed",
            "error": str(exc),
            "auth_state_path": str(cfg.auth_state_path),
            "next_action": "keep the browser open and retry `complete_manual_login`",
        }


# ── Tool actions ─────────────────────────────────────────────────────────────

def _auth_state_loaded_output(validation: dict[str, Any]) -> dict[str, Any]:
    return {
        "success": True,
        "status": "auth_state_loaded",
        **validation,
    }


async def handle(ctx: ToolContext) -> ToolResult:
    params = dict(ctx.params)
    action = str(params.get("action") or "ensure_auth_state").strip()

    try:
        if action == "status_auth_state":
            status = _saved_auto_login_status(params)
            validation: dict[str, Any] | None = None
            if status.get("has_base_url"):
                try:
                    validation = _validate_auth_state(_resolve_runtime_config({**params, "persist_credentials": False}))
                except Exception as exc:
                    validation = {
                        "valid": False,
                        "reason": "auth_state_validate_failed",
                        "error": str(exc),
                        "auth_state_path": status["auth_state_path"],
                    }
            return ToolResult(
                success=True,
                output={
                    "success": True,
                    "status": "saved_auto_login_status",
                    **status,
                    "validation": validation,
                },
            )

        cfg = _resolve_runtime_config(params)

        if action == "validate_auth_state":
            validation = _validate_auth_state(cfg)
            return ToolResult(
                success=bool(validation.get("valid")),
                output=validation,
                error=None if validation.get("valid") else str(validation.get("reason") or "auth_state_invalid"),
            )

        if action == "complete_manual_login":
            result = _complete_manual_login(cfg)
            return ToolResult(
                success=bool(result.get("success")),
                output=result,
                error=None if result.get("success") else result.get("reason"),
            )

        if action not in {"ensure_auth_state", "refresh_auth_state"}:
            return ToolResult(
                success=False,
                error="Unsupported Sangfor EDR auth action. Use status_auth_state, ensure_auth_state, validate_auth_state, refresh_auth_state, or complete_manual_login.",
            )

        force_refresh = action == "refresh_auth_state" or _coerce_bool(params.get("force_refresh"), default=False)
        if not force_refresh:
            validation = _validate_auth_state(cfg)
            if validation.get("valid"):
                return ToolResult(success=True, output=_auth_state_loaded_output(validation))

        result = _refresh_auth_state_with_cdp_login(
            cfg,
            captcha_code=str(params.get("captcha_code") or ""),
        )
        return ToolResult(
            success=bool(result.get("success")),
            output=result,
            error=None if result.get("success") else result.get("reason"),
        )
    except Exception as exc:
        return ToolResult(success=False, error=str(exc))
