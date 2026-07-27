#!/usr/bin/env python3
"""SkyEye browser auth-state helper.

This script follows the skill's existing browser workflow and only automates
the manual login step when credentials are available:

1. load and validate the saved browser auth-state;
2. if it is missing or expired, open the real login page with CDP;
3. wait for the async-rendered login form;
4. fetch and OCR the captcha in the browser session;
5. fill the form, submit it, and save a refreshed auth-state.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

from flocks.browser import helpers

AUTH_DIR = Path.home() / ".flocks" / "browser" / "skyeye"
DEFAULT_AUTH_STATE = AUTH_DIR / "auth-state.json"
AUTH_CONFIG = AUTH_DIR / "auth-config.json"
USERNAME_SECRET_ID = "skyeye_username"
PASSWORD_SECRET_ID = "skyeye_password"
DEFAULT_LOGIN_PATH = "/skyeye/home/login"
DEFAULT_CAPTCHA_PATH = "/skyeye/v1/admin/code"
DEFAULT_TIMEOUT = 25
ESSENTIAL_LOCAL_STORAGE_KEYS = {"csrf_token", "csrfToken", "system_type"}
MAX_LOCAL_STORAGE_VALUE_BYTES = 4096


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


def _split_base_url_and_path(value: str) -> tuple[str, str]:
    candidate = value.strip()
    if not candidate:
        raise ValueError("SkyEye base_url is required.")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    parsed = urlparse(candidate)
    if not parsed.hostname:
        raise ValueError(f"Invalid SkyEye base_url: {value!r}")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port else ""
    path = parsed.path or ""
    return f"{parsed.scheme}://{host}{port}".rstrip("/"), path


def _normalise_base_url(value: str) -> str:
    base_url, _path = _split_base_url_and_path(value)
    return base_url


def _login_path_from_url(value: str) -> str:
    _base_url, path = _split_base_url_and_path(value)
    return path if path and path != "/" else ""


def _read_config() -> dict[str, Any]:
    if not AUTH_CONFIG.exists():
        return {}
    try:
        data = json.loads(AUTH_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(data: dict[str, Any]) -> None:
    AUTH_DIR.mkdir(parents=True, exist_ok=True)
    AUTH_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _persist_inputs(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    if args.base_url:
        config["base_url"] = _normalise_base_url(args.base_url)
        inferred_login_path = _login_path_from_url(args.base_url)
        if inferred_login_path and not args.login_path:
            config["login_path"] = inferred_login_path
    if args.auth_state:
        config["auth_state_path"] = str(Path(args.auth_state).expanduser())
    if args.login_path:
        config["login_path"] = args.login_path
    if args.captcha_path:
        config["captcha_path"] = args.captcha_path

    if args.base_url or args.auth_state or args.login_path or args.captcha_path:
        _write_config(config)

    if args.save_credentials:
        secrets = _get_secret_manager()
        if args.username:
            secrets.set(USERNAME_SECRET_ID, args.username)
            config["username"] = f"{{secret:{USERNAME_SECRET_ID}}}"
        if args.password:
            secrets.set(PASSWORD_SECRET_ID, args.password)
            config["password"] = f"{{secret:{PASSWORD_SECRET_ID}}}"
        if args.username or args.password:
            _write_config(config)
    return config


def saved_auto_login_status() -> dict[str, Any]:
    """Return non-sensitive information about saved auto-login inputs."""
    config = _read_config()
    username = _resolve_ref(config.get("username")) or _get_secret_manager().get(USERNAME_SECRET_ID)
    password = _resolve_ref(config.get("password")) or _get_secret_manager().get(PASSWORD_SECRET_ID)
    base_url = _resolve_ref(config.get("base_url")) or os.getenv("SKYEYE_BASE_URL")
    auth_state_path = Path(
        _resolve_ref(config.get("auth_state_path"))
        or os.getenv("SKYEYE_AUTH_STATE")
        or DEFAULT_AUTH_STATE
    ).expanduser()
    has_username = bool(str(username or "").strip())
    has_password = bool(str(password or "").strip())
    has_base_url = bool(str(base_url or "").strip())
    return {
        "auth_state_path": str(auth_state_path),
        "auth_state_exists": auth_state_path.exists(),
        "auth_config_path": str(AUTH_CONFIG),
        "auth_config_exists": AUTH_CONFIG.exists(),
        "has_base_url": has_base_url,
        "has_saved_username": has_username,
        "has_saved_password": has_password,
        "can_auto_refresh": has_base_url and has_username and has_password,
    }


class RuntimeConfig:
    def __init__(
        self,
        *,
        base_url: str,
        auth_state_path: Path,
        username: str,
        password: str,
        login_path: str,
        captcha_path: str,
        timeout: int,
        auto_ocr: bool,
        max_captcha_retry: int,
    ) -> None:
        self.base_url = base_url
        self.auth_state_path = auth_state_path
        self.username = username
        self.password = password
        self.login_path = login_path
        self.captcha_path = captcha_path
        self.timeout = timeout
        self.auto_ocr = auto_ocr
        self.max_captcha_retry = max_captcha_retry


def _runtime_config(args: argparse.Namespace) -> RuntimeConfig:
    config = _persist_inputs(args, _read_config())
    secrets = _get_secret_manager()

    base_url = _normalise_base_url(
        args.base_url
        or _resolve_ref(config.get("base_url"))
        or os.getenv("SKYEYE_BASE_URL")
        or ""
    )
    auth_state_path = Path(
        args.auth_state
        or _resolve_ref(config.get("auth_state_path"))
        or os.getenv("SKYEYE_AUTH_STATE")
        or DEFAULT_AUTH_STATE
    ).expanduser()
    username = (
        args.username
        or _resolve_ref(config.get("username"))
        or secrets.get(USERNAME_SECRET_ID)
        or os.getenv("SKYEYE_USERNAME")
        or ""
    ).strip()
    password = (
        args.password
        or _resolve_ref(config.get("password"))
        or secrets.get(PASSWORD_SECRET_ID)
        or os.getenv("SKYEYE_PASSWORD")
        or ""
    ).strip()

    return RuntimeConfig(
        base_url=base_url,
        auth_state_path=auth_state_path,
        username=username,
        password=password,
        login_path=args.login_path or str(config.get("login_path") or DEFAULT_LOGIN_PATH),
        captcha_path=args.captcha_path or str(config.get("captcha_path") or DEFAULT_CAPTCHA_PATH),
        timeout=max(5, int(args.timeout or DEFAULT_TIMEOUT)),
        auto_ocr=not args.no_ocr,
        max_captcha_retry=max(1, int(args.max_captcha_retry or 5)),
    )


def _url(cfg: RuntimeConfig, path: str) -> str:
    return urljoin(cfg.base_url + "/", path.lstrip("/"))


def _login_url(cfg: RuntimeConfig) -> str:
    return _url(cfg, cfg.login_path)


def _storage_entry_keep(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    name = str(entry.get("name") or "")
    value = str(entry.get("value") or "")
    if name in ESSENTIAL_LOCAL_STORAGE_KEYS:
        return True
    lowered = name.lower()
    if "token" in lowered or "csrf" in lowered:
        return len(value.encode("utf-8", errors="ignore")) <= MAX_LOCAL_STORAGE_VALUE_BYTES
    return False


def _filter_auth_state_file(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"filtered": False}
    if not isinstance(state, dict):
        return {"filtered": False}
    origins = state.get("origins")
    if not isinstance(origins, list):
        return {"filtered": False}

    before = 0
    after = 0
    for origin in origins:
        if not isinstance(origin, dict):
            continue
        entries = origin.get("localStorage")
        if not isinstance(entries, list):
            continue
        before += len(entries)
        kept = [entry for entry in entries if _storage_entry_keep(entry)]
        after += len(kept)
        origin["localStorage"] = kept

    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"filtered": True, "localStorageItemsBefore": before, "localStorageItemsAfter": after}


def _save_filtered_state(cfg: RuntimeConfig) -> dict[str, Any]:
    saved = helpers.save_state(cfg.auth_state_path, url=cfg.base_url)
    return {**saved, "filter": _filter_auth_state_file(cfg.auth_state_path)}


def _auth_state_has_auth_indicators(path: Path) -> dict[str, Any]:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"valid": False, "reason": "auth_state_json_invalid", "error": str(exc)}
    cookies = state.get("cookies") if isinstance(state, dict) else []
    origins = state.get("origins") if isinstance(state, dict) else []
    cookie_names = {
        str(cookie.get("name") or "")
        for cookie in cookies or []
        if isinstance(cookie, dict) and cookie.get("name")
    }
    local_storage_names = set()
    for origin in origins or []:
        if not isinstance(origin, dict):
            continue
        for entry in origin.get("localStorage") or []:
            if isinstance(entry, dict) and entry.get("name"):
                local_storage_names.add(str(entry.get("name")))
    has_cookie = bool(cookie_names & {"csrfToken", "sessionid", "JSESSIONID", "PHPSESSID"})
    has_token = bool(local_storage_names & {"csrf_token", "csrfToken"})
    return {
        "valid": has_cookie or has_token,
        "reason": "auth_state_has_cookie_or_csrf" if (has_cookie or has_token) else "auth_state_missing_auth_indicators",
        "cookies": len(cookies or []),
        "origins": len(origins or []),
        "cookieNames": sorted(cookie_names),
        "localStorageAuthKeys": sorted(local_storage_names & {"csrf_token", "csrfToken", "system_type"}),
    }


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


def _has_auth_cookie(cfg: RuntimeConfig) -> bool:
    try:
        cookies = helpers.cdp("Network.getCookies", urls=[cfg.base_url]).get("cookies", [])
    except Exception:
        return False
    if not isinstance(cookies, list):
        return False
    names = {str(cookie.get("name") or "") for cookie in cookies if isinstance(cookie, dict)}
    return bool(names & {"csrfToken", "sessionid", "JSESSIONID", "PHPSESSID"})


def _looks_like_login_page(url: str, text: str) -> bool:
    haystack = f"{url}\n{text}".lower()
    return any(marker in haystack for marker in ("login", "请输入用户名", "请输入密码", "请输入验证码", "验证码"))


def _is_logged_in(cfg: RuntimeConfig) -> bool:
    info = helpers.page_info()
    current_url = str(info.get("url") or "")
    if _looks_like_login_page(current_url, _page_text()):
        return False
    return _has_auth_cookie(cfg) or cfg.base_url.rstrip("/") in current_url


def _is_browser_daemon_error(exc: Exception) -> bool:
    message = str(exc)
    return "bu.port" in message or "daemon" in message.lower()


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


def validate_auth_state(cfg: RuntimeConfig) -> dict[str, Any]:
    if not cfg.auth_state_path.exists():
        return {
            "valid": False,
            "reason": "auth_state_not_found",
            "auth_state_path": str(cfg.auth_state_path),
        }
    try:
        loaded = helpers.load_state(cfg.auth_state_path, url=cfg.base_url)
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
        if _is_browser_daemon_error(exc):
            result = _browser_daemon_result(exc, cfg.auth_state_path)
            result["reason"] = "auth_state_load_failed_browser_daemon_not_ready"
            return result
        lightweight = _auth_state_has_auth_indicators(cfg.auth_state_path)
        if lightweight.get("valid"):
            return {
                "valid": True,
                "reason": "auth_state_lightweight_valid_after_browser_load_failed",
                "error": str(exc),
                "auth_state_path": str(cfg.auth_state_path),
                "lightweight": lightweight,
            }
        return {
            "valid": False,
            "reason": "auth_state_load_failed",
            "error": str(exc),
            "auth_state_path": str(cfg.auth_state_path),
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


def _handle_pre_login_prompts() -> dict[str, Any]:
    script = """(() => {
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const buttons = Array.from(document.querySelectorAll("button")).filter(visible);
  const clicked = [];
  for (const button of buttons) {
    const text = (button.innerText || button.textContent || "").replace(/\\s+/g, "");
    if (/确认并继续|同意|我同意|继续|关闭/.test(text)) {
      button.click();
      clicked.push(text);
      break;
    }
  }
  const checkboxes = Array.from(document.querySelectorAll(".authorize-check input[type='checkbox'], .protocol input[type='checkbox'], .q-checkbox__original"))
    .filter(visible);
  for (const checkbox of checkboxes) {
    if (!checkbox.checked) {
      checkbox.click();
      clicked.push("checkbox");
    }
  }
  return {clicked};
})()"""
    try:
        result = helpers.js(script)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as exc:
        return {"error": str(exc)}


def _wait_for_login_form_ready(cfg: RuntimeConfig) -> dict[str, bool]:
    script = """(() => {
  const visible = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  };
  const pick = (selectors) => selectors
    .map((selector) => Array.from(document.querySelectorAll(selector)).find(visible))
    .find(Boolean);
  const textInputs = Array.from(document.querySelectorAll("input"))
    .filter((el) => visible(el) && !["hidden", "password", "checkbox", "radio", "submit", "button"].includes((el.type || "").toLowerCase()));
  const nonCaptchaTextInputs = textInputs.filter((el) => !/验证码|captcha|code|vcode/i.test([
    el.placeholder || "",
    el.name || "",
    el.id || "",
    String(el.className || ""),
    el.closest("div")?.className || ""
  ].join(" ")));
  const username = pick([
    ".sys-account input",
    ".account input",
    "input[placeholder*='用户名']",
    "input[placeholder*='账号']",
    "input[name*='user' i]",
    "input[id*='user' i]"
  ]) || nonCaptchaTextInputs[0];
  const password = pick([
    ".sys-password input",
    ".password input[type='password']",
    "input[placeholder*='密码']",
    "input[type='password']"
  ]);
  const captcha = pick([
    ".sys-code input",
    ".authority-code input",
    ".code-input input",
    "input[placeholder*='验证码']",
    "input[name*='captcha' i]",
    "input[id*='captcha' i]",
    "input[name*='code' i]",
    "input[id*='code' i]"
  ]) || textInputs.find((el) => /验证码|captcha|code|vcode/i.test([
    el.placeholder || "",
    el.name || "",
    el.id || "",
    String(el.className || ""),
    el.closest("div")?.className || ""
  ].join(" ")));
  const textButton = Array.from(document.querySelectorAll("button"))
    .filter(visible)
    .some((el) => /登\\s*录|login/i.test((el.innerText || "").trim()));
  const submit = pick([
    "button.login-button",
    ".login-button",
    ".login-form button.q-button--primary",
    "button[type='submit']"
  ]);
  return {
    username: Boolean(username),
    password: Boolean(password),
    captcha: Boolean(captcha),
    submit: Boolean(submit) || textButton
  };
})()"""
    deadline = time.time() + cfg.timeout
    last_state: dict[str, bool] = {}
    while time.time() < deadline:
        state = helpers.js(script)
        last_state = state if isinstance(state, dict) else {}
        if last_state.get("username") and last_state.get("password") and last_state.get("captcha") and last_state.get("submit"):
            return {key: bool(value) for key, value in last_state.items()}
        time.sleep(0.5)
    raise RuntimeError(
        "SkyEye login form was not rendered before timeout: "
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
      const score = (item) => /captcha|verify|vcode|code|验证码/.test(item.hint) ? 0 : 1;
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
    return str(helpers.js(script) or "")


def _captcha_image_from_browser(cfg: RuntimeConfig) -> bytes:
    data_url = _captcha_image_data_url_from_dom()
    if not data_url:
        captcha_url = _url(cfg, f"{cfg.captcha_path}?r={random.random()}")
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
        raise RuntimeError("SkyEye captcha fetch did not return a data URL.")
    return base64.b64decode(data_url.split(",", 1)[1])


def _ocr_code(image_bytes: bytes) -> str:
    try:
        import ddddocr
    except ImportError as exc:
        raise RuntimeError("ddddocr is required for automatic captcha recognition.") from exc
    return str(ddddocr.DdddOcr(show_ad=False).classification(image_bytes)).strip()[:4]


def _fill_and_submit(cfg: RuntimeConfig, code: str) -> dict[str, Any]:
    payload = {
        "username": cfg.username,
        "password": cfg.password,
        "code": code,
    }
    script = f"""(() => {{
  const cfg = {json.dumps(payload, ensure_ascii=False)};
  const visible = (el) => {{
    if (!el) return false;
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0;
  }};
  const pick = (selectors) => selectors
    .map((selector) => Array.from(document.querySelectorAll(selector)).find(visible))
    .find(Boolean);
  const textInputs = Array.from(document.querySelectorAll("input"))
    .filter((el) => visible(el) && !["hidden", "password", "checkbox", "radio", "submit", "button"].includes((el.type || "").toLowerCase()));
  const nonCaptchaTextInputs = textInputs.filter((el) => !/验证码|captcha|code|vcode/i.test([
    el.placeholder || "",
    el.name || "",
    el.id || "",
    String(el.className || ""),
    el.closest("div")?.className || ""
  ].join(" ")));
  const username = pick([
    ".sys-account input",
    ".account input",
    "input[placeholder*='用户名']",
    "input[placeholder*='账号']",
    "input[name*='user' i]",
    "input[id*='user' i]"
  ]) || nonCaptchaTextInputs[0];
  const password = pick([
    ".sys-password input",
    ".password input[type='password']",
    "input[placeholder*='密码']",
    "input[type='password']"
  ]);
  const captcha = pick([
    ".sys-code input",
    ".authority-code input",
    ".code-input input",
    "input[placeholder*='验证码']",
    "input[name*='captcha' i]",
    "input[id*='captcha' i]",
    "input[name*='code' i]",
    "input[id*='code' i]"
  ]) || textInputs.find((el) => /验证码|captcha|code|vcode/i.test([
    el.placeholder || "",
    el.name || "",
    el.id || "",
    String(el.className || ""),
    el.closest("div")?.className || ""
  ].join(" ")));
  const agreement = document.querySelector(".protocol input[type='checkbox']")
    || document.querySelector(".authorize-check input[type='checkbox']")
    || document.querySelector(".q-checkbox__original");
  const submit = pick([
    "button.login-button",
    ".login-button",
    ".login-form button.q-button--primary",
    "button[type='submit']"
  ])
    || Array.from(document.querySelectorAll("button"))
      .filter(visible)
      .find((el) => /登\\s*录|login/i.test((el.innerText || "").trim()));

  function setValue(el, value) {{
    if (!el) return false;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    el.focus();
    setter.call(el, value);
    el.dispatchEvent(new Event("input", {{bubbles: true}}));
    el.dispatchEvent(new Event("change", {{bubbles: true}}));
    el.dispatchEvent(new KeyboardEvent("keyup", {{bubbles: true}}));
    return true;
  }}

  const filled = {{
    username: setValue(username, cfg.username),
    password: setValue(password, cfg.password),
    captcha: setValue(captcha, cfg.code),
    agreement: false
  }};
  if (!filled.username || !filled.password || !filled.captcha) {{
    throw new Error("missing SkyEye login input");
  }}
  if (agreement) {{
    if (!agreement.checked) agreement.click();
    agreement.dispatchEvent(new Event("input", {{bubbles: true}}));
    agreement.dispatchEvent(new Event("change", {{bubbles: true}}));
    filled.agreement = Boolean(agreement.checked);
  }}
  if (!submit) {{
    throw new Error("missing SkyEye login submit button");
  }}
  submit.click();
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


def refresh_auth_state(cfg: RuntimeConfig, captcha_code: str = "") -> dict[str, Any]:
    missing = []
    if not cfg.username:
        missing.append("username")
    if not cfg.password:
        missing.append("password")
    if missing:
        return {
            "success": False,
            "status": "manual_login_required",
            "reason": "missing_credentials",
            "missing": missing,
            "auth_state_path": str(cfg.auth_state_path),
        }

    try:
        _open_page(_login_url(cfg))
        _handle_pre_login_prompts()
        form_state = _wait_for_login_form_ready(cfg)
    except Exception as exc:
        if _is_browser_daemon_error(exc):
            return _browser_daemon_result(exc, cfg.auth_state_path)
        raise
    last_error = ""
    for attempt in range(1, cfg.max_captcha_retry + 1):
        try:
            code = captcha_code.strip()
            if not code:
                if not cfg.auto_ocr:
                    return {
                        "success": False,
                        "status": "manual_login_required",
                        "reason": "captcha_code_required",
                        "auth_state_path": str(cfg.auth_state_path),
                    }
                code = _ocr_code(_captcha_image_from_browser(cfg))
            filled = _fill_and_submit(cfg, code)
            if _wait_for_login_success(cfg):
                saved = _save_filtered_state(cfg)
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
            _handle_pre_login_prompts()
            form_state = _wait_for_login_form_ready(cfg)
        except Exception:
            pass

    return {
        "success": False,
        "status": "manual_login_required",
        "reason": "browser_cdp_login_failed",
        "last_error": last_error,
        "auth_state_path": str(cfg.auth_state_path),
    }


def ensure_auth_state(cfg: RuntimeConfig, captcha_code: str = "", force_refresh: bool = False) -> dict[str, Any]:
    if not force_refresh:
        validation = validate_auth_state(cfg)
        if validation.get("valid"):
            return {"success": True, "status": "auth_state_loaded", **validation}
    return refresh_auth_state(cfg, captcha_code=captcha_code)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ensure SkyEye browser auth-state")
    parser.add_argument("action", nargs="?", choices=["ensure", "validate", "refresh", "status"], default="ensure")
    parser.add_argument("--base-url", help="SkyEye base URL, for example https://skyeye.example.com")
    parser.add_argument("--username", help="Username for CDP-assisted login")
    parser.add_argument("--password", help="Password for CDP-assisted login")
    parser.add_argument("--auth-state", help=f"Auth-state path, default: {DEFAULT_AUTH_STATE}")
    parser.add_argument("--login-path", default="", help=f"Login path, default: {DEFAULT_LOGIN_PATH}")
    parser.add_argument("--captcha-path", default="", help="Captcha path override")
    parser.add_argument("--captcha-code", default="", help="Manual captcha code; OCR is used when omitted")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-captcha-retry", type=int, default=5)
    parser.add_argument("--no-ocr", action="store_true", help="Do not OCR captcha; require --captcha-code")
    parser.set_defaults(save_credentials=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.action == "status":
            result = {"success": True, "status": "saved_auto_login_status", **saved_auto_login_status()}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        cfg = _runtime_config(args)
        if args.action == "validate":
            result = validate_auth_state(cfg)
        elif args.action == "refresh":
            result = refresh_auth_state(cfg, captcha_code=args.captcha_code)
        else:
            result = ensure_auth_state(cfg, captcha_code=args.captcha_code)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("success") or result.get("valid") else 1
    except Exception as exc:
        print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    sys.exit(main())
