from pathlib import Path
import pytest
from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.task.store import TaskStore
from flocks.task.manager import TaskManager
from flocks.session.message import Message
from flocks.session.session import Session
from flocks.auth.context import AuthUser, set_current_auth_user, reset_current_auth_user
from flocks.workspace.manager import WorkspaceManager

@pytest.fixture(autouse=True)
async def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: tmp_path))
    monkeypatch.setenv('FLOCKS_ROOT', str(tmp_path / '.flocks'))
    monkeypatch.setenv('FLOCKS_WORKSPACE_DIR', str(tmp_path / '.flocks/workspace'))
    monkeypatch.delenv('FLOCKS_PROJECT_ROOTS', raising=False)
    monkeypatch.setattr(WorkspaceManager, '_instance', None)
    monkeypatch.setenv('FLOCKS_CONFIG_DIR', str(tmp_path / '.flocks/config'))
    monkeypatch.setenv('FLOCKS_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('FLOCKS_HUB_ROOT', str(Path(__file__).resolve().parents[2] / '.flocks/flockshub'))
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    await TaskStore.close()
    TaskManager._instance = None
    token = set_current_auth_user(AuthUser(id='owner', username='owner', role='admin'))
    await Storage.init()
    await TaskStore.init()
    yield
    await TaskManager.stop()
    await TaskStore.close()
    reset_current_auth_user(token)
    Storage._initialized = False
    Storage._db_path = None
