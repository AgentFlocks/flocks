import pytest

from flocks.browser.daemon import Daemon


@pytest.mark.asyncio
async def test_daemon_managed_tab_registry_round_trip() -> None:
    daemon = Daemon()

    registered = await daemon.handle({"meta": "register_managed_tab", "target_id": "target-1", "url": "https://example.com"})
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
from flocks.browser import daemon


def test_is_real_page_filters_edge_internal_pages() -> None:
    assert not daemon.is_real_page({"type": "page", "url": "edge://inspect/#remote-debugging"})


def test_is_real_page_accepts_normal_https_pages() -> None:
    assert daemon.is_real_page({"type": "page", "url": "https://example.com"})


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
