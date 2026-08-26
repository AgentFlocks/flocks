from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from flocks.tool.registry import ToolContext

import flocks_code_security.reporting as reporting_module
import flocks_code_security.runtime as runtime_module
import flocks_code_security.tools as tools_module
from flocks_code_security.contract import validate_document
from flocks_code_security.coverage import normalize_open_questions
from flocks_code_security.execution import ExecutionCapsuleError
from flocks_code_security.orchestration import (
    plan_baseline_units,
    plan_verification_units,
)
from flocks_code_security.runtime import build_runtime
from flocks_code_security.service import AuditService
from flocks_code_security.tools import (
    audit_adjudication_context,
    audit_cancel,
    audit_finalize,
    audit_inventory,
    audit_knowledge_base,
    audit_probe_subject,
    audit_prepare,
    audit_read,
    audit_repository_summary,
    audit_search,
    audit_run_workers,
    audit_submit_candidate,
    audit_submit_coverage,
    audit_submit_probe,
    audit_submit_adjudication,
    audit_submit_threat_model,
    audit_submit_verdict,
    audit_threat_model_context,
    audit_verification_subject,
    audit_wait_workers,
)


def _agent_context(session_id: str, message_id: str, agent: str) -> ToolContext:
    return ToolContext(
        session_id=session_id,
        message_id=message_id,
        agent=agent,
        extra={
            "agent_execution_session": True,
            "model": {"providerID": None, "modelID": None},
            "turn_callable_tool_names": [],
        },
    )


def _candidate_payload(
    evidence: list[dict],
    *,
    title: str = "Untrusted input reaches eval",
    confidence: float = 0.95,
) -> dict:
    enriched_evidence = []
    for index, item in enumerate(evidence):
        enriched_evidence.append(
            {
                **item,
                "label": "Dangerous evaluation" if index == 0 else "Supporting code",
                "role": "root_control" if index == 0 else "propagation",
                "explanation": (
                    "The caller-controlled value is evaluated as code."
                    if index == 0
                    else "This source supports the independently reviewable path."
                ),
            }
        )
    return {
        "rule_id": "code-injection.dynamic-eval",
        "identity_anchor": "handler-input-to-eval",
        "title": title,
        "summary": "Caller-controlled input reaches a dynamic code evaluator.",
        "severity": "high",
        "severity_rationale": "Successful exploitation executes code with process authority.",
        "confidence": confidence,
        "confidence_rationale": "The source shows a direct, guard-free data flow into eval.",
        "category": "code-injection",
        "cwe": ["CWE-95"],
        "attack_path": {
            "summary": "A caller supplies text that the handler evaluates as code.",
            "dataflow": {"summary": "handler input flows directly to eval."},
            "reachability": {"summary": "The handler is callable with attacker-controlled input."},
        },
        "dangerous_operation": "eval(user)",
        "root_cause": "Untrusted text is interpreted as executable code.",
        "remediation": "Replace eval with a strict parser.",
        "remediation_tests": ["Reject code expressions while accepting the intended data format."],
        "preventive_controls": ["Ban dynamic evaluation of untrusted values."],
        "evidence": enriched_evidence,
    }


async def _complete_threat_model(
    runtime,
    *,
    scan_id: str,
    snapshot_id: str,
    session_id: str = "threat-modeler",
) -> str:
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id=session_id,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=work_unit_id,
    )
    modeler = _agent_context(
        session_id,
        f"{session_id}-message",
        "code-security-threat-modeler",
    )
    summary = await audit_repository_summary(modeler)
    assert summary.success
    inventory = await audit_inventory(modeler)
    assert inventory.success
    source_file = next(item for item in inventory.output["files"] if not item["is_binary"])
    source = await audit_read(
        modeler,
        source_file["path"],
        start_line=1,
        end_line=max(1, min(source_file["line_count"], 20)),
    )
    assert source.success
    submitted = await audit_submit_threat_model(
        modeler,
        {
            "summary": "A source-backed test application with a callable entry point.",
            "assets": ["Application integrity and process authority."],
            "trustBoundaries": [f"Caller input crosses into application code at {source_file['path']}:1."],
            "attackerCapabilities": ["A caller may control ordinary application input but not trusted configuration."],
            "securityObjectives": ["Untrusted input must not gain process execution authority."],
            "assumptions": ["Deployment exposure is not established by this fixture."],
            "evidence": [
                {
                    "relative_path": source_file["path"],
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": source.output["end_line"],
                }
            ],
        },
    )
    assert submitted.success
    runtime.store.update_work_unit_status(work_unit_id, "completed")
    return work_unit_id


def _create_verification_batch(
    runtime,
    *,
    scan_id: str,
    candidate_ids: list[str],
) -> dict:
    batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="verification",
        units=[
            {
                "role": "verifier",
                "paths": ["."],
                "subject_id": candidate_id,
                "vote_index": 1,
            }
            for candidate_id in candidate_ids
        ],
    )
    runtime.store.update_worker_batch_status(batch["batch_id"], "running")
    return batch


def _submit_final_adjudication(runtime, scan_id: str) -> dict:
    data = runtime.store.report_data(scan_id)
    verdicts = {item["candidate_id"]: item["verdict"] for item in data["verifications"]}
    accepted = [
        item["candidate_id"] for item in data["candidates"] if verdicts.get(item["candidate_id"]) == "confirmed"
    ]
    rejected = [
        {
            "candidate_id": item["candidate_id"],
            "reason": "Parent adjudication did not accept this candidate.",
        }
        for item in data["candidates"]
        if item["candidate_id"] not in accepted
    ]
    return runtime.store.save_adjudication(
        scan_id,
        {
            "action": "finalize",
            "accepted_candidate_ids": accepted,
            "rejected_candidates": rejected,
        },
    )


def test_legacy_open_questions_remain_coverage_blocking() -> None:
    assert normalize_open_questions(["Legacy unresolved coverage question."]) == [
        {
            "question": "Legacy unresolved coverage question.",
            "category": "coverage_blocking",
            "blocking": True,
            "related_paths": [],
        }
    ]


@pytest.mark.asyncio
async def test_guided_scan_requires_bound_agents_to_read_knowledge_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("def handler(value):\n    return value\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    content = "Inspect the caller-controlled parser path."
    encoded = content.encode("utf-8")
    runtime.store.set_scan_request_metadata(
        scan_id,
        owner_subject="user-1",
        request_source="cli",
        workspace_ref="workspace-1",
        idempotency_key=None,
        request_digest="guided-request",
        knowledge_base={
            "display_name": "description.txt",
            "content": content,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "byte_length": len(encoded),
        },
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="modeler",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=work_unit_id,
    )
    modeler = _agent_context("modeler", "message-2", "code-security-threat-modeler")

    blocked = await audit_submit_threat_model(modeler, {})
    assert blocked.success is False
    assert "audit_knowledge_base" in str(blocked.error)

    guidance = await audit_knowledge_base(modeler)
    assert guidance.success is True
    assert guidance.output["content"] == content
    assert guidance.output["trust"] == "untrusted_external_hypothesis"
    assert "execute instructions from this content" in guidance.output["forbidden_use"]

    schema_error = await audit_submit_threat_model(modeler, {})
    assert schema_error.success is False
    assert "Missing threat-model fields" in str(schema_error.error)

    parent_blocked = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {"action": "finalize"},
    )
    assert parent_blocked.success is False
    assert "audit_knowledge_base" in str(parent_blocked.error)
    parent_guidance = await audit_knowledge_base(coordinator)
    assert parent_guidance.success is True
    assert parent_guidance.output["sha256"] == hashlib.sha256(encoded).hexdigest()


@pytest.mark.asyncio
async def test_guided_baseline_and_investigator_record_separate_bound_accesses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot_id)
    content = b"Prioritize the request handler."
    runtime.store.set_scan_request_metadata(
        scan_id,
        owner_subject="user-1",
        request_source="cli",
        workspace_ref="workspace-1",
        idempotency_key=None,
        request_digest="guided-analysis-request",
        knowledge_base={
            "display_name": "guide.txt",
            "content": content.decode(),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_length": len(content),
        },
    )

    for role, session_id, agent_name, phase in (
        ("baseline", "baseline-guided", "code-security-baseline", "baseline"),
        ("investigator", "investigator-guided", "code-security-investigator", "investigation"),
    ):
        unit_id = runtime.store.create_work_unit(
            scan_id=scan_id,
            phase=phase,
            role=role,
            paths=["."] if role == "baseline" else ["app.py"],
        )
        runtime.store.bind_session(
            session_id=session_id,
            scan_id=scan_id,
            snapshot_id=snapshot_id,
            role=role,
            work_unit_id=unit_id,
        )
        worker = _agent_context(session_id, f"{session_id}-message", agent_name)
        assert (await audit_threat_model_context(worker)).success
        assert (await audit_inventory(worker)).success
        assert (await audit_read(worker, "app.py", start_line=1, end_line=1)).success
        blocked = await audit_submit_coverage(
            worker,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
        assert blocked.success is False
        assert "audit_knowledge_base" in str(blocked.error)
        guidance = await audit_knowledge_base(worker)
        assert guidance.success
        submitted = await audit_submit_coverage(
            worker,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
        assert submitted.success
        runtime.store.update_work_unit_status(unit_id, "completed")

    with runtime.store._connect() as connection:
        accesses = connection.execute(
            "SELECT session_id, role, content_sha256 FROM knowledge_base_access "
            "WHERE scan_id = ? ORDER BY role",
            (scan_id,),
        ).fetchall()
    assert [(item["session_id"], item["role"]) for item in accesses] == [
        ("baseline-guided", "baseline"),
        ("investigator-guided", "investigator"),
    ]
    assert {item["content_sha256"] for item in accesses} == {hashlib.sha256(content).hexdigest()}


def test_adjudication_schema_migrates_to_latest_eight_columns(
    tmp_path: Path,
) -> None:
    root = tmp_path / "plugin-data"
    root.mkdir()
    database = root / "code-security.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE adjudications (
                scan_id TEXT NOT NULL,
                adjudication_round INTEGER NOT NULL,
                action TEXT NOT NULL,
                accepted_candidate_ids_json TEXT NOT NULL,
                rejected_candidates_json TEXT NOT NULL,
                rescan_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, adjudication_round)
            )
            """
        )

    runtime = build_runtime(root)

    with runtime.store._connect() as connection:
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(adjudications)")}
    assert columns == {
        "scan_id",
        "adjudication_round",
        "action",
        "accepted_candidate_ids_json",
        "rejected_candidates_json",
        "rescan_json",
        "dynamic_assessments_json",
        "created_at",
    }


def test_manifest_does_not_claim_non_runnable_probes_executed() -> None:
    manifest = reporting_module.ReportWriter._manifest(
        scan={
            "scan_id": "scan_dynamic",
            "dynamic_enabled": True,
            "created_at": "2026-08-21T00:00:00+00:00",
        },
        snapshot=SimpleNamespace(
            target_kind="directory_snapshot",
            repository_identity="repo",
            display_name="snapshot",
            source_revision=None,
            tree_digest="a" * 64,
            file_count=44,
            copy_source=True,
        ),
        threat_model={},
        coverage={
            "includePaths": ["."],
            "excludePaths": [],
            "limitations": [],
            "deferred": [],
        },
        dynamic_runs=[{"status": "not_runnable"} for _ in range(8)],
        completed_at="2026-08-21T00:10:00+00:00",
        artifacts=[],
    )

    scope = manifest["scan"]["scope"]
    assert scope["runtimeStatus"] == (
        "Dynamic validation results: completed Docker probe pairs: 0; inconclusive attempts: 0; non-runnable probes: 8."
    )
    assert scope["validationMode"].endswith("(no target execution)")


def test_manifest_and_markdown_record_guided_audit_without_raw_content() -> None:
    knowledge_base = {
        "display_name": "description.txt",
        "sha256": "b" * 64,
        "byte_length": 42,
        "trust": "untrusted_external_hypothesis",
    }
    threat_model = {
        "summary": "Source-backed threat model.",
        "assets": [],
        "trustBoundaries": [],
        "attackerCapabilities": [],
        "securityObjectives": [],
        "assumptions": [],
    }
    coverage = {
        "includePaths": ["."],
        "excludePaths": [],
        "limitations": [],
        "deferred": [],
        "completeness": "complete",
        "files": {
            "effectiveCoveragePercent": 100,
            "analyzed": 1,
            "notApplicable": 0,
            "failed": 0,
        },
        "surfaces": [],
        "explicitExclusions": [],
    }
    manifest = reporting_module.ReportWriter._manifest(
        scan={
            "scan_id": "scan_guided",
            "dynamic_enabled": False,
            "created_at": "2026-08-21T00:00:00+00:00",
        },
        snapshot=SimpleNamespace(
            target_kind="git_revision",
            repository_identity="repo",
            display_name="snapshot",
            source_revision="abc123",
            tree_digest="a" * 64,
            file_count=1,
            copy_source=False,
        ),
        threat_model=threat_model,
        coverage=coverage,
        dynamic_runs=[],
        completed_at="2026-08-21T00:10:00+00:00",
        artifacts=[
            {
                "path": "findings.json",
                "sha256": "c" * 64,
                "mediaType": "application/json",
            },
            {
                "path": "coverage.json",
                "sha256": "d" * 64,
                "mediaType": "application/json",
            },
        ],
        knowledge_base=knowledge_base,
    )

    validate_document("manifest", manifest)
    assert manifest["scan"]["target"]["kind"] == "git_revision"
    assert "snapshotDigest" in manifest["scan"]["target"]
    assert "digest-bound source" in manifest["scan"]["scope"]["summary"]
    assert manifest["scan"]["knowledgeBase"] == {
        "displayName": "description.txt",
        "sha256": "b" * 64,
        "byteLength": 42,
        "trust": "untrusted_external_hypothesis",
    }
    markdown = reporting_module.ReportWriter._markdown(
        manifest,
        {
            "documentType": "codex-security.findings",
            "schemaVersion": "1.0",
            "scanId": "scan_guided",
            "findings": [],
        },
        coverage,
    )
    assert "Audit mode: `knowledge-guided`" in markdown
    assert "Knowledge base SHA-256" in markdown
    assert "caller-controlled parser" not in markdown


class _FakeBackgroundManager:
    def __init__(self) -> None:
        self.tasks: dict[str, SimpleNamespace] = {}
        self.calls: list[dict] = []

    async def run_existing_session(self, **kwargs):
        self.calls.append(kwargs)
        task_id = f"task-{len(self.tasks) + 1}"
        task = SimpleNamespace(id=task_id, status="running")
        self.tasks[task_id] = task
        return task

    def get_task(self, task_id: str | None):
        return self.tasks.get(task_id or "")

    def cancel(self, task_id: str | None = None, all_tasks: bool = False) -> int:
        task = self.tasks.get(task_id or "")
        if task is None or task.status in {"completed", "cancelled", "error"}:
            return 0
        task.status = "cancelled"
        return 1


def test_background_task_timestamps_are_projected_as_utc() -> None:
    assert tools_module._background_timestamp(1_000) == "1970-01-01T00:00:01+00:00"
    assert tools_module._background_timestamp(-1) is None
    assert tools_module._background_timestamp(True) is None


@pytest.mark.asyncio
async def test_transient_worker_failure_resumes_same_session_and_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    parent = SimpleNamespace(
        id="coordinator",
        project_id="project",
        directory=str(tmp_path),
        provider="provider",
        model="model",
    )
    children: list[SimpleNamespace] = []

    async def create_child(**kwargs):
        child = SimpleNamespace(
            id=f"worker-{len(children) + 1}",
            agent=kwargs["agent"],
        )
        children.append(child)
        return child

    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", create_child)
    monkeypatch.setattr(Message, "create", AsyncMock())
    monkeypatch.setattr(
        tools_module,
        "get_session_callable_tools",
        AsyncMock(
            return_value={
                "audit_repository_summary",
                "audit_inventory",
                "audit_read",
                "audit_search",
                "audit_submit_threat_model",
            }
        ),
    )
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    launched = await audit_run_workers(
        coordinator,
        prepared.output["scan_id"],
        "threat_modeling",
    )
    first_worker = launched.output["workers"][0]
    manager.tasks["task-1"].status = "error"
    manager.tasks["task-1"].error = "provider 503 stream interrupted"

    resumed = await audit_wait_workers(
        coordinator,
        launched.output["batch_id"],
        timeout_seconds=0,
    )

    resumed_worker = resumed.output["workers"][0]
    assert resumed.output["status"] == "running"
    assert resumed_worker["attempt_id"] == first_worker["attempt_id"]
    assert resumed_worker["attempt_ordinal"] == 1
    assert resumed_worker["resume_count"] == 1
    assert manager.calls[1]["session_id"] == "worker-1"
    assert (
        manager.calls[1]["execution_capsule"]["capsule_digest"]
        == manager.calls[0]["execution_capsule"]["capsule_digest"]
    )

    manager.tasks["task-2"].status = "error"
    manager.tasks["task-2"].error = "provider 503 stream interrupted"
    fresh = await audit_wait_workers(
        coordinator,
        launched.output["batch_id"],
        timeout_seconds=0,
    )
    fresh_worker = fresh.output["workers"][0]
    assert fresh.output["status"] == "running"
    assert fresh_worker["attempt_id"] != first_worker["attempt_id"]
    assert fresh_worker["attempt_ordinal"] == 2
    assert manager.calls[2]["session_id"] == "worker-2"

    manager.tasks["task-3"].status = "completed"
    exhausted = await audit_wait_workers(
        coordinator,
        launched.output["batch_id"],
        timeout_seconds=0,
    )
    assert exhausted.output["status"] == "failed"
    attempts = runtime.store.list_work_attempts(
        exhausted.output["workers"][0]["work_unit_id"]
    )
    assert [attempt["status"] for attempt in attempts] == ["failed", "failed"]
    assert attempts[-1]["failure_class"] == "attempts_exhausted"


@pytest.mark.asyncio
async def test_analysis_progress_without_coverage_gets_one_coverage_only_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot_id)

    parent = SimpleNamespace(
        id="coordinator",
        project_id="project",
        directory=str(tmp_path),
        provider="provider",
        model="model",
    )
    children: list[SimpleNamespace] = []

    async def create_child(**kwargs):
        child = SimpleNamespace(id=f"worker-{len(children) + 1}", agent=kwargs["agent"])
        children.append(child)
        return child

    callable_tools = {
        "audit_knowledge_base",
        "audit_threat_model_context",
        "audit_inventory",
        "audit_read",
        "audit_search",
        "audit_submit_candidate",
        "audit_submit_coverage",
    }
    messages = AsyncMock()
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", create_child)
    monkeypatch.setattr(Message, "create", messages)
    monkeypatch.setattr(
        tools_module,
        "get_session_callable_tools",
        AsyncMock(return_value=callable_tools),
    )
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)

    launched = await audit_run_workers(coordinator, scan_id, "baseline")
    assert launched.success
    worker = launched.output["workers"][0]
    baseline = _agent_context("worker-1", "baseline-message", "code-security-baseline")
    baseline.extra.update(
        model={"providerID": "provider", "modelID": "model"},
        turn_callable_tool_names=sorted(callable_tools),
    )
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    manager.tasks["task-1"].status = "completed"

    resumed = await audit_wait_workers(
        coordinator,
        launched.output["batch_id"],
        timeout_seconds=0,
    )

    assert resumed.output["status"] == "running"
    assert resumed.output["workers"][0]["attempt_id"] == worker["attempt_id"]
    assert resumed.output["workers"][0]["resume_count"] == 1
    assert len(children) == 1
    assert manager.calls[1]["session_id"] == "worker-1"
    assert "Do not restart or broaden" in messages.await_args_list[-1].kwargs["content"]

    manager.tasks["task-2"].status = "completed"
    exhausted = await audit_wait_workers(
        coordinator,
        launched.output["batch_id"],
        timeout_seconds=0,
    )
    assert exhausted.output["status"] == "failed"
    assert exhausted.output["workers"][0]["failure_class"] == "coverage_attestation_missing"
    assert len(children) == 1
    assert runtime.store.list_source_accesses(worker["attempt_id"])


@pytest.mark.asyncio
async def test_coverage_resume_capsule_mismatch_fails_the_work_unit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    unit = {
        "work_unit_id": "unit_baseline",
        "status": "running",
        "role": "baseline",
        "attempt_id": "attempt_baseline",
        "resume_count": 0,
        "background_task_id": "task_baseline",
        "started_at": None,
        "finished_at": None,
    }
    batch = {
        "batch_id": "batch_baseline",
        "scan_id": "scan_test",
        "status": "running",
        "units": [unit],
    }

    class Store:
        def __init__(self) -> None:
            self.events: list[tuple] = []

        @staticmethod
        def work_unit_has_required_facts(*_args, **_kwargs):
            return False

        @staticmethod
        def work_attempt_has_analysis_progress(_attempt_id: str):
            return True

        @staticmethod
        def get_worker_batch(_batch_id: str):
            return batch

        @staticmethod
        def finish_work_attempt(
            attempt_id: str,
            *,
            status: str,
            failure_class: str,
            work_unit_status: str,
        ) -> None:
            assert attempt_id == "attempt_baseline"
            assert status == "failed"
            unit["status"] = work_unit_status
            unit["failure_class"] = failure_class

        def append_scan_event(self, *args, **kwargs) -> None:
            self.events.append((args, kwargs))

        @staticmethod
        def update_worker_batch_status(_batch_id: str, status: str) -> None:
            batch["status"] = status

    store = Store()
    task = SimpleNamespace(
        status="completed",
        started_at=None,
        completed_at=None,
        error=None,
    )
    manager = SimpleNamespace(get_task=lambda _task_id: task)
    resume = AsyncMock(side_effect=ExecutionCapsuleError("digest mismatch"))
    monkeypatch.setattr(
        tools_module,
        "get_runtime",
        lambda: SimpleNamespace(store=store),
    )
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)
    monkeypatch.setattr(tools_module, "_resume_worker_attempt", resume)

    refreshed = await tools_module._refresh_worker_batch(
        "batch_baseline",
        ctx=SimpleNamespace(),
    )

    assert refreshed["status"] == "failed"
    assert unit["failure_class"] == "identity_capsule_mismatch"
    assert store.events[0][0][1] == "identity.mismatch"


@pytest.mark.asyncio
async def test_threat_model_without_baseline_cannot_be_finalized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    await _complete_threat_model(
        runtime,
        scan_id=prepared.output["scan_id"],
        snapshot_id=prepared.output["snapshot"]["snapshot_id"],
    )

    finalized = await audit_finalize(coordinator, prepared.output["scan_id"])

    assert finalized.success is False
    assert "baseline analysis" in str(finalized.error)


@pytest.mark.asyncio
async def test_threat_model_submission_is_role_bound_and_schema_validated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("def handler(value):\n    return value\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="modeler",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=work_unit_id,
    )
    modeler = _agent_context(
        "modeler",
        "message-2",
        "code-security-threat-modeler",
    )
    source = await audit_read(modeler, "app.py", start_line=1, end_line=2)
    incomplete = await audit_submit_threat_model(
        modeler,
        {"summary": "Missing canonical fields."},
    )
    assert incomplete.success is False
    assert "Missing threat-model fields" in str(incomplete.error)

    payload = {
        "summary": "A caller invokes a local application handler.",
        "assets": ["Application integrity."],
        "trustBoundaries": ["Caller input enters app.py:1."],
        "attackerCapabilities": ["The caller controls the value argument."],
        "securityObjectives": ["Input remains data."],
        "assumptions": [],
        "evidence": [
            {
                "relative_path": "app.py",
                "blob_digest": source.output["blob_digest"],
                "start_line": 1,
                "end_line": 2,
            }
        ],
    }
    invalid_type = await audit_submit_threat_model(
        modeler,
        {**payload, "assets": [1]},
    )
    assert invalid_type.success is False
    assert "array of at most 100 strings" in str(invalid_type.error)
    missing_summary = await audit_submit_threat_model(modeler, payload)
    assert missing_summary.success is False
    assert "repository summary" in str(missing_summary.error)
    assert missing_summary.metadata["error_code"] == "MANIFEST_NOT_CONSUMED"
    assert missing_summary.metadata["retryable"] is True
    assert missing_summary.metadata["rejection_id"].startswith("rejection_")
    rejection = runtime.store.report_data(scan_id)["submission_rejections"][-1]
    assert rejection["attempt_id"] == runtime.store.resolve_binding(
        "modeler"
    ).attempt_id
    assert rejection["tool_name"] == "audit_submit_threat_model"
    assert rejection["error_code"] == "MANIFEST_NOT_CONSUMED"
    events = runtime.store.list_recent_scan_events(scan_id, limit=10)["items"]
    assert events[-1]["type"] == "submission.rejected"
    assert events[-1]["summary"]["rejection_id"] == rejection["rejection_id"]
    assert runtime.store.get_work_unit(work_unit_id)["status"] == "running"
    assert (await audit_repository_summary(modeler)).success

    wrong_evidence_shape = await audit_submit_threat_model(
        modeler,
        {
            **payload,
            "evidence": [
                {
                    "path": "app.py",
                    "digest": source.output["blob_digest"],
                    "lines": "1-2",
                }
            ],
        },
    )
    assert wrong_evidence_shape.success is False
    assert "relative_path, blob_digest, start_line, end_line" in str(wrong_evidence_shape.error)

    placeholder = await audit_submit_threat_model(
        modeler,
        {
            "summary": "minimal",
            "assets": ["a"],
            "trustBoundaries": ["b"],
            "attackerCapabilities": ["c"],
            "securityObjectives": ["d"],
            "assumptions": ["e"],
            "evidence": payload["evidence"],
        },
    )
    assert placeholder.success is True
    assert placeholder.output["operation"] == "created"

    submitted = await audit_submit_threat_model(modeler, payload)
    assert submitted.success is True
    assert submitted.output["operation"] == "updated"
    assert runtime.store.get_threat_model(scan_id)["threat_model"]["summary"] == payload["summary"]

    identity_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="wrong-agent-modeler",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=identity_unit,
    )
    wrong_agent = await audit_submit_threat_model(
        _agent_context(
            "wrong-agent-modeler",
            "message-3",
            "code-security-baseline",
        ),
        payload,
    )
    assert wrong_agent.success is False
    assert wrong_agent.metadata["error_code"] == "IDENTITY_CAPSULE_MISMATCH"
    assert wrong_agent.metadata["retryable"] is False
    assert runtime.store.get_work_unit(identity_unit)["status"] == "failed"

    missing_capsule_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="missing-capsule-modeler",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=missing_capsule_unit,
    )
    missing_capsule = await audit_read(
        ToolContext(
            session_id="missing-capsule-modeler",
            message_id="message-4",
            agent="code-security-threat-modeler",
            extra={"agent_execution_session": True},
        ),
        "app.py",
        start_line=1,
        end_line=1,
    )
    assert missing_capsule.success is False
    assert missing_capsule.metadata["error_code"] == "IDENTITY_CAPSULE_MISMATCH"
    assert runtime.store.get_work_unit(missing_capsule_unit)["status"] == "failed"

    runtime.store.update_work_unit_status(work_unit_id, "completed")
    late_update = await audit_submit_threat_model(modeler, payload)
    assert late_update.success is False
    assert late_update.metadata["error_code"] == "ATTEMPT_NOT_ACTIVE"

    with runtime.store._connect() as connection:
        connection.execute(
            "UPDATE threat_models SET payload_json = ? WHERE scan_id = ?",
            (
                json.dumps(
                    {
                        "summary": "",
                        "assets": ["a"],
                        "trustBoundaries": ["b"],
                        "attackerCapabilities": ["c"],
                        "securityObjectives": ["d"],
                        "assumptions": ["e"],
                    }
                ),
                scan_id,
            ),
        )
    assert (
        runtime.store.work_unit_has_required_facts(
            work_unit_id,
            role="threat_modeler",
        )
        is False
    )
    invalid_status = runtime.store.scan_status(scan_id)
    assert invalid_status["threat_model_status"] == "invalid"
    assert invalid_status["integrity_status"] == "invalid"
    assert "non-empty string" in invalid_status["integrity_errors"][0]
    with pytest.raises(ValueError, match="failed contract validation"):
        runtime.store.require_threat_model_ready(scan_id)


@pytest.mark.asyncio
async def test_large_repository_threat_model_needs_summary_not_inventory_pagination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("def handler(value):\n    return value\n", encoding="utf-8")
    for index in range(501):
        (target / f"module_{index:03d}.py").write_text("safe = True\n", encoding="utf-8")

    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="modeler-large",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="threat_modeler",
        work_unit_id=work_unit_id,
    )
    modeler = _agent_context(
        "modeler-large",
        "message-2",
        "code-security-threat-modeler",
    )

    summary = await audit_repository_summary(modeler)
    assert summary.success is True
    assert summary.output["file_count"] == 502
    source = await audit_read(modeler, "app.py", start_line=1, end_line=2)
    submitted = await audit_submit_threat_model(
        modeler,
        {
            "summary": "A caller invokes a local application handler.",
            "assets": ["Application integrity."],
            "trustBoundaries": ["Caller input enters app.py:1."],
            "attackerCapabilities": ["The caller controls the value argument."],
            "securityObjectives": ["Input remains data."],
            "assumptions": [],
            "evidence": [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                }
            ],
        },
    )

    assert submitted.success is True
    with runtime.store._connect() as connection:
        inventory_count = connection.execute(
            "SELECT COUNT(*) FROM source_access WHERE work_unit_id = ? AND operation = 'inventory'",
            (work_unit_id,),
        ).fetchone()[0]
    assert inventory_count == 0


@pytest.mark.asyncio
async def test_prepare_candidate_verify_finalize_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text(
        "def handler(user):\n    return eval(user)\n",
        encoding="utf-8",
    )
    (target / "aaa_helper.py").write_text("def helper():\n    return True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )

    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    assert prepared.success is True
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )

    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    unconsumed_threat_model = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert unconsumed_threat_model.success is False
    assert "threat-model context" in str(unconsumed_threat_model.error)
    assert (await audit_threat_model_context(baseline)).success
    unbacked_coverage = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert unbacked_coverage.success is False
    assert "not backed" in str(unbacked_coverage.error)
    assert (await audit_inventory(baseline)).success
    assert (await audit_search(baseline, "return", path_glob="app.py")).success
    search_only_coverage = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert search_only_coverage.success is False
    assert search_only_coverage.metadata["error_code"] == "COVERAGE_OVERCLAIM"
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    partial_read_coverage = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert partial_read_coverage.success is False
    assert "complete snapshot source reads" in str(partial_read_coverage.error)
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    auxiliary = await audit_read(
        baseline,
        "aaa_helper.py",
        start_line=1,
        end_line=2,
    )
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                },
                {
                    "relative_path": "aaa_helper.py",
                    "blob_digest": auxiliary.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                },
            ]
        ),
    )
    assert candidate.success is True
    coverage = await audit_submit_coverage(
        baseline,
        dispositions=[
            {"path": "aaa_helper.py", "claim": "analyzed"},
            {"path": "app.py", "claim": "analyzed"},
        ],
    )
    assert coverage.success is True
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verification_batch = _create_verification_batch(
        runtime,
        scan_id=scan_id,
        candidate_ids=[candidate.output["candidate_id"]],
    )
    verifier_unit = verification_batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="verifier",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier = _agent_context("verifier", "message-3", "code-security-verifier")
    unbacked_verdict = await audit_submit_verdict(
        verifier,
        candidate.output["candidate_id"],
        "confirmed",
        "Untrusted self-assertion without reading source.",
    )
    assert unbacked_verdict.success is False
    assert "audit_verification_subject" in str(unbacked_verdict.error)
    assert (await audit_verification_subject(verifier)).success
    counter_evidence_bypass = await audit_submit_verdict(
        verifier,
        candidate.output["candidate_id"],
        "confirmed",
        "Counter-evidence validation must not count as an independent source read.",
        counter_evidence=[
            {
                "relative_path": "app.py",
                "blob_digest": source.output["blob_digest"],
                "start_line": 1,
                "end_line": 2,
            },
            {
                "relative_path": "aaa_helper.py",
                "blob_digest": auxiliary.output["blob_digest"],
                "start_line": 1,
                "end_line": 2,
            },
        ],
    )
    assert counter_evidence_bypass.success is False
    assert "independently read" in str(counter_evidence_bypass.error)
    unread_verdict = await audit_submit_verdict(
        verifier,
        candidate.output["candidate_id"],
        "confirmed",
        "Candidate was retrieved but source evidence was not read.",
    )
    assert unread_verdict.success is False
    assert "independently read" in str(unread_verdict.error)
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    assert (await audit_read(verifier, "aaa_helper.py", start_line=1, end_line=2)).success
    verdict = await audit_submit_verdict(
        verifier,
        candidate.output["candidate_id"],
        "confirmed",
        "The public handler argument reaches eval directly.",
    )
    assert verdict.success is True
    runtime.store.update_work_unit_status(verifier_unit, "completed")
    runtime.store.update_worker_batch_status(
        verification_batch["batch_id"],
        "completed",
    )
    adjudication = _submit_final_adjudication(runtime, scan_id)
    assert adjudication["dynamic_assessments"] is None

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "completed"
    completed_status = runtime.store.scan_status(scan_id)
    assert completed_status["integrity_status"] == "valid"
    assert completed_status["integrity_errors"] == []
    output_path = Path(finalized.output["output_dir"])
    assert (output_path / "report.md").is_file()
    assert (output_path / "findings.json").read_text(encoding="utf-8").count("code-injection.dynamic-eval") == 1
    assert (output_path / "report.sarif").is_file()
    findings_document = json.loads((output_path / "findings.json").read_text(encoding="utf-8"))
    findings = findings_document["findings"]
    assert findings_document["documentType"] == "codex-security.findings"
    assert findings[0]["locations"][0]["path"] == "app.py"
    assert findings[0]["findingId"].startswith("csf_")
    assert findings[0]["occurrenceId"].startswith("occ_")
    assert findings[0]["fingerprints"]["algorithm"] == "codex-security/v1"
    assert findings[0]["taxonomy"]["cwe"] == ["CWE-95"]
    assert findings[0]["validation"]["conclusion"] == "confirmed"
    assert findings[0]["attackPath"]["dataflow"]["summary"]
    sarif = json.loads((output_path / "report.sarif").read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "app.py"
    assert output_path.stat().st_mode & 0o777 == 0o700
    assert (output_path / "report.md").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((output_path / "scan-manifest.json").read_text(encoding="utf-8"))
    coverage_document = json.loads((output_path / "coverage.json").read_text(encoding="utf-8"))
    validate_document("manifest", manifest)
    validate_document("findings", findings_document)
    validate_document("coverage", coverage_document)
    assert manifest["documentType"] == "codex-security.scan-manifest"
    assert manifest["scan"]["status"] == "completed"
    assert manifest["scan"]["sealedAt"] == manifest["scan"]["completedAt"]
    assert manifest["scan"]["threatModel"]["trustBoundaries"]
    assert any(artifact["path"] == "adjudication.json" for artifact in manifest["scan"]["artifacts"])
    assert coverage_document["completeness"] == "complete"
    for artifact in manifest["scan"]["artifacts"]:
        contents = (output_path / artifact["path"]).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == artifact["sha256"]
    threat_model = json.loads((output_path / "threat-model.json").read_text(encoding="utf-8"))
    adjudication = json.loads((output_path / "adjudication.json").read_text(encoding="utf-8"))
    assert threat_model["threatModel"]["trustBoundaries"]
    assert adjudication["adjudications"][-1]["action"] == "finalize"
    assert threat_model["evidence"][0]["relative_path"] in {
        "aaa_helper.py",
        "app.py",
    }
    assert "result_status" not in manifest["scan"]
    assert (output_path / ".scan-manifest.final").exists() is False

    legacy_manifest = json.loads(json.dumps(manifest))
    legacy_manifest["scan"]["artifacts"] = [
        artifact
        for artifact in legacy_manifest["scan"]["artifacts"]
        if artifact["path"]
        not in reporting_module.LEGACY_UNSEALED_REPORT_ARTIFACTS
    ]
    manifest_path = output_path / "scan-manifest.json"
    reporting_module.ReportWriter._write_json(manifest_path, legacy_manifest)
    legacy_manifest_bytes = manifest_path.read_bytes()
    legacy_status = runtime.store.scan_status(scan_id)
    assert legacy_status["integrity_status"] == "invalid"
    assert "Required sealed artifacts are missing" in legacy_status["integrity_errors"][0]

    writer = reporting_module.ReportWriter(runtime.store)
    assert writer.reseal_legacy_bundle(scan_id, output_path) is True
    assert writer.reseal_legacy_bundle(scan_id, output_path) is False
    assert runtime.store.scan_status(scan_id)["integrity_status"] == "valid"
    repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    repaired_paths = {
        artifact["path"] for artifact in repaired_manifest["scan"]["artifacts"]
    }
    assert reporting_module.LEGACY_UNSEALED_REPORT_ARTIFACTS <= repaired_paths

    reporting_module.ReportWriter._write_bytes(manifest_path, legacy_manifest_bytes)
    (output_path / "report.md").write_text("tampered\n", encoding="utf-8")
    assert writer.reseal_legacy_bundle(scan_id, output_path) is False
    tampered_status = runtime.store.scan_status(scan_id)
    assert tampered_status["integrity_status"] == "invalid"
    assert "report.md" in tampered_status["integrity_errors"][0]


@pytest.mark.asyncio
async def test_dynamic_report_seals_facts_and_promotes_only_reproduced_poc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text(
        "def handler(user):\n    return eval(user)\n",
        encoding="utf-8",
    )
    (target / "Dockerfile").write_text(
        "FROM local/test:latest\nCOPY . /app\n",
        encoding="utf-8",
    )
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("dynamic-coordinator", "message-1", "code-security")
    prepared = await audit_prepare(
        coordinator,
        str(target),
        dynamic_enabled=True,
    )
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        session_id="dynamic-modeler",
    )

    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="dynamic-baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context(
        "dynamic-baseline",
        "message-2",
        "code-security-baseline",
    )
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    assert source.success
    assert (await audit_search(baseline, "not-present-in-fixture")).success
    assert (await audit_read(baseline, "Dockerfile", start_line=1, end_line=2)).success
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                }
            ]
        ),
    )
    assert candidate.success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[
                {"path": "Dockerfile", "claim": "analyzed"},
                {"path": "app.py", "claim": "analyzed"},
            ],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verification_batch = _create_verification_batch(
        runtime,
        scan_id=scan_id,
        candidate_ids=[candidate.output["candidate_id"]],
    )
    verifier_unit = verification_batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="dynamic-verifier",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier = _agent_context(
        "dynamic-verifier",
        "message-3",
        "code-security-verifier",
    )
    assert (await audit_verification_subject(verifier)).success
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    assert (
        await audit_submit_verdict(
            verifier,
            candidate.output["candidate_id"],
            "confirmed",
            "The public argument reaches eval without a guard.",
        )
    ).success
    runtime.store.update_work_unit_status(verifier_unit, "completed")
    runtime.store.update_worker_batch_status(
        verification_batch["batch_id"],
        "completed",
    )

    batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="probing",
        units=[
            {
                "role": "prober",
                "paths": ["."],
                "subject_id": candidate.output["candidate_id"],
            }
        ],
    )
    prober_unit = batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="dynamic-prober",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="prober",
        work_unit_id=prober_unit,
    )
    prober = _agent_context(
        "dynamic-prober",
        "message-4",
        "code-security-prober",
    )
    assert (await audit_probe_subject(prober)).success
    assert (await audit_inventory(prober)).success
    assert (await audit_read(prober, "app.py", start_line=1, end_line=2)).success
    assert (await audit_search(prober, "eval")).success
    submitted_probe = await audit_submit_probe(
        prober,
        {
            "candidate_id": candidate.output["candidate_id"],
            "status": "runnable",
            "context_path": ".",
            "dockerfile_path": "Dockerfile",
            "control": {"script": "python /app/app.py safe", "timeout_seconds": 10},
            "attack": {"script": "python /app/app.py attack", "timeout_seconds": 10},
            "expected_difference": "Attack demonstrates evaluation unavailable to control.",
        },
    )
    assert submitted_probe.success
    runtime.store.update_work_unit_status(prober_unit, "completed")
    runtime.store.update_worker_batch_status(batch["batch_id"], "running")
    runtime.store.update_worker_batch_status(batch["batch_id"], "completed")
    with pytest.raises(ValueError, match="still pending"):
        runtime.store.get_adjudication_context(scan_id)
    runtime.store.complete_dynamic_run(
        candidate.output["candidate_id"],
        "completed",
        {
            "runner_status": "completed",
            "build": {"exit_code": 0},
            "control": {"exit_code": 0, "stdout": "safe"},
            "attack": {"exit_code": 0, "stdout": "executed"},
        },
    )
    with pytest.raises(ValueError, match="already completed"):
        runtime.store.complete_dynamic_run(
            candidate.output["candidate_id"],
            "completed",
            {"runner_status": "completed"},
        )
    with pytest.raises(ValueError, match="require one assessment"):
        runtime.store.save_adjudication(
            scan_id,
            {
                "action": "finalize",
                "accepted_candidate_ids": [candidate.output["candidate_id"]],
                "rejected_candidates": [],
            },
        )
    with pytest.raises(ValueError, match="does not match run status"):
        runtime.store.save_adjudication(
            scan_id,
            {
                "action": "finalize",
                "accepted_candidate_ids": [candidate.output["candidate_id"]],
                "rejected_candidates": [],
                "dynamic_assessments": [
                    {
                        "candidate_id": candidate.output["candidate_id"],
                        "conclusion": "inconclusive",
                        "rationale": "Mismatched conclusion for completed facts.",
                    }
                ],
            },
        )
    runtime.store.save_adjudication(
        scan_id,
        {
            "action": "finalize",
            "accepted_candidate_ids": [candidate.output["candidate_id"]],
            "rejected_candidates": [],
            "dynamic_assessments": [
                {
                    "candidate_id": candidate.output["candidate_id"],
                    "conclusion": "reproduced",
                    "rationale": "Attack output demonstrates the claimed effect while control does not.",
                }
            ],
        },
    )

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success
    output = Path(finalized.output["output_dir"])
    candidate_id = candidate.output["candidate_id"]
    assert (output / "dynamic-validation.json").is_file()
    assert (output / "poc" / candidate_id / "probe.sh").is_file()
    assert (output / "poc" / candidate_id / "poc.json").is_file()
    findings = json.loads((output / "findings.json").read_text(encoding="utf-8"))
    validation = findings["findings"][0]["validation"]
    assert validation["dynamicConclusion"] == "reproduced"
    assert validation["pocRef"] == f"poc/{candidate_id}/probe.sh"
    manifest = json.loads((output / "scan-manifest.json").read_text(encoding="utf-8"))
    assert "completed Docker probe pairs: 1" in manifest["scan"]["scope"]["runtimeStatus"]
    sealed_paths = {item["path"] for item in manifest["scan"]["artifacts"]}
    assert "dynamic-validation.json" in sealed_paths
    assert f"poc/{candidate_id}/probe.sh" in sealed_paths


@pytest.mark.asyncio
async def test_empty_files_and_static_limitations_do_not_make_coverage_partial(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "assets").mkdir()
    (target / "docs").mkdir()
    (target / "tests").mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    (target / "empty.py").write_text("", encoding="utf-8")
    (target / "assets" / "logo.svg").write_text("<svg/>\n", encoding="utf-8")
    (target / "docs" / "security.md").write_text("guide\n", encoding="utf-8")
    (target / "tests" / "test_app.py").write_text(
        "def test_app(): pass\n",
        encoding="utf-8",
    )
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )

    planned_units = plan_baseline_units(
        runtime.manifests.get_or_build(snapshot_id)
    )
    assert len(planned_units) == 1
    assert planned_units[0]["paths"] == ["."]
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=planned_units[0]["paths"],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    assert (await audit_read(baseline, "assets/logo.svg", start_line=1, end_line=1)).success
    assert (await audit_read(baseline, "docs/security.md", start_line=1, end_line=1)).success
    assert (await audit_read(baseline, "tests/test_app.py", start_line=1, end_line=1)).success

    invalid = await audit_submit_coverage(
        baseline,
        dispositions=[
            {"path": path, "claim": "analyzed"}
            for path in (
                "app.py",
                "assets/logo.svg",
                "docs/security.md",
                "tests/test_app.py",
            )
        ],
        open_questions=[
            {
                "question": "Deployment controls are outside the source snapshot.",
                "category": "validation_limitation",
                "blocking": True,
            }
        ],
    )
    assert invalid.success is False
    assert "coverage_blocking" in str(invalid.error)

    submitted = await audit_submit_coverage(
        baseline,
        dispositions=[
            {"path": path, "claim": "analyzed"}
            for path in (
                "app.py",
                "assets/logo.svg",
                "docs/security.md",
                "tests/test_app.py",
            )
        ],
        open_questions=[
            {
                "question": "Deployment controls are outside the source snapshot.",
                "category": "validation_limitation",
                "blocking": False,
                "follow_up": "Provide the production gateway configuration.",
            }
        ],
    )
    assert submitted.success is True
    runtime.store.update_work_unit_status(unit_id, "completed")
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    output_path = Path(finalized.output["output_dir"])
    coverage = json.loads((output_path / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["completeness"] == "complete"
    assert coverage["deferred"] == []
    assert not {"*.svg", "docs", "tests"} & set(coverage["excludePaths"])
    assert coverage["files"] == {
        "total": 5,
        "inventoried": 5,
        "analyzed": 4,
        "notApplicable": 1,
        "failed": 0,
        "effectiveCoveragePercent": 100,
    }
    assert coverage["limitations"][0]["category"] == "validation_limitation"
    receipt_path = coverage["surfaces"][0]["receiptRefs"][0]
    receipt = json.loads((output_path / receipt_path).read_text(encoding="utf-8"))
    assert receipt["notApplicable"] == ["empty.py"]
    report = (output_path / "report.md").read_text(encoding="utf-8")
    assert "Effective source coverage: **100%**" in report
    assert "Static Validation Limitations" in report


@pytest.mark.asyncio
async def test_host_attestation_records_partial_coverage_from_current_attempt_receipts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\nsecond\n", encoding="utf-8")
    (target / "unread.py").write_text("unread = True\n", encoding="utf-8")
    (target / "empty.py").write_text("", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )

    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=2)).success

    submitted = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )

    assert submitted.success is True
    assert submitted.output["policy"] == "evidence_backed_partial"
    assert submitted.output["completeness"] == "partial"
    assert submitted.output["counts"] == {
        "assigned": 3,
        "read_complete": 1,
        "failed": 0,
        "unexamined": 1,
    }
    records = runtime.store.list_coverage_records(submitted.output["attestation_id"])
    assert {item["relative_path"]: item["state"] for item in records} == {
        "app.py": "read_complete",
        "empty.py": "not_applicable",
        "unread.py": "inventoried",
    }
    assert runtime.store.work_unit_has_required_facts(unit_id, role="baseline") is True
    summary = runtime.store.analysis_coverage_summary(scan_id)
    assert summary["policy"] == "evidence_backed_partial"
    assert summary["completeness"] == "partial"
    assert summary["counts"] == {
        "assigned": 3,
        "read_complete": 1,
        "failed": 0,
        "unexamined": 1,
        "active_gaps": 1,
    }

    runtime.store.update_work_unit_status(unit_id, "completed")
    _submit_final_adjudication(runtime, scan_id)
    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    output_path = Path(finalized.output["output_dir"])
    coverage = json.loads((output_path / "coverage.json").read_text(encoding="utf-8"))
    assert coverage["completeness"] == "partial"
    assert any(item.get("paths") == ["unread.py"] for item in coverage["deferred"])
    receipt_path = coverage["surfaces"][0]["receiptRefs"][0]
    receipt = json.loads((output_path / receipt_path).read_text(encoding="utf-8"))
    assert receipt["attestationId"] == submitted.output["attestation_id"]
    assert receipt["unexamined"] == ["unread.py"]
    report = (output_path / "report.md").read_text(encoding="utf-8")
    assert "Unexamined source files: **1**" in report


def test_service_coverage_status_uses_terminal_scan_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    snapshot = runtime.snapshots.create(str(target))
    service = AuditService()

    for policy, expected in (
        ("evidence_backed_partial", "partial"),
        ("exhaustive", "blocked"),
    ):
        scan_id = runtime.store.create_scan(
            parent_session_id=f"coordinator-{policy}",
            snapshot_id=snapshot.snapshot_id,
            mode="standard",
            ruleset_digest="rules",
            coverage_policy=policy,
        )
        runtime.store.create_work_unit(
            scan_id=scan_id,
            phase="baseline",
            role="baseline",
            paths=["."],
            status="failed",
        )

        assert service._coverage_summary(scan_id)["completeness"] == expected


@pytest.mark.asyncio
async def test_exhaustive_attestation_blocks_until_all_files_have_terminal_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\nsecond\nthird\n", encoding="utf-8")
    (target / "other.py").write_text("other = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
        coverage_policy="exhaustive",
    )
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
    )
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    assert (await audit_read(baseline, "app.py", start_line=3, end_line=3)).success

    gap_rejected = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert gap_rejected.success is False
    assert gap_rejected.metadata["error_code"] == "COVERAGE_OVERCLAIM"
    assert gap_rejected.metadata["violations"][0]["actual_state"] == "read_partial"

    assert (await audit_read(baseline, "app.py", start_line=2, end_line=2)).success

    blocked = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )

    assert blocked.success is True
    assert blocked.output["completeness"] == "blocked"
    assert blocked.output["counts"]["unexamined"] == 1
    assert runtime.store.work_unit_has_required_facts(unit_id, role="baseline") is True

    invalid_not_applicable = await audit_submit_coverage(
        baseline,
        dispositions=[
            {"path": "app.py", "claim": "analyzed"},
            {
                "path": "other.py",
                "claim": "not_applicable",
                "reason": "model-declared irrelevant source",
            },
        ],
    )
    assert invalid_not_applicable.success is False
    assert invalid_not_applicable.metadata["error_code"] == "COVERAGE_OVERCLAIM"
    assert invalid_not_applicable.metadata["violations"] == [
        {
            "path": "other.py",
            "claimed_state": "not_applicable",
            "actual_state": "inventoried",
            "required_receipt": "host_determined_not_applicable",
        }
    ]

    assert (await audit_read(baseline, "other.py", start_line=1, end_line=1)).success
    complete = await audit_submit_coverage(
        baseline,
        dispositions=[
            {"path": "app.py", "claim": "analyzed"},
            {"path": "other.py", "claim": "analyzed"},
        ],
    )
    assert complete.success is True
    assert complete.output["completeness"] == "complete"
    assert complete.output["counts"]["unexamined"] == 0
    assert complete.output["attestation_id"] == blocked.output["attestation_id"]
    with runtime.store._connect() as connection:
        attestation_count = connection.execute(
            "SELECT COUNT(*) FROM coverage_attestations "
            "WHERE work_unit_id = ? AND attempt_id = ?",
            (unit_id, complete.output["attempt_id"]),
        ).fetchone()[0]
    assert attestation_count == 1
    assert runtime.store.work_unit_has_required_facts(unit_id, role="baseline") is True


@pytest.mark.asyncio
async def test_fresh_attempt_cannot_attest_with_previous_attempt_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\nsecond\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
    )
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline-1",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    first = _agent_context("baseline-1", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(first)).success
    assert (await audit_inventory(first)).success
    assert (await audit_read(first, "app.py", start_line=1, end_line=2)).success
    first_binding = runtime.store.require_binding("baseline-1", {"baseline"})
    runtime.store.finish_work_attempt(
        first_binding.attempt_id,
        status="failed",
        failure_class="session_missing",
    )
    runtime.store.create_work_attempt(
        work_unit_id=unit_id,
        session_id="baseline-2",
        agent_name="code-security-baseline",
    )
    second = _agent_context("baseline-2", "message-3", "code-security-baseline")

    rejected = await audit_submit_coverage(
        second,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )

    assert rejected.success is False
    assert rejected.metadata["error_code"] == "COVERAGE_OVERCLAIM"
    assert rejected.metadata["violations"] == [
        {
            "path": "app.py",
            "claimed_state": "analyzed",
            "actual_state": "unexamined",
            "required_receipt": "read_complete",
        }
    ]


@pytest.mark.asyncio
async def test_failed_coverage_is_sealed_as_deferred_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    assert (await audit_prepare(coordinator, str(target))).success is False
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success

    invalid = await audit_submit_coverage(
        baseline,
        dispositions=[{"path": "does-not-exist.py", "claim": "analyzed"}],
    )
    assert invalid.success is False
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    failed = await audit_submit_coverage(
        baseline,
        dispositions=[
            {
                "path": "app.py",
                "claim": "failed",
                "reason": "worker_reported_failure",
            }
        ],
    )
    assert failed.success is True
    runtime.store.update_work_unit_status(unit_id, "completed")
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "completed"
    manifest = json.loads((Path(finalized.output["output_dir"]) / "scan-manifest.json").read_text(encoding="utf-8"))
    coverage = json.loads((Path(finalized.output["output_dir"]) / "coverage.json").read_text(encoding="utf-8"))
    assert manifest["scan"]["status"] == "completed"
    assert coverage["completeness"] == "partial"
    assert any(item.get("paths") == ["app.py"] for item in coverage["deferred"])
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success is False
    assert (await audit_finalize(coordinator, scan_id)).success is False
    assert (await audit_cancel(coordinator, scan_id)).success is False


@pytest.mark.asyncio
async def test_investigator_success_removes_active_baseline_gap_from_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\nsecond\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot_id)

    unit_ids: dict[str, str] = {}
    for role, phase, session_id, end_line, questions in (
        (
            "baseline",
            "baseline",
            "baseline",
            1,
            [
                {
                    "question": "Read the remaining handler flow.",
                    "category": "coverage_blocking",
                    "blocking": True,
                    "related_paths": ["app.py"],
                }
            ],
        ),
        ("investigator", "investigation", "investigator", 2, []),
    ):
        unit_id = runtime.store.create_work_unit(
            scan_id=scan_id,
            phase=phase,
            role=role,
            paths=["."] if role == "baseline" else ["app.py"],
        )
        unit_ids[role] = unit_id
        runtime.store.bind_session(
            session_id=session_id,
            scan_id=scan_id,
            snapshot_id=snapshot_id,
            role=role,
            work_unit_id=unit_id,
        )
        worker = _agent_context(
            session_id,
            f"{session_id}-message",
            "code-security-baseline" if role == "baseline" else "code-security-investigator",
        )
        assert (await audit_threat_model_context(worker)).success
        assert (await audit_inventory(worker)).success
        assert (await audit_read(worker, "app.py", start_line=1, end_line=end_line)).success
        dispositions = (
            [{"path": "app.py", "claim": "analyzed"}]
            if role == "investigator"
            else []
        )
        submitted = await audit_submit_coverage(
            worker,
            dispositions=dispositions,
            open_questions=questions,
        )
        assert submitted.success
        runtime.store.update_work_unit_status(unit_id, "completed")

    _submit_final_adjudication(runtime, scan_id)
    finalized = await audit_finalize(coordinator, scan_id)

    assert finalized.success
    coverage = json.loads(
        (Path(finalized.output["output_dir"]) / "coverage.json").read_text(encoding="utf-8")
    )
    assert coverage["completeness"] == "complete"
    assert coverage["files"]["analyzed"] == 1
    assert coverage["deferred"] == []
    assert "openQuestions" not in coverage
    receipts = [
        json.loads((Path(finalized.output["output_dir"]) / reference).read_text(encoding="utf-8"))
        for surface in coverage["surfaces"]
        for reference in surface["receiptRefs"]
    ]
    receipts_by_unit = {item["workUnitId"]: item for item in receipts}
    assert receipts_by_unit[unit_ids["baseline"]]["completeness"] == "partial"
    assert receipts_by_unit[unit_ids["baseline"]]["openQuestions"][0]["blocking"] is True
    assert receipts_by_unit[unit_ids["investigator"]]["completeness"] == "complete"


@pytest.mark.asyncio
async def test_exhaustive_finalize_returns_coverage_blocked_and_preserves_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(
        coordinator,
        str(target),
        coverage_policy="exhaustive",
    )
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot_id)
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    submitted = await audit_submit_coverage(
        baseline,
        open_questions=[
            {
                "question": "The assigned source remains unread.",
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": ["app.py"],
            }
        ],
    )
    assert submitted.success
    assert submitted.output["completeness"] == "blocked"
    runtime.store.update_work_unit_status(unit_id, "completed")
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)

    assert finalized.success
    assert finalized.output["status"] == "failed"
    assert finalized.output["failure_code"] == "coverage_blocked"
    assert finalized.output["coverage_completeness"] == "blocked"
    assert runtime.store.get_scan(scan_id)["failure_code"] == "coverage_blocked"
    assert runtime.store.report_data(scan_id)["coverage"][0]["attestation_id"] == submitted.output["attestation_id"]


@pytest.mark.asyncio
async def test_worker_tool_creates_at_most_one_exact_path_investigator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(runtime, scan_id=scan_id, snapshot_id=snapshot_id)
    baseline_batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="baseline",
        units=[
            {
                "role": "baseline",
                "paths": ["."],
                "subject_id": None,
                "assignment_digest": "a" * 64,
            }
        ],
    )
    baseline_unit = baseline_batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    submitted = await audit_submit_coverage(
        baseline,
        open_questions=[
            {
                "question": "Trace the exact source path.",
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": ["app.py"],
            }
        ],
    )
    assert submitted.success
    binding = runtime.store.require_binding("baseline", {"baseline"})
    runtime.store.finish_work_attempt(binding.attempt_id, status="completed")
    runtime.store.update_worker_batch_status(baseline_batch["batch_id"], "running")
    runtime.store.update_worker_batch_status(baseline_batch["batch_id"], "completed")
    launch = AsyncMock(return_value={})
    monkeypatch.setattr(tools_module, "_launch_worker", launch)

    investigation = await audit_run_workers(coordinator, scan_id, "investigation")

    assert investigation.success
    assert investigation.output["worker_count"] == 1
    assert investigation.output["workers"][0]["role"] == "investigator"
    assert investigation.output["workers"][0]["assigned_paths"] == ["app.py"]
    assert launch.await_args.kwargs["open_questions"][0]["question"] == "Trace the exact source path."
    second = await audit_run_workers(coordinator, scan_id, "investigation")
    assert second.success is False
    assert "already been created" in str(second.error)


@pytest.mark.asyncio
async def test_store_enforces_analysis_phase_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    baseline_plan = [
        {
            "role": "baseline",
            "paths": ["."],
            "subject_id": None,
            "assignment_digest": "a" * 64,
        }
    ]

    with pytest.raises(ValueError, match="threat model"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="baseline",
            units=baseline_plan,
        )

    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    baseline_batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="baseline",
        units=baseline_plan,
    )
    with pytest.raises(ValueError, match="baseline worker batch is already active"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="baseline",
            units=baseline_plan,
        )
    investigation_plan = [
        {
            "role": "investigator",
            "paths": ["app.py"],
            "subject_id": None,
        }
    ]
    with pytest.raises(ValueError, match="terminated baseline"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="investigation",
            units=investigation_plan,
        )
    with pytest.raises(ValueError, match="all analysis workers"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="verification",
            units=[
                {
                    "role": "verifier",
                    "paths": ["."],
                    "subject_id": "cand_missing",
                    "vote_index": 1,
                }
            ],
        )

    baseline_unit = baseline_batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="baseline-ordering",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context(
        "baseline-ordering",
        "baseline-ordering-message",
        "code-security-baseline",
    )
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    coverage = await audit_submit_coverage(
        baseline,
        open_questions=[
            {
                "question": "Trace the remaining source path.",
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": ["app.py"],
            }
        ],
    )
    assert coverage.success
    baseline_binding = runtime.store.require_binding(
        "baseline-ordering",
        {"baseline"},
    )
    runtime.store.finish_work_attempt(
        baseline_binding.attempt_id,
        status="completed",
    )

    investigation = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="investigation",
        units=investigation_plan,
    )
    with pytest.raises(ValueError, match="investigation worker batch is already active"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="investigation",
            units=investigation_plan,
        )
    with pytest.raises(ValueError, match="all analysis workers"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="verification",
            units=[
                {
                    "role": "verifier",
                    "paths": ["."],
                    "subject_id": "cand_missing",
                    "vote_index": 1,
                }
            ],
        )

    runtime.store.update_work_unit_status(
        investigation["units"][0]["work_unit_id"],
        "failed",
    )
    with pytest.raises(ValueError, match="does not belong to the scan"):
        runtime.store.create_worker_batch(
            scan_id=scan_id,
            phase="verification",
            units=[
                {
                    "role": "verifier",
                    "paths": ["."],
                    "subject_id": "cand_missing",
                    "vote_index": 1,
                }
            ],
        )


@pytest.mark.asyncio
async def test_parent_can_direct_only_one_targeted_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    scan = runtime.store.get_scan(scan_id)
    assert scan["mode"] == "standard"
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )

    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["app.py"],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[
                {
                    "path": "app.py",
                    "claim": "failed",
                    "reason": "analysis_context_missing",
                }
            ],
            open_questions=[
                {
                    "question": "Is the unresolved path attacker reachable?",
                    "category": "coverage_blocking",
                    "blocking": True,
                },
                {
                    "question": "Runtime configuration was not available.",
                    "category": "validation_limitation",
                    "blocking": False,
                },
            ],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    first_context = await audit_adjudication_context(coordinator, scan_id)
    assert first_context.success
    assert first_context.output["view"] == "overview"
    assert first_context.output["adjudication_round"] == 1
    assert first_context.output["candidate_count"] == 0
    assert first_context.output["coverage_gaps"]["failed_paths"] == ["app.py"]
    assert first_context.output["coverage_gaps"]["blocking_questions"][0][
        "question"
    ] == "Is the unresolved path attacker reachable?"
    assert first_context.output["validation_limitations"][0]["question"] == (
        "Runtime configuration was not available."
    )
    broad_rescan = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "targeted_rescan",
            "rescan": {
                "reason": "This request is intentionally too broad.",
                "paths": ["."],
                "questions": ["Can the whole repository be scanned again?"],
            },
        },
    )
    assert broad_rescan.success is False
    assert "narrower path" in str(broad_rescan.error)
    overclassified_rescan = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "targeted_rescan",
            "accepted_candidate_ids": [],
            "rejected_candidates": [],
            "rescan": {
                "reason": "This still needs more evidence.",
                "paths": ["app.py"],
                "questions": ["Is the unresolved path attacker reachable?"],
            },
        },
    )
    assert overclassified_rescan.success is False
    assert "only the rescan direction" in str(overclassified_rescan.error)
    first = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "targeted_rescan",
            "rescan": {
                "reason": "Confirm the remaining configuration hypothesis.",
                "paths": ["app.py"],
                "questions": ["Does this module load attacker-controlled code?"],
            },
        },
    )
    assert first.success

    batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="targeted_rescan",
        units=[{"role": "baseline", "paths": ["app.py"], "subject_id": None}],
    )
    runtime.store.update_worker_batch_status(batch["batch_id"], "running")
    rescan_unit = batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="targeted-rescan",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=rescan_unit,
    )
    rescan = _agent_context(
        "targeted-rescan",
        "message-3",
        "code-security-baseline",
    )
    rescan_context = await audit_threat_model_context(rescan)
    assert rescan_context.success
    assert rescan_context.output["targeted_rescan"]["paths"] == ["app.py"]
    assert (await audit_inventory(rescan)).success
    assert (await audit_read(rescan, "app.py", start_line=1, end_line=1)).success
    assert (
        await audit_submit_coverage(
            rescan,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(rescan_unit, "completed")
    runtime.store.update_worker_batch_status(batch["batch_id"], "completed")

    second_context = await audit_adjudication_context(coordinator, scan_id)
    assert second_context.success
    assert second_context.output["adjudication_round"] == 2
    second_rescan = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "targeted_rescan",
            "rescan": {
                "reason": "Try again.",
                "paths": ["app.py"],
                "questions": ["Can another pass find a vulnerability?"],
            },
        },
    )
    assert second_rescan.success is False
    assert "second adjudication must finalize" in str(second_rescan.error)
    final = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "finalize",
            "accepted_candidate_ids": [],
            "rejected_candidates": [],
        },
    )
    assert final.success
    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success
    assert finalized.output["finding_count"] == 0


@pytest.mark.asyncio
async def test_parent_rejection_removes_verifier_confirmed_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("result = eval(user)\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )

    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["app.py"],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=1)
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 1,
                }
            ]
        ),
    )
    assert candidate.success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verification_batch = _create_verification_batch(
        runtime,
        scan_id=scan_id,
        candidate_ids=[candidate.output["candidate_id"]],
    )
    verifier_unit = verification_batch["units"][0]["work_unit_id"]
    runtime.store.bind_session(
        session_id="verifier",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier = _agent_context("verifier", "message-3", "code-security-verifier")
    assert (await audit_verification_subject(verifier)).success
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=1)).success
    assert (
        await audit_submit_verdict(
            verifier,
            candidate.output["candidate_id"],
            "confirmed",
            "The direct data flow is present in this minimal fixture.",
        )
    ).success
    runtime.store.update_work_unit_status(verifier_unit, "completed")
    runtime.store.update_worker_batch_status(
        verification_batch["batch_id"],
        "completed",
    )

    with runtime.store._connect() as connection:
        connection.execute(
            "UPDATE evidence SET excerpt_hash = ? WHERE candidate_id = ?",
            ("0" * 64, candidate.output["candidate_id"]),
        )
    corrupted_context = await audit_adjudication_context(
        coordinator,
        scan_id,
        candidate_id=candidate.output["candidate_id"],
    )
    assert corrupted_context.success is False
    assert "excerpt hash mismatch" in str(corrupted_context.error)
    with runtime.store._connect() as connection:
        connection.execute(
            "UPDATE evidence SET excerpt_hash = ? WHERE candidate_id = ?",
            (source.output["excerpt_hash"], candidate.output["candidate_id"]),
        )
    context = await audit_adjudication_context(
        coordinator,
        scan_id,
        candidate_id=candidate.output["candidate_id"],
    )
    assert context.success
    assert context.output["view"] == "candidate"
    assert context.output["candidate"]["evidence"][0]["text"]
    incomplete = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "finalize",
            "accepted_candidate_ids": [],
            "rejected_candidates": [],
        },
    )
    assert incomplete.success is False
    assert "classified exactly once" in str(incomplete.error)
    rejected = await audit_submit_adjudication(
        coordinator,
        scan_id,
        {
            "action": "finalize",
            "accepted_candidate_ids": [],
            "rejected_candidates": [
                {
                    "candidate_id": candidate.output["candidate_id"],
                    "reason": "The fixture does not establish attacker reachability.",
                }
            ],
        },
    )
    assert rejected.success
    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success
    assert finalized.output["finding_count"] == 0
    findings = json.loads((Path(finalized.output["output_dir"]) / "findings.json").read_text(encoding="utf-8"))
    assert findings["findings"] == []


@pytest.mark.asyncio
async def test_unverified_candidate_blocks_completed_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("value = eval(user)\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=1)
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
            title="Pending eval candidate",
            confidence=0.8,
        ),
    )
    assert candidate.success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(unit_id, "completed")

    finalized = await audit_finalize(coordinator, scan_id)

    assert finalized.success is False
    assert "independent verification verdict" in str(finalized.error)
    assert runtime.store.scan_status(scan_id)["status"] == "running"
    assert (tmp_path / "outputs" / scan_id).exists() is False


@pytest.mark.asyncio
async def test_report_write_failure_does_not_publish_partial_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    output_root = tmp_path / "outputs"
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: output_root / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=unit_id,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(unit_id, "completed")
    original_write_bytes = reporting_module.ReportWriter._write_bytes

    def fail_while_writing_bundle(path: Path, contents: bytes) -> None:
        if path.name == "coverage.json":
            raise OSError("simulated report write failure")
        original_write_bytes(path, contents)

    monkeypatch.setattr(
        reporting_module.ReportWriter,
        "_write_bytes",
        staticmethod(fail_while_writing_bundle),
    )
    _submit_final_adjudication(runtime, scan_id)
    finalized = await audit_finalize(coordinator, scan_id)

    assert finalized.success is False
    assert runtime.store.get_scan(scan_id)["status"] == "failed"
    assert (output_root / scan_id).exists() is False
    assert list(output_root.glob(f".{scan_id}-*")) == []


@pytest.mark.asyncio
async def test_duplicate_candidates_merge_and_verdict_is_single_assignment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("def handler(user):\n    return eval(user)\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )
    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    candidate_payload = _candidate_payload(
        [
            {
                "relative_path": "app.py",
                "blob_digest": source.output["blob_digest"],
                "start_line": 1,
                "end_line": 2,
            }
        ],
        title="Unsafe ![remote](https://invalid.example/image)",
        confidence=0.9,
    )
    first = await audit_submit_candidate(baseline, candidate_payload)
    second = await audit_submit_candidate(baseline, candidate_payload)
    assert first.success and second.success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    candidate_ids = [
        first.output["candidate_id"],
        second.output["candidate_id"],
    ]
    verification_batch = _create_verification_batch(
        runtime,
        scan_id=scan_id,
        candidate_ids=candidate_ids,
    )
    for index, (unit, candidate_id) in enumerate(
        zip(verification_batch["units"], candidate_ids, strict=True),
        start=1,
    ):
        session_id = f"verifier-{index}"
        runtime.store.bind_session(
            session_id=session_id,
            scan_id=scan_id,
            snapshot_id=snapshot_id,
            role="verifier",
            work_unit_id=unit["work_unit_id"],
        )
        verifier = _agent_context(
            session_id,
            f"message-{index + 2}",
            "code-security-verifier",
        )
        assert (await audit_verification_subject(verifier)).success
        assert (
            await audit_read(verifier, "app.py", start_line=1, end_line=2)
        ).success
        assert (
            await audit_submit_verdict(
                verifier,
                candidate_id,
                "confirmed",
                "independently confirmed",
            )
        ).success
        if index == 1:
            duplicate_verdict = await audit_submit_verdict(
                verifier,
                candidate_id,
                "rejected",
                "conflicting retry",
            )
            assert duplicate_verdict.success is False
        runtime.store.update_work_unit_status(unit["work_unit_id"], "completed")
    runtime.store.update_worker_batch_status(
        verification_batch["batch_id"],
        "completed",
    )
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.output["status"] == "completed"
    output_path = Path(finalized.output["output_dir"])
    findings = json.loads((output_path / "findings.json").read_text(encoding="utf-8"))["findings"]
    assert len(findings) == 1
    assert len(findings[0]["extensions"]["candidateIds"]) == 2
    report = (output_path / "report.md").read_text(encoding="utf-8")
    assert "\\!\\[remote\\]\\(https://invalid.example/image\\)" in report


@pytest.mark.asyncio
async def test_three_independent_votes_produce_one_majority_verdict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text(
        "def handler(user):\n    return eval(user)\n",
        encoding="utf-8",
    )
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)

    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(
        coordinator,
        str(target),
        verification_votes=3,
    )
    scan_id = prepared.output["scan_id"]
    snapshot_id = prepared.output["snapshot"]["snapshot_id"]
    await _complete_threat_model(
        runtime,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
    )

    baseline_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="baseline",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="baseline",
        work_unit_id=baseline_unit,
    )
    baseline = _agent_context("baseline", "message-2", "code-security-baseline")
    assert (await audit_threat_model_context(baseline)).success
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                }
            ]
        ),
    )
    assert candidate.success
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    pending = runtime.store.list_unverified_candidates(scan_id)
    assert pending[0]["pending_vote_indices"] == [1, 2, 3]
    batch = runtime.store.create_worker_batch(
        scan_id=scan_id,
        phase="verification",
        units=plan_verification_units(pending),
    )
    runtime.store.update_worker_batch_status(batch["batch_id"], "running")

    submitted_verdicts = ["confirmed", "rejected", "confirmed"]
    subjects = []
    for index, (unit, submitted_verdict) in enumerate(
        zip(batch["units"], submitted_verdicts, strict=True),
        start=1,
    ):
        session_id = f"verifier-{index}"
        runtime.store.bind_session(
            session_id=session_id,
            scan_id=scan_id,
            snapshot_id=snapshot_id,
            role="verifier",
            work_unit_id=unit["work_unit_id"],
        )
        verifier = _agent_context(
            session_id,
            f"verifier-message-{index}",
            "code-security-verifier",
        )
        subject = await audit_verification_subject(verifier)
        assert subject.success
        assert set(subject.output) == {
            "candidate_id",
            "vote_index",
            "trust",
            "claim",
            "evidence",
            "threat_context",
        }
        assert subject.output["vote_index"] == index
        assert subject.output["trust"] == "untrusted_candidate_claim"
        subjects.append(subject.output["claim"])
        assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
        vote = await audit_submit_verdict(
            verifier,
            candidate.output["candidate_id"],
            submitted_verdict,
            f"Independent vote {index} after reading the source.",
        )
        assert vote.success
        assert vote.output["vote_index"] == index
        if index < 3:
            assert vote.output["consensus_verdict"] is None
            assert runtime.store.report_data(scan_id)["verifications"] == []
        runtime.store.update_work_unit_status(unit["work_unit_id"], "completed")

    runtime.store.update_worker_batch_status(batch["batch_id"], "completed")
    assert subjects[0] == subjects[1] == subjects[2]
    report_data = runtime.store.report_data(scan_id)
    assert len(report_data["verification_votes"]) == 3
    assert [item["verdict"] for item in report_data["verifications"]] == [
        "confirmed"
    ]


@pytest.mark.asyncio
async def test_background_worker_orchestration_retries_failed_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text(
        "def handler(user):\n    return eval(user)\n",
        encoding="utf-8",
    )
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    monkeypatch.setattr(
        reporting_module,
        "output_dir",
        lambda scan_id: tmp_path / "outputs" / scan_id,
    )
    parent = SimpleNamespace(
        id="coordinator",
        project_id="project",
        directory=str(tmp_path),
        provider="provider",
        model="model",
    )
    children: list[SimpleNamespace] = []

    async def create_child(**kwargs):
        child = SimpleNamespace(
            id=f"worker-{len(children) + 1}",
            agent=kwargs["agent"],
            creation_kwargs=kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", create_child)
    monkeypatch.setattr(Message, "create", AsyncMock())
    get_callable_tools = AsyncMock(
        return_value={
            "audit_knowledge_base",
            "audit_repository_summary",
            "audit_inventory",
            "audit_read",
            "audit_search",
            "audit_submit_threat_model",
        }
    )
    monkeypatch.setattr(tools_module, "get_session_callable_tools", get_callable_tools)
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)

    coordinator = _agent_context("coordinator", "message-1", "code-security")
    coordinator.extra["langfuse_trace_context"] = {
        "trace_id": "a" * 32,
        "parent_span_id": "b" * 16,
    }
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]

    blocked_baseline = await audit_run_workers(coordinator, scan_id, "baseline")
    assert blocked_baseline.success is False
    assert "threat model" in str(blocked_baseline.error).lower()

    threat_model_batch = await audit_run_workers(
        coordinator,
        scan_id,
        "threat_modeling",
    )
    assert threat_model_batch.success is True
    assert children[0].creation_kwargs["metadata"]["langfuse"]["trace_context"] == {
        "trace_id": "a" * 32,
        "parent_span_id": "b" * 16,
    }
    assert children[0].creation_kwargs["metadata"]["langfuse"]["root_trace_name"] == "code-security.scan"
    assert runtime.store.scan_status(scan_id)["threat_model_status"] == "running"
    threat_model_session = children[0].id
    modeler = _agent_context(
        threat_model_session,
        "threat-model-message",
        "code-security-threat-modeler",
    )
    modeler.extra.update(
        model={"providerID": "provider", "modelID": "model"},
        turn_callable_tool_names=sorted(get_callable_tools.return_value),
    )
    assert (await audit_repository_summary(modeler)).success
    inventory = await audit_inventory(modeler)
    assert inventory.success
    source = await audit_read(modeler, "app.py", start_line=1, end_line=2)
    assert (
        await audit_submit_threat_model(
            modeler,
            {
                "summary": "A callable Python handler processes caller input.",
                "assets": ["Process execution authority."],
                "trustBoundaries": ["Caller input enters handler at app.py:1."],
                "attackerCapabilities": ["A caller controls the handler argument."],
                "securityObjectives": ["Input must not become executable code."],
                "assumptions": ["Network exposure is not established."],
                "evidence": [
                    {
                        "relative_path": "app.py",
                        "blob_digest": source.output["blob_digest"],
                        "start_line": 1,
                        "end_line": 2,
                    }
                ],
            },
        )
    ).success
    manager.tasks["task-1"].status = "error"
    threat_model_wait = await audit_wait_workers(
        coordinator,
        threat_model_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert threat_model_wait.output["status"] == "completed"
    assert runtime.store.scan_status(scan_id)["threat_model_status"] == "completed"

    baseline_batch = await audit_run_workers(coordinator, scan_id, "baseline")
    assert baseline_batch.success is True
    assert baseline_batch.output["launched_workers"] == 1
    baseline_observability = children[1].creation_kwargs["metadata"]["langfuse"]
    assert baseline_observability["session_id"] == scan_id
    assert baseline_observability["metadata"]["phase"] == "baseline"
    assert baseline_observability["metadata"]["assigned_paths"] == ["."]
    assert len(baseline_observability["metadata"]["assignment_digest"]) == 64
    assert baseline_batch.output["workers"][0]["assigned_paths"] == ["."]
    assert (
        baseline_batch.output["workers"][0]["assignment_digest"]
        == baseline_observability["metadata"]["assignment_digest"]
    )
    persisted_baseline = runtime.store.get_worker_batch(
        baseline_batch.output["batch_id"]
    )
    assert len(persisted_baseline["units"][0]["assignment_digest"]) == 64
    assert manager.calls[1]["parent_session_id"] == "coordinator"
    running_wait = await audit_wait_workers(
        coordinator,
        baseline_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert running_wait.output["timed_out"] is True
    assert (await audit_finalize(coordinator, scan_id)).success is False
    baseline_session = children[1].id
    baseline = _agent_context(
        baseline_session,
        "baseline-message",
        "code-security-baseline",
    )
    baseline.extra.update(
        model={"providerID": "provider", "modelID": "model"},
        turn_callable_tool_names=sorted(get_callable_tools.return_value),
    )
    assert (await audit_threat_model_context(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    assert (await audit_inventory(baseline)).success
    candidate = await audit_submit_candidate(
        baseline,
        _candidate_payload(
            [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 2,
                }
            ],
        ),
    )
    assert candidate.success is True
    assert (
        await audit_submit_coverage(
            baseline,
            dispositions=[{"path": "app.py", "claim": "analyzed"}],
        )
    ).success
    manager.tasks["task-2"].status = "completed"
    baseline_wait = await audit_wait_workers(
        coordinator,
        baseline_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert baseline_wait.output["status"] == "completed"

    verification_batch = await audit_run_workers(
        coordinator,
        scan_id,
        "verification",
    )
    assert verification_batch.success is True
    verification_observability = children[2].creation_kwargs["metadata"]["langfuse"]
    assert verification_observability["metadata"]["candidate_id"] == candidate.output["candidate_id"]
    manager.tasks["task-3"].status = "error"
    manager.tasks["task-3"].error = "Session worker-3 not found"
    failed_verification = await audit_wait_workers(
        coordinator,
        verification_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert failed_verification.output["status"] == "running"
    assert failed_verification.output["workers"][0]["attempt_ordinal"] == 2
    attempts = runtime.store.list_work_attempts(
        failed_verification.output["workers"][0]["work_unit_id"]
    )
    assert [attempt["status"] for attempt in attempts] == ["failed", "running"]
    verifier = _agent_context(
        children[3].id,
        "verifier-message",
        "code-security-verifier",
    )
    verifier.extra.update(
        model={"providerID": "provider", "modelID": "model"},
        turn_callable_tool_names=sorted(get_callable_tools.return_value),
    )
    assert (await audit_verification_subject(verifier)).success
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    assert (
        await audit_submit_verdict(
            verifier,
            candidate.output["candidate_id"],
            "confirmed",
            "The input reaches eval without a guard.",
        )
    ).success
    manager.tasks["task-4"].status = "completed"
    verification_wait = await audit_wait_workers(
        coordinator,
        verification_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert verification_wait.output["status"] == "completed"
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "completed"
    assert finalized.output["finding_count"] == 1
    assert finalized.output["finding_summaries"][0]["severity"] == "high"
    assert finalized.output["finding_summaries"][0]["locations"][0]["path"] == "app.py"
    assert finalized.output["coverage_completeness"] == "complete"
    assert finalized.output["pending_count"] == 0


@pytest.mark.asyncio
async def test_cancel_stops_bound_background_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    parent = SimpleNamespace(
        id="coordinator",
        project_id="project",
        directory=str(tmp_path),
        provider="provider",
        model="model",
    )
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        Session,
        "create",
        AsyncMock(
            return_value=SimpleNamespace(
                id="worker",
                agent="code-security-threat-modeler",
            )
        ),
    )
    monkeypatch.setattr(Message, "create", AsyncMock())
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    launched = await audit_run_workers(coordinator, scan_id, "threat_modeling")
    assert launched.success

    cancelled = await audit_cancel(coordinator, scan_id)

    assert cancelled.success
    assert cancelled.output["cancelled_workers"] == 1
    assert manager.tasks["task-1"].status == "cancelled"
    batch = runtime.store.get_worker_batch(launched.output["batch_id"])
    assert batch is not None
    assert batch["status"] == "cancelled"
    assert batch["units"][0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_worker_launch_cancels_task_when_runtime_binding_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.session.message import Message
    from flocks.session.session import Session

    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(runtime_module, "_runtime", runtime)
    parent = SimpleNamespace(
        id="coordinator",
        project_id="project",
        directory=str(tmp_path),
        provider="provider",
        model="model",
    )
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(
        Session,
        "create",
        AsyncMock(return_value=SimpleNamespace(id="worker")),
    )
    monkeypatch.setattr(Message, "create", AsyncMock())
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))

    def fail_runtime_binding(*_args, **_kwargs):
        raise ValueError("runtime binding failed")

    monkeypatch.setattr(runtime.store, "set_work_attempt_runtime", fail_runtime_binding)
    launched = await audit_run_workers(
        coordinator,
        prepared.output["scan_id"],
        "threat_modeling",
    )

    assert launched.success is True
    assert launched.output["launch_failures"] == 1
    assert manager.tasks["task-1"].status == "cancelled"
    batch = runtime.store.get_worker_batch(launched.output["batch_id"])
    assert batch is not None
    assert batch["units"][0]["background_task_id"] is None
    assert batch["units"][0]["status"] == "failed"
