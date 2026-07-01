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
from flocks.config.config_writer import ConfigWriter
from flocks.tool.registry import ToolContext, ToolResult

SERVICE_ID = "sangfor_edr"
USERNAME_SECRET_ID = "sangfor_edr_username"
PASSWORD_SECRET_ID = "sangfor_edr_password"
DEFAULT_AUTH_STATE_PATH = "~/.flocks/browser/sangfor-edr/auth-state.json"
DEFAULT_LOGIN_PATH = "/ui/login.php"
DEFAULT_INDEX_PATH = "/ui/#/index"
DEFAULT_TIMEOUT = 25


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


def _save_params_to_service(params: dict[str, Any]) -> dict[str, Any]:
    raw = ConfigWriter.get_api_service_raw(SERVICE_ID)
    service = dict(raw) if isinstance(raw, dict) else {}

    for key in (
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
    ):
        value = params.get(key)
        if value not in (None, ""):
            service[key] = value

    secrets = _get_secret_manager()
    username = params.get("username")
    if isinstance(username, str) and username:
        secrets.set(USERNAME_SECRET_ID, username)
        service["username"] = f"{{secret:{USERNAME_SECRET_ID}}}"

    password = params.get("password")
    if isinstance(password, str) and password:
        secrets.set(PASSWORD_SECRET_ID, password)
        service["password"] = f"{{secret:{PASSWORD_SECRET_ID}}}"

    if _coerce_bool(params.get("persist_credentials"), default=True) and any(
        params.get(key) not in (None, "")
        for key in ("base_url", "auth_state_path", "username", "password")
    ):
        ConfigWriter.set_api_service(SERVICE_ID, service)

    return service


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
        or os.getenv("SANGFOR_EDR_USERNAME")
        or ""
    ).strip()
    password = (
        _resolve_ref(raw.get("password"))
        or secrets.get(PASSWORD_SECRET_ID)
        or secrets.get(f"{SERVICE_ID}_password")
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
    return any(
        str(cookie.get("name") or "").lower() == "sessionid" and cookie.get("value")
        for cookie in cookies
        if isinstance(cookie, dict)
    )


def _is_logged_in(cfg: RuntimeConfig) -> bool:
    info = helpers.page_info()
    current_url = str(info.get("url") or "")
    if _looks_like_login_page(_page_text(), current_url):
        return False
    return _has_session_cookie(cfg) or cfg.base_url.rstrip("/") in current_url


def _validate_auth_state(cfg: RuntimeConfig) -> dict[str, Any]:
    if not cfg.auth_state_path.exists():
        return {
            "valid": False,
            "reason": "auth_state_not_found",
            "auth_state_path": str(cfg.auth_state_path),
        }
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


def _captcha_image_from_browser(cfg: RuntimeConfig) -> bytes:
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


def _missing_login_inputs(cfg: RuntimeConfig) -> list[str]:
    missing = []
    if not cfg.username:
        missing.append("username")
    if not cfg.password:
        missing.append("password")
    return missing


def _refresh_auth_state_with_cdp_login(cfg: RuntimeConfig, captcha_code: str = "") -> dict[str, Any]:
    missing = _missing_login_inputs(cfg)
    if missing:
        return {
            "success": False,
            "status": "manual_login_required",
            "reason": "missing_cdp_login_credentials",
            "missing": missing,
            "auth_state_path": str(cfg.auth_state_path),
        }

    _open_page(_login_url(cfg))
    form_state = _wait_for_login_form_ready(cfg)
    last_error = ""
    for attempt in range(1, cfg.max_captcha_retry + 1):
        try:
            code = captcha_code.strip()
            if not code:
                if not cfg.auto_ocr_code:
                    return {
                        "success": False,
                        "status": "manual_login_required",
                        "reason": "captcha_code_required",
                        "auth_state_path": str(cfg.auth_state_path),
                    }
                code = _ocr_verify_code(_captcha_image_from_browser(cfg))

            filled = _set_login_form_values(cfg, code)
            if _wait_for_login_success(cfg):
                saved = helpers.save_state(cfg.auth_state_path, url=_index_url(cfg))
                return {
                    "success": True,
                    "status": "browser_cdp_login_refreshed_auth_state",
                    "auth_state_path": str(cfg.auth_state_path),
                    "attempt": attempt,
                    "form": form_state,
                    "filled": {key: bool(value) for key, value in filled.items()},
                    "saved": saved,
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

    return {
        "success": False,
        "status": "manual_login_required",
        "reason": "browser_cdp_login_failed",
        "last_error": last_error,
        "auth_state_path": str(cfg.auth_state_path),
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
        cfg = _resolve_runtime_config(params)

        if action == "validate_auth_state":
            validation = _validate_auth_state(cfg)
            return ToolResult(success=bool(validation.get("valid")), output=validation)

        if action not in {"ensure_auth_state", "refresh_auth_state"}:
            return ToolResult(
                success=False,
                error="Unsupported Sangfor EDR auth action. Use ensure_auth_state, validate_auth_state, or refresh_auth_state.",
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
