"""Uploads kept in PostgreSQL: a file uploaded through one instance can be
bound, read and served by another, whose disk only caches it."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.session import files

requires_pg = pytest.mark.skipif(not os.environ.get("FLOCKS_TEST_PG_DSN"), reason="FLOCKS_TEST_PG_DSN is not set")

OWNER = "user-1"
SESSION = "ses_mirror_test"
UPLOAD = "part_0123456789abcdef"


class Instances:
    """Two data directories standing in for two instances' disks."""

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


async def _stage(content: bytes = b"quarterly report") -> Path:
    target = files.create_chat_upload_target(OWNER, UPLOAD, "report.txt")
    target.write_bytes(content)
    await files.store_upload(target)
    return target


@requires_pg
async def test_an_upload_staged_on_one_instance_binds_on_another(instances):
    await _stage()

    instances.use("b")
    with pytest.raises(FileNotFoundError):
        files.resolve_chat_upload(SESSION, OWNER, UPLOAD)
    await files.restore_chat_upload_source(SESSION, OWNER, UPLOAD)
    original = files.resolve_chat_upload(SESSION, OWNER, UPLOAD)
    bound = files.bind_staged_chat_upload(SESSION, OWNER, UPLOAD)
    await files.move_stored_upload(original, bound)

    assert bound.read_bytes() == b"quarterly report"
    stored = await files.uploads_mirror().listing("")
    assert list(stored) == [f"{SESSION}/{UPLOAD}.txt"], "the staged copy moved with it"


@requires_pg
async def test_a_bound_upload_is_on_every_instance_that_reads_the_session(instances):
    await _stage(b"\x89PNG not really")
    await files.restore_chat_upload_source(SESSION, OWNER, UPLOAD)
    original = files.resolve_chat_upload(SESSION, OWNER, UPLOAD)
    await files.move_stored_upload(original, files.bind_staged_chat_upload(SESSION, OWNER, UPLOAD))

    disk = instances.use("b")
    assert await files.restore_session_uploads(SESSION) == 1
    assert (disk / "uploads" / SESSION / f"{UPLOAD}.txt").read_bytes() == b"\x89PNG not really"
    assert await files.restore_session_uploads(SESSION) == 0, "already here: nothing fetched again"


@requires_pg
async def test_discarding_a_staged_upload_removes_the_stored_copy(instances):
    await _stage()

    instances.use("b")
    assert await files.discard_staged_chat_upload(OWNER, UPLOAD) is True

    assert await files.uploads_mirror().listing("") == {}
    # Discarded through b: a's cache still holds the file, and is refused.
    instances.use("a")
    assert files.resolve_chat_upload(SESSION, OWNER, UPLOAD).exists()
    with pytest.raises(FileNotFoundError, match="discarded"):
        await files.restore_chat_upload_source(SESSION, OWNER, UPLOAD)


@requires_pg
async def test_deleting_a_session_forgets_its_uploads(instances):
    await _stage()
    await files.restore_chat_upload_source(SESSION, OWNER, UPLOAD)
    original = files.resolve_chat_upload(SESSION, OWNER, UPLOAD)
    await files.move_stored_upload(original, files.bind_staged_chat_upload(SESSION, OWNER, UPLOAD))

    assert await files.forget_session_uploads(SESSION) == 1

    instances.use("b")
    assert await files.restore_session_uploads(SESSION) == 0


@requires_pg
async def test_the_store_refuses_paths_outside_the_uploads_root(instances, tmp_path):
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    with pytest.raises(ValueError):
        await files.store_upload(outside)
    with pytest.raises(ValueError):
        files.uploads_mirror().local("../escape.txt")
