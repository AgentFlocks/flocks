"""Tests for keeping the Memory search index fresh."""

from unittest.mock import AsyncMock

import pytest

from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager


@pytest.mark.asyncio
async def test_search_syncs_before_query_when_on_search_enabled(tmp_path) -> None:
    """Search must discover Memory files changed outside MemoryManager."""
    manager = MemoryManager(
        project_id="prj_test",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(),
    )
    manager._initialized = True
    manager.search_engine = AsyncMock()
    manager.search_engine.search.return_value = []
    manager.sync = AsyncMock()

    await manager.search(query="updated preference")

    manager.sync.assert_awaited_once_with(reason="search")
    manager.search_engine.search.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_skips_sync_when_on_search_disabled(tmp_path) -> None:
    """The explicit on_search configuration remains authoritative."""
    config = MemoryConfig()
    config.sync.on_search = False
    manager = MemoryManager(
        project_id="prj_test",
        workspace_dir=str(tmp_path),
        config=config,
    )
    manager._initialized = True
    manager.search_engine = AsyncMock()
    manager.search_engine.search.return_value = []
    manager.sync = AsyncMock()

    await manager.search(query="updated preference")

    manager.sync.assert_not_awaited()
    manager.search_engine.search.assert_awaited_once()
