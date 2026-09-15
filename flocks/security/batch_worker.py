"""Private, process-isolated entrypoint used by ``security batch``."""

from __future__ import annotations

import asyncio
import errno
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import glob
import shutil
import signal
import sys
import tarfile
import tempfile
import time

from flocks.security.batch import atomic_json, file_lock, read_json, resolve_task, task_result
from flocks.utils.process_identity import process_identity


def extract_source(
    archive: Path, destination: Path, limit: int,
    *, skip_external_symlinks: dict[str, str] | None = None,
    auto_exclude_external_symlinks: bool = False,
    exclude_cyclic_symlinks: bool = False,
) -> list[dict[str, str]]:
    """Validate entries and record omitted external or broken internal symlinks."""
    from flocks.security.batch import parse_link_exclusions

    policy = parse_link_exclusions([f"{name}={target}" for name, target in (skip_external_symlinks or {}).items()])
    exclusions = {}
    with tarfile.open(archive, "r:gz") as bundle:
        total = 0
        members = []
        for index, member in enumerate(bundle):
            if index >= 200_000:
                raise ValueError("Archive contains too many entries")
            if not (member.isfile() or member.isdir() or member.issym() or member.islnk()):
                raise ValueError(f"Unsupported archive entry: {member.name}")
            total += max(0, member.size)
            if total > limit:
                raise ValueError("Archive exceeds --max-snapshot-bytes")
            name = PurePosixPath(member.name)
            if (member.issym() and not name.is_absolute() and ".." not in name.parts
                    and (policy.get(name.as_posix()) == member.linkname
                         or (auto_exclude_external_symlinks and member.linkname.startswith("/")))):
                previous = exclusions.get(name.as_posix())
                if previous and previous["target"] != member.linkname:
                    raise ValueError(f"Conflicting external symlinks: {name}")
                exclusions[name.as_posix()] = {
                    "path": name.as_posix(), "target": member.linkname,
                    "reason": "external_symlink_auto" if auto_exclude_external_symlinks else "external_symlink",
                }
                # Bound persistent metadata and validate paths, including automatically discovered names.
                parse_link_exclusions([f"{item['path']}={item['target']}" for item in exclusions.values()])
                continue
            # data_filter rejects traversal and external links, and strips unsafe permissions.
            checked = tarfile.data_filter(member, str(destination))
            if checked is not None:
                members.append(checked)
        for member in members:
            name = PurePosixPath(member.name).as_posix()
            if any(name == path or name.startswith(path + "/") for path in exclusions):
                raise ValueError(f"Archive entry overlaps an excluded symlink: {name}")
            if member.issym() or member.islnk():
                target = posixpath.normpath(posixpath.join(
                    posixpath.dirname(name) if member.issym() else "", member.linkname,
                ))
                if any(target == path or target.startswith(path + "/") for path in exclusions):
                    raise ValueError(f"Archive link depends on an excluded symlink: {name}")
        bundle.extractall(destination, members=members, filter="data")
    if any(path.is_symlink() for path in destination.rglob("*")):
        # The audit snapshot rejects symlinks. Materialize only internal, non-cyclic
        # links, bounding the expanded tree as well as the original archive.
        normalized = destination.with_name(destination.name + "-normalized")
        total, entries = 0, 0

        def exclude_cycle(source: Path, target: Path) -> None:
            name = target.relative_to(normalized).as_posix()
            exclusions[name] = {"path": name, "target": os.readlink(source), "reason": "cyclic_symlink"}
            if len(exclusions) > 128 or len(json.dumps(list(exclusions.values())).encode("utf-8")) > 48_000:
                raise ValueError("Source exclusion metadata exceeds limit")

        def copy_node(source: Path, target: Path, ancestors: frozenset[Path]) -> None:
            nonlocal total, entries
            entries += 1
            if entries > 200_000:
                raise ValueError("Expanded source contains too many entries")
            try:
                # Check containment even when the final target does not exist.
                resolved = source.resolve(strict=False)
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError(f"External source link: {source.relative_to(destination)}")
                try:
                    resolved = source.resolve(strict=True)
                except FileNotFoundError:
                    if not source.is_symlink():
                        raise
                    name = target.relative_to(normalized).as_posix()
                    exclusions[name] = {
                        "path": name, "target": os.readlink(source),
                        "reason": "broken_internal_symlink",
                    }
                    if len(exclusions) > 128 or len(json.dumps(list(exclusions.values())).encode("utf-8")) > 48_000:
                        raise ValueError("Source exclusion metadata exceeds limit")
                    return
            except (OSError, RuntimeError) as exc:
                if exclude_cyclic_symlinks and source.is_symlink() and (isinstance(exc, RuntimeError) or exc.errno == errno.ELOOP):
                    exclude_cycle(source, target)
                    return
                raise ValueError(f"Unresolved or cyclic source link: {source.relative_to(destination)}") from exc
            if resolved in ancestors:
                if exclude_cyclic_symlinks and source.is_symlink():
                    exclude_cycle(source, target)
                    return
                raise ValueError(f"Cyclic source link: {source.relative_to(destination)}")
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
    return [exclusions[name] for name in sorted(exclusions)]


def cleanup_work(path: str | None, task_dir: Path) -> None:
    if not path:
        return
    root = Path(path)
    receipt = task_dir / f".cleanup-{root.name}.json"
    if not root.exists():
        receipt.unlink(missing_ok=True)
        return
    if root.is_symlink() or root.parent.resolve() != Path(tempfile.gettempdir()).resolve():
        raise ValueError("Refusing cleanup outside batch temporary directory")
    marker = root / ".batch-owner"
    identity = root.stat()
    expected = {"path": str(root.resolve()), "device": identity.st_dev, "inode": identity.st_ino}
    if not marker.exists():
        # Only finish an empty directory whose ownership was verified before interruption.
        if receipt.is_file() and read_json(receipt) == expected:
            root.rmdir()
            receipt.unlink()
            return
        raise FileNotFoundError(f"Temporary directory owner marker missing: {marker}")
    if marker.is_symlink() or marker.read_text() != str(task_dir):
        raise ValueError("Temporary directory owner mismatch")
    root.chmod(0o700)
    for child in root.rglob("*"):
        if not child.is_symlink():
            child.chmod(0o700 if child.is_dir() else 0o600)
    # Keep the ownership marker until all potentially failing recursive work ends.
    for child in root.iterdir():
        if child.name == ".batch-owner":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink(missing_ok=True)
    atomic_json(receipt, expected)
    marker.unlink()
    root.rmdir()
    receipt.unlink()


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
        "failure_code": status.get("failure_code"),
    }


def merge_scan_summary(result: dict, summary: dict) -> None:
    """Keep business failures distinct from the scheduler's termination reason."""
    result.update({key: value for key, value in summary.items() if key not in {"status", "error"}})
    if summary["status"] == "completed" or summary.get("audit_status") == "failed":
        result["status"] = summary["status"]
        if summary.get("error"):
            result["error"] = summary["error"]
        else:
            result.pop("error", None)


async def wait_for_cancel(task_dir: Path, attempt: str, started_at: float, timeout: float) -> str:
    """Worker-side deadline remains a fallback if its scheduler disappears."""
    while True:
        cancel = task_dir / "cancel.json"
        if cancel.exists() and read_json(cancel).get("attempt") in {attempt, "pending"}:
            return "cancelled"
        if time.time() - started_at >= timeout:
            return "timed_out"
        await asyncio.sleep(0.2)


async def stop_audit_task(task: asyncio.Task, task_dir: Path, result: dict) -> None:
    """Bound cancellation in this private worker, including cancellation-resistant tasks."""
    task.cancel()
    _, pending = await asyncio.wait({task}, timeout=10)
    if not pending:
        await asyncio.gather(task, return_exceptions=True)
        return
    result.update(cleanup_status="failed", cleanup_error="Worker audit did not stop within cancellation grace")
    atomic_json(task_dir / "result.json", result)
    # Do not enter asyncio.run's unbounded pending-task shutdown. The supervisor
    # reconciles the persisted result after this worker and its tools stop.
    if os.name == "posix" and os.getpgrp() == os.getpid():
        os.killpg(os.getpid(), signal.SIGKILL)
    os._exit(1)


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
        exclusions = extract_source(
            archive, source, config["max_snapshot_bytes"],
            skip_external_symlinks=config.get("skip_external_symlinks"),
            auto_exclude_external_symlinks=config.get("auto_exclude_external_symlinks", False),
            exclude_cyclic_symlinks=config.get("exclude_cyclic_symlinks", False),
        )
        result["source_exclusions"] = exclusions
        atomic_json(task_dir / "source-exclusions.json", {"exclusions": exclusions})
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
                scan_mode="cybergym_level1" if config.get("dynamic") else "standard",
                cybergym_manifest=task.get("cybergym_manifest"),
                copy_source=False,
                exclude_patterns=[glob.escape(item["path"]) for item in exclusions],
                source_exclusions=exclusions,
                cleanup_intermediates=True,
                max_total_bytes=config["max_snapshot_bytes"],
                max_files=config.get("max_snapshot_files", 50_000),
            )

        audit_task = asyncio.create_task(audit())

        watcher = asyncio.create_task(wait_for_cancel(
            task_dir, attempt, current["started_at"], config["task_timeout"],
        ))
        try:
            done, _ = await asyncio.wait({audit_task, watcher}, return_when=asyncio.FIRST_COMPLETED)
            if audit_task in done:
                await audit_task
                result.update(scan_summary(runtime.store, scan_id))
                atomic_json(task_dir / "result.json", result)
            else:
                result["status"] = watcher.result()
                atomic_json(task_dir / "result.json", result)
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            if not audit_task.done():
                if result["status"] == "failed" and not result.get("error"):
                    result.update(status="cancelled", error="Audit cancelled")
                stopping = asyncio.create_task(stop_audit_task(audit_task, task_dir, result))
                try:
                    await asyncio.shield(stopping)
                except asyncio.CancelledError:
                    await asyncio.shield(stopping)
                    raise
    except asyncio.CancelledError:
        result.update(status="cancelled", error="Audit cancelled")
    except Exception as exc:
        result.update(error=f"{type(exc).__name__}: {exc}")
    finally:
        if scan_id:
            result["scan_id"] = scan_id
            try:
                latest = scan_summary(runtime.store, scan_id)
                merge_scan_summary(result, latest)
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
            result = task_result(task_dir) or {
                "status": "cancelled" if isinstance(exc, (KeyboardInterrupt, asyncio.CancelledError)) else "failed",
                "attempt": attempt,
                "error": f"{type(exc).__name__}: {exc}",
            }
            result.update(cleanup_status="failed", cleanup_error=f"{type(exc).__name__}: {exc}")
            atomic_json(task_dir / "result.json", result)
        # Reconcile the isolated store and remove local files only after resource shutdown.
        from flocks.security.batch import cleanup_child_work

        asyncio.run(cleanup_child_work(task_dir, result))
    raise SystemExit(0 if result["status"] == "completed" else 1)


if __name__ == "__main__":
    main()
