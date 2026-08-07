"""Session transcript FTS lifecycle tests."""

from datetime import UTC, datetime
from pathlib import Path
import sqlite3
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.config.config import Config
from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.memory.search.hybrid import HybridSearch
from flocks.memory.types import MemorySearchResult, MemoryTimeRange
from flocks.memory.types import MemorySource
from flocks.provider import Provider
from flocks.session.features.memory import SessionMemory
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


async def _create_session(
    tmp_path: Path,
    project_id: str = "project-search",
    *,
    owner_user_id: str | None = None,
    owner_username: str | None = None,
    metadata: dict | None = None,
    status: str = "active",
):
    session = SessionInfo(
        id=f"session-{uuid.uuid4().hex}",
        project_id=project_id,
        directory=str(tmp_path),
        agent="rex",
        memory_enabled=True,
        owner_user_id=owner_user_id,
        owner_username=owner_username,
        metadata=metadata or {},
        status=status,
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


def test_auto_embedding_uses_first_configured_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    openai = Mock()
    openai.supports_embeddings.return_value = True
    openai.is_configured.return_value = False
    google = Mock()
    google.supports_embeddings.return_value = True
    google.is_configured.return_value = True
    providers = {"openai": openai, "google": google}
    monkeypatch.setattr(Provider, "get", providers.get)

    manager = MemoryManager(
        project_id="default",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(
            search={"embedding": {"enabled": True, "provider": "auto"}},
        ),
    )

    provider_id = manager._resolve_embedding_provider()

    assert provider_id == "google"
    assert manager._resolve_embedding_model(provider_id) == (
        "models/text-embedding-004"
    )


def test_auto_embedding_prefers_configured_openai(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    providers = {}
    for provider_id in ("openai", "google"):
        provider = Mock()
        provider.supports_embeddings.return_value = True
        provider.is_configured.return_value = True
        providers[provider_id] = provider
    monkeypatch.setattr(Provider, "get", providers.get)

    manager = MemoryManager(
        project_id="default",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(
            search={"embedding": {"enabled": True, "provider": "auto"}},
        ),
    )

    provider_id = manager._resolve_embedding_provider()

    assert provider_id == "openai"
    assert manager._resolve_embedding_model(provider_id) == (
        "text-embedding-3-small"
    )


@pytest.mark.asyncio
async def test_embedding_initialization_applies_provider_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    openai = Mock()
    openai.supports_embeddings.return_value = True
    openai.is_configured.return_value = True
    apply_config = AsyncMock()
    monkeypatch.setattr(Provider, "init", AsyncMock())
    monkeypatch.setattr(Provider, "apply_config", apply_config)
    monkeypatch.setattr(
        Provider,
        "get",
        lambda provider_id: openai if provider_id == "openai" else None,
    )

    manager = MemoryManager(
        project_id="default",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(
            search={"embedding": {"enabled": True, "provider": "auto"}},
            sync={"on_session_start": False},
        ),
    )

    await manager.initialize()

    apply_config.assert_awaited_once()
    assert manager.provider_id == "openai"


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
async def test_session_search_is_limited_to_current_project(
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
    }


@pytest.mark.asyncio
async def test_session_search_filters_readable_ids_within_project(
    tmp_path: Path,
) -> None:
    readable = await _create_session(tmp_path, project_id="prj_alpha")
    private = await _create_session(tmp_path, project_id="prj_alpha")
    readable_message = await Message.create(
        readable.id,
        MessageRole.USER,
        "same project permission marker readable",
    )
    await Message.create(
        private.id,
        MessageRole.USER,
        "same project permission marker private",
    )

    results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id="prj_alpha",
        query="same project permission marker",
        max_results=10,
        readable_session_ids={readable.id},
    )

    assert [result["path"] for result in results] == [
        f"sessions/{readable.id}/messages/{readable_message.id}"
    ]
    assert not await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id="prj_alpha",
        query="same project permission marker",
        max_results=10,
        readable_session_ids=set(),
    )


@pytest.mark.asyncio
async def test_session_search_filters_time_and_allows_empty_query(
    tmp_path: Path,
) -> None:
    session = await _create_session(tmp_path, project_id="prj_alpha")
    old_message = await Message.create(
        session.id,
        MessageRole.USER,
        "time window marker old",
    )
    matching_message = await Message.create(
        session.id,
        MessageRole.ASSISTANT,
        "time window marker matching",
    )
    end_message = await Message.create(
        session.id,
        MessageRole.USER,
        "time window marker end",
    )

    def timestamp(day: int) -> int:
        return int(datetime(2026, 8, day, tzinfo=UTC).timestamp() * 1000)

    async with Storage.connect(Storage.get_db_path()) as db:
        for message, created_at in [
            (old_message, timestamp(1)),
            (matching_message, timestamp(2)),
            (end_message, timestamp(3)),
        ]:
            await db.execute(
                """
                UPDATE session_transcript_index_state
                SET created_at = ?
                WHERE message_id = ?
                """,
                (created_at, message.id),
            )
        await db.commit()

    time_range = MemoryTimeRange.from_strings(
        "2026-08-02T00:00:00Z",
        "2026-08-03T00:00:00Z",
    )
    keyword_results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="time window marker",
        max_results=10,
        time_range=time_range,
    )
    empty_query_results = await session_fts_search(
        db_path=Storage.get_db_path(),
        project_id=session.project_id,
        query="",
        max_results=10,
        time_range=time_range,
    )

    expected_path = f"sessions/{session.id}/messages/{matching_message.id}"
    assert [result["path"] for result in keyword_results] == [expected_path]
    assert [result["path"] for result in empty_query_results] == [expected_path]
    assert empty_query_results[0]["text"] == "time window marker matching"


@pytest.mark.asyncio
async def test_session_memory_uses_session_read_policy(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.project.project import Project

    caller = AuthUser(id="user-a", username="alice", role="member")
    current = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=caller.id,
        owner_username=caller.username,
    )
    owned = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=caller.id,
        owner_username=caller.username,
    )
    archived = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=caller.id,
        owner_username=caller.username,
        status="archived",
    )
    private = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id="user-b",
        owner_username="bob",
    )
    shared = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id="user-b",
        owner_username="bob",
        metadata={"shared_read_access_user_ids": [caller.id]},
    )
    deleted = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=caller.id,
        owner_username=caller.username,
        status="deleted",
    )
    other_project = await _create_session(
        tmp_path,
        project_id="prj_beta",
        owner_user_id=caller.id,
        owner_username=caller.username,
    )
    monkeypatch.setattr(Project, "shared_project_ids", lambda: set())

    token = set_current_auth_user(caller)
    try:
        memory = SessionMemory(
            session_id=current.id,
            project_id=current.project_id,
            workspace_dir=str(tmp_path),
            enabled=True,
        )
        resolved_session, resolved_caller, shared_projects = (
            await memory._search_access_context()
        )
        readable_ids = await memory._readable_session_ids(
            resolved_session,
            resolved_caller,
            shared_projects,
        )
    finally:
        reset_current_auth_user(token)

    assert readable_ids == {current.id, owned.id, archived.id, shared.id}
    assert private.id not in readable_ids
    assert deleted.id not in readable_ids
    assert other_project.id not in readable_ids


@pytest.mark.asyncio
async def test_session_memory_honors_shared_project_access(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.project.project import Project

    caller = AuthUser(id="user-a", username="alice", role="member")
    current = await _create_session(
        tmp_path,
        project_id="prj_shared",
        owner_user_id="user-b",
        owner_username="bob",
    )
    sibling = await _create_session(
        tmp_path,
        project_id="prj_shared",
        owner_user_id="user-b",
        owner_username="bob",
    )
    monkeypatch.setattr(
        Project,
        "shared_project_ids",
        lambda: {"prj_shared"},
    )

    token = set_current_auth_user(caller)
    try:
        memory = SessionMemory(
            session_id=current.id,
            project_id=current.project_id,
            workspace_dir=str(tmp_path),
            enabled=True,
        )
        resolved_session, resolved_caller, shared_projects = (
            await memory._search_access_context()
        )
        readable_ids = await memory._readable_session_ids(
            resolved_session,
            resolved_caller,
            shared_projects,
        )
    finally:
        reset_current_auth_user(token)

    assert readable_ids == {current.id, sibling.id}


@pytest.mark.asyncio
async def test_session_memory_without_caller_only_reads_current_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.auth.service import AuthService
    from flocks.project.project import Project

    current = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id="missing-user",
    )
    await _create_session(tmp_path, project_id="prj_alpha")
    monkeypatch.setattr(Project, "shared_project_ids", lambda: set())
    monkeypatch.setattr(
        AuthService,
        "get_user_by_id",
        AsyncMock(return_value=None),
    )
    memory = SessionMemory(
        session_id=current.id,
        project_id=current.project_id,
        workspace_dir=str(tmp_path),
        enabled=True,
    )

    resolved_session, caller, shared_projects = (
        await memory._search_access_context()
    )
    readable_ids = await memory._readable_session_ids(
        resolved_session,
        caller,
        shared_projects,
    )

    assert caller is None
    assert readable_ids == {current.id}


@pytest.mark.asyncio
async def test_session_memory_falls_back_to_session_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.auth.service import AuthService
    from flocks.project.project import Project

    owner_auth = AuthUser(id="user-a", username="alice", role="member")
    owner = Mock()
    owner.to_auth_user.return_value = owner_auth
    current = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=owner_auth.id,
        owner_username=owner_auth.username,
    )
    sibling = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id=owner_auth.id,
        owner_username=owner_auth.username,
    )
    private = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id="user-b",
        owner_username="bob",
    )
    monkeypatch.setattr(Project, "shared_project_ids", lambda: set())
    monkeypatch.setattr(
        AuthService,
        "get_user_by_id",
        AsyncMock(return_value=owner),
    )
    memory = SessionMemory(
        session_id=current.id,
        project_id=current.project_id,
        workspace_dir=str(tmp_path),
        enabled=True,
    )

    resolved_session, caller, shared_projects = (
        await memory._search_access_context()
    )
    readable_ids = await memory._readable_session_ids(
        resolved_session,
        caller,
        shared_projects,
    )

    assert caller == owner_auth
    assert readable_ids == {current.id, sibling.id}
    assert private.id not in readable_ids


@pytest.mark.asyncio
async def test_session_memory_rejects_unreadable_current_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from flocks.project.project import Project

    current = await _create_session(
        tmp_path,
        project_id="prj_alpha",
        owner_user_id="user-b",
        owner_username="bob",
    )
    monkeypatch.setattr(Project, "shared_project_ids", lambda: set())
    caller = AuthUser(id="user-a", username="alice", role="member")
    token = set_current_auth_user(caller)
    try:
        memory = SessionMemory(
            session_id=current.id,
            project_id=current.project_id,
            workspace_dir=str(tmp_path),
            enabled=True,
        )
        with pytest.raises(PermissionError, match="Session access denied"):
            await memory._search_access_context()
    finally:
        reset_current_auth_user(token)


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
        readable_session_ids={session.id},
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
