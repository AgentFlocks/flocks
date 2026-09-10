from __future__ import annotations

import asyncio
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest
from typer.testing import CliRunner

from flocks.security import batch
from flocks.security.batch_worker import extract_source
from flocks.cli.commands.security import security_app


def make_batch(tmp_path, monkeypatch, count=1, concurrency=30):
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    monkeypatch.setattr(batch, "registry_root", lambda: tmp_path / "registry")
    source = tmp_path / "input"
    for index in range(count):
        directory = source / str(index)
        directory.mkdir(parents=True)
        (directory / "description.txt").write_text("Review this source")
        (directory / "repo-vul.tar.gz").write_bytes(b"archive")
    return batch.prepare_batch(
        source,
        run_dir=tmp_path / "run",
        concurrency=concurrency,
        model="test/model",
        poc=True,
        task_timeout=10,
        max_snapshot_bytes=1024,
    )


FAKE_WORKER = """
import json, os, sys, time
from pathlib import Path
from flocks.security.batch import atomic_json, file_lock, read_json
root, task_id, attempt = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
directory = root / "tasks" / task_id
with file_lock(directory / "task.lock"):
    current = read_json(directory / "current.json")
    assert current["attempt"] == attempt
    current["pid"] = os.getpid()
    atomic_json(directory / "current.json", current)
    # Independent data roots, one inner worker, real concurrently open SQLite databases.
    import sqlite3
    data = Path(os.environ["FLOCKS_DATA_DIR"])
    data.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(data / "flocks.db") as db:
        db.execute("create table if not exists marker (task text)")
        db.execute("insert into marker values (?)", (task_id,))
    assert os.environ["FLOCKS_CODE_SECURITY_WORKERS"] == "1"
    plugin = Path(os.environ["FLOCKS_CODE_SECURITY_ROOT"])
    plugin.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(plugin / "scan.db") as db:
        db.execute("create table if not exists marker (task text)")
        db.execute("insert into marker values (?)", (task_id,))
    def event(kind):
        while True:
            try:
                with file_lock(root / "test.lock"):
                    with (root / "events").open("a") as stream:
                        stream.write(json.dumps([kind, task_id]) + "\\n")
                break
            except BlockingIOError: time.sleep(.005)
    event("start")
    time.sleep(.5)
    event("end")
    status = "failed" if task_id == "2" else "completed"
    atomic_json(directory / "result.json", {"attempt": attempt, "status": status,
        "cleanup_status": "completed", "source_cleanup_status": "completed"})
"""


@pytest.mark.asyncio
async def test_thirty_real_process_slots_refill_and_isolate_databases(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch, 37)
    script = tmp_path / "worker.py"
    script.write_text(FAKE_WORKER)
    monkeypatch.setattr(
        batch, "_worker_command", lambda root, key, attempt: [sys.executable, str(script), str(root), key, attempt]
    )
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["counts"] == {"completed": 36, "failed": 1}
    active, peak = set(), 0
    for line in (root / "events").read_text().splitlines():
        event, task = json.loads(line)
        if event == "start":
            assert task not in active
            active.add(task)
            peak = max(peak, len(active))
        else:
            active.remove(task)
    assert not active
    assert 2 <= peak <= 30
    before = (root / "events").read_text()
    await batch.run_batch(root, progress=lambda _: None)
    assert (root / "events").read_text() == before
    await batch.run_batch(root, retry_failed=True, progress=lambda _: None)
    assert len((root / "events").read_text().splitlines()) == 2 * 38
    import sqlite3

    for index in range(37):
        assert not (root / f"tasks/{index}/data/flocks").exists()
        with sqlite3.connect(root / f"tasks/{index}/data/code-security/scan.db") as db:
            assert {row[0] for row in db.execute("select task from marker")} == {str(index)}


def test_lock_is_process_owned_and_released_on_exit(tmp_path):
    lock = tmp_path / "run.lock"
    with batch.file_lock(lock):
        with pytest.raises(BlockingIOError):
            with batch.file_lock(lock):
                pass
    assert lock.exists()
    with batch.file_lock(lock):
        pass


@pytest.mark.asyncio
async def test_resume_adopts_live_child_without_duplicate(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    task_dir = batch.resolve_task(root, "0")
    batch.atomic_json(task_dir / "current.json", {"attempt": "old"})
    script = """
import sys, time
from pathlib import Path
from flocks.security.batch import file_lock, atomic_json
root = Path(sys.argv[1])
with file_lock(root / "task.lock"):
 print("ready", flush=True)
 time.sleep(.7)
 atomic_json(root / "result.json", {"attempt":"old", "status":"completed", "cleanup_status":"completed", "source_cleanup_status":"completed"})
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(task_dir)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        monkeypatch.setattr(batch, "_worker_command", lambda *_: pytest.fail("Must not relaunch a live task"))
        result = await batch.run_batch(root, progress=lambda _: None)
        assert result["counts"] == {"completed": 1}
    finally:
        child.wait(timeout=5)


def test_registration_rejects_task_traversal_and_symlinks(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / "batch.json")
    assert batch.resolve_batch(config["batch_id"]) == root
    with pytest.raises(ValueError):
        batch.resolve_task(root, "../0")
    (root / "tasks").mkdir()
    (root / "tasks/0").symlink_to(tmp_path)
    with pytest.raises(ValueError):
        batch.resolve_task(root, "0")


@pytest.mark.parametrize("name,link,size", [("../escape", None, 1), ("link", "/etc/passwd", 0), ("big", None, 100)])
def test_archive_rejects_escape_links_and_expansion_limit(tmp_path, name, link, size):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        member = tarfile.TarInfo(name)
        member.size = size
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = link
        out.addfile(member, io.BytesIO(b"x" * size) if not link else None)
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises((tarfile.FilterError, ValueError)):
        extract_source(archive, source, 10)
    assert not (tmp_path / "escape").exists()


def test_batch_cli_is_registered_and_validates_inputs():
    runner = CliRunner()
    assert runner.invoke(security_app, ["batch", "--help"]).exit_code == 0
    assert "resume" in runner.invoke(security_app, ["batch", "--help"]).output
    assert runner.invoke(security_app, ["batch", "run", ".", "--concurrency", "0"]).exit_code != 0


@pytest.mark.asyncio
async def test_pending_cancel_never_launches_a_process(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    batch.request_cancel(batch.resolve_task(root, "0"))
    monkeypatch.setattr(batch, "_worker_command", lambda *_: pytest.fail("Cancelled task must not start"))
    assert (await batch.run_batch(root, progress=lambda _: None))["counts"] == {"cancelled": 1}


@pytest.mark.asyncio
async def test_timeout_reaps_process_and_continues_queue(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch, 2, concurrency=1)
    config = batch.read_json(root / "batch.json")
    config["task_timeout"] = 1
    batch.atomic_json(root / "batch.json", config)
    monkeypatch.setattr(batch, "_worker_command", lambda *_: [sys.executable, "-c", "import time; time.sleep(60)"])
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["counts"] == {"timed_out": 2}
    assert all(not batch.task_running(batch.resolve_task(root, key)) for key in ("0", "1"))


def test_archive_materializes_internal_links_without_losing_files(tmp_path):
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        member = tarfile.TarInfo("src/main.c")
        member.size = 4
        out.addfile(member, io.BytesIO(b"code"))
        link = tarfile.TarInfo("alias.c")
        link.type, link.linkname = tarfile.SYMTYPE, "src/main.c"
        out.addfile(link)
    source = tmp_path / "source"
    source.mkdir()
    extract_source(archive, source, 20)
    assert (source / "alias.c").read_bytes() == b"code"
    assert not (source / "alias.c").is_symlink()
    assert (source / "src/main.c").read_bytes() == b"code"


@pytest.mark.asyncio
async def test_scheduler_cancellation_stops_child_and_leaves_resumable_state(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    script = tmp_path / "long_worker.py"
    script.write_text("""
import sys, time, os
from pathlib import Path
from flocks.security.batch import file_lock, atomic_json, read_json
root = Path(sys.argv[1]) / "tasks/0"
with file_lock(root / "task.lock"):
 current = read_json(root / "current.json")
 current["pid"] = os.getpid()
 atomic_json(root / "current.json", current)
 (root / "ready").touch()
 time.sleep(60)
""")
    monkeypatch.setattr(batch, "_worker_command", lambda root, *_: [sys.executable, str(script), str(root)])
    running = asyncio.create_task(batch.run_batch(root, progress=lambda _: None))
    try:
        async with asyncio.timeout(5):
            while not (root / "tasks/0/ready").exists():
                await asyncio.sleep(0.02)
        pid = batch.read_json(root / "tasks/0/current.json")["pid"]
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert batch.batch_status(root)["counts"] == {"interrupted": 1}
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.asyncio
async def test_cleanup_after_abrupt_worker_exit_and_repeated_clean(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    script = tmp_path / "crash.py"
    script.write_text("""
import os, sys, tempfile
from pathlib import Path
from flocks.security.batch import read_json, atomic_json, file_lock
root = Path(sys.argv[1]) / "tasks/0"
with file_lock(root / "task.lock"):
 work = Path(tempfile.mkdtemp(prefix="flocks-batch-"))
 (work / ".batch-owner").write_text(str(root))
 (work / "source").mkdir()
 (work / "source/main.c").write_text("code")
 (work / "source").chmod(0o500)
 current = read_json(root / "current.json")
 current["work_dir"] = str(work)
 atomic_json(root / "current.json", current)
 (root / "test-work-path").write_text(str(work))
 for name in ("data/flocks", "data/code-security/runtime", "data/code-security/data/snapshots"):
  directory = root / name
  directory.mkdir(parents=True)
  (directory / "intermediate").write_text("scratch")
 os._exit(9)
""")
    monkeypatch.setattr(batch, "_worker_command", lambda root, *_: [sys.executable, str(script), str(root)])
    result = await batch.run_batch(root, progress=lambda _: None)
    task = root / "tasks/0"
    assert result["counts"] == {"interrupted": 1}
    assert result["tasks"][0]["cleanup_status"] == "completed"
    assert not Path((task / "test-work-path").read_text()).exists()
    for name in (
        "data/flocks",
        "data/code-security/runtime",
        "data/code-security/data/snapshots",
        "stdout.log",
        "stderr.log",
    ):
        assert not (task / name).exists()
    await batch.clean_batch(root)
    assert batch.read_json(task / "current.json") == {
        "attempt": batch.read_json(task / "result.json")["attempt"],
        "started_at": batch.read_json(task / "current.json")["started_at"],
    }


@pytest.mark.asyncio
async def test_clean_skips_live_worker_and_rejects_external_tree(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    task = batch.resolve_task(root, "0")
    batch.atomic_json(task / "current.json", {"attempt": "test"})
    scratch = task / "data/flocks"
    scratch.mkdir(parents=True)
    (scratch / "keep").touch()
    with batch.file_lock(task / "task.lock"):
        await batch.clean_batch(root)
    assert (scratch / "keep").exists()
    import shutil

    shutil.rmtree(scratch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").touch()
    scratch.symlink_to(outside)
    result = await batch.clean_batch(root)
    assert result["tasks"][0]["cleanup_status"] == "failed"
    assert (outside / "keep").exists()
    scratch.unlink()
    await batch.clean_batch(root)
    assert batch.read_json(task / "result.json")["cleanup_status"] == "completed"


@pytest.mark.asyncio
async def test_cancel_during_cleanup_keeps_ownership_until_filesystem_work_stops(tmp_path, monkeypatch):
    import threading

    root = make_batch(tmp_path, monkeypatch)
    task = batch.resolve_task(root, "0")
    batch.atomic_json(task / "current.json", {"attempt": "test"})
    started, release = threading.Event(), threading.Event()

    def slow_cleanup(*_):
        started.set()
        assert release.wait(5)

    monkeypatch.setattr(batch, "_cleanup_child_work", slow_cleanup)
    cleaning = asyncio.create_task(batch.clean_batch(root))
    try:
        async with asyncio.timeout(5):
            while not started.is_set():
                await asyncio.sleep(0.01)
        cleaning.cancel()
        await asyncio.sleep(0)
        assert not cleaning.done()
        with pytest.raises(BlockingIOError):
            with batch.file_lock(root / "run.lock"):
                pass
        with pytest.raises(BlockingIOError):
            with batch.file_lock(task / "task.lock"):
                pass
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await cleaning
    assert not batch.task_running(task)
