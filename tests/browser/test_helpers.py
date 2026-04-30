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
    assert sent == [{"meta": "set_session", "session_id": "session-1", "target_id": "target-1"}]


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

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Target.attachToTarget":
            return {"sessionId": "session-1"}
        return {}

    with (
        patch("flocks.browser.helpers.cdp", side_effect=fake_cdp),
        patch("flocks.browser.helpers._send", return_value={"session_id": "session-1"}),
    ):
        assert helpers.new_tab("https://example.com", activate=False) == "target-1"

    assert calls[0] == ("Target.createTarget", {"url": "about:blank", "background": True})
    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls
    assert ("Page.navigate", {"url": "https://example.com"}) in calls


def test_close_tab_can_skip_activating_next_tab() -> None:
    calls = []

    def fake_cdp(method, **kwargs):
        calls.append((method, kwargs))
        if method == "Target.closeTarget":
            return {"success": True}
        if method == "Target.getTargets":
            return {
                "targetInfos": [
                    {"type": "page", "targetId": "target-1", "url": "https://example.com", "title": "Example"}
                ]
            }
        return {}

    with patch("flocks.browser.helpers.cdp", side_effect=fake_cdp):
        assert helpers.close_tab("target-2", activate_next=False) == {"success": True}

    assert ("Target.closeTarget", {"targetId": "target-2"}) in calls
    assert ("Target.activateTarget", {"targetId": "target-1"}) not in calls
