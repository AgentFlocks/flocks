"""Content-free runtime snapshots retained independently of audit cleanup."""

from __future__ import annotations

import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import time


def read_runtime(task_dir: Path, attempt: str) -> dict | None:
    from flocks.security.batch import read_json

    try:
        snapshot = read_json(task_dir / "runtime.json")
        if snapshot.get("attempt") != attempt:
            return None
        if not snapshot.get("termination_reason") and snapshot.get("observed_at") is not None:
            snapshot["snapshot_age_seconds"] = max(0, time.time() - snapshot["observed_at"])
        return snapshot
    except (OSError, ValueError, TypeError):
        return None


def write_runtime(task_dir: Path, snapshot: dict) -> None:
    """Diagnostic persistence must never prevent result saving or cleanup."""
    from flocks.security.batch import atomic_json

    snapshot.pop("diagnostics_error", None)
    try:
        atomic_json(task_dir / "runtime.json", snapshot)
    except (OSError, ValueError, TypeError) as exc:
        snapshot["diagnostics_error"] = type(exc).__name__


def termination_snapshot(task_dir: Path, attempt: str, reason: str) -> dict:
    """Supervisor fallback, including blocked event loops and hard kills."""
    snapshot = read_runtime(task_dir, attempt) or {"attempt": attempt, "phase": "unknown"}
    if snapshot.get("termination_reason"):
        return snapshot
    now = time.time()
    snapshot.update(termination_reason=reason, captured_at=now)
    observed = snapshot.get("observed_at")
    snapshot["snapshot_age_seconds"] = max(0, now - observed) if observed is not None else None
    return snapshot


def session_snapshot(session_id: str) -> dict:
    from flocks.session.core.status import SessionStatus
    from flocks.session.core.turn_state import get_turn_state
    from flocks.session.session_loop import SessionLoop

    ctx = SessionLoop.get_context(session_id)
    status = SessionStatus.get_for_session(session_id)
    turn = get_turn_state(session_id)
    snapshot = {
        "session_id": session_id, "loop_running": ctx is not None,
        "session_status": status.type, "step": ctx.step if ctx else turn.step,
        "state": status.type, "stop_reason": turn.stop_reason if ctx is None else None,
    }
    if ctx:
        activity = deepcopy(ctx.execution_activity)
        snapshot.update(
            activity, agent=ctx.agent_name, provider=ctx.provider_id, model=ctx.model_id,
            trace_step=ctx.trace_step,
        )
        if status.type in {"retry", "compacting", "queued"}:
            snapshot["state"] = status.type
        elif activity.get("active_tools"):
            snapshot["state"] = "executing_tool"
        else:
            snapshot["state"] = activity.get("state", "processing")
        if activity.get("last_activity_at") is not None:
            snapshot["activity_age_seconds"] = max(0, time.time() - activity["last_activity_at"])
    if status.type == "retry":
        # Provider error messages may contain request content; retain timing only.
        snapshot.update(retry_attempt=status.attempt, retry_at=status.next / 1000)
    return snapshot


class RuntimeDiagnostics:
    """One monitor per isolated batch worker; never counts polling as activity."""

    def __init__(self, task_dir: Path, attempt: str, store) -> None:
        self.task_dir = task_dir
        self.store = store
        self.scan_id = None
        self.phase = "source_extraction"
        self.phase_started_at = time.time()
        self.latest = {"attempt": attempt}
        self.wake = asyncio.Event()
        self.closed = False

    def progress(self, event: str, payload: dict) -> None:
        if self.closed:
            return
        if event == "scan.prepared":
            self.scan_id = payload["scan_id"]
        # The audit service owns phase semantics, including dynamic subphases.
        phase = payload.get("current_phase")
        if phase and phase != self.phase:
            self.phase = phase
            self.phase_started_at = time.time()
            self.latest.pop("workers", None)
            self.latest.pop("work_unit_counts", None)
        self.latest["last_orchestrator_event"] = event
        self.wake.set()

    def collect(self) -> dict:
        """Only DB reads run on a thread; session state is read on the loop."""
        scan = self.store.get_scan(self.scan_id) or {}
        batches = self.store.list_worker_batches(self.scan_id)
        units = [unit for batch in batches for unit in batch["units"]]
        fields = (
            "work_unit_id", "phase", "role", "status", "subject_id", "session_id",
            "attempt_id", "attempt_ordinal", "resume_count", "attempt_status",
        )
        return {
            "parent_session_id": scan.get("parent_session_id"),
            "work_unit_counts": dict(Counter(unit["status"] for unit in units)),
            "workers": [
                {key: unit.get(key) for key in fields}
                for unit in units if unit["status"] in {"pending", "running"}
            ],
            "workers_observed_at": time.time(),
        }

    def persist(self) -> None:
        self.latest.update(
            scan_id=self.scan_id, phase=self.phase, phase_started_at=self.phase_started_at,
            observed_at=time.time(),
        )
        try:
            parent = self.latest.get("parent_session_id")
            if parent:
                self.latest["coordinator"] = session_snapshot(parent)
            for worker in self.latest.get("workers", []):
                if worker.get("session_id"):
                    worker["execution"] = session_snapshot(worker["session_id"])
        except Exception as exc:
            # Diagnostics must not replace an audit outcome with a monitoring error.
            self.latest["session_snapshot_error"] = type(exc).__name__
        else:
            self.latest.pop("session_snapshot_error", None)
        write_runtime(self.task_dir, self.latest)

    async def run(self) -> None:
        collection = None
        collection_phase_started_at = None
        try:
            while not self.closed:
                self.wake.clear()
                if self.scan_id:
                    if collection is None:
                        collection_phase_started_at = self.phase_started_at
                        collection = asyncio.create_task(asyncio.to_thread(self.collect))
                    # asyncio.wait leaves a slow query alive. Reuse it on the
                    # next tick instead of accumulating executor jobs.
                    done, _ = await asyncio.wait({collection}, timeout=1)
                    if self.closed:
                        return
                    if done:
                        try:
                            collected = collection.result()
                            if collection_phase_started_at == self.phase_started_at:
                                self.latest.update(collected)
                            self.latest.pop("collection_error", None)
                        except Exception as exc:
                            self.latest["collection_error"] = type(exc).__name__
                        collection = None
                    else:
                        self.latest["collection_error"] = "TimeoutError"
                self.persist()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
        finally:
            if collection is not None:
                collection.cancel()
                await asyncio.gather(collection, return_exceptions=True)

    def freeze(self, reason: str) -> dict:
        if self.closed:
            return deepcopy(self.latest)
        self.closed = True
        self.latest.update(termination_reason=reason, captured_at=time.time(), snapshot_age_seconds=0)
        self.persist()
        snapshot = deepcopy(self.latest)
        try:
            print(json.dumps({"event": "audit.termination_snapshot", **snapshot}, ensure_ascii=False), flush=True)
        except OSError:
            pass
        return snapshot
