"""Reusable browser-auth helpers for WebCLI-backed device tools.

The helpers keep browser login state and login credentials separate:

- ``auth_state_path`` stores site-scoped cookies/localStorage captured by
  :func:`flocks.browser.helpers.save_state`.
- ``username`` / ``password`` are expected to come from the device credential
  secret path, not SQL plaintext.

Generated device handlers can call :func:`ensure_browser_auth_state` before
making WebCLI-backed requests. The function never returns credential values.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin

from flocks.browser import helpers


CheckSpec = Mapping[str, Any]


def _target_url(base_url: str, maybe_path: str | None) -> str:
    base = base_url.rstrip("/") + "/"
    if not maybe_path:
        return base
    return urljoin(base, str(maybe_path))


def _selector_exists(selector: str) -> bool:
    return bool(helpers.js(
        f"Boolean(document.querySelector({json.dumps(selector)}))"
    ))


def _eval_bool(expression: str) -> bool:
    return bool(helpers.js(f"Boolean(({expression}))"))


def _matches_check(spec: CheckSpec | None) -> bool:
    """Return whether the current page matches a declarative check spec."""
    if not spec:
        return False

    info = helpers.page_info()
    url = str(info.get("url") or "")

    selector = spec.get("selector")
    if isinstance(selector, str) and selector:
        if not _selector_exists(selector):
            return False

    url_contains = spec.get("url_contains")
    if isinstance(url_contains, str) and url_contains and url_contains not in url:
        return False

    url_not_contains = spec.get("url_not_contains")
    if isinstance(url_not_contains, str) and url_not_contains and url_not_contains in url:
        return False

    js_expr = spec.get("js")
    if isinstance(js_expr, str) and js_expr:
        if not _eval_bool(js_expr):
            return False

    return True


def _is_logged_in(success_check: CheckSpec | None, expired_check: CheckSpec | None) -> bool:
    if expired_check and _matches_check(expired_check):
        return False
    if success_check:
        return _matches_check(success_check)
    info = helpers.page_info()
    return "/login" not in str(info.get("url") or "").lower()


def _set_input(selector: str, value: str) -> None:
    script = f"""(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) throw new Error("missing input: {selector}");
  el.focus();
  el.value = {json.dumps(value)};
  el.dispatchEvent(new Event("input", {{bubbles: true}}));
  el.dispatchEvent(new Event("change", {{bubbles: true}}));
  return true;
}})()"""
    helpers.js(script)


def _click_selector(selector: str) -> None:
    script = f"""(() => {{
  const el = document.querySelector({json.dumps(selector)});
  if (!el) throw new Error("missing clickable element: {selector}");
  el.click();
  return true;
}})()"""
    helpers.js(script)


def _wait_until_logged_in(
    *,
    success_check: CheckSpec | None,
    expired_check: CheckSpec | None,
    timeout_seconds: float,
) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if _is_logged_in(success_check, expired_check):
            return True
        time.sleep(0.5)
    return False


def ensure_browser_auth_state(
    *,
    base_url: str,
    auth_state_path: str | Path,
    username: str | None = None,
    password: str | None = None,
    login_url: str | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
    success_check: CheckSpec | None = None,
    expired_check: CheckSpec | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Ensure a browser session is authenticated for a WebCLI device.

    The function first tries the saved browser state. If that state is expired
    and login credentials plus selectors are available, it performs a form
    login and refreshes ``auth_state_path``.
    """
    if not base_url.strip():
        raise ValueError("base_url is required")

    state_path = Path(auth_state_path).expanduser()
    start_url = _target_url(base_url, None)

    if state_path.exists():
        try:
            helpers.load_state(state_path, url=start_url)
            if _is_logged_in(success_check, expired_check):
                return {
                    "success": True,
                    "status": "auth_state_loaded",
                    "auth_state_path": str(state_path),
                }
        except Exception as exc:
            load_error = str(exc)
        else:
            load_error = None
    else:
        load_error = "auth_state_not_found"

    required_login_bits = {
        "username": username,
        "password": password,
        "username_selector": username_selector,
        "password_selector": password_selector,
        "submit_selector": submit_selector,
    }
    missing = [key for key, value in required_login_bits.items() if not value]
    if missing:
        return {
            "success": False,
            "status": "manual_login_required",
            "reason": "missing_auto_login_inputs",
            "missing": missing,
            "auth_state_path": str(state_path),
            "load_error": load_error,
        }

    helpers.goto_url(_target_url(base_url, login_url))
    helpers.wait_for_load()
    _set_input(str(username_selector), str(username))
    _set_input(str(password_selector), str(password))
    _click_selector(str(submit_selector))
    helpers.wait_for_load(timeout=min(timeout_seconds, 10.0))

    if not _wait_until_logged_in(
        success_check=success_check,
        expired_check=expired_check,
        timeout_seconds=timeout_seconds,
    ):
        return {
            "success": False,
            "status": "manual_login_required",
            "reason": "auto_login_failed_or_requires_human_verification",
            "auth_state_path": str(state_path),
            "load_error": load_error,
        }

    saved = helpers.save_state(state_path, url=start_url)
    return {
        "success": True,
        "status": "auto_login_refreshed_auth_state",
        "auth_state_path": str(state_path),
        "saved": saved,
    }
