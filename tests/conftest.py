"""
Pytest configuration and global fixtures
"""

import importlib.metadata
import os
from pathlib import Path

import pytest


_installed_entry_points = importlib.metadata.entry_points


def _core_entry_points(**params):
    """``importlib.metadata.entry_points`` without other packages' ``flocks.*`` entries.

    A development venv usually has a package such as Flocks Pro installed next to
    Core. Its ``flocks.plugins`` / ``flocks.plugins.critical`` / ``flocks.cli``
    entry points would load while these tests build an app or import the CLI and
    replace Core behaviour (workspace layout, identity, licensing, commands), so
    the suite would test that package instead of Core, which Core's CI never has.
    Tests of entry-point loading install their own fakes over this one.
    """
    kept = importlib.metadata.EntryPoints(
        entry_point
        for entry_point in _installed_entry_points()
        if not entry_point.group.startswith("flocks.")
        or entry_point.dist is None
        or entry_point.dist.name == "flocks"
    )
    return kept.select(**params) if params else kept


@pytest.fixture(autouse=True)
async def _postgres_test_database(monkeypatch, tmp_path, _isolate_runtime_root_from_shell):
    """Use a retained, unique PG schema when the integration DSN is supplied.

    Tests cannot inherit the application's production connection string.
    Specialized PG fixtures may install their own schema after this fixture.
    """
    from flocks.db import registry

    dsn = os.environ.get("FLOCKS_TEST_PG_DSN", "")
    monkeypatch.delenv("FLOCKS_DB_DSN", raising=False)
    monkeypatch.delenv("FLOCKS_DB_DSN_FILE", raising=False)
    monkeypatch.setenv("FLOCKS_DB_BACKEND", "postgres")
    registry.reset()
    if not dsn:
        yield
        registry.reset()
        return

    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    from uuid import uuid4
    import asyncpg

    schema = "test_" + uuid4().hex
    connection = await asyncpg.connect(dsn, timeout=10)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await connection.close()
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema + ",public"
    monkeypatch.setenv("FLOCKS_DB_DSN", urlunsplit(parts._replace(query=urlencode(query))))
    from flocks.db.active import reset
    from flocks.db.connect import close_pools
    from flocks.db.sync_store import close_sync_reader
    import asyncio

    await reset()
    try:
        yield
    finally:
        await close_pools()
        await asyncio.to_thread(close_sync_reader)
        await reset()
        registry.reset()

_API_KEY_MARKERS = {
    "requires_anthropic_key": ("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY not set"),
    "requires_openai_key": ("OPENAI_API_KEY", "OPENAI_API_KEY not set"),
    "requires_threatbook_key": ("THREATBOOK_API_KEY", "THREATBOOK_API_KEY not set"),
    "requires_google_key": ("GOOGLE_API_KEY", "GOOGLE_API_KEY not set"),
    "requires_glm_key": ("GLM_API_KEY", "GLM_API_KEY not set"),
}


def pytest_configure(config):
    """Configure custom markers"""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test (may require external services)"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running (>30s per test)"
    )
    config.addinivalue_line(
        "markers",
        "live: mark test as requiring live services "
        "(set FLOCKS_LIVE_TEST=1 and start dev server to enable)"
    )
    for marker_name, (_, reason) in _API_KEY_MARKERS.items():
        config.addinivalue_line("markers", f"{marker_name}: skip if {reason}")
    # Before collection: importing flocks.cli.main already loads CLI extensions.
    importlib.metadata.entry_points = _core_entry_points


def pytest_unconfigure(config):
    importlib.metadata.entry_points = _installed_entry_points


def pytest_runtest_setup(item):
    """Auto-skip tests whose required API keys are not set."""
    for marker_name, (env_var, reason) in _API_KEY_MARKERS.items():
        if item.get_closest_marker(marker_name) and not os.getenv(env_var):
            pytest.skip(reason)


@pytest.fixture(autouse=True)
def _home_honors_env(monkeypatch):
    """Make ``Path.home()`` follow the ``HOME`` env var on every platform.

    Many suites isolate their filesystem by ``monkeypatch.setenv("HOME", ...)``
    and then rely on production code that resolves ``Path.home() / ".flocks"``
    (hub installs, plugin roots, the WebUI contract store, ...). That works on
    Linux CI, where ``Path.home()`` honors ``$HOME`` — but on Windows
    ``Path.home()`` reads ``%USERPROFILE%`` and ignores ``HOME`` entirely, so
    those tests silently leak into (and assert against) the *real* user home.

    This autouse fixture patches ``pathlib.Path.home`` to honor ``HOME`` (then
    ``USERPROFILE``) at call time, aligning Windows with Linux CI. It is
    behavior-preserving when ``HOME`` already points at the real home (the
    default), so tests that never touch ``HOME`` see no change.
    """
    def _home() -> Path:
        candidate = os.environ.get("HOME") or os.environ.get("USERPROFILE")
        if candidate:
            return Path(candidate)
        return Path(os.path.expanduser("~"))

    monkeypatch.setattr(Path, "home", staticmethod(_home))

    # ``Path.home()`` covers explicit home lookups, but ``~``/``~user`` tilde
    # expansion goes through ``os.path.expanduser`` (and ``Path.expanduser``),
    # which on Windows keys off ``%USERPROFILE%``/``%HOMEDRIVE%%HOMEPATH%`` and
    # likewise ignores ``HOME``. Route a bare ``~`` / ``~/...`` through the same
    # ``HOME``-aware root so tilde-based tests are hermetic too. Anything else
    # (``~otheruser``) falls back to the real implementation untouched.
    _real_expanduser = os.path.expanduser

    def _expanduser(path):
        text = os.fspath(path)
        # Only rewrite plain ``str`` tilde paths; leave bytes and ``~user``
        # forms to the real implementation so we never change its type
        # contract or raise on a bytes path.
        if isinstance(text, str):
            home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
            if home and (text == "~" or text.startswith("~/") or text.startswith("~\\")):
                return home + text[1:]
        return _real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", _expanduser)


@pytest.fixture(autouse=True)
async def _cleanup_runtime_singletons_after_test():
    """Best-effort runtime cleanup to prevent cross-test thread leaks.

    Several code paths lazily initialize singleton stores backed by
    ``aiosqlite`` worker threads. If a test exits without closing them,
    pytest can hang at interpreter shutdown while waiting for non-daemon
    threads. Keep cleanup centralized here so individual tests do not need
    bespoke teardown blocks.
    """
    yield

    # Close workflow store first because it owns its own long-lived
    # connection in addition to the shared storage layer.
    try:
        from flocks.workflow.store import WorkflowStore

        await WorkflowStore.close()
    except Exception:
        pass

    # Session listing can also open the independent channel-binding database.
    try:
        from flocks.channel.inbound.session_binding import close_binding_db

        await close_binding_db()
    except Exception:
        pass

    # Then run storage shutdown (idempotent when not initialized).
    try:
        from flocks.storage.storage import Storage

        await Storage.shutdown()
    except Exception:
        pass

    # Output limits raised after a cut-off are process-wide; keep one test's
    # raised or rejected limit out of the next test's requests.
    try:
        from flocks.provider import output_limits

        output_limits.reset()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _reset_log_retention_handover():
    """Give the logger its own cleanup back after every test.

    Starting the CLI or the server hands log cleanup to data retention for the
    rest of the process (``preload_log_retention``); a test that does so would
    otherwise leave later tests' loggers deleting nothing, whatever they expect.
    """
    yield
    from flocks.utils.log import set_log_cleanup_managed, set_log_retention_days_override

    set_log_cleanup_managed(False)
    set_log_retention_days_override(None)


@pytest.fixture(autouse=True)
def _restore_log_output():
    """Give the logger its output back after every test.

    Invoking a CLI command initialises logging to the day's file
    (``Log.init(print=False)``); left that way, every later test's log lines
    go to that file and a test reading them from stderr sees nothing.
    """
    from flocks.utils.log import Log

    fields = ("_writer", "_error_writer", "_log_file", "_log_dir_path", "_log_date", "_level")
    before = {name: getattr(Log, name, None) for name in fields}
    yield
    with Log._state_lock:
        for name in ("_writer", "_error_writer"):
            current = getattr(Log, name, None)
            if current is not None and current is not before[name]:
                try:
                    current.close()
                except Exception:
                    pass
        for name, value in before.items():
            setattr(Log, name, value)


@pytest.fixture(autouse=True)
def _default_source_deploy_mode(monkeypatch):
    """Run the updater as a plain source install unless a test opts into another mode.

    ``flocks.updater.deploy.detect_deploy_mode`` treats a process in which a
    license-enforcement extension replaced the OSS default (or a runtime root
    carrying a ``config/deployment.json`` descriptor) as a *managed* release
    package and refuses in-place upgrades. Developer machines and CI runners that
    carry such an extension would otherwise flip every updater test into that
    refusal path, so pin the default to ``source`` here; tests covering
    docker/managed behaviour set ``FLOCKS_DEPLOY_MODE`` themselves (after this
    fixture) and clear the detection cache.
    """
    from flocks.updater.deploy import detect_deploy_mode

    if not os.environ.get("FLOCKS_DEPLOY_MODE"):
        monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "source")
    detect_deploy_mode.cache_clear()
    yield
    detect_deploy_mode.cache_clear()


@pytest.fixture(autouse=True)
def _isolate_runtime_root_from_shell(monkeypatch):
    """Drop a ``FLOCKS_ROOT`` (and its sibling overrides) inherited from the shell.

    Suites isolate the runtime tree by ``monkeypatch.setenv("HOME", tmp)`` and
    expect the plugin root / config dir / data dir to follow ``~/.flocks``.
    Those paths now resolve from ``FLOCKS_ROOT`` first, so a developer shell
    that exports it (e.g. to drive a test instance) would make dozens of
    HOME-isolated tests read the *real* instance's plugins and config.
    Tests that need a runtime root set it themselves; this fixture runs before
    per-directory conftests (``tests/server/conftest.py`` sets its own root).
    """
    for name in ("FLOCKS_ROOT", "FLOCKS_CONFIG_DIR", "FLOCKS_DATA_DIR", "FLOCKS_WORKSPACE_DIR"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture()
def announced_suites():
    """List two licensed-edition scene suites the way an extension does.

    The bundled hub lists open-edition plugins only; an extension that
    licenses suites lists them from a hub root of its own
    (``tests/fixtures/licensed_suites_hub``), with the contents delivered
    sealed (see tests.sealed_fakes).
    """
    from flocks.extensions import register_hub_root, unregister_hub_root

    root = Path(__file__).parent / "fixtures" / "licensed_suites_hub"
    register_hub_root("tests-licensed-suites", root)
    try:
        yield root
    finally:
        unregister_hub_root("tests-licensed-suites")
