"""PostgreSQL DocumentStore, with the atomicity guarantee tested rather than assumed.

The reason ``mutate_many`` takes a hook at all is that a derived index must
commit with the documents it describes. A test that only checks the happy path
would pass even if the hook ran in its own transaction, so the failure case is
here too.
"""

from __future__ import annotations

import os
import uuid

import pytest

from flocks.db.store import DocumentStore, Transaction

PG_DSN = os.environ.get("FLOCKS_TEST_PG_DSN", "")
requires_pg = pytest.mark.skipif(not PG_DSN, reason="FLOCKS_TEST_PG_DSN is not set")

pytestmark = requires_pg


@pytest.fixture
async def store():
    from flocks.db.backends.postgres import PostgresDocumentStore

    instance = PostgresDocumentStore(os.environ["FLOCKS_DB_DSN"])
    await instance.initialize()
    prefix = f"test:{uuid.uuid4().hex[:8]}:"
    yield instance, prefix
    await instance.close()


async def _side_table(instance) -> str:
    """A stand-in for a derived index, created once per run."""
    pool = await instance._ensure_pool()
    async with pool.acquire() as connection:
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS derived_probe (id TEXT PRIMARY KEY, body TEXT)"
        )
    return "derived_probe"


@pytest.mark.asyncio
async def test_satisfies_the_document_store_contract(store):
    instance, _prefix = store
    assert isinstance(instance, DocumentStore)


@pytest.mark.asyncio
async def test_exists_and_get_round_trip(store):
    instance, prefix = store
    key = prefix + "a"
    assert await instance.exists(key) is False
    await instance.set(key, {"n": 1})
    assert await instance.exists(key) is True
    assert await instance.get(key) == {"n": 1}


@pytest.mark.asyncio
async def test_mutate_many_writes_and_deletes_in_one_call(store):
    instance, prefix = store
    await instance.mutate_many(
        set_entries=[(prefix + "a", {"v": 1}, "json"), (prefix + "b", {"v": 2}, "json")]
    )
    assert await instance.get(prefix + "a") == {"v": 1}
    assert await instance.get(prefix + "b") == {"v": 2}

    deleted = await instance.mutate_many(delete_keys=[prefix + "a"])
    assert deleted == 1
    assert await instance.get(prefix + "a") is None
    assert await instance.get(prefix + "b") == {"v": 2}

    # Upsert semantics: writing the same key again replaces it.
    await instance.mutate_many(set_entries=[(prefix + "b", {"v": 3}, "json")])
    assert await instance.get(prefix + "b") == {"v": 3}

    deleted = await instance.mutate_many(delete_prefixes=[prefix])
    assert deleted == 1


@pytest.mark.asyncio
async def test_empty_mutation_does_nothing(store):
    instance, _prefix = store
    assert await instance.mutate_many() == 0


@pytest.mark.asyncio
async def test_hook_commits_together_with_the_documents(store):
    instance, prefix = store
    table = await _side_table(instance)
    key = prefix + "indexed"

    async def hook(transaction: Transaction) -> None:
        # Written the portable way: no backend named, statement from dialect.
        sql = transaction.dialect.upsert(
            table, ("id", "body"), key_columns=("id",), update_columns=("body",)
        )
        await transaction.execute(sql, (key, "indexed body"))

    await instance.mutate_many(
        set_entries=[(key, {"text": "indexed body"}, "json")], transaction_hook=hook
    )

    assert await instance.get(key) == {"text": "indexed body"}
    pool = await instance._ensure_pool()
    async with pool.acquire() as connection:
        body = await connection.fetchval(f"SELECT body FROM {table} WHERE id = $1", key)
    assert body == "indexed body"


@pytest.mark.asyncio
async def test_hook_failure_rolls_back_the_documents_too(store):
    instance, prefix = store
    await _side_table(instance)
    key = prefix + "rollback"

    async def failing_hook(transaction: Transaction) -> None:
        raise RuntimeError("derived index failed")

    with pytest.raises(RuntimeError):
        await instance.mutate_many(
            set_entries=[(key, {"v": "should not survive"}, "json")],
            transaction_hook=failing_hook,
        )

    # The document must not be there: a search index that failed to update
    # cannot be allowed to leave a message only half-recorded.
    assert await instance.get(key) is None


@pytest.mark.asyncio
async def test_pre_hook_runs_before_documents_in_the_same_transaction(store):
    instance, prefix = store
    key = prefix + "ordered"
    await instance.set(key, {"v": "before"})
    observed = []

    async def before(transaction: Transaction) -> None:
        row = await transaction.fetch_one(
            "SELECT value ->> 'v', pg_backend_pid(), txid_current() "
            "FROM runtime_state WHERE key = ? FOR UPDATE", (key,),
        )
        observed.append(row)

    async def after(transaction: Transaction) -> None:
        row = await transaction.fetch_one(
            "SELECT value ->> 'v', pg_backend_pid(), txid_current() "
            "FROM runtime_state WHERE key = ?", (key,),
        )
        observed.append(row)

    await instance.mutate_many(
        set_entries=[(key, {"v": "after"}, "json")],
        pre_transaction_hook=before, transaction_hook=after,
    )
    assert [row[0] for row in observed] == ["before", "after"]
    assert observed[0][1:] == observed[1][1:]
    assert await instance.get(key) == {"v": "after"}


@pytest.mark.asyncio
async def test_pre_hook_failure_rolls_back_its_writes_and_keeps_documents(store):
    instance, prefix = store
    key, side_key = prefix + "original", prefix + "side-effect"
    await instance.set(key, {"v": "before"})

    async def before(transaction: Transaction) -> None:
        await transaction.execute(
            "INSERT INTO runtime_state(key, value, value_type) VALUES (?, ?::jsonb, 'json')",
            (side_key, '{"v":"temporary"}'),
        )
        raise RuntimeError("pre-hook failed")

    with pytest.raises(RuntimeError, match="pre-hook failed"):
        await instance.mutate_many(delete_keys=[key], pre_transaction_hook=before)
    assert await instance.get(key) == {"v": "before"}
    assert await instance.get(side_key) is None


@pytest.mark.asyncio
async def test_pre_hook_without_document_mutations_still_commits(store):
    instance, prefix = store
    key = prefix + "hook-only"

    async def before(transaction: Transaction) -> None:
        await transaction.execute(
            "INSERT INTO runtime_state(key, value, value_type) VALUES (?, ?::jsonb, 'json')",
            (key, '{"v":"committed"}'),
        )

    assert await instance.mutate_many(pre_transaction_hook=before) == 0
    assert await instance.get(key) == {"v": "committed"}
