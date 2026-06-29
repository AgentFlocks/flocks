from __future__ import annotations

from pathlib import Path

from flocks.browser import device_auth


def test_ensure_browser_auth_state_uses_existing_valid_state(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "auth-state.json"
    state_path.write_text('{"cookies":[],"origins":[]}', encoding="utf-8")
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        device_auth.helpers,
        "load_state",
        lambda path, url=None: calls.append(("load", (path, url))) or {"finalUrl": url},
    )
    monkeypatch.setattr(device_auth.helpers, "page_info", lambda: {"url": "https://example.test/app"})
    monkeypatch.setattr(
        device_auth.helpers,
        "js",
        lambda expression: expression.startswith("Boolean(document.querySelector") and ".app" in expression,
    )
    monkeypatch.setattr(
        device_auth.helpers,
        "save_state",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("save_state should not be called")),
    )

    result = device_auth.ensure_browser_auth_state(
        base_url="https://example.test",
        auth_state_path=state_path,
        success_check={"selector": ".app"},
    )

    assert result["success"] is True
    assert result["status"] == "auth_state_loaded"
    assert calls == [("load", (state_path, "https://example.test/"))]


def test_ensure_browser_auth_state_auto_login_refreshes_state(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "auth-state.json"
    page = {"url": "https://example.test/login", "logged_in": False}
    calls: list[tuple[str, object]] = []

    monkeypatch.setattr(
        device_auth.helpers,
        "goto_url",
        lambda url: calls.append(("goto", url)) or page.update({"url": url}),
    )
    monkeypatch.setattr(device_auth.helpers, "wait_for_load", lambda timeout=15.0: True)
    monkeypatch.setattr(device_auth.helpers, "page_info", lambda: {"url": page["url"]})

    def fake_js(expression: str):
        if "Boolean(document.querySelector" in expression:
            return page["logged_in"] and ".app" in expression
        if "missing input" in expression:
            calls.append(("input", expression))
            return True
        if "missing clickable element" in expression:
            calls.append(("submit", expression))
            page.update({"logged_in": True, "url": "https://example.test/app"})
            return True
        raise AssertionError(expression)

    monkeypatch.setattr(device_auth.helpers, "js", fake_js)
    monkeypatch.setattr(
        device_auth.helpers,
        "save_state",
        lambda path, url=None: calls.append(("save", (path, url))) or {"path": str(path), "cookies": 1},
    )

    result = device_auth.ensure_browser_auth_state(
        base_url="https://example.test",
        auth_state_path=state_path,
        username="admin",
        password="secret-password",
        login_url="/login",
        username_selector="#username",
        password_selector="#password",
        submit_selector="button[type=submit]",
        success_check={"selector": ".app", "url_not_contains": "/login"},
        timeout_seconds=0.6,
    )

    assert result["success"] is True
    assert result["status"] == "auto_login_refreshed_auth_state"
    assert ("goto", "https://example.test/login") in calls
    assert ("save", (state_path, "https://example.test/")) in calls


def test_ensure_browser_auth_state_requires_manual_login_without_credentials(tmp_path: Path) -> None:
    result = device_auth.ensure_browser_auth_state(
        base_url="https://example.test",
        auth_state_path=tmp_path / "missing-auth-state.json",
        username="admin",
        password=None,
        login_url="/login",
        username_selector="#username",
        password_selector="#password",
        submit_selector="button[type=submit]",
    )

    assert result["success"] is False
    assert result["status"] == "manual_login_required"
    assert result["reason"] == "missing_auto_login_inputs"
    assert result["missing"] == ["password"]
