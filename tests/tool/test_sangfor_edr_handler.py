import importlib.util
from pathlib import Path


_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / ".flocks"
    / "plugins"
    / "tools"
    / "device"
    / "sangfor_edr_webcli"
    / "sangfor_edr.handler.py"
)


def _load_handler():
    spec = importlib.util.spec_from_file_location("_test_sangfor_edr_handler", _HANDLER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _cfg(handler, state_path: Path):
    return handler.RuntimeConfig(
        base_url="https://edr.example.com",
        auth_state_path=state_path,
        username="admin",
        password="secret",
        login_path="/ui/login.php",
        index_path="/ui/#/index",
        timeout=5,
        auto_ocr_code=True,
        max_captcha_retry=1,
        username_selector="",
        password_selector="",
        captcha_selector="",
        agreement_selector="",
        submit_selector="",
    )


def test_validate_missing_state_does_not_start_daemon(tmp_path, monkeypatch):
    handler = _load_handler()
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: (_ for _ in ()).throw(AssertionError()))

    result = handler._validate_auth_state(_cfg(handler, tmp_path / "missing.json"))

    assert result["reason"] == "auth_state_not_found"


def test_validate_existing_state_ensures_daemon(tmp_path, monkeypatch):
    handler = _load_handler()
    state_path = tmp_path / "auth-state.json"
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: calls.append("ensure"))
    monkeypatch.setattr(handler.helpers, "load_state", lambda *args, **kwargs: {})
    monkeypatch.setattr(handler.helpers, "wait_for_load", lambda *args, **kwargs: True)
    monkeypatch.setattr(handler, "_is_logged_in", lambda cfg: True)

    result = handler._validate_auth_state(_cfg(handler, state_path))

    assert result["valid"] is True
    assert calls == ["ensure"]


def test_validate_reports_daemon_failure_separately(tmp_path, monkeypatch):
    handler = _load_handler()
    state_path = tmp_path / "auth-state.json"
    state_path.write_text('{"cookies": [], "origins": []}', encoding="utf-8")
    monkeypatch.setattr(
        handler,
        "_ensure_browser_daemon",
        lambda: (_ for _ in ()).throw(RuntimeError("browser daemon did not start")),
    )

    result = handler._validate_auth_state(_cfg(handler, state_path))

    assert result["status"] == "browser_daemon_not_ready"
    assert result["reason"] == "auth_state_load_failed_browser_daemon_not_ready"
    assert "flocks browser --setup" in result["next_action"]


def test_refresh_distinguishes_page_open_failure(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: None)
    monkeypatch.setattr(
        handler,
        "_open_page",
        lambda url: (_ for _ in ()).throw(RuntimeError("EDR connection refused")),
    )

    result = handler._refresh_auth_state_with_cdp_login(cfg)

    assert result["status"] == "browser_login_page_open_failed"
    assert result["reason"] == "browser_login_page_open_failed"
    assert result["login_url"] == "https://edr.example.com/ui/login.php"
