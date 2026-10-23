"""
Shared autouse isolation fixture for ALL tests under tests/server/.

This prevents the "attempt to write a readonly database" error by redirecting
all data/config IO to a per-test tmp directory and resetting all global singletons.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
async def _server_isolated_env(tmp_path: Path, monkeypatch):
    """
    Redirect writes to an isolated tmp dir and reset all global singletons
    (Config, Storage, Instance, TaskStore, Agent) before and after each test.
    """
    data_dir = tmp_path / "flocks_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    flocks_root = tmp_path / ".flocks"
    workspace_dir = flocks_root / "workspace"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setenv("FLOCKS_WORKSPACE_DIR", str(workspace_dir))
    monkeypatch.setenv("FLOCKS_PROJECT_ROOTS", str(tmp_path))

    from flocks.config.config import Config
    from flocks.storage.storage import Storage
    from flocks.project.instance import Instance, _state_manager
    from flocks.project.project import Project

    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False

    from flocks.auth.service import AuthService
    AuthService._initialized = False
    AuthService._initialized_db_path = None
    AuthService._has_users_cached = False

    original_cache = dict(Instance._cache)
    Instance._cache.clear()
    Project.invalidate_session_stats()
    for key in list(_state_manager._states.keys()):
        _state_manager._states.pop(key, None)
        _state_manager._disposers.pop(key, None)

    from flocks.agent.registry import Agent as AgentRegistry
    original_custom = dict(AgentRegistry._custom_agents)
    AgentRegistry._custom_agents.clear()

    await Storage.init()

    from flocks.task.store import TaskStore
    if TaskStore._conn is not None:
        try:
            await TaskStore._conn.close()
        except Exception:
            pass
    TaskStore._conn = None
    TaskStore._initialized = False

    yield

    # ---------- teardown ----------
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    AuthService._initialized = False
    AuthService._initialized_db_path = None
    AuthService._has_users_cached = False
    Instance._cache.clear()
    Instance._cache.update(original_cache)
    Project.invalidate_session_stats()
    for key in list(_state_manager._states.keys()):
        _state_manager._states.pop(key, None)
        _state_manager._disposers.pop(key, None)
    AgentRegistry._custom_agents.clear()
    AgentRegistry._custom_agents.update(original_custom)
    if TaskStore._conn is not None:
        try:
            await TaskStore._conn.close()
        except Exception:
            pass
    TaskStore._conn = None
    TaskStore._initialized = False
