"""Local batch scheduling: one writer for state, one process/database per task."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time
from typing import Iterator
from uuid import uuid4

from flocks.utils.process_identity import process_identity


UI_PATH = "/contracts/webui/workspaces/code_security/code-security-workspace"


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


@contextmanager
def file_lock(path: Path, *, blocking: bool = False) -> Iterator[None]:
    """Kernel lock. A leftover file is not a held lock."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise BlockingIOError(f"Already running: {path.parent}") from exc
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def task_running(task_dir: Path) -> bool:
    try:
        with file_lock(task_dir / "task.lock"):
            return False
    except BlockingIOError:
        return True


def registry_root() -> Path:
    return Path.home() / ".flocks/workspace/code-security/batches"


def resolve_batch(batch_id: str) -> Path:
    if not re.fullmatch(r"batch_[a-f0-9]{32}", batch_id):
        raise ValueError("Invalid batch ID")
    root = Path(read_json(registry_root() / f"{batch_id}.json")["run_dir"]).resolve()
    if read_json(root / "batch.json")["batch_id"] != batch_id:
        raise ValueError("Batch registration mismatch")
    return root


def resolve_task(root: Path, task_id: str, *, config: dict | None = None) -> Path:
    if not re.fullmatch(r"[0-9]+", task_id):
        raise ValueError("Invalid task ID")
    if task_id not in (config if config is not None else read_json(root / "batch.json"))["tasks"]:
        raise ValueError("Task not found")
    path = root / "tasks" / task_id
    if path.resolve() != path.absolute():
        raise ValueError("Task directory must not contain symbolic links")
    return path


def parse_link_exclusions(values: list[str]) -> dict[str, str]:
    """Exact archive-relative symlink paths and absolute link text, not globs."""
    from pathlib import PurePosixPath

    if len(values) > 128 or sum(len(value.encode("utf-8")) for value in values) > 16_000:
        raise ValueError("External symlink exclusions exceed the 128-entry/16-KiB limit")
    result = {}
    for value in values:
        name, separator, target = value.partition("=")
        path = PurePosixPath(name)
        if (not separator or not name or path.is_absolute() or ".." in path.parts
                or str(path) == "." or "\\" in name or name != name.strip()
                or not target.startswith("/") or any(ord(c) < 32 for c in value)):
            raise ValueError("Expected --skip-external-symlink ARCHIVE_RELATIVE_PATH=/absolute/link/target")
        name = path.as_posix()
        if name in result and result[name] != target:
            raise ValueError(f"Conflicting external symlink targets: {name}")
        result[name] = target
    return result


def prepare_batch(
    source: Path,
    *,
    run_dir: Path | None,
    concurrency: int,
    task_timeout: int,
    model: str | None,
    poc: bool,
    max_snapshot_bytes: int,
    dynamic: bool = False,
    bash_enabled: bool = False,
    web_search_enabled: bool = False,
    dynamic_concurrency: int = 2,
    skip_external_symlinks: list[str] | None = None,
    auto_exclude_external_symlinks: bool = False,
) -> Path:
    if concurrency < 1 or task_timeout < 1 or max_snapshot_bytes < 1 or dynamic_concurrency < 1:
        raise ValueError("Concurrency, timeout and size limit must be positive")
    link_exclusions = parse_link_exclusions(skip_external_symlinks or [])
    source = source.expanduser().resolve(strict=True)
    tasks = {}
    for directory in sorted(source.iterdir(), key=lambda p: (int(p.name) if p.name.isdecimal() else -1, p.name)):
        if not re.fullmatch(r"[0-9]+", directory.name) or not directory.is_dir():
            continue
        archive, description = directory / "repo-vul.tar.gz", directory / "description.txt"
        if not archive.is_file() or not description.is_file():
            continue
        if directory.is_symlink() or archive.is_symlink() or description.is_symlink():
            raise ValueError(f"Task {directory.name} contains symbolic input paths")
        if description.stat().st_size > 32 * 1024:
            raise ValueError(f"Task {directory.name}: description exceeds 32768 bytes")
        tasks[directory.name] = {
            "archive": str(archive),
            "description": str(description),
            "archive_size": archive.stat().st_size,
            "archive_mtime_ns": archive.stat().st_mtime_ns,
            "description_content": description.read_text(encoding="utf-8-sig"),
        }
        if not tasks[directory.name]["description_content"].strip() or description.stat().st_size > 32 * 1024:
            raise ValueError(f"Task {directory.name}: description must contain 1–32768 bytes")
    if not tasks:
        raise ValueError("No Arvo tasks containing repo-vul.tar.gz and description.txt were found")
    if dynamic:
        from flocks.security.batch_dynamic import load_manifest, preflight

        for key, task in tasks.items():
            task["cybergym_manifest"] = load_manifest(source / key / "cybergym.json")
        preflight([task["cybergym_manifest"] for task in tasks.values()])
    batch_id = f"batch_{uuid4().hex}"
    root = (
        (
            run_dir
            or Path.home()
            / ".flocks/workspace/outputs"
            / datetime.now().strftime("%Y-%m-%d")
            / "code-security-batch"
            / batch_id
        )
        .expanduser()
        .resolve()
    )
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with file_lock(root / "run.lock"):
        if (root / "batch.json").exists():
            raise ValueError("Run directory already contains a batch; use batch resume")
        atomic_json(
            root / "batch.json",
            {
                "version": 1,
                "batch_id": batch_id,
                "created_at": datetime.now().astimezone().isoformat(),
                "concurrency": concurrency,
                "task_timeout": task_timeout,
                "model": model,
                "poc": poc or dynamic,
                "dynamic": dynamic,
                "bash_enabled": bash_enabled,
                "web_search_enabled": web_search_enabled,
                "dynamic_concurrency": dynamic_concurrency,
                "max_snapshot_bytes": max_snapshot_bytes,
                "skip_external_symlinks": link_exclusions,
                "auto_exclude_external_symlinks": auto_exclude_external_symlinks,
                "tasks": tasks,
            },
        )
        atomic_json(root / "state.json", {"tasks": {key: {"status": "pending"} for key in tasks}})
        atomic_json(registry_root() / f"{batch_id}.json", {"run_dir": str(root)})
    return root


def task_result(task_dir: Path) -> dict | None:
    path = task_dir / "result.json"
    if not path.exists():
        return None
    result = read_json(path)
    current = read_json(task_dir / "current.json")
    return result if result.get("attempt") == current.get("attempt") else None


def batch_status(root: Path) -> dict:
    config = read_json(root / "batch.json")
    state = read_json(root / "state.json")
    items = []
    for task_id in config["tasks"]:
        task_dir = resolve_task(root, task_id, config=config)
        if (task_dir / "deleted.json").exists() and read_json(task_dir / "deleted.json").get("status") == "deleted":
            continue
        item = dict(state["tasks"].get(task_id, {"status": "pending"}))
        result = task_result(task_dir) if (task_dir / "current.json").exists() else None
        if result:
            item.update(result)
        elif item["status"] == "running" and not task_running(task_dir):
            item["status"] = "interrupted"
        current = read_json(task_dir / "current.json") if (task_dir / "current.json").exists() else {}
        item.update(
            task_id=task_id,
            scan_id=item.get("scan_id") or current.get("scan_id"),
            ui_url=f"{UI_PATH}?batch_id={config['batch_id']}&task_id={task_id}",
        )
        items.append(item)
    return {
        "batch_id": config["batch_id"],
        "run_dir": str(root),
        "concurrency": config["concurrency"],
        "counts": dict(Counter(item["status"] for item in items)),
        "tasks": items,
    }


def delete_batch_task(task_dir: Path) -> None:
    """Delete only a terminal task under its worker lock; never modify batch inputs."""
    from flocks.security.batch_cleanup import remove_owned_tree
    from flocks.security.batch_worker import cleanup_work
    from flocks_code_security.store import ScanStore
    from flocks_code_security.paths import outputs_root
    from flocks_code_security.service import _remove_owned_tree

    with file_lock(task_dir / "task.lock"):
        marker = task_dir / "deleted.json"
        if marker.exists() and read_json(marker).get("status") == "deleted":
            return
        current = read_json(task_dir / "current.json") if (task_dir / "current.json").exists() else {}
        result = task_result(task_dir) if current else None
        if result is None:
            result = read_json(task_dir.parents[1] / "state.json")["tasks"].get(task_dir.name, {})
        if result.get("status") not in {"completed", "failed", "cancelled", "interrupted", "timed_out"}:
            raise ValueError("Cancel the task and wait for it to stop before deleting it")
        try:
            with file_lock(task_dir.parents[1] / "run.lock"):
                pass
        except BlockingIOError:
            state = read_json(task_dir.parents[1] / "state.json")["tasks"].get(task_dir.name, {})
            if state.get("status") not in {"completed", "failed", "cancelled", "interrupted", "timed_out"}:
                raise ValueError("Task cleanup is still running; wait before deleting") from None
        database = task_dir / "data/code-security/data/code-security.db"
        if database.resolve() != database.absolute():
            raise ValueError("Refusing deletion through a symbolic database path")
        scans = []
        if database.exists():
            with ScanStore(database, read_only=True)._connect() as connection:
                scans = connection.execute("SELECT scan_id, status, output_dir FROM scans").fetchall()
            if any(scan["status"] not in {"completed", "failed", "cancelled", "interrupted"} for scan in scans):
                raise ValueError("Audit scan has not stopped; cancel it before deleting")
        # A durable intent prevents resume --retry-failed from restarting a half-deleted task.
        atomic_json(marker, {"status": "deleting"})
        config = read_json(task_dir.parents[1] / "batch.json")
        if config.get("dynamic") and result.get("cleanup_status") != "completed":
            from flocks.security.batch_dynamic import owner, remove_containers
            remove_containers(owner(task_dir))
        for work in {current.get("work_dir"), current.get("previous", {}).get("work_dir")} - {None}:
            cleanup_work(work, task_dir)
        for scan in scans:
            if scan["output_dir"]:
                _remove_owned_tree(Path(scan["output_dir"]), root=outputs_root(), expected_name=scan["scan_id"])
        remove_owned_tree(task_dir, "data")
        atomic_json(marker, {"status": "deleted"})
        for name in ("current.json", "result.json", "source-exclusions.json", "stdout.log", "stderr.log", "cleanup.log", "cancel.json"):
            (task_dir / name).unlink(missing_ok=True)


def request_cancel(task_dir: Path) -> None:
    current = read_json(task_dir / "current.json") if (task_dir / "current.json").exists() else {"attempt": "pending"}
    atomic_json(task_dir / "cancel.json", {"attempt": current["attempt"]})


async def stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T")
            await killer.wait()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), 20)
    except asyncio.TimeoutError:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            killer = await asyncio.create_subprocess_exec("taskkill", "/PID", str(process.pid), "/T", "/F")
            await killer.wait()
        await process.wait()
    # SIGTERM may stop the leader before an uncooperative tool subprocess exits.
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def cleanup_child_work(task_dir: Path, result: dict) -> None:
    """Finish cleanup before releasing ownership, even if the scheduler is cancelled."""
    operation = asyncio.create_task(asyncio.to_thread(_cleanup_child_work, task_dir, result))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise


def _cleanup_child_work(task_dir: Path, result: dict) -> None:
    """Caller owns task.lock or the just-reaped worker; never clean a live task."""
    from flocks.security.batch_worker import cleanup_work
    from flocks.security.batch_cleanup import remove_owned_tree, reconcile

    current = read_json(task_dir / "current.json")
    scan_id = current.get("scan_id") or current.get("previous", {}).get("scan_id")
    if scan_id:
        current["scan_id"] = scan_id
        result.setdefault("scan_id", scan_id)
    atomic_json(task_dir / "result.json", result)
    try:
        config = read_json(task_dir.parents[1] / "batch.json")
        if config.get("dynamic") and not (
            result.get("cleanup_status") == "completed" and result.get("source_cleanup_status") == "completed"
        ):
            from flocks.security.batch_dynamic import owner, remove_containers

            remove_containers(owner(task_dir))
        database = task_dir / "data/code-security/data/code-security.db"
        if (
            database.is_symlink()
            or database.resolve() != task_dir.resolve() / "data/code-security/data/code-security.db"
        ):
            raise ValueError("Refusing cleanup through a symbolic database path")
        if database.exists() and result.get("cleanup_status") != "completed":
            reconcile(task_dir, result)
        for work_dir in {current.get("work_dir"), current.get("previous", {}).get("work_dir")} - {None}:
            cleanup_work(work_dir, task_dir)
        result["source_cleanup_status"] = "completed"
        # The task's audit database and sealed outputs are retained for its UI.
        for relative in ("data/flocks", "data/code-security/runtime", "data/code-security/data/snapshots"):
            remove_owned_tree(task_dir, relative)
        result["cleanup_status"] = "completed"
        for name in ("stdout.log", "stderr.log", "cleanup.log", "cancel.json"):
            (task_dir / name).unlink(missing_ok=True)
        for key in ("work_dir", "previous", "pid", "process_identity"):
            current.pop(key, None)
        atomic_json(task_dir / "current.json", current)
        result.pop("cleanup_error", None)
    except Exception as exc:
        result.update(cleanup_status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
    atomic_json(task_dir / "result.json", result)


async def clean_batch(root: Path) -> dict:
    """Clean stopped tasks only, without retrying audits or interrupting live workers."""
    root = root.expanduser().resolve()
    with file_lock(root / "run.lock"):
        config = read_json(root / "batch.json")
        state = read_json(root / "state.json")
        for task_id in config["tasks"]:
            task_dir = resolve_task(root, task_id, config=config)
            try:
                with file_lock(task_dir / "task.lock"):
                    if (task_dir / "deleted.json").exists():
                        continue
                    if not (task_dir / "current.json").exists():
                        continue
                    current = read_json(task_dir / "current.json")
                    result = task_result(task_dir) or {"attempt": current["attempt"], "status": "interrupted"}
                    await cleanup_child_work(task_dir, result)
                    state["tasks"][task_id] = result
                    atomic_json(root / "state.json", state)
            except BlockingIOError:
                continue
    return batch_status(root)


async def stop_adopted(task_dir: Path) -> None:
    """Only signal a verified birth identity, never a bare PID from a stale file."""
    current = read_json(task_dir / "current.json")
    request_cancel(task_dir)
    for _ in range(100):
        if not task_running(task_dir):
            return
        await asyncio.sleep(0.2)
    pid, identity = current.get("pid"), current.get("process_identity")
    if not identity or process_identity(pid) != identity:
        raise RuntimeError("Cannot safely terminate adopted task: process identity is unavailable")
    if os.name == "posix":
        if os.getpgid(pid) != pid:
            raise RuntimeError("Adopted worker no longer owns its process group")
        os.killpg(pid, signal.SIGKILL)
    else:
        process = await asyncio.create_subprocess_exec("taskkill", "/PID", str(pid), "/T", "/F")
        await process.wait()
    for _ in range(50):
        if not task_running(task_dir):
            return
        await asyncio.sleep(0.1)
    raise RuntimeError("Adopted worker did not release its task lock")


def _worker_command(root: Path, task_id: str, attempt: str) -> list[str]:
    return [sys.executable, "-m", "flocks.security.batch_worker", str(root), task_id, attempt]


async def run_batch(root: Path, *, retry_failed: bool = False, progress=print) -> dict:
    root = root.expanduser().resolve()
    with file_lock(root / "run.lock"):
        config = read_json(root / "batch.json")
        state = read_json(root / "state.json")
        queue = asyncio.Queue()
        # Adopt running tasks before dispatching pending tasks, so they count toward the limit.
        ordered = sorted(config["tasks"], key=lambda key: not task_running(resolve_task(root, key, config=config)))
        for task_id in ordered:
            queue.put_nowait(task_id)

        def save(task_id: str, item: dict) -> None:
            state["tasks"][task_id] = item
            atomic_json(root / "state.json", state)
            progress(f"{task_id}: {item['status']}")

        async def run_one(task_id: str) -> None:
            task_dir = resolve_task(root, task_id, config=config)
            cancel_file = task_dir / "cancel.json"
            while True:
                # A child can survive a killed scheduler. Never launch a duplicate.
                try:
                    while task_running(task_dir):
                        current = read_json(task_dir / "current.json")
                        if (
                            current.get("started_at")
                            and time.time() - current["started_at"] > config["task_timeout"] + 60
                        ):
                            await stop_adopted(task_dir)
                            break
                        await asyncio.sleep(0.2)
                except asyncio.CancelledError:
                    await stop_adopted(task_dir)
                    result = task_result(task_dir) or {}
                    if result.get("status") != "completed":
                        result.update(status="interrupted", attempt=read_json(task_dir / "current.json")["attempt"])
                        atomic_json(task_dir / "result.json", result)
                    await cleanup_child_work(task_dir, result)
                    save(task_id, result)
                    raise
                try:
                    with file_lock(task_dir / "task.lock"):
                        if (task_dir / "deleted.json").exists():
                            return
                        if cancel_file.exists() and read_json(cancel_file).get("attempt") == "pending":
                            attempt = uuid4().hex
                            atomic_json(task_dir / "current.json", {"attempt": attempt})
                            result = {
                                "status": "cancelled",
                                "attempt": attempt,
                                "cleanup_status": "completed",
                                "source_cleanup_status": "completed",
                            }
                            atomic_json(task_dir / "result.json", result)
                            cancel_file.unlink()
                            save(task_id, result)
                            return
                        result = task_result(task_dir) if (task_dir / "current.json").exists() else None
                        if (task_dir / "current.json").exists():
                            result = result or {
                                "status": "interrupted",
                                "attempt": read_json(task_dir / "current.json")["attempt"],
                            }
                            await cleanup_child_work(task_dir, result)
                            if (
                                result.get("cleanup_status") == "failed"
                                or result.get("source_cleanup_status") == "failed"
                            ):
                                save(task_id, result)
                                return
                        if result and (
                            (
                                result["status"] == "completed"
                                and result.get("cleanup_status") == "completed"
                                and result.get("source_cleanup_status") == "completed"
                            )
                            or (result["status"] in {"failed", "timed_out", "cancelled"} and not retry_failed)
                        ):
                            save(task_id, result)
                            return
                        attempt = uuid4().hex
                        atomic_json(
                            task_dir / "current.json",
                            {"attempt": attempt, "started_at": time.time()},
                        )
                        save(task_id, {"status": "running", "attempt": attempt})
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.2)
            from flocks.security.batch_cleanup import task_environment

            environment = task_environment(task_dir)
            process = None
            termination_reason = None
            try:
                with (task_dir / "stdout.log").open("ab") as out, (task_dir / "stderr.log").open("ab") as err:
                    process = await asyncio.create_subprocess_exec(
                        *_worker_command(root, task_id, attempt),
                        env=environment,
                        stdout=out,
                        stderr=err,
                        start_new_session=(os.name == "posix"),
                    )
                    started = time.monotonic()
                    while process.returncode is None:
                        cancelled = cancel_file.exists() and read_json(cancel_file).get("attempt") in {
                            attempt,
                            "pending",
                        }
                        expired = time.monotonic() - started >= config["task_timeout"]
                        if cancelled or expired:
                            termination_reason = "cancelled" if cancelled else "timed_out"
                            await stop_process(process)
                            break
                        try:
                            await asyncio.wait_for(process.wait(), 0.2)
                        except asyncio.TimeoutError:
                            pass
                # A crashed leader can leave tool subprocesses behind in its group.
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                result = task_result(task_dir)
                if result is None:
                    result = {
                        "status": termination_reason or "interrupted",
                        "attempt": attempt,
                        "error": f"Worker exited without a result (exit {process.returncode}); resume to reconcile",
                    }
                elif termination_reason and result["status"] != "completed":
                    result["status"] = termination_reason
                await cleanup_child_work(task_dir, result)
            except asyncio.CancelledError:
                if process:
                    request_cancel(task_dir)
                    await stop_process(process)
                result = task_result(task_dir) or {}
                if result.get("status") != "completed":
                    result.update(status="interrupted", attempt=attempt)
                    atomic_json(task_dir / "result.json", result)
                await cleanup_child_work(task_dir, result)
                save(task_id, result)
                raise
            except Exception as exc:
                if process:
                    await stop_process(process)
                result = {"status": "failed", "attempt": attempt, "error": str(exc)}
                await cleanup_child_work(task_dir, result)
            save(task_id, result)

        async def worker() -> None:
            while not queue.empty():
                task_id = queue.get_nowait()
                try:
                    try:
                        await run_one(task_id)
                    except (ValueError, KeyError) as exc:
                        save(task_id, {"status": "failed", "error": str(exc)})
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(min(config["concurrency"], queue.qsize()))]
        try:
            await asyncio.gather(*workers)
        finally:
            for worker_task in workers:
                worker_task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
        return batch_status(root)
