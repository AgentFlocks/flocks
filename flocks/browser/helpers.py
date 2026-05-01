"""Browser control via CDP.

Core helpers live here. Agent-editable helpers live in ``BH_AGENT_WORKSPACE``.
"""

import base64
import importlib.util
import json
import math
import os
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import DEFAULT_AGENT_WORKSPACE
from . import _ipc as ipc


AGENT_WORKSPACE = Path(os.environ.get("BH_AGENT_WORKSPACE", DEFAULT_AGENT_WORKSPACE)).expanduser()
NAME = os.environ.get("BU_NAME", "default")
INTERNAL = ("chrome://", "chrome-untrusted://", "devtools://", "chrome-extension://", "about:")
_COMMON_SECOND_LEVEL_SUFFIXES = {"ac", "co", "com", "edu", "gov", "mil", "net", "org"}
_COOKIE_IMPORT_FIELDS = {
    "name",
    "value",
    "url",
    "domain",
    "path",
    "secure",
    "httpOnly",
    "sameSite",
    "expires",
    "priority",
    "sameParty",
    "sourceScheme",
    "sourcePort",
    "partitionKey",
}


def _load_env() -> None:
    for path in (Path(__file__).resolve().parents[2] / ".env", AGENT_WORKSPACE / ".env"):
        if not path.exists():
            continue
        _load_env_file(path)


def _load_env_file(path: Path) -> None:
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_env()


def _send(req: dict) -> dict:
    sock = ipc.connect(NAME, timeout=5.0)
    sock.sendall((json.dumps(req) + "\n").encode())
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1 << 20)
        if not chunk:
            break
        data += chunk
    sock.close()
    response = json.loads(data)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response


def cdp(method: str, session_id: str | None = None, **params):
    """Send a raw CDP command."""
    return _send({"method": method, "params": params, "session_id": session_id}).get("result", {})


def drain_events():
    return _send({"meta": "drain_events"})["events"]


def _js_snippet(expression: str, limit: int = 160) -> str:
    snippet = expression.strip().replace("\n", "\\n")
    return snippet[: limit - 3] + "..." if len(snippet) > limit else snippet


def _js_exception_description(result: dict, details: dict | None) -> str:
    desc = result.get("description")
    exc = details.get("exception") if details else None
    if not desc and isinstance(exc, dict):
        desc = exc.get("description")
        if desc is None and "value" in exc:
            desc = str(exc["value"])
        if desc is None:
            desc = exc.get("className")
    if not desc and details:
        desc = details.get("text")
    return desc or "JavaScript evaluation failed"


def _decode_unserializable_js_value(value: str):
    if value == "NaN":
        return math.nan
    if value == "Infinity":
        return math.inf
    if value == "-Infinity":
        return -math.inf
    if value == "-0":
        return -0.0
    if value.endswith("n"):
        return int(value[:-1])
    return value


def _runtime_value(response: dict, expression: str):
    result = response.get("result", {})
    details = response.get("exceptionDetails")
    if details or result.get("subtype") == "error":
        desc = _js_exception_description(result, details)
        if details:
            line = details.get("lineNumber")
            col = details.get("columnNumber")
            loc = f" at line {line}, column {col}" if line is not None and col is not None else ""
        else:
            loc = ""
        raise RuntimeError(f"JavaScript evaluation failed{loc}: {desc}; expression: {_js_snippet(expression)}")
    if "value" in result:
        return result["value"]
    if "unserializableValue" in result:
        return _decode_unserializable_js_value(result["unserializableValue"])
    return None


def _runtime_evaluate(expression: str, session_id: str | None = None, await_promise: bool = False):
    try:
        response = cdp(
            "Runtime.evaluate",
            session_id=session_id,
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
        )
    except TimeoutError as error:
        raise RuntimeError(f"Runtime.evaluate timed out; expression: {_js_snippet(expression)}") from error
    return _runtime_value(response, expression)


def _has_return_statement(expression: str) -> bool:
    i = 0
    state = "code"
    quote = ""
    while i < len(expression):
        ch = expression[i]
        nxt = expression[i + 1] if i + 1 < len(expression) else ""
        if state == "code":
            if ch in ("'", '"', "`"):
                state = "string"
                quote = ch
                i += 1
                continue
            if ch == "/" and nxt == "/":
                state = "line_comment"
                i += 2
                continue
            if ch == "/" and nxt == "*":
                state = "block_comment"
                i += 2
                continue
            if expression.startswith("return", i):
                before = expression[i - 1] if i > 0 else ""
                after = expression[i + 6] if i + 6 < len(expression) else ""
                if not (before == "_" or before.isalnum()) and not (after == "_" or after.isalnum()):
                    return True
            i += 1
            continue
        if state == "line_comment":
            if ch == "\n":
                state = "code"
            i += 1
            continue
        if state == "block_comment":
            if ch == "*" and nxt == "/":
                state = "code"
                i += 2
                continue
            i += 1
            continue
        if state == "string":
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                state = "code"
                quote = ""
            i += 1
    return False


def goto_url(url: str):
    response = cdp("Page.navigate", url=url)
    domain = (urlparse(url).hostname or "").removeprefix("www.").split(".")[0]
    skills_dir = AGENT_WORKSPACE / "domain-skills" / domain
    if skills_dir.is_dir():
        return {**response, "domain_skills": sorted(path.name for path in skills_dir.rglob("*.md"))[:10]}
    return response


def page_info():
    """Return basic page state or a blocking dialog if one is open."""
    dialog = _send({"meta": "pending_dialog"}).get("dialog")
    if dialog:
        return {"dialog": dialog}
    expression = (
        "JSON.stringify({url:location.href,title:document.title,w:innerWidth,h:innerHeight,"
        "sx:scrollX,sy:scrollY,pw:document.documentElement.scrollWidth,"
        "ph:document.documentElement.scrollHeight})"
    )
    return json.loads(_runtime_evaluate(expression))


def _origin_from_url(url: str | None) -> str | None:
    parsed = urlparse(url or "")
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _storage_origin_url(origin: str) -> str:
    return origin if origin.endswith("/") else f"{origin}/"


def _stringify_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _site_root_from_hostname(hostname: str) -> str:
    labels = [label for label in hostname.lower().strip(".").split(".") if label]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _COMMON_SECOND_LEVEL_SUFFIXES:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def _domain_matches_site(domain: str | None, site_root: str) -> bool:
    normalized = str(domain or "").lower().lstrip(".")
    return bool(normalized) and (normalized == site_root or normalized.endswith(f".{site_root}"))


def _current_page_state() -> tuple[dict[str, Any], str]:
    info = page_info()
    if info.get("dialog"):
        raise RuntimeError("cannot inspect page state while a JavaScript dialog is open")
    origin = _origin_from_url(info.get("url"))
    if not origin:
        raise RuntimeError("current tab does not have a restorable web origin")
    return info, origin


def _storage_entries(storage_name: str) -> list[dict[str, str]]:
    raw = js(
        f"""(() => {{
  const storage = window[{json.dumps(storage_name)}];
  return JSON.stringify(
    Object.entries(storage).map(([name, value]) => ({{name, value}}))
  );
}})()"""
    )
    data = json.loads(raw or "[]")
    if not isinstance(data, list):
        raise RuntimeError(f"{storage_name} export did not return a list")
    return [
        {"name": str(item.get("name", "")), "value": str(item.get("value", ""))}
        for item in data
        if isinstance(item, dict) and "name" in item
    ]


def _collect_site_cookies(target_url: str) -> list[dict[str, Any]]:
    hostname = urlparse(target_url).hostname
    if not hostname:
        raise RuntimeError(f"could not determine hostname for {target_url!r}")
    site_root = _site_root_from_hostname(hostname)
    try:
        cookies = cdp("Storage.getCookies").get("cookies", [])
    except Exception:
        cookies = []
    if not isinstance(cookies, list):
        cookies = []
    filtered = [
        cookie
        for cookie in cookies
        if isinstance(cookie, dict) and _domain_matches_site(cookie.get("domain"), site_root)
    ]
    if filtered:
        return filtered
    fallback = cdp("Network.getCookies", urls=[target_url]).get("cookies", [])
    if not isinstance(fallback, list):
        raise RuntimeError("cookie export did not return a list")
    return fallback


def _set_storage_entries(storage_name: str, entries: list[dict[str, Any]]) -> int:
    payload = _stringify_json(entries)
    # Merge keys instead of clearing storage: flocks/browser reuses the user's real profile.
    applied = js(
        f"""(() => {{
  const entries = JSON.parse({json.dumps(payload)});
  const storage = window[{json.dumps(storage_name)}];
  for (const item of entries) {{
    if (!item || typeof item.name === "undefined") {{
      continue;
    }}
    storage.setItem(String(item.name), String(item.value ?? ""));
  }}
  return entries.length;
}})()"""
    )
    return int(applied or 0)


def _sanitize_cookie_for_import(cookie: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in cookie.items() if key in _COOKIE_IMPORT_FIELDS}
    if "expires" in result:
        try:
            expires = float(result["expires"])
        except (TypeError, ValueError):
            result.pop("expires", None)
        else:
            if expires <= 0:
                result.pop("expires", None)
            else:
                result["expires"] = expires
    return result


def _normalize_state_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RuntimeError("state file must contain a JSON object")

    cookies = payload.get("cookies", [])
    origins = payload.get("origins", [])
    if not isinstance(cookies, list):
        raise RuntimeError("state file field `cookies` must be a list")
    if not isinstance(origins, list):
        raise RuntimeError("state file field `origins` must be a list")
    return {"cookies": cookies, "origins": origins}


def _read_state_file(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return _normalize_state_payload(raw)


def save_state(path: str | Path, url: str | None = None) -> dict[str, Any]:
    """Persist site-scoped cookies plus origin-scoped localStorage to a JSON file."""
    info, origin = _current_page_state()
    target_url = url or info["url"]
    target_origin = _origin_from_url(target_url)
    if target_origin and target_origin != origin:
        raise RuntimeError(
            f"current tab origin {origin!r} does not match requested save origin {target_origin!r}; "
            "attach or navigate to the target origin first"
        )

    cookies = _collect_site_cookies(target_url)
    local_storage = _storage_entries("localStorage")
    state = {
        "cookies": cookies,
        "origins": [{"origin": origin, "localStorage": local_storage}],
    }

    output_path = Path(path).expanduser()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "path": str(output_path),
        "cookies": len(cookies),
        "origins": len(state["origins"]),
        "localStorageItems": len(local_storage),
    }


def load_state(path: str | Path, url: str | None = None, reload: bool = True) -> dict[str, Any]:
    """Load cookies plus origin-scoped localStorage from a JSON file."""
    state = _read_state_file(path)
    cookies = state.get("cookies") or []
    origins = state.get("origins") or []

    imported_cookies = [_sanitize_cookie_for_import(cookie) for cookie in cookies if isinstance(cookie, dict)]
    if imported_cookies:
        cdp("Network.setCookies", cookies=imported_cookies)

    applied_local_storage = 0

    for origin_entry in origins:
        if not isinstance(origin_entry, dict):
            continue
        origin = origin_entry.get("origin")
        if not isinstance(origin, str) or not origin:
            continue
        goto_url(_storage_origin_url(origin))
        wait_for_load()
        applied_local_storage += _set_storage_entries(
            "localStorage",
            origin_entry.get("localStorage") if isinstance(origin_entry.get("localStorage"), list) else [],
        )

    final_url = url or current_tab().get("url")
    if final_url:
        if current_tab().get("url") == final_url:
            if reload:
                cdp("Page.reload")
                wait_for_load()
        else:
            goto_url(final_url)
            if reload:
                wait_for_load()

    return {
        "path": str(Path(path).expanduser()),
        "cookiesApplied": len(imported_cookies),
        "originsApplied": len([item for item in origins if isinstance(item, dict) and item.get("origin")]),
        "localStorageItemsApplied": applied_local_storage,
        "finalUrl": final_url,
    }


def summarize_state(path: str | Path) -> dict[str, Any]:
    """Return a redacted summary of a saved state file."""
    state = _read_state_file(path)
    cookies = state.get("cookies") or []
    origins = state.get("origins") or []
    domains = sorted(
        {
            str(cookie.get("domain", "")).lstrip(".")
            for cookie in cookies
            if isinstance(cookie, dict) and cookie.get("domain")
        }
    )
    return {
        "path": str(Path(path).expanduser()),
        "cookies": len(cookies),
        "cookieDomains": domains,
        "origins": [
            {
                "origin": item.get("origin"),
                "localStorageItems": len(item.get("localStorage") or []),
            }
            for item in origins
            if isinstance(item, dict)
        ],
    }


_debug_click_counter = 0


def click_at_xy(x: int | float, y: int | float, button: str = "left", clicks: int = 1) -> None:
    if os.environ.get("BH_DEBUG_CLICKS"):
        global _debug_click_counter
        try:
            from PIL import Image, ImageDraw

            dpr = js("window.devicePixelRatio") or 1
            path = capture_screenshot(str(ipc._TMP / f"debug_click_{_debug_click_counter}.png"))
            image = Image.open(path)
            draw = ImageDraw.Draw(image)
            px, py = int(x * dpr), int(y * dpr)
            radius = int(15 * dpr)
            draw.ellipse([px - radius, py - radius, px + radius, py + radius], outline="red", width=int(3 * dpr))
            draw.line(
                [px - radius - int(5 * dpr), py, px + radius + int(5 * dpr), py],
                fill="red",
                width=int(2 * dpr),
            )
            draw.line(
                [px, py - radius - int(5 * dpr), px, py + radius + int(5 * dpr)],
                fill="red",
                width=int(2 * dpr),
            )
            image.save(path)
            print(f"[debug_click] saved {path} (x={x}, y={y}, dpr={dpr})")
        except Exception as error:
            print(f"[debug_click] overlay failed: {error}")
        _debug_click_counter += 1
    cdp("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button=button, clickCount=clicks)
    cdp("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button=button, clickCount=clicks)


def type_text(text: str) -> None:
    cdp("Input.insertText", text=text)


_KEYS = {
    "Enter": (13, "Enter", "\r"),
    "Tab": (9, "Tab", "\t"),
    "Backspace": (8, "Backspace", ""),
    "Escape": (27, "Escape", ""),
    "Delete": (46, "Delete", ""),
    " ": (32, "Space", " "),
    "ArrowLeft": (37, "ArrowLeft", ""),
    "ArrowUp": (38, "ArrowUp", ""),
    "ArrowRight": (39, "ArrowRight", ""),
    "ArrowDown": (40, "ArrowDown", ""),
    "Home": (36, "Home", ""),
    "End": (35, "End", ""),
    "PageUp": (33, "PageUp", ""),
    "PageDown": (34, "PageDown", ""),
}


def press_key(key: str, modifiers: int = 0) -> None:
    """Dispatch a key press with optional modifier bitfield."""
    vk, code, text = _KEYS.get(key, (ord(key[0]) if len(key) == 1 else 0, key, key if len(key) == 1 else ""))
    base = {"key": key, "code": code, "modifiers": modifiers, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    cdp("Input.dispatchKeyEvent", type="keyDown", **base, **({"text": text} if text else {}))
    if text and len(text) == 1:
        cdp("Input.dispatchKeyEvent", type="char", text=text, **{k: v for k, v in base.items() if k != "text"})
    cdp("Input.dispatchKeyEvent", type="keyUp", **base)


def scroll(x: int | float, y: int | float, dy: int | float = -300, dx: int | float = 0) -> None:
    cdp("Input.dispatchMouseEvent", type="mouseWheel", x=x, y=y, deltaX=dx, deltaY=dy)


def capture_screenshot(path: str | None = None, full: bool = False, max_dim: int | None = None) -> str:
    """Save a PNG of the current viewport."""
    path = path or str(ipc._TMP / "shot.png")
    response = cdp("Page.captureScreenshot", format="png", captureBeyondViewport=full)
    Path(path).write_bytes(base64.b64decode(response["data"]))
    if max_dim:
        from PIL import Image

        image = Image.open(path)
        if max(image.size) > max_dim:
            image.thumbnail((max_dim, max_dim))
            image.save(path)
    return path


def list_tabs(include_chrome: bool = True) -> list[dict]:
    output = []
    for tab in cdp("Target.getTargets")["targetInfos"]:
        if tab["type"] != "page":
            continue
        url = tab.get("url", "")
        if not include_chrome and url.startswith(INTERNAL):
            continue
        output.append({"targetId": tab["targetId"], "title": tab.get("title", ""), "url": url})
    return output


def current_tab() -> dict:
    status = _send({"meta": "connection_status"})
    page = status.get("page") or {}
    target_id = page.get("targetId") or status.get("target_id")
    if not target_id:
        return {"targetId": None, "url": "", "title": ""}
    if page:
        return {"targetId": target_id, "url": page.get("url", ""), "title": page.get("title", "")}
    try:
        target = cdp("Target.getTargetInfo", targetId=target_id).get("targetInfo", {})
        return {"targetId": target_id, "url": target.get("url", ""), "title": target.get("title", "")}
    except Exception:
        return {"targetId": target_id, "url": "", "title": ""}


def _mark_tab() -> None:
    """Prepend a visible marker to the controlled tab title."""
    try:
        cdp("Runtime.evaluate", expression="if(!document.title.startsWith('🟢'))document.title='🟢 '+document.title")
    except Exception:
        pass


def _unmark_tab() -> None:
    """Remove the visible marker from the previously controlled tab."""
    try:
        cdp("Runtime.evaluate", expression="if(document.title.startsWith('🟢 '))document.title=document.title.slice(2)")
    except Exception:
        pass


def attach_tab(target) -> str:
    """Attach to a tab without making it the visible Chrome tab."""
    target_id = target.get("targetId") if isinstance(target, dict) else target
    _unmark_tab()
    session_id = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"]
    _send({"meta": "set_session", "session_id": session_id, "target_id": target_id})
    _mark_tab()
    return session_id


def switch_tab(target) -> str:
    """Attach to a tab and make it the visible Chrome tab."""
    target_id = target.get("targetId") if isinstance(target, dict) else target
    cdp("Target.activateTarget", targetId=target_id)
    return attach_tab(target_id)


def new_tab(url: str = "about:blank", activate: bool = True) -> str:
    create_params = {"url": "about:blank"}
    if not activate:
        create_params["background"] = True
    target_id = cdp("Target.createTarget", **create_params)["targetId"]
    if activate:
        switch_tab(target_id)
    else:
        attach_tab(target_id)
    if url != "about:blank":
        goto_url(url)
    return target_id


def close_tab(target=None, activate_next: bool = True):
    """Close the specified tab or the currently attached tab."""
    if target is None:
        target_id = current_tab().get("targetId")
    else:
        target_id = target.get("targetId") if isinstance(target, dict) else target
    if not target_id:
        raise RuntimeError("no current tab to close")
    result = cdp("Target.closeTarget", targetId=target_id)
    try:
        tabs = [tab for tab in list_tabs(include_chrome=False) if tab["targetId"] != target_id]
        if tabs and activate_next:
            switch_tab(tabs[0])
    except Exception:
        pass
    return result


def ensure_real_tab():
    """Switch to a non-internal user tab if the current session is stale."""
    tabs = list_tabs(include_chrome=False)
    if not tabs:
        return None
    try:
        current = current_tab()
        if current["url"] and not current["url"].startswith(INTERNAL):
            return current
    except Exception:
        pass
    switch_tab(tabs[0]["targetId"])
    return tabs[0]


def iframe_target(url_substr: str) -> str | None:
    """Return the first iframe target whose URL contains ``url_substr``."""
    for tab in cdp("Target.getTargets")["targetInfos"]:
        if tab["type"] == "iframe" and url_substr in tab.get("url", ""):
            return tab["targetId"]
    return None


def wait(seconds: float = 1.0) -> None:
    time.sleep(seconds)


def wait_for_load(timeout: float = 15.0) -> bool:
    """Poll until the document reaches ``complete`` or the timeout elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if js("document.readyState") == "complete":
            return True
        time.sleep(0.3)
    return False


def js(expression: str, target_id: str | None = None):
    """Run JavaScript in the active tab or a given iframe target."""
    session_id = cdp("Target.attachToTarget", targetId=target_id, flatten=True)["sessionId"] if target_id else None
    if _has_return_statement(expression) and not expression.strip().startswith("("):
        expression = f"(function(){{{expression}}})()"
    return _runtime_evaluate(expression, session_id=session_id, await_promise=True)


_KC = {
    "Enter": 13,
    "Tab": 9,
    "Escape": 27,
    "Backspace": 8,
    " ": 32,
    "ArrowLeft": 37,
    "ArrowUp": 38,
    "ArrowRight": 39,
    "ArrowDown": 40,
}


def dispatch_key(selector: str, key: str = "Enter", event: str = "keypress") -> None:
    """Dispatch a DOM keyboard event on a matched element."""
    key_code = _KC.get(key, ord(key) if len(key) == 1 else 0)
    js(
        f"(()=>{{const e=document.querySelector({json.dumps(selector)});if(e){{"
        f"e.focus();e.dispatchEvent(new KeyboardEvent({json.dumps(event)},{{"
        f"key:{json.dumps(key)},code:{json.dumps(key)},keyCode:{key_code},"
        f"which:{key_code},bubbles:true}}));}}}})()"
    )


def upload_file(selector: str, path: str | list[str]) -> None:
    """Set files on a file input via ``DOM.setFileInputFiles``."""
    document = cdp("DOM.getDocument", depth=-1)
    node_id = cdp("DOM.querySelector", nodeId=document["root"]["nodeId"], selector=selector)["nodeId"]
    if not node_id:
        raise RuntimeError(f"no element for {selector}")
    cdp("DOM.setFileInputFiles", files=[path] if isinstance(path, str) else list(path), nodeId=node_id)


def http_get(url: str, headers: dict | None = None, timeout: float = 20.0) -> str:
    """Fetch a URL directly without using the browser."""

    import gzip

    request_headers = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"}
    if headers:
        request_headers.update(headers)
    with urllib.request.urlopen(urllib.request.Request(url, headers=request_headers), timeout=timeout) as response:
        data = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data.decode()


def _load_agent_helpers() -> None:
    helper_path = AGENT_WORKSPACE / "agent_helpers.py"
    if not helper_path.exists():
        return
    spec = importlib.util.spec_from_file_location("flocks_browser_agent_helpers", helper_path)
    if not spec or not spec.loader:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, value in vars(module).items():
        if name.startswith("_"):
            continue
        globals()[name] = value


_load_agent_helpers()
