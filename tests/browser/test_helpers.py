import json
import os
import tempfile
from unittest.mock import patch

import pytest
from PIL import Image

from flocks.browser import helpers


def _run(fake_png, width: int, height: int, **kwargs):
    def fake(method, **_):
        return {"data": fake_png(width, height)}

    with patch("flocks.browser.helpers.cdp", side_effect=fake), tempfile.TemporaryDirectory() as temp_dir:
        path = os.path.join(temp_dir, "shot.png")
        helpers.capture_screenshot(path, **kwargs)
        return Image.open(path).size


def test_max_dim_downsizes_oversized_image(fake_png) -> None:
    assert max(_run(fake_png, 4592, 2286, max_dim=1800)) == 1800


def test_max_dim_skips_when_image_already_small(fake_png) -> None:
    assert _run(fake_png, 800, 400, max_dim=1800) == (800, 400)


def test_max_dim_default_is_no_resize(fake_png) -> None:
    assert _run(fake_png, 4592, 2286) == (4592, 2286)


def test_load_env_uses_shared_loader_for_existing_files(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    repo_env = repo_root / ".env"
    workspace_env = workspace / ".env"
    repo_env.write_text("TOKEN=repo\n", encoding="utf-8")
    workspace_env.write_text("TOKEN=workspace\n", encoding="utf-8")
    loaded_paths = []

    class _FakeModulePath:
        def resolve(self):
            return self

        @property
        def parents(self):
            return [None, None, repo_root]

    monkeypatch.setattr(helpers, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(helpers, "Path", lambda _value: _FakeModulePath())
    monkeypatch.setattr(helpers, "load_env_file", lambda path: loaded_paths.append(path))

    helpers._load_env()

    assert loaded_paths == [repo_env, workspace_env]


def test_page_info_raises_clear_error_on_js_exception() -> None:
    def fake_send(req):
        return {}

    def fake_cdp(method, **kwargs):
        return {
            "result": {
                "type": "object",
                "subtype": "error",
                "description": "ReferenceError: location is not defined",
            },
            "exceptionDetails": {
                "text": "Uncaught",
                "lineNumber": 0,
                "columnNumber": 16,
            },
        }

    with (
        patch("flocks.browser.helpers._send", side_effect=fake_send),
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
    ):
        with pytest.raises(RuntimeError, match="ReferenceError"):
            helpers.page_info()


def test_managed_tab_meta_degrades_when_daemon_is_old() -> None:
    with patch("flocks.browser.helpers._send", side_effect=RuntimeError("'method'")):
        assert helpers._managed_tabs() == []
        helpers._register_managed_tab("target-1", "https://example.com")
        helpers._touch_managed_tab("target-1")
        helpers._remove_managed_tab("target-1")


def test_attach_tab_does_not_activate_target() -> None:
    calls = []
    sent = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=lambda req: sent.append(req) or {"session_id": "session-1"}),
    ):
        assert helpers.attach_tab("target-1") == "session-1"

    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls
    assert ("Target.attachToTarget", {"targetId": "target-1", "flatten": True}) in calls
    assert sent[0] == {"meta": "set_session", "session_id": "session-1", "target_id": "target-1"}
    assert sent[1] == {"meta": "touch_managed_tab", "target_id": "target-1"}


def test_switch_tab_activates_then_attaches_target() -> None:
    calls = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", return_value={"session_id": "session-1"}),
    ):
        assert helpers.switch_tab({"targetId": "target-1"}) == "session-1"

    assert calls[0] == ("Target.activateTarget", {"targetId": "target-1"})
    assert ("Target.attachToTarget", {"targetId": "target-1", "flatten": True}) in calls


def test_new_tab_can_attach_in_background() -> None:
    calls = []
    sent = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=lambda req: sent.append(req) or {"session_id": "session-1"}),
    ):
        assert helpers.new_tab("https://example.com", activate=False) == "target-1"

    assert calls[0] == ("Target.createTarget", {"url": "about:blank", "background": True})
    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls
    assert ("Page.navigate", {"url": "https://example.com"}) in calls
    assert {"meta": "register_managed_tab", "target_id": "target-1", "url": "https://example.com"} in sent


def test_close_tab_rejects_unmanaged_tabs_by_default() -> None:
    with (
        patch("flocks.browser.helpers._send", return_value={"tabs": []}),
        patch("flocks.browser.helpers.cdp", return_value={"targetInfos": []}),
    ):
        with pytest.raises(RuntimeError, match="refusing to close unmanaged tab"):
            helpers.close_tab("target-2")


def test_close_tab_can_skip_activating_next_tab() -> None:
    calls = []
    state = {
        "managed": [{"targetId": "target-2", "url": "https://example.com", "current_url": "https://example.com"}],
        "closed": False,
    }

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.closeTarget":
            state["closed"] = True
            return {"success": True}
        if method == "Target.getTargets":
            return {
                "targetInfos": (
                    [{"type": "page", "targetId": "target-1", "url": "https://next.example.com", "title": "Next"}]
                    if state["closed"]
                    else [
                        {"type": "page", "targetId": "target-2", "url": "https://example.com", "title": "Example"},
                        {"type": "page", "targetId": "target-1", "url": "https://next.example.com", "title": "Next"},
                    ]
                )
            }
        return {}

    def fake_send(req):
        meta = req["meta"]
        if meta == "managed_tabs":
            return {"tabs": list(state["managed"])}
        if meta == "remove_managed_tab":
            state["managed"] = [tab for tab in state["managed"] if tab["targetId"] != req["target_id"]]
            return {"removed": True}
        raise AssertionError(req)

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=fake_send),
    ):
        assert helpers.close_tab("target-2") == {"success": True}

    assert ("Target.closeTarget", {"targetId": "target-2"}) in calls
    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls


def test_close_tab_can_activate_next_when_requested() -> None:
    calls = []
    state = {
        "managed": [{"targetId": "target-2", "url": "https://example.com", "current_url": "https://example.com"}],
        "closed": False,
    }

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.closeTarget":
            state["closed"] = True
            return {"success": True}
        if method == "Target.getTargets":
            return {
                "targetInfos": (
                    [{"type": "page", "targetId": "target-1", "url": "https://next.example.com", "title": "Next"}]
                    if state["closed"]
                    else [
                        {"type": "page", "targetId": "target-2", "url": "https://example.com", "title": "Example"},
                        {"type": "page", "targetId": "target-1", "url": "https://next.example.com", "title": "Next"},
                    ]
                )
            }
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    def fake_send(req):
        meta = req["meta"]
        if meta == "managed_tabs":
            return {"tabs": list(state["managed"])}
        if meta == "remove_managed_tab":
            state["managed"] = []
            return {"removed": True}
        if meta == "set_session":
            return {"session_id": req["session_id"]}
        if meta == "touch_managed_tab":
            return {"tab": None}
        raise AssertionError(req)

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=fake_send),
    ):
        assert helpers.close_tab("target-2", activate_next=True) == {"success": True}

    assert ("Target.activateTarget", {"targetId": "target-1"}) in calls


def test_open_or_attach_tab_reuses_matching_managed_tab() -> None:
    calls = []
    state = {
        "managed": [
            {
                "targetId": "target-1",
                "url": "https://example.com",
                "current_url": "https://example.com/dashboard",
                "created_at": 1.0,
                "last_accessed": 1.0,
            }
        ]
    }

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {
                        "type": "page",
                        "targetId": "target-1",
                        "url": "https://example.com/dashboard",
                        "title": "Example",
                    }
                ]
            }
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    def fake_send(req):
        meta = req["meta"]
        if meta == "managed_tabs":
            return {"tabs": list(state["managed"])}
        if meta == "set_session":
            return {"session_id": req["session_id"]}
        if meta == "touch_managed_tab":
            return {"tab": {"targetId": req["target_id"]}}
        raise AssertionError(req)

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=fake_send),
    ):
        assert helpers.open_or_attach_tab("https://example.com", activate=False) == "target-1"

    assert ("Target.createTarget", {"url": "about:blank"}) not in calls
    assert ("Target.attachToTarget", {"targetId": "target-1", "flatten": True}) in calls


def test_open_or_attach_tab_does_not_reuse_unmanaged_user_tab() -> None:
    calls = []
    sent = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"type": "page", "targetId": "user-tab", "url": "https://example.com", "title": "Example"}
                ]
            }
        if method == "Target.createTarget":
            return {"targetId": "managed-tab"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    def fake_send(req):
        sent.append(req)
        meta = req["meta"]
        if meta == "managed_tabs":
            return {"tabs": []}
        if meta == "set_session":
            return {"session_id": req["session_id"]}
        if meta == "touch_managed_tab":
            return {"tab": None}
        if meta == "register_managed_tab":
            return {"tab": {"targetId": req["target_id"]}}
        raise AssertionError(req)

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=fake_send),
    ):
        assert helpers.open_or_attach_tab("https://example.com", activate=False) == "managed-tab"

    assert ("Target.createTarget", {"url": "about:blank", "background": True}) in calls
    assert {"meta": "register_managed_tab", "target_id": "managed-tab", "url": "https://example.com"} in sent


def test_list_tabs_excludes_edge_internal_pages_when_requested() -> None:
    def fake_cdp(method, **kwargs):
        assert method == "Target.getTargets"
        return {
            "targetInfos": [
                {
                    "type": "page",
                    "targetId": "edge-internal",
                    "url": "edge://inspect/#remote-debugging",
                    "title": "Inspect",
                },
                {"type": "page", "targetId": "real-page", "url": "https://example.com", "title": "Example"},
            ]
        }

    with patch("flocks.browser.helpers.cdp", side_effect=fake_cdp):
        tabs = helpers.list_tabs(include_chrome=False)

    assert tabs == [{"targetId": "real-page", "title": "Example", "url": "https://example.com"}]


def test_ensure_real_tab_attaches_instead_of_switching() -> None:
    calls = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"type": "page", "targetId": "target-1", "url": "https://example.com", "title": "Example"}
                ]
            }
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    def fake_send(req):
        meta = req["meta"]
        if meta == "connection_status":
            return {"target_id": "internal-tab", "page": {"targetId": "internal-tab", "url": "chrome://settings"}}
        if meta == "set_session":
            return {"session_id": req["session_id"]}
        if meta == "touch_managed_tab":
            return {"tab": None}
        raise AssertionError(req)

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", side_effect=fake_send),
    ):
        assert helpers.ensure_real_tab() == {
            "targetId": "target-1",
            "url": "https://example.com",
            "title": "Example",
        }

    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls


def test_save_state_writes_portable_schema(tmp_path) -> None:
    out = tmp_path / "auth-state.json"
    cookies = [
        {"name": "sid", "value": "secret", "domain": ".zhihu.com", "path": "/"},
        {"name": "api", "value": "token", "domain": "api.zhihu.com", "path": "/"},
        {"name": "other", "value": "skip", "domain": ".example.com", "path": "/"},
    ]

    def fake_cdp(method, **kwargs):
        if method == "Storage.getCookies":
            return {"cookies": cookies}
        raise AssertionError((method, kwargs))

    def fake_js(expression):
        if 'window["localStorage"]' in expression:
            return '[{"name":"token","value":"abc"}]'
        raise AssertionError(expression)

    with (
        patch(
            "flocks.browser.helpers.page_info", return_value={"url": "https://www.zhihu.com/app", "title": "Example"}
        ),
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers.js", side_effect=fake_js),
    ):
        result = helpers.save_state(out)

    saved = json.loads(out.read_text(encoding="utf-8"))
    assert result["cookies"] == 2
    assert set(saved) == {"cookies", "origins"}
    assert {item["domain"] for item in saved["cookies"]} == {".zhihu.com", "api.zhihu.com"}
    assert saved["origins"] == [
        {"origin": "https://www.zhihu.com", "localStorage": [{"name": "token", "value": "abc"}]}
    ]


def test_load_state_restores_cookies_and_storage(tmp_path) -> None:
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(
        json.dumps(
            {
                "cookies": [
                    {
                        "name": "sid",
                        "value": "secret",
                        "domain": ".example.com",
                        "path": "/",
                        "expires": 12345,
                        "size": 999,
                    }
                ],
                "origins": [{"origin": "https://example.com", "localStorage": [{"name": "token", "value": "abc"}]}],
            }
        ),
        encoding="utf-8",
    )

    cdp_calls = []
    goto_calls = []
    restored = []

    def fake_cdp(method, **kwargs):
        cdp_calls.append((method, kwargs))
        return {}

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers.goto_url", side_effect=lambda url: goto_calls.append(url)),
        patch("flocks.browser.helpers.wait_for_load"),
        patch("flocks.browser.helpers.current_tab", return_value={"url": ""}),
        patch(
            "flocks.browser.helpers._set_storage_entries",
            side_effect=lambda storage_name, entries: restored.append((storage_name, entries)) or len(entries),
        ),
    ):
        result = helpers.load_state(state_file, url="https://example.com/dashboard")

    assert cdp_calls == [
        (
            "Network.setCookies",
            {
                "cookies": [
                    {
                        "name": "sid",
                        "value": "secret",
                        "domain": ".example.com",
                        "path": "/",
                        "expires": 12345.0,
                    }
                ]
            },
        )
    ]
    assert goto_calls == [
        "https://example.com/",
        "https://example.com/dashboard",
    ]
    assert restored == [("localStorage", [{"name": "token", "value": "abc"}])]
    assert result["cookiesApplied"] == 1
    assert result["localStorageItemsApplied"] == 1


def test_summarize_state_reports_storage_state_shape(tmp_path) -> None:
    state_file = tmp_path / "auth-state.json"
    state_file.write_text(
        json.dumps(
            {
                "cookies": [{"name": "sid", "value": "secret", "domain": ".example.com", "path": "/"}],
                "origins": [{"origin": "https://example.com", "localStorage": [{"name": "token", "value": "abc"}]}],
            }
        ),
        encoding="utf-8",
    )

    summary = helpers.summarize_state(state_file)

    assert summary["cookies"] == 1
    assert summary["cookieDomains"] == ["example.com"]
    assert summary["origins"] == [{"origin": "https://example.com", "localStorageItems": 1}]
