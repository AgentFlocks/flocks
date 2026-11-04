"""Deleting messages takes the uploads they brought with them, and only those.

Retention trims old turns with ``Message.delete_selected``.  An upload a
removed message carried goes too, once no message anywhere names it.  An
upload a message left behind still points at stays -- a retry sends the same
file again, an older fork or copy points at the original's file -- and so does
a file that is not the session's own upload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.session import files
from flocks.session.message import FilePart, Message, MessageRole, UploadSweep
from flocks.session.session import Session


@pytest.fixture(autouse=True)
async def data_dir(tmp_path, monkeypatch):
    from flocks.storage.storage import Storage

    data = tmp_path / "data"
    monkeypatch.setattr(Config, "get_data_path", classmethod(lambda cls: data))
    Storage._initialized = False
    Storage._db_path = None
    await Storage.init(data / "flocks.db")
    yield data
    Session.invalidate_cache()
    Message.invalidate_cache()
    Storage._initialized = False
    Storage._db_path = None


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


async def test_an_upload_goes_with_the_message_that_brought_it(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    old = await _upload(session.id, "part_old0000000001.txt", b"old report")
    new = await _upload(session.id, "part_new0000000001.txt", b"new report")
    first = await _turn(session.id, "first", old)
    await _turn(session.id, "second", new)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert not old.exists()
    assert new.read_bytes() == b"new report"


async def test_an_upload_a_later_message_sent_again_stays(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    shared = await _upload(session.id, "part_retry00000001.txt", b"sent twice")
    first = await _turn(session.id, "first", shared)
    await _turn(session.id, "retry", shared)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert shared.read_bytes() == b"sent twice"


async def test_a_fork_keeps_its_own_copy_when_the_original_is_trimmed(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_fork000000001.txt", b"forked along")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    fork = await Session.fork(session.project_id, session.id)
    fork_files = [part for message in await Message.list_with_parts(fork.id) for part in message.parts if part.type == "file"]
    (fork_file,) = fork_files
    own = files.session_uploads_dir(fork.id) / upload.name
    assert fork_file.url == own.resolve().as_uri() and own.read_bytes() == b"forked along"

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert not upload.exists(), "the original's upload went with its message"
    assert own.read_bytes() == b"forked along", "the fork's copy stays"


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
    # An upload on no disk here (another machine's export): unchanged.
    foreign = {"type": "file", "url": "file:///elsewhere/uploads/ses_other/a.txt", "mime": "text/plain"}
    assert (await _adopt_part_upload(foreign, "ses_imported"))["url"] == foreign["url"]
    media = {"type": "file", "url": "file:///elsewhere/channel_media/wecom/acct/a.png", "mime": "image/png"}
    assert await _adopt_part_upload(media, "ses_imported") == media


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
    await _turn(old_copy.id, "copied", upload)  # the URL still names the original's upload

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert upload.read_bytes() == b"named by an old copy"


async def test_one_sweep_covers_the_uploads_of_a_whole_run(tmp_path):
    trimmed = []
    for name in ("a", "b"):
        session = await Session.create(project_id="uploads", directory=str(tmp_path))
        upload = await _upload(session.id, f"part_sweep{name}0000001.txt", f"report {name}".encode())
        first = await _turn(session.id, "first", upload)
        await _turn(session.id, "second")
        assert await Message.delete_selected(session.id, lambda stored, live, first=first: first, sweep_uploads=False) == 2
        assert upload.exists(), "nothing goes before the sweep runs"
        trimmed.append(upload)

    assert await UploadSweep().run() == 2
    assert not any(upload.exists() for upload in trimmed)
    assert await UploadSweep().run() == 0, "nothing left to sweep"


async def test_a_file_sent_again_while_the_sweep_runs_is_kept(tmp_path, monkeypatch):
    """The sweep found nothing naming the file, then a retry named it before the removal."""
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


async def test_a_sweep_that_failed_leaves_its_candidates_for_the_next(tmp_path, monkeypatch):
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
    assert not upload.exists()


async def test_deleting_a_session_keeps_the_uploads_another_session_still_names(tmp_path):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    named = await _upload(session.id, "part_delnamed00001.txt", b"named elsewhere")
    alone = await _upload(session.id, "part_delalone00001.txt", b"named nowhere else")
    await _turn(session.id, "first", named, alone)
    old_copy = await Session.create(project_id="uploads", directory=str(tmp_path))
    await _turn(old_copy.id, "copied", named)

    assert await Session.delete(session.project_id, session.id)

    assert named.read_bytes() == b"named elsewhere"
    assert not alone.exists()


async def test_a_file_that_is_not_the_sessions_upload_is_left_alone(tmp_path, data_dir):
    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    other = await _upload("ses_someone_else", "part_other00000001.txt", b"not this session's")
    media = data_dir / "channel_media" / "wecom" / "acct" / "msg_1_photo.png"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\x89PNG")
    first = await _turn(session.id, "first", other, media)

    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2

    assert other.exists() and media.exists()


def _stored_urls(session_id: str) -> set:
    import json
    import sqlite3

    from flocks.storage.storage import Storage

    with sqlite3.connect(Storage.get_db_path()) as conn:
        rows = conn.execute(
            "SELECT value FROM storage WHERE key LIKE ?", (f"message_parts:{session_id}:%",),
        ).fetchall()
    return {part.get("url") for (value,) in rows for part in json.loads(value) if part.get("type") == "file"}


async def test_a_message_that_names_a_removed_upload_is_refused(tmp_path):
    from flocks.session.message import UploadRemoved

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_gone000000001.txt", b"removed")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    assert await Message.delete_selected(session.id, lambda stored, live: first) == 2
    assert not upload.exists()

    retry = await Message.create(session.id, MessageRole.USER, "retry")
    with pytest.raises(UploadRemoved):
        await Message.store_part(session.id, retry.id, FilePart(
            sessionID=session.id, messageID=retry.id, url=upload.resolve().as_uri(),
            mime="text/plain", filename=upload.name,
        ))

    assert upload.resolve().as_uri() not in _stored_urls(session.id)
    assert not [part for part in (await Message.get_with_parts_lazy(session.id, retry.id)).parts if part.type == "file"]


async def test_a_retry_landing_right_after_the_last_look_does_not_lose_its_file(tmp_path, monkeypatch):
    """The sweep's last look found no message naming the upload; the retry wrote one; the file went anyway."""
    import asyncio

    from flocks.session.message import UploadRemoved

    session = await Session.create(project_id="uploads", directory=str(tmp_path))
    upload = await _upload(session.id, "part_race000000001.txt", b"sent again")
    first = await _turn(session.id, "first", upload)
    await _turn(session.id, "second")
    retry = await Message.create(session.id, MessageRole.USER, "retry")
    retry_write = []

    # The lowest-level look: the last one is the second call.
    name = "_named_on" if hasattr(UploadSweep, "_named_on") else "_named_in"
    look = getattr(UploadSweep, name)
    calls = []

    async def look_then_retry(*args):
        named = await look(*args)
        calls.append(1)
        if len(calls) == 2:
            retry_write.append(asyncio.create_task(Message.store_part(session.id, retry.id, FilePart(
                sessionID=session.id, messageID=retry.id, url=upload.resolve().as_uri(),
                mime="text/plain", filename=upload.name,
            ))))
            await asyncio.sleep(0.3)
        return named

    monkeypatch.setattr(UploadSweep, name, staticmethod(look_then_retry))
    await Message.delete_selected(session.id, lambda stored, live: first)
    (written,) = await asyncio.gather(*retry_write, return_exceptions=True)

    named = upload.resolve().as_uri() in _stored_urls(session.id)
    assert upload.exists() or not named, "a message names an upload that is gone"
    assert isinstance(written, UploadRemoved) or upload.exists()


async def test_a_refused_attachment_is_not_written_by_a_later_flush_of_a_legacy_session(tmp_path):
    """The refused part stayed in the serialized copy; a running tool's flush wrote the whole blob with it."""
    import json as _json

    from flocks.session.message import ToolPart, ToolStateRunning, UploadRemoved, UserMessageInfo
    from flocks.storage.storage import Storage

    session_id = "ses_legacy_refused"
    upload = await _upload(session_id, "part_legacy0000001.txt", b"removed")
    relative = files.uploads_mirror().relative(upload)
    await Storage.set(files.REMOVED_UPLOAD_PREFIX + relative, {"sessionID": session_id})  # a sweep removed it
    messages = [
        UserMessageInfo(id=mid, sessionID=session_id, role="user", time={"created": 1}, agent="rex",
                        model={"providerID": "test", "modelID": "test"}).model_dump()
        for mid in ("msg_a", "msg_b")
    ]
    await Storage.set(f"message:{session_id}", messages, "message")
    await Storage.set(f"message_parts:{session_id}", {"msg_a": [], "msg_b": []}, "message_parts")
    Message.invalidate_cache(session_id)

    await Message.store_part(session_id, "msg_a", ToolPart(
        id="part_tool", sessionID=session_id, messageID="msg_a", callID="call_a", tool="read",
        state=ToolStateRunning(input={}, time={"start": 1}),
    ))  # running: written a little later, with the whole legacy blob
    with pytest.raises(UploadRemoved):
        await Message.store_part(session_id, "msg_b", FilePart(
            sessionID=session_id, messageID="msg_b", url=upload.resolve().as_uri(),
            mime="text/plain", filename=upload.name,
        ))
    await Message.quiesce_parts(session_id, persist=False)
    await Message._persist_parts(session_id, message_id="msg_a")  # what the delayed flush does

    stored = await Storage.get(f"message_parts:{session_id}")
    assert upload.name not in _json.dumps(stored)
    assert [part["type"] for part in stored["msg_a"]] == ["tool"]
