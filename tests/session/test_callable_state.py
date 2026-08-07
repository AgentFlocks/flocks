import pytest

from flocks.storage.storage import Storage
from flocks.session.callable_state import (
    add_session_callable_tools,
    clear_session_callable_tools,
    get_session_callable_tools,
)

@pytest.mark.asyncio
async def test_session_callable_persists_unique_sorted_tools() -> None:
    await add_session_callable_tools("session-callable", ["websearch", "task", "websearch"])

    result = await get_session_callable_tools("session-callable")

    assert result == {"task", "websearch"}
    stored = await Storage.get("session_callable_tools:session-callable")
    assert stored == {"tools": ["task", "websearch"]}


@pytest.mark.asyncio
async def test_session_callable_clear_removes_cache_and_storage() -> None:
    await add_session_callable_tools("session-callable-clear", ["websearch"])
    await clear_session_callable_tools("session-callable-clear")

    result = await get_session_callable_tools("session-callable-clear")

    assert result == set()
    assert await Storage.get("session_callable_tools:session-callable-clear") is None
