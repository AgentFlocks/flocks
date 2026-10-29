import pytest

from flocks.storage.storage import Storage
from flocks.session.callable_state import (
    add_session_callable_tools,
    clear_session_callable_tools,
    get_session_callable_tools,
    invalidate_session_callable_tools_cache,
)


@pytest.fixture
def callable_storage(isolate_session_storage):
    """Reuse the outer storage lifetime and isolate process-local tool state."""
    session_ids = ("session-callable", "session-callable-clear")
    for session_id in session_ids:
        invalidate_session_callable_tools_cache(session_id)
    yield
    for session_id in session_ids:
        invalidate_session_callable_tools_cache(session_id)


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
