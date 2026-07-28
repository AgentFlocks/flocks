"""Tests for Global and Project Memory scope isolation."""

from pathlib import Path
import sqlite3
from unittest.mock import patch

import pytest

from flocks.memory.config import MemoryConfig
from flocks.memory.learning import LearningCheckpointStore
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.memory.types import MemoryScope
from flocks.storage import (
    Storage,
    ensure_vector_tables,
    fts_search,
    replace_memory_file_index,
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
        "embedding": None,
        "embedding_model": None,
        "embedding_dims": None,
    }


@pytest.mark.asyncio
async def test_memory_search_is_global_across_scopes(tmp_path: Path) -> None:
    db_path = tmp_path / "scope.db"
    await Storage.init(db_path)
    records = [
        ("global", "", "MEMORY.md", "scopeword global"),
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
            chunks=[_chunk(scope, scope_id, path, text)],
        )

    alpha = await fts_search(db_path, "prj_alpha", "scopeword")
    default = await fts_search(db_path, "default", "scopeword")

    expected_paths = {
        "MEMORY.md",
        "projects/prj_alpha/MEMORY.md",
        "projects/prj_beta/MEMORY.md",
    }
    assert {result["path"] for result in alpha} == expected_paths
    assert {result["path"] for result in default} == expected_paths


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


@pytest.mark.asyncio
async def test_old_dream_checkpoint_is_copied_to_global_scope(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "legacy-checkpoint.db"
    await Storage.init(db_path)
    async with Storage.connect(db_path) as db:
        await db.execute(
            """
            CREATE TABLE memory_learning_checkpoints (
                pipeline TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_key TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                line_count INTEGER NOT NULL DEFAULT 0,
                last_message_id TEXT,
                source_mtime REAL,
                processed_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (pipeline, source_type, source_key)
            )
            """
        )
        await db.execute(
            """
            INSERT INTO memory_learning_checkpoints
            VALUES (
                'dream', 'session', 'ses_old', 'hash', 1, 'msg_old',
                NULL, 'now', 'now'
            )
            """
        )
        await db.commit()

    await LearningCheckpointStore.ensure_schema()

    global_row = await LearningCheckpointStore.get(
        "dream",
        "session",
        "ses_old",
    )
    project_row = await LearningCheckpointStore.get(
        "dream",
        "session",
        "ses_old",
        scope=MemoryScope.PROJECT,
        scope_id="prj_test",
    )
    assert global_row["last_message_id"] == "msg_old"
    assert project_row is None
