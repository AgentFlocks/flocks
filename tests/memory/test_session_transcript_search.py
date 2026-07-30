"""Session transcript FTS lifecycle tests."""

from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, Mock
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
    SessionSearchUnavailableError,
    _is_fts5_unavailable_error,
    ensure_session_index_ready,
    ensure_session_search_tables,
    reconcile_session_index,
    session_fts_search,
)
from flocks.storage import session_search as session_search_module
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


def test_only_explicit_missing_fts5_errors_are_classified() -> None:
    assert _is_fts5_unavailable_error(
        sqlite3.OperationalError("no such module: fts5")
    )
    assert _is_fts5_unavailable_error(
        sqlite3.OperationalError("unknown module: fts5")
    )
    assert not _is_fts5_unavailable_error(
        sqlite3.OperationalError("database is locked")
    )
    assert not _is_fts5_unavailable_error(
        RuntimeError("no such module: fts5")
    )


@pytest.mark.asyncio
async def test_session_schema_probe_degrades_when_fts5_module_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class MissingFtsConnection:
        def __init__(self):
            self.statements: list[str] = []
            self.committed = False

        async def executescript(self, sql: str):
            self.statements.append(sql)

        async def execute(self, sql: str, _parameters=()):
            self.statements.append(sql)
            if "_flocks_session_fts5_probe" in sql:
                raise sqlite3.OperationalError("no such module: fts5")
            return None

        async def commit(self):
            self.committed = True

    class MissingFtsContext:
        def __init__(self):
            self.connection = MissingFtsConnection()

        async def __aenter__(self):
            return self.connection

        async def __aexit__(self, *_args):
            return None

    context = MissingFtsContext()
    monkeypatch.setattr(
        Storage,
        "connect",
        classmethod(lambda _cls, _path=None: context),
    )

    assert not await ensure_session_search_tables(tmp_path / "missing-fts.db")
    assert context.connection.committed
    assert any(
        "DELETE FROM session_transcript_meta" in statement
        for statement in context.connection.statements
    )


@pytest.mark.asyncio
async def test_messages_persist_when_session_search_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path)
    index_message = AsyncMock(
        side_effect=AssertionError("Session FTS hook must be disabled")
    )
    monkeypatch.setattr(
        session_search_module,
        "upsert_session_document",
        index_message,
    )
    monkeypatch.setattr(Storage, "_session_search_available", False)

    message = await Message.create(
        session.id,
        MessageRole.USER,
        "canonical message survives without FTS5",
    )

    stored = await Message.get(session.id, message.id)
    assert stored is not None
    parts = await Message.parts(message.id, session.id)
    assert [part.text for part in parts if part.type == "text"] == [
        "canonical message survives without FTS5"
    ]
    index_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_session_search_reports_fts5_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Storage, "_session_search_available", False)

    with pytest.raises(
        SessionSearchUnavailableError,
        match="SQLite runtime does not support FTS5",
    ):
        await session_fts_search(
            db_path=Storage.get_db_path(),
            project_id="default",
            query="anything",
            max_results=10,
        )


@pytest.mark.asyncio
async def test_memory_manager_starts_without_fts5_and_session_search_fails_clearly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _provider_id: None)
    monkeypatch.setattr(Storage, "_session_search_available", False)

    manager = MemoryManager(
        project_id="default",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(sources=["session"]),
    )

    await manager.initialize()

    assert manager._initialized
    with pytest.raises(
        SessionSearchUnavailableError,
        match="SQLite runtime does not support FTS5",
    ):
        await manager.search(
            query="anything",
            sources=[MemorySource.SESSION],
        )


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
async def test_session_search_is_global_across_projects(
    tmp_path: Path,
) -> None:
    alpha = await _create_session(tmp_path, project_id="prj_alpha")
    beta = await _create_session(tmp_path, project_id="prj_beta")
    alpha_message = await Message.create(
        alpha.id,
        MessageRole.USER,
        "cross project session marker alpha",
    )
    beta_message = await Message.create(
        beta.id,
        MessageRole.ASSISTANT,
        "cross project session marker beta",
    )

    results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=alpha.project_id,
        query="cross project session marker",
        max_results=10,
    )

    assert {result["path"] for result in results} == {
        f"sessions/{alpha.id}/messages/{alpha_message.id}",
        f"sessions/{beta.id}/messages/{beta_message.id}",
    }

    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM session_transcript_fts")
        await db.execute("DELETE FROM session_transcript_index_state")
        await db.commit()
    stats = await reconcile_session_index(batch_size=1)
    rebuilt = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=alpha.project_id,
        query="cross project session marker",
        max_results=10,
    )
    assert stats["updated"] == 2
    assert {result["path"] for result in rebuilt} == {
        f"sessions/{alpha.id}/messages/{alpha_message.id}",
        f"sessions/{beta.id}/messages/{beta_message.id}",
    }


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
async def test_session_history_backfill_runs_only_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path)
    message = await Message.create(
        session.id,
        MessageRole.USER,
        "legacy transcript backfill marker",
    )
    async with Storage.connect(Storage.get_db_path()) as db:
        await db.execute("DELETE FROM session_transcript_fts")
        await db.execute("DELETE FROM session_transcript_index_state")
        await db.commit()

    reconcile = AsyncMock(
        wraps=session_search_module._reconcile_session_index_unlocked
    )
    monkeypatch.setattr(
        session_search_module,
        "_reconcile_session_index_unlocked",
        reconcile,
    )

    assert await ensure_session_index_ready(batch_size=1)
    assert not await ensure_session_index_ready(batch_size=1)
    assert reconcile.await_count == 1

    results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="legacy transcript",
        max_results=10,
    )
    assert [result["path"] for result in results] == [
        f"sessions/{session.id}/messages/{message.id}"
    ]


@pytest.mark.asyncio
async def test_failed_session_backfill_is_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_reconcile = session_search_module._reconcile_session_index_unlocked
    attempts = 0

    async def fail_once(*, project_id=None, batch_size=50):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected backfill failure")
        return await original_reconcile(
            project_id=project_id,
            batch_size=batch_size,
        )

    monkeypatch.setattr(
        session_search_module,
        "_reconcile_session_index_unlocked",
        fail_once,
    )

    with pytest.raises(RuntimeError, match="injected backfill failure"):
        await ensure_session_index_ready(batch_size=1)

    assert await ensure_session_index_ready(batch_size=1)
    assert attempts == 2


@pytest.mark.asyncio
async def test_memory_managers_share_one_global_file_indexer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "get", lambda _provider_id: None)
    sync = AsyncMock(
        return_value={
            "files_scanned": 0,
            "files_indexed": 0,
            "files_skipped": 0,
            "chunks_created": 0,
            "embeddings_generated": 0,
            "cache_hits": 0,
        }
    )
    monkeypatch.setattr("flocks.memory.sync.indexer.MemoryIndexer.sync", sync)

    config = MemoryConfig(sources=["memory"])
    alpha = MemoryManager.get_instance(
        project_id="prj_alpha",
        workspace_dir=str(tmp_path / "alpha"),
        config=config,
    )
    beta = MemoryManager.get_instance(
        project_id="prj_beta",
        workspace_dir=str(tmp_path / "beta"),
        config=config,
    )

    await alpha.initialize()
    await beta.initialize()

    assert alpha.indexer is beta.indexer
    assert sync.await_count == 1


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
    provider_init = AsyncMock()
    provider_get = Mock()
    monkeypatch.setattr(Provider, "init", provider_init)
    monkeypatch.setattr(Provider, "get", provider_get)
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
    provider_init.assert_not_awaited()
    provider_get.assert_not_called()
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
