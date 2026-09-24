"""Tests for Global and Project Memory scope isolation."""

import os
from pathlib import Path
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.memory.types import MemoryScope, MemoryTimeRange
from flocks.storage import (
    Storage,
    ensure_vector_tables,
    fts_search,
    replace_memory_file_index,
    vector_search,
)


def _file_entry(
    scope: str,
    scope_id: str,
    path: str,
) -> dict[str, object]:
    return {
        "scope": scope,
        "scope_id": scope_id,
        "path": path,
        "hash": f"hash:{scope}:{scope_id}:{path}",
        "mtime": 1,
        "size": 10,
    }


def _chunk(
    scope: str,
    scope_id: str,
    path: str,
    text: str,
    embedding: list[float] | None = None,
) -> dict[str, object]:
    return {
        "id": f"chunk:{scope}:{scope_id}:{path}",
        "scope": scope,
        "scope_id": scope_id,
        "path": path,
        "source": "memory",
        "start_line": 1,
        "end_line": 1,
        "hash": f"hash:{text}",
        "text": text,
        "embedding": embedding,
        "embedding_model": "test" if embedding else None,
        "embedding_dims": len(embedding) if embedding else None,
    }


@pytest.mark.asyncio
async def test_search_reconciles_filesystem_before_every_search(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(
        project_id="prj_alpha",
        workspace_dir=str(tmp_path),
        config=MemoryConfig(),
    )
    manager._initialized = True
    manager.sync = AsyncMock(return_value={})
    manager.search_engine = SimpleNamespace(
        search=AsyncMock(return_value=[]),
    )

    await manager.search("new filesystem memory")

    manager.sync.assert_awaited_once_with(reason="search")


@pytest.mark.asyncio
async def test_memory_search_uses_global_and_current_project_scopes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "scope.db"
    await Storage.init(db_path)
    records = [
        ("global", "", "USER.md", "scopeword user"),
        ("global", "", "MEMORY.md", "scopeword global"),
        ("global", "", "daily/2026-08-03.md", "scopeword daily"),
        (
            "project",
            "prj_alpha",
            "projects/prj_alpha/MEMORY.md",
            "scopeword alpha",
        ),
        (
            "project",
            "prj_beta",
            "projects/prj_beta/MEMORY.md",
            "scopeword beta",
        ),
    ]
    for scope, scope_id, path, text in records:
        await replace_memory_file_index(
            db_path,
            file_entry=_file_entry(scope, scope_id, path),
            chunks=[_chunk(scope, scope_id, path, text, [1.0, 0.0])],
        )

    expected_global_paths = {
        "USER.md",
        "MEMORY.md",
        "daily/2026-08-03.md",
    }
    expected_alpha_paths = expected_global_paths | {
        "projects/prj_alpha/MEMORY.md",
    }

    alpha_fts = await fts_search(db_path, "prj_alpha", "scopeword")
    default_fts = await fts_search(db_path, "default", "scopeword")
    alpha_vector = await vector_search(
        db_path,
        "prj_alpha",
        [1.0, 0.0],
    )
    default_vector = await vector_search(
        db_path,
        "default",
        [1.0, 0.0],
    )

    assert {result["path"] for result in alpha_fts} == expected_alpha_paths
    assert {result["path"] for result in alpha_vector} == expected_alpha_paths
    assert {result["path"] for result in default_fts} == expected_global_paths
    assert {result["path"] for result in default_vector} == expected_global_paths


@pytest.mark.asyncio
async def test_memory_time_range_searches_only_matching_daily_files(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "time-range.db"
    await Storage.init(db_path)
    records = [
        ("global", "", "USER.md", "timeline user"),
        ("global", "", "MEMORY.md", "timeline global"),
        ("global", "", "daily/2026-08-01.md", "timeline old daily"),
        ("global", "", "daily/2026-08-03.md", "timeline matching daily"),
        ("global", "", "daily/2026-08-04.md", "timeline end daily"),
        (
            "project",
            "prj_alpha",
            "projects/prj_alpha/MEMORY.md",
            "timeline project",
        ),
    ]
    for scope, scope_id, path, text in records:
        await replace_memory_file_index(
            db_path,
            file_entry=_file_entry(scope, scope_id, path),
            chunks=[_chunk(scope, scope_id, path, text, [1.0, 0.0])],
        )

    time_range = MemoryTimeRange.from_strings("2026-08-02", "2026-08-04")

    keyword_results = await fts_search(
        db_path,
        "prj_alpha",
        "timeline",
        time_range=time_range,
    )
    empty_query_results = await fts_search(
        db_path,
        "prj_alpha",
        "",
        time_range=time_range,
    )
    vector_results = await vector_search(
        db_path,
        "prj_alpha",
        [1.0, 0.0],
        time_range=time_range,
    )

    expected_paths = {"daily/2026-08-03.md"}
    assert {result["path"] for result in keyword_results} == expected_paths
    assert {result["path"] for result in empty_query_results} == expected_paths
    assert {result["path"] for result in vector_results} == expected_paths


def test_memory_time_range_rejects_reversed_bounds() -> None:
    with pytest.raises(ValueError, match="start_time must be earlier"):
        MemoryTimeRange.from_strings("2026-08-04", "2026-08-03")


@pytest.mark.asyncio
async def test_indexer_scans_global_and_all_projects(
    tmp_path: Path,
) -> None:
    memory_root = tmp_path / "memory"
    (memory_root / "daily").mkdir(parents=True)
    (memory_root / "projects" / "prj_alpha").mkdir(parents=True)
    (memory_root / "projects" / "prj_beta").mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text("global", encoding="utf-8")
    (memory_root / "daily" / "2026-01-01.md").write_text(
        "daily",
        encoding="utf-8",
    )
    (memory_root / "projects" / "prj_alpha" / "MEMORY.md").write_text(
        "alpha",
        encoding="utf-8",
    )
    (memory_root / "projects" / "prj_beta" / "MEMORY.md").write_text(
        "beta",
        encoding="utf-8",
    )
    indexer = MemoryIndexer(
        project_id="prj_alpha",
        workspace_dir=tmp_path,
        provider_id=None,
        embedding_model="unused",
        config=MemoryConfig(),
    )

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        files = await indexer._scan_memory_files()

    identities = {(entry.scope.value, entry.scope_id, entry.path) for entry in files}
    assert ("global", "", "MEMORY.md") in identities
    assert ("global", "", "daily/2026-01-01.md") in identities
    assert (
        "project",
        "prj_alpha",
        "projects/prj_alpha/MEMORY.md",
    ) in identities
    assert (
        "project",
        "prj_beta",
        "projects/prj_beta/MEMORY.md",
    ) in identities


@pytest.mark.asyncio
async def test_indexer_does_not_read_unchanged_files(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "metadata-scan.db"
    await Storage.init(db_path)
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    memory_file = memory_root / "MEMORY.md"
    memory_file.write_text("stable memory", encoding="utf-8")
    indexer = MemoryIndexer(
        project_id="global",
        workspace_dir=tmp_path,
        provider_id=None,
        embedding_model="unused",
        config=MemoryConfig(),
    )

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        initial = await indexer.sync()
        with patch.object(
            Path,
            "read_text",
            side_effect=AssertionError("unchanged Memory file was read"),
        ):
            unchanged = await indexer.sync()

    assert initial["files_indexed"] == 1
    assert unchanged["files_indexed"] == 0
    assert unchanged["files_skipped"] == 1


@pytest.mark.asyncio
async def test_indexer_refreshes_metadata_without_reindexing_unchanged_content(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "metadata-refresh.db"
    await Storage.init(db_path)
    memory_root = tmp_path / "memory"
    memory_root.mkdir()
    memory_file = memory_root / "MEMORY.md"
    memory_file.write_text("stable memory", encoding="utf-8")
    indexer = MemoryIndexer(
        project_id="global",
        workspace_dir=tmp_path,
        provider_id=None,
        embedding_model="unused",
        config=MemoryConfig(),
    )

    with patch("flocks.config.Config.get_data_path", return_value=tmp_path):
        await indexer.sync()
        before = memory_file.stat()
        os.utime(
            memory_file,
            ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000),
        )
        with patch.object(
            indexer,
            "_index_file",
            wraps=indexer._index_file,
        ) as index_file:
            touched = await indexer.sync()
        indexed_files = await indexer._get_indexed_files()
        with patch.object(
            Path,
            "read_text",
            side_effect=AssertionError("refreshed Memory file was read again"),
        ):
            unchanged = await indexer.sync()

    indexed = indexed_files[("global", "", "MEMORY.md")]
    assert touched["files_indexed"] == 0
    assert touched["files_skipped"] == 1
    index_file.assert_not_awaited()
    assert indexed["mtime"] == memory_file.stat().st_mtime
    assert unchanged["files_indexed"] == 0
    assert unchanged["files_skipped"] == 1


@pytest.mark.asyncio
async def test_old_memory_index_schema_is_rebuilt_without_other_data(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy.db"
    async with Storage.transaction(ensure_init=False) as connection:
        await connection.executescript(
            """
            CREATE TABLE memory_files (
                path TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                hash TEXT NOT NULL,
                mtime REAL NOT NULL,
                size INTEGER NOT NULL,
                indexed_at REAL NOT NULL
            );
            CREATE TABLE memory_chunks (
                id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                project_id TEXT NOT NULL,
                source TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                hash TEXT NOT NULL,
                text TEXT NOT NULL,
                embedding BYTEA,
                embedding_model TEXT,
                embedding_dims INTEGER,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE memory_fts (text TEXT, chunk_id TEXT, path TEXT, source TEXT,
                project_id TEXT, start_line INTEGER, end_line INTEGER);
            CREATE TABLE memory_embedding_cache (
                text_hash TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                embedding BYTEA NOT NULL,
                dims INTEGER NOT NULL,
                created_at REAL NOT NULL,
                accessed_at REAL NOT NULL,
                PRIMARY KEY (text_hash, provider, model)
            );
            INSERT INTO memory_embedding_cache
            VALUES ('hash', 'provider', 'model', '[1.0]', 1, 1, 1);
            CREATE TABLE session_transcript_fts(text TEXT);
            INSERT INTO session_transcript_fts VALUES ('preserved');
            INSERT INTO memory_files VALUES ('old.md','legacy-project','memory','hash',1,1,1);
            CREATE INDEX idx_memory_files_source ON memory_files(source);
            """
        )
    await ensure_vector_tables(db_path)
    await ensure_vector_tables(db_path)
    async with Storage.transaction(ensure_init=False) as connection:
        columns = {row[0] for row in await connection.fetch_all("SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() AND table_name = 'memory_files'")}
        cache_row = await connection.fetch_one("SELECT text_hash FROM memory_embedding_cache")
        marker_row = await connection.fetch_one("SELECT text FROM session_transcript_fts")
        legacy_tables = await connection.fetch_all("SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema() AND table_name LIKE 'memory_files_legacy_prescope_%'")
        assert len(legacy_tables) == 1
        assert await connection.fetch_one(f'SELECT path FROM "{legacy_tables[0][0]}"') == ('old.md',)
        assert await connection.fetch_one("SELECT tablename FROM pg_indexes WHERE schemaname = current_schema() AND indexname = 'idx_memory_files_source'") == ('memory_files',)

    assert {"scope", "scope_id", "path"}.issubset(columns)
    assert cache_row == ("hash",)
    assert marker_row == ("preserved",)


@pytest.mark.asyncio
async def test_pg_embedding_writers_accept_arrays_and_legacy_json_bytes(tmp_path: Path):
    from flocks.storage.vector import insert_chunks, put_embedding_to_cache, get_embedding_from_cache

    path = tmp_path / "logical-memory"
    await Storage.init(path)
    representations = [[1.0, 0.0], '[1.0, 0.0]', b'[1.0, 0.0]', bytearray(b'[1.0, 0.0]'), memoryview(b'[1.0, 0.0]')]
    for index, value in enumerate(representations):
        filename = f"mixed-{index}.md"
        chunk = _chunk('global', '', filename, 'mixed embedding', [1.0, 0.0])
        chunk['embedding'] = value
        await replace_memory_file_index(path, file_entry=_file_entry('global', '', filename), chunks=[chunk])
    changed = _chunk('global', '', 'mixed-0.md', 'updated by insert_chunks', [0.5, 0.0])
    await insert_chunks(path, [changed])
    await put_embedding_to_cache(path, 'cache-key', 'provider', 'model', [0.5, 1.0], 2)
    assert await get_embedding_from_cache(path, 'cache-key', 'provider', 'model') == ([0.5, 1.0], 2)
    async with Storage.transaction() as db:
        blobs = await db.fetch_all("SELECT embedding FROM memory_chunks ORDER BY id")
        assert all(isinstance(row[0], bytes) and isinstance(json.loads(row[0]), list) for row in blobs)
        assert await db.fetch_one("SELECT pg_typeof(embedding)::text FROM memory_embedding_cache WHERE text_hash = ?", ('cache-key',)) == ('bytea',)
        # One invalid historical vector must not prevent other records from matching.
        await db.execute("UPDATE memory_chunks SET embedding = ? WHERE id = ?", (b'invalid json', 'chunk:global::mixed-4.md'))
    hits = await vector_search(path, 'default', [1.0, 0.0], max_results=20)
    assert len(hits) == 4 and all(hit['score'] == 1.0 for hit in hits)


@pytest.mark.asyncio
async def test_optional_keyword_index_failure_keeps_vector_chunk_transaction(tmp_path: Path):
    from flocks.storage.vector import insert_chunks

    path = tmp_path / 'missing-keyword-index'
    await Storage.init(path)
    chunk = _chunk('global', '', 'preserved.md', 'original text', [1.0, 0.0])
    await replace_memory_file_index(path, file_entry=_file_entry('global', '', 'preserved.md'), chunks=[chunk])
    async with Storage.transaction() as db:
        await db.execute('ALTER TABLE memory_fts RENAME TO memory_fts_retained')
    changed = dict(chunk, text='updated vector text')
    assert await insert_chunks(path, [changed]) == 1
    async with Storage.transaction() as db:
        assert await db.fetch_one('SELECT text FROM memory_chunks WHERE id = ?', (chunk['id'],)) == ('updated vector text',)
        assert await db.fetch_one('SELECT text FROM memory_fts_retained WHERE chunk_id = ?', (chunk['id'],)) == ('original text',)


@pytest.mark.asyncio
async def test_vector_search_batches_rows_and_scores_outside_event_loop(tmp_path: Path, monkeypatch):
    import threading
    from flocks.storage import vector
    from flocks.db.backends.postgres import PostgresTransaction

    path = tmp_path / 'large-memory'
    await Storage.init(path)
    chunks = [dict(_chunk('global', '', 'many.md', f'row {i}', [1.0, i / 600]), id=f'chunk-{i:04d}') for i in range(600)]
    await replace_memory_file_index(path, file_entry=_file_entry('global', '', 'many.md'), chunks=chunks)
    batch_sizes, workers = [], []
    fetch = PostgresTransaction.fetch_all
    score = vector._score_vector_rows
    async def fetch_all(db, sql, params=()):
        rows = await fetch(db, sql, params)
        if 'FROM memory_chunks' in sql:
            batch_sizes.append(len(rows))
        return rows
    def score_rows(*args):
        workers.append(threading.get_ident())
        return score(*args)
    monkeypatch.setattr(PostgresTransaction, 'fetch_all', fetch_all)
    monkeypatch.setattr(vector, '_score_vector_rows', score_rows)
    hits = await vector_search(path, 'default', [1.0, 0.0], max_results=7)
    assert [hit['id'] for hit in hits] == [f'chunk-{i:04d}' for i in range(7)]
    assert batch_sizes == [256, 256, 88]
    assert all(worker != threading.get_ident() for worker in workers)


@pytest.mark.asyncio
async def test_prescope_index_only_is_retained_and_rebuilt_from_scoped_chunks(tmp_path: Path):
    from flocks.storage.vector import _vector_schema

    async with Storage.transaction(ensure_init=False) as db:
        await db.executescript(';\n'.join(_vector_schema(db.dialect)))
        await db.execute("INSERT INTO memory_files VALUES ('global','','current.md','memory','hash',1,1,1)")
        await db.execute("INSERT INTO memory_chunks(id,scope,scope_id,path,source,start_line,end_line,hash,text,created_at,updated_at) VALUES ('current','global','','current.md','memory',1,1,'hash','保留中文告警',1,1)")
        await db.execute("CREATE TABLE memory_fts(text TEXT, chunk_id TEXT, project_id TEXT)")
        await db.execute("INSERT INTO memory_fts VALUES ('legacy index','old','legacy')")
    await ensure_vector_tables(tmp_path / 'logical-memory')
    await ensure_vector_tables(tmp_path / 'logical-memory')
    async with Storage.transaction(ensure_init=False) as db:
        assert await db.fetch_one("SELECT chunk_id, text FROM memory_fts") == ('current', '保留中文告警')
        retained = await db.fetch_all("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema() AND table_name LIKE 'memory_fts_legacy_prescope_%'")
        assert len(retained) == 1
        assert await db.fetch_one(f'SELECT chunk_id FROM "{retained[0][0]}"') == ('old',)
