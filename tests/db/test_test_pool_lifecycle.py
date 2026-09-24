"""Repeated CLI/TestClient event loops must not exhaust the isolated PG server."""

import asyncio
import os

import pytest

from flocks.db import connect


@pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")
def test_short_lived_loops_release_their_shared_pools():
    dsn = os.environ["FLOCKS_DB_DSN"]

    async def query():
        pool = await connect.pool_for(dsn)
        async with pool.acquire() as connection:
            assert await connection.fetchval("SELECT 42") == 42
        return pool

    for _ in range(32):
        pool = asyncio.run(query())
        assert pool.is_closing()
        assert not any(key[0] == dsn for key in connect._pools)


@pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="Requires isolated PostgreSQL")
@pytest.mark.parametrize("cancel", [False, True])
def test_failed_commands_release_pools_before_the_loop_closes(cancel):
    dsn = os.environ["FLOCKS_DB_DSN"]
    opened = []

    async def command():
        opened.append(await connect.pool_for(dsn))
        if cancel:
            raise asyncio.CancelledError()
        raise ValueError("command failed")

    with pytest.raises(asyncio.CancelledError if cancel else ValueError):
        asyncio.run(command())
    assert opened[0].is_closing()
    assert not any(key[0] == dsn for key in connect._pools)
