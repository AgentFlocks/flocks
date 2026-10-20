"""Deleting messages takes the uploads they brought with them, and only those.

Retention trims old turns with ``Message.delete_selected``.  An upload a
removed message carried goes too: from this instance's disk and from the
shared store (a tombstone, so no instance brings it back).  An upload a message
left behind still points at stays -- a retry sends the same file again, a fork
copies the file part with its URL -- and so does a file that is not the
session's own upload.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.session import files
from flocks.session.message import FilePart, Message, MessageRole
from flocks.session.session import Session

pytestmark = pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="FLOCKS_TEST_PG_DSN is not set")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setattr(Config, "get_data_path", classmethod(lambda cls: data))
    return data


async def _upload(session_id: str, name: str, content: bytes) -> Path:
    target = files.session_uploads_dir(session_id) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    assert await files.store_upload(target)
    return target


async def _turn(session_id: str, text: str, *attached: Path) -> list[str]:
    user = await Message.create(session_id, MessageRole.USER, text)
    for path in attached:
        await Message.store_part(session_id, user.id, FilePart(
            sessionID=session_id, messageID=user.id, url=path.resolve().as_uri(),
            mime="text/plain", filename=path.name,
        ))
    reply = await Message.create(session_id, MessageRole.ASSISTANT, f"read {text}", parentID=user.id)
    return [user.id, reply.id]


def _stored(session_id: str, path: Path) -> str:
    return f"{session_id}/{path.name}"


async def test_an_upload_goes_with_the_message_that_brought_it(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    old = await _upload(session.id, "part_old0000000001.txt", b"old report")
    new = await _upload(session.id, "part_new0000000001.txt", b"new report")
    first = await _turn(session.id, "first", old)
    await _turn(session.id, "second", new)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    mirror = files.uploads_mirror()
    assert not old.exists() and await mirror.is_removed(_stored(session.id, old))
    assert await mirror.fetch(_stored(session.id, old)) is None
    assert new.read_bytes() == b"new report" and await mirror.fetch(_stored(session.id, new)) == b"new report"


async def test_an_upload_a_later_message_sent_again_stays(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    shared = await _upload(session.id, "part_retry00000001.txt", b"sent twice")
    first = await _turn(session.id, "first", shared)
    await _turn(session.id, "retry", shared)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert shared.read_bytes() == b"sent twice"
    assert await files.uploads_mirror().fetch(_stored(session.id, shared)) == b"sent twice"


async def test_a_fork_keeps_its_own_copy_when_the_original_is_trimmed(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_fork000000001.txt", b"forked along")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    fork = await Session.fork(session.project_id, session.id)
    assert fork.parent_id == session.id
    fork_files = [part for message in await Message.list_with_parts(fork.id) for part in message.parts if part.type == "file"]
    (fork_file,) = fork_files
    own = files.session_uploads_dir(fork.id) / upload.name
    assert fork_file.url == own.resolve().as_uri() and own.read_bytes() == b"forked along"

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert not upload.exists(), "the original's upload went with its message"
    assert own.read_bytes() == b"forked along", "the fork's copy stays"
    assert await files.uploads_mirror().fetch(_stored(fork.id, own)) == b"forked along"


async def test_a_fork_made_before_forks_had_copies_still_keeps_the_file(tmp_path):
    """Older forks point at the original's file: a descendant's reference keeps it."""
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_oldfork000001.txt", b"shared with an old fork")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    old_fork = await Session.create(project_id="uploads", directory=str(tmp_path), parent_id=session.id)
    await _turn(old_fork.id, "copied", upload)  # the URL still names the original's upload

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert upload.read_bytes() == b"shared with an old fork"


async def test_a_session_imported_as_a_copy_gets_its_own_uploads(tmp_path):
    from flocks.cli.commands.import_ import _adopt_part_upload

    upload = await _upload("ses_exported", "part_import0000001.txt", b"exported attachment")
    part = {"type": "file", "url": upload.resolve().as_uri(), "mime": "text/plain", "sessionID": "ses_exported"}

    adopted = await _adopt_part_upload(part, "ses_imported")

    own = files.session_uploads_dir("ses_imported") / upload.name
    assert adopted["url"] == own.resolve().as_uri() and adopted["sessionID"] == "ses_imported"
    assert own.read_bytes() == b"exported attachment"
    assert await files.uploads_mirror().fetch(_stored("ses_imported", own)) == b"exported attachment"
    # An upload on no disk here and not in the shared store (another machine's export): unchanged.
    foreign = {"type": "file", "url": "file:///elsewhere/uploads/ses_other/a.txt", "mime": "text/plain"}
    assert (await _adopt_part_upload(foreign, "ses_imported"))["url"] == foreign["url"]
    media = {"type": "file", "url": "file:///elsewhere/channel_media/wecom/acct/a.png", "mime": "image/png"}
    assert await _adopt_part_upload(media, "ses_imported") == media


async def test_an_imported_fork_gets_its_own_copy_of_its_ancestors_upload():
    """A fork made before forks had copies points at its ancestor's file; exported and imported, it gets one."""
    from flocks.cli.commands.import_ import _adopt_part_upload

    upload = await _upload("ses_root", "part_ancestor00001.txt", b"the ancestor's attachment")
    part = {"type": "file", "url": upload.resolve().as_uri(), "mime": "text/plain", "sessionID": "ses_old_fork"}

    adopted = await _adopt_part_upload(part, "ses_imported_fork")

    own = files.session_uploads_dir("ses_imported_fork") / upload.name
    assert adopted["url"] == own.resolve().as_uri() and own.read_bytes() == b"the ancestor's attachment"


async def test_a_legacy_part_with_its_url_in_metadata_is_imported_with_its_own_copy():
    from flocks.cli.commands.import_ import _imported_parts

    upload = await _upload("ses_legacy", "part_legacy0000001.txt", b"legacy attachment")
    legacy = {"type": "file", "metadata": {"url": upload.resolve().as_uri(), "mime": "text/plain"}}

    (stored,) = await _imported_parts([legacy], message_time=None, new_session_id="ses_legacy_copy")

    own = (files.session_uploads_dir("ses_legacy_copy") / upload.name).resolve().as_uri()
    assert stored["url"] == own and stored["metadata"]["url"] == own


async def test_a_copy_imported_before_copies_had_their_own_uploads_still_keeps_the_file(tmp_path):
    """A copy with no relation to the original -- made by an older import -- still names its file."""
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_oldcopy000001.txt", b"named by an old copy")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    old_copy = await Session.create(project_id="uploads", directory=str(tmp_path))
    assert not old_copy.parent_id
    await _turn(old_copy.id, "copied", upload)  # the URL still names the original's upload

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert upload.read_bytes() == b"named by an old copy"
    assert await files.uploads_mirror().fetch(_stored(session.id, upload)) == b"named by an old copy"


async def test_one_sweep_covers_the_uploads_of_a_whole_run(tmp_path):
    from flocks.session.message import UploadSweep

    trimmed = []
    for name in ("a", "b"):
        session = await Session.create(project_id="uploads", directory=str(tmp_path))
        upload = await _upload(session.id, f"part_sweep{name}0000001.txt", f"report {name}".encode())
        first = await _turn(session.id, "first", upload)
        await _turn(session.id, "second")
        assert await Message.delete_selected(session.id, lambda stored, live, first=first: first, sweep_uploads=False) == 2
        assert upload.exists(), "nothing goes before the sweep runs"
        trimmed.append((session.id, upload))

    assert await UploadSweep().run() == 2
    for session_id, upload in trimmed:
        assert not upload.exists() and await files.uploads_mirror().is_removed(_stored(session_id, upload))
    assert await UploadSweep().run() == 0, "nothing left to sweep"


async def test_a_file_sent_again_while_the_sweep_runs_is_kept(tmp_path, monkeypatch):
    """The reviewer's case: the sweep found nothing naming the file, then a retry named it before the removal."""
    from flocks.session.message import UploadSweep

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_retryrace0001.txt", b"sent again")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    assert await Message.delete_selected(session.id, lambda stored, live: first, sweep_uploads=False) == 2

    real_still_named = UploadSweep._still_named

    async def look_then_retry(self, candidates):
        named = await real_still_named(self, candidates)
        assert not named, "nothing named it when the sweep looked"
        await _turn(session.id, "retry", upload)  # the user sends the same file again
        return named

    monkeypatch.setattr(UploadSweep, "_still_named", look_then_retry)
    assert await UploadSweep(sessions=[session.id]).run() == 0
    assert upload.read_bytes() == b"sent again"
    assert await files.uploads_mirror().fetch(_stored(session.id, upload)) == b"sent again"


async def test_a_sweep_that_failed_leaves_its_candidates_for_the_next(tmp_path, monkeypatch):
    from flocks.session.message import UploadSweep

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_sweepfail0001.txt", b"still here")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")

    async def broken(self, candidates):
        raise RuntimeError("database went away")

    with monkeypatch.context() as patch:
        patch.setattr(UploadSweep, "_still_named", broken)
        with pytest.raises(RuntimeError):
            await Message.delete_selected(session.id, lambda stored, live: first)
    assert upload.exists()

    assert await UploadSweep().run() == 1, "the next sweep takes it"
    assert not upload.exists()


async def test_an_upload_kept_for_an_old_copy_goes_once_the_copy_no_longer_names_it(tmp_path):
    from flocks.session.message import UploadSweep

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_keptcopy00001.txt", b"named by a copy")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    old_copy = await Session.create(project_id="uploads", directory=str(tmp_path))
    copied = await _turn(old_copy.id, "copied", upload)
    await _turn(old_copy.id, "later")

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2
    assert upload.exists(), "the old copy still names it"

    assert await Message.delete_selected(old_copy.id, lambda stored, live: copied) == 2
    assert await UploadSweep().run() == 1
    assert not upload.exists() and await files.uploads_mirror().is_removed(_stored(session.id, upload))


async def test_deleting_a_session_keeps_the_uploads_another_session_still_names(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    named = await _upload(session.id, "part_delnamed00001.txt", b"named elsewhere")
    alone = await _upload(session.id, "part_delalone00001.txt", b"named nowhere else")
    await _turn(session.id, "first", named, alone)
    old_copy = await Session.create(project_id="uploads", directory=str(tmp_path))
    await _turn(old_copy.id, "copied", named)

    assert await Session.delete(session.project_id, session.id)

    assert named.read_bytes() == b"named elsewhere"
    assert await files.uploads_mirror().fetch(_stored(session.id, named)) == b"named elsewhere"
    assert not alone.exists() and await files.uploads_mirror().is_removed(_stored(session.id, alone))


async def test_a_file_named_after_the_sweep_removed_it_is_refused(tmp_path):
    """The reviewer's case the other way round: the removal commits first, the write comes after."""
    from flocks.session.message import UploadRemoved, UploadSweep

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_afterrm00001.txt", b"removed first")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2
    assert not upload.exists()

    with pytest.raises(UploadRemoved):
        await _turn(session.id, "sent again", upload)
    # Another session naming it directly is refused the same way.
    other = await Session.create(project_id="uploads", directory=str(tmp_path))
    with pytest.raises(UploadRemoved):
        await _turn(other.id, "named elsewhere", upload)
    stored = [part for message in await Message.list_with_parts(other.id) for part in message.parts if part.type == "file"]
    assert stored == [], "the refused part is not kept, in storage or in the cache"
    assert await UploadSweep().run() == 0


async def test_a_write_holding_the_uploads_lock_is_seen_by_the_removal(tmp_path):
    """A write that named the file first holds its lock; the removal waits, looks again, and keeps it."""
    import asyncio

    from flocks.session.message import UploadSweep, guard_new_upload_references
    from flocks.storage.storage import Storage

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_lockfirst0001.txt", b"named first")
    first = await _turn(session.id, "first", upload)
    second = await _turn(session.id, "second")
    assert await Message.delete_selected(session.id, lambda stored, live: first, sweep_uploads=False) == 2

    part = {"type": "file", "url": upload.resolve().as_uri(), "mime": "text/plain"}
    holding = asyncio.Event()
    release = asyncio.Event()

    async def writer():
        async with Storage.transaction() as db:
            await guard_new_upload_references(db, [], [part])
            await db.execute(
                "UPDATE runtime_state SET value = value || ?::jsonb WHERE key = ?",
                ('{"note": "file:/%s"}' % _stored(session.id, upload), Message._parts_item_key(session.id, second[0])),
            )
            holding.set()
            await release.wait()

    task = asyncio.create_task(writer())
    await holding.wait()
    sweep = asyncio.create_task(UploadSweep().run())
    await asyncio.sleep(0.5)
    assert not sweep.done(), "the removal waits for the write's lock"
    release.set()
    await task
    assert await sweep == 0, "the second look sees the write"
    assert upload.read_bytes() == b"named first"


async def test_a_session_whose_uploads_cannot_be_listed_is_not_deleted(tmp_path, monkeypatch):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_nolist000001.txt", b"kept with its session")
    await _turn(session.id, "first", upload)

    async def unlisted(session_ids):
        raise OSError("the store did not answer")

    monkeypatch.setattr(files, "session_upload_candidates", unlisted)
    with pytest.raises(OSError):
        await Session.delete(session.project_id, session.id)
    assert await Session.get_by_id_unfiltered(session.id) is not None
    assert upload.exists()


async def test_an_import_that_names_a_removed_upload_is_refused(tmp_path, monkeypatch):
    """Imports write parts directly: the same lock and tombstone check as every other write."""
    import json

    import typer

    from flocks.cli.commands import import_ as import_cmd

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_importrm0001.txt", b"removed before the import")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    exported = {
        "info": (await Session.get_by_id(session.id)).model_dump(by_alias=True),
        "messages": [
            {"info": item.info.model_dump(by_alias=True), "parts": [part.model_dump() for part in item.parts]}
            for item in await Message.list_with_parts(session.id)
        ],
    }
    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2
    assert not upload.exists()
    path = tmp_path / "export.json"
    path.write_text(json.dumps(exported), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(typer.Exit):
        await import_cmd._import_session(str(path), "uploads", force=True)
    Message.invalidate_cache(session.id)
    named = [
        part for item in await Message.list_with_parts(session.id) for part in item.parts
        if part.type == "file" and upload.name in str(part.url)
    ]
    assert named == [], "the refused import wrote nothing naming the removed upload"


async def test_a_worker_whose_cache_is_behind_cannot_write_a_removed_upload_back(tmp_path):
    """The reviewer's case: another worker removed the part and its upload; this one still has it cached."""
    from flocks.session.message import UploadRemoved, UploadSweep, register_upload_candidates
    from flocks.storage.storage import Storage

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_stalecache001.txt", b"removed elsewhere")
    user_id, _reply_id = await _turn(session.id, "first", upload)
    (cached,) = [part for part in (await Message.list_with_parts(session.id))[0].parts if part.type == "file"]

    # The other worker: the part goes from storage, its upload becomes a candidate and is swept.
    async with Storage.transaction() as db:
        key = Message._parts_item_key(session.id, user_id)
        row = await db.fetch_one("SELECT value FROM runtime_state WHERE key = ?", (key,))
        stored = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        kept = [part for part in stored if part.get("type") != "file"]
        await db.execute("UPDATE runtime_state SET value = ?::jsonb WHERE key = ?", (json.dumps(kept), key))
        await register_upload_candidates(db, session.id, [_stored(session.id, upload)])
    assert await UploadSweep().run() == 1

    # This worker, cache unchanged, renames the attachment it still holds.
    renamed = cached.model_copy(update={"filename": "renamed.txt"})
    with pytest.raises(UploadRemoved):
        await Message.store_part(session.id, user_id, renamed)
    async with Storage.transaction() as db:
        row = await db.fetch_one("SELECT value FROM runtime_state WHERE key = ?", (key,))
    stored = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    assert not [part for part in stored if part.get("type") == "file"], "the removed part was not written back"


async def test_the_guard_uses_only_its_transactions_connection(tmp_path, monkeypatch):
    """No second connection from inside the write's transaction (a full pool would wait on itself)."""
    from flocks.session.message import guard_new_upload_references
    from flocks.storage.file_mirror import FileMirror
    from flocks.storage.storage import Storage

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_oneconn00001.txt", b"one connection")

    async def no_second_connection():
        raise AssertionError("the guard took another connection inside the transaction")

    monkeypatch.setattr(FileMirror, "_pool", staticmethod(no_second_connection))
    part = {"type": "file", "url": upload.resolve().as_uri(), "mime": "text/plain"}
    async with Storage.transaction() as db:
        await guard_new_upload_references(db, [], [part])


async def test_the_uploads_locks_are_taken_in_one_statement():
    """However many uploads a sweep removes: one round trip, every lock held until the transaction ends."""
    from flocks.session.message import _lock_uploads
    from flocks.storage.storage import Storage

    statements = []
    relatives = [f"ses_lockmany/part_{index:04d}.txt" for index in range(50)]

    class Counted:
        def __init__(self, db):
            self.db = db

        async def execute(self, sql, *args, **kwargs):
            statements.append(sql)
            return await self.db.execute(sql, *args, **kwargs)

    async with Storage.transaction() as db:
        await _lock_uploads(Counted(db), reversed(relatives))
        row = await db.fetch_one(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()", (),
        )
    assert len(statements) == 1
    assert row[0] == len(relatives)


async def test_the_uploads_locks_are_taken_in_one_order():
    """Named high-then-low, the low one is still taken first: it is held while the high one is waited for."""
    import asyncio

    from flocks.session.message import _UPLOAD_LOCK, _lock_uploads
    from flocks.storage.storage import Storage

    low, high = "ses_lockorder/part_a.txt", "ses_lockorder/part_b.txt"
    held, release = asyncio.Event(), asyncio.Event()
    waiter_pid = asyncio.get_running_loop().create_future()

    async def hold(relative):
        async with Storage.transaction() as db:
            await _lock_uploads(db, [relative])
            held.set()
            await release.wait()

    async def lock_both():
        async with Storage.transaction() as db:
            waiter_pid.set_result((await db.fetch_one("SELECT pg_backend_pid()", ()))[0])
            await _lock_uploads(db, [high, low])

    holder = asyncio.create_task(hold(high))
    await held.wait()
    waiter = asyncio.create_task(lock_both())
    try:
        pid = await asyncio.wait_for(waiter_pid, timeout=30)
        for _attempt in range(500):  # until this waiter (not any other backend) is blocked on `high`
            async with Storage.transaction() as db:
                row = await db.fetch_one(
                    "SELECT count(*) FROM pg_locks WHERE pid = ? AND locktype = 'advisory' AND NOT granted", (pid,),
                )
            if row[0]:
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("the waiter never waited for the high one")
        async with Storage.transaction() as db:
            row = await db.fetch_one("SELECT pg_try_advisory_xact_lock(hashtextextended(?, 0))", (_UPLOAD_LOCK + low,))
        assert row[0] is False, "the low one was taken before the high one was waited for"
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(holder, waiter), timeout=30)


async def test_a_store_that_failed_before_its_transaction_keeps_nothing(tmp_path, monkeypatch):
    """The tombstone table could not be made ready: an edit made right after does not write the part, nor does a read show it."""
    from flocks.storage.file_mirror import FileMirror

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_initfail00001.txt", b"never stored")
    user = await Message.create(session.id, MessageRole.USER, "with an attachment to come")
    (text,) = [part for part in (await Message.list_with_parts(session.id))[0].parts if part.type == "text"]
    real_pool = FileMirror._pool

    async def database_went_away():
        raise RuntimeError("database went away")

    monkeypatch.setattr(FileMirror, "_pool", staticmethod(database_went_away))
    with pytest.raises(RuntimeError):
        await Message.store_part(session.id, user.id, FilePart(
            sessionID=session.id, messageID=user.id, url=upload.resolve().as_uri(),
            mime="text/plain", filename=upload.name,
        ))
    monkeypatch.setattr(FileMirror, "_pool", staticmethod(real_pool))

    def files_of(messages):
        return [part for message in messages for part in message.parts if part.type == "file"]

    await Message.store_part(session.id, user.id, text.model_copy(update={"text": "edited"}))
    assert files_of(await Message.list_with_parts(session.id)) == [], "not in what this process reads"
    Message.invalidate_cache(session.id)
    messages = await Message.list_with_parts(session.id)
    assert files_of(messages) == [], "the edit made right after did not write it"
    assert [part.text for part in messages[0].parts if part.type == "text"] == ["edited"]


async def test_a_failed_attachment_is_not_written_by_its_messages_pending_flush(tmp_path, monkeypatch):
    """The reviewer's case: a running tool waits to be written on the same message when the attachment's store fails."""
    from flocks.session.message import ToolPart, ToolStateRunning
    from flocks.storage.file_mirror import FileMirror

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_initfail00002.txt", b"never stored")
    user_id, _reply_id = await _turn(session.id, "first")
    reply = await Message.create(session.id, MessageRole.ASSISTANT, "working on it", parentID=user_id)
    await Message.store_part(session.id, reply.id, ToolPart(
        sessionID=session.id, messageID=reply.id, callID="call_initfail", tool="read",
        state=ToolStateRunning(input={"filePath": upload.name}, time={"start": 1}),
    ))  # running: written a little later
    real_pool = FileMirror._pool

    async def database_went_away():
        raise RuntimeError("database went away")

    monkeypatch.setattr(FileMirror, "_pool", staticmethod(database_went_away))
    with pytest.raises(RuntimeError):
        await Message.store_part(session.id, reply.id, FilePart(
            sessionID=session.id, messageID=reply.id, url=upload.resolve().as_uri(),
            mime="text/plain", filename=upload.name,
        ))
    monkeypatch.setattr(FileMirror, "_pool", staticmethod(real_pool))

    def kinds(messages):
        return [(part.type, getattr(getattr(part, "state", None), "status", None))
                for message in messages if message.info.id == reply.id for part in message.parts]

    assert ("file", None) not in kinds(await Message.list_with_parts(session.id)), "not in what this process reads"
    await Message.quiesce_parts(session.id, persist=True)  # whatever is still pending is written now
    Message.invalidate_cache(session.id)
    stored = kinds(await Message.list_with_parts(session.id))
    assert ("file", None) not in stored, "the pending flush did not write it"
    assert ("tool", "running") in stored, "the change the message had pending was written"


async def test_a_refused_upload_is_not_sent_again_with_its_messages_pending_changes(tmp_path):
    """A running tool's unsaved progress and a refused upload on one message: the progress is kept, the session stays usable."""
    from flocks.session.message import ToolPart, ToolStateCompleted, ToolStateRunning, UploadRemoved

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_pending00001.txt", b"removed first")
    first = await _turn(session.id, "first", upload)
    user_id, _reply_id = await _turn(session.id, "second")
    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2
    assert not upload.exists()

    reply = await Message.create(session.id, MessageRole.ASSISTANT, "working on it", parentID=user_id)
    tool = ToolPart(sessionID=session.id, messageID=reply.id, callID="call_pending", tool="read",
                    state=ToolStateRunning(input={"filePath": upload.name}, time={"start": 1}))
    await Message.store_part(session.id, reply.id, tool)  # running: written a little later
    with pytest.raises(UploadRemoved):
        await Message.store_part(session.id, reply.id, FilePart(
            sessionID=session.id, messageID=reply.id, url=upload.resolve().as_uri(),
            mime="text/plain", filename=upload.name,
        ))

    def parts_of(messages, kind):
        return [part for message in messages for part in message.parts if part.type == kind]

    assert parts_of(await Message.list_with_parts(session.id), "file") == [], "readable, without the refused part"
    await Message.quiesce_parts(session.id, persist=True)  # whatever is still pending is written now
    Message.invalidate_cache(session.id)
    assert [part.state.status for part in parts_of(await Message.list_with_parts(session.id), "tool")] == ["running"]
    done = tool.model_copy(update={"state": ToolStateCompleted(
        input={"filePath": upload.name}, output="gone", title="read", metadata={}, time={"start": 1, "end": 2},
    )})
    await Message.store_part(session.id, reply.id, done)
    Message.invalidate_cache(session.id)
    messages = await Message.list_with_parts(session.id)
    assert parts_of(messages, "file") == []
    assert [part.state.status for part in parts_of(messages, "tool")] == ["completed"]


async def test_a_file_that_is_not_the_sessions_upload_is_left_alone(tmp_path, data_dir):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    other = await _upload("ses_someone_else", "part_other00000001.txt", b"not this session's")
    media = data_dir / "channel_media" / "wecom" / "acct" / "msg_1_photo.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\x89PNG")
    first = await _turn(session.id, "first", other, media)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert other.exists() and media.exists()
    assert await files.uploads_mirror().fetch(_stored("ses_someone_else", other)) == b"not this session's"
