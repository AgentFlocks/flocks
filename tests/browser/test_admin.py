from flocks.browser import admin


def test_local_chrome_mode_is_false_when_env_provides_remote_cdp() -> None:
    assert not admin._is_local_chrome_mode({"BU_CDP_WS": "ws://example.test/devtools/browser/1"})


def test_local_chrome_mode_is_false_when_process_env_provides_remote_cdp(monkeypatch) -> None:
    monkeypatch.setenv("BU_CDP_WS", "ws://example.test/devtools/browser/1")
    assert not admin._is_local_chrome_mode()


def test_local_chrome_mode_is_false_when_env_provides_remote_cdp_url() -> None:
    assert not admin._is_local_chrome_mode({"BU_CDP_URL": "http://127.0.0.1:9222"})


def test_local_chrome_mode_is_false_when_process_env_provides_remote_cdp_url(monkeypatch) -> None:
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    assert not admin._is_local_chrome_mode()


def test_handshake_timeout_does_not_open_chrome_inspect() -> None:
    msg = "CDP WS handshake failed: timed out during opening handshake"
    assert not admin._needs_chrome_remote_debugging_prompt(msg)


def test_handshake_403_needs_chrome_remote_debugging_prompt() -> None:
    msg = "CDP WS handshake failed: server rejected WebSocket connection: HTTP 403"
    assert admin._needs_chrome_remote_debugging_prompt(msg)


def test_load_env_uses_shared_loader_for_existing_files(tmp_path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repo_env = tmp_path / ".env"
    workspace_env = workspace / ".env"
    repo_env.write_text("TOKEN=repo\n", encoding="utf-8")
    workspace_env.write_text("TOKEN=workspace\n", encoding="utf-8")
    loaded_paths = []

    monkeypatch.setattr(admin, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("BH_AGENT_WORKSPACE", str(workspace))
    monkeypatch.setattr(admin, "load_env_file", lambda path: loaded_paths.append(path))

    admin._load_env()

    assert loaded_paths == [repo_env, workspace_env]


def test_stale_websocket_does_not_open_chrome_inspect() -> None:
    msg = "no close frame received or sent"
    assert not admin._needs_chrome_remote_debugging_prompt(msg)


def test_generic_remote_debugging_message_triggers_prompt() -> None:
    msg = "The browser's remote-debugging page is open, but DevTools is not live yet on 127.0.0.1:9222"
    assert admin._needs_chrome_remote_debugging_prompt(msg)


def test_daemon_endpoint_names_discovers_default_and_named_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    browser_dir = tmp_path / "browser"
    browser_dir.mkdir()
    (browser_dir / "bu.sock").touch()
    (browser_dir / "bu-session-xyz.sock").touch()
    (browser_dir / "bu-stale.pid").touch()

    assert admin._daemon_endpoint_names() == ["default", "session-xyz", "stale"]


def test_daemon_endpoint_names_returns_empty_when_runtime_dir_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(admin.ipc, "IS_WINDOWS", False)
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))

    assert admin._daemon_endpoint_names() == []


def test_active_browser_connections_counts_only_healthy_daemons(monkeypatch) -> None:
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default", "stale", "remote"])

    def fake_request(name, payload, timeout=1.0):
        assert payload == {"meta": "connection_status"}
        if name == "stale":
            raise ConnectionRefusedError()
        if name == "remote":
            return {"error": "no close frame received or sent"}
        return {"target_id": "target-1", "session_id": "session-1", "page": None}

    monkeypatch.setattr(admin.ipc, "request", fake_request)
    assert admin.active_browser_connections() == 1


def test_active_browser_connections_skips_daemons_reporting_cdp_disconnected(monkeypatch) -> None:
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default", "stale"])

    def fake_request(name, payload, timeout=1.0):
        assert payload == {"meta": "connection_status"}
        if name == "stale":
            return {"error": "cdp_disconnected"}
        return {"target_id": "target-1", "session_id": "session-1", "page": None}

    monkeypatch.setattr(admin.ipc, "request", fake_request)
    assert admin.active_browser_connections() == 1


def test_browser_connections_returns_attached_page(monkeypatch) -> None:
    monkeypatch.setattr(admin, "_daemon_endpoint_names", lambda: ["default"])
    response = {
        "target_id": "target-1",
        "session_id": "session-1",
        "page": {
            "targetId": "target-1",
            "title": "Cat - Wikipedia",
            "url": "https://en.wikipedia.org/wiki/Cat",
        },
    }
    monkeypatch.setattr(admin.ipc, "request", lambda name, payload, timeout=1.0: response)

    assert admin.browser_connections() == [
        {
            "name": "default",
            "page": {"title": "Cat - Wikipedia", "url": "https://en.wikipedia.org/wiki/Cat"},
        }
    ]


def test_stop_all_daemons_restarts_every_visible_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(admin.lifecycle, "stop_all_daemons", lambda: (["default", "remote_1"], {}))

    assert admin.stop_all_daemons() == ["default", "remote_1"]


def test_daemon_protocol_probe_accepts_current_daemon(monkeypatch) -> None:
    responses = [{"result": {"targetInfos": []}}, {"tabs": []}]

    def fake_request(name, payload, timeout=3.0):
        return responses.pop(0)

    monkeypatch.setattr(admin.ipc, "request", fake_request)

    assert admin._daemon_has_current_protocol()


def test_daemon_protocol_probe_rejects_old_managed_tab_protocol(monkeypatch) -> None:
    responses = [{"result": {"targetInfos": []}}, {"error": "'method'"}]

    def fake_request(name, payload, timeout=3.0):
        return responses.pop(0)

    monkeypatch.setattr(admin.ipc, "request", fake_request)

    assert not admin._daemon_has_current_protocol()


def test_run_doctor_prints_active_browser_connections_and_active_pages(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(
        admin,
        "browser_connections",
        lambda: [
            {"name": "default", "page": {"title": "Example", "url": "https://example.test"}},
            {"name": "cats", "page": {"title": "Cat - Wikipedia", "url": "https://en.wikipedia.org/wiki/Cat"}},
        ],
    )
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "[ok  ] active browser connections — 2" in out
    assert "        default — active page: Example — https://example.test" in out
    assert "        cats — active page: Cat - Wikipedia — https://en.wikipedia.org/wiki/Cat" in out
    assert "next action       ready; use `flocks browser -c 'print(page_info())'`" in out
    assert "profile-use installed" not in out
    assert "BROWSER_USE_API_KEY set" not in out


def test_doctor_page_output_truncates_long_text(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "DOCTOR_TEXT_LIMIT", 20)
    monkeypatch.setattr(
        admin,
        "browser_connections",
        lambda: [
            {
                "name": "default",
                "page": {"title": "A very long page title", "url": "https://example.test/very/long/path"},
            }
        ],
    )
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "A very long page ..." in out
    assert "https://example.t..." in out
    assert "profile-use installed" not in out
    assert "BROWSER_USE_API_KEY set" not in out


def test_run_doctor_suggests_attach_when_daemon_alive_without_active_connections(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "[ok  ] active browser connections — 0" not in out
    assert "[FAIL] active browser connections — 0" in out
    assert "next action       attach; run `flocks browser -c 'print(page_info())'` before setup" in out


def test_run_doctor_suggests_setup_when_target_exists_but_daemon_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")

    assert admin.run_doctor() == 1

    out = capsys.readouterr().out
    assert "next action       setup; run `flocks browser --setup`" in out


def test_run_setup_uses_generic_missing_browser_wording(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)

    assert admin.run_setup() == 1

    out = capsys.readouterr().out
    assert "no Chrome/Chromium/Edge process detected" in out


def test_run_setup_uses_generic_remote_debugging_wording(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "_is_local_chrome_mode", lambda env=None: True)
    open_calls = []
    monkeypatch.setattr(admin, "_open_browser_inspect", lambda: open_calls.append(True))

    calls = {"count": 0}

    def fake_ensure_daemon(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError(
                "The browser's remote-debugging page is open, but DevTools is not live yet on 127.0.0.1:9222"
            )
        return None

    monkeypatch.setattr(admin, "ensure_daemon", fake_ensure_daemon)

    assert admin.run_setup() == 0

    out = capsys.readouterr().out
    assert "browser remote debugging is not enabled on the current profile." in out
    assert "opening your browser's inspect page" in out
    assert "if the browser shows the profile picker" in out
    assert open_calls == [True]


def test_run_setup_restarts_stale_existing_local_daemon(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "_daemon_has_current_protocol", lambda: False)
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    restarted = []
    ensure_calls = []
    monkeypatch.setattr(admin, "restart_daemon", lambda name=None: restarted.append(name))
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: ensure_calls.append(kwargs))

    assert admin.run_setup() == 0

    out = capsys.readouterr().out
    assert "browser connection is stale; restarting" in out
    assert "daemon is up." in out
    assert restarted == [None]
    assert ensure_calls == [{"wait": admin._SETUP_ATTACH_WAIT, "_open_inspect": False}]


def test_run_setup_retries_at_most_once(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_chrome_running", lambda: True)
    monkeypatch.setattr(admin, "_is_local_chrome_mode", lambda env=None: False)
    ensure_calls = []

    def fake_ensure_daemon(**kwargs):
        ensure_calls.append(kwargs)
        raise RuntimeError("daemon didn't come up")

    monkeypatch.setattr(admin, "ensure_daemon", fake_ensure_daemon)

    assert admin.run_setup() == 1

    out = capsys.readouterr()
    assert "retrying once" in out.out
    assert ensure_calls == [
        {"wait": admin._SETUP_ATTACH_WAIT, "_open_inspect": False},
        {"wait": admin._SETUP_RETRY_WAIT, "_open_inspect": False},
    ]


def test_run_setup_allows_explicit_remote_cdp_without_local_browser(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BU_CDP_WS", "ws://example.test/devtools/browser/1")
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)
    ensure_calls = []
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: ensure_calls.append(kwargs))

    assert admin.run_setup() == 0

    out = capsys.readouterr().out
    assert "attaching via BU_CDP_WS" in out
    assert "daemon is up." in out
    assert ensure_calls == [{"wait": admin._SETUP_ATTACH_WAIT, "_open_inspect": False}]


def test_run_setup_restarts_existing_daemon_for_explicit_remote_cdp(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(
        admin, "_chrome_running", lambda: (_ for _ in ()).throw(AssertionError("should not probe browser"))
    )
    restarted = []
    ensure_calls = []
    monkeypatch.setattr(admin, "restart_daemon", lambda name=None: restarted.append(name))
    monkeypatch.setattr(admin, "ensure_daemon", lambda **kwargs: ensure_calls.append(kwargs))

    assert admin.run_setup() == 0

    out = capsys.readouterr().out
    assert "attaching via BU_CDP_URL" in out
    assert "restarting to attach via BU_CDP_URL" in out
    assert "daemon is up." in out
    assert restarted == [None]
    assert ensure_calls == [{"wait": admin._SETUP_ATTACH_WAIT, "_open_inspect": False}]


def test_run_doctor_uses_generic_browser_wording_when_missing(monkeypatch, capsys) -> None:
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)
    monkeypatch.setattr(admin, "daemon_alive", lambda: False)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")

    assert admin.run_doctor() == 1

    out = capsys.readouterr().out
    assert "[FAIL] browser running" in out
    assert "start Chrome, Chromium, or Edge and rerun `flocks browser --setup`" in out
    assert "next action       start Chrome/Chromium/Edge or provide BU_CDP_URL/BU_CDP_WS" in out


def test_run_doctor_accepts_explicit_remote_cdp_without_local_browser(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:9222")
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_probe_cdp_endpoint", lambda _name, _value: (True, "CDP handshake succeeded"))

    assert admin.run_doctor() == 0

    out = capsys.readouterr().out
    assert "[ok  ] browser target — configured via BU_CDP_URL" in out
    assert "[ok  ] daemon alive" in out
    assert "next action       attach; run `flocks browser -c 'print(page_info())'` before setup" in out


def test_run_doctor_rejects_unreachable_explicit_remote_cdp(monkeypatch, capsys) -> None:
    monkeypatch.setenv("BU_CDP_URL", "http://127.0.0.1:19222")
    monkeypatch.setattr(admin, "_version", lambda: "0.1.0")
    monkeypatch.setattr(admin, "_install_mode", lambda: "git")
    monkeypatch.setattr(admin, "_chrome_running", lambda: False)
    monkeypatch.setattr(admin, "daemon_alive", lambda: True)
    monkeypatch.setattr(admin, "browser_connections", lambda: [])
    monkeypatch.setattr(admin, "_latest_release_tag", lambda: "0.1.0")
    monkeypatch.setattr(
        admin,
        "_probe_cdp_endpoint",
        lambda _name, _value: (False, "connection refused"),
    )

    assert admin.run_doctor() == 1

    out = capsys.readouterr().out
    assert "[FAIL] browser target — BU_CDP_URL is unreachable: connection refused" in out
    assert "next action       fix BU_CDP_URL, then run `flocks browser --setup`" in out


def test_probe_cdp_endpoint_rejects_invalid_websocket_url() -> None:
    assert admin._probe_cdp_endpoint("BU_CDP_WS", "not-a-websocket") == (False, "invalid WebSocket URL")


def test_probe_cdp_url_discovers_websocket_and_performs_cdp_handshake(monkeypatch) -> None:
    sent_messages = []

    class FakeHttpResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def read(self) -> bytes:
            return b'{"webSocketDebuggerUrl":"ws://browser.test/devtools/browser/1"}'

    class FakeWebSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def send(self, message: str) -> None:
            sent_messages.append(message)

        def recv(self, timeout: float) -> str:
            assert timeout == 3.0
            return '{"id":1,"result":{"product":"Chrome/1"}}'

    def fake_urlopen(url: str, timeout: float):
        assert url == "http://browser.test/json/version"
        assert timeout == 3.0
        return FakeHttpResponse()

    def fake_websocket_connect(url: str, **kwargs):
        assert url == "ws://browser.test/devtools/browser/1"
        assert kwargs == {"open_timeout": 3.0, "close_timeout": 3.0}
        return FakeWebSocket()

    monkeypatch.setattr(admin.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(admin, "websocket_connect", fake_websocket_connect)

    assert admin._probe_cdp_endpoint("BU_CDP_URL", "http://browser.test") == (
        True,
        "CDP handshake succeeded",
    )
    assert sent_messages == ['{"id": 1, "method": "Browser.getVersion"}']


def test_probe_cdp_websocket_rejects_non_cdp_response(monkeypatch) -> None:
    class FakeWebSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def send(self, _message: str) -> None:
            pass

        def recv(self, timeout: float) -> str:
            return "[]"

    monkeypatch.setattr(admin, "websocket_connect", lambda *_args, **_kwargs: FakeWebSocket())

    assert admin._probe_cdp_endpoint("BU_CDP_WS", "ws://browser.test/devtools/browser/1") == (
        False,
        "Browser.getVersion returned an invalid response",
    )


def test_chrome_running_on_windows_handles_non_utf8_tasklist_output(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "subprocess.check_output", lambda *args, **kwargs: b"\xcf\xd6\xce\xf1\xbc\xfe\r\nmsedge.exe\r\n"
    )

    assert admin._chrome_running()


def test_chrome_running_on_windows_detects_chromium_process(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Windows")
    monkeypatch.setattr("subprocess.check_output", lambda *args, **kwargs: b"chromium.exe\r\n")

    assert admin._chrome_running()


def test_chrome_running_on_non_windows_matches_text_output(monkeypatch) -> None:
    monkeypatch.setattr("platform.system", lambda: "Darwin")
    monkeypatch.setattr("subprocess.check_output", lambda *args, **kwargs: "Google Chrome\n")

    assert admin._chrome_running()
