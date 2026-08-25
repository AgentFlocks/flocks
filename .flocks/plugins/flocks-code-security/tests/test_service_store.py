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
from flocks_code_security.execution import ExecutionCapsuleError, toolset_digest
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


def test_scan_persists_bounded_verification_vote_count(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        verification_vote_count=3,
    )

    assert store.get_scan(scan_id)["verification_vote_count"] == 3
    with pytest.raises(ValueError, match="between 1 and 5"):
        store.create_scan(
            parent_session_id="session-2",
            snapshot_id="snapshot_test",
            mode="standard",
            ruleset_digest="rules",
            verification_vote_count=6,
        )


def test_dynamic_probe_queue_prioritizes_execution_candidates(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        dynamic_enabled=True,
    )
    candidates = [
        (
            "candidate_rce_metadata",
            {"rule_id": "metadata.rce-label", "category": "source-rce-metadata"},
            "2026-08-24T23:59:59+00:00",
        ),
        (
            "candidate_access",
            {"rule_id": "access-control.idor", "category": "access-control"},
            "2026-08-25T00:00:00+00:00",
        ),
        (
            "candidate_execution",
            {"rule_id": "code-injection.eval", "category": "code-injection"},
            "2026-08-25T00:00:01+00:00",
        ),
    ]
    with store._connect() as connection:
        for candidate_id, payload, created_at in candidates:
            connection.execute(
                "INSERT INTO candidates VALUES (?, ?, NULL, ?, ?, ?)",
                (
                    candidate_id,
                    scan_id,
                    "baseline",
                    json.dumps(payload),
                    created_at,
                ),
            )
            connection.execute(
                "INSERT INTO verifications VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    f"verification_{candidate_id}",
                    candidate_id,
                    scan_id,
                    "confirmed",
                    "confirmed",
                    "[]",
                    created_at,
                ),
            )

    queued = store.list_confirmed_without_dynamic_record(scan_id, limit=1)

    assert [item["candidate_id"] for item in queued] == [
        "candidate_execution"
    ]


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

    verifier_unit = store.create_work_unit(
        scan_id=scan_id,
        phase="verification",
        role="verifier",
        paths=["."],
    )
    store.bind_session(
        session_id="verifier",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier_binding = store.require_binding("verifier", {"verifier"})
    with pytest.raises(ValueError, match="cannot read"):
        store.read_knowledge_base(verifier_binding)

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
    assert first_digest != AuditService._request_digest(
        StartScanRequest(target_path=Path("/tmp/target"), verification_votes=3)
    )

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


def test_progress_recorder_creates_one_phase_run_per_worker_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    monkeypatch.setattr(
        service_module,
        "get_runtime",
        lambda: SimpleNamespace(store=store),
    )
    recorder = _ProgressRecorder(scan_id, dynamic_enabled=False)

    recorder(
        "batch.started",
        {"batch_id": "batch-1", "phase": "threat_modeling", "attempt_ordinal": 1},
    )
    recorder(
        "batch.status",
        {"batch_id": "batch-1", "phase": "threat_modeling", "status": "failed"},
    )
    recorder(
        "batch.started",
        {"batch_id": "batch-2", "phase": "threat_modeling", "attempt_ordinal": 2},
    )
    recorder(
        "batch.status",
        {"batch_id": "batch-2", "phase": "threat_modeling", "status": "completed"},
    )

    phase_runs = store.list_phase_runs(scan_id)
    assert [(item["ordinal"], item["status"]) for item in phase_runs] == [
        (1, "failed"),
        (2, "completed"),
    ]
    assert [item["summary"]["batch_id"] for item in phase_runs] == [
        "batch-1",
        "batch-2",
    ]


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


def test_final_finding_metric_prefers_dynamic_reproductions() -> None:
    metric = service_module._final_finding_metric(
        lifecycle_status="completed",
        integrity_status="valid",
        dynamic_enabled=True,
        finding_summary={"total": 6, "dynamic_reproduced": 2},
        dynamic_summary={
            "status": "completed",
            "ready": 0,
            "completed": 4,
            "inconclusive": 1,
            "not_runnable": 1,
        },
    )

    assert metric == {
        "final_finding_count": 2,
        "final_finding_basis": "动态验证复现",
    }


def test_final_finding_metric_uses_static_results_when_no_probe_was_runnable() -> None:
    metric = service_module._final_finding_metric(
        lifecycle_status="completed",
        integrity_status="valid",
        dynamic_enabled=True,
        finding_summary={"total": 5, "dynamic_reproduced": 0},
        dynamic_summary={
            "status": "not_runnable",
            "ready": 0,
            "completed": 0,
            "inconclusive": 0,
            "not_runnable": 5,
        },
    )

    assert metric == {
        "final_finding_count": 5,
        "final_finding_basis": "静态验证确认",
    }


def test_dynamic_summary_distinguishes_unrunnable_probes_from_success() -> None:
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        report_data=lambda _scan_id: {
            "dynamic_runs": [
                {"status": "not_runnable"},
                {"status": "not_runnable"},
            ]
        }
    )

    summary = service._dynamic_summary(
        {"scan_id": "scan_test", "status": "completed", "counts": {}},
        enabled=True,
    )

    assert summary == {
        "status": "not_runnable",
        "ready": 0,
        "completed": 0,
        "inconclusive": 0,
        "not_runnable": 2,
    }


@pytest.mark.asyncio
async def test_scan_listing_exposes_the_final_finding_metric() -> None:
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        list_scans=lambda **_kwargs: {
            "items": [
                {
                    "scan_id": "scan_completed",
                    "status": "completed",
                    "current_phase": "finalization",
                    "dynamic_enabled": True,
                    "created_at": "2026-08-21T00:00:00+00:00",
                    "updated_at": "2026-08-21T00:10:00+00:00",
                }
            ],
            "next_cursor": None,
        },
        scan_status=lambda _scan_id: {
            "integrity_status": "valid",
            "integrity_artifacts": {"findings.json": "trusted"},
        },
    )
    service._finding_summary = lambda *_args, **_kwargs: {
        "total": 6,
        "dynamic_reproduced": 2,
    }
    service._dynamic_summary = lambda *_args, **_kwargs: {
        "status": "completed",
        "ready": 0,
        "completed": 4,
        "inconclusive": 1,
        "not_runnable": 1,
    }

    page = await service.list_scans(AuditCaller(subject="admin", source="webui", is_admin=True))

    assert page["items"][0]["final_finding_count"] == 2
    assert page["items"][0]["final_finding_basis"] == "动态验证复现"


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


@pytest.mark.asyncio
async def test_scan_detail_reuses_one_report_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-detail",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
        owner_subject="user-1",
    )
    service = object.__new__(AuditService)
    service.store = store
    calls = 0
    original_report_data = store.report_data

    def counted_report_data(selected_scan_id: str) -> dict:
        nonlocal calls
        calls += 1
        return original_report_data(selected_scan_id)

    monkeypatch.setattr(store, "report_data", counted_report_data)

    detail = await service.get_scan(
        scan_id,
        AuditCaller(subject="user-1", source="webui"),
    )

    assert detail["scan"]["scan_id"] == scan_id
    assert calls == 1


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


def test_target_validation_allows_source_projects_in_the_flocks_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flocks_root = tmp_path / ".flocks"
    workspace_root = flocks_root / "workspace"
    source_project = workspace_root / "code-security" / "aiemail"
    source_project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        service_module.WorkspaceManager,
        "get_instance",
        lambda: SimpleNamespace(get_workspace_dir=lambda: workspace_root),
    )

    assert AuditService._validate_target(source_project) == source_project.resolve()


def test_target_validation_still_rejects_flocks_runtime_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flocks_root = tmp_path / ".flocks"
    workspace_root = flocks_root / "workspace"
    runtime_parent = workspace_root / "code-security"
    (runtime_parent / "data").mkdir(parents=True)
    (runtime_parent / "runtime").mkdir()
    (workspace_root / "outputs").mkdir()
    (flocks_root / "data" / "repository").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        service_module.WorkspaceManager,
        "get_instance",
        lambda: SimpleNamespace(get_workspace_dir=lambda: workspace_root),
    )

    for target in (
        workspace_root,
        runtime_parent,
        runtime_parent / "data",
        runtime_parent / "runtime",
        workspace_root / "outputs",
        flocks_root / "data" / "repository",
    ):
        with pytest.raises(AuditServiceError) as rejected:
            AuditService._validate_target(target)
        assert rejected.value.code == "unsafe_target_scope"


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


def test_public_worker_projection_exposes_distinct_verification_results() -> None:
    rationale = "x" * (service_module.MAX_WORKER_RATIONALE_CHARS + 1)
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        report_data=lambda _scan_id: {
            "candidates": [
                {
                    "candidate_id": "candidate-1",
                    "work_unit_id": "unit-baseline",
                    "payload": {
                        "title": "Path traversal in file download",
                        "severity": "high",
                    },
                }
            ],
            "verifications": [
                {
                    "candidate_id": "candidate-1",
                    "work_unit_id": "unit-verifier",
                    "verdict": "confirmed",
                    "rationale": rationale,
                }
            ],
            "coverage": [],
            "submission_rejections": [
                {
                    "rejection_id": "rejection-1",
                    "attempt_id": "attempt-1",
                    "work_unit_id": "unit-verifier",
                    "tool_name": "audit_submit_verdict",
                    "error_code": "EVIDENCE_NOT_READ",
                    "retryable": True,
                    "violations": [{"path": "src/app.py"}],
                    "created_at": "2026-08-21T00:00:00+00:00",
                }
            ],
            "dynamic_runs": [],
            "source_access_counts": {"unit-verifier": {"inventory": 1, "search": 8, "read": 3}},
        },
        list_worker_batches=lambda _scan_id: [
            {
                "units": [
                    {
                        "work_unit_id": "unit-verifier",
                        "phase": "verification",
                        "role": "verifier",
                        "subject_id": "candidate-1",
                        "status": "completed",
                        "created_at": "2026-08-21T00:00:00+00:00",
                        "updated_at": "2026-08-21T00:00:03+00:00",
                        "started_at": "2026-08-21T00:00:01+00:00",
                        "finished_at": "2026-08-21T00:00:03+00:00",
                        "paths": ["."],
                    },
                    {
                        "work_unit_id": "unit-queued",
                        "phase": "verification",
                        "role": "verifier",
                        "subject_id": "candidate-2",
                        "status": "running",
                        "created_at": "2026-08-21T00:00:00+00:00",
                        "updated_at": "2026-08-21T00:00:00+00:00",
                        "started_at": None,
                        "finished_at": None,
                        "paths": ["."],
                    },
                ]
            }
        ],
    )

    workers = service._public_workers("scan-test")
    worker = workers[0]

    assert worker["activity_counts"] == {"inventory": 1, "search": 8, "read": 3}
    assert worker["elapsed_ms"] == 2_000
    assert worker["recent_rejection"] == {
        "rejection_id": "rejection-1",
        "attempt_id": "attempt-1",
        "tool_name": "audit_submit_verdict",
        "error_code": "EVIDENCE_NOT_READ",
        "retryable": True,
        "violation_count": 1,
        "created_at": "2026-08-21T00:00:00+00:00",
    }
    assert worker["candidate_summaries"] == [
        {
            "candidate_id": "candidate-1",
            "title": "Path traversal in file download",
            "severity": "high",
            "verdict": "confirmed",
            "rationale": "x" * service_module.MAX_WORKER_RATIONALE_CHARS,
            "rationale_truncated": True,
        }
    ]
    assert workers[1]["started_at"] is None
    assert workers[1]["elapsed_ms"] is None


def test_public_worker_projection_marks_an_unrunnable_probe() -> None:
    service = object.__new__(AuditService)
    service.store = SimpleNamespace(
        report_data=lambda _scan_id: {
            "candidates": [],
            "verifications": [],
            "coverage": [],
            "dynamic_runs": [
                {
                    "candidate_id": "candidate-1",
                    "probe_work_unit_id": "unit-prober",
                    "status": "not_runnable",
                }
            ],
            "source_access_counts": {},
        },
        list_worker_batches=lambda _scan_id: [
            {
                "units": [
                    {
                        "work_unit_id": "unit-prober",
                        "phase": "probing",
                        "role": "prober",
                        "subject_id": "candidate-1",
                        "status": "completed",
                        "created_at": "2026-08-21T00:00:00+00:00",
                        "updated_at": "2026-08-21T00:00:03+00:00",
                        "started_at": "2026-08-21T00:00:01+00:00",
                        "finished_at": "2026-08-21T00:00:03+00:00",
                        "paths": ["."],
                    }
                ]
            }
        ],
    )

    workers = service._public_workers("scan-test")

    assert workers[0]["status"] == "not_runnable"
    assert workers[0]["record_counts"] == {"dynamic_runs": 1}


def test_finding_summary_counts_only_dynamically_reproduced_findings(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "findings.json"
    artifact.write_text(
        json.dumps(
            {
                "findings": [
                    {
                        "severity": {"level": "high"},
                        "validation": {"dynamicConclusion": "reproduced"},
                    },
                    {
                        "severity": {"level": "medium"},
                        "validation": {"dynamicConclusion": "not_reproduced"},
                    },
                    {
                        "severity": {"level": "low"},
                        "validation": {"conclusion": "confirmed"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    service = object.__new__(AuditService)
    service._artifact_file = lambda _scan_id, _kind: artifact

    summary = service._finding_summary(
        "scan-test",
        verified_artifacts={"findings.json": "trusted-digest"},
    )

    assert summary == {
        "total": 3,
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 1,
        "dynamic_reproduced": 1,
    }


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


def test_work_unit_timing_can_be_synchronized_to_background_task(tmp_path: Path) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="session-1",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="verification",
        role="verifier",
        paths=["."],
    )
    store.bind_session(
        session_id="worker-1",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="verifier",
        work_unit_id=work_unit_id,
    )
    binding = store.require_binding("worker-1", {"verifier"})
    store.set_work_attempt_runtime(
        binding.attempt_id,
        background_task_id="task-1",
        started_at=None,
    )

    queued = store.get_work_unit(work_unit_id)
    assert queued is not None
    assert queued["started_at"] is not None
    attempt = store.list_work_attempts(work_unit_id)[0]
    assert attempt["background_task_id"] == "task-1"
    assert attempt["status"] == "running"

    store.set_work_unit_timing(
        work_unit_id,
        started_at="2026-08-21T00:00:02+00:00",
        finished_at="2026-08-21T00:00:07+00:00",
    )

    work_unit = store.get_work_unit(work_unit_id)
    assert work_unit is not None
    assert work_unit["started_at"] == "2026-08-21T00:00:02+00:00"
    assert work_unit["finished_at"] == "2026-08-21T00:00:07+00:00"

    with pytest.raises(ValueError, match="timezone"):
        store.set_work_unit_timing(
            work_unit_id,
            started_at="2026-08-21T00:00:08",
        )


@pytest.mark.parametrize(
    "mismatch",
    ["identity", "snapshot", "scope", "assignment", "toolset"],
)
def test_execution_capsule_mismatch_fails_closed(
    tmp_path: Path,
    mismatch: str,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="coordinator",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    store.bind_session(
        session_id="worker-1",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="baseline",
        work_unit_id=work_unit_id,
    )
    binding = store.require_binding("worker-1", {"baseline"})
    assert binding.attempt_id is not None

    observed_agent = "code-security-baseline"
    if mismatch == "identity":
        observed_agent = "code-security-verifier"
    elif mismatch == "snapshot":
        other_root = tmp_path / "snapshots" / "snapshot_other"
        other_root.mkdir()
        store.save_snapshot(
            SnapshotRef(
                snapshot_id="snapshot_other",
                repository_identity="other",
                source_revision=None,
                tree_digest="c" * 64,
                scope_digest="d" * 64,
                file_count=0,
                total_bytes=0,
                created_at="2026-08-21T00:00:01+00:00",
                root_path=str(other_root),
            ),
            [],
        )
        with store._connect() as connection:
            connection.execute(
                "UPDATE session_bindings SET snapshot_id = ? WHERE session_id = ?",
                ("snapshot_other", "worker-1"),
            )
        binding = store.require_binding("worker-1", {"baseline"})
    elif mismatch == "scope":
        with store._connect() as connection:
            connection.execute(
                "UPDATE work_units SET paths_json = ? WHERE work_unit_id = ?",
                ('["tampered"]', work_unit_id),
            )
    elif mismatch == "assignment":
        with store._connect() as connection:
            connection.execute(
                "UPDATE work_units SET assignment_digest = ? WHERE work_unit_id = ?",
                ("a" * 64, work_unit_id),
            )

    observed_toolset = "b" * 64 if mismatch == "toolset" else toolset_digest(())
    with pytest.raises(ExecutionCapsuleError):
        store.verify_execution_capsule(
            binding,
            agent_name=observed_agent,
            provider_id=None,
            model_id=None,
            toolset_digest_value=observed_toolset,
        )

    attempt = store.get_work_attempt(binding.attempt_id)
    assert attempt is not None
    assert attempt["status"] == "failed"
    assert attempt["failure_class"] == "identity_capsule_mismatch"
    assert store.get_work_unit(work_unit_id)["status"] == "failed"
    events = store.list_recent_scan_events(scan_id, limit=10)["items"]
    assert events[-1]["type"] == "identity.mismatch"


def test_initialize_backfills_legacy_worker_binding_and_receipts(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="coordinator",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    store.bind_session(
        session_id="legacy-worker",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="baseline",
        work_unit_id=work_unit_id,
    )
    original = store.require_binding("legacy-worker", {"baseline"})
    store.record_source_access(
        original,
        operation="inventory",
        relative_path=".",
    )
    with store._connect() as connection:
        connection.execute(
            "UPDATE session_bindings SET attempt_id = NULL WHERE session_id = ?",
            ("legacy-worker",),
        )
        connection.execute("DROP INDEX source_access_attempt_path_op")
        connection.execute(
            "ALTER TABLE source_access RENAME TO source_access_with_attempt"
        )
        connection.execute(
            """
            CREATE TABLE source_access (
                access_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                scan_id TEXT NOT NULL REFERENCES scans(scan_id) ON DELETE CASCADE,
                work_unit_id TEXT NOT NULL REFERENCES work_units(work_unit_id) ON DELETE CASCADE,
                operation TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                blob_digest TEXT,
                start_line INTEGER,
                end_line INTEGER,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO source_access
            SELECT access_id, session_id, scan_id, work_unit_id, operation,
                   relative_path, blob_digest, start_line, end_line, created_at
            FROM source_access_with_attempt
            """
        )
        connection.execute("DROP TABLE source_access_with_attempt")
        connection.execute(
            "DELETE FROM work_attempts WHERE attempt_id = ?",
            (original.attempt_id,),
        )
        connection.execute(
            "UPDATE work_units SET session_id = ?, background_task_id = ? "
            "WHERE work_unit_id = ?",
            ("legacy-worker", "legacy-task", work_unit_id),
        )

    store.initialize()

    migrated = store.require_binding("legacy-worker", {"baseline"})
    assert migrated.attempt_id is not None
    attempt = store.get_work_attempt(migrated.attempt_id)
    assert attempt is not None
    assert attempt["background_task_id"] == "legacy-task"
    with store._connect() as connection:
        receipt_attempt = connection.execute(
            "SELECT attempt_id FROM source_access WHERE session_id = ?",
            ("legacy-worker",),
        ).fetchone()[0]
    assert receipt_attempt == migrated.attempt_id


def test_initialize_migrates_legacy_coverage_as_untrusted_partial_state(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    scan_id = store.create_scan(
        parent_session_id="coordinator",
        snapshot_id="snapshot_test",
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    store.bind_session(
        session_id="legacy-coverage-worker",
        scan_id=scan_id,
        snapshot_id="snapshot_test",
        role="baseline",
        work_unit_id=work_unit_id,
    )
    with store._connect() as connection:
        connection.execute(
            "INSERT INTO snapshot_files VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("snapshot_test", "app.py", "c" * 64, 12, 1, "python", 0),
        )
        connection.execute(
            "CREATE TABLE coverage ("
            "scan_id TEXT NOT NULL, work_unit_id TEXT NOT NULL, "
            "payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, "
            "PRIMARY KEY (scan_id, work_unit_id))"
        )
        connection.execute(
            "INSERT INTO coverage VALUES (?, ?, ?, ?)",
            (
                scan_id,
                work_unit_id,
                json.dumps(
                    {
                        "analyzed_paths": ["app.py"],
                        "open_questions": [
                            "Which route reaches this file?",
                            *(f"Legacy question {index}" for index in range(99)),
                        ],
                    }
                ),
                "2026-08-25T00:00:00+00:00",
            ),
        )

    store.initialize()
    migrated = store.list_latest_coverage(scan_id)

    assert len(migrated) == 1
    assert migrated[0]["completeness"] == "partial"
    assert migrated[0]["counts"] == {
        "assigned": 1,
        "read_complete": 0,
        "failed": 0,
        "unexamined": 1,
    }
    assert migrated[0]["records"] == [
        {
            "relative_path": "app.py",
            "state": "unexamined",
            "reason": "legacy_coverage_requires_reanalysis",
            "receipt_digest": None,
        }
    ]
    assert migrated[0]["open_questions"][0]["blocking"] is True
    assert "legacy untrusted format" in migrated[0]["open_questions"][0]["question"]
    assert migrated[0]["open_questions"][1]["question"] == (
        "Which route reaches this file?"
    )
    assert len(migrated[0]["open_questions"]) == 100

    store.initialize()
    assert len(store.list_latest_coverage(scan_id)) == 1
