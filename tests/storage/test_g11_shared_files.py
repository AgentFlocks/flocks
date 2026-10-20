"""Files reach every instance, including those written before sharing began (G11).

Two data directories stand in for two instances' disks on one database:
* an upload written before uploads were kept in the database is found by a
  check and stored by a backfill; another instance then reads it, with the
  first one gone;
* a backfill never brings back a file removed elsewhere, never overwrites a
  stored copy, and leaves a file over the size limit on its own disk;
* channel media one instance received is stored, and another instance reads
  and serves it.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.session import files
from flocks.storage import file_backfill
from flocks.storage.file_backfill import (
    DIFFERS,
    LOCAL_ONLY,
    REMOVED_ELSEWHERE,
    SHARED,
    STORED_NOW,
    STORED_ONLY,
    TOO_LARGE,
)

pytestmark = pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="FLOCKS_TEST_PG_DSN is not set")


class Instances:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        self.disks = {"a": tmp_path / "a", "b": tmp_path / "b"}
        self.current = "a"
        monkeypatch.setattr(Config, "get_data_path", classmethod(lambda cls: self.disks[self.current]))

    def use(self, name: str) -> Path:
        self.current = name
        return self.disks[name]


@pytest.fixture
def instances(tmp_path, monkeypatch):
    return Instances(tmp_path, monkeypatch)


def _old_upload(session: str, name: str, content: bytes) -> Path:
    """An upload as written before uploads were kept in the database."""
    target = files.session_uploads_dir(session) / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _states(states) -> dict[str, str]:
    return {state.path: state.state for state in states}


async def test_an_upload_from_before_sharing_reaches_another_instance_after_a_backfill(instances):
    _old_upload("ses_g11_a", "report.txt", b"written before sharing")

    assert _states(await file_backfill.check_area("uploads")) == {"ses_g11_a/report.txt": LOCAL_ONLY}
    assert _states(await file_backfill.backfill_area("uploads", dry_run=True)) == {"ses_g11_a/report.txt": LOCAL_ONLY}
    backfilled = await file_backfill.backfill_area("uploads")
    assert _states(backfilled) == {"ses_g11_a/report.txt": STORED_NOW}
    assert backfilled[0].sha256 and backfilled[0].size == len(b"written before sharing")
    assert _states(await file_backfill.check_area("uploads")) == {"ses_g11_a/report.txt": SHARED}

    shutil.rmtree(instances.disks["a"])  # the first instance is gone
    disk_b = instances.use("b")
    assert _states(await file_backfill.check_area("uploads")) == {"ses_g11_a/report.txt": STORED_ONLY}
    assert await files.restore_session_uploads("ses_g11_a") == 1
    assert (disk_b / "uploads" / "ses_g11_a" / "report.txt").read_bytes() == b"written before sharing"


async def test_a_backfill_brings_back_nothing_removed_elsewhere_and_overwrites_nothing(instances):
    removed = _old_upload("ses_g11_b", "removed.txt", b"old copy")
    differing = _old_upload("ses_g11_b", "edited.txt", b"this disk's copy")
    mirror = files.uploads_mirror()
    # Elsewhere: one was stored and then removed, the other stored with other content.
    await mirror.put_bytes("ses_g11_b/removed.txt", b"old copy")
    await mirror.forget("ses_g11_b/removed.txt")
    await mirror.put_bytes("ses_g11_b/edited.txt", b"the stored copy, longer")

    result = _states(await file_backfill.backfill_area("uploads"))

    assert result == {"ses_g11_b/edited.txt": DIFFERS, "ses_g11_b/removed.txt": REMOVED_ELSEWHERE}
    assert await mirror.is_removed("ses_g11_b/removed.txt")
    assert await mirror.fetch("ses_g11_b/edited.txt") == b"the stored copy, longer"
    assert removed.exists() and differing.read_bytes() == b"this disk's copy", "nothing on this disk is touched"


async def test_a_file_over_the_limit_stays_on_its_instance(instances, monkeypatch):
    monkeypatch.setenv("FLOCKS_FILE_MIRROR_MAX_BYTES", "8")
    big = _old_upload("ses_g11_c", "big.bin", b"0123456789")

    assert _states(await file_backfill.backfill_area("uploads")) == {"ses_g11_c/big.bin": TOO_LARGE}
    assert await files.store_upload(big) is False, "kept, not refused"
    assert big.exists() and await files.uploads_mirror().fetch("ses_g11_c/big.bin") is None


async def test_channel_media_one_instance_received_is_read_and_served_by_another(instances):
    from flocks.channel.inbound.dispatcher import _share_channel_media
    from flocks.session.message import FilePart

    disk_a = instances.use("a")
    image = disk_a / "channel_media" / "wecom" / "acct" / "msg_1_photo.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"\x89PNG channel image")
    await _share_channel_media(image.as_uri())

    # B has its own data directory; the part keeps the URL A wrote.
    disk_b = instances.use("b")
    on_b = disk_b / "channel_media" / "wecom" / "acct" / "msg_1_photo.png"
    part = FilePart(sessionID="ses_g11_d", messageID="msg_1", url=image.as_uri(), mime="image/png", filename="photo.png")
    assert files.channel_media_paths([part]) == [on_b.resolve()]
    assert await files.restore_channel_media(files.channel_media_paths([part])) == 1
    assert on_b.read_bytes() == b"\x89PNG channel image"
    # What the model is given is read from B's copy.
    assert files.local_file_part_url("ses_g11_d", part) == on_b.resolve().as_uri()

    # Served on B, asked for by A's path, through the guarded download route
    # (which fetches it first).
    on_b.unlink()
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from flocks.server.routes.file import router

    app = FastAPI()
    app.include_router(router, prefix="/api/file")
    response = TestClient(app).get("/api/file/download", params={"path": str(image)})
    assert response.status_code == 200 and response.content == b"\x89PNG channel image"
    assert on_b.read_bytes() == b"\x89PNG channel image"


def test_only_channel_media_maps_onto_this_instances_directory(instances):
    disk = instances.use("b")
    root = (disk / "channel_media").resolve()
    assert files.local_channel_media_path(Path("/other/node/data/channel_media/feishu/acct/a.png")) == root / "feishu" / "acct" / "a.png"
    assert files.local_channel_media_path(root / "x" / "b.png") == root / "x" / "b.png"
    for outside in ("/other/node/data/uploads/ses_1/a.png", "/other/channel_media", "/other/channel_media/../etc/passwd", "/etc/passwd"):
        assert files.local_channel_media_path(Path(outside)) is None, outside


async def test_the_check_reports_a_mapping_of_every_file(instances):
    _old_upload("ses_g11_e", "a.txt", b"a")
    await files.store_upload(_old_upload("ses_g11_e", "b.txt", b"bb"))

    states = await file_backfill.check_area("uploads")
    mapping = {state.path: state.to_dict() for state in states}

    assert mapping["ses_g11_e/a.txt"] == {"area": "uploads", "path": "ses_g11_e/a.txt", "state": LOCAL_ONLY, "size": 1, "sha256": None}
    assert mapping["ses_g11_e/b.txt"]["state"] == SHARED and len(mapping["ses_g11_e/b.txt"]["sha256"]) == 64
    assert json.loads(json.dumps(file_backfill.summarize(states))) == {LOCAL_ONLY: 1, SHARED: 1}


async def test_a_channel_message_stores_its_media_as_it_arrives(instances):
    """The dispatcher shares a channel's file when it attaches it to the session."""
    from flocks.channel.base import InboundMessage
    from flocks.channel.inbound.dispatcher import InboundDispatcher
    from flocks.session.session import Session

    disk_a = instances.use("a")
    voice = disk_a / "channel_media" / "weixin" / "acct" / "msg_9_voice.amr"
    voice.parent.mkdir(parents=True)
    voice.write_bytes(b"#!AMR voice note")
    session = await Session.create(project_id="g11", directory="/tmp", title="g11 channel", category="channel")
    message = InboundMessage(
        channel_id="weixin", account_id="acct", message_id="msg_9", sender_id="u1",
        media_url=voice.as_uri(), media_mime="audio/amr",
    )

    await InboundDispatcher._append_user_message_unchecked(session.id, "[语音]", message)

    stored = await files.channel_media_mirror().fetch("weixin/acct/msg_9_voice.amr")
    assert stored == b"#!AMR voice note"
