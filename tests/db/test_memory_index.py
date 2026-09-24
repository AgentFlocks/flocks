"""Memory substring search contracts on real PostgreSQL.

Chinese substring matching uses pg_trgm without a segmenter. Cases execute
real queries so indexing and case-insensitive fallback semantics are verified.
"""

from __future__ import annotations

import os

import pytest

from flocks.db.params import Params
from flocks.db.search import (
    MEMORY_MIN_TERM_CHARS,
    PostgresMemoryIndex,
    memory_search_index_for,
)

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")

ROWS = [
    ("c1", "notes/alerts.md", "memory", "global", "", 1, 9, "告警处理约定：先降噪再派单"),
    ("c2", "notes/deploy.md", "memory", "global", "", 1, 9, "deployment runbook for staging"),
    ("c3", "daily/2026-09-17.md", "memory", "global", "", 1, 9, "今天把迁移收尾了"),
]


def test_every_backend_gets_an_index_or_an_honest_none():
    assert memory_search_index_for("sqlite") is None
    assert isinstance(memory_search_index_for("postgres"), PostgresMemoryIndex)
    # MySQL needs an ngram parser before it tokenises CJK at all; saying so is
    # better than returning a search that finds nothing.
    assert memory_search_index_for("mysql") is None


def test_short_terms_are_below_what_a_trigram_index_can_match():
    assert MEMORY_MIN_TERM_CHARS == 3


async def _search(handle, index, query, *, short_terms=()):
    # The query string is whatever this backend's matching consumes: FTS5
    # syntax on SQLite, the bare terms on PostgreSQL.
    prepared = index.prepare_query(query) if query else None
    params = Params(handle.dialect)
    rank = index.rank_expression(params, prepared)
    sql = f"SELECT f.chunk_id, {rank} AS rank_score FROM {index.table} f WHERE 1 = 1"
    if prepared:
        sql += f" AND {index.match_clause(params, 'f.text', prepared)}"
    for term in short_terms:
        sql += f" AND {index.contains_clause(params, 'f.text', f'%{term}%')}"
    rows = await handle.fetch_all(sql, params.values)
    return sorted(str(row[0]) for row in rows)












@requires_pg
@pytest.mark.asyncio
async def test_postgres_answers_the_same_questions():
    import asyncpg

    from flocks.db.backends.postgres import PostgresTransaction

    index = PostgresMemoryIndex()
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        async with connection.transaction():
            handle = PostgresTransaction(connection)
            # A temporary copy of the real schema: same columns, same names,
            # gone when this transaction ends.
            await connection.execute(
                f"CREATE TABLE {index.table} ("
                " chunk_id TEXT PRIMARY KEY, path TEXT, source TEXT, scope TEXT,"
                " scope_id TEXT, start_line INTEGER, end_line INTEGER, text TEXT"
                ")"
            )
            for row in ROWS:
                await handle.execute(
                    f"INSERT INTO {index.table}"
                    " (chunk_id, path, source, scope, scope_id, start_line, end_line, text)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )

            assert await _search(handle, index, "告警处理") == ["c1"]
            assert await _search(handle, index, "runbook") == ["c2"]
            assert await _search(handle, index, None, short_terms=["派单"]) == ["c1"]

            rows = await handle.fetch_all(
                f"SELECT f.chunk_id FROM {index.table} f"
                f" WHERE {index.daily_path_clause('f.path')}"
            )
            assert [str(row[0]) for row in rows] == ["c3"]
    finally:
        await connection.close()


@requires_pg
@pytest.mark.asyncio
async def test_postgres_case_folding_matches_sqlite():
    """SQLite's LIKE ignores ASCII case; the fallback here must too."""
    import asyncpg

    from flocks.db.backends.postgres import PostgresTransaction

    index = PostgresMemoryIndex()
    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        async with connection.transaction():
            handle = PostgresTransaction(connection)
            await connection.execute(
                f"CREATE TABLE {index.table} (chunk_id TEXT, text TEXT)"
                ""
            )
            await handle.execute(
                f"INSERT INTO {index.table} (chunk_id, text) VALUES (?, ?)",
                ("c1", "Deployment Runbook"),
            )
            assert await _search(handle, index, None, short_terms=["runbook"]) == ["c1"]
    finally:
        await connection.close()
