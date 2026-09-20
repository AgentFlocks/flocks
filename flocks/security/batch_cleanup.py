"""Reconcile a stopped batch worker without loading a model or sharing its runtime."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


def read_batch_config(task_dir: Path) -> dict:
    from flocks.security.batch import read_json, atomic_json

    return read_json(task_dir.parents[1] / "batch.json")


def task_environment(task_dir: Path) -> dict[str, str]:
    config = read_batch_config(task_dir)
    return {
        **os.environ,
        "FLOCKS_CODE_SECURITY_BATCH_TASK": str(task_dir) if config.get("dynamic") else "",
        "FLOCKS_DATA_DIR": str(task_dir / "data/flocks"),
        "FLOCKS_CODE_SECURITY_ROOT": str(task_dir / "data/code-security"),
        "FLOCKS_CODE_SECURITY_WORKERS": "1",
        "FLOCKS_CODE_SECURITY_RETAIN_UI_HISTORY": "1",
        "FLOCKS_CODE_SECURITY_SKIP_ORPHAN_RECOVERY": "1",
    }


def remove_owned_tree(task_dir: Path, relative: str) -> None:
    path = task_dir / relative
    if path.is_symlink() or path.resolve() != task_dir.resolve() / relative:
        raise ValueError(f"Refusing cleanup through a symbolic link: {relative}")
    if not path.exists():
        return
    # Read-only source projections must be removable too. Never follow child links.
    path.chmod(0o700)
    for child in path.rglob("*"):
        if not child.is_symlink():
            child.chmod(0o700 if child.is_dir() else 0o600)
    shutil.rmtree(path)


def reconcile(task_dir: Path, result: dict) -> None:
    """Reconcile only this stopped task's store; never initialize a global agent runtime."""
    try:
        from flocks_code_security.store import ScanStore
    except ModuleNotFoundError as exc:
        if exc.name != "flocks_code_security":
            raise
        source = Path(__file__).resolve().parents[2] / ".flocks/plugins/flocks-code-security/src"
        if not source.is_dir():
            raise RuntimeError("The flocks-code-security source plugin is not available") from None
        if str(source) not in sys.path:
            sys.path.insert(0, str(source))
        from flocks_code_security.store import ScanStore
    from flocks.security.batch import read_json, atomic_json
    from flocks.security.batch_worker import scan_summary, merge_scan_summary

    current = read_json(task_dir / "current.json")
    if result.get("attempt") != current.get("attempt"):
        raise ValueError("Refusing reconciliation for a superseded task attempt")
    store = ScanStore(task_dir / "data/code-security/data/code-security.db")
    store.retain_ui_history = True
    store.initialize()
    scan_id = current.get("scan_id") or current.get("previous", {}).get("scan_id")
    if scan_id and store.get_scan(scan_id):
        summary = scan_summary(store, scan_id)
        merge_scan_summary(result, summary)
        atomic_json(task_dir / "result.json", result)

    with store._connect() as connection:
        scans = connection.execute("SELECT scan_id, status FROM scans").fetchall()
    dynamic = read_batch_config(task_dir).get("dynamic", False)
    for scan in scans:
        scan_id = scan["scan_id"]
        if scan["status"] == "completed" and store.scan_status(scan_id)["integrity_status"] != "valid":
            raise ValueError("Final artifact bundle is invalid; retaining execution data")
        if scan["status"] not in {"completed", "failed", "cancelled", "interrupted"}:
            store.mark_scan_terminal(scan_id, "interrupted", failure_code="batch_interrupted")
        # The caller has already confirmed removal of this task's containers.
        if dynamic:
            for run in store.list_cybergym_runs(scan_id):
                if run["status"] == "running":
                    store.finish_cybergym_run(
                        run["run_id"],
                        "cancelled",
                        {
                            "status": "cancelled",
                            "cancel_source": "batch_recovery",
                        },
                    )
        store.assert_cybergym_runs_terminal(scan_id)
        store.cancel_scan_work(scan_id)
        summary = store.prune_scan_execution_history(scan_id)
        if summary.get("status") != "completed":
            raise RuntimeError(f"Cleanup {scan_id}: {summary}")


CLEANUP_BUSY = 75


def main() -> None:
    """Private bounded cleanup process; its lock survives scheduler termination."""
    import signal
    import threading
    import time
    from contextlib import ExitStack
    from flocks.security.batch import _cleanup_child_work, atomic_json, file_lock, read_json
    from flocks.utils.process_identity import process_identity

    task_dir, attempt = Path(sys.argv[1]), sys.argv[2]
    with ExitStack() as ownership:
        try:
            ownership.enter_context(file_lock(task_dir / "cleanup.lock"))
        except BlockingIOError:
            raise SystemExit(CLEANUP_BUSY) from None
        current = read_json(task_dir / "current.json")
        if current.get("attempt") != attempt:
            raise ValueError("Superseded cleanup attempt")
        result = read_json(task_dir / "result.json")
        if (result.get("cleanup_status") == "completed"
                and result.get("source_cleanup_status") == "completed" and not current.get("work_dir")):
            return
        state = current["phase_timeout"]
        if state["phase"] != "cleanup":
            raise ValueError("Cleanup has no active budget")
        remaining = state["deadline"] - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Cleanup budget exhausted")

        def expired():
            if os.name == "posix" and os.getpgrp() == os.getpid():
                os.killpg(os.getpid(), signal.SIGKILL)
            os._exit(124)

        timer = threading.Timer(remaining, expired)
        timer.daemon = True
        timer.start()
        try:
            current.update(pid=os.getpid(), process_identity=process_identity(os.getpid()))
            atomic_json(task_dir / "current.json", current)
            _cleanup_child_work(task_dir, result)
        finally:
            timer.cancel()


if __name__ == "__main__":
    main()
