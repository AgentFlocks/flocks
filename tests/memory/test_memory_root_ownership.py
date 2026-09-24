"""Shared PostgreSQL must not treat another local tree as authoritative."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

import pytest

from flocks.memory.config import MemoryConfig
from flocks.memory.manager import MemoryManager
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.memory.sync.ownership import MemoryIndexOwnership, MemoryOwnershipConflict
from flocks.memory.utils.hash import compute_text_hash
from flocks.storage import Storage, fts_search, replace_memory_file_index

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.getenv("FLOCKS_TEST_PG_DSN"), reason="requires isolated PostgreSQL")]


def tree(tmp_path, name, content=None, filename="MEMORY.md"):
    data = tmp_path / name
    root = data / "memory"
    root.mkdir(parents=True)
    if content is not None:
        (root / filename).write_text(content)
    return data


async def sync(data):
    indexer = MemoryIndexer("prj_test", data, None, "unused", MemoryConfig())
    with patch("flocks.config.Config.get_data_path", return_value=data):
        stats = await indexer.sync()
    return indexer, stats


async def counts():
    async with Storage.transaction() as db:
        return await db.fetch_one("SELECT (SELECT COUNT(*) FROM memory_files),"
                                  "(SELECT COUNT(*) FROM memory_chunks),(SELECT COUNT(*) FROM memory_fts)")


async def legacy(content="legacyword survives"):
    await replace_memory_file_index(Storage.get_db_path(), file_entry={
        "scope": "global", "scope_id": "", "path": "MEMORY.md",
        "hash": compute_text_hash(content), "mtime": 1, "size": len(content),
    }, chunks=[{
        "id": "legacy-chunk", "scope": "global", "scope_id": "", "path": "MEMORY.md",
        "source": "memory", "start_line": 1, "end_line": 1, "hash": "legacy-chunk-hash", "text": content,
    }])


async def test_empty_other_tree_and_public_search_preserve_shared_index_and_source(tmp_path):
    await Storage.init()
    first = tree(tmp_path, "first", "sharedword preserved")
    second = tree(tmp_path, "second")
    await sync(first)
    original = (first / "memory" / "MEMORY.md").read_bytes()
    await sync(second)
    with patch("flocks.config.Config.get_data_path", return_value=second):
        manager = MemoryManager("prj_test", str(second), MemoryConfig(sync={"sessions": {"enabled": False}}))
        hits = await manager.search("sharedword", min_score=0)
    assert hits and await counts() == (1, 1, 1)
    assert (first / "memory" / "MEMORY.md").read_bytes() == original


async def test_same_root_edits_and_deleted_file_reconcile_normally(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "same", "firstword")
    await sync(data)
    path = data / "memory" / "MEMORY.md"
    path.write_text("changedword")
    await sync(data)
    assert await fts_search(Storage.get_db_path(), "prj_test", "changedword")
    path.rename(path.with_suffix(".removed"))
    await sync(data)
    assert await counts() == (0, 0, 0)


async def test_other_root_conflict_does_not_replace_metadata_chunks_or_fts(tmp_path):
    await Storage.init()
    first = tree(tmp_path, "first", "firstword")
    second = tree(tmp_path, "second", "otherword")
    await sync(first)
    with pytest.raises(MemoryOwnershipConflict, match="different content"):
        await sync(second)
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "firstword")
    assert not await fts_search(Storage.get_db_path(), "prj_test", "otherword")
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_index_owners") == (1,)


async def test_identical_content_can_have_two_owners_and_each_removal_is_local(tmp_path):
    await Storage.init()
    first = tree(tmp_path, "first", "sharedword")
    second = tree(tmp_path, "second", "sharedword")
    await sync(first)
    await sync(second)
    path = first / "memory" / "MEMORY.md"
    path.rename(path.with_suffix(".removed"))
    await sync(first)
    assert await counts() == (1, 1, 1)
    assert (second / "memory" / "MEMORY.md").read_text() == "sharedword"
    path = second / "memory" / "MEMORY.md"
    path.rename(path.with_suffix(".removed"))
    await sync(second)
    assert await counts() == (0, 0, 0)


@pytest.mark.parametrize("local_content", [None, "legacyword survives"])
async def test_unowned_legacy_rows_survive_and_matching_source_can_be_adopted(tmp_path, local_content):
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "legacy", local_content)
    await sync(data)
    assert await counts() == (1, 1, 1)
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_index_owners") == (int(local_content is not None),)
    if local_content is not None:
        path = data / "memory" / "MEMORY.md"
        path.rename(path.with_suffix(".removed"))
        await sync(data)
        assert await counts() == (0, 0, 0)


async def test_different_legacy_content_requires_reconciliation_without_overwrite(tmp_path):
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "legacy", "differentword")
    with pytest.raises(MemoryOwnershipConflict, match="legacy"):
        await sync(data)
    assert await fts_search(Storage.get_db_path(), "prj_test", "legacyword")
    assert await counts() == (1, 1, 1)


async def test_missing_root_or_marker_after_scan_cannot_remove_owned_rows(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "source", "preservedword")
    indexer, _ = await sync(data)
    (data / "memory").rename(data / "memory-offline")
    assert await indexer._clean_deleted_files([]) == 0
    await sync(data)
    assert await counts() == (1, 1, 1)
    assert (data / "memory-offline" / "MEMORY.md").read_text() == "preservedword"


async def test_superseded_scan_cannot_clean_a_newer_generation(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "same", "preservedword")
    old_indexer, _ = await sync(data)
    await sync(data)
    with pytest.raises(MemoryOwnershipConflict, match="superseded"):
        await old_indexer._clean_deleted_files([])
    assert await counts() == (1, 1, 1)


async def test_external_legacy_writer_hash_change_is_not_deleted_by_old_owner(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "same", "oldword")
    await sync(data)
    await legacy("externallychangedword")  # The legacy API remains usable without ownership callbacks.
    path = data / "memory" / "MEMORY.md"
    path.rename(path.with_suffix(".removed"))
    await sync(data)
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "externallychangedword")


async def test_failed_replacement_rolls_back_new_owner_with_old_index_intact(tmp_path):
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "same", "legacyword survives")
    owner = await MemoryIndexOwnership.begin(data / "memory")
    with pytest.raises(Exception):
        await replace_memory_file_index(Storage.get_db_path(), file_entry={
            "scope": "global", "scope_id": "", "path": "MEMORY.md",
            "hash": compute_text_hash("legacyword survives"), "mtime": 2, "size": 19,
        }, chunks=[{
            "id": None, "scope": "global", "scope_id": "", "path": "MEMORY.md",
            "source": "memory", "start_line": 1, "end_line": 1, "hash": "h", "text": "invalid chunk",
        }], before_replace=owner.claim)
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "legacyword")
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_index_owners") == (0,)


async def test_cleanup_batches_more_than_128_files_without_touching_other_root(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "batch")
    for index in range(131):
        (data / "memory" / f"note-{index}.md").write_text("batchword")
    await sync(data)
    peer = tree(tmp_path, "peer", "peerword", "peer.md")
    await sync(peer)
    for path in (data / "memory").glob("*.md"):
        path.rename(path.with_suffix(".removed"))
    await sync(data)
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "peerword")


async def test_root_appearing_during_scan_cannot_create_an_unowned_index(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "new", "newword")
    with patch.object(MemoryIndexOwnership, "begin", return_value=None):
        with pytest.raises(MemoryOwnershipConflict, match="appeared"):
            await sync(data)
    assert await counts() == (0, 0, 0)


async def test_two_processes_with_distinct_trees_keep_both_files(tmp_path):
    await Storage.init()
    script = """
import asyncio, json, os
from pathlib import Path
from flocks.config import Config
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.memory.config import MemoryConfig
from flocks.storage import Storage
async def main():
    assert Config.get_data_path().is_relative_to(Path(os.environ['HOME']))
    await Storage.init()
    indexer = MemoryIndexer('prj_test',Path(os.environ['HOME']),None,'unused',MemoryConfig())
    await indexer.sync()
    async with Storage.transaction() as db:
        schema = (await db.fetch_one('SELECT current_schema()'))[0]
    print(json.dumps({'root':str(Config.get_data_path()),'schema':schema}))
    await Storage.shutdown()
asyncio.run(main())
"""
    peers = []
    for name in ("alpha", "beta"):
        data = tree(tmp_path, name, name + "word", name + ".md")
        env = {key: value for key, value in os.environ.items() if not key.startswith("FLOCKS_")}
        env.update(HOME=str(data), USERPROFILE=str(data), FLOCKS_ROOT=str(data / "runtime"),
                   FLOCKS_DATA_DIR=str(data), FLOCKS_DB_DSN=os.environ["FLOCKS_DB_DSN"],
                   PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
                   PYTHONPATH=str(Path(__file__).resolve().parents[2]))
        peers.append(asyncio.to_thread(subprocess.run, [sys.executable, "-c", script],
                                      cwd=data, env=env, capture_output=True, text=True, timeout=45))
    completed = await asyncio.gather(*peers)
    for result in completed:
        assert result.returncode == 0, result.stderr + result.stdout
        proof = json.loads(result.stdout.strip().splitlines()[-1])
        assert proof["schema"].startswith("test_")
        assert Path(proof["root"]).is_relative_to(tmp_path)
    assert await counts() == (2, 2, 2)
    assert await fts_search(Storage.get_db_path(), "prj_test", "alphaword")
    assert await fts_search(Storage.get_db_path(), "prj_test", "betaword")
