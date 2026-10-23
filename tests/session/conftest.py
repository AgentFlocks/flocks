"""Session test fixtures."""

from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.session.session_loop import SessionLoop
from flocks.storage.storage import Storage


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_context_usage_push: run SessionLoop._schedule_context_usage_push for real",
    )


@pytest.fixture(autouse=True)
def skip_background_context_usage_push(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Keep loop tests from starting the composer-meter push.

    The push runs in a background task that outlives the test body: it loads
    the agent registry and reads storage while the fixtures tear down, and the
    test's event loop cancels it halfway.  Tests of the push itself opt out
    with ``real_context_usage_push``.
    """
    if request.node.get_closest_marker("real_context_usage_push") is not None:
        return
    # Same signature as the real method, so a wrong call still fails loudly.
    monkeypatch.setattr(
        SessionLoop,
        "_schedule_context_usage_push",
        classmethod(lambda cls, ctx, callbacks, *, force=False: None),
    )


@pytest.fixture(autouse=True)
async def isolate_session_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Force session tests to use a per-test temporary Flocks home."""
    flocks_root = tmp_path / "flocks-home"
    data_dir = flocks_root / "data"
    log_dir = flocks_root / "logs"
    record_dir = data_dir / "records"
    db_path = data_dir / "session-tests.db"

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

    await Storage.init(db_path)

    yield

    await Storage.clear()
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
