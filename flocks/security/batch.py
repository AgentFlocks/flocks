"""Local batch scheduling: one writer for state, one process/database per task."""

from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import ExitStack, contextmanager
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
from flocks.session.lifecycle.retry import MODEL_QUOTA_EXHAUSTED
from flocks.security.batch_timeouts import (
    DEFAULT_PHASE_TIMEOUTS, ExecutionControlError, PhaseTimeout, enter_phase, read_control,
    timeout_details, validate_budgets,
)


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


def task_running(task_dir: Path, *, cleanup_only: bool = False) -> bool:
    try:
        with ExitStack() as locks:
            if not cleanup_only:
                locks.enter_context(file_lock(task_dir / "task.lock"))
            locks.enter_context(file_lock(task_dir / "cleanup.lock"))
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
    task_timeout: int | None = None,
    phase_timeouts: dict[str, int] | None = None,
    model: str | None,
    max_snapshot_bytes: int,
    dynamic: bool = False,
    dynamic_concurrency: int = 2,
    skip_external_symlinks: list[str] | None = None,
    auto_exclude_external_symlinks: bool = False,
    exclude_cyclic_symlinks: bool = False,
    max_snapshot_files: int = 50_000,
) -> Path:
    if phase_timeouts is None and task_timeout is None:
        phase_timeouts = DEFAULT_PHASE_TIMEOUTS
    if phase_timeouts is not None:
        if task_timeout is not None:
            raise ValueError("Choose phase_timeouts or legacy task_timeout, not both")
        phase_timeouts = validate_budgets(phase_timeouts, dynamic=dynamic)
    elif type(task_timeout) is not int or task_timeout < 1:
        raise ValueError("task_timeout must be a positive integer in seconds")
    if concurrency < 1 or max_snapshot_bytes < 1 or dynamic_concurrency < 1:
        raise ValueError("Concurrency, timeout and size limit must be positive")
    if type(max_snapshot_files) is not int or max_snapshot_files < 1:
        raise ValueError("max_snapshot_files must be a positive integer")
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
                "version": 2 if phase_timeouts is not None else 1,
                "batch_id": batch_id,
                "created_at": datetime.now().astimezone().isoformat(),
                "concurrency": concurrency,
                **({"phase_timeouts": phase_timeouts} if phase_timeouts is not None else {"task_timeout": task_timeout}),
                "model": model,
                "poc": True,
                "dynamic": dynamic,
                "dynamic_concurrency": dynamic_concurrency,
                "max_snapshot_bytes": max_snapshot_bytes,
                "skip_external_symlinks": link_exclusions,
                "auto_exclude_external_symlinks": auto_exclude_external_symlinks,
                "exclude_cyclic_symlinks": exclude_cyclic_symlinks,
                "max_snapshot_files": max_snapshot_files,
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
    from flocks.security.batch_diagnostics import read_runtime

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
        runtime = item.get("runtime") or read_runtime(task_dir, current.get("attempt"))
        if runtime:
            item["runtime"] = runtime
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

    with file_lock(task_dir / "task.lock"), file_lock(task_dir / "cleanup.lock"):
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
        for name in ("current.json", "result.json", "runtime.json", "source-exclusions.json", "stdout.log", "stderr.log", "cleanup.log", "cancel.json"):
            (task_dir / name).unlink(missing_ok=True)


def request_cancel(task_dir: Path, termination: dict | None = None) -> None:
    current = read_json(task_dir / "current.json") if (task_dir / "current.json").exists() else {"attempt": "pending"}
    request = {"attempt": current["attempt"]}
    if (task_dir / "cancel.json").exists():
        previous = read_json(task_dir / "cancel.json")
        if previous.get("attempt") == current["attempt"] and previous.get("termination"):
            termination = previous["termination"]
    if termination is not None:
        request["termination"] = termination
    atomic_json(task_dir / "cancel.json", request)


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
        await process.wait()
        return
    try:
        await asyncio.wait_for(process.wait(), 20)
    except asyncio.TimeoutError:
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
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
    config = read_json(task_dir.parents[1] / "batch.json")
    if "phase_timeouts" in config:
        await _cleanup_child_process(task_dir, result, config["phase_timeouts"])
        return
    operation = asyncio.create_task(asyncio.to_thread(_cleanup_child_work, task_dir, result))
    try:
        await asyncio.shield(operation)
    except asyncio.CancelledError:
        await operation
        raise


async def _cleanup_child_process(task_dir: Path, result: dict, budgets: dict) -> None:
    """Bound synchronous cleanup in a process; never leave a detached thread deleting files."""
    from flocks.security.batch_cleanup import CLEANUP_BUSY, task_environment

    current = read_json(task_dir / "current.json")
    if (result.get("cleanup_status") == "completed"
            and result.get("source_cleanup_status") == "completed" and not current.get("work_dir")):
        return
    process = None
    cleanup_started = False
    try:
        busy = False
        with ExitStack() as ownership:
            try:
                ownership.enter_context(file_lock(task_dir / "cleanup.lock"))
            except BlockingIOError:
                busy = True
            if not busy:
                # A recovering scheduler must not overwrite a live owner's PID
                # or result while that owner is completing cleanup.
                current = read_json(task_dir / "current.json")
                saved = task_result(task_dir)
                if (saved and saved.get("cleanup_status") == "completed"
                        and saved.get("source_cleanup_status") == "completed" and not current.get("work_dir")):
                    result.clear()
                    result.update(saved)
                    return
                enter_phase(current, budgets, "cleanup", after_exit=True)
                atomic_json(task_dir / "current.json", current)
                atomic_json(task_dir / "result.json", result)
        if not busy:
            remaining = max(0, current["phase_timeout"]["deadline"] - time.monotonic())
            cleanup_started = True
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "flocks.security.batch_cleanup", str(task_dir), current["attempt"],
                env=task_environment(task_dir), stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=(os.name == "posix"),
            )
            # Allow the child's exit notification to arrive after its timer fires.
            await asyncio.wait_for(process.wait(), remaining + 1)
            busy = process.returncode == CLEANUP_BUSY
        if busy:
            # A previously spawned child can acquire the lock during startup.
            # Adopt its existing budget rather than launching cleanup again.
            current = read_control(task_dir, current["attempt"], current)
            while task_running(task_dir, cleanup_only=True):
                if time.monotonic() >= current["phase_timeout"]["deadline"] + 1:
                    await stop_adopted(task_dir, cleanup_only=True)
                    break
                await asyncio.sleep(0.1)
        saved = task_result(task_dir)
        if saved:
            result.clear()
            result.update(saved)
        if busy:
            if result.get("cleanup_status") != "completed" or result.get("source_cleanup_status") != "completed":
                raise RuntimeError(result.get("cleanup_error") or "Existing cleanup exited without completing")
        elif process.returncode != 0:
            raise RuntimeError(f"Cleanup process exited with status {process.returncode}")
    except (asyncio.CancelledError, OSError, ValueError, RuntimeError) as exc:
        cleanup_started = cleanup_started or busy
        if process:
            await stop_process(process)
        if task_running(task_dir, cleanup_only=True):
            cleanup_started = True
            await stop_adopted(task_dir, cleanup_only=True)
        # Reconciliation may have committed a newer audit result before cleanup
        # stopped. Read it only after its writer exits, and discard stale fields.
        if cleanup_started:
            saved = task_result(task_dir)
            if saved:
                result.clear()
                result.update(saved)
        cancelled = isinstance(exc, asyncio.CancelledError)
        result.update(
            cleanup_status="failed",
            cleanup_error="Cleanup interrupted" if cancelled else f"{type(exc).__name__}: {exc}",
        )
        if cancelled:
            atomic_json(task_dir / "result.json", result)
            raise
    atomic_json(task_dir / "result.json", result)


def _cleanup_child_work(task_dir: Path, result: dict) -> None:
    """Caller owns task.lock or the just-reaped worker; never clean a live task."""
    from flocks.security.batch_worker import cleanup_work
    from flocks.security.batch_cleanup import remove_owned_tree, reconcile
    from flocks.security.batch_diagnostics import termination_snapshot, write_runtime

    current = read_json(task_dir / "current.json")
    if result.get("status") != "completed" and "runtime" not in result:
        result["runtime"] = termination_snapshot(task_dir, current["attempt"], result.get("status", "interrupted"))
    if result.get("runtime"):
        write_runtime(task_dir, result["runtime"])
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
                    # An explicit clean command may retry failed cleanup, but must
                    # never race a cleanup child left by a previous scheduler.
                    with file_lock(task_dir / "cleanup.lock"):
                        current = read_json(task_dir / "current.json")
                        if "phase_timeouts" in config:
                            previous = current.pop("phase_timeout", {})
                            enter_phase(current, config["phase_timeouts"], "cleanup")
                            current["phase_timeout"]["spent_seconds"] = {
                                phase: seconds for phase, seconds in previous.get("spent_seconds", {}).items()
                                if phase != "cleanup"
                            }
                            atomic_json(task_dir / "current.json", current)
                    result = task_result(task_dir) or {"attempt": current["attempt"], "status": "interrupted"}
                    await cleanup_child_work(task_dir, result)
                    state["tasks"][task_id] = result
                    atomic_json(root / "state.json", state)
            except BlockingIOError:
                continue
    return batch_status(root)


async def stop_adopted(task_dir: Path, termination: dict | None = None, *, cleanup_only: bool = False) -> None:
    """Only signal a verified birth identity, never a bare PID from a stale file."""
    current = read_json(task_dir / "current.json")
    request_cancel(task_dir, termination)
    # Cleanup has no cancellation watcher; signal its verified owner directly.
    for _ in range(0 if cleanup_only else 100):
        if not task_running(task_dir, cleanup_only=cleanup_only):
            return
        await asyncio.sleep(0.2)
    pid, identity = current.get("pid"), current.get("process_identity")
    if not task_running(task_dir, cleanup_only=cleanup_only):
        return
    if not identity or process_identity(pid) != identity:
        if not task_running(task_dir, cleanup_only=cleanup_only):
            return
        raise RuntimeError("Cannot safely terminate adopted task: process identity is unavailable")
    if os.name == "posix":
        try:
            if os.getpgid(pid) != pid:
                raise RuntimeError("Adopted worker no longer owns its process group")
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass  # Still verify release of the task lock below.
    else:
        process = await asyncio.create_subprocess_exec("taskkill", "/PID", str(pid), "/T", "/F")
        await process.wait()
    for _ in range(50):
        if not task_running(task_dir, cleanup_only=cleanup_only):
            return
        await asyncio.sleep(0.1)
    raise RuntimeError("Adopted worker did not release its task lock")


def _worker_command(root: Path, task_id: str, attempt: str) -> list[str]:
    return [sys.executable, "-m", "flocks.security.batch_worker", str(root), task_id, attempt]


def apply_termination(result: dict, termination: dict) -> None:
    """Record why execution stopped without replacing a known audit outcome."""
    reason = termination.get("failure_code") or termination["status"]
    result["termination_reason"] = reason
    if result.get("runtime"):
        result["runtime"]["termination_reason"] = reason
    # The service may persist failed/phase_timeout before the worker catches
    # that same timeout. Complete its details instead of treating it as a
    # separate business failure. A later cleanup timeout must not replace it.
    projected_timeout = (
        result.get("failure_code") == termination.get("failure_code") == PhaseTimeout.code
        and not result.get("timeout_phase")
        and termination.get("timeout_phase") != "cleanup"
    )
    known_failure = (
        result.get("audit_status") == "failed"
        or result.get("failure_code") in {
            MODEL_QUOTA_EXHAUSTED, PhaseTimeout.code, ExecutionControlError.code,
        }
    )
    if result.get("status") != "completed" and (projected_timeout or not known_failure):
        result.update(termination)
    if termination.get("timeout_phase") == "cleanup":
        result.update(cleanup_status="failed", cleanup_error="Cleanup budget exhausted")


async def run_batch(root: Path, *, retry_failed: bool = False, progress=print) -> dict:
    root = root.expanduser().resolve()
    with file_lock(root / "run.lock"):
        config = read_json(root / "batch.json")
        budgets = config.get("phase_timeouts")
        # Older batches could omit the PoC phase and its budget. New attempts
        # always generate PoCs, including when resuming those saved batches.
        if not config.get("poc", False):
            config["poc"] = True
            if budgets is not None:
                budgets.setdefault("poc_generation", DEFAULT_PHASE_TIMEOUTS["poc_generation"])
            atomic_json(root / "batch.json", config)
        if budgets is not None:
            validate_budgets(budgets, dynamic=config.get("dynamic", False))
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
            if item.get("runtime") and item["status"] != "running":
                progress(json.dumps({"event": "audit.termination_snapshot", "task_id": task_id,
                                     "runtime": item["runtime"]}, ensure_ascii=False))

        async def run_one(task_id: str) -> None:
            task_dir = resolve_task(root, task_id, config=config)
            cancel_file = task_dir / "cancel.json"
            adopted_timeout = None
            last_control = None
            while True:
                # A child can survive a killed scheduler. Never launch a duplicate.
                try:
                    while task_running(task_dir):
                        if budgets is not None:
                            if last_control is None:
                                last_control = read_json(task_dir / "current.json")
                            try:
                                last_control = read_control(task_dir, last_control["attempt"], last_control)
                                adopted_timeout = timeout_details(last_control)
                                if adopted_timeout:
                                    last_control = read_control(task_dir, last_control["attempt"], last_control)
                                    adopted_timeout = timeout_details(last_control)
                            except ExecutionControlError as exc:
                                adopted_timeout = {"status": "failed", "failure_code": exc.code, "error": str(exc)}
                            if adopted_timeout:
                                await stop_adopted(task_dir, adopted_timeout)
                                break
                            await asyncio.sleep(0.2)
                            continue
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
                    result.setdefault("attempt", read_json(task_dir / "current.json")["attempt"])
                    apply_termination(result, {"status": "interrupted"})
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
                            if adopted_timeout:
                                apply_termination(result, adopted_timeout)
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
                        with file_lock(task_dir / "cleanup.lock"):
                            attempt = uuid4().hex
                            last_control = {"attempt": attempt, "started_at": time.time()}
                            if budgets is not None:
                                enter_phase(last_control, budgets, "source_extraction")
                            atomic_json(task_dir / "current.json", last_control)
                            save(task_id, {"status": "running", "attempt": attempt})
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.2)
            from flocks.security.batch_cleanup import task_environment

            environment = task_environment(task_dir)
            process = None
            termination_reason = None
            phase_termination = None
            runtime_at_termination = None
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
                        if budgets is not None:
                            try:
                                last_control = read_control(task_dir, attempt, last_control)
                                phase_termination = timeout_details(last_control)
                                if phase_termination:
                                    last_control = read_control(task_dir, attempt, last_control)
                                    phase_termination = timeout_details(last_control)
                            except ExecutionControlError as exc:
                                phase_termination = {"status": "failed", "failure_code": exc.code, "error": str(exc)}
                            expired = phase_termination is not None
                        else:
                            expired = time.monotonic() - started >= config["task_timeout"]
                        if cancelled or expired:
                            from flocks.security.batch_diagnostics import termination_snapshot

                            termination_reason = "cancelled" if cancelled else (phase_termination["status"] if phase_termination else "timed_out")
                            runtime_at_termination = termination_snapshot(task_dir, attempt, termination_reason)
                            request_cancel(task_dir, phase_termination if not cancelled else None)
                            try:
                                await asyncio.wait_for(process.wait(), 20)
                            except asyncio.TimeoutError:
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
                if termination_reason:
                    result.setdefault("runtime", runtime_at_termination)
                    apply_termination(result, (
                        phase_termination if phase_termination and termination_reason != "cancelled"
                        else {"status": termination_reason}
                    ))
                await cleanup_child_work(task_dir, result)
            except asyncio.CancelledError:
                if process:
                    request_cancel(task_dir)
                    await stop_process(process)
                result = task_result(task_dir) or {}
                result.setdefault("attempt", attempt)
                apply_termination(result, {"status": "interrupted"})
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
