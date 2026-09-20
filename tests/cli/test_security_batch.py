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
@pytest.mark.parametrize("quota", [False, True])
async def test_provider_quota_stops_dispatch_but_allows_explicit_resume(tmp_path, monkeypatch, quota):
    root = make_batch(tmp_path, monkeypatch, count=4, concurrency=1)
    batch.request_cancel(batch.resolve_task(root, "3"))
    script = tmp_path / "quota_worker.py"
    script.write_text('''
import sys
from pathlib import Path
from flocks.security.batch import atomic_json
root, key, attempt, quota = sys.argv[1:]
directory = Path(root) / "tasks" / key
atomic_json(directory / "result.json", {
    "attempt": attempt, "status": "failed" if key == "0" else "completed",
    "failure_code": "model_quota_exhausted" if quota == "True" and key == "0" else "audit_execution_failed",
    "cleanup_status": "completed", "source_cleanup_status": "completed",
})
''')
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [
        sys.executable, str(script), str(root), key, attempt, str(quota),
    ])
    progress = []
    result = await batch.run_batch(root, progress=progress.append)
    assert result["counts"] == {"failed": 1, "pending" if quota else "completed": 2, "cancelled": 1}
    assert any("quota exhausted" in item for item in progress) == quota
    if quota:
        assert not (root / "tasks/1/current.json").exists()
        quota = False
        result = await batch.run_batch(root, retry_failed=True, progress=lambda _: None)
        assert result["counts"] == {"failed": 1, "completed": 3}


@pytest.mark.asyncio
@pytest.mark.parametrize("quota", [False, True])
async def test_resume_adopts_live_child_without_duplicate(tmp_path, monkeypatch, quota):
    root = make_batch(tmp_path, monkeypatch, count=2 if quota else 1, concurrency=1)
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
 atomic_json(root / "result.json", {"attempt":"old", "status":"failed" if sys.argv[2] == "True" else "completed",
   "failure_code": "model_quota_exhausted" if sys.argv[2] == "True" else None,
   "cleanup_status":"completed", "source_cleanup_status":"completed"})
"""
    child = subprocess.Popen([sys.executable, "-c", script, str(task_dir), str(quota)], stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == "ready"
        monkeypatch.setattr(batch, "_worker_command", lambda *_: pytest.fail("Must not relaunch a live task"))
        result = await batch.run_batch(root, progress=lambda _: None)
        assert result["counts"] == ({"failed": 1, "pending": 1} if quota else {"completed": 1})
    finally:
        child.wait(timeout=5)


@pytest.mark.asyncio
async def test_quota_stop_keeps_inflight_worker_and_cleanup(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch, count=4, concurrency=2)
    script = tmp_path / "inflight_worker.py"
    script.write_text('''
import sys, time
from pathlib import Path
from flocks.security.batch import atomic_json
root, key, attempt = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if key == "0":
    while not (root / "second-started").exists(): time.sleep(.01)
else:
    assert key == "1", "Pending worker must not start after quota stop"
    (root / "second-started").touch()
    while not (root / "quota-observed").exists(): time.sleep(.01)
atomic_json(root / "tasks" / key / "result.json", {
    "attempt": attempt, "status": "failed" if key == "0" else "completed",
    "failure_code": "model_quota_exhausted" if key == "0" else None,
    "cleanup_status": "completed", "source_cleanup_status": "completed",
})
''')
    monkeypatch.setattr(batch, "_worker_command", lambda root, key, attempt: [
        sys.executable, str(script), str(root), key, attempt,
    ])

    def progress(message):
        if "quota exhausted" in message:
            (root / "quota-observed").touch()

    result = await asyncio.wait_for(batch.run_batch(root, progress=progress), 10)
    assert result["counts"] == {"failed": 1, "completed": 1, "pending": 2}
    for key in ("0", "1"):
        saved = batch.task_result(batch.resolve_task(root, key))
        assert saved["cleanup_status"] == "completed"
        assert saved["source_cleanup_status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["fresh", "adopted", "recovered", "launch_error"])
async def test_quota_reconciled_during_cleanup_stops_dispatch(tmp_path, monkeypatch, source):
    from unittest.mock import AsyncMock

    root = make_batch(tmp_path, monkeypatch, count=2, concurrency=1)
    launched = []

    def command(root, key, attempt):
        launched.append(key)
        return [sys.executable, "-c", "pass"]

    async def reconcile(task_dir, result):
        # Simulate the scan failure restored by batch_cleanup.reconcile after
        # a worker exits before writing its final result.json.
        result.update(status="failed", failure_code="model_quota_exhausted",
                      cleanup_status="completed", source_cleanup_status="completed")
        batch.atomic_json(task_dir / "result.json", result)

    monkeypatch.setattr(batch, "_worker_command", command)
    monkeypatch.setattr(batch, "cleanup_child_work", reconcile)
    if source in {"adopted", "recovered"}:
        task_dir = batch.resolve_task(root, "0")
        batch.atomic_json(task_dir / "current.json", {"attempt": "old"})
        batch.atomic_json(task_dir / "result.json", {"attempt": "old", "status": "interrupted"})
        state = batch.read_json(root / "state.json")
        state["tasks"]["0"] = {"status": "running" if source == "adopted" else "interrupted"}
        batch.atomic_json(root / "state.json", state)
    elif source == "launch_error":
        monkeypatch.setattr(batch.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("spawn failed")))
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["counts"] == {"failed": 1, "pending": 1}
    assert launched == ([] if source in {"adopted", "recovered"} else ["0"])
    assert not (root / "tasks/1/current.json").exists()


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


def dynamic_manifest():
    return {
        "task_id": "arvo:0",
        "task_kind": "arvo",
        "vulnerable_runner": "fixture:latest",
        "target_binary": "/out/target",
        "argv_template": ["{input}"],
        "input_path": "/scratch/input",
        "fuzzer_supported": True,
        "fuzzer_target": "/out/target",
        "input_contract": {},
        "gdb_supported": True,
        "limits": {"fuzz_seconds": 10, "gdb_seconds": 5},
    }


def test_dynamic_batch_freezes_validated_manifest_and_checks_images(tmp_path, monkeypatch):
    from flocks.security import batch_dynamic

    root = make_batch(tmp_path, monkeypatch)
    source = tmp_path / "input"
    manifest_file = source / "0/cybergym.json"
    manifest_file.write_text(json.dumps(dynamic_manifest()))
    checked = []
    monkeypatch.setattr(batch_dynamic, "preflight", lambda manifests: checked.extend(manifests))
    dynamic_root = batch.prepare_batch(
        source,
        run_dir=tmp_path / "dynamic",
        concurrency=30,
        model=None,
        poc=False,
        task_timeout=60,
        max_snapshot_bytes=1024,
        dynamic=True,
        dynamic_concurrency=2,
    )
    manifest_file.write_text("changed")
    config = batch.read_json(dynamic_root / "batch.json")
    assert config["dynamic"] and config["poc"]
    assert config["dynamic_concurrency"] == 2
    assert config["tasks"]["0"]["cybergym_manifest"]["limits"]["fuzz_seconds"] == 10
    assert checked[0]["gdb_supported"]
    assert not batch.read_json(root / "batch.json")["dynamic"]


@pytest.mark.parametrize("change", [{"gdb_supported": False}, {"limits": {"fuzz_seconds": 99999}}])
def test_dynamic_manifest_rejects_disabled_capabilities_and_unbounded_execution(tmp_path, change):
    from flocks.security.batch_dynamic import load_manifest

    path = tmp_path / "cybergym.json"
    path.write_text(json.dumps({**dynamic_manifest(), **change}))
    with pytest.raises(ValueError):
        load_manifest(path)


@pytest.mark.asyncio
async def test_dynamic_slots_bound_real_worker_processes_and_reclaim_crashed_leases(tmp_path, monkeypatch):
    from flocks.security import batch_dynamic

    root = make_batch(tmp_path, monkeypatch, 8, concurrency=8)
    config = batch.read_json(root / "batch.json")
    config.update(dynamic=True, dynamic_concurrency=2)
    batch.atomic_json(root / "batch.json", config)
    monkeypatch.setattr(batch_dynamic, "remove_containers", lambda *_: None)
    script = tmp_path / "dynamic_worker.py"
    script.write_text("""
import asyncio, json, os, sys, time
from pathlib import Path
from flocks.security import batch_dynamic
from flocks.security.batch import atomic_json, file_lock
root, key, attempt = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
task = root / "tasks" / key

def event(kind, name):
 while True:
  try:
   with file_lock(root / "events.lock"):
    with (root / "dynamic.events").open("a") as stream:
     stream.write(json.dumps([kind, name, key]) + "\\n")
   return
  except BlockingIOError: time.sleep(.005)

def remove(owner, name=None): event("remove", name)
batch_dynamic.remove_containers = remove
async def run():
 async with batch_dynamic.container_slot(["docker", "run", "--rm", "fixture:latest"]) as command:
  assert "--label" in command and "--name" in command
  name = command[command.index("--name") + 1]
  event("start", name)
  if key == "0": os._exit(9)
  await asyncio.sleep(.1)
with file_lock(task / "task.lock"):
 asyncio.run(run())
 atomic_json(task / "result.json", {"attempt": attempt, "status": "completed", "cleanup_status": "completed"})
""")
    monkeypatch.setattr(
        batch, "_worker_command", lambda root, key, attempt: [sys.executable, str(script), str(root), key, attempt]
    )
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result["counts"] == {"interrupted": 1, "completed": 7}
    # Acquire again even if the crashing worker happened to run last.
    monkeypatch.setenv("FLOCKS_CODE_SECURITY_BATCH_TASK", str(root / "tasks/1"))
    removed = []
    monkeypatch.setattr(batch_dynamic, "remove_containers", lambda owner, name=None: removed.append(name))
    async with batch_dynamic.container_slot(["docker", "run", "fixture:latest"]):
        pass
    active, peak, crashed = set(), 0, None
    for kind, name, key in map(json.loads, (root / "dynamic.events").read_text().splitlines()):
        if kind == "start":
            active.add(name)
            peak = max(peak, len(active))
            if key == "0":
                crashed = name
        else:
            active.discard(name)
    assert peak <= 2
    assert crashed not in active or crashed in removed


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["--rm", "image", "target", "--name", "input"], None),
        (["--name=owned", "image", "--name", "input"], "owned"),
        (["--network", "none", "--user", "1000:1000", "--name", "owned", "image", "--name", "input"], "owned"),
    ],
)
def test_container_name_ignores_target_arguments(arguments, expected):
    from flocks.security.batch_dynamic import docker_container_name

    assert docker_container_name(["docker", "run", *arguments]) == expected


@pytest.mark.asyncio
async def test_failed_container_removal_retains_scratch_until_task_cleanup(tmp_path, monkeypatch):
    from flocks.security import batch_dynamic

    root = make_batch(tmp_path, monkeypatch)
    task = batch.resolve_task(root, "0")
    config = batch.read_json(root / "batch.json")
    config.update(dynamic=True, dynamic_concurrency=1)
    batch.atomic_json(root / "batch.json", config)
    batch.atomic_json(task / "current.json", {"attempt": "one"})
    monkeypatch.setenv("FLOCKS_CODE_SECURITY_BATCH_TASK", str(task))

    def unavailable(*_):
        raise RuntimeError("Docker cleanup unavailable")

    monkeypatch.setattr(batch_dynamic, "remove_containers", unavailable)
    with pytest.raises(RuntimeError, match="cleanup unavailable"):
        with batch_dynamic.scratch_directory(prefix="cybergym-fuzz-") as directory:
            scratch = Path(directory)
            (scratch / "crash").write_bytes(b"crashing input")
            async with batch_dynamic.container_slot(["docker", "run", "image", "target", "--name", "input"]) as command:
                assert batch_dynamic.docker_container_name(command).startswith("flocks-batch-")
    assert (scratch / "crash").exists()
    assert (root / "dynamic-slots/0").exists()
    monkeypatch.setattr(batch_dynamic, "remove_containers", lambda *_: None)
    result = {"attempt": "one", "status": "failed"}
    await batch.cleanup_child_work(task, result)
    assert result["cleanup_status"] == "completed"
    assert not scratch.exists()


@pytest.mark.asyncio
async def test_clean_completed_dynamic_task_does_not_require_docker(tmp_path, monkeypatch):
    from flocks.security import batch_dynamic

    root = make_batch(tmp_path, monkeypatch)
    task = batch.resolve_task(root, "0")
    config = batch.read_json(root / "batch.json")
    config["dynamic"] = True
    batch.atomic_json(root / "batch.json", config)
    batch.atomic_json(task / "current.json", {"attempt": "one"})
    result = {
        "attempt": "one",
        "status": "completed",
        "cleanup_status": "completed",
        "source_cleanup_status": "completed",
    }
    batch.atomic_json(task / "result.json", result)
    monkeypatch.setattr(
        batch_dynamic, "remove_containers", lambda *_: pytest.fail("Completed cleanup must work offline")
    )
    cleaned = await batch.clean_batch(root)
    assert cleaned["counts"] == {"completed": 1}
    assert cleaned["tasks"][0]["cleanup_status"] == "completed"


def test_named_container_cleanup_escapes_name_filter(tmp_path, monkeypatch):
    from flocks.security import batch_dynamic

    calls = []
    monkeypatch.setattr(batch_dynamic, "docker", lambda *args: calls.append(args) or "")
    batch_dynamic.remove_containers("batch:0", "runner.v1")
    assert "name=^/runner\\.v1$" in calls[0]
    assert "label=flocks.batch.task=batch:0" in calls[0]


def _archive_with_link(tmp_path, entries):
    archive = tmp_path / "links.tar.gz"
    with tarfile.open(archive, "w:gz") as out:
        source = tarfile.TarInfo("main.c")
        source.size = 4
        out.addfile(source, io.BytesIO(b"code"))
        for name, target, kind in entries:
            entry = tarfile.TarInfo(name)
            entry.type = kind
            entry.linkname = target
            out.addfile(entry)
    destination = tmp_path / "source"
    destination.mkdir()
    return archive, destination


def test_explicit_external_symlink_exclusion_never_materializes_host_file(tmp_path):
    outside = tmp_path / "host-secret"
    outside.write_text("must not enter source")
    archive, source = _archive_with_link(tmp_path, [
        ("./install-sh", str(outside), tarfile.SYMTYPE),
        ("alias.c", "main.c", tarfile.SYMTYPE),
    ])
    result = extract_source(archive, source, 100, skip_external_symlinks={"install-sh": str(outside)})
    assert result == [{"path": "install-sh", "target": str(outside), "reason": "external_symlink"}]
    assert not (source / "install-sh").exists()
    assert (source / "alias.c").read_bytes() == b"code"
    assert outside.read_text() == "must not enter source"


@pytest.mark.parametrize("entries,policy", [
    ([("install-sh", "/etc/passwd", tarfile.SYMTYPE)], {"install-sh": "/usr/share/install-sh"}),
    ([("../install-sh", "/usr/share/install-sh", tarfile.SYMTYPE)], {"install-sh": "/usr/share/install-sh"}),
    ([("install-sh", "/usr/share/install-sh", tarfile.LNKTYPE)], {"install-sh": "/usr/share/install-sh"}),
    ([("install-sh", "/usr/share/install-sh", tarfile.SYMTYPE), ("alias", "install-sh", tarfile.SYMTYPE)], {"install-sh": "/usr/share/install-sh"}),
    ([("install-sh", "/usr/share/install-sh", tarfile.SYMTYPE), ("alias", "install-sh", tarfile.LNKTYPE)], {"install-sh": "/usr/share/install-sh"}),
    ([("install-sh", "/usr/share/install-sh", tarfile.SYMTYPE), ("install-sh/child", "main.c", tarfile.SYMTYPE)], {"install-sh": "/usr/share/install-sh"}),
])
def test_exclusion_does_not_relax_other_archive_guards(tmp_path, entries, policy):
    archive, source = _archive_with_link(tmp_path, entries)
    with pytest.raises((ValueError, tarfile.FilterError)):
        extract_source(archive, source, 100, skip_external_symlinks=policy)
    assert not list(source.iterdir())


def test_batch_freezes_exact_link_policy_and_cli_exposes_option(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    second = batch.prepare_batch(tmp_path / "input", run_dir=tmp_path / "second",
        concurrency=1, task_timeout=10, model=None, poc=False, max_snapshot_bytes=1024,
        skip_external_symlinks=["./install-sh=/usr/share/install-sh"])
    assert batch.read_json(second / "batch.json")["skip_external_symlinks"] == {"install-sh": "/usr/share/install-sh"}
    assert batch.read_json(root / "batch.json")["skip_external_symlinks"] == {}
    from flocks.cli.commands import security_batch
    monkeypatch.setattr(security_batch, "_run", lambda *_: None)
    result = CliRunner().invoke(security_app, ["batch", "run", str(tmp_path / "input"),
        "--task-timeout", "10", "--run-dir", str(tmp_path / "cli"), "--skip-external-symlink", "install-sh=/usr/share/install-sh"])
    assert result.exit_code == 0, result.stdout
    assert batch.read_json(tmp_path / "cli/batch.json")["skip_external_symlinks"] == {"install-sh": "/usr/share/install-sh"}


@pytest.mark.parametrize("rule", ["/absolute=/target", "../outside=/target", "name=relative", "name", "name\\child=/target"])
def test_invalid_link_exclusion_rules_are_rejected(rule):
    with pytest.raises(ValueError):
        batch.parse_link_exclusions([rule])


@pytest.mark.asyncio
@pytest.mark.parametrize("deletion_status", ["deleted", "deleting"])
async def test_deleted_tasks_are_not_restarted_or_listed(tmp_path, monkeypatch, deletion_status):
    root = make_batch(tmp_path, monkeypatch)
    task = batch.resolve_task(root, '0')
    batch.atomic_json(task / 'deleted.json', {'status': deletion_status})
    monkeypatch.setattr(batch, '_worker_command', lambda *_: pytest.fail('Deleted task restarted'))
    result = await batch.run_batch(root, retry_failed=True, progress=lambda _: None)
    assert len(result['tasks']) == (0 if deletion_status == 'deleted' else 1)
    assert len((await batch.clean_batch(root))['tasks']) == len(result['tasks'])


@pytest.mark.parametrize('auto', [False, True])
def test_automatic_exclusion_handles_all_five_build_links(tmp_path, auto):
    names = ['install-sh', 'depcomp', 'missing', 'ylwrap', 'compile']
    archive, source = _archive_with_link(tmp_path, [
        (name, '/usr/share/automake-1.15/' + name, tarfile.SYMTYPE) for name in names
    ])
    if not auto:
        with pytest.raises(tarfile.AbsoluteLinkError):
            extract_source(archive, source, 100)
        assert not list(source.iterdir())
        return
    exclusions = extract_source(archive, source, 100, auto_exclude_external_symlinks=True)
    assert {item['path'] for item in exclusions} == set(names)
    assert all(item['reason'] == 'external_symlink_auto' for item in exclusions)
    assert list(source.iterdir()) == [source / 'main.c']
    assert (source / 'main.c').read_bytes() == b'code'


@pytest.mark.parametrize('entries', [
    [('link', '../escape', tarfile.SYMTYPE)],
    [('link', '/etc/passwd', tarfile.LNKTYPE)],
    [('../escape', '/etc/passwd', tarfile.SYMTYPE)],
    [('link', '/etc/passwd', tarfile.SYMTYPE), ('alias', 'link', tarfile.SYMTYPE)],
    [('link', '/one', tarfile.SYMTYPE), ('link', '/two', tarfile.SYMTYPE)],
])
def test_automatic_mode_keeps_other_rejections(tmp_path, entries):
    archive, source = _archive_with_link(tmp_path, entries)
    with pytest.raises((ValueError, tarfile.FilterError)):
        extract_source(archive, source, 100, auto_exclude_external_symlinks=True)
    assert not list(source.iterdir())


def test_automatic_mode_is_frozen_by_cli(tmp_path, monkeypatch):
    make_batch(tmp_path, monkeypatch)
    from flocks.cli.commands import security_batch
    monkeypatch.setattr(security_batch, '_run', lambda *_: None)
    result = CliRunner().invoke(security_app, ['batch', 'run', str(tmp_path / 'input'),
        '--task-timeout', '10', '--run-dir', str(tmp_path / 'auto'), '--auto-exclude-external-symlinks'])
    assert result.exit_code == 0, result.stdout
    assert batch.read_json(tmp_path / 'auto/batch.json')['auto_exclude_external_symlinks'] is True
    assert batch.read_json(tmp_path / 'run/batch.json')['auto_exclude_external_symlinks'] is False


@pytest.mark.parametrize("name,target", [
    ("src-vul/skia/tools/gyp", "../third_party/externals/gyp/"),
    ("src-vul/botan/.travis.yml", "src-vul/scripts/ci/travis.yml"),
])
def test_archive_excludes_broken_internal_links_without_guessing_target(tmp_path, name, target):
    archive, source = _archive_with_link(tmp_path, [
        (name, target, tarfile.SYMTYPE),
        ("src-vul/botan/src/scripts/ci/travis.yml", "", tarfile.REGTYPE),
        ("src-vul/skia/third_party/externals/angle2/gyp", "", tarfile.DIRTYPE),
        ("alias.c", "main.c", tarfile.SYMTYPE),
    ])
    exclusions = extract_source(archive, source, 100)
    assert len(exclusions) == 1
    assert exclusions[0]["path"] == name
    # tarfile may normalize the trailing slash on newer Python versions.
    assert exclusions[0]["target"].rstrip("/") == target.rstrip("/")
    assert exclusions[0]["reason"] == "broken_internal_symlink"
    assert not (source / name).exists()
    assert not (source / name).is_symlink()
    assert (source / "alias.c").read_text() == "code"
    assert (source / "src-vul/botan/src/scripts/ci/travis.yml").is_file()


def test_archive_records_broken_links_in_materialized_directory_aliases(tmp_path):
    archive, source = _archive_with_link(tmp_path, [
        ("directory/broken", "missing", tarfile.SYMTYPE),
        ("alias", "directory", tarfile.SYMTYPE),
        ("chain", "directory/broken", tarfile.SYMTYPE),
    ])
    exclusions = extract_source(archive, source, 100)
    assert [item["path"] for item in exclusions] == ["alias/broken", "chain", "directory/broken"]
    assert all(item["reason"] == "broken_internal_symlink" for item in exclusions)
    assert not any(path.is_symlink() for path in source.rglob("*"))


@pytest.mark.parametrize("entries", [
    [("loop", "loop", tarfile.SYMTYPE)],
    [("a", "b", tarfile.SYMTYPE), ("b", "a", tarfile.SYMTYPE)],
    [("directory/loop", "..", tarfile.SYMTYPE)],
    [("broken", "../missing", tarfile.SYMTYPE)],
    [("a", "b", tarfile.SYMTYPE), ("b", "../missing", tarfile.SYMTYPE)],
])
def test_broken_link_exclusion_still_rejects_cycles_and_missing_external_targets(tmp_path, entries):
    archive, source = _archive_with_link(tmp_path, entries)
    with pytest.raises((ValueError, tarfile.FilterError)):
        extract_source(archive, source, 100)


def test_broken_link_exclusion_metadata_is_bounded(tmp_path):
    archive, source = _archive_with_link(tmp_path, [
        (f"broken-{index}", "missing", tarfile.SYMTYPE) for index in range(129)
    ])
    with pytest.raises(ValueError, match="metadata exceeds limit"):
        extract_source(archive, source, 100)


@pytest.mark.asyncio
async def test_stop_process_reaps_leader_when_group_already_exited(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock(return_value=0))
    monkeypatch.setattr(batch.os, 'killpg', lambda *args: (_ for _ in ()).throw(ProcessLookupError()))
    await batch.stop_process(process)
    process.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_process_tolerates_exit_during_hard_kill(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    process = SimpleNamespace(pid=123, returncode=None, wait=AsyncMock(side_effect=[asyncio.TimeoutError(), 0]))
    kill = Mock(side_effect=[None, ProcessLookupError(), ProcessLookupError()])
    monkeypatch.setattr(batch.os, 'killpg', kill)
    await batch.stop_process(process)
    assert process.wait.await_count == 2


def test_reconciled_business_failure_is_not_overwritten_by_timeout():
    from flocks.security.batch_worker import merge_scan_summary
    result = {'status': 'timed_out', 'termination_reason': 'timed_out', 'error': 'no worker result'}
    merge_scan_summary(result, {'status': 'failed', 'audit_status': 'failed', 'failure_code': 'audit_execution_failed', 'error': 'Missing adjudication'})
    assert result['status'] == 'failed'
    assert result['error'] == 'Missing adjudication'
    assert result['termination_reason'] == 'timed_out'


def test_cancelled_database_state_preserves_timeout_reason():
    from flocks.security.batch_worker import merge_scan_summary
    result = {'status': 'timed_out'}
    merge_scan_summary(result, {'status': 'failed', 'audit_status': 'cancelled', 'error': None})
    assert result['status'] == 'timed_out'


def test_cleanup_keeps_owner_on_partial_failure_and_can_retry(tmp_path, monkeypatch):
    from flocks.security import batch_worker
    monkeypatch.setattr(batch_worker.tempfile, 'gettempdir', lambda: str(tmp_path))
    work = tmp_path / 'flocks-batch-test'
    work.mkdir()
    task = tmp_path / 'task'
    (work / '.batch-owner').write_text(str(task))
    (work / 'source').mkdir()
    remove = batch_worker.shutil.rmtree
    monkeypatch.setattr(batch_worker.shutil, 'rmtree', lambda path: (_ for _ in ()).throw(OSError('busy')))
    with pytest.raises(OSError):
        batch_worker.cleanup_work(str(work), task)
    assert (work / '.batch-owner').read_text() == str(task)
    monkeypatch.setattr(batch_worker.shutil, 'rmtree', remove)
    batch_worker.cleanup_work(str(work), task)
    batch_worker.cleanup_work(str(work), task)
    assert not work.exists()


def test_cleanup_still_refuses_existing_directory_without_owner(tmp_path, monkeypatch):
    from flocks.security import batch_worker
    monkeypatch.setattr(batch_worker.tempfile, 'gettempdir', lambda: str(tmp_path))
    work = tmp_path / 'flocks-batch-test'
    work.mkdir()
    with pytest.raises(FileNotFoundError):
        batch_worker.cleanup_work(str(work), tmp_path / 'task')
    assert work.exists()


@pytest.mark.asyncio
async def test_timeout_allows_worker_result_and_preserves_failure(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / 'batch.json')
    config['task_timeout'] = 1
    batch.atomic_json(root / 'batch.json', config)
    script = '''
import json, sys, time
from pathlib import Path
folder, attempt = Path(sys.argv[1]), sys.argv[2]
while not (folder / 'cancel.json').exists():
    time.sleep(.02)
(folder / 'result.json').write_text(json.dumps({'attempt': attempt, 'status': 'failed', 'audit_status': 'failed', 'error': 'Missing adjudication'}))
'''
    monkeypatch.setattr(batch, '_worker_command', lambda root, task, attempt: [sys.executable, '-c', script, str(batch.resolve_task(root, task)), attempt])
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result['counts'] == {'failed': 1}
    saved = batch.task_result(batch.resolve_task(root, '0'))
    assert saved['termination_reason'] == 'timed_out'
    assert saved['error'] == 'Missing adjudication'


@pytest.mark.asyncio
async def test_worker_deadline_without_scheduler_or_cancel_file(tmp_path):
    from flocks.security.batch_worker import wait_for_cancel
    import time
    assert await wait_for_cancel(tmp_path, 'attempt', time.time() - 2, 1) == 'timed_out'


@pytest.mark.asyncio
async def test_worker_ignores_old_attempt_cancel(tmp_path):
    from flocks.security.batch_worker import wait_for_cancel
    import time
    batch.atomic_json(tmp_path / 'cancel.json', {'attempt': 'old'})
    assert await wait_for_cancel(tmp_path, 'new', time.time() - 2, 1) == 'timed_out'


@pytest.mark.asyncio
async def test_timeout_does_not_treat_cancelled_error_as_business_failure(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / 'batch.json')
    config['task_timeout'] = 1
    batch.atomic_json(root / 'batch.json', config)
    script = '''
import json, sys, time
from pathlib import Path
folder, attempt = Path(sys.argv[1]), sys.argv[2]
while not (folder / 'cancel.json').exists():
    time.sleep(.02)
(folder / 'result.json').write_text(json.dumps({'attempt': attempt, 'status': 'failed', 'error': 'CancelledError'}))
'''
    monkeypatch.setattr(batch, '_worker_command', lambda root, task, attempt: [sys.executable, '-c', script, str(batch.resolve_task(root, task)), attempt])
    result = await batch.run_batch(root, progress=lambda _: None)
    assert result['counts'] == {'timed_out': 1}
    assert batch.task_result(batch.resolve_task(root, '0'))['error'] == 'CancelledError'


@pytest.mark.asyncio
async def test_timeout_preserves_snapshot_before_worker_cleanup(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / 'batch.json')
    config['task_timeout'] = 1
    batch.atomic_json(root / 'batch.json', config)
    script = '''
import sys, time
from pathlib import Path
from flocks.security.batch import atomic_json
folder, attempt = Path(sys.argv[1]), sys.argv[2]
atomic_json(folder / 'runtime.json', {'attempt': attempt, 'phase': 'verification',
    'observed_at': time.time(), 'workers': [{'execution': {'step': 71, 'state': 'executing_tool', 'active_tools': {'bash': 1}}}]})
while not (folder / 'cancel.json').exists():
    time.sleep(.02)
# Emulate an uninstrumented cleanup overwriting transient runtime state.
atomic_json(folder / 'runtime.json', {'attempt': attempt, 'phase': 'cleanup'})
atomic_json(folder / 'result.json', {'attempt': attempt, 'status': 'cancelled'})
'''
    monkeypatch.setattr(batch, '_worker_command', lambda root, task, attempt: [sys.executable, '-c', script, str(batch.resolve_task(root, task)), attempt])
    messages = []
    result = await batch.run_batch(root, progress=messages.append)
    runtime = result['tasks'][0]['runtime']
    assert runtime['phase'] == 'verification'
    assert runtime['termination_reason'] == 'timed_out'
    assert runtime['workers'][0]['execution']['step'] == 71
    task = batch.resolve_task(root, '0')
    assert batch.read_json(task / 'runtime.json') == runtime
    assert batch.task_result(task)['runtime'] == runtime
    assert not (task / 'stdout.log').exists()
    assert json.loads(messages[-1])['runtime'] == runtime


@pytest.mark.asyncio
async def test_worker_freezes_diagnostics_before_cancelling_audit(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import time
    from flocks.cli.commands import security
    from flocks.security import batch_worker

    root = make_batch(tmp_path, monkeypatch)
    config = batch.read_json(root / 'batch.json')
    config['task_timeout'] = 0.3
    batch.atomic_json(root / 'batch.json', config)
    task_dir = batch.resolve_task(root, '0')
    batch.atomic_json(task_dir / 'current.json', {'attempt': 'attempt', 'started_at': time.time()})
    store = SimpleNamespace(get_scan=lambda _: {}, list_worker_batches=lambda _: [])
    monkeypatch.setitem(sys.modules, 'flocks_code_security.runtime', SimpleNamespace(get_runtime=lambda: SimpleNamespace(store=store)))
    monkeypatch.setattr(batch_worker, 'extract_source', lambda *a, **kw: [])
    monkeypatch.setattr(batch_worker, 'scan_summary', lambda *a: {'status': 'failed', 'audit_status': 'cancelled'})

    async def audit(source, *, progress, **kwargs):
        progress('scan.prepared', {'scan_id': 'scan'})
        progress('adjudication.started', {'current_phase': 'adjudication'})
        try:
            await asyncio.Future()
        finally:
            frozen = batch.read_json(task_dir / 'runtime.json')
            assert frozen['termination_reason'] == 'timed_out'
            assert frozen['phase'] == 'adjudication'
            progress('scan.cancelled', {})

    monkeypatch.setattr(security, '_load_plugin_cli', lambda: (audit, None))
    result = await batch_worker.execute(root, '0', 'attempt')
    assert result['status'] == 'timed_out'
    assert result['runtime']['phase'] == 'adjudication'
    assert batch.read_json(task_dir / 'runtime.json') == result['runtime']
    batch_worker.cleanup_work(batch.read_json(task_dir / 'current.json')['work_dir'], task_dir)


def test_runtime_write_failure_does_not_block_result_or_cleanup(tmp_path, monkeypatch):
    root = make_batch(tmp_path, monkeypatch)
    task_dir = batch.resolve_task(root, '0')
    batch.atomic_json(task_dir / 'current.json', {'attempt': 'attempt'})
    intermediate = task_dir / 'data/flocks'
    intermediate.mkdir(parents=True)
    (intermediate / 'temporary').write_text('remove me')
    original_write = batch.atomic_json

    def fail_runtime(path, value):
        if path.name == 'runtime.json':
            raise OSError('diagnostics unavailable')
        original_write(path, value)

    monkeypatch.setattr(batch, 'atomic_json', fail_runtime)
    result = {'attempt': 'attempt', 'status': 'timed_out', 'runtime': {'attempt': 'attempt'}}
    batch._cleanup_child_work(task_dir, result)
    assert result['status'] == 'timed_out'
    assert result['cleanup_status'] == 'completed'
    assert result['runtime']['diagnostics_error'] == 'OSError'
    assert batch.task_result(task_dir) == result
    assert not intermediate.exists()


@pytest.mark.parametrize('error', [OSError('busy'), KeyboardInterrupt()])
def test_cleanup_can_resume_after_owner_marker_removal(tmp_path, monkeypatch, error):
    from flocks.security import batch_worker
    monkeypatch.setattr(batch_worker.tempfile, 'gettempdir', lambda: str(tmp_path))
    work, task = tmp_path / 'flocks-batch-final', tmp_path / 'task'
    work.mkdir()
    (work / '.batch-owner').write_text(str(task))
    original = Path.rmdir
    def fail_final(path):
        if path == work:
            raise error
        return original(path)
    monkeypatch.setattr(Path, 'rmdir', fail_final)
    with pytest.raises(type(error)):
        batch_worker.cleanup_work(str(work), task)
    assert not (work / '.batch-owner').exists()
    assert (task / '.cleanup-flocks-batch-final.json').exists()
    monkeypatch.setattr(Path, 'rmdir', original)
    batch_worker.cleanup_work(str(work), task)
    assert not work.exists()
    assert not list(task.glob('.cleanup-*'))


def test_cleanup_receipt_never_authorizes_nonempty_or_different_directory(tmp_path, monkeypatch):
    from flocks.security import batch_worker
    monkeypatch.setattr(batch_worker.tempfile, 'gettempdir', lambda: str(tmp_path))
    work, task = tmp_path / 'flocks-batch-final', tmp_path / 'task'
    work.mkdir()
    info = work.stat()
    receipt = task / '.cleanup-flocks-batch-final.json'
    batch.atomic_json(receipt, {'path': str(work.resolve()), 'device': info.st_dev, 'inode': info.st_ino + 1})
    with pytest.raises(FileNotFoundError):
        batch_worker.cleanup_work(str(work), task)
    batch.atomic_json(receipt, {'path': str(work.resolve()), 'device': info.st_dev, 'inode': info.st_ino})
    (work / 'unrelated').write_text('keep')
    with pytest.raises(OSError):
        batch_worker.cleanup_work(str(work), task)
    assert (work / 'unrelated').read_text() == 'keep'


def test_worker_shutdown_error_preserves_saved_business_result(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from flocks.security import batch_worker
    from flocks.cli.commands import security
    batch.atomic_json(tmp_path / 'current.json', {'attempt': 'current'})
    batch.atomic_json(tmp_path / 'batch.json', {'task_timeout': 30})
    async def execute(*args):
        result = {'attempt': 'current', 'status': 'completed', 'audit_status': 'completed'}
        batch.atomic_json(tmp_path / 'result.json', result)
        return result
    async def close_failure(runner, root):
        await runner(root)
        raise asyncio.CancelledError()
    async def cleanup(*args): pass
    monkeypatch.setattr(sys, 'argv', ['worker', str(tmp_path), 'task', 'current'])
    monkeypatch.setattr(batch_worker, 'resolve_task', lambda *args: tmp_path)
    monkeypatch.setattr(batch_worker, 'file_lock', lambda *args, **kwargs: nullcontext())
    monkeypatch.setattr(batch_worker, 'execute', execute)
    monkeypatch.setattr(security, '_run_audit_with_cleanup', close_failure)
    monkeypatch.setattr(batch, 'cleanup_child_work', cleanup)
    with pytest.raises(SystemExit) as exited:
        batch_worker.main()
    result = batch.read_json(tmp_path / 'result.json')
    assert exited.value.code == 0
    assert result['status'] == 'completed'
    assert 'CancelledError' in result['cleanup_error']


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['getpgid', 'killpg'])
async def test_adopted_process_exit_race_still_checks_task_lock(tmp_path, monkeypatch, operation):
    from unittest.mock import Mock, AsyncMock
    batch.atomic_json(tmp_path / 'current.json', {'attempt': 'current', 'pid': 123, 'process_identity': 'identity'})
    running = Mock(side_effect=[True] * 101 + [False])
    monkeypatch.setattr(batch, 'task_running', running)
    monkeypatch.setattr(batch, 'process_identity', lambda pid: 'identity')
    monkeypatch.setattr(batch.asyncio, 'sleep', AsyncMock())
    monkeypatch.setattr(batch.os, 'getpgid', Mock(return_value=123))
    monkeypatch.setattr(batch.os, 'killpg', Mock())
    monkeypatch.setattr(batch.os, operation, Mock(side_effect=ProcessLookupError()))
    await batch.stop_adopted(tmp_path)
    assert running.call_count == 102


def test_cleanup_failure_does_not_erase_reconciled_business_failure(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import Mock
    from flocks.security import batch_cleanup, batch_worker
    source = Path(__file__).resolve().parents[2] / '.flocks/plugins/flocks-code-security/src'
    monkeypatch.syspath_prepend(str(source))
    from flocks_code_security import store as store_module
    batch.atomic_json(tmp_path / 'current.json', {'attempt': 'current', 'scan_id': 'scan'})
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [{'scan_id': 'scan', 'status': 'failed'}]
    store = Mock()
    store.assert_cybergym_runs_terminal = Mock()
    store._connect.return_value = nullcontext(connection)
    store.prune_scan_execution_history.side_effect = OSError('cleanup failed')
    monkeypatch.setattr(store_module, 'ScanStore', lambda path: store)
    monkeypatch.setattr(batch_cleanup, 'read_batch_config', lambda path: {})
    monkeypatch.setattr(batch_worker, 'scan_summary', lambda *args: {'status': 'failed', 'audit_status': 'failed', 'error': 'Missing adjudication'})
    result = {'attempt': 'current', 'status': 'timed_out'}
    with pytest.raises(OSError):
        batch_cleanup.reconcile(tmp_path, result)
    persisted = batch.read_json(tmp_path / 'result.json')
    assert persisted['status'] == 'failed'
    assert persisted['error'] == 'Missing adjudication'


def test_cancellation_resistant_worker_exits_and_persists_result(tmp_path):
    script = '''
import asyncio, sys
from pathlib import Path
from flocks.security.batch_worker import stop_audit_task
async def stubborn():
    while True:
        try: await asyncio.sleep(1)
        except asyncio.CancelledError: pass
async def main():
    task = asyncio.create_task(stubborn())
    await asyncio.sleep(0)
    await stop_audit_task(task, Path(sys.argv[1]), {'attempt':'current','status':'timed_out'})
asyncio.run(main())
'''
    process = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], start_new_session=True)
    try:
        assert process.wait(timeout=20) != 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    saved = batch.read_json(tmp_path / 'result.json')
    assert saved['status'] == 'timed_out'
    assert saved['cleanup_status'] == 'failed'


@pytest.mark.parametrize('target', ['.', 'loop'])
def test_cycles_can_be_explicitly_excluded_without_losing_siblings(tmp_path, target):
    archive = tmp_path / 'source.tar.gz'
    with tarfile.open(archive, 'w:gz') as output:
        file = tarfile.TarInfo('main.c')
        file.size = 4
        output.addfile(file, io.BytesIO(b'code'))
        link = tarfile.TarInfo('loop')
        link.type = tarfile.SYMTYPE
        link.linkname = target
        output.addfile(link)
    strict = tmp_path / 'strict'
    strict.mkdir()
    with pytest.raises(ValueError, match='cyclic|Cyclic'):
        extract_source(archive, strict, 100)
    relaxed = tmp_path / 'relaxed'
    relaxed.mkdir()
    exclusions = extract_source(archive, relaxed, 100, exclude_cyclic_symlinks=True)
    assert (relaxed / 'main.c').read_text() == 'code'
    assert not (relaxed / 'loop').exists()
    assert exclusions == [{'path': 'loop', 'target': target, 'reason': 'cyclic_symlink'}]


@pytest.mark.parametrize('audit_status', ['running', 'failed', 'cancelled', 'completed'])
def test_worker_quota_survives_stale_scan_summary(audit_status):
    from flocks.security.batch_worker import merge_scan_summary
    from flocks.session.lifecycle.retry import MODEL_QUOTA_EXHAUSTED

    result = {'status': 'failed', 'failure_code': MODEL_QUOTA_EXHAUSTED, 'error': 'provider quota exhausted'}
    merge_scan_summary(result, {
        'status': 'completed' if audit_status == 'completed' else 'failed',
        'audit_status': audit_status, 'failure_code': None, 'error': None,
    })
    if audit_status == 'completed':
        assert result['status'] == 'completed'
        assert result['failure_code'] is None
        assert 'error' not in result
    else:
        assert result['status'] == 'failed'
        assert result['failure_code'] == MODEL_QUOTA_EXHAUSTED
        assert result['error'] == 'provider quota exhausted'


@pytest.mark.asyncio
@pytest.mark.parametrize('summary_unavailable', [False, True])
async def test_worker_serializes_quota_without_database_failure_code(tmp_path, monkeypatch, summary_unavailable):
    import sqlite3
    import time
    from types import SimpleNamespace
    from flocks.cli.commands import security
    from flocks.security import batch_worker
    from flocks.session.lifecycle.retry import MODEL_QUOTA_EXHAUSTED, ModelQuotaExhaustedError

    root = make_batch(tmp_path, monkeypatch)
    task_dir = batch.resolve_task(root, '0')
    batch.atomic_json(task_dir / 'current.json', {'attempt': 'attempt', 'started_at': time.time()})

    def scan_status(_):
        if summary_unavailable:
            raise sqlite3.OperationalError('database is locked')
        return {'status': 'running', 'integrity_status': 'pending', 'counts': {}, 'failure_code': None}

    store = SimpleNamespace(get_scan=lambda _: {}, list_worker_batches=lambda _: [], scan_status=scan_status)
    monkeypatch.setitem(sys.modules, 'flocks_code_security.runtime', SimpleNamespace(get_runtime=lambda: SimpleNamespace(store=store)))
    monkeypatch.setattr(batch_worker, 'extract_source', lambda *a, **kw: [])

    async def audit(source, *, progress, **kwargs):
        progress('scan.prepared', {'scan_id': 'scan'})
        raise ModelQuotaExhaustedError('provider quota exhausted')

    monkeypatch.setattr(security, '_load_plugin_cli', lambda: (audit, None))
    try:
        result = await batch_worker.execute(root, '0', 'attempt')
        assert result['status'] == 'failed'
        assert result['failure_code'] == MODEL_QUOTA_EXHAUSTED
        assert batch.read_json(task_dir / 'result.json')['failure_code'] == MODEL_QUOTA_EXHAUSTED
    finally:
        batch_worker.cleanup_work(batch.read_json(task_dir / 'current.json')['work_dir'], task_dir)
