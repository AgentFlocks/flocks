"""Marker rewriting decides whether hundreds of existing statements keep
working, and a mistake binds the wrong value rather than raising, so the edge
cases are tested explicitly."""

from __future__ import annotations

import os

import pytest

from flocks.db.dialect import PostgresDialect
from flocks.db.markers import rewrite_markers

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")

pg = PostgresDialect().placeholder


def test_markers_are_numbered_in_order():
    assert rewrite_markers("WHERE a = ? AND b = ?", pg) == "WHERE a = $1 AND b = $2"


def test_statement_without_markers_is_unchanged():
    assert rewrite_markers("SELECT 1", pg) == "SELECT 1"


def test_question_marks_inside_string_literals_are_left_alone():
    # The killer case: a LIKE pattern that happens to contain a question mark.
    assert rewrite_markers("WHERE name LIKE '%?%' AND id = ?", pg) == (
        "WHERE name LIKE '%?%' AND id = $1"
    )
    assert rewrite_markers("""WHERE tag = "why?" AND id = ?""", pg) == (
        '''WHERE tag = "why?" AND id = $1'''
    )


def test_escaped_quotes_do_not_end_the_literal_early():
    original = "WHERE note = 'it''s a ? here' AND id = ?"
    assert rewrite_markers(original, pg) == "WHERE note = 'it''s a ? here' AND id = $1"




def test_multiline_statement_numbers_across_lines():
    sql = """
        INSERT INTO t (a, b, c)
        VALUES (?, ?, ?)
    """
    assert "VALUES ($1, $2, $3)" in rewrite_markers(sql, pg)


@requires_pg
@pytest.mark.asyncio
async def test_existing_sqlite_style_statement_runs_on_postgres():
    """The point of all this: unchanged ``?`` SQL executing against PostgreSQL."""
    import asyncpg

    from flocks.db.backends.postgres import PostgresTransaction

    connection = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"])
    try:
        async with connection.transaction():
            await connection.execute(
                "CREATE TABLE marker_probe (id TEXT PRIMARY KEY, note TEXT)"
            )
            handle = PostgresTransaction(connection)

            # Written the SQLite way, executed on PostgreSQL, untouched.
            await handle.execute(
                "INSERT INTO marker_probe (id, note) VALUES (?, ?)", ("a", "first")
            )
            row = await handle.fetch_one_mapping(
                "SELECT note FROM marker_probe WHERE id = ?", ("a",)
            )
            assert row["note"] == "first"

            # And a literal question mark still means itself.
            await handle.execute(
                "INSERT INTO marker_probe (id, note) VALUES (?, ?)", ("b", "why?")
            )
            rows = await handle.fetch_all_mappings(
                "SELECT id FROM marker_probe WHERE note LIKE '%?%' AND id = ?", ("b",)
            )
            assert [r["id"] for r in rows] == ["b"]
    finally:
        await connection.close()
