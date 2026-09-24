"""Synchronous plugin facade must retain transaction and timeout semantics."""
import asyncio
import os
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

from flocks.db import registry, sync_query

pytestmark = pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")


@pytest.fixture
def database(monkeypatch):
    import asyncpg
    dsn = os.environ["FLOCKS_TEST_PG_DSN"]
    schema = "sync_query_" + uuid.uuid4().hex
    async def prepare():
        connection = await asyncpg.connect(dsn)
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.close()
    asyncio.run(prepare())
    parts = urlsplit(dsn)
    query = dict(parse_qsl(parts.query))
    query["search_path"] = schema + ",public"
    isolated = urlunsplit(parts._replace(query=urlencode(query)))
    monkeypatch.setattr(registry, "_settings", registry.DatabaseSettings("postgres", isolated))
    with sync_query.connect() as db:
        db.execute("CREATE TABLE sample(id TEXT PRIMARY KEY, value TEXT)")


def test_transaction_commit_rollback_and_streaming_rows(database):
    with sync_query.connect() as db:
        db.executemany("INSERT INTO sample(id, value) VALUES (?, ?)", [(str(i), f"v{i}") for i in range(600)])
    with sync_query.connect(readonly=True) as db:
        assert db.table_exists("sample")
        assert db.columns("sample") == {"id", "value"}
        rows = list(db.execute("SELECT id, value FROM sample ORDER BY id"))
        assert len(rows) == 600 and rows[0][0] == rows[0]["id"]
    with pytest.raises(RuntimeError):
        with sync_query.connect() as db:
            db.execute("INSERT INTO sample(id, value) VALUES (?, ?)", ("rollback", "x"))
            raise RuntimeError("abort")
    with sync_query.connect() as db:
        assert db.execute("SELECT value FROM sample WHERE id = ?", ("rollback",)).fetchone() is None


def test_timeout_releases_connection_and_readonly_rejects_write(database):
    with pytest.raises(Exception):
        with sync_query.connect(timeout=0.05) as db:
            db.execute("SELECT pg_sleep(?)", (0.5,)).fetchone()
    with sync_query.connect() as db:
        assert db.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(Exception):
        with sync_query.connect(readonly=True) as db:
            db.execute("INSERT INTO sample(id, value) VALUES (?, ?)", ("forbidden", "x"))


def test_table_exists_does_not_fall_back_to_another_schema(database):
    fallback = "sync_query_fallback_" + uuid.uuid4().hex
    with sync_query.connect() as db:
        current = db.execute("SELECT current_schema()").fetchone()[0]
        db.execute(f'CREATE SCHEMA "{fallback}"')
        db.execute(f'CREATE TABLE "{fallback}".outside(id TEXT PRIMARY KEY)')
        db.execute(f'SET LOCAL search_path = "{current}", "{fallback}", public')
        assert db.execute("SELECT to_regclass('outside')").fetchone()[0]
        assert not db.table_exists("outside")
        assert db.columns("outside") == set()
        assert db.table_exists("sample")


@pytest.mark.asyncio
async def test_async_callers_must_offload(database):
    with pytest.raises(RuntimeError, match="worker thread"):
        with sync_query.connect():
            pass


def test_one_execution_and_one_shared_forward_cursor(database):
    with sync_query.connect() as db:
        db.execute("CREATE SEQUENCE probe_counter")
        query = db.execute("SELECT nextval('probe_counter') AS number FROM generate_series(1,5)")
        assert db.execute("SELECT last_value FROM probe_counter").fetchone()[0] == 5
        assert query.fetchone()["number"] == 1
        assert query.fetchone()[0] == 2
        assert [row[0] for row in query.fetchall()] == [3, 4, 5]
        assert query.fetchone() is None
        assert query.fetchall() == [] and list(query) == []
        assert db.execute("SELECT last_value FROM probe_counter").fetchone()[0] == 5


def test_unconsumed_returning_and_writable_cte_execute_and_commit(database):
    with sync_query.connect() as db:
        db.execute("INSERT INTO sample(id,value) VALUES ('one','initial')\nRETURNING id")
        db.execute("WITH changed AS (UPDATE sample SET value='changed' WHERE id='one' RETURNING id) SELECT id FROM changed")
        query = db.execute("UPDATE sample SET value=value || '!' WHERE id='one' RETURNING value")
        assert query.fetchone()[0] == "changed!"
        assert query.fetchone() is None
    with sync_query.connect() as db:
        assert db.execute("SELECT value FROM sample WHERE id='one'").fetchone()[0] == "changed!"


def test_large_result_spills_and_fetches_remaining_rows(database):
    with sync_query.connect() as db:
        query = db.execute("SELECT i,repeat('x',4096) AS text FROM generate_series(1,1200) i")
        assert query._result._rolled  # The facade does not retain all 4.8 MB in RAM.
        assert query.fetchone()[0] == 1
        iterator = iter(query)
        assert next(iterator)[0] == 2
        rows = query.fetchall()
        assert len(rows) == 1198 and rows[-1][0] == 1200
        assert list(iterator) == []
        assert dict(rows[-1]) == {"i": 1200, "text": "x" * 4096}
    with pytest.raises(RuntimeError, match="closed"):
        query.fetchone()


def test_unconsumed_write_rolls_back_on_context_error(database):
    with pytest.raises(RuntimeError, match="abort"):
        with sync_query.connect() as db:
            db.execute("/* comment */ WITH x AS (INSERT INTO sample VALUES ('one','v') RETURNING id) SELECT * FROM x")
            raise RuntimeError("abort")
    with sync_query.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 0


def test_returning_more_than_one_batch_executes_all_writes_without_consumption(database):
    with sync_query.connect() as db:
        db.execute("INSERT INTO sample SELECT i::text,'v' FROM generate_series(1,700) i RETURNING id")
    with sync_query.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sample").fetchone()[0] == 700
