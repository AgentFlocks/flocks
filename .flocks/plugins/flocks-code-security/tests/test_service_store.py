from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks_code_security.models import SnapshotRef
from flocks_code_security import store as store_module
from flocks_code_security.service import (
    AuditCaller,
    AuditService,
    AuditServiceError,
    StartScanRequest,
    _ProgressRecorder,
)
from flocks_code_security.store import ScanStore, process_identity


def _store(tmp_path: Path) -> ScanStore:
    store = ScanStore(tmp_path / "audit.db")
    store.initialize()
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    store.save_snapshot(
        SnapshotRef(
            snapshot_id="snapshot_test",
            repository_identity="repository",
            source_revision="abc123",
            tree_digest="a" * 64,
            scope_digest="b" * 64,
            file_count=0,
            total_bytes=0,
            created_at="2026-08-21T00:00:00+00:00",
            root_path=str(snapshot_root),
            display_name="repository",
        ),
        [],
    )
    return store


def test_request_metadata_enforces_caller_scoped_idempotency(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    store.set_scan_request_metadata(
        first,
        owner_subject="user-1",
        request_source="tool",
        workspace_ref="workspace-1",
        idempotency_key="same-key",
        request_digest="digest-1",
    )

    found = store.find_scan_by_idempotency("user-1", "same-key")
    assert found is not None
    assert found["scan_id"] == first

    second = store.create_scan(
        parent_session_id="session-2",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    with pytest.raises(ValueError, match="Idempotency"):
        store.set_scan_request_metadata(
            second,
            owner_subject="user-1",
            request_source="webui",
            workspace_ref="workspace-1",
            idempotency_key="same-key",
            request_digest="digest-2",
        )


def test_phase_and_event_sequences_are_durable_and_monotonic(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    phase = store.start_phase_run(scan_id, "snapshot", summary={"file_count": 0})
    store.finish_phase_run(phase["phase_run_id"], "completed")
    first = store.append_scan_event(
        scan_id,
        "scan.snapshot_ready",
        "Snapshot ready",
        {"file_count": 0},
        phase_run_id=phase["phase_run_id"],
    )
    second = store.append_scan_event(
        scan_id,
        "phase.started",
        "Threat modeling started",
        {"phase": "threat_modeling"},
    )

    page = store.list_scan_events(scan_id, after_seq=first["seq"], limit=10)
    assert second["seq"] > first["seq"]
    assert [item["seq"] for item in page["items"]] == [second["seq"]]
    assert page["latest_seq"] == second["seq"]
    assert store.list_phase_runs(scan_id)[0]["status"] == "completed"


def test_recent_event_page_is_bounded_and_chronological(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    events = [store.append_scan_event(scan_id, "scan.status", f"Event {index}", {}) for index in range(5)]

    page = store.list_recent_scan_events(scan_id, limit=2)

    assert [item["seq"] for item in page["items"]] == [
        events[-2]["seq"],
        events[-1]["seq"],
    ]
    assert page["latest_seq"] == events[-1]["seq"]
    assert page["has_more"] is True

    older = store.list_scan_events_before(
        scan_id,
        before_seq=events[-1]["seq"],
        limit=2,
    )
    assert [item["seq"] for item in older["items"]] == [
        events[-3]["seq"],
        events[-2]["seq"],
    ]
    assert older["has_more"] is True


def test_projected_event_payload_is_bounded() -> None:
    payload = {
        "scan_id": "scan_test",
        "status": "completed",
        "finding_count": 500,
        "finding_summaries": [
            {
                "finding_id": f"finding_{index}",
                "title": "A" * 200,
                "locations": [{"path": "src/module.py", "startLine": index + 1}],
            }
            for index in range(500)
        ],
    }

    safe = _ProgressRecorder._safe_payload(payload)

    assert len(json.dumps(safe, ensure_ascii=False).encode("utf-8")) < 64 * 1024
    assert safe["finding_count"] == 500
    assert safe["payload_truncated"] is True
    assert "finding_summaries" in safe["truncated_fields"]


def test_scan_listing_is_owner_scoped_and_cursor_based(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for index, owner in enumerate(("user-1", "user-1", "user-2")):
        store.create_scan(
            parent_session_id=f"session-{index}",
            snapshot_id="snapshot_test",
            mode="standard",
            ruleset_digest="rules",
            owner_subject=owner,
            idempotency_key=f"key-{index}",
            request_digest=f"digest-{index}",
        )

    first_page = store.list_scans(owner_subject="user-1", limit=1)
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"]
    second_page = store.list_scans(
        owner_subject="user-1",
        limit=1,
        cursor=first_page["next_cursor"],
    )
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["scan_id"] != first_page["items"][0]["scan_id"]


@pytest.mark.asyncio
async def test_scan_listing_maps_an_invalid_cursor_to_a_stable_error(tmp_path: Path) -> None:
    service = object.__new__(AuditService)
    service.store = _store(tmp_path)

    with pytest.raises(AuditServiceError) as raised:
        await service.list_scans(
            AuditCaller(subject="user-1", source="webui"),
            cursor="not-a-cursor",
        )

    assert raised.value.code == "invalid_parameter"
    assert raised.value.status_code == 400


def test_recovery_marks_orphaned_running_scan_interrupted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )

    assert store.recover_interrupted_scans() == [scan_id]
    scan = store.get_scan(scan_id)
    assert scan is not None
    assert scan["status"] == "interrupted"
    assert scan["failure_code"] == "scan_interrupted"


def test_recovery_preserves_scans_owned_by_a_live_process(tmp_path: Path) -> None:
    store = _store(tmp_path)
    live_scan = store.create_scan(
        parent_session_id="session-live",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        task_owner_pid=os.getpid(),
        task_owner_token="task_live",
        task_owner_identity=process_identity(os.getpid()),
    )
    orphaned_scan = store.create_scan(
        parent_session_id="session-orphaned",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        task_owner_pid=2_147_483_647,
    )

    assert store.recover_interrupted_scans(active_owner_tokens={"task_live"}) == [orphaned_scan]
    assert store.get_scan(live_scan)["status"] == "running"
    assert store.get_scan(orphaned_scan)["status"] == "interrupted"


def test_recovery_interrupts_a_missing_task_in_the_current_process(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-stale",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        task_owner_pid=os.getpid(),
        task_owner_token="task_stale",
    )

    assert store.recover_interrupted_scans(active_owner_tokens=set()) == [scan_id]
    assert store.get_scan(scan_id)["status"] == "interrupted"


def test_recovery_rejects_a_reused_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-reused",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        task_owner_pid=41_000,
        task_owner_token="task_old",
        task_owner_identity="process-old",
    )
    monkeypatch.setattr(store_module, "_pid_is_running", lambda _pid: True)
    monkeypatch.setattr(store_module, "process_identity", lambda _pid: "process-new")

    assert store.recover_interrupted_scans() == [scan_id]
    assert store.get_scan(scan_id)["status"] == "interrupted"


def test_terminal_scan_status_cannot_be_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )

    assert store.mark_scan_terminal(scan_id, "cancelled") is True
    assert (
        store.mark_scan_terminal(
            scan_id,
            "failed",
            failure_code="late_failure",
            failure_summary="A late task failure",
        )
        is False
    )
    scan = store.get_scan(scan_id)
    assert scan is not None
    assert scan["status"] == "cancelled"
    assert scan["failure_code"] is None


@pytest.mark.asyncio
async def test_scan_start_requires_admin_and_an_authorized_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    service = object.__new__(AuditService)

    with pytest.raises(AuditServiceError) as forbidden:
        await service.start_scan(
            StartScanRequest(target_path=workspace),
            AuditCaller(
                subject="user-1",
                source="tool",
                authorized_root=workspace,
            ),
        )
    assert forbidden.value.code == "scan_start_forbidden"
    assert forbidden.value.status_code == 403

    with pytest.raises(AuditServiceError) as escaped:
        await service.start_scan(
            StartScanRequest(target_path=outside),
            AuditCaller(
                subject="admin-1",
                source="tool",
                is_admin=True,
                authorized_root=workspace,
            ),
        )
    assert escaped.value.code == "target_not_authorized"
    assert escaped.value.status_code == 403


@pytest.mark.asyncio
async def test_completed_scan_with_invalid_integrity_has_invalid_result_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = object.__new__(AuditService)

    async def get_scan(_scan_id: str, _caller: AuditCaller) -> dict:
        return {
            "scan": {
                "lifecycle_status": "completed",
                "integrity_status": "invalid",
            },
            "counts": {"candidates": 0},
            "finding_summary": {"total": 0},
            "coverage_summary": {"completeness": "unknown"},
            "artifacts": [],
        }

    monkeypatch.setattr(service, "get_scan", get_scan)

    result = await service.get_result(
        "scan_invalid",
        AuditCaller(subject="admin-1", source="tool", is_admin=True),
    )

    assert result["result_state"] == "invalid"


def test_verified_artifact_response_uses_the_bytes_that_were_hashed(tmp_path: Path) -> None:
    artifact = tmp_path / "report.md"
    trusted = b"trusted report"
    artifact.write_bytes(trusted)
    expected = hashlib.sha256(trusted).hexdigest()

    def scan_status(_scan_id: str) -> dict:
        artifact.write_bytes(b"changed after the response buffer was read")
        return {
            "integrity_status": "valid",
            "integrity_artifacts": {"report.md": expected},
        }

    service = object.__new__(AuditService)
    service.store = SimpleNamespace(scan_status=scan_status)

    contents = service._read_verified_artifact(
        {"scan_id": "scan_test", "status": "completed"},
        artifact,
    )

    assert contents == trusted
    assert artifact.read_bytes() != contents


def test_verified_artifact_response_rejects_unsealed_bytes(tmp_path: Path) -> None:
    artifact = tmp_path / "report.md"
    artifact.write_bytes(b"tampered")
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        scan_status=lambda _scan_id: {
            "integrity_status": "valid",
            "integrity_artifacts": {
                "report.md": hashlib.sha256(b"trusted").hexdigest(),
            },
        },
    )

    with pytest.raises(AuditServiceError) as raised:
        service._read_verified_artifact(
            {"scan_id": "scan_test", "status": "completed"},
            artifact,
        )

    assert raised.value.code == "artifact_integrity_invalid"


def test_work_unit_records_started_and_finished_times(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["src"],
        status="running",
    )

    running = store.get_work_unit(work_unit_id)
    assert running is not None
    assert running["started_at"] is not None
    assert running["finished_at"] is None

    store.update_work_unit_status(work_unit_id, "completed")
    completed = store.get_work_unit(work_unit_id)
    assert completed is not None
    assert completed["started_at"] == running["started_at"]
    assert completed["finished_at"] is not None
