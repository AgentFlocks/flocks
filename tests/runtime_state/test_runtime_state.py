"""
Tests for the shared runtime-state store and the SQLite -> PG migration.

The PG-backed cases test behaviour of the database under two writers -- one
winner on a contended claim, prefix scans that respect LIKE wildcards -- which a
fake cannot exercise. They skip without FLOCKS_TEST_PG_DSN; the pure-logic cases
always run.
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from flocks.runtime_state import ExecutionOwnership
from flocks.runtime_state.migrate import (
    MigrationReport,
    discover_sqlite_files,
    migrate_state,
    verify_migration,
)
from flocks.runtime_state.postgres import PostgresRuntimeStateStore, _is_duplicate_object, _like_prefix

DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not DSN, reason="FLOCKS_TEST_PG_DSN is not set")


# ---------------------------------------------------------------------------
# Pure logic
# ---------------------------------------------------------------------------

def test_lease_liveness():
    now = datetime.now(timezone.utc)
    live = ExecutionOwnership("e1", "node-a", now + timedelta(minutes=5), 1)
    expired = ExecutionOwnership("e1", "node-a", now - timedelta(minutes=5), 1)
    unowned = ExecutionOwnership("e1", None, None, 0)

    assert live.lease_is_live(now)
    assert not expired.lease_is_live(now)
    assert not unowned.lease_is_live(now)


def test_like_prefix_escapes_wildcards():
    """A key containing % or _ must be matched literally, not as a wildcard."""
    assert _like_prefix("session/") == "session/%"
    assert _like_prefix("a%b") == "a\\%b%"
    assert _like_prefix("a_b") == "a\\_b%"


def test_duplicate_object_detection():
    """A concurrent-DDL race is recognised so both instances can start."""
    class _Exc(Exception):
        sqlstate = "42P07"

    assert _is_duplicate_object(_Exc())
    other = Exception()
    other.sqlstate = "42601"  # syntax error, a real failure
    assert not _is_duplicate_object(other)


def test_discover_finds_only_databases(tmp_path):
    (tmp_path / "flocks.db").write_bytes(b"")
    (tmp_path / "flocks.db-wal").write_bytes(b"")  # not a database on its own
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "tasks.db").write_bytes(b"")

    found = {p.name for p in discover_sqlite_files(tmp_path)}
    assert found == {"flocks.db", "tasks.db"}


# ---------------------------------------------------------------------------
# Against a real database
# ---------------------------------------------------------------------------

@pytest.fixture
async def store():
    s = PostgresRuntimeStateStore(DSN)
    await s.initialize()
    yield s
    await s.close()


@requires_pg
async def test_document_round_trip(store):
    key = f"session/{uuid.uuid4().hex}"
    await store.set(key, {"title": "hi", "n": 3})
    assert await store.get(key) == {"title": "hi", "n": 3}


@requires_pg
async def test_batch_write_and_prefix_list(store):
    ns = uuid.uuid4().hex[:8]
    await store.set_many([
        (f"msg/{ns}/a", {"x": 1}, "json"),
        (f"msg/{ns}/b", {"x": 2}, "json"),
    ])
    keys = await store.list_keys(f"msg/{ns}/")
    assert len(keys) == 2


@requires_pg
async def test_only_one_instance_wins_a_claim(store):
    """Two instances claiming one task get exactly one winner."""
    ex = f"exec-{uuid.uuid4().hex}"
    await store.register_execution(ex)
    a = await store.claim_execution(ex, node_id="node-a", lease_seconds=60)
    b = await store.claim_execution(ex, node_id="node-b", lease_seconds=60)
    assert (a is not None) and (b is None)


@requires_pg
async def test_stale_generation_renewal_is_refused(store):
    ex = f"exec-{uuid.uuid4().hex}"
    await store.register_execution(ex)
    owned = await store.claim_execution(ex, node_id="node-a", lease_seconds=60)
    assert await store.renew_execution_lease(ex, node_id="node-a", generation=owned.generation, lease_seconds=60)
    assert not await store.renew_execution_lease(ex, node_id="node-a", generation=owned.generation - 1, lease_seconds=60)


@requires_pg
async def test_live_lease_not_reclaimable_expired_is(store):
    ex = f"exec-{uuid.uuid4().hex}"
    await store.register_execution(ex)
    await store.claim_execution(ex, node_id="node-a", lease_seconds=60)

    horizon = datetime.now(timezone.utc) + timedelta(hours=1)
    live = await store.list_reclaimable_executions(node_id="node-b", stale_before=horizon)
    assert all(o.execution_id != ex for o in live)

    pool = await store._ensure_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE runtime_executions SET lease_expires_at = now() - interval '1 minute' WHERE execution_id=$1", ex
        )
    reclaimable = await store.list_reclaimable_executions(node_id="node-b", stale_before=datetime.now(timezone.utc))
    assert any(o.execution_id == ex for o in reclaimable)


@requires_pg
async def test_migration_is_idempotent_and_verified(store):
    ns = uuid.uuid4().hex[:8]
    with tempfile.TemporaryDirectory() as tmp:
        import sqlite3

        db = Path(tmp) / "flocks.db"
        with sqlite3.connect(db) as c:
            c.execute(
                "CREATE TABLE storage (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "type TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
            )
            c.executemany("INSERT INTO storage VALUES (?,?,?,?,?)", [
                (f"mig/{ns}/k{i}", json.dumps({"n": i}), "json", "now", "now") for i in range(20)
            ])

        report = await migrate_state(Path(tmp), store, key_prefixes=[f"mig/{ns}/"])
        assert report.ok and report.migrated == 20

        ok, problems = await verify_migration(Path(tmp), store, key_prefixes=[f"mig/{ns}/"])
        assert ok and not problems

        # Re-running does not duplicate.
        again = await migrate_state(Path(tmp), store, key_prefixes=[f"mig/{ns}/"])
        assert again.ok
        assert len(await store.list_keys(f"mig/{ns}/")) == 20


# ---------------------------------------------------------------------------
# Connection configuration (2026-09 full test pass regressions)
# ---------------------------------------------------------------------------

def test_missing_asyncpg_reports_the_postgres_extra(monkeypatch):
    """A bare ModuleNotFoundError says nothing about how to fix it."""
    from flocks.runtime_state.postgres import import_asyncpg

    monkeypatch.setitem(sys.modules, "asyncpg", None)
    with pytest.raises(ImportError, match="uv sync --extra postgres"):
        import_asyncpg()


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres@/db?host=/tmp/" + "d" * 100,
        "postgresql://postgres@%2Ftmp%2F" + "d" * 100 + "/db",
    ],
)
def test_overlong_unix_socket_directory_is_rejected_as_configuration(dsn):
    """AF_UNIX sun_path is 104/108 bytes; report it as a config error, not a socket OSError."""
    from flocks.runtime_state.postgres import validate_dsn_socket_path

    with pytest.raises(ValueError, match="AF_UNIX"):
        validate_dsn_socket_path(dsn)


@pytest.mark.parametrize(
    "dsn",
    ["postgresql://postgres@127.0.0.1:5433/db", "postgresql://postgres@/db?host=/tmp", "not-a-dsn"],
)
def test_ordinary_dsns_pass_socket_validation(dsn):
    from flocks.runtime_state.postgres import validate_dsn_socket_path

    validate_dsn_socket_path(dsn)


async def test_store_rejects_overlong_socket_dsn_before_connecting():
    from flocks.runtime_state.postgres import PostgresRuntimeStateStore

    store = PostgresRuntimeStateStore("postgresql://postgres@/db?host=/tmp/" + "x" * 120)
    with pytest.raises(ValueError, match="AF_UNIX"):
        await store.initialize()
    assert store._pool is None
