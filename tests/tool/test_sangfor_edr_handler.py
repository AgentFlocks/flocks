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


def test_refresh_falls_back_to_manual_login_when_form_is_missing(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: None)
    monkeypatch.setattr(handler, "_open_page", lambda url: None)
    monkeypatch.setattr(handler, "_wait_for_login_form_ready", lambda cfg: (_ for _ in ()).throw(RuntimeError("missing form")))
    monkeypatch.setattr(handler, "_page_text", lambda: "请输入动态口令")

    result = handler._refresh_auth_state_with_cdp_login(cfg)

    assert result["status"] == "manual_login_required"
    assert result["reason"] == "mfa_required"
    assert result["browser_left_open"] is True


def test_complete_manual_login_saves_state(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: None)
    monkeypatch.setattr(handler.helpers, "page_info", lambda: {"url": "https://edr.example.com/ui/#/index"})
    monkeypatch.setattr(handler, "_is_logged_in", lambda cfg: True)
    monkeypatch.setattr(handler, "_save_auth_state", lambda cfg: {"cookies": 1})
    monkeypatch.setattr(handler, "_save_captured_login_token", lambda: True)

    result = handler._complete_manual_login(cfg)

    assert result["status"] == "manual_login_captured_auth_state"
    assert result["saved"] == {"cookies": 1}
    assert result["token_saved"] is True


def test_complete_manual_login_keeps_waiting_when_not_logged_in(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: None)
    monkeypatch.setattr(handler.helpers, "page_info", lambda: {"url": "https://edr.example.com/ui/login.php"})
    monkeypatch.setattr(handler, "_is_logged_in", lambda cfg: False)

    result = handler._complete_manual_login(cfg)

    assert result["status"] == "manual_login_required"
    assert result["reason"] == "manual_login_not_completed"


def test_missing_credentials_opens_browser_for_manual_login(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    cfg.username = ""
    opened = []
    monkeypatch.setattr(handler, "_ensure_browser_daemon", lambda: None)
    monkeypatch.setattr(handler, "_open_page", lambda url: opened.append(url))

    result = handler._refresh_auth_state_with_cdp_login(cfg)

    assert opened == ["https://edr.example.com/ui/login.php"]
    assert result["reason"] == "missing_cdp_login_credentials"
    assert result["missing"] == ["username"]


def test_captured_login_token_is_saved_as_secret(monkeypatch):
    handler = _load_handler()
    saved = {}
    monkeypatch.setattr(handler.helpers, "js", lambda script: "token-value")
    monkeypatch.setattr(
        handler,
        "_get_secret_manager",
        lambda: type("Secrets", (), {"set": lambda self, key, value: saved.update({key: value})})(),
    )

    assert handler._save_captured_login_token(timeout=0.1) is True
    assert saved == {"sangfor_edr_token": "token-value"}


def test_login_token_capture_hooks_fetch_and_xhr(monkeypatch):
    handler = _load_handler()
    scripts = []
    monkeypatch.setattr(handler.helpers, "js", lambda script: scripts.append(script) or True)

    assert handler._install_login_token_capture() is True
    assert "/launch_login.php" in scripts[0]
    assert "window.fetch" in scripts[0]
    assert "XMLHttpRequest.prototype" in scripts[0]
