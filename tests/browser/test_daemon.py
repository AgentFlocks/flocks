from pathlib import Path

import pytest

from flocks.browser import daemon
from flocks.browser.daemon import Daemon


@pytest.mark.asyncio
async def test_daemon_managed_tab_registry_round_trip() -> None:
    daemon = Daemon()

    registered = await daemon.handle(
        {"meta": "register_managed_tab", "target_id": "target-1", "url": "https://example.com"}
    )
    assert registered["tab"]["targetId"] == "target-1"
    assert registered["tab"]["url"] == "https://example.com"
    assert registered["tab"]["current_url"] == "https://example.com"

    touched = await daemon.handle(
        {"meta": "touch_managed_tab", "target_id": "target-1", "url": "https://example.com/dashboard"}
    )
    assert touched["tab"]["current_url"] == "https://example.com/dashboard"

    listed = await daemon.handle({"meta": "managed_tabs"})
    assert listed["tabs"] == [
        {
            "targetId": "target-1",
            "url": "https://example.com",
            "current_url": "https://example.com/dashboard",
            "created_at": registered["tab"]["created_at"],
            "last_accessed": touched["tab"]["last_accessed"],
        }
    ]

    removed = await daemon.handle({"meta": "remove_managed_tab", "target_id": "target-1"})
    assert removed == {"removed": True}
    assert (await daemon.handle({"meta": "managed_tabs"})) == {"tabs": []}


@pytest.mark.asyncio
async def test_daemon_retries_stale_session_on_same_target_before_fallback(monkeypatch) -> None:
    daemon = Daemon()
    daemon.session = "session-1"
    daemon.target_id = "target-1"

    class FakeCDP:
        def __init__(self) -> None:
            self.calls = []

        async def send_raw(self, method, params=None, session_id=None):
            self.calls.append((method, params or {}, session_id))
            if method == "Page.navigate" and session_id == "session-1":
                raise RuntimeError("Session with given id not found")
            if method == "Target.attachToTarget":
                assert params == {"targetId": "target-1", "flatten": True}
                return {"sessionId": "session-2"}
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "target-1", "url": "https://example.com", "type": "page"}}
            if method in {"Page.enable", "DOM.enable", "Runtime.enable", "Network.enable"}:
                return {}
            if method == "Page.navigate" and session_id == "session-2":
                return {"frameId": "frame-1"}
            raise AssertionError((method, params, session_id))

    async def fail_if_called():
        raise AssertionError("attach_first_page should not be used when re-attaching the original target succeeds")

    daemon.cdp = FakeCDP()
    monkeypatch.setattr(daemon, "attach_first_page", fail_if_called)

    response = await daemon.handle({"method": "Page.navigate", "params": {"url": "https://example.com/next"}})

    assert response == {"result": {"frameId": "frame-1"}}
    assert daemon.session == "session-2"
    assert daemon.target_id == "target-1"


@pytest.mark.asyncio
async def test_daemon_ping_reports_strict_session_identity() -> None:
    browser_daemon = Daemon(name="test-session")

    response = await browser_daemon.handle({"meta": "ping"})

    assert response["pong"] is True
    assert response["name"] == "test-session"
    assert type(response["pid"]) is int
    assert response["instance_id"]
    assert response["protocol_version"] == 1


@pytest.mark.asyncio
async def test_attach_first_page_does_not_register_automatic_blank_tab() -> None:
    browser_daemon = Daemon()

    class FakeCDP:
        async def send_raw(self, method, params=None, session_id=None):
            if method == "Target.getTargets":
                return {"targetInfos": []}
            if method == "Target.createTarget":
                return {"targetId": "bootstrap-1"}
            if method == "Target.attachToTarget":
                return {"sessionId": "session-1"}
            if method == "Target.getTargetInfo":
                return {"targetInfo": {"targetId": "bootstrap-1", "url": "about:blank", "type": "page"}}
            if method.endswith(".enable"):
                return {}
            raise AssertionError((method, params, session_id))

    browser_daemon.cdp = FakeCDP()
    await browser_daemon.attach_first_page()

    assert browser_daemon.managed_tabs == {}


@pytest.mark.asyncio
async def test_daemon_close_stops_cdp_client() -> None:
    browser_daemon = Daemon()

    class FakeCDP:
        def __init__(self) -> None:
            self.stopped = False

        async def stop(self) -> None:
            self.stopped = True

    fake_cdp = FakeCDP()
    browser_daemon.cdp = fake_cdp

    await browser_daemon.close()

    assert fake_cdp.stopped is True
    assert browser_daemon.cdp is None


@pytest.mark.asyncio
async def test_daemon_close_logs_stop_failure_without_hiding_startup_error(monkeypatch) -> None:
    browser_daemon = Daemon()
    messages = []

    class FakeCDP:
        async def stop(self) -> None:
            raise RuntimeError("stop failed")

    browser_daemon.cdp = FakeCDP()
    monkeypatch.setattr(daemon, "log", messages.append)

    await browser_daemon.close()

    assert messages == ["CDP client stop failed: stop failed"]
    assert browser_daemon.cdp is None


def test_is_real_page_filters_edge_internal_pages() -> None:
    assert not daemon.is_real_page({"type": "page", "url": "edge://inspect/#remote-debugging"})


def test_is_real_page_accepts_normal_https_pages() -> None:
    assert daemon.is_real_page({"type": "page", "url": "https://example.com"})


def test_profile_dirs_only_returns_paths_for_requested_os() -> None:
    home = Path.home() / "profile-test-home"
    local_app_data = home / "AppData/Local"

    mac_profiles = daemon.profile_dirs(system="Darwin", home=home, environ={})
    linux_profiles = daemon.profile_dirs(system="Linux", home=home, environ={})
    windows_profiles = daemon.profile_dirs(
        system="Windows",
        home=home,
        environ={"LOCALAPPDATA": str(local_app_data)},
    )

    assert all("Library" in path.parts and "Application Support" in path.parts for path in mac_profiles)
    assert all(".config" in path.parts or ".var" in path.parts for path in linux_profiles)
    assert all(path.is_relative_to(local_app_data) for path in windows_profiles)


def test_get_ws_url_skips_unreachable_profile_and_uses_next_candidate(tmp_path, monkeypatch) -> None:
    stale_profile = tmp_path / "stale"
    healthy_profile = tmp_path / "healthy"
    stale_profile.mkdir()
    healthy_profile.mkdir()
    (stale_profile / "DevToolsActivePort").write_text(
        "1111\n/devtools/browser/stale\n",
        encoding="utf-8",
    )
    (healthy_profile / "DevToolsActivePort").write_text(
        "2222\n/devtools/browser/healthy\n",
        encoding="utf-8",
    )

    class FakeHttpResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def read(self) -> bytes:
            return b'{"webSocketDebuggerUrl":"ws://127.0.0.1:2222/devtools/browser/healthy"}'

    def fake_urlopen(url: str, timeout: float):
        if ":1111/" in url:
            raise OSError("connection refused")
        assert url == "http://127.0.0.1:2222/json/version"
        return FakeHttpResponse()

    monkeypatch.delenv("BU_CDP_WS", raising=False)
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.setattr(daemon, "profile_dirs", lambda: [stale_profile, healthy_profile])
    monkeypatch.setattr(daemon.urllib.request, "urlopen", fake_urlopen)

    assert daemon.get_ws_url() == "ws://127.0.0.1:2222/devtools/browser/healthy"


def test_get_ws_url_skips_malformed_profile_and_uses_next_candidate(tmp_path, monkeypatch) -> None:
    malformed_profile = tmp_path / "malformed"
    healthy_profile = tmp_path / "healthy"
    malformed_profile.mkdir()
    healthy_profile.mkdir()
    (malformed_profile / "DevToolsActivePort").write_text("not-a-port-record\n", encoding="utf-8")
    (healthy_profile / "DevToolsActivePort").write_text(
        "2222\n/devtools/browser/healthy\n",
        encoding="utf-8",
    )

    class FakeHttpResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def read(self) -> bytes:
            return b'{"webSocketDebuggerUrl":"ws://127.0.0.1:2222/devtools/browser/healthy"}'

    monkeypatch.delenv("BU_CDP_WS", raising=False)
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.setattr(daemon, "profile_dirs", lambda: [malformed_profile, healthy_profile])
    monkeypatch.setattr(daemon.urllib.request, "urlopen", lambda _url, timeout: FakeHttpResponse())

    assert daemon.get_ws_url() == "ws://127.0.0.1:2222/devtools/browser/healthy"


def test_get_ws_url_skips_open_non_cdp_port_and_uses_next_candidate(tmp_path, monkeypatch) -> None:
    stale_profile = tmp_path / "stale"
    healthy_profile = tmp_path / "healthy"
    stale_profile.mkdir()
    healthy_profile.mkdir()
    (stale_profile / "DevToolsActivePort").write_text(
        "1111\n/devtools/browser/stale\n",
        encoding="utf-8",
    )
    (healthy_profile / "DevToolsActivePort").write_text(
        "2222\n/devtools/browser/healthy\n",
        encoding="utf-8",
    )

    class FakeHttpResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            pass

        def read(self) -> bytes:
            return b'{"webSocketDebuggerUrl":"ws://127.0.0.1:2222/devtools/browser/healthy"}'

    def fake_urlopen(url: str, timeout: float):
        if ":1111/" in url:
            raise OSError("not a CDP endpoint")
        assert url == "http://127.0.0.1:2222/json/version"
        return FakeHttpResponse()

    monkeypatch.delenv("BU_CDP_WS", raising=False)
    monkeypatch.delenv("BU_CDP_URL", raising=False)
    monkeypatch.setattr(daemon, "profile_dirs", lambda: [stale_profile, healthy_profile])
    monkeypatch.setattr(daemon.urllib.request, "urlopen", fake_urlopen)

    assert daemon.get_ws_url() == "ws://127.0.0.1:2222/devtools/browser/healthy"


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

    monkeypatch.setattr(daemon, "AGENT_WORKSPACE", workspace)
    monkeypatch.setattr(daemon, "Path", lambda _value: _FakeModulePath())
    monkeypatch.setattr(daemon, "load_env_file", lambda path: loaded_paths.append(path))

    daemon._load_env()

    assert loaded_paths == [repo_env, workspace_env]
