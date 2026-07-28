"""Session transcript FTS lifecycle tests."""

from pathlib import Path
from unittest.mock import AsyncMock
import uuid

import pytest

from flocks.config.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.memory.search.hybrid import HybridSearch
from flocks.memory.types import MemorySearchResult
from flocks.memory.types import MemorySource
from flocks.provider import Provider
from flocks.session.message import Message, MessageRole
from flocks.session.session import Session, SessionInfo
from flocks.storage.session_search import (
    reconcile_session_index,
    session_fts_search,
)
from flocks.storage.storage import Storage


@pytest.fixture(autouse=True)
async def isolate_transcript_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    flocks_root = tmp_path / "flocks-home"
    data_dir = flocks_root / "data"
    monkeypatch.setenv("FLOCKS_ROOT", str(flocks_root))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(flocks_root / "logs"))
    monkeypatch.setenv("FLOCKS_RECORD_DIR", str(data_dir / "records"))

    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None
    Session.invalidate_cache()
    Message.invalidate_cache()
    MemoryManager._instances.clear()
    await Storage.init()

    yield

    Session.invalidate_cache()
    Message.invalidate_cache()
    MemoryManager._instances.clear()
    Config._global_config = None
    Config.clear_cache()
    Storage._initialized = False
    Storage._db_path = None


async def _create_session(tmp_path: Path, project_id: str = "project-search"):
    session = SessionInfo(
        id=f"session-{uuid.uuid4().hex}",
        project_id=project_id,
        directory=str(tmp_path),
        agent="rex",
        memory_enabled=True,
    )
    await Storage.set(
        f"session:{project_id}:{session.id}",
        session,
        "session",
    )
    Session.invalidate_cache()
    return session


@pytest.mark.asyncio
async def test_text_part_updates_and_message_delete_update_fts(
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path)
    message = await Message.create(
        session.id,
        MessageRole.USER,
        "initial searchable phrase",
    )

    results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="searchable",
        max_results=10,
    )
    assert [result["path"] for result in results] == [
        f"sessions/{session.id}/messages/{message.id}"
    ]

    part = (await Message.parts(message.id, session.id))[0]
    await Message.update_part(
        session.id,
        message.id,
        part.id,
        text="replacement transcript text",
    )
    assert not await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="initial",
        max_results=10,
    )
    assert await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="replacement",
        max_results=10,
    )

    assert await Message.delete(session.id, message.id)
    assert not await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="replacement",
        max_results=10,
    )


@pytest.mark.asyncio
async def test_reconciliation_restores_history_and_removes_orphans(
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path)
    message = await Message.create(
        session.id,
        MessageRole.ASSISTANT,
        "historical reconciliation marker",
    )

    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM session_transcript_fts")
        await db.execute("DELETE FROM session_transcript_index_state")
        await db.execute(
            "INSERT INTO session_transcript_fts(rowid, text) VALUES (999, 'orphan')"
        )
        await db.commit()

    stats = await reconcile_session_index(
        project_id=session.project_id,
        batch_size=1,
    )
    assert stats["updated"] == 1
    results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="reconciliation",
        max_results=10,
    )
    assert results[0]["path"].endswith(message.id)

    async with Storage.connect(Storage.get_db_path()) as db:
        cursor = await db.execute(
            "SELECT count(*) FROM session_transcript_fts WHERE rowid = 999"
        )
        assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
async def test_synthetic_text_is_not_indexed(tmp_path: Path) -> None:
    session = await _create_session(tmp_path)
    await Message.create(
        session.id,
        MessageRole.ASSISTANT,
        "synthetic compaction marker",
        synthetic=True,
    )

    assert not await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="compaction",
        max_results=10,
    )


@pytest.mark.asyncio
async def test_explicit_session_search_persists_opt_in_without_embeddings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path)
    await Message.create(
        session.id,
        MessageRole.USER,
        "session source opt in marker",
    )
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _provider_id: None)

    manager = MemoryManager(
        project_id=session.project_id,
        workspace_dir=str(tmp_path),
        config=MemoryConfig(sources=["memory"]),
    )
    results = await manager.search(
        query="marker",
        sources=[MemorySource.SESSION],
    )

    assert results
    assert results[0].source is MemorySource.SESSION
    assert manager.provider_id is None
    assert "session" in manager.config.sources

    config_path = Config.get_config_file()
    persisted = config_path.read_text(encoding="utf-8")
    assert '"sources": [' in persisted
    assert '"session"' in persisted


@pytest.mark.asyncio
async def test_memory_search_uses_fts_without_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _provider_id: None)
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute(
            """
                INSERT INTO memory_fts (
                    text, chunk_id, path, source, scope, scope_id,
                    start_line, end_line
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "durable keyword memory",
                "chunk-1",
                    "MEMORY.md",
                    "memory",
                    "global",
                    "",
                    1,
                1,
            ),
        )
        await db.commit()

    manager = MemoryManager(
        project_id="project-memory",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(sources=["memory"]),
    )
    results = await manager.search("durable")

    assert manager.provider_id is None
    assert [result.path for result in results] == ["MEMORY.md"]


@pytest.mark.asyncio
async def test_memory_sync_indexes_fts_when_embeddings_are_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _provider_id: None)
    memory_root = Config.get_data_path() / "memory"
    memory_root.mkdir(parents=True, exist_ok=True)
    (memory_root / "notes.md").write_text(
        "fts fallback indexing marker",
        encoding="utf-8",
    )

    manager = MemoryManager(
        project_id="project-sync",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(sources=["memory"]),
    )
    await manager.sync(force=True)
    results = await manager.search("fallback")

    assert results
    assert results[0].path == "notes.md"


@pytest.mark.asyncio
async def test_embedding_failure_falls_back_to_keyword_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = HybridSearch(
        project_id="project-fallback",
        provider_id="openai",
        embedding_model="embedding-model",
        config=MemoryConfig().query,
    )
    monkeypatch.setattr(
        engine,
        "_vector_search",
        AsyncMock(side_effect=RuntimeError("embedding unavailable")),
    )
    monkeypatch.setattr(
        engine,
        "_keyword_search",
        AsyncMock(
            return_value=[
                MemorySearchResult(
                    path="MEMORY.md",
                    start_line=1,
                    end_line=1,
                    score=1.0,
                    snippet="keyword fallback",
                    source=MemorySource.MEMORY,
                )
            ]
        ),
    )

    results = await engine.search(
        query="fallback",
        max_results=6,
        min_score=0.35,
        sources=[MemorySource.MEMORY],
    )
    assert [result.path for result in results] == ["MEMORY.md"]
