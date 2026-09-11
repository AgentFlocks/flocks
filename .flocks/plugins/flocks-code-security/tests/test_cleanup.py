from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from flocks_code_security import cleanup as cleanup_module
from flocks_code_security.cleanup import cleanup_scan
from flocks_code_security.runtime import build_runtime
from flocks_code_security.service import AuditService, StartScanRequest


def prepare(tmp_path, monkeypatch, *, enabled=True, copy_source=True):
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("print('keep original')\n")
    runtime = build_runtime(tmp_path / "data")
    snapshot = runtime.snapshots.create(target, copy_source=copy_source)
    scan_id = runtime.store.create_scan(
        parent_session_id="parent",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="test",
        cleanup_intermediates=enabled,
    )
    runtime.store.append_scan_event(scan_id, "worker.started", "started", {"internal": "history"})
    phase = runtime.store.start_phase_run(scan_id, "baseline")
    runtime.store.finish_phase_run(phase["phase_run_id"], "completed")
    monkeypatch.setattr(cleanup_module, "runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setattr(cleanup_module, "_delete_session", AsyncMock(return_value=0))
    monkeypatch.setattr(cleanup_module, "_owned_worker_sessions", AsyncMock(return_value=[]))
    return runtime, scan_id, snapshot, target


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
async def test_terminal_cleanup_is_scoped_and_idempotent(tmp_path, monkeypatch, status):
    runtime, scan_id, snapshot, target = prepare(tmp_path, monkeypatch)
    other = runtime.store.create_scan(
        parent_session_id="other", snapshot_id=snapshot.snapshot_id, mode="standard", ruleset_digest="test"
    )
    runtime.store.append_scan_event(other, "keep", "other audit", {})
    # A shared snapshot must survive even if only one audit opts into retention.
    runtime.store.mark_scan_terminal(scan_id, status, failure_code="original_failure")
    docker = tmp_path / "runtime" / "docker" / scan_id
    docker.mkdir(parents=True)
    (docker / "scratch").write_text("temporary")
    shell = tmp_path / "runtime" / "shell" / scan_id
    shell.mkdir(parents=True)
    (shell / "scratch").write_text("temporary")
    other_shell = shell.parent / other
    other_shell.mkdir()
    first = await cleanup_scan(runtime, scan_id)
    assert first["status"] == "completed"
    assert not docker.exists()
    assert not shell.exists()
    assert other_shell.is_dir()
    assert Path(snapshot.root_path).exists()
    assert (target / "app.py").exists()
    assert runtime.store.get_scan(scan_id)["failure_code"] == "original_failure"
    assert runtime.store.list_phase_runs(scan_id) == []
    assert [event["type"] for event in runtime.store.list_scan_events(scan_id)["items"]] == ["scan.cleanup_completed"]
    assert runtime.store.list_scan_events(other)["items"][0]["type"] == "keep"
    assert await cleanup_scan(runtime, scan_id) == first
    with runtime.store._connect() as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("copy_source", [False, True])
async def test_cleanup_never_deletes_original_source(tmp_path, monkeypatch, copy_source):
    runtime, scan_id, snapshot, target = prepare(tmp_path, monkeypatch, copy_source=copy_source)
    runtime.store.mark_scan_terminal(scan_id, "failed")
    result = await cleanup_scan(runtime, scan_id)
    assert result["status"] == "completed"
    assert target.is_dir()
    assert Path(snapshot.root_path).exists() is (not copy_source)
    assert runtime.store.get_snapshot(snapshot.snapshot_id) is not None


@pytest.mark.asyncio
async def test_disabled_cleanup_does_not_touch_history_or_files(tmp_path, monkeypatch):
    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch, enabled=False)
    runtime.store.mark_scan_terminal(scan_id, "failed")
    before = runtime.store.list_scan_events(scan_id)
    assert await cleanup_scan(runtime, scan_id) == {"status": "disabled"}
    assert runtime.store.list_scan_events(scan_id) == before
    assert Path(snapshot.root_path).exists()
    cleanup_module._delete_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_scan_is_not_cleaned(tmp_path, monkeypatch):
    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch)
    assert (await cleanup_scan(runtime, scan_id))["status"] == "pending"
    assert Path(snapshot.root_path).exists()
    assert runtime.store.list_phase_runs(scan_id)


@pytest.mark.asyncio
async def test_cleanup_failure_preserves_result_and_can_retry(tmp_path, monkeypatch):
    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch)
    runtime.store.mark_scan_terminal(scan_id, "failed", failure_code="solver_failed")
    original = cleanup_module._remove_tree
    monkeypatch.setattr(cleanup_module, "_remove_tree", lambda *a, **kw: (_ for _ in ()).throw(OSError("disk busy")))
    assert (await cleanup_scan(runtime, scan_id))["status"] == "failed"
    scan = runtime.store.get_scan(scan_id)
    assert scan["status"] == "failed" and scan["failure_code"] == "solver_failed"
    assert runtime.store.list_phase_runs(scan_id)
    assert Path(snapshot.root_path).exists()
    monkeypatch.setattr(cleanup_module, "_remove_tree", original)
    assert (await cleanup_scan(runtime, scan_id))["status"] == "completed"


@pytest.mark.asyncio
async def test_invalid_completed_bundle_keeps_all_execution_data(tmp_path, monkeypatch):
    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch)
    runtime.store.mark_scan_terminal(scan_id, "completed")
    result = await cleanup_scan(runtime, scan_id)
    assert result["status"] == "failed"
    assert Path(snapshot.root_path).exists()
    assert runtime.store.list_phase_runs(scan_id)
    cleanup_module._delete_session.assert_not_awaited()


def test_cleanup_flag_affects_idempotency_digest(tmp_path):
    assert AuditService._request_digest(StartScanRequest(target_path=tmp_path)) != AuditService._request_digest(
        StartScanRequest(target_path=tmp_path, cleanup_intermediates=True)
    )


def test_owned_tree_rejects_symlink_and_outside_path(tmp_path):
    outside = tmp_path / "source"
    outside.mkdir()
    (outside / "keep").write_text("original")
    link = tmp_path / "snapshot"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        cleanup_module._remove_tree(link, expected=link)
    with pytest.raises(OSError):
        cleanup_module._remove_tree(outside, expected=tmp_path / "snapshot")
    assert (outside / "keep").read_text() == "original"


def test_pruning_retains_selected_fuzz_input_and_ancestry(tmp_path):
    from test_cybergym_runtime import _store

    store, scan_id = _store(tmp_path)
    with store._connect() as connection:
        connection.execute("UPDATE scans SET cleanup_intermediates = 1 WHERE scan_id = ?", (scan_id,))

    def artifact(raw, parent=None):
        return store.create_cybergym_artifact(
            scan_id,
            kind="seed",
            raw=raw,
            parent_id=parent,
            provenance={},
        )["artifact_id"]

    root = artifact(b"root")
    selected = artifact(b"selected", root)
    unused = artifact(b"unused")
    artifact(b"unused child", unused)
    with store._connect() as connection:
        connection.execute("UPDATE cybergym_tasks SET final_artifact_id = ? WHERE scan_id = ?", (selected, scan_id))
    store.mark_scan_terminal(scan_id, "failed")
    summary = store.prune_scan_execution_history(scan_id)
    assert summary["deleted_rows"]["cybergym_artifacts"] == 2
    with store._connect() as connection:
        rows = connection.execute("SELECT artifact_id, raw_bytes FROM cybergym_artifacts").fetchall()
        assert {row["artifact_id"]: row["raw_bytes"] for row in rows} == {root: b"root", selected: b"selected"}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.asyncio
async def test_session_cleanup_refuses_unrelated_descendants(monkeypatch):
    from types import SimpleNamespace
    from flocks.session.session import Session

    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(return_value=SimpleNamespace(project_id="project")))
    monkeypatch.setattr(
        Session, "collect_tree", AsyncMock(return_value=[SimpleNamespace(id="worker"), SimpleNamespace(id="unrelated")])
    )
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(Session, "delete", delete)
    with pytest.raises(ValueError, match="unrelated"):
        await cleanup_module._delete_session("worker", {"worker"})
    delete.assert_not_awaited()
    assert await cleanup_module._delete_session("worker", {"worker", "unrelated"}) == 2
    delete.assert_awaited_once_with("project", "worker")


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_service_failure_runs_optional_cleanup(tmp_path, monkeypatch, enabled):
    from types import SimpleNamespace
    from flocks_code_security import service as service_module

    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch, enabled=enabled)
    monkeypatch.setattr(service_module, "get_runtime", lambda: runtime)
    orchestrator = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("audit failed")))
    monkeypatch.setattr(service_module, "AuditOrchestrator", lambda *a, **kw: orchestrator)
    service = AuditService()
    recorder = SimpleNamespace(_publish_change=lambda *a: None)
    with pytest.raises(RuntimeError, match="audit failed"):
        await service._run_background(
            StartScanRequest(target_path=tmp_path, cleanup_intermediates=enabled), None, {"scan_id": scan_id}, recorder
        )
    assert runtime.store.get_scan(scan_id)["status"] == "failed"
    assert Path(snapshot.root_path).exists() is (not enabled)
    assert bool(runtime.store.list_phase_runs(scan_id)) is (not enabled)
    if enabled:
        cleanup_module._delete_session.assert_awaited_once_with("parent", {"parent"})


@pytest.mark.asyncio
async def test_recovery_cleans_opted_in_interrupted_scan(tmp_path, monkeypatch):
    import asyncio
    from flocks_code_security import service as service_module

    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(service_module, "get_runtime", lambda: runtime)
    monkeypatch.setattr(service_module._ProgressRecorder, "_publish_change", lambda *a: None)
    service = AuditService()
    assert service.recover_orphaned_scans() == [scan_id]
    await asyncio.gather(*service._cleanup_tasks)
    assert runtime.store.get_scan(scan_id)["status"] == "interrupted"
    assert not Path(snapshot.root_path).exists()
    assert runtime.store.list_phase_runs(scan_id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled", "interrupted"])
async def test_restart_retries_terminal_cleanup(tmp_path, monkeypatch, status):
    import asyncio
    from flocks_code_security import service as service_module

    runtime, scan_id, _, _ = prepare(tmp_path, monkeypatch)
    runtime.store.mark_scan_terminal(scan_id, status)
    runtime.store.record_cleanup_failure(scan_id, "interrupted during cleanup")
    monkeypatch.setattr(service_module, "get_runtime", lambda: runtime)
    clean = AsyncMock(return_value={"status": "completed"})
    monkeypatch.setattr(cleanup_module, "cleanup_scan", clean)
    service = AuditService()
    assert service.recover_orphaned_scans() == []
    await asyncio.gather(*service._cleanup_tasks)
    clean.assert_awaited_once_with(runtime, scan_id, owned_parent_session=False)


@pytest.mark.asyncio
async def test_cleanup_initial_database_failure_does_not_escape(tmp_path, monkeypatch):
    runtime, scan_id, _, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime.store, "get_scan", lambda _: (_ for _ in ()).throw(OSError("db unavailable")))
    assert (await cleanup_scan(runtime, scan_id))["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("audit_error", [False, True])
async def test_cleanup_exception_preserves_result_and_unregisters_task(tmp_path, monkeypatch, audit_error):
    from types import SimpleNamespace
    from flocks_code_security import service as service_module

    runtime, scan_id, _, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(service_module, "get_runtime", lambda: runtime)
    run = (
        AsyncMock(side_effect=RuntimeError("original audit failure"))
        if audit_error
        else AsyncMock(return_value={"result": "kept"})
    )
    monkeypatch.setattr(service_module, "AuditOrchestrator", lambda *a, **kw: SimpleNamespace(run=run))
    monkeypatch.setattr(cleanup_module, "cleanup_scan", AsyncMock(side_effect=OSError("cleanup failure")))
    service = AuditService()
    service._active[scan_id] = object()
    operation = service._run_background(
        StartScanRequest(target_path=tmp_path, cleanup_intermediates=True),
        None,
        {"scan_id": scan_id},
        SimpleNamespace(_publish_change=lambda *a: None),
    )
    if audit_error:
        with pytest.raises(RuntimeError, match="original audit failure"):
            await operation
    else:
        assert await operation == {"result": "kept"}
    assert scan_id not in service._active


@pytest.mark.asyncio
async def test_unbound_worker_is_discovered_by_persisted_ownership(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from flocks.session.session import Session

    discover = cleanup_module._owned_worker_sessions
    runtime, scan_id, snapshot, _ = prepare(tmp_path, monkeypatch)
    monkeypatch.setattr(cleanup_module, "_owned_worker_sessions", discover)
    runtime.store.mark_scan_terminal(scan_id, "failed")
    monkeypatch.setattr(Session, "get_by_id_unfiltered", AsyncMock(return_value=SimpleNamespace(project_id="project")))
    monkeypatch.setattr(
        Session,
        "collect_tree",
        AsyncMock(
            return_value=[
                SimpleNamespace(id="parent", metadata={}),
                SimpleNamespace(id="unbound-worker", metadata={"code_security_scan_id": scan_id}),
                SimpleNamespace(id="other-worker", metadata={"code_security_scan_id": "other-scan"}),
            ]
        ),
    )
    assert (await cleanup_scan(runtime, scan_id))["status"] == "completed"
    cleanup_module._delete_session.assert_awaited_once_with("unbound-worker", {"unbound-worker"})
    assert not Path(snapshot.root_path).exists()


def test_cleanup_recovery_skips_live_owners_and_completed_cleanup(tmp_path, monkeypatch):
    import os
    from flocks_code_security.store import process_identity

    runtime, scan_id, _, _ = prepare(tmp_path, monkeypatch)
    runtime.store.mark_scan_terminal(scan_id, "failed")
    with runtime.store._connect() as connection:
        connection.execute(
            "UPDATE scans SET task_owner_pid = ?, task_owner_token = ?, task_owner_identity = ? WHERE scan_id = ?",
            (os.getpid(), "live-owner", process_identity(os.getpid()), scan_id),
        )
    assert runtime.store.pending_cleanup_scan_ids(active_owner_tokens={"live-owner"}) == []
    assert runtime.store.pending_cleanup_scan_ids(active_owner_tokens=set()) == [scan_id]
    runtime.store.prune_scan_execution_history(scan_id)
    assert runtime.store.pending_cleanup_scan_ids(active_owner_tokens=set()) == []
