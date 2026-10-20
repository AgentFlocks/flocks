"""Data retention test fixtures: a per-test Flocks home with its own storage, logs and records."""

from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage
from flocks.utils.log import set_log_cleanup_managed, set_log_retention_days_override


@pytest.fixture(autouse=True)
async def isolate_retention_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    flocks_root = tmp_path / "flocks-home"
    data_dir = flocks_root / "data"
    log_dir = flocks_root / "logs"
    record_dir = data_dir / "records"
    db_path = data_dir / "retention-tests.db"

    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(log_dir))
    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(record_dir))
    monkeypatch.delenv("FLOCKS_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    from flocks.session.message import Message
    from flocks.session.session import Session

    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
    Session.invalidate_cache()
    Message.invalidate_cache()
    set_log_retention_days_override(None)
    set_log_cleanup_managed(False)

    await Storage.init(db_path)

    yield

    # Nothing to wipe: every test runs in its own PostgreSQL schema (tests/conftest.py),
    # and Storage.clear refuses to clear without a prefix.
    Session.invalidate_cache()
    Message.invalidate_cache()
    set_log_retention_days_override(None)
    set_log_cleanup_managed(False)
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
