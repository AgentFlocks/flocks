"""Session test fixtures."""

from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.storage.storage import Storage


@pytest.fixture(autouse=True)
async def isolate_session_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Force session tests to use a per-test temporary Flocks home."""
    flocks_root = tmp_path / "flocks-home"
    data_dir = flocks_root / "data"
    log_dir = flocks_root / "logs"
    record_dir = data_dir / "records"

    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(log_dir))
    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(record_dir))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None

    await Storage.init()

    yield

    await Storage.shutdown()
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
