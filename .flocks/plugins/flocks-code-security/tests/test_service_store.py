from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from flocks_code_security.models import SnapshotRef
from flocks_code_security import service as service_module
from flocks_code_security import store as store_module
from flocks_code_security.service import (
    AuditCaller,
    AuditService,
    AuditServiceError,
    KnowledgeBaseInput,
    StartScanRequest,
    _ProgressRecorder,
    _effective_finished_at,
)
from flocks_code_security.store import ScanStore, process_identity


def _store(tmp_path: Path) -> ScanStore:
    store = ScanStore(tmp_path / "audit.db")
    store.initialize()
    snapshot_root = tmp_path / "snapshots" / "snapshot_test"
    snapshot_root.mkdir(parents=True)
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


def test_knowledge_base_is_immutable_bound_and_private_to_its_scan(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="coordinator",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    content = "Prioritize the caller-controlled parser."
    encoded = content.encode("utf-8")
    store.set_scan_request_metadata(
        scan_id,
        owner_subject="user-1",
        request_source="cli",
        workspace_ref="workspace-1",
        idempotency_key="guided",
        request_digest="request",
        knowledge_base={
            "display_name": "description.txt",
            "content": content,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "byte_length": len(encoded),
        },
    )
    store.bind_session(
        session_id="coordinator",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="coordinator",
    )
    binding = store.require_binding("coordinator", {"coordinator"})

    with pytest.raises(ValueError, match="audit_knowledge_base"):
        store.require_knowledge_base_consumed(binding)
    captured = store.read_knowledge_base(binding)
    assert captured is not None
    assert captured["content"] == content
    store.require_knowledge_base_consumed(binding)

    metadata = store.get_knowledge_base_metadata(scan_id)
    assert metadata == {
        "display_name": "description.txt",
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "byte_length": len(encoded),
        "trust": "untrusted_external_hypothesis",
    }
    assert "content" not in metadata
    assert "content" not in store.report_data(scan_id)["knowledge_base"]

    store.delete_scan(scan_id)
    assert store.get_knowledge_base_metadata(scan_id) is None


def test_knowledge_base_changes_the_request_digest_and_is_validated() -> None:
    first = AuditService._validate_knowledge_base(KnowledgeBaseInput("description.txt", "\ufefffirst hypothesis"))
    second = AuditService._validate_knowledge_base(KnowledgeBaseInput("description.txt", "second hypothesis"))
    assert first is not None
    assert first.content == "first hypothesis"

    first_digest = AuditService._request_digest(StartScanRequest(target_path=Path("/tmp/target"), knowledge_base=first))
    second_digest = AuditService._request_digest(
        StartScanRequest(target_path=Path("/tmp/target"), knowledge_base=second)
    )
    assert first_digest != second_digest
    renamed_digest = AuditService._request_digest(
        StartScanRequest(
            target_path=Path("/tmp/target"),
            knowledge_base=KnowledgeBaseInput("renamed.txt", first.content),
        )
    )
    assert first_digest != renamed_digest

    with pytest.raises(AuditServiceError, match="32 KiB"):
        AuditService._validate_knowledge_base(KnowledgeBaseInput("description.txt", "a" * (32 * 1024 + 1)))
    with pytest.raises(AuditServiceError, match="plain file name"):
        AuditService._validate_knowledge_base(KnowledgeBaseInput("description\n.txt", "hypothesis"))


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
async def test_delete_scan_requires_admin_and_a_terminal_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-delete",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        owner_subject="admin-1",
    )
    service = object.__new__(AuditService)
    service.store = store
    service.runtime = SimpleNamespace(
        snapshots=SimpleNamespace(snapshots_root=tmp_path / "snapshots"),
    )
    service._active = {}

    with pytest.raises(AuditServiceError) as forbidden:
        await service.delete_scan(
            scan_id,
            AuditCaller(subject="user-1", source="webui"),
        )
    assert forbidden.value.code == "scan_delete_forbidden"
    assert forbidden.value.status_code == 403

    with pytest.raises(AuditServiceError) as running:
        await service.delete_scan(
            scan_id,
            AuditCaller(subject="admin-1", source="webui", is_admin=True),
        )
    assert running.value.code == "scan_delete_conflict"
    assert running.value.status_code == 409
    assert store.get_scan(scan_id) is not None

    output_root = tmp_path / "outputs"
    output = output_root / "2026-08-21" / "code-security" / scan_id
    output.mkdir(parents=True)
    (output / "report.md").write_text("report", encoding="utf-8")
    monkeypatch.setattr(service_module, "find_output_directory", lambda _scan_id: output)
    monkeypatch.setattr(service_module, "outputs_root", lambda: output_root)
    store.start_phase_run(scan_id, "snapshot")
    store.append_scan_event(scan_id, "scan.status", "Completed", {})
    store.mark_scan_terminal(scan_id, "completed")

    await service.delete_scan(
        scan_id,
        AuditCaller(subject="admin-1", source="webui", is_admin=True),
    )

    assert store.get_scan(scan_id) is None
    assert store.get_snapshot("snapshot_test") is None
    assert store.list_phase_runs(scan_id) == []
    assert store.list_scan_events(scan_id, after_seq=0)["items"] == []
    assert not output.exists()
    assert not (tmp_path / "snapshots" / "snapshot_test").exists()


def test_legacy_terminal_scan_uses_and_persists_its_last_update_time(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-legacy",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    store.mark_scan_terminal(scan_id, "completed")
    with store._connect() as connection:
        connection.execute(
            "UPDATE scans SET finished_at = NULL WHERE scan_id = ?",
            (scan_id,),
        )

    legacy = store.get_scan(scan_id)
    assert legacy is not None
    assert _effective_finished_at(legacy) == legacy["updated_at"]

    store.initialize()
    repaired = store.get_scan(scan_id)
    assert repaired is not None
    assert repaired["finished_at"] == repaired["updated_at"]


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


@pytest.mark.asyncio
async def test_invalid_bundle_still_exposes_projected_coverage() -> None:
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        scan_status=lambda _scan_id: {
            "integrity_status": "invalid",
            "integrity_artifacts": {},
        },
        report_data=lambda _scan_id: {"coverage": [{"payload": {"scope": "src"}}]},
    )
    service._require_visible_scan = lambda _scan_id, _caller: {
        "scan_id": "scan_legacy",
        "status": "completed",
    }
    service._artifact_file = lambda _scan_id, _kind: Path("/unsealed/coverage.json")
    service._read_verified_artifact = lambda *_args: (_ for _ in ()).throw(
        AssertionError("unsealed file must not be read")
    )

    artifact = await service.get_artifact(
        "scan_legacy",
        "coverage",
        AuditCaller(subject="admin-1", source="webui", is_admin=True),
    )

    assert artifact == {
        "kind": "coverage",
        "state": "partial",
        "content": [{"payload": {"scope": "src"}}],
    }


def test_legacy_worker_history_projects_completed_phases() -> None:
    phases = AuditService._phase_runs_from_workers(
        "scan_legacy",
        [
            {
                "work_unit_id": "unit_model",
                "phase": "threat_modeling",
                "status": "completed",
                "started_at": "2026-08-21T00:00:00+00:00",
                "finished_at": "2026-08-21T00:00:02+00:00",
            },
            {
                "work_unit_id": "unit_baseline_1",
                "phase": "baseline",
                "status": "completed",
                "started_at": "2026-08-21T00:00:02+00:00",
                "finished_at": "2026-08-21T00:00:05+00:00",
            },
            {
                "work_unit_id": "unit_baseline_2",
                "phase": "baseline",
                "status": "completed",
                "started_at": "2026-08-21T00:00:02+00:00",
                "finished_at": "2026-08-21T00:00:06+00:00",
            },
        ],
    )

    assert [phase["phase"] for phase in phases] == ["threat_modeling", "baseline"]
    assert phases[0]["duration_ms"] == 2_000
    assert phases[1]["duration_ms"] == 4_000
    assert phases[1]["worker_count"] == 2
    assert phases[1]["worker_status_counts"] == {"completed": 2}


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
