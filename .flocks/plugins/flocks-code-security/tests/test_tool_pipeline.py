from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks_code_security.reporting as reporting_module
import flocks_code_security.runtime as runtime_module
from flocks.tool.registry import ToolContext
from flocks_code_security.runtime import build_runtime
from flocks_code_security.tools import (
    audit_cancel,
    audit_finalize,
    audit_inventory,
    audit_prepare,
    audit_read,
    audit_run_workers,
    audit_submit_candidate,
    audit_submit_coverage,
    audit_submit_verdict,
    audit_wait_workers,
)
import flocks_code_security.tools as tools_module


def _agent_context(session_id: str, message_id: str, agent: str) -> ToolContext:
    return ToolContext(
        session_id=session_id,
        message_id=message_id,
        agent=agent,
        extra={"agent_execution_session": True},
    )


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
    unbacked_coverage = await audit_submit_coverage(
        baseline,
        inventoried_paths=["app.py"],
        analyzed_paths=["app.py"],
    )
    assert unbacked_coverage.success is False
    assert "not backed" in str(unbacked_coverage.error)
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    partial_read_coverage = await audit_submit_coverage(
        baseline,
        inventoried_paths=["app.py"],
        analyzed_paths=["app.py"],
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
        {
            "rule_id": "PY-EVAL-001",
            "title": "Untrusted input reaches eval",
            "severity": "high",
            "confidence": 0.95,
            "attack_path": "handler argument reaches eval without validation",
            "dangerous_operation": "eval(user)",
            "remediation": "Replace eval with a strict parser.",
            "evidence": [
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
                }
            ],
        },
    )
    assert candidate.success is True
    coverage = await audit_submit_coverage(
        baseline,
        inventoried_paths=["."],
        analyzed_paths=["."],
    )
    assert coverage.success is True
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verifier_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="verification",
        role="verifier",
        paths=["."],
    )
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
    assert "independently read" in str(unbacked_verdict.error)
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    assert (
        await audit_read(verifier, "aaa_helper.py", start_line=1, end_line=2)
    ).success
    verdict = await audit_submit_verdict(
        verifier,
        candidate.output["candidate_id"],
        "confirmed",
        "The public handler argument reaches eval directly.",
    )
    assert verdict.success is True
    runtime.store.update_work_unit_status(verifier_unit, "completed")

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "completed"
    output_path = Path(finalized.output["output_dir"])
    assert (output_path / "report.md").is_file()
    assert (output_path / "findings.json").read_text(encoding="utf-8").count("PY-EVAL-001") == 1
    assert (output_path / "report.sarif").is_file()
    findings = json.loads(
        (output_path / "findings.json").read_text(encoding="utf-8")
    )["findings"]
    assert findings[0]["primary_evidence"]["relative_path"] == "app.py"
    sarif = json.loads((output_path / "report.sarif").read_text(encoding="utf-8"))
    assert sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] == "app.py"
    assert output_path.stat().st_mode & 0o777 == 0o700
    assert (output_path / "report.md").stat().st_mode & 0o777 == 0o600
    manifest = json.loads(
        (output_path / "scan-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["status"] == "completed"
    assert (output_path / "threat-model.json").exists() is False
    assert "result_status" not in manifest
    assert (output_path / ".scan-manifest.final").exists() is False


@pytest.mark.asyncio
async def test_invalid_or_failed_coverage_never_completes_scan(
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

    invalid = await audit_submit_coverage(
        baseline,
        analyzed_paths=["does-not-exist.py"],
    )
    assert invalid.success is False
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    failed = await audit_submit_coverage(
        baseline,
        inventoried_paths=["app.py"],
        analyzed_paths=["app.py"],
        failed_paths=["app.py"],
    )
    assert failed.success is True
    runtime.store.update_work_unit_status(unit_id, "completed")

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "partial"
    manifest = json.loads(
        (Path(finalized.output["output_dir"]) / "scan-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["incomplete_details"]["failed_paths"] == ["app.py"]
    assert (await audit_submit_coverage(baseline, analyzed_paths=["app.py"])).success is False
    assert (await audit_finalize(coordinator, scan_id)).success is False
    assert (await audit_cancel(coordinator, scan_id)).success is False


@pytest.mark.asyncio
async def test_partial_report_preserves_pending_candidate_details(
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
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=1)
    candidate = await audit_submit_candidate(
        baseline,
        {
            "rule_id": "PY-EVAL-001",
            "title": "Pending eval candidate",
            "severity": "high",
            "confidence": 0.8,
            "attack_path": "user reaches eval",
            "dangerous_operation": "eval(user)",
            "remediation": "Use a strict parser.",
            "evidence": [
                {
                    "relative_path": "app.py",
                    "blob_digest": source.output["blob_digest"],
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        },
    )
    assert candidate.success
    assert (
        await audit_submit_coverage(
            baseline,
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    runtime.store.update_work_unit_status(unit_id, "completed")

    finalized = await audit_finalize(coordinator, scan_id)

    assert finalized.output["status"] == "partial"
    output_path = Path(finalized.output["output_dir"])
    manifest = json.loads((output_path / "scan-manifest.json").read_text())
    findings = json.loads((output_path / "findings.json").read_text())
    assert manifest["incomplete_details"]["pending_candidate_ids"] == [
        candidate.output["candidate_id"]
    ]
    assert findings["pending_candidates"][0]["payload"]["rule_id"] == "PY-EVAL-001"
    assert findings["pending_candidates"][0]["evidence"][0]["relative_path"] == "app.py"


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
    assert (await audit_inventory(baseline)).success
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success
    assert (
        await audit_submit_coverage(
            baseline,
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    runtime.store.update_work_unit_status(unit_id, "completed")
    original_write_json = reporting_module.ReportWriter._write_json

    def fail_after_manifest(path: Path, payload) -> None:
        if path.name == "coverage.json":
            raise OSError("simulated report write failure")
        original_write_json(path, payload)

    monkeypatch.setattr(
        reporting_module.ReportWriter,
        "_write_json",
        staticmethod(fail_after_manifest),
    )
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
    assert (await audit_inventory(baseline)).success
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    candidate_payload = {
        "rule_id": "PY-EVAL-001",
        "title": "Unsafe ![remote](https://invalid.example/image)",
        "severity": "high",
        "confidence": 0.9,
        "attack_path": "argument reaches eval",
        "dangerous_operation": "eval(user)",
        "remediation": "replace eval",
        "evidence": [
            {
                "relative_path": "app.py",
                "blob_digest": source.output["blob_digest"],
                "start_line": 1,
                "end_line": 2,
            }
        ],
    }
    first = await audit_submit_candidate(baseline, candidate_payload)
    second = await audit_submit_candidate(baseline, candidate_payload)
    assert first.success and second.success
    assert (
        await audit_submit_coverage(
            baseline,
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verifier_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="verification",
        role="verifier",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="verifier",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier = _agent_context("verifier", "message-3", "code-security-verifier")
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    for candidate_id in (first.output["candidate_id"], second.output["candidate_id"]):
        assert (
            await audit_submit_verdict(
                verifier,
                candidate_id,
                "confirmed",
                "independently confirmed",
            )
        ).success
    duplicate_verdict = await audit_submit_verdict(
        verifier,
        first.output["candidate_id"],
        "rejected",
        "conflicting retry",
    )
    assert duplicate_verdict.success is False
    runtime.store.update_work_unit_status(verifier_unit, "completed")

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.output["status"] == "completed"
    output_path = Path(finalized.output["output_dir"])
    findings = json.loads((output_path / "findings.json").read_text(encoding="utf-8"))[
        "findings"
    ]
    assert len(findings) == 1
    assert len(findings[0]["candidate_ids"]) == 2
    report = (output_path / "report.md").read_text(encoding="utf-8")
    assert "\\!\\[remote\\]\\(https://invalid.example/image\\)" in report


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
        )
        children.append(child)
        return child

    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", create_child)
    monkeypatch.setattr(Message, "create", AsyncMock())
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)

    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]

    baseline_batch = await audit_run_workers(coordinator, scan_id, "baseline")
    assert baseline_batch.success is True
    assert baseline_batch.output["launched_workers"] == 1
    assert manager.calls[0]["parent_session_id"] == "coordinator"
    running_wait = await audit_wait_workers(
        coordinator,
        baseline_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert running_wait.output["timed_out"] is True
    assert (await audit_finalize(coordinator, scan_id)).success is False
    baseline_session = children[0].id
    baseline = _agent_context(
        baseline_session,
        "baseline-message",
        "code-security-baseline",
    )
    source = await audit_read(baseline, "app.py", start_line=1, end_line=2)
    assert (await audit_inventory(baseline)).success
    candidate = await audit_submit_candidate(
        baseline,
        {
            "rule_id": "PY-EVAL-001",
            "title": "Untrusted input reaches eval",
            "severity": "high",
            "confidence": 0.95,
            "attack_path": "handler input reaches eval",
            "dangerous_operation": "eval(user)",
            "remediation": "Use a strict parser.",
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
    assert candidate.success is True
    assert (
        await audit_submit_coverage(
            baseline,
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    manager.tasks["task-1"].status = "completed"
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
    manager.tasks["task-2"].status = "error"
    failed_verification = await audit_wait_workers(
        coordinator,
        verification_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert failed_verification.output["status"] == "failed"

    verification_batch = await audit_run_workers(
        coordinator,
        scan_id,
        "verification",
    )
    assert verification_batch.success is True
    verifier = _agent_context(
        children[2].id,
        "verifier-message",
        "code-security-verifier",
    )
    assert (await audit_read(verifier, "app.py", start_line=1, end_line=2)).success
    assert (
        await audit_submit_verdict(
            verifier,
            candidate.output["candidate_id"],
            "confirmed",
            "The input reaches eval without a guard.",
        )
    ).success
    manager.tasks["task-3"].status = "completed"
    verification_wait = await audit_wait_workers(
        coordinator,
        verification_batch.output["batch_id"],
        timeout_seconds=0,
    )
    assert verification_wait.output["status"] == "completed"

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "partial"
    assert finalized.output["finding_count"] == 1
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
                agent="code-security-baseline",
            )
        ),
    )
    monkeypatch.setattr(Message, "create", AsyncMock())
    manager = _FakeBackgroundManager()
    monkeypatch.setattr(tools_module, "_background_manager", lambda: manager)
    coordinator = _agent_context("coordinator", "message-1", "code-security")
    prepared = await audit_prepare(coordinator, str(target))
    scan_id = prepared.output["scan_id"]
    launched = await audit_run_workers(coordinator, scan_id, "baseline")
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

    monkeypatch.setattr(runtime.store, "set_work_unit_runtime", fail_runtime_binding)
    launched = await audit_run_workers(
        coordinator,
        prepared.output["scan_id"],
        "baseline",
    )

    assert launched.success is True
    assert launched.output["launch_failures"] == 1
    assert manager.tasks["task-1"].status == "cancelled"
    batch = runtime.store.get_worker_batch(launched.output["batch_id"])
    assert batch is not None
    assert batch["units"][0]["background_task_id"] is None
    assert batch["units"][0]["status"] == "failed"
