"""Document semantics and atomic derived-index writes on PostgreSQL."""

from __future__ import annotations

import os
import uuid

import pytest

from flocks.db.store import DocumentStore, Transaction

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")



@pytest.fixture
async def store():
    """Use the unique PostgreSQL schema supplied by tests/conftest.py."""
    from flocks.db import registry
    from flocks.db.backends.postgres import PostgresDocumentStore
    if not PG_DSN:
        pytest.skip("FLOCKS_TEST_PG_DSN is required")
    instance = PostgresDocumentStore(registry.current().dsn)
    await instance.initialize()
    try:
        yield instance, f"contract:{uuid.uuid4().hex[:8]}:"
    finally:
        await instance.close()


@pytest.mark.asyncio
async def test_conforms_to_the_contract(store):
    instance, _prefix = store
    assert isinstance(instance, DocumentStore)


@pytest.mark.asyncio
async def test_set_then_get_returns_the_value(store):
    instance, prefix = store
    await instance.set(prefix + "a", {"n": 1, "nested": {"x": ["y"]}})
    assert await instance.get(prefix + "a") == {"n": 1, "nested": {"x": ["y"]}}


@pytest.mark.asyncio
async def test_missing_key_reads_as_none(store):
    instance, prefix = store
    assert await instance.get(prefix + "absent") is None


@pytest.mark.asyncio
async def test_exists_tracks_writes_and_deletes(store):
    instance, prefix = store
    key = prefix + "e"
    assert await instance.exists(key) is False
    await instance.set(key, {"v": 1})
    assert await instance.exists(key) is True
    assert await instance.delete(key) is True
    assert await instance.exists(key) is False


@pytest.mark.asyncio
async def test_delete_reports_whether_anything_went(store):
    instance, prefix = store
    await instance.set(prefix + "d", {"v": 1})
    assert await instance.delete(prefix + "d") is True
    assert await instance.delete(prefix + "d") is False


@pytest.mark.asyncio
async def test_set_overwrites_rather_than_duplicating(store):
    instance, prefix = store
    key = prefix + "over"
    await instance.set(key, {"v": 1})
    await instance.set(key, {"v": 2})
    assert await instance.get(key) == {"v": 2}
    assert await instance.list_keys(key) == [key]


@pytest.mark.asyncio
async def test_list_keys_filters_by_prefix_and_sorts(store):
    instance, prefix = store
    await instance.set_many(
        [(prefix + "b", {"v": 2}, "json"), (prefix + "a", {"v": 1}, "json")]
    )
    await instance.set("other:" + prefix + "c", {"v": 3})
    try:
        assert await instance.list_keys(prefix) == [prefix + "a", prefix + "b"]
    finally:
        await instance.delete("other:" + prefix + "c")


@pytest.mark.asyncio
async def test_mutate_many_applies_writes_and_deletes(store):
    instance, prefix = store
    await instance.mutate_many(
        set_entries=[(prefix + "m1", {"v": 1}, "json"), (prefix + "m2", {"v": 2}, "json")]
    )
    assert await instance.get(prefix + "m1") == {"v": 1}

    deleted = await instance.mutate_many(delete_keys=[prefix + "m1"])
    assert deleted == 1
    assert await instance.get(prefix + "m1") is None
    assert await instance.get(prefix + "m2") == {"v": 2}


@pytest.mark.asyncio
async def test_empty_mutation_is_a_no_op(store):
    instance, _prefix = store
    assert await instance.mutate_many() == 0


@pytest.mark.asyncio
async def test_hook_sees_a_transaction_and_commits_with_the_documents(store):
    instance, prefix = store
    key = prefix + "hooked"
    seen: dict = {}

    async def hook(transaction: Transaction) -> None:
        # The hook must receive the portable handle on every backend.
        assert isinstance(transaction, Transaction)
        seen["dialect"] = transaction.dialect.name
        await transaction.execute(
            f"CREATE TABLE IF NOT EXISTS contract_probe (id {'TEXT'} PRIMARY KEY, body TEXT)"
        )
        sql = transaction.dialect.upsert(
            "contract_probe", ("id", "body"), key_columns=("id",), update_columns=("body",)
        )
        await transaction.execute(sql, (key, "from hook"))

    await instance.mutate_many(set_entries=[(key, {"v": "doc"}, "json")], transaction_hook=hook)

    assert seen["dialect"] == "postgres"
    assert await instance.get(key) == {"v": "doc"}


@pytest.mark.asyncio
async def test_hook_failure_rolls_the_documents_back(store):
    instance, prefix = store
    key = prefix + "rollback"

    async def failing(transaction: Transaction) -> None:
        raise RuntimeError("derived index failed")

    with pytest.raises(RuntimeError):
        await instance.mutate_many(
            set_entries=[(key, {"v": "must not survive"}, "json")], transaction_hook=failing
        )

    assert await instance.get(key) is None
