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
    monkeypatch.setattr(handler, "_save_browser_auth_pair", lambda cfg: {"cookies": 1})
    monkeypatch.setattr(handler, "_probe_auth_pair", lambda cfg: {"valid": True})

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


def test_normalise_base_url_extracts_origin_from_page_url():
    handler = _load_handler()

    assert (
        handler._normalise_base_url("https://edr.example.com:8443/ui/#/index")
        == "https://edr.example.com:8443"
    )
    assert handler._normalise_base_url("edr.example.com/ui/login.php") == "https://edr.example.com"


def test_auth_pair_rejects_cookie_token_mismatch(tmp_path, monkeypatch):
    handler = _load_handler()
    state_path = tmp_path / "auth-state.json"
    state_path.write_text(
        '{"cookies":[{"name":"sessionid","value":"new","domain":"edr.example.com","path":"/"}],"origins":[]}',
        encoding="utf-8",
    )
    old_cookies = [{"name": "sessionid", "value": "old", "domain": "edr.example.com", "path": "/"}]
    bundle = {
        "token": "old-token",
        "base_url": "https://edr.example.com",
        "cookie_fingerprint": handler._cookie_fingerprint(old_cookies, "https://edr.example.com"),
    }
    monkeypatch.setattr(
        handler,
        "_get_secret_manager",
        lambda: type("Secrets", (), {"get": lambda self, key: __import__("json").dumps(bundle)})(),
    )

    try:
        handler._load_verified_auth_pair(_cfg(handler, state_path))
    except RuntimeError as exc:
        assert "not from the same login" in str(exc)
    else:
        raise AssertionError("mismatched EDR authentication must be rejected")


def test_http_login_saves_matched_cookie_and_token(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")

    class Response:
        def __init__(self, data=None, content=b"captcha"):
            self._data = data or {}
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class Session:
        def __init__(self):
            self.cookies = handler.requests.cookies.RequestsCookieJar()
            self.cookies.set("sessionid", "cookie-value", domain="edr.example.com", path="/")
            self.posts = []

        def get(self, url, **kwargs):
            return Response()

        def post(self, url, **kwargs):
            self.posts.append((url, kwargs.get("json")))
            payload = kwargs.get("json") or {}
            if payload.get("opr") == "rsakey":
                return Response({"success": True, "key": "c7" * 64})
            if url.endswith("/login"):
                return Response({"success": True, "key": 7})
            return Response({"success": True, "data": {"token": "token-value"}})

    saved = {}
    monkeypatch.setattr(handler, "_http_session", lambda cfg: Session())
    monkeypatch.setattr(handler, "_ocr_verify_code", lambda content: "1234")
    monkeypatch.setattr(
        handler,
        "_save_auth_pair",
        lambda cfg, state, token: saved.update({"state": state, "token": token}) or {"pair_verified": True},
    )

    result = handler._http_login(cfg)

    assert result["status"] == "http_login_refreshed_auth_state"
    assert saved["token"] == "token-value"
    assert saved["state"]["cookies"][0]["value"] == "cookie-value"


def test_dashboard_request_definitions_use_dynamic_base_inputs(tmp_path):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")

    definitions = handler._dashboard_requests(cfg, "token-value", days=7)

    agent_path, agent_payload = definitions["agent_overview"]
    assert agent_path.endswith("opr=get_agent_overview")
    assert agent_payload["app_args"]["name"] == "app.web.event_center.head"
    vulner_path, vulner_payload = definitions["vulnerability_overview"]
    assert "s=token-value" in vulner_path
    assert vulner_payload["uid"] == "admin"
    assert vulner_payload["token"] == "token-value"


def test_auth_probe_requires_http_200_and_expected_user(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_load_verified_auth_pair", lambda cfg: ({"cookies": []}, "token"))

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"success": True, "data": {"user_name": "admin"}}

    class Session:
        def post(self, *args, **kwargs):
            assert kwargs["allow_redirects"] is False
            assert kwargs["json"]["app_args"]["option"] == {}
            return Response()

    monkeypatch.setattr(handler, "_dashboard_session", lambda cfg, state: Session())

    result = handler._probe_auth_pair(cfg)

    assert result == {
        "valid": True,
        "reason": "auth_probe_succeeded",
        "http_status": 200,
        "user_verified": True,
    }


def test_auth_probe_accepts_real_list_auth_info_shape_without_username(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    cfg.username = ""
    monkeypatch.setattr(handler, "_load_verified_auth_pair", lambda cfg: ({"cookies": []}, "token"))

    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"success": True, "auth_info": {"id": "device-license-id", "auth_status": "normal"}}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(handler, "_dashboard_session", lambda cfg, state: Session())

    result = handler._probe_auth_pair(cfg)

    assert result["valid"] is True


def test_auth_probe_rejects_login_redirect(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(handler, "_load_verified_auth_pair", lambda cfg: ({"cookies": []}, "token"))

    class Response:
        status_code = 302
        headers = {"Location": "/ui/login.php"}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(handler, "_dashboard_session", lambda cfg, state: Session())

    result = handler._probe_auth_pair(cfg)

    assert result["valid"] is False
    assert result["reason"] == "auth_probe_redirected"


def test_http_auth_reuses_valid_pair_without_login(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    monkeypatch.setattr(
        handler,
        "_probe_auth_pair",
        lambda cfg: {"valid": True, "reason": "auth_probe_succeeded"},
    )
    monkeypatch.setattr(
        handler,
        "_http_login",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("login must be skipped")),
    )

    result = handler._ensure_http_auth_pair(cfg)

    assert result["status"] == "http_auth_pair_reused"
    assert result["login_skipped"] is True


def test_http_auth_relogs_and_confirms_when_probe_fails(tmp_path, monkeypatch):
    handler = _load_handler()
    cfg = _cfg(handler, tmp_path / "auth-state.json")
    probes = iter(
        [
            {"valid": False, "reason": "auth_probe_unauthorized"},
            {"valid": True, "reason": "auth_probe_succeeded"},
        ]
    )
    monkeypatch.setattr(handler, "_probe_auth_pair", lambda cfg: next(probes))
    monkeypatch.setattr(
        handler,
        "_http_login",
        lambda cfg, captcha_code="": {"success": True, "status": "http_login_refreshed_auth_state"},
    )

    result = handler._ensure_http_auth_pair(cfg)

    assert result["success"] is True
    assert result["previous_probe"]["reason"] == "auth_probe_unauthorized"
    assert result["probe"]["valid"] is True


def test_dashboard_error_redacts_login_token():
    handler = _load_handler()

    error = handler._safe_error(
        RuntimeError("401 Client Error for url: https://edr.test/launch.php?s=secret-token&opr=check"),
        "secret-token",
    )

    assert "secret-token" not in error
    assert "<redacted>" in error
