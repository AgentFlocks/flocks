from types import SimpleNamespace
import asyncio
import json
import time
import threading

import pytest

from flocks.security.batch import atomic_json
from flocks.security.batch_diagnostics import RuntimeDiagnostics, read_runtime, session_snapshot, termination_snapshot
from flocks.session.core.status import SessionStatus, SessionStatusBusy, SessionStatusRetry
from flocks.session.session_loop import SessionLoop


@pytest.fixture
def active_session(monkeypatch):
    ctx = SimpleNamespace(
        step=7, trace_step=12, agent_name="code-security-verifier", provider_id="test", model_id="model",
        execution_activity={"state": "waiting_model", "last_activity_at": 100.0, "active_tools": {}},
    )
    monkeypatch.setattr(SessionLoop, "get_context", lambda _: ctx)
    monkeypatch.setattr(SessionStatus, "get_for_session", lambda _: SessionStatusBusy())
    return ctx


def test_runtime_distinguishes_tool_model_retry_and_logical_step(active_session, monkeypatch):
    snapshot = session_snapshot("worker")
    assert (snapshot["state"], snapshot["step"], snapshot["trace_step"]) == ("waiting_model", 7, 12)
    active_session.execution_activity.update(state="streaming_model", active_tools={"bash": 2})
    snapshot = session_snapshot("worker")
    assert snapshot["state"] == "executing_tool"
    assert snapshot["active_tools"] == {"bash": 2}
    monkeypatch.setattr(SessionStatus, "get_for_session", lambda _: SessionStatusRetry(attempt=2, next=200000, message="secret"))
    snapshot = session_snapshot("worker")
    assert snapshot["state"] == "retry"
    assert snapshot["retry_at"] == 200
    assert "secret" not in json.dumps(snapshot)


def test_polling_does_not_refresh_activity_and_freeze_survives_cleanup(tmp_path, active_session):
    diagnostics = RuntimeDiagnostics(tmp_path, "attempt", None)
    diagnostics.latest.update(parent_session_id="parent", workers=[{"session_id": "worker"}])
    diagnostics.progress("phase.dispatching", {"current_phase": "verification"})
    diagnostics.latest["workers"] = [{"session_id": "worker"}]
    diagnostics.progress("batch.status", {"current_phase": "verification", "status": "running"})
    diagnostics.persist()
    assert diagnostics.latest["workers"][0]["execution"]["last_activity_at"] == 100
    frozen = diagnostics.freeze("timed_out")
    active_session.execution_activity.update(state="processing", active_tools={})
    diagnostics.progress("scan.cancelled", {})
    assert read_runtime(tmp_path, "attempt") == frozen
    assert frozen["coordinator"]["state"] == "waiting_model"
    assert frozen["phase"] == "verification"


def test_supervisor_reports_stale_and_missing_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 120)
    atomic_json(tmp_path / "runtime.json", {"attempt": "old", "phase": "verification", "observed_at": 100})
    assert read_runtime(tmp_path, "new") is None
    assert termination_snapshot(tmp_path, "new", "timed_out")["phase"] == "unknown"
    snapshot = termination_snapshot(tmp_path, "old", "timed_out")
    assert snapshot["snapshot_age_seconds"] == 20
    assert snapshot["observed_at"] == 100


@pytest.mark.asyncio
async def test_monitor_reads_workers_before_batch_started_and_isolates_errors(tmp_path):
    store = SimpleNamespace(
        get_scan=lambda _: {"parent_session_id": "parent"},
        list_worker_batches=lambda _: [{"units": [
            {"status": "running", "role": "verifier", "phase": "verification", "work_unit_id": "unit"},
            {"status": "pending", "role": "verifier", "phase": "verification"},
            {"status": "completed"},
        ]}],
    )
    diagnostics = RuntimeDiagnostics(tmp_path, "attempt", store)
    diagnostics.progress("scan.prepared", {"scan_id": "scan"})
    diagnostics.progress("phase.dispatching", {"current_phase": "verification"})
    monitor = asyncio.create_task(diagnostics.run())
    try:
        async with asyncio.timeout(3):
            while "workers_observed_at" not in diagnostics.latest:
                await asyncio.sleep(0.01)
        assert len(diagnostics.latest["workers"]) == 2
        assert diagnostics.latest["work_unit_counts"] == {"running": 1, "pending": 1, "completed": 1}
        observed = diagnostics.latest["workers_observed_at"]

        def broken_store(_):
            raise OSError("private DB details")

        store.get_scan = broken_store
        diagnostics.wake.set()
        async with asyncio.timeout(3):
            while "collection_error" not in diagnostics.latest:
                await asyncio.sleep(0.01)
        assert diagnostics.latest["workers_observed_at"] == observed
        assert diagnostics.latest["collection_error"] == "OSError"
        assert "private DB details" not in json.dumps(diagnostics.latest)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.asyncio
async def test_slow_collection_is_reused_and_cannot_overwrite_frozen_snapshot(tmp_path, monkeypatch):
    diagnostics = RuntimeDiagnostics(tmp_path, "attempt", None)
    diagnostics.scan_id = "scan"
    release = threading.Event()
    calls = []

    def collect():
        calls.append(True)
        release.wait(5)
        return {"workers": [{"work_unit_id": "late-result"}]}

    monkeypatch.setattr(diagnostics, "collect", collect)
    writes = []
    from flocks.security import batch
    original_write = batch.atomic_json

    def record_write(path, value):
        writes.append(True)
        original_write(path, value)

    monkeypatch.setattr(batch, "atomic_json", record_write)
    monitor = asyncio.create_task(diagnostics.run())
    try:
        async with asyncio.timeout(4):
            while len(writes) < 1:
                await asyncio.sleep(0.01)
            diagnostics.wake.set()
            while len(writes) < 2:
                await asyncio.sleep(0.01)
        assert len(calls) == 1
        assert diagnostics.latest["collection_error"] == "TimeoutError"
        frozen = diagnostics.freeze("timed_out")
        release.set()
        diagnostics.wake.set()
        await asyncio.wait_for(monitor, timeout=2)
        assert diagnostics.latest == frozen
        assert read_runtime(tmp_path, "attempt") == frozen
    finally:
        release.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)


@pytest.mark.asyncio
async def test_progress_events_coalesce_into_one_snapshot(tmp_path, monkeypatch):
    from flocks.security import batch_diagnostics

    diagnostics = RuntimeDiagnostics(tmp_path, "attempt", None)
    writes = []
    monkeypatch.setattr(batch_diagnostics, "write_runtime", lambda *args: writes.append(True))
    for event in ("dynamic.started", "phase.dispatching", "batch.status"):
        diagnostics.progress(event, {"phase": "probing", "current_phase": "dynamic_validation"})
    assert writes == []
    assert diagnostics.phase == "dynamic_validation"
    monitor = asyncio.create_task(diagnostics.run())
    try:
        await asyncio.sleep(0)
        assert writes == [True]
        assert diagnostics.freeze("timed_out")["phase"] == "dynamic_validation"
        assert len(writes) == 2
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
