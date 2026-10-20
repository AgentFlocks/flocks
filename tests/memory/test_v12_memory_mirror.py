"""Memory notes kept in PostgreSQL: what one instance writes, deletes or
appends reaches the others, and concurrent edits are never silently lost."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from flocks.memory.sync.mirror import manifest_path, reconcile
from flocks.storage.file_mirror import FileMirror

requires_pg = pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="FLOCKS_TEST_PG_DSN is not set")
pytestmark = requires_pg


@pytest.fixture
def roots(tmp_path) -> tuple[Path, Path]:
    return tmp_path / "a" / "memory", tmp_path / "b" / "memory"


def _write(root: Path, name: str, text: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _notes(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.glob("**/*.md"))
    }


async def test_a_note_written_on_one_instance_reaches_another(roots):
    a, b = roots
    _write(a, "MEMORY.md", "prefers PostgreSQL")
    _write(a, "users/u1/2026-10-14.md", "met the auditors")

    assert sorted((await reconcile(a)).pushed) == ["MEMORY.md", "users/u1/2026-10-14.md"]
    assert sorted((await reconcile(b)).pulled) == ["MEMORY.md", "users/u1/2026-10-14.md"]

    assert _notes(b) == _notes(a)
    # What b last synced is kept beside the notes, never among them.
    assert manifest_path(b).exists() and manifest_path(b).parent == b.parent
    assert not [path for path in b.rglob("*") if path.name.startswith(".")]


async def test_changes_and_deletions_follow_the_note(roots):
    a, b = roots
    _write(a, "MEMORY.md", "v1")
    _write(a, "old.md", "obsolete")
    await reconcile(a)
    await reconcile(b)

    _write(a, "MEMORY.md", "v2")
    (a / "old.md").unlink()
    stats = await reconcile(a)
    assert stats.pushed == ["MEMORY.md"] and stats.deleted_remote == ["old.md"]

    stats = await reconcile(b)
    assert stats.pulled == ["MEMORY.md"] and stats.moved_aside == ["old.md"]
    assert _notes(b) == {"MEMORY.md": "v2"}
    # Deleted elsewhere is moved aside here, not erased.
    assert [path.name.split(".removed-")[0] for path in b.glob("old.md.removed-*")] == ["old.md"]


async def test_an_append_on_one_side_wins_over_the_shorter_copy(roots):
    a, b = roots
    _write(a, "2026-10-14.md", "morning")
    await reconcile(a)
    await reconcile(b)

    # Both changed it; b's version is a's plus more.
    _write(a, "2026-10-14.md", "morning\nnoon")
    _write(b, "2026-10-14.md", "morning\nnoon\nevening")
    await reconcile(a)
    stats = await reconcile(b)

    assert stats.pushed == ["2026-10-14.md"] and stats.conflicts == []
    await reconcile(a)
    assert _notes(a)["2026-10-14.md"] == "morning\nnoon\nevening"


async def test_diverging_edits_keep_both_versions(roots):
    a, b = roots
    _write(a, "MEMORY.md", "base")
    await reconcile(a)
    await reconcile(b)

    _write(a, "MEMORY.md", "edited on a")
    _write(b, "MEMORY.md", "edited on b")
    await reconcile(a)
    stats = await reconcile(b)

    assert stats.conflicts == ["MEMORY.md"]
    assert (b / "MEMORY.md").read_text() == "edited on a"
    copies = list(b.glob("MEMORY.conflict-*.md"))
    assert [copy.read_text() for copy in copies] == ["edited on b"]
    # The kept copy is a note like any other: it reaches the other instance.
    await reconcile(b)
    await reconcile(a)
    assert any(name.startswith("MEMORY.conflict-") for name in _notes(a))


async def test_a_stale_writer_cannot_overwrite_a_newer_stored_note(roots):
    a, b = roots
    _write(a, "MEMORY.md", "base")
    await reconcile(a)
    await reconcile(b)
    _write(b, "MEMORY.md", "newer, from b")
    await reconcile(b)

    # a pushes its edit against the stored copy it last saw: refused.
    mirror = FileMirror("memory", a)
    assert await mirror.put_bytes("MEMORY.md", b"stale edit from a", expected=mirror_sha(b"base")) is False
    assert await mirror.fetch("MEMORY.md") == b"newer, from b"


def mirror_sha(data: bytes) -> str:
    from flocks.storage.file_mirror import sha256_of

    return sha256_of(data)


async def test_reconciling_one_note_leaves_the_others_alone(roots):
    a, b = roots
    _write(a, "MEMORY.md", "one")
    _write(a, "other.md", "two")

    stats = await reconcile(a, only={"MEMORY.md"})

    assert stats.pushed == ["MEMORY.md"]
    assert list((await FileMirror("memory", a).listing(""))) == ["MEMORY.md"]
    assert (await reconcile(a)).pushed == ["other.md"]


async def test_a_linked_note_is_neither_replaced_nor_taken_as_deleted(roots, tmp_path):
    a, b = roots
    _write(a, "MEMORY.md", "stored")
    await reconcile(a)
    await reconcile(b)

    # b's MEMORY.md becomes a link to a file of its owner's choosing.
    elsewhere = tmp_path / "elsewhere.md"
    elsewhere.write_text("kept outside", encoding="utf-8")
    (b / "MEMORY.md").unlink()
    (b / "MEMORY.md").symlink_to(elsewhere)
    stats = await reconcile(b)

    assert stats.linked == ["MEMORY.md"] and not stats.deferred
    assert not stats.deleted_remote and not stats.pulled
    assert (b / "MEMORY.md").is_symlink() and elsewhere.read_text() == "kept outside"
    assert await FileMirror("memory", a).fetch("MEMORY.md") == b"stored"


async def test_reconciliations_of_one_root_do_not_interleave(roots):
    import asyncio

    a, _b = roots
    for index in range(20):
        _write(a, f"note-{index}.md", f"text {index}")

    full, single = await asyncio.gather(reconcile(a), reconcile(a, only={"note-3.md"}))

    pushed = set(full.pushed) | set(single.pushed)
    assert pushed == {f"note-{index}.md" for index in range(20)}
    assert not full.deferred and not single.deferred


# ---------------------------------------------------------------------------
# Review round 1 regressions
# ---------------------------------------------------------------------------

async def test_a_replaced_memory_root_never_deletes_stored_notes(roots):
    a, _b = roots
    _write(a, "MEMORY.md", "keep me")
    _write(a, "daily/day.md", "and me")
    await reconcile(a)

    # The root is renamed away; the record of the last sync is still there.
    a.rename(a.with_name("memory-moved"))
    stats = await reconcile(a)

    assert stats.fresh and not stats.deleted_remote
    assert sorted(await FileMirror("memory", a).listing("")) == ["MEMORY.md", "daily/day.md"]
    assert _notes(a) == {"MEMORY.md": "keep me", "daily/day.md": "and me"}


async def test_a_linked_directory_is_neither_written_through_nor_taken_as_deleted(roots, tmp_path):
    a, b = roots
    _write(a, "daily/day.md", "stored day")
    await reconcile(a)
    await reconcile(b)

    outside = tmp_path / "outside"
    outside.mkdir()
    import shutil

    shutil.move(str(b / "daily" / "day.md"), str(outside / "day-moved.md"))
    (b / "daily").rmdir()
    (b / "daily").symlink_to(outside, target_is_directory=True)
    stats = await reconcile(b)

    assert "daily/day.md" in stats.linked and not stats.deferred and not stats.deleted_remote
    assert await FileMirror("memory", a).fetch("daily/day.md") == b"stored day"
    assert not (outside / "day.md").exists(), "nothing is written through the link"


async def test_conflicting_copies_never_overwrite_each_other(roots):
    a, b = roots
    _write(a, "MEMORY.md", "base")
    await reconcile(a)
    await reconcile(b)
    for round_ in range(2):
        _write(a, "MEMORY.md", f"a{round_}")
        await reconcile(a)
        _write(b, "MEMORY.md", f"b{round_}")
        await reconcile(b)

    copies = sorted(copy.read_text() for copy in b.glob("MEMORY.conflict-*.md"))
    assert copies == ["b0", "b1"], "both of b's versions are kept, under different names"


async def test_a_lost_record_does_not_bring_back_a_note_removed_elsewhere(roots):
    a, b = roots
    _write(a, "old.md", "obsolete")
    await reconcile(a)
    await reconcile(b)
    (a / "old.md").unlink()
    await reconcile(a)

    manifest_path(b).unlink()  # b lost its record of the last sync
    stats = await reconcile(b)

    assert stats.moved_aside == ["old.md"]
    assert await FileMirror("memory", a).fetch("old.md") is None


async def test_a_note_written_again_after_its_removal_is_kept(roots):
    import time

    a, b = roots
    _write(a, "topic.md", "first take")
    await reconcile(a)
    (a / "topic.md").unlink()
    await reconcile(a)

    time.sleep(1.1)
    _write(b, "topic.md", "written again later")
    stats = await reconcile(b)

    assert stats.pushed == ["topic.md"]
    assert await FileMirror("memory", a).fetch("topic.md") == b"written again later"


# ---------------------------------------------------------------------------
# Every way into the notes works on the shared copy
# ---------------------------------------------------------------------------

@pytest.fixture
def on_instance_b(tmp_path, monkeypatch):
    from flocks.config.config import Config

    monkeypatch.setattr(Config, "get_data_path", classmethod(lambda cls: tmp_path / "b"))
    return tmp_path / "b" / "memory"


async def test_a_note_edited_with_a_file_tool_reaches_the_store_with_the_next_memory_sync(roots, on_instance_b):
    """File tools work on this instance's copy; the memory sync (search, session start) shares it."""
    from flocks.memory.sync.mirror import reconcile_all

    a, b = roots
    _write(a, "MEMORY.md", "written on a")
    await reconcile(a)
    await reconcile(b)

    (b / "MEMORY.md").write_text("written on a\nedited on b with a file tool", encoding="utf-8")
    assert await FileMirror("memory", a).fetch("MEMORY.md") == b"written on a", "a file tool alone shares nothing"

    stats = await reconcile_all(b)

    assert stats is not None and stats.pushed == ["MEMORY.md"]
    assert await FileMirror("memory", a).fetch("MEMORY.md") == b"written on a\nedited on b with a file tool"


async def test_session_start_loads_the_shared_note_instead_of_creating_a_template(roots, on_instance_b):
    from flocks.memory.bootstrap import MemoryBootstrap

    a, b = roots
    _write(a, "MEMORY.md", "# Notes from a\n\nremember the auditors")
    await reconcile(a)

    result = await MemoryBootstrap().bootstrap(load_main=True)

    assert "remember the auditors" in (result["main_memory"] or {}).get("content", "")
    assert (b / "MEMORY.md").read_text(encoding="utf-8") == "# Notes from a\n\nremember the auditors"


async def test_a_daily_note_written_on_one_instance_is_appended_to_on_another(roots, on_instance_b):
    from flocks.memory.daily import DailyMemory

    a, b = roots
    _write(a, "daily/2026-10-14.md", "morning on a")
    await reconcile(a)

    await DailyMemory().write_daily("afternoon on b", date="2026-10-14", append=True)

    stored = await FileMirror("memory", a).fetch("daily/2026-10-14.md")
    assert stored is not None and stored.decode().startswith("morning on a") and "afternoon on b" in stored.decode()



# ---------------------------------------------------------------------------
# Review round 2 (batch 4): settled after the gates, on the final arguments;
# a note the store did not take is never reported as shared
# ---------------------------------------------------------------------------

async def test_a_refused_tool_call_changes_nothing_in_the_store(roots, on_instance_b):
    from flocks.tool.registry import ToolContext, ToolRegistry

    a, b = roots
    _write(a, "MEMORY.md", "stored note")
    await reconcile(a)
    await reconcile(b)
    # b no longer has the note: a sync would take that for a deletion.
    (b / "MEMORY.md").unlink()

    ctx = ToolContext(session_id="ses_x", message_id="msg_x",
                      extra={"enforce_callable_tools": True, "turn_callable_tool_names": ["read"]})
    result = await ToolRegistry.execute("edit", ctx, filePath=str(b / "MEMORY.md"), oldString="a", newString="b")

    assert not result.success and "not callable" in (result.error or "")
    assert await FileMirror("memory", a).fetch("MEMORY.md") == b"stored note"


@pytest.fixture
def store_refuses_writes(monkeypatch):
    """The stored note changed meanwhile, every time: no conditional write goes through."""

    async def _refuse(self, relative, data, *, expected):
        return False

    monkeypatch.setattr(FileMirror, "put_bytes", _refuse)


async def test_a_note_the_store_did_not_take_is_not_reported_as_shared(roots, on_instance_b, store_refuses_writes):
    from flocks.memory.daily import DailyMemory
    from flocks.memory.sync.mirror import MemoryNotShared, reconcile_note

    _a, b = roots
    note = _write(b, "topic.md", "only here")
    assert await reconcile_note(b, note, attempts=2) is False

    with pytest.raises(MemoryNotShared, match="saved on this instance only"):
        await DailyMemory().write_daily("afternoon on b", date="2026-10-14", append=True)


# ---------------------------------------------------------------------------
# Review round 3 (batch 4): an edit that lost to another instance's is stored
# too -- as its conflict copy -- before the note counts as shared
# ---------------------------------------------------------------------------

async def _both_edited(roots) -> tuple[Path, Path]:
    a, b = roots
    _write(a, "topic.md", "base")
    await reconcile(a)
    await reconcile(b)
    _write(a, "topic.md", "a's take")
    await reconcile(a)
    return a, _write(b, "topic.md", "b's take")


async def test_an_edit_that_lost_to_another_instance_is_kept_in_the_store_too(roots):
    from flocks.memory.sync.mirror import reconcile_note

    a, b_note = await _both_edited(roots)
    b = b_note.parent

    assert await reconcile_note(b, b_note) is True

    stored = await FileMirror("memory", a).listing("")
    copies = [path for path in stored if path.startswith("topic.conflict-")]
    assert len(copies) == 1, stored
    assert await FileMirror("memory", a).fetch(copies[0]) == b"b's take", "b's edit survives b"
    assert await FileMirror("memory", a).fetch("topic.md") == b"a's take"


@pytest.fixture
def copies_refused_once(monkeypatch):
    """The store refuses the first write of a conflict copy, then takes it."""
    real_put = FileMirror.put_bytes
    refused: list[str] = []

    async def _put(self, relative, data, *, expected):
        if ".conflict-" in relative and not refused:
            refused.append(relative)
            return False
        return await real_put(self, relative, data, expected=expected)

    monkeypatch.setattr(FileMirror, "put_bytes", _put)
    return refused


async def test_a_refused_conflict_copy_is_retried_with_its_note(roots, copies_refused_once):
    from flocks.memory.sync.mirror import reconcile_note

    a, b_note = await _both_edited(roots)

    assert await reconcile_note(b_note.parent, b_note, attempts=3) is True
    assert copies_refused_once, "the first write of the copy was refused"
    stored = await FileMirror("memory", a).listing("")
    assert [path for path in stored if path.startswith("topic.conflict-")], "and the retry stored it"


async def test_a_conflict_copy_left_behind_is_stored_by_the_next_settling_of_its_note(roots, copies_refused_once):
    from flocks.memory.sync.mirror import reconcile_note

    a, b_note = await _both_edited(roots)
    assert await reconcile_note(b_note.parent, b_note) is False, "one try: the copy stayed here"

    assert await reconcile_note(b_note.parent, b_note) is True
    stored = await FileMirror("memory", a).listing("")
    assert [path for path in stored if path.startswith("topic.conflict-")]


async def test_an_edit_whose_conflict_copy_the_store_refused_is_not_shared(roots, monkeypatch):
    from flocks.memory.sync.mirror import reconcile_note

    _a, b_note = await _both_edited(roots)
    real_put = FileMirror.put_bytes

    async def _refuse_copies(self, relative, data, *, expected):
        if ".conflict-" in relative:
            return False
        return await real_put(self, relative, data, expected=expected)

    monkeypatch.setattr(FileMirror, "put_bytes", _refuse_copies)

    assert await reconcile_note(b_note.parent, b_note) is False
