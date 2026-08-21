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
from flocks_code_security.runtime import build_runtime
from flocks_code_security.tools import (
    audit_adjudication_context,
    audit_cancel,
    audit_finalize,
    audit_inventory,
    audit_probe_subject,
    audit_prepare,
    audit_read,
    audit_search,
    audit_run_workers,
    audit_submit_candidate,
    audit_submit_coverage,
    audit_submit_probe,
    audit_submit_adjudication,
    audit_submit_threat_model,
    audit_submit_verdict,
    audit_threat_model_context,
    audit_wait_workers,
)


def _agent_context(session_id: str, message_id: str, agent: str) -> ToolContext:
    return ToolContext(
        session_id=session_id,
        message_id=message_id,
        agent=agent,
        extra={"agent_execution_session": True},
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
    inventory = await audit_inventory(modeler)
    assert inventory.success
    source_file = next(
        item for item in inventory.output["files"] if not item["is_binary"]
    )
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
            "trustBoundaries": [
                f"Caller input crosses into application code at {source_file['path']}:1."
            ],
            "attackerCapabilities": [
                "A caller may control ordinary application input but not trusted configuration."
            ],
            "securityObjectives": [
                "Untrusted input must not gain process execution authority."
            ],
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


def _submit_final_adjudication(runtime, scan_id: str) -> dict:
    data = runtime.store.report_data(scan_id)
    verdicts = {
        item["candidate_id"]: item["verdict"]
        for item in data["verifications"]
    }
    accepted = [
        item["candidate_id"]
        for item in data["candidates"]
        if verdicts.get(item["candidate_id"]) == "confirmed"
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
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(adjudications)")
        }
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
        "Dynamic validation results: completed Docker probe pairs: 0; "
        "inconclusive attempts: 0; non-runnable probes: 8."
    )
    assert scope["validationMode"].endswith("(no target execution)")


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
    wrong_agent = await audit_submit_threat_model(
        _agent_context("modeler", "message-3", "code-security-baseline"),
        payload,
    )
    assert wrong_agent.success is False
    assert "Agent identity" in str(wrong_agent.error)

    missing_inventory = await audit_submit_threat_model(modeler, payload)
    assert missing_inventory.success is False
    assert "audit_inventory access" in str(missing_inventory.error)
    assert (await audit_inventory(modeler)).success

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
    assert (
        "relative_path, blob_digest, start_line, end_line"
        in str(wrong_evidence_shape.error)
    )

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
    assert (
        runtime.store.get_threat_model(scan_id)["threat_model"]["summary"]
        == payload["summary"]
    )

    runtime.store.update_work_unit_status(work_unit_id, "completed")
    late_update = await audit_submit_threat_model(modeler, payload)
    assert late_update.success is False
    assert "binding is not active" in str(late_update.error)

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
        inventoried_paths=["app.py"],
        analyzed_paths=["app.py"],
    )
    assert unconsumed_threat_model.success is False
    assert "threat-model context" in str(unconsumed_threat_model.error)
    assert (await audit_threat_model_context(baseline)).success
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
                }
            ]
        ),
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
    assert (
        (output_path / "findings.json")
        .read_text(encoding="utf-8")
        .count("code-injection.dynamic-eval")
        == 1
    )
    assert (output_path / "report.sarif").is_file()
    findings_document = json.loads(
        (output_path / "findings.json").read_text(encoding="utf-8")
    )
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
    assert sarif["runs"][0]["results"][0]["locations"][0]["physicalLocation"][
        "artifactLocation"
    ]["uri"] == "app.py"
    assert output_path.stat().st_mode & 0o777 == 0o700
    assert (output_path / "report.md").stat().st_mode & 0o777 == 0o600
    manifest = json.loads(
        (output_path / "scan-manifest.json").read_text(encoding="utf-8")
    )
    coverage_document = json.loads(
        (output_path / "coverage.json").read_text(encoding="utf-8")
    )
    validate_document("manifest", manifest)
    validate_document("findings", findings_document)
    validate_document("coverage", coverage_document)
    assert manifest["documentType"] == "codex-security.scan-manifest"
    assert manifest["scan"]["status"] == "completed"
    assert manifest["scan"]["sealedAt"] == manifest["scan"]["completedAt"]
    assert manifest["scan"]["threatModel"]["trustBoundaries"]
    assert any(
        artifact["path"] == "adjudication.json"
        for artifact in manifest["scan"]["artifacts"]
    )
    assert coverage_document["completeness"] == "complete"
    for artifact in manifest["scan"]["artifacts"]:
        contents = (output_path / artifact["path"]).read_bytes()
        assert hashlib.sha256(contents).hexdigest() == artifact["sha256"]
    threat_model = json.loads(
        (output_path / "threat-model.json").read_text(encoding="utf-8")
    )
    adjudication = json.loads(
        (output_path / "adjudication.json").read_text(encoding="utf-8")
    )
    assert threat_model["threatModel"]["trustBoundaries"]
    assert adjudication["adjudications"][-1]["action"] == "finalize"
    assert threat_model["evidence"][0]["relative_path"] in {
        "aaa_helper.py",
        "app.py",
    }
    assert "result_status" not in manifest["scan"]
    assert (output_path / ".scan-manifest.final").exists() is False


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
            inventoried_paths=["."],
            analyzed_paths=["."],
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
    manifest = json.loads(
        (output / "scan-manifest.json").read_text(encoding="utf-8")
    )
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
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
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
    assert (await audit_read(baseline, "app.py", start_line=1, end_line=1)).success

    invalid = await audit_submit_coverage(
        baseline,
        inventoried_paths=["."],
        analyzed_paths=["app.py"],
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
        inventoried_paths=["."],
        analyzed_paths=["app.py"],
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
    coverage = json.loads(
        (output_path / "coverage.json").read_text(encoding="utf-8")
    )
    assert coverage["completeness"] == "complete"
    assert coverage["deferred"] == []
    assert coverage["files"] == {
        "total": 2,
        "inventoried": 2,
        "analyzed": 1,
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
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.success is True
    assert finalized.output["status"] == "completed"
    manifest = json.loads(
        (Path(finalized.output["output_dir"]) / "scan-manifest.json").read_text(
            encoding="utf-8"
        )
    )
    coverage = json.loads(
        (Path(finalized.output["output_dir"]) / "coverage.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["scan"]["status"] == "completed"
    assert coverage["completeness"] == "partial"
    assert any(item.get("paths") == ["app.py"] for item in coverage["deferred"])
    assert (await audit_submit_coverage(baseline, analyzed_paths=["app.py"])).success is False
    assert (await audit_finalize(coordinator, scan_id)).success is False
    assert (await audit_cancel(coordinator, scan_id)).success is False


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
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    first_context = await audit_adjudication_context(coordinator, scan_id)
    assert first_context.success
    assert first_context.output["view"] == "overview"
    assert first_context.output["adjudication_round"] == 1
    assert first_context.output["candidate_count"] == 0
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
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
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
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
        )
    ).success
    runtime.store.update_work_unit_status(baseline_unit, "completed")

    verifier_unit = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="verification",
        role="verifier",
        paths=["app.py"],
    )
    runtime.store.bind_session(
        session_id="verifier",
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role="verifier",
        work_unit_id=verifier_unit,
    )
    verifier = _agent_context("verifier", "message-3", "code-security-verifier")
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
    findings = json.loads(
        (Path(finalized.output["output_dir"]) / "findings.json").read_text(
            encoding="utf-8"
        )
    )
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
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
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
    _submit_final_adjudication(runtime, scan_id)

    finalized = await audit_finalize(coordinator, scan_id)
    assert finalized.output["status"] == "completed"
    output_path = Path(finalized.output["output_dir"])
    findings = json.loads((output_path / "findings.json").read_text(encoding="utf-8"))[
        "findings"
    ]
    assert len(findings) == 1
    assert len(findings[0]["extensions"]["candidateIds"]) == 2
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
            creation_kwargs=kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=parent))
    monkeypatch.setattr(Session, "create", create_child)
    monkeypatch.setattr(Message, "create", AsyncMock())
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
    assert (
        children[0].creation_kwargs["metadata"]["langfuse"]["root_trace_name"]
        == "code-security.scan"
    )
    assert runtime.store.scan_status(scan_id)["threat_model_status"] == "running"
    threat_model_session = children[0].id
    modeler = _agent_context(
        threat_model_session,
        "threat-model-message",
        "code-security-threat-modeler",
    )
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
    assert baseline_observability["metadata"]["assigned_paths"] == ["app.py"]
    assert baseline_batch.output["workers"][0]["assigned_paths"] == ["app.py"]
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
            inventoried_paths=["app.py"],
            analyzed_paths=["app.py"],
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
    assert (
        verification_observability["metadata"]["candidate_id"]
        == candidate.output["candidate_id"]
    )
    manager.tasks["task-3"].status = "error"
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
        children[3].id,
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
    assert (
        finalized.output["finding_summaries"][0]["locations"][0]["path"]
        == "app.py"
    )
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

    monkeypatch.setattr(runtime.store, "set_work_unit_runtime", fail_runtime_binding)
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
