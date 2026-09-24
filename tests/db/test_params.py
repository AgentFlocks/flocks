"""Placeholder numbering is the kind of thing that fails silently, so it is
tested against the exact strings each backend needs."""

from __future__ import annotations

import pytest

from flocks.db.dialect import PostgresDialect
from flocks.db.params import Params, in_clause


def test_markers_follow_the_backend():
    postgres = Params(PostgresDialect())
    assert [postgres.add("a"), postgres.add("b")] == ["$1", "$2"]


def test_values_keep_the_order_markers_were_issued_in():
    params = Params(PostgresDialect())
    clause = f"a = {params.add(1)} AND b = {params.add(2)} AND c = {params.add(3)}"
    assert clause == "a = $1 AND b = $2 AND c = $3"
    assert params.values == (1, 2, 3)
    assert len(params) == 3


def test_optional_filters_stay_numbered_correctly():
    """The case hand-written numbering gets wrong."""
    def build(dialect, *, provider=None, session=None):
        params = Params(dialect)
        clause = f"WHERE model_id = {params.add('gpt')}"
        if provider:
            clause += f" AND provider_id = {params.add(provider)}"
        if session:
            clause += f" AND session_id = {params.add(session)}"
        return clause, params.values

    clause, values = build(PostgresDialect(), session="s1")
    assert clause == "WHERE model_id = $1 AND session_id = $2"
    assert values == ("gpt", "s1")

    clause, values = build(PostgresDialect(), provider="p1", session="s1")
    assert clause == "WHERE model_id = $1 AND provider_id = $2 AND session_id = $3"
    assert values == ("gpt", "p1", "s1")


def test_in_clause_numbers_each_element():
    params = Params(PostgresDialect())
    assert in_clause(params, "session_id", ["a", "b", "c"]) == "session_id IN ($1, $2, $3)"
    assert params.values == ("a", "b", "c")


def test_empty_in_clause_matches_nothing_rather_than_everything():
    params = Params(PostgresDialect())
    assert in_clause(params, "session_id", []) == "1 = 0"
    assert params.values == ()


@pytest.mark.asyncio
async def test_a_built_clause_actually_runs(postgres_handle):
    handle = postgres_handle
    await handle.execute("CREATE TABLE t (name TEXT, score INTEGER)")
    await handle.execute("INSERT INTO t VALUES ('a', 1), ('b', 2), ('c', 3)")

    params = Params(handle.dialect)
    clause = f"score >= {params.add(2)} AND {in_clause(params, 'name', ['b', 'c'])}"
    rows = await handle.fetch_all_mappings(f"SELECT name FROM t WHERE {clause} ORDER BY name", params.values)
    assert [row["name"] for row in rows] == ["b", "c"]
