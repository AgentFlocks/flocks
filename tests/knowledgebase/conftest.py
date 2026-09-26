"""Offline isolation for the knowledgebase suite only.

The dedicated launcher isolates Python home and Flocks paths before collection
and blocks network during imports and global teardown as well.
"""

import importlib
import os
from pathlib import Path

import httpx
import pytest


@pytest.fixture(autouse=True)
def _home_honors_env(monkeypatch, tmp_path):
    # Override the root fixture for this suite without changing Git's HOME.
    home = Path(os.environ.get("FLOCKS_TEST_HOME", str(tmp_path / "home")))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    original = os.path.expanduser

    def expanduser(value):
        text = os.fspath(value)
        if isinstance(text, str) and (text == "~" or text.startswith(("~/", "~\\"))):
            return str(home) + text[1:]
        return original(value)

    monkeypatch.setattr(os.path, "expanduser", expanduser)


@pytest.fixture(autouse=True)
def _knowledgebase_no_network():
    # Keep this guard local to each knowledgebase test, not unrelated suites.
    # The dedicated launcher guards imports and global cleanup separately.
    with pytest.MonkeyPatch.context() as patch:
        def blocked(*args, **kwargs):
            pytest.fail("Knowledgebase tests require MockTransport/ASGITransport, not network I/O")

        async def blocked_async(*args, **kwargs):
            blocked()

        patch.setattr(httpx.HTTPTransport, "handle_request", blocked)
        patch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked_async)
        yield


@pytest.fixture(autouse=True)
async def _knowledgebase_state(monkeypatch, _knowledgebase_no_network):
    from flocks.config.config import Config
    from flocks.knowledgebase import runtime
    from flocks.security import secrets

    monkeypatch.setattr(Config, "_global_config", None)
    monkeypatch.setattr(Config, "_cached_config", None)
    monkeypatch.setattr(secrets, "_secret_manager", None)
    monkeypatch.setattr(runtime, "_client", None)
    yield
    await runtime.stop()


@pytest.fixture(autouse=True)
def _knowledgebase_tool_registry(monkeypatch, _knowledgebase_no_network):
    from flocks.tool.registry import ToolRegistry

    for name, value in {
        "_tools": {},
        "_enabled_defaults": {},
        "_initialized": False,
        "_initializing_thread_id": None,
        "_dynamic_modules": {},
        "_dynamic_tools_by_module": {},
        "_plugin_tool_names": [],
        "_plugin_load_errors": [],
        "_failure_state": {},
        "_revision": 0,
        "_config_state_token": None,
        "_watcher": None,
    }.items():
        monkeypatch.setattr(ToolRegistry, name, value)

    # Keep the actual builtin initializer, registration decorator and toolset
    # resolution. Both permission tests initialize this registry, not only the
    # explicit registration test. No fake Tool/ToolInfo is inserted here.
    groups = [
        (package, [name for name in modules if (package, name) == ("flocks.tool", "knowledgebase")])
        for package, modules in ToolRegistry._builtin_module_groups
    ]
    monkeypatch.setattr(ToolRegistry, "_builtin_module_groups", [(package, names) for package, names in groups if names])
    for name in (
        "_register_dynamic_tools",
        "_register_plugin_extension_point",
        "_bootstrap_user_api_services",
        "_sync_api_service_states",
        "_apply_tool_settings",
        "_sync_configured_enabled_states",
    ):
        monkeypatch.setattr(ToolRegistry, name, classmethod(lambda cls, *args, **kwargs: None))
    monkeypatch.setattr(ToolRegistry, "_load_plugin_tools", classmethod(lambda cls, *args, **kwargs: []))

    # Collection has already imported this module in test_tool_wiring. Replay
    # its real decorator against the fresh registry, preserving the module's
    # identity so those tests' existing monkeypatch targets still work.
    from flocks.tool import knowledgebase

    importlib.reload(knowledgebase)
