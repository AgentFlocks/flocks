from pathlib import Path
import tempfile

import pytest

from flocks.storage.storage import Storage
from flocks.session.callable_state import (
    add_session_callable_tools,
    clear_session_callable_tools,
    get_session_callable_tools,
)


@pytest.fixture
async def callable_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_session_callable.db"
        await Storage.init(db_path)
        yield
        await Storage.clear()


@pytest.mark.asyncio
async def test_session_callable_persists_unique_sorted_tools(callable_storage) -> None:
    await add_session_callable_tools("session-callable", ["websearch", "task", "websearch"])

    result = await get_session_callable_tools("session-callable")

    assert result == {"task", "websearch"}
    stored = await Storage.get("session_callable_tools:session-callable")
    assert stored == {"tools": ["task", "websearch"]}


@pytest.mark.asyncio
async def test_session_callable_clear_removes_cache_and_storage(callable_storage) -> None:
    await add_session_callable_tools("session-callable-clear", ["websearch"])
    await clear_session_callable_tools("session-callable-clear")

    result = await get_session_callable_tools("session-callable-clear")

    assert result == set()
    assert await Storage.get("session_callable_tools:session-callable-clear") is None
