"""Shared PostgreSQL must not treat another local tree as authoritative."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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


async def test_other_root_with_different_content_keeps_both_versions_without_replacing_the_stored_one(tmp_path):
    """Local trees are caches of the stored notes: a second tree's different
    MEMORY.md does not replace the stored one, and is not lost either -- it is
    kept beside it as a conflict copy, and both are indexed."""
    await Storage.init()
    first = tree(tmp_path, "first", "firstword")
    second = tree(tmp_path, "second", "otherword")
    await sync(first)
    await sync(second)
    assert (second / "memory" / "MEMORY.md").read_text() == "firstword"
    copies = list((second / "memory").glob("MEMORY.conflict-*.md"))
    assert [copy.read_text() for copy in copies] == ["otherword"]
    assert await counts() == (2, 2, 2)
    assert await fts_search(Storage.get_db_path(), "prj_test", "firstword")
    assert await fts_search(Storage.get_db_path(), "prj_test", "otherword")


async def test_a_removal_in_one_tree_removes_the_note_everywhere(tmp_path):
    """Both trees are copies of the one shared tree: removing the note in one of
    them removes it from the index at once, and from the other copy when that
    one syncs -- moved aside there, not erased."""
    await Storage.init()
    first = tree(tmp_path, "first", "sharedword")
    second = tree(tmp_path, "second", "sharedword")
    await sync(first)
    await sync(second)
    path = first / "memory" / "MEMORY.md"
    path.rename(path.with_suffix(".removed"))
    await sync(first)
    assert await counts() == (0, 0, 0)
    assert not await fts_search(Storage.get_db_path(), "prj_test", "sharedword")
    await sync(second)
    assert not (second / "memory" / "MEMORY.md").exists()
    assert [aside.read_text() for aside in (second / "memory").glob("MEMORY.md.removed-*")] == ["sharedword"]
    assert await counts() == (0, 0, 0)


async def test_legacy_rows_are_adopted_by_the_shared_tree(tmp_path):
    """Rows indexed before ownership existed belong to the shared tree like any
    other: kept while the note exists, removed with it."""
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "legacy", "legacyword survives")
    await sync(data)
    assert await counts() == (1, 1, 1)
    async with Storage.transaction() as db:
        assert await db.fetch_one("SELECT COUNT(*) FROM memory_index_owners") == (1,)
    path = data / "memory" / "MEMORY.md"
    path.rename(path.with_suffix(".removed"))
    await sync(data)
    assert await counts() == (0, 0, 0)


async def test_a_legacy_row_whose_note_exists_nowhere_is_cleaned(tmp_path):
    """No copy of the shared tree, and nothing in the store, holds the note."""
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "legacy")
    await sync(data)
    assert await counts() == (0, 0, 0)


async def test_the_shared_tree_replaces_different_legacy_content(tmp_path):
    """The settled tree is the source: its note replaces an older index of it."""
    await Storage.init()
    await legacy()
    data = tree(tmp_path, "legacy", "differentword")
    await sync(data)
    assert await fts_search(Storage.get_db_path(), "prj_test", "differentword")
    assert not await fts_search(Storage.get_db_path(), "prj_test", "legacyword")
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


async def test_cleanup_batches_more_than_128_files_and_deletions_reach_the_other_root(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "batch")
    for index in range(131):
        (data / "memory" / f"note-{index}.md").write_text("batchword")
    await sync(data)
    peer = tree(tmp_path, "peer", "peerword", "peer.md")
    await sync(peer)
    assert len(list((peer / "memory").glob("note-*.md"))) == 131, "the peer has the stored notes"
    for path in (data / "memory").glob("*.md"):
        path.rename(path.with_suffix(".removed"))
    await sync(data)
    # One tree removed them: gone from the index at once, in batches.
    assert await counts() == (1, 1, 1)
    await sync(peer)
    assert not list((peer / "memory").glob("note-*.md"))
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "peerword")
    assert (data / "memory" / "peer.md").read_text() == "peerword"


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


async def test_a_process_starting_while_another_writes_the_memory_index_waits_for_no_table(tmp_path):
    await Storage.init()
    data = tree(tmp_path, "starter", "starterword", "starter.md")
    script = """
import asyncio, os
from pathlib import Path
from flocks.memory.sync.indexer import MemoryIndexer
from flocks.memory.config import MemoryConfig
from flocks.storage import Storage
async def main():
    await Storage.init()
    await MemoryIndexer('prj_test',Path(os.environ['HOME']),None,'unused',MemoryConfig()).sync()
    await Storage.shutdown()
asyncio.run(main())
"""
    parts = urlsplit(os.environ["FLOCKS_DB_DSN"])
    # Waiting for a lock fails the start at once instead of hanging it.
    dsn = urlunsplit(parts._replace(query=urlencode({**dict(parse_qsl(parts.query)), "lock_timeout": "5000"})))
    env = {key: value for key, value in os.environ.items() if not key.startswith("FLOCKS_")}
    env.update(HOME=str(data), USERPROFILE=str(data), FLOCKS_ROOT=str(data / "runtime"),
               FLOCKS_DATA_DIR=str(data), FLOCKS_DB_DSN=dsn,
               PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[2]))
    async with Storage.transaction() as writer:
        # Mid-replacement: chunks deleted, the file row not written yet. The
        # writer also holds the lock every other writer holds on each table.
        await writer.execute("DELETE FROM memory_chunks WHERE path = ?", ("writer.md",))
        for (table,) in await writer.fetch_all("SELECT tablename FROM pg_tables WHERE schemaname = current_schema()"):
            await writer.execute(f'LOCK TABLE "{table}" IN ROW EXCLUSIVE MODE')
        started = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", script],
                                          cwd=data, env=env, capture_output=True, text=True, timeout=120)
    assert started.returncode == 0, started.stderr + started.stdout
    assert await fts_search(Storage.get_db_path(), "prj_test", "starterword")


async def test_a_memory_index_write_chosen_as_deadlock_victim_runs_once_more():
    import asyncpg

    await Storage.init()
    attempts = []

    async def before_replace(db, entry):
        attempts.append(entry["path"])

    other = await asyncpg.connect(os.environ["FLOCKS_DB_DSN"], timeout=10)
    try:
        locks = other.transaction()
        await locks.start()
        await other.execute("LOCK TABLE memory_files IN SHARE MODE")
        writing = asyncio.create_task(replace_memory_file_index(Storage.get_db_path(), file_entry={
            "scope": "global", "scope_id": "", "path": "MEMORY.md",
            "hash": compute_text_hash("deadlockword"), "mtime": 1, "size": 12,
        }, chunks=[{
            "id": "deadlock-chunk", "scope": "global", "scope_id": "", "path": "MEMORY.md",
            "source": "memory", "start_line": 1, "end_line": 1, "hash": "chunk-hash", "text": "deadlockword",
        }], before_replace=before_replace))
        # The write has deleted its chunks and now waits for memory_files.
        for _ in range(500):
            if await other.fetchval(
                "SELECT count(*) FROM pg_locks WHERE NOT granted AND relation = 'memory_files'::regclass"
            ):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("the memory index write never waited for memory_files")
        # Closing the cycle: PostgreSQL rolls back the write, which waited first.
        await other.execute("LOCK TABLE memory_chunks IN SHARE MODE")
        await locks.commit()
        assert await asyncio.wait_for(writing, 30) == 1
    finally:
        await other.close()

    assert attempts == ["MEMORY.md", "MEMORY.md"]
    assert await counts() == (1, 1, 1)
    assert await fts_search(Storage.get_db_path(), "prj_test", "deadlockword")


async def test_a_memory_index_write_failing_otherwise_is_not_repeated():
    await Storage.init()
    attempts = []

    async def before_replace(db, entry):
        attempts.append(entry["path"])
        raise MemoryOwnershipConflict("owned elsewhere")

    with pytest.raises(MemoryOwnershipConflict):
        await legacy_with(before_replace)
    assert len(attempts) == 1 and await counts() == (0, 0, 0)


async def legacy_with(before_replace, content="legacyword survives"):
    await replace_memory_file_index(Storage.get_db_path(), file_entry={
        "scope": "global", "scope_id": "", "path": "MEMORY.md",
        "hash": compute_text_hash(content), "mtime": 1, "size": len(content),
    }, chunks=[{
        "id": "legacy-chunk", "scope": "global", "scope_id": "", "path": "MEMORY.md",
        "source": "memory", "start_line": 1, "end_line": 1, "hash": "legacy-chunk-hash", "text": content,
    }], before_replace=before_replace)


async def test_an_edit_in_one_tree_is_indexed_without_waiting_for_the_other_copies(tmp_path):
    """Under the old per-tree owners both trees indexing v1 blocked either from
    indexing v2. One shared owner: the edited tree indexes it at once."""
    await Storage.init()
    first = tree(tmp_path, "first", "oldword")
    second = tree(tmp_path, "second", "oldword")
    await sync(first)
    await sync(second)

    (first / "memory" / "MEMORY.md").write_text("newword")
    await sync(first)

    assert await fts_search(Storage.get_db_path(), "prj_test", "newword")
    assert not await fts_search(Storage.get_db_path(), "prj_test", "oldword")
    await sync(second)
    assert (second / "memory" / "MEMORY.md").read_text() == "newword"
    assert await counts() == (1, 1, 1)


async def test_only_one_instance_indexes_the_shared_tree_at_a_time(tmp_path, monkeypatch):
    await Storage.init()
    data = tree(tmp_path, "busy", "busyword")
    from flocks.db.singleton import cluster_lock
    import flocks.memory.sync.indexer as indexer_module

    monkeypatch.setattr(indexer_module, "_INDEX_LOCK_WAIT_S", 0.2)
    async with cluster_lock("memory.index") as held:
        assert held
        _indexer, stats = await sync(data)
    assert stats.get("skipped") == "another_instance_indexing"
    assert await counts() == (0, 0, 0)
    _indexer, stats = await sync(data)
    assert "skipped" not in stats and await counts() == (1, 1, 1)


async def test_a_run_that_waited_for_another_indexes_what_that_one_left(tmp_path, monkeypatch):
    """Review round 2 (batch 4): settled after the lock, not before it."""
    from contextlib import asynccontextmanager

    import flocks.db.singleton as singleton_module
    import flocks.memory.sync.mirror as mirror_module

    await Storage.init()
    data = tree(tmp_path, "waiting", "seenword")
    order: list[str] = []
    real_lock = singleton_module.cluster_lock

    @asynccontextmanager
    async def _recorded_lock(name, **kwargs):
        async with real_lock(name, **kwargs) as held:
            order.append("lock")
            yield held

    async def _recorded_settle(root):
        order.append("settle")
        return mirror_module.ReconcileStats()

    async def _recorded_quiet(root):
        order.append("settle")
        return True

    monkeypatch.setattr(singleton_module, "cluster_lock", _recorded_lock)
    monkeypatch.setattr(mirror_module, "reconcile_all", _recorded_settle, raising=False)
    monkeypatch.setattr(mirror_module, "reconcile_quietly", _recorded_quiet)

    await sync(data)

    assert order == ["lock", "settle"]


async def test_an_unreachable_store_leaves_the_shared_index_alone(tmp_path, monkeypatch):
    """Review round 3 (batch 4): a tree that could not be settled is not published."""
    import flocks.memory.sync.mirror as mirror_module

    await Storage.init()
    data = tree(tmp_path, "published", "firstword")
    await sync(data)
    assert (await counts())[0] == 1
    (data / "memory" / "MEMORY.md").write_text("staleword only here")

    async def _unreachable(root):
        return None

    monkeypatch.setattr(mirror_module, "reconcile_all", _unreachable, raising=False)
    _indexer, stats = await sync(data)

    assert stats["skipped"] == "memory_store_unreachable"
    async with Storage.transaction() as db:
        hits = await db.fetch_one("SELECT COUNT(*) FROM memory_chunks WHERE text LIKE '%staleword%'")
    assert hits[0] == 0, "the shared index still has the settled tree"


async def test_a_note_the_settling_could_not_align_keeps_its_shared_index_rows(tmp_path, monkeypatch):
    """Review round 3 (batch 4): this copy of a deferred note is not the shared one."""
    import flocks.memory.sync.mirror as mirror_module

    await Storage.init()
    data = tree(tmp_path, "aligned", "sharedversion")
    await sync(data)
    (data / "memory" / "MEMORY.md").write_text("localversion changed in the store meanwhile")

    async def _deferred(root):
        stats = mirror_module.ReconcileStats()
        stats.deferred.append("MEMORY.md")
        return stats

    monkeypatch.setattr(mirror_module, "reconcile_all", _deferred, raising=False)
    _indexer, stats = await sync(data)

    assert stats["files_unsettled"] == 1
    async with Storage.transaction() as db:
        local = await db.fetch_one("SELECT COUNT(*) FROM memory_chunks WHERE text LIKE '%localversion%'")
        shared = await db.fetch_one("SELECT COUNT(*) FROM memory_chunks WHERE text LIKE '%sharedversion%'")
    assert (local[0], shared[0]) == (0, 1)


async def test_notes_left_unsettled_keep_their_index_rows(tmp_path, monkeypatch):
    """Review round 2 (batch 4): a note behind a link here may be a real note elsewhere."""
    import flocks.memory.sync.mirror as mirror_module

    await Storage.init()
    data = tree(tmp_path, "linked", "keptword", filename="kept.md")
    await sync(data)
    assert (await counts())[0] == 1
    # The note is now behind a link on this instance (or could not be settled).
    (data / "memory" / "kept.md").unlink()

    async def _settled(root):
        stats = mirror_module.ReconcileStats()
        stats.linked.append("kept.md")
        return stats

    monkeypatch.setattr(mirror_module, "reconcile_all", _settled, raising=False)
    await sync(data)

    assert (await counts())[0] == 1, "the row of a note left unsettled is not cleaned"
