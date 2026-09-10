"""Private, process-isolated entrypoint used by ``security batch``."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tarfile
import tempfile
import time

from flocks.security.batch import atomic_json, file_lock, read_json, resolve_task
from flocks.utils.process_identity import process_identity


def extract_source(archive: Path, destination: Path, limit: int) -> None:
    """Validate the complete archive before extracting; never normalize by deleting links."""
    with tarfile.open(archive, "r:gz") as bundle:
        total = 0
        members = []
        for member in bundle:
            if len(members) >= 200_000:
                raise ValueError("Archive contains too many entries")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported archive entry: {member.name}")
            # data_filter rejects traversal and external links, and strips unsafe permissions.
            checked = tarfile.data_filter(member, str(destination))
            if checked is not None:
                total += max(0, checked.size)
                if total > limit:
                    raise ValueError("Archive exceeds --max-snapshot-bytes")
                members.append(checked)
        bundle.extractall(destination, members=members, filter="data")
    if any(path.is_symlink() for path in destination.rglob("*")):
        # The audit snapshot rejects symlinks. Materialize only internal, non-cyclic
        # links, bounding the expanded tree as well as the original archive.
        normalized = destination.with_name(destination.name + "-normalized")
        total, entries = 0, 0

        def copy_node(source: Path, target: Path, ancestors: frozenset[Path]) -> None:
            nonlocal total, entries
            resolved = source.resolve(strict=True)
            if not resolved.is_relative_to(destination.resolve()) or resolved in ancestors:
                raise ValueError(f"External or cyclic source link: {source.name}")
            entries += 1
            if entries > 200_000:
                raise ValueError("Expanded source contains too many entries")
            if resolved.is_dir():
                target.mkdir()
                for item in sorted(resolved.iterdir()):
                    copy_node(item, target / item.name, ancestors | {resolved})
            elif resolved.is_file():
                total += resolved.stat().st_size
                if total > limit:
                    raise ValueError("Expanded source exceeds --max-snapshot-bytes")
                shutil.copy2(resolved, target)
            else:
                raise ValueError(f"Unsupported source file: {source.name}")

        copy_node(destination, normalized, frozenset())
        shutil.rmtree(destination)
        normalized.rename(destination)


def cleanup_work(path: str | None, task_dir: Path) -> None:
    if not path:
        return
    root = Path(path)
    if not root.exists():
        return
    if root.is_symlink() or root.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        raise ValueError("Refusing cleanup outside batch temporary directory")
    if (root / ".batch-owner").read_text() != str(task_dir):
        raise ValueError("Temporary directory owner mismatch")
    root.chmod(0o700)
    for child in root.rglob("*"):
        if not child.is_symlink():
            child.chmod(0o700 if child.is_dir() else 0o600)
    shutil.rmtree(root)


def scan_summary(store, scan_id: str) -> dict:
    status = store.scan_status(scan_id)
    completed = status["status"] == "completed" and status["integrity_status"] == "valid"
    return {
        "status": "completed" if completed else "failed",
        "scan_id": scan_id,
        "audit_status": status["status"],
        "integrity_status": status["integrity_status"],
        "poc_count": status["counts"].get("poc_bundles", 0),
        "cleanup_status": json.loads(status.get("cleanup_summary_json", "{}")).get("status", "pending"),
        "output_dir": status.get("output_dir"),
        "error": status.get("failure_summary"),
    }


async def execute(root: Path, task_id: str, attempt: str) -> dict:
    from flocks.cli.commands.security import _load_plugin_cli, _read_knowledge_base

    config = read_json(root / "batch.json")
    task_dir = resolve_task(root, task_id)
    current = read_json(task_dir / "current.json")
    if current["attempt"] != attempt:
        raise ValueError("Superseded task attempt")
    current.update(pid=os.getpid(), process_identity=process_identity(os.getpid()))
    atomic_json(task_dir / "current.json", current)
    run_audit, _ = _load_plugin_cli()
    from flocks_code_security.runtime import get_runtime

    runtime = get_runtime()
    result: dict = {"status": "failed", "attempt": attempt, "cleanup_status": "pending"}
    scan_id = None
    try:
        task = config["tasks"][task_id]
        archive = Path(task["archive"])
        stat = archive.stat()
        if archive.is_symlink() or stat.st_size != task["archive_size"] or stat.st_mtime_ns != task["archive_mtime_ns"]:
            raise ValueError("Archive changed since batch creation")
        work = Path(tempfile.mkdtemp(prefix="flocks-batch-"))
        (work / ".batch-owner").write_text(str(task_dir))
        current["work_dir"] = str(work)
        atomic_json(task_dir / "current.json", current)
        source = work / "source"
        source.mkdir()
        # Extraction stays in this killable process, not an uninterruptible parent thread.
        extract_source(archive, source, config["max_snapshot_bytes"])
        description = work / "description.txt"
        description.write_text(task["description_content"], encoding="utf-8")

        def progress(event: str, payload: dict) -> None:
            nonlocal scan_id
            if event == "scan.prepared":
                scan_id = payload["scan_id"]
                current["scan_id"] = scan_id
                atomic_json(task_dir / "current.json", current)
            print(json.dumps({"event": event, **payload}, ensure_ascii=False, default=str), flush=True)

        async def audit():
            return await run_audit(
                source,
                model=config["model"],
                progress=progress,
                knowledge_base=_read_knowledge_base(description, audited_target=source),
                poc_enabled=config["poc"],
                copy_source=False,
                cleanup_intermediates=True,
                max_total_bytes=config["max_snapshot_bytes"],
            )

        audit_task = asyncio.create_task(audit())

        async def cancel_requested():
            while True:
                cancel = task_dir / "cancel.json"
                if cancel.exists() and read_json(cancel).get("attempt") in {attempt, "pending"}:
                    return "cancelled"
                if time.time() - current["started_at"] >= config["task_timeout"]:
                    return "timed_out"
                await asyncio.sleep(0.2)

        watcher = asyncio.create_task(cancel_requested())
        try:
            done, _ = await asyncio.wait({audit_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if audit_task in done:
                await audit_task
                result.update(scan_summary(runtime.store, scan_id))
            else:
                result["status"] = watcher.result()
                audit_task.cancel()
                await asyncio.gather(audit_task, return_exceptions=True)
        finally:
            watcher.cancel()
            audit_task.cancel()
            await asyncio.gather(watcher, audit_task, return_exceptions=True)
    except asyncio.CancelledError:
        result.update(status="cancelled", error="Audit cancelled")
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}: {exc}")
    finally:
        if scan_id:
            result["scan_id"] = scan_id
            try:
                latest = scan_summary(runtime.store, scan_id)
                result.update({key: value for key, value in latest.items() if key not in {"status", "error"}})
            except Exception as exc:
                result["state_error"] = str(exc)
        atomic_json(task_dir / "result.json", result)
    return result


def main() -> None:
    root, task_id, attempt = Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    task_dir = resolve_task(root, task_id)
    with file_lock(task_dir / "task.lock", blocking=True):
        if read_json(task_dir / "current.json")["attempt"] != attempt:
            return

        # SIGTERM requests normal coroutine cleanup; parent escalates if necessary.
        async def run():
            task = asyncio.current_task()
            loop = asyncio.get_running_loop()
            if os.name == "posix":
                loop.add_signal_handler(signal.SIGTERM, task.cancel)
            from flocks.cli.commands.security import _run_audit_with_cleanup

            async def owned_run(_target):
                return await execute(root, task_id, attempt)

            return await _run_audit_with_cleanup(owned_run, root)

        try:
            result = asyncio.run(run())
        except BaseException as exc:
            result = {
                "status": "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed",
                "attempt": attempt,
                "error": f"{type(exc).__name__}: {exc}",
                "cleanup_status": "failed",
            }
            atomic_json(task_dir / "result.json", result)
        # Reconcile the isolated store and remove local files only after resource shutdown.
        from flocks.security.batch import cleanup_child_work

        asyncio.run(cleanup_child_work(task_dir, result))
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
