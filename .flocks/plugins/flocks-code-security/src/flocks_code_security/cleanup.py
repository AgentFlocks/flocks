"""Opt-in terminal audit retention. Never delete caller-owned source or reports."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import shutil
from pathlib import Path
from weakref import WeakValueDictionary
from typing import TYPE_CHECKING

from flocks_code_security.paths import runtime_dir

if TYPE_CHECKING:
    from flocks_code_security.runtime import PluginRuntime

logger = logging.getLogger(__name__)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _remove_tree(path: Path, *, expected: Path) -> int:
    if path.is_symlink() or path.absolute() != expected.absolute() or path.resolve() != expected.absolute():
        raise OSError(f"Refusing cleanup outside owned directory: {path}")
    if not path.exists():
        return 0
    path.chmod(0o700)
    for child in path.rglob("*"):
        if not child.is_symlink():
            child.chmod(0o700 if child.is_dir() else 0o600)
    shutil.rmtree(path)
    return 1


async def _delete_session(session_id: str, allowed_ids: set[str]) -> int:
    from flocks.session.session import Session

    session = await Session.get_by_id_unfiltered(session_id)
    if session is None:
        return 0
    tree = await Session.collect_tree(session.project_id, session_id)
    if any(item.id not in allowed_ids for item in tree):
        raise ValueError("Audit session contains unrelated child sessions")
    if not await Session.delete(session.project_id, session_id):
        raise RuntimeError("Audit session could not be quiesced for cleanup")
    return len(tree)


async def _owned_worker_sessions(parent_session_id: str, scan_id: str) -> list[str]:
    from flocks.session.session import Session

    parent = await Session.get_by_id_unfiltered(parent_session_id)
    if parent is None:
        return []
    tree = await Session.collect_tree(parent.project_id, parent_session_id)
    return [
        item.id
        for item in tree
        if item.id != parent_session_id and (item.metadata or {}).get("code_security_scan_id") == scan_id
    ]


def _remove_legacy_fuzz_container(container_name: str | None) -> dict[str, str]:
    """Remove only a persisted container named by the retired fuzz runner."""
    if container_name is None:
        return {"status": "not_requested"}
    if not isinstance(container_name, str) or not re.fullmatch(r"cybergym-fuzz-[0-9a-f]{24}", container_name):
        raise ValueError("Refusing cleanup of an unrecognized legacy fuzz container")
    try:
        result = subprocess.run(
            ["docker", "rm", "--force", container_name],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Legacy fuzz container cleanup is unavailable; retaining execution data") from exc
    if result.returncode == 0:
        return {"status": "removed"}
    if "no such container" in result.stderr.casefold():
        return {"status": "not_found"}
    raise RuntimeError(f"Legacy fuzz container cleanup failed: {result.stderr.strip()[:1000]}")


async def _cleanup_legacy_fuzz_runs(store, scan_id: str) -> None:
    # Called only after the scan is terminal and its worker sessions are stopped.
    # Docker failure must leave data available for the next cleanup attempt.
    for run in store.list_cybergym_runs(scan_id):
        if run["kind"] != "fuzz":
            continue
        prior = (run.get("result") or {}).get("container_cleanup") or {}
        if run["status"] != "running" and prior.get("status") in {"removed", "not_found", "not_requested"}:
            continue
        cleanup = await asyncio.to_thread(_remove_legacy_fuzz_container, run.get("container_name"))
        if run["status"] == "running":
            store.finish_cybergym_run(
                run["run_id"], "cancelled",
                {"status": "cancelled", "failure_code": "cancelled", "cancel_source": "terminal_cleanup"},
            )
        store.record_cybergym_fuzz_cleanup(run["run_id"], cleanup)


async def cleanup_scan(runtime: PluginRuntime, scan_id: str, *, owned_parent_session: bool = False) -> dict:
    try:
        return await _cleanup_scan(runtime, scan_id, owned_parent_session=owned_parent_session)
    except Exception as exc:
        logger.warning("Audit cleanup failed for %s", scan_id, exc_info=True)
        try:
            runtime.store.record_cleanup_failure(scan_id, f"{type(exc).__name__}: {exc}")
        except Exception:
            logger.warning("Unable to persist cleanup failure for %s", scan_id, exc_info=True)
        return {"status": "failed", "reason": str(exc)[:1000]}


async def _cleanup_scan(runtime: PluginRuntime, scan_id: str, *, owned_parent_session: bool = False) -> dict:
    store = runtime.store
    scan = store.get_scan(scan_id)
    if scan is None or not scan.get("cleanup_intermediates"):
        return {"status": "disabled"}
    key = f"{store.database_path}:{scan_id}"
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        scan = store.get_scan(scan_id)
        if scan is None:
            return {"status": "missing"}
        prior = json.loads(scan["cleanup_summary_json"])
        if prior.get("status") == "completed":
            return prior
        if scan["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            return {"status": "pending", "reason": "audit_is_active"}
        # Successful reports must already be sealed before any destructive work.
        if scan["status"] == "completed" and store.scan_status(scan_id)["integrity_status"] != "valid":
            raise ValueError("Final artifact bundle is not valid; retaining execution data")
        session_ids = store.cleanup_session_ids(scan_id)
        session_ids.extend(await _owned_worker_sessions(scan["parent_session_id"], scan_id))
        session_ids = list(dict.fromkeys(session_ids))
        if owned_parent_session:
            session_ids.append(scan["parent_session_id"])
        allowed = set(session_ids)
        deleted_sessions = 0
        for session_id in session_ids:
            deleted_sessions += await _delete_session(session_id, allowed)
        # Session deletion waits for execution to stop before source removal.
        store.cancel_scan_work(scan_id)
        await _cleanup_legacy_fuzz_runs(store, scan_id)
        store.assert_cybergym_runs_terminal(scan_id)
        snapshot = store.get_snapshot(scan["snapshot_id"])
        deleted_trees = 0
        if snapshot is not None and snapshot.copy_source:
            with store._connect() as connection:
                shared = connection.execute(
                    "SELECT 1 FROM scans WHERE snapshot_id = ? AND scan_id != ? LIMIT 1",
                    (snapshot.snapshot_id, scan_id),
                ).fetchone()
            if not shared:
                expected = runtime.snapshots.snapshots_root.resolve() / snapshot.snapshot_id
                deleted_trees += await asyncio.to_thread(_remove_tree, Path(snapshot.root_path), expected=expected)
        docker_root = runtime_dir().resolve() / "docker" / scan_id
        deleted_trees += await asyncio.to_thread(_remove_tree, docker_root, expected=docker_root)
        return await asyncio.to_thread(
            store.prune_scan_execution_history,
            scan_id,
            deleted_sessions=deleted_sessions,
            deleted_trees=deleted_trees,
        )
