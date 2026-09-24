"""PostgreSQL handles in the per-test schema allocated by the root fixture."""

import os

import asyncpg
import pytest

from flocks.db import registry
from flocks.db.backends.postgres import transaction_on


@pytest.fixture
async def postgres_handle():
    if not os.environ.get("FLOCKS_TEST_PG_DSN"):
        pytest.skip("FLOCKS_TEST_PG_DSN is required for PostgreSQL integration tests")
    connection = await asyncpg.connect(registry.current().dsn, timeout=10)
    try:
        async with transaction_on(connection) as handle:
            yield handle
    finally:
        await connection.close()
