"""Tests for Global and Project Memory scope isolation."""

import os
from pathlib import Path
import sqlite3
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
    connection = sqlite3.connect(db_path)
    connection.executescript(
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
            embedding BLOB,
            embedding_model TEXT,
            embedding_dims INTEGER,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE VIRTUAL TABLE memory_fts USING fts5(
            text, chunk_id, path, source, project_id, start_line, end_line
        );
        CREATE TABLE memory_embedding_cache (
            text_hash TEXT NOT NULL,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dims INTEGER NOT NULL,
            created_at REAL NOT NULL,
            accessed_at REAL NOT NULL,
            PRIMARY KEY (text_hash, provider, model)
        );
        INSERT INTO memory_embedding_cache
        VALUES ('hash', 'provider', 'model', '[1.0]', 1, 1, 1);
        CREATE VIRTUAL TABLE session_transcript_fts USING fts5(text);
        INSERT INTO session_transcript_fts VALUES ('preserved');
        """
    )
    connection.commit()
    connection.close()

    await ensure_vector_tables(db_path)

    connection = sqlite3.connect(db_path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_files)")}
    cache_row = connection.execute("SELECT text_hash FROM memory_embedding_cache").fetchone()
    marker_row = connection.execute(
        "SELECT text FROM session_transcript_fts"
    ).fetchone()
    connection.close()

    assert {"scope", "scope_id", "path"}.issubset(columns)
    assert cache_row == ("hash",)
    assert marker_row == ("preserved",)
