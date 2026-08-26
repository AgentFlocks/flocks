"""Deterministic Codex Security v1 reduction, sealing, and projections."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from flocks_code_security.artifact_integrity import find_output_directory, verify_artifact_bundle
from flocks_code_security.contract import (
    PRODUCER_NAME,
    PRODUCER_VERSION,
    SCHEMA_VERSION,
    artifact_record,
    canonical_json_bytes,
    finding_fingerprint,
    finding_identity,
    sha256_bytes,
    snapshot_digest,
    stable_id,
    validate_bundle,
)
from flocks_code_security.coverage import public_open_question
from flocks_code_security.orchestration import (
    baseline_focus_exclusion,
    baseline_focus_exclusions,
)
from flocks_code_security.paths import output_dir
from flocks_code_security.snapshot import DEFAULT_EXCLUDES, TargetSnapshotService
from flocks_code_security.store import ScanStore

SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "medium": 2,
    "low": 3,
    "informational": 4,
}
SARIF_LEVELS = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "informational": "note",
}
LEGACY_UNSEALED_REPORT_ARTIFACTS = frozenset(
    {"report.md", "report.sarif", "threat-model.json"}
)


class ReportWriter:
    def __init__(self, store: ScanStore):
        self.store = store

    def write(self, scan_id: str) -> dict[str, Any]:
        self.store.ensure_ready_to_finalize(scan_id)
        self.store.transition_scan_status(
            scan_id,
            from_statuses={"running"},
            to_status="reducing",
        )
        staging: Path | None = None
        target: Path | None = None
        published = False
        status_committed = False
        try:
            data = self.store.report_data(scan_id)
            scan = data["scan"]
            snapshot = self.store.get_snapshot(scan["snapshot_id"])
            if snapshot is None:
                raise ValueError("Scan snapshot not found")
            threat_model_record = data["threat_model"]
            if threat_model_record is None:
                raise ValueError("Scan threat model not found")

            evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
            for item in data["evidence"]:
                evidence_by_candidate.setdefault(item["candidate_id"], []).append(item)
            verdict_by_candidate = {item["candidate_id"]: item for item in data["verifications"]}
            adjudications = data["adjudications"]
            if not adjudications or adjudications[-1]["action"] != "finalize":
                raise ValueError("A final parent-agent adjudication is required")
            accepted_candidate_ids = set(adjudications[-1]["accepted_candidate_ids"])
            dynamic_runs = {item["candidate_id"]: item for item in data["dynamic_runs"]}
            dynamic_assessments = {
                item["candidate_id"]: item for item in (adjudications[-1]["dynamic_assessments"] or [])
            }

            confirmed_groups: dict[str, list[dict[str, Any]]] = {}
            outcomes: dict[str, str] = {}
            deferred_ids: list[str] = []
            for candidate in data["candidates"]:
                candidate_id = candidate["candidate_id"]
                evidence = evidence_by_candidate.get(candidate_id, [])
                verification = verdict_by_candidate.get(candidate_id)
                if not evidence or verification is None:
                    raise ValueError("Canonical reduction requires evidence and one verification per candidate")
                verdict = verification["verdict"]
                if candidate_id not in accepted_candidate_ids:
                    if verdict == "insufficient_evidence":
                        outcomes[candidate_id] = "deferred"
                        deferred_ids.append(candidate_id)
                    else:
                        outcomes[candidate_id] = "rejected"
                    continue
                if verdict == "rejected":
                    raise ValueError("Parent adjudication accepted a verifier-rejected candidate")
                if verdict == "insufficient_evidence":
                    raise ValueError("Parent adjudication accepted an insufficient-evidence candidate")
                if verdict != "confirmed":
                    raise ValueError(f"Unsupported persisted verification verdict: {verdict}")

                outcomes[candidate_id] = "reported"
                payload = candidate["payload"]
                fingerprint = finding_fingerprint(
                    snapshot.repository_identity,
                    payload["rule_id"],
                    payload["identity_anchor"],
                    payload.get("identity_instance", ""),
                )
                confirmed_groups.setdefault(fingerprint, []).append(
                    {
                        "candidate": candidate,
                        "evidence": evidence,
                        "verification": verification,
                        "dynamic_run": dynamic_runs.get(candidate_id),
                        "dynamic_assessment": dynamic_assessments.get(candidate_id),
                    }
                )

            findings = [
                self._merge_confirmed_group(
                    scan_id,
                    snapshot.snapshot_id,
                    snapshot.repository_identity,
                    group,
                )
                for _fingerprint, group in sorted(confirmed_groups.items())
            ]
            findings.sort(
                key=lambda item: (
                    SEVERITY_ORDER.get(item["severity"]["level"], 99),
                    item["fingerprints"]["primary"],
                )
            )
            findings_document = {
                "documentType": "codex-security.findings",
                "schemaVersion": SCHEMA_VERSION,
                "scanId": scan_id,
                "findings": findings,
            }

            coverage_document, receipts = self._coverage_document(
                data,
                snapshot,
                outcomes,
                deferred_ids,
            )
            completed_at = self._rfc3339_now()
            findings_bytes = canonical_json_bytes(findings_document)
            coverage_bytes = canonical_json_bytes(coverage_document)
            adjudication_document = {
                "scanId": scan_id,
                "adjudications": adjudications,
            }
            adjudication_bytes = canonical_json_bytes(adjudication_document)
            threat_model_bytes = canonical_json_bytes(
                {
                    "scanId": scan_id,
                    "snapshotId": snapshot.snapshot_id,
                    "workUnitId": threat_model_record["work_unit_id"],
                    "createdAt": threat_model_record["created_at"],
                    "threatModel": threat_model_record["threat_model"],
                    "evidence": threat_model_record["evidence"],
                }
            )
            supplemental_contents: dict[str, bytes] = {}
            if scan["dynamic_enabled"]:
                dynamic_document = {
                    "documentType": "flocks-code-security.dynamic-validation",
                    "schemaVersion": "1.0",
                    "scanId": scan_id,
                    "dynamicEnabled": True,
                    "candidates": [
                        {
                            "candidateId": run["candidate_id"],
                            "status": run["status"],
                            "probe": run["probe"],
                            "run": run["run"],
                            "assessment": dynamic_assessments.get(run["candidate_id"]),
                        }
                        for run in data["dynamic_runs"]
                    ],
                }
                supplemental_contents["dynamic-validation.json"] = canonical_json_bytes(dynamic_document)
                for candidate_id in sorted(accepted_candidate_ids):
                    assessment = dynamic_assessments.get(candidate_id)
                    verification = verdict_by_candidate.get(candidate_id)
                    run = dynamic_runs.get(candidate_id)
                    if (
                        assessment is None
                        or assessment["conclusion"] != "reproduced"
                        or verification is None
                        or verification["verdict"] != "confirmed"
                    ):
                        continue
                    if run is None or run["status"] != "completed":
                        raise ValueError("Reproduced assessment requires completed run facts")
                    probe = run["probe"]
                    probe_script = ("#!/bin/sh\n" + probe["attack"]["script"].rstrip() + "\n").encode("utf-8")
                    prefix = f"poc/{candidate_id}"
                    script_path = f"{prefix}/probe.sh"
                    supplemental_contents[script_path] = probe_script
                    supplemental_contents[f"{prefix}/poc.json"] = canonical_json_bytes(
                        {
                            "documentType": "flocks-code-security.poc",
                            "schemaVersion": "1.0",
                            "scanId": scan_id,
                            "candidateId": candidate_id,
                            "snapshotDigest": snapshot_digest(snapshot.tree_digest),
                            "contextPath": probe["context_path"],
                            "dockerfilePath": probe["dockerfile_path"],
                            "probeScriptRef": "probe.sh",
                            "probeScriptSha256": sha256_bytes(probe_script),
                            "timeoutSeconds": probe["attack"]["timeout_seconds"],
                            "expectedDifference": probe["expected_difference"],
                            "networkMode": "none",
                        }
                    )
            artifact_contents = {
                "findings.json": findings_bytes,
                "coverage.json": coverage_bytes,
                "adjudication.json": adjudication_bytes,
                "threat-model.json": threat_model_bytes,
                **receipts,
                **supplemental_contents,
            }
            artifacts = self._artifact_records(artifact_contents)
            manifest = self._manifest(
                scan,
                snapshot,
                threat_model_record["threat_model"],
                coverage_document,
                data["dynamic_runs"],
                completed_at,
                artifacts,
                knowledge_base=data["knowledge_base"],
            )
            artifact_contents.update(
                {
                    "report.md": self._markdown(
                        manifest,
                        findings_document,
                        coverage_document,
                    ).encode("utf-8"),
                    "report.sarif": canonical_json_bytes(self._sarif(manifest, findings_document)),
                }
            )
            manifest["scan"]["artifacts"] = self._artifact_records(artifact_contents)
            validate_bundle(
                manifest,
                findings_document,
                coverage_document,
                artifact_contents,
            )

            target = output_dir(scan_id)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.parent.chmod(0o700)
            if target.exists():
                raise ValueError("The audit output directory already exists")
            staging = Path(tempfile.mkdtemp(prefix=f".{scan_id}-", dir=target.parent))
            staging.chmod(0o700)
            for path, contents in sorted(artifact_contents.items()):
                self._write_bytes(staging / path, contents)
            self._write_json(staging / "scan-manifest.json", manifest)
            staging.replace(target)
            published = True
            self.store.set_scan_output_dir(scan_id, target)
            self.store.transition_scan_status(
                scan_id,
                from_statuses={"reducing"},
                to_status="completed",
            )
            status_committed = True
            return {
                "scan_id": scan_id,
                "status": "completed",
                "finding_count": len(findings),
                "finding_summaries": [
                    {
                        "finding_id": finding["findingId"],
                        "title": finding["title"],
                        "severity": finding["severity"]["level"],
                        "rule_id": finding["ruleId"],
                        "cwe": finding["taxonomy"]["cwe"],
                        "locations": finding["locations"],
                    }
                    for finding in findings
                ],
                "pending_count": 0,
                "deferred_count": len(coverage_document["deferred"]),
                "coverage_completeness": coverage_document["completeness"],
                "output_dir": str(target),
                "report_path": str(target / "report.md"),
                "sarif_path": str(target / "report.sarif"),
            }
        except Exception:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)
            if published and not status_committed and target is not None and target.exists():
                shutil.rmtree(target)
            if not status_committed:
                try:
                    self.store.transition_scan_status(
                        scan_id,
                        from_statuses={"reducing"},
                        to_status="failed",
                    )
                except ValueError:
                    pass
            raise

    def reseal_legacy_bundle(
        self,
        scan_id: str,
        output_directory: Path | None = None,
    ) -> bool:
        """Complete a legacy manifest only when its report bytes are reproducible."""
        output = output_directory or find_output_directory(scan_id)
        if output is None or output.is_symlink() or not output.is_dir():
            return False
        output = output.resolve()

        expected_error = (
            "Required sealed artifacts are missing: "
            + ", ".join(sorted(LEGACY_UNSEALED_REPORT_ARTIFACTS))
        )
        integrity = verify_artifact_bundle(scan_id, output)
        if integrity.status == "valid" or integrity.errors != (expected_error,):
            return False

        scan = self.store.get_scan(scan_id)
        if scan is None or scan["status"] != "completed":
            return False
        stored_output = scan.get("output_dir")
        if stored_output and Path(stored_output).expanduser().resolve() != output:
            return False

        manifest_path = output / "scan-manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            return False
        original_manifest = manifest_path.read_bytes()
        manifest = json.loads(original_manifest)

        def read_artifact(value: Any) -> tuple[str, bytes]:
            if not isinstance(value, str):
                raise ValueError("Sealed artifact path is invalid")
            relative = PurePosixPath(value)
            if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                raise ValueError(f"Sealed artifact path is invalid: {value}")
            candidate = output / Path(*relative.parts)
            if not candidate.is_file() or candidate.is_symlink():
                raise ValueError(f"Sealed artifact is missing: {value}")
            resolved = candidate.resolve()
            resolved.relative_to(output)
            return relative.as_posix(), resolved.read_bytes()

        artifact_contents = dict(
            read_artifact(record.get("path"))
            for record in manifest["scan"]["artifacts"]
        )
        if (output / "dynamic-validation.json").exists() and (
            "dynamic-validation.json" not in artifact_contents
        ):
            return False

        data = self.store.report_data(scan_id)
        threat_model = data["threat_model"]
        if threat_model is None:
            return False
        findings = json.loads(artifact_contents["findings.json"])
        coverage = json.loads(artifact_contents["coverage.json"])
        expected_contents = {
            "threat-model.json": canonical_json_bytes(
                {
                    "scanId": scan_id,
                    "snapshotId": data["scan"]["snapshot_id"],
                    "workUnitId": threat_model["work_unit_id"],
                    "createdAt": threat_model["created_at"],
                    "threatModel": threat_model["threat_model"],
                    "evidence": threat_model["evidence"],
                }
            ),
            "report.md": self._markdown(
                manifest,
                findings,
                coverage,
            ).encode("utf-8"),
            "report.sarif": canonical_json_bytes(
                self._sarif(
                    manifest,
                    findings,
                )
            ),
        }
        for path, expected in expected_contents.items():
            actual_path = output / path
            if (
                not actual_path.is_file()
                or actual_path.is_symlink()
                or actual_path.read_bytes() != expected
            ):
                return False
        artifact_contents.update(expected_contents)

        updated_manifest = copy.deepcopy(manifest)
        updated_manifest["scan"]["artifacts"] = self._artifact_records(artifact_contents)
        validate_bundle(updated_manifest, findings, coverage, artifact_contents)

        self._write_json(manifest_path, updated_manifest)
        verified = verify_artifact_bundle(scan_id, output)
        if verified.status != "valid":
            self._write_bytes(manifest_path, original_manifest)
            raise ValueError("Legacy artifact manifest could not be resealed safely")
        return True

    def _merge_confirmed_group(
        self,
        scan_id: str,
        snapshot_id: str,
        target_id: str,
        group: list[dict[str, Any]],
    ) -> dict[str, Any]:
        ranked = sorted(
            group,
            key=lambda item: (
                (item.get("dynamic_assessment") or {}).get("conclusion") != "reproduced",
                -len(item["evidence"]),
                -float(item["candidate"]["payload"]["confidence"]),
                item["candidate"]["candidate_id"],
            ),
        )
        selected = ranked[0]
        candidate = selected["candidate"]
        payload = candidate["payload"]
        identity = {"anchor": payload["identity_anchor"]}
        if payload.get("identity_instance"):
            identity["instance"] = payload["identity_instance"]
        finding_id, occurrence_id, fingerprints = finding_identity(
            scan_id,
            target_id,
            payload["rule_id"],
            identity["anchor"],
            identity.get("instance", ""),
        )

        evidence_by_key: dict[tuple[Any, ...], tuple[dict[str, Any], dict[str, str]]] = {}
        for item in ranked:
            metadata_rows = item["candidate"]["payload"].get("evidence_metadata", [])
            for evidence in item["evidence"]:
                key = (
                    evidence["relative_path"],
                    evidence["blob_digest"],
                    evidence["start_line"],
                    evidence["end_line"],
                )
                ordinal = int(evidence.get("ordinal", 0))
                metadata = (
                    metadata_rows[ordinal]
                    if ordinal < len(metadata_rows)
                    else {
                        "label": "Source evidence",
                        "role": "root_control" if ordinal == 0 else "propagation",
                        "explanation": payload["summary"],
                    }
                )
                evidence_by_key.setdefault(key, (evidence, metadata))

        code_evidence: list[dict[str, Any]] = []
        for key, (evidence, metadata) in evidence_by_key.items():
            evidence_id = (
                "evidence-" + hashlib.sha256("\0".join(str(part) for part in key).encode("utf-8")).hexdigest()[:16]
            )
            source_file = self.store.get_snapshot_file(
                snapshot_id,
                evidence["relative_path"],
            )
            if source_file is None:
                raise ValueError("Finding evidence is missing from the snapshot")
            code_evidence.append(
                {
                    "id": evidence_id,
                    "label": metadata["label"],
                    "path": evidence["relative_path"],
                    "startLine": evidence["start_line"],
                    "endLine": evidence["end_line"],
                    "language": source_file.language,
                    "role": metadata["role"],
                    "code": self._evidence_code(
                        snapshot_id,
                        evidence,
                    ),
                    "explanation": metadata["explanation"],
                }
            )
        evidence_refs = [item["id"] for item in code_evidence]
        locations = [
            {
                "path": item["path"],
                "startLine": item["startLine"],
                "endLine": item["endLine"],
                "role": item["role"],
            }
            for item in code_evidence
        ]
        verification = selected["verification"]
        attack_path = copy.deepcopy(payload["attack_path"])
        attack_path.setdefault("evidenceRefs", evidence_refs)
        attack_path["dataflow"].setdefault("evidenceRefs", evidence_refs)
        attack_path["dataflow"].setdefault("sink", payload["dangerous_operation"])
        severity_conflicts = sorted(
            {item["candidate"]["payload"]["severity"] for item in group},
            key=lambda value: SEVERITY_ORDER.get(value, 99),
        )
        extensions: dict[str, Any] = {
            "candidateId": candidate["candidate_id"],
        }
        reproduced_ids = sorted(
            item["candidate"]["candidate_id"]
            for item in group
            if (item.get("dynamic_assessment") or {}).get("conclusion") == "reproduced"
        )
        if reproduced_ids:
            extensions["pocRefs"] = [f"poc/{candidate_id}/probe.sh" for candidate_id in reproduced_ids]
        if len(group) > 1:
            extensions["candidateIds"] = sorted(item["candidate"]["candidate_id"] for item in group)
        if len(severity_conflicts) > 1:
            extensions["severityConflicts"] = severity_conflicts
        dynamic_assessment = selected.get("dynamic_assessment")
        if dynamic_assessment is None:
            validation = {
                "method": "independent static source review",
                "summary": verification["rationale"],
                "conclusion": "confirmed",
                "evidenceRefs": evidence_refs,
                "counterevidence": verification["counter_evidence"],
                "limitations": ["Validated by static source review; target code was not executed."],
            }
        else:
            validation = {
                "method": "independent-static-review+docker-probe",
                "staticConclusion": "confirmed",
                "dynamicConclusion": dynamic_assessment["conclusion"],
                "summary": dynamic_assessment["rationale"],
                "evidenceRefs": evidence_refs,
                "counterevidence": verification["counter_evidence"],
            }
            if dynamic_assessment["conclusion"] == "reproduced":
                validation["pocRef"] = f"poc/{candidate['candidate_id']}/probe.sh"
            else:
                validation["limitations"] = [
                    "Dynamic validation did not reproduce the claimed effect."
                    if dynamic_assessment["conclusion"] == "not_reproduced"
                    else "Dynamic validation was unavailable or inconclusive."
                ]
        return {
            "findingId": finding_id,
            "occurrenceId": occurrence_id,
            "ruleId": payload["rule_id"],
            "identity": identity,
            "fingerprints": fingerprints,
            "title": payload["title"],
            "summary": payload["summary"],
            "severity": {
                "level": payload["severity"],
                "rationale": payload["severity_rationale"],
            },
            "confidence": {
                "level": self._confidence_level(float(payload["confidence"])),
                "rationale": payload["confidence_rationale"],
            },
            "taxonomy": {
                "category": payload["category"],
                "cwe": payload["cwe"],
            },
            "locations": locations,
            "codeEvidence": code_evidence,
            "rootCause": {
                "summary": payload["root_cause"],
                "evidenceRefs": evidence_refs,
            },
            "remediation": payload["remediation"],
            "validation": validation,
            "attackPath": attack_path,
            "remediationTests": payload.get("remediation_tests", []),
            "preventiveControls": payload.get("preventive_controls", []),
            "provenance": {"source": "local_plugin"},
            "extensions": extensions,
        }

    def _coverage_document(
        self,
        data: dict[str, Any],
        snapshot,
        outcomes: dict[str, str],
        deferred_candidate_ids: list[str],
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        scan_id = data["scan"]["scan_id"]
        coverage_by_unit = {item["work_unit_id"]: item for item in data["coverage"]}
        all_snapshot_files = self.store.list_snapshot_files(snapshot.snapshot_id)
        all_scope_paths = [
            item.relative_path for item in all_snapshot_files
        ] + [item["relative_path"] for item in data["omissions"]]
        focus_exclusions = baseline_focus_exclusions(
            all_scope_paths,
            include_paths=snapshot.include_paths,
        )
        snapshot_files = [
            item
            for item in all_snapshot_files
            if baseline_focus_exclusion(
                item.relative_path,
                include_paths=snapshot.include_paths,
            )
            is None
        ]
        snapshot_paths = {item.relative_path for item in snapshot_files}

        def paths_in_states(
            coverage: dict[str, Any],
            states: set[str],
        ) -> list[str]:
            return [
                item["relative_path"]
                for item in coverage["records"]
                if item["state"] in states
            ]

        candidates_by_unit: dict[str, list[dict[str, Any]]] = {}
        candidates_by_id: dict[str, dict[str, Any]] = {}
        for candidate in data["candidates"]:
            candidates_by_id[candidate["candidate_id"]] = candidate
            if candidate.get("work_unit_id"):
                candidates_by_unit.setdefault(candidate["work_unit_id"], []).append(candidate)

        surfaces: list[dict[str, Any]] = []
        receipts: dict[str, bytes] = {}
        deferred: list[dict[str, Any]] = []
        open_questions: list[dict[str, Any]] = []
        limitations: list[dict[str, Any]] = []
        surface_by_unit: dict[str, str] = {}
        analysis_units = [item for item in data["work_units"] if item["role"] in {"baseline", "investigator"}]
        for unit in analysis_units:
            paths = unit["paths"]
            surface_id = (
                "surface_" + hashlib.sha256("\0".join((unit["work_unit_id"], *paths)).encode("utf-8")).hexdigest()[:16]
            )
            surface_by_unit[unit["work_unit_id"]] = surface_id
            coverage = coverage_by_unit.get(unit["work_unit_id"])
            candidates = candidates_by_unit.get(unit["work_unit_id"], [])
            unit_outcomes = [outcomes[item["candidate_id"]] for item in candidates]
            needs_follow_up = unit["status"] != "completed" or coverage is None
            if coverage is not None:
                blocking_questions = [question for question in coverage["open_questions"] if question["blocking"]]
                failed_paths = paths_in_states(coverage, {"failed"})
                needs_follow_up = needs_follow_up or bool(
                    failed_paths
                    or blocking_questions
                    or coverage.get("completeness") in {"partial", "blocked"}
                )
            if "reported" in unit_outcomes:
                disposition = "reported"
            elif needs_follow_up or "deferred" in unit_outcomes:
                disposition = "needs_follow_up"
            elif "rejected" in unit_outcomes:
                disposition = "rejected"
            else:
                disposition = "no_issue_found"

            unit_not_applicable = []
            if coverage is not None:
                unit_not_applicable = paths_in_states(
                    coverage,
                    {"not_applicable"},
                )
            inventory = (
                []
                if coverage is None
                else [item["relative_path"] for item in coverage["records"]]
            )
            analyzed = (
                []
                if coverage is None
                else paths_in_states(coverage, {"read_complete"})
            )
            failed = (
                []
                if coverage is None
                else paths_in_states(coverage, {"failed"})
            )
            unexamined = (
                []
                if coverage is None
                else paths_in_states(
                    coverage,
                    {"unexamined", "inventoried", "located", "read_partial"},
                )
            )
            receipt_path = f"artifacts/03_coverage/{surface_id}.json"
            receipt = {
                "documentType": "flocks-code-security.coverage-receipt",
                "schemaVersion": "1.0",
                "scanId": scan_id,
                "surfaceId": surface_id,
                "workUnitId": unit["work_unit_id"],
                "workUnitStatus": unit["status"],
                "assignedPaths": paths,
                "attestationId": None if coverage is None else coverage.get("attestation_id"),
                "attemptId": None if coverage is None else coverage.get("attempt_id"),
                "policy": None if coverage is None else coverage.get("policy"),
                "completeness": None if coverage is None else coverage.get("completeness"),
                "counts": None if coverage is None else coverage.get("counts"),
                "inventory": inventory,
                "analyzed": analyzed,
                "notApplicable": unit_not_applicable,
                "failed": failed,
                "unexamined": unexamined,
                "openQuestions": (
                    []
                    if coverage is None
                    else [public_open_question(question) for question in coverage["open_questions"]]
                ),
                "candidateOutcomes": [
                    {
                        "candidateId": item["candidate_id"],
                        "disposition": outcomes[item["candidate_id"]],
                    }
                    for item in candidates
                ],
            }
            receipts[receipt_path] = canonical_json_bytes(receipt)
            surfaces.append(
                {
                    "id": surface_id,
                    "label": ("Repository source" if paths == ["."] else "Source scope: " + ", ".join(paths)),
                    "disposition": disposition,
                    "receiptRefs": [receipt_path],
                    "riskArea": "Static source security review",
                    "notes": f"{len(candidates)} candidate(s) recorded",
                }
            )
            if unit["status"] != "completed":
                deferred.append(
                    {
                        "id": stable_id("deferred", unit["work_unit_id"]),
                        "reason": f"Work unit ended with status {unit['status']}",
                        "paths": paths,
                        "surfaceIds": [surface_id],
                    }
                )
            if coverage is None:
                deferred.append(
                    {
                        "id": stable_id("deferred", unit["work_unit_id"], "coverage"),
                        "reason": "Coverage receipt was not submitted",
                        "paths": paths,
                        "surfaceIds": [surface_id],
                    }
                )
                continue
            for failed_path in failed:
                deferred.append(
                    {
                        "id": stable_id("deferred", unit["work_unit_id"], "failed", failed_path),
                        "reason": "Source path could not be fully analyzed",
                        "paths": [failed_path],
                        "surfaceIds": [surface_id],
                    }
                )
            if unexamined:
                deferred.append(
                    {
                        "id": stable_id(
                            "deferred",
                            unit["work_unit_id"],
                            "unexamined",
                            *unexamined,
                        ),
                        "reason": "Assigned source remains unexamined in the host coverage attestation",
                        "paths": unexamined,
                        "surfaceIds": [surface_id],
                    }
                )
            for question in coverage["open_questions"]:
                public_question = public_open_question(question)
                open_questions.append(public_question)
                if question["blocking"]:
                    row: dict[str, Any] = {
                        "id": stable_id(
                            "deferred",
                            unit["work_unit_id"],
                            "question",
                            question["question"],
                        ),
                        "reason": question["question"],
                        "surfaceIds": [surface_id],
                    }
                    if question["related_paths"]:
                        row["paths"] = question["related_paths"]
                    deferred.append(row)
                else:
                    limitations.append(public_question)

        for candidate_id in deferred_candidate_ids:
            candidate = candidates_by_id[candidate_id]
            surface_id = surface_by_unit.get(candidate.get("work_unit_id", ""))
            paths = sorted({item["relative_path"] for item in data["evidence"] if item["candidate_id"] == candidate_id})
            row: dict[str, Any] = {
                "id": stable_id("deferred", candidate_id),
                "reason": "Independent verification found insufficient evidence",
            }
            if paths:
                row["paths"] = paths
            if surface_id:
                row["surfaceIds"] = [surface_id]
            deferred.append(row)

        for omission in data["omissions"]:
            if baseline_focus_exclusion(
                omission["relative_path"],
                include_paths=snapshot.include_paths,
            ) is not None:
                continue
            deferred.append(
                {
                    "id": stable_id("deferred", "omission", omission["relative_path"]),
                    "reason": f"Snapshot omission: {omission['reason']}",
                    "paths": [omission["relative_path"]],
                }
            )

        inventoried_files = {
            path
            for coverage in coverage_by_unit.values()
            for path in (item["relative_path"] for item in coverage["records"])
            if path in snapshot_paths
        }
        analyzed_files = {
            path
            for coverage in coverage_by_unit.values()
            for path in paths_in_states(coverage, {"read_complete"})
            if path in snapshot_paths
        }
        failed_files = {
            path
            for coverage in coverage_by_unit.values()
            for path in paths_in_states(coverage, {"failed"})
            if path in snapshot_paths
        }
        not_applicable_paths = {
            path
            for coverage in coverage_by_unit.values()
            for path in paths_in_states(coverage, {"not_applicable"})
            if path in snapshot_paths
        }
        uncovered = sorted(
            path
            for path in snapshot_paths
            if path not in analyzed_files and path not in not_applicable_paths and path not in failed_files
        )
        if uncovered:
            deferred.append(
                {
                    "id": stable_id("deferred", "uncovered", *uncovered),
                    "reason": "Snapshot paths were not covered by a completed analysis receipt",
                    "paths": uncovered,
                }
            )
        if not analysis_units:
            deferred.append(
                {
                    "id": stable_id("deferred", "no-analysis-units"),
                    "reason": "No baseline analysis work units were recorded",
                }
            )

        deferred_by_id = {item["id"]: item for item in deferred}
        deferred = [deferred_by_id[key] for key in sorted(deferred_by_id)]

        def question_key(item: dict[str, Any]) -> tuple[Any, ...]:
            return (
                item["category"],
                item["question"],
                tuple(item.get("relatedPaths", [])),
                item.get("followUpPrompt", ""),
            )

        open_questions = sorted(
            {question_key(item): item for item in open_questions}.values(),
            key=question_key,
        )
        limitations = sorted(
            {question_key(item): item for item in limitations}.values(),
            key=question_key,
        )

        def exclusion_reason(pattern: str) -> str:
            if pattern in DEFAULT_EXCLUDES:
                return "Excluded by the deterministic snapshot safety policy"
            return "Excluded by the requested audit scope"

        explicit_exclusions = [
            {"pattern": pattern, "reason": exclusion_reason(pattern)}
            for pattern in dict.fromkeys(snapshot.exclude_patterns)
        ]
        explicit_exclusions.extend(
            {"pattern": pattern, "reason": reason}
            for pattern, reason in focus_exclusions.items()
        )
        if snapshot.target_kind.startswith("git_"):
            explicit_exclusions.append(
                {
                    "pattern": "<repository-git-ignore-rules>",
                    "reason": "Excluded by repository Git ignore rules during inventory",
                }
            )
        include_paths = list(snapshot.include_paths)
        exclude_paths = list(
            dict.fromkeys((*snapshot.exclude_patterns, *focus_exclusions))
        )
        scoped = include_paths != ["."]
        completeness = (
            "complete"
            if analysis_units
            and len(coverage_by_unit) == len(analysis_units)
            and all(
                coverage["completeness"] == "complete"
                for coverage in coverage_by_unit.values()
            )
            else "partial"
        )
        total_files = len(snapshot_files)
        effectively_covered = len(analyzed_files | not_applicable_paths)
        document: dict[str, Any] = {
            "documentType": "codex-security.coverage",
            "schemaVersion": SCHEMA_VERSION,
            "scanId": scan_id,
            "mode": "scoped_path" if scoped else "repository",
            "completeness": completeness,
            "inventoryStrategy": (
                "scoped_path" if scoped else "repository" if snapshot.target_kind.startswith("git_") else "directory"
            ),
            "includePaths": include_paths,
            "excludePaths": exclude_paths,
            "surfaces": sorted(surfaces, key=lambda item: item["id"]),
            "explicitExclusions": explicit_exclusions,
            "deferred": deferred,
            "files": {
                "total": total_files,
                "inventoried": len(inventoried_files),
                "analyzed": len(analyzed_files),
                "notApplicable": len(not_applicable_paths),
                "failed": len(failed_files),
                "effectiveCoveragePercent": (
                    100 if total_files == 0 else round(effectively_covered * 100 / total_files, 2)
                ),
            },
        }
        if open_questions:
            document["openQuestions"] = open_questions
        if limitations:
            document["limitations"] = limitations
        return document, receipts

    @staticmethod
    def _manifest(
        scan: dict[str, Any],
        snapshot,
        threat_model: dict[str, Any],
        coverage: dict[str, Any],
        dynamic_runs: list[dict[str, Any]],
        completed_at: str,
        artifacts: list[dict[str, str]],
        knowledge_base: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        target: dict[str, Any] = {
            "kind": snapshot.target_kind,
            "targetId": snapshot.repository_identity,
            "displayName": snapshot.display_name,
        }
        if snapshot.source_revision:
            target["revision"] = snapshot.source_revision
        if snapshot.target_kind != "git_revision" or not snapshot.copy_source:
            target["snapshotDigest"] = snapshot_digest(snapshot.tree_digest)
        limitations = list(
            dict.fromkeys(
                [item["question"] for item in coverage.get("limitations", [])]
                + [item["reason"] for item in coverage["deferred"]]
            )
        )[:100]
        if scan["dynamic_enabled"]:
            status_counts = Counter(run["status"] for run in dynamic_runs)
            if dynamic_runs:
                runtime_status = (
                    "Dynamic validation results: completed Docker probe pairs: "
                    f"{status_counts['completed']}; inconclusive attempts: "
                    f"{status_counts['inconclusive']}; non-runnable probes: "
                    f"{status_counts['not_runnable']}."
                )
            else:
                runtime_status = "Dynamic validation was enabled; no statically confirmed candidate required a probe."
            if status_counts["completed"]:
                validation_mode = "Independent static source verification plus completed Docker probes"
            elif status_counts["inconclusive"]:
                validation_mode = "Independent static source verification plus attempted Docker validation"
            else:
                validation_mode = (
                    "Independent static source verification plus dynamic probe planning (no target execution)"
                )
        else:
            runtime_status = "Target code was not executed."
            validation_mode = "Independent static source verification"
        scope: dict[str, Any] = {
            "includePaths": coverage["includePaths"],
            "excludePaths": coverage["excludePaths"],
            "summary": (
                f"Static review of {snapshot.file_count} "
                f"{'immutable snapshot' if snapshot.copy_source else 'digest-bound source'} files."
            ),
            "runtimeStatus": runtime_status,
            "validationMode": validation_mode,
            "context": "Threat-model-guided standard source-code security audit.",
        }
        if limitations:
            scope["limitations"] = limitations
        manifest_scan: dict[str, Any] = {
            "id": scan["scan_id"],
            "producer": {
                "name": PRODUCER_NAME,
                "version": PRODUCER_VERSION,
            },
            "status": "completed",
            "startedAt": scan["created_at"],
            "completedAt": completed_at,
            "sealedAt": completed_at,
            "target": target,
            "scope": scope,
            "threatModel": threat_model,
            "coverageRef": "coverage.json",
            "findingsRef": "findings.json",
            "artifacts": artifacts,
        }
        if knowledge_base is not None:
            scope["context"] = "Knowledge-base-guided standard source-code security audit."
            manifest_scan["knowledgeBase"] = {
                "displayName": knowledge_base["display_name"],
                "sha256": knowledge_base["sha256"],
                "byteLength": knowledge_base["byte_length"],
                "trust": knowledge_base["trust"],
            }
        return {
            "documentType": "codex-security.scan-manifest",
            "schemaVersion": SCHEMA_VERSION,
            "scan": manifest_scan,
        }

    def _evidence_code(
        self,
        snapshot_id: str,
        evidence: dict[str, Any],
    ) -> str:
        snapshot = self.store.get_snapshot(snapshot_id)
        record = self.store.get_snapshot_file(snapshot_id, evidence["relative_path"])
        if snapshot is None or record is None or record.is_binary:
            raise ValueError("Code evidence is not readable source text")
        root_descriptor = TargetSnapshotService._open_directory(Path(snapshot.root_path))
        descriptor: int | None = None
        try:
            descriptor = TargetSnapshotService._open_snapshot_file(
                root_descriptor,
                evidence["relative_path"],
            )
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != record.size_bytes:
                raise ValueError("Snapshot evidence file changed")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            data = b"".join(chunks)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(root_descriptor)
        if hashlib.sha256(data).hexdigest() != evidence["blob_digest"]:
            raise ValueError("Snapshot evidence digest changed")
        lines = data.decode("utf-8", errors="replace").splitlines()
        code = "\n".join(lines[evidence["start_line"] - 1 : evidence["end_line"]])
        if hashlib.sha256(code.encode("utf-8")).hexdigest() != evidence["excerpt_hash"]:
            raise ValueError("Snapshot evidence excerpt changed")
        return code

    @staticmethod
    def _confidence_level(value: float) -> str:
        if value >= 0.85:
            return "high"
        if value >= 0.60:
            return "medium"
        return "low"

    @staticmethod
    def _rfc3339_now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _artifact_records(contents: dict[str, bytes]) -> list[dict[str, str]]:
        records = []
        for path, value in sorted(contents.items()):
            media_type = "application/json"
            if path.endswith(".sh"):
                media_type = "text/x-shellscript"
            elif path.endswith(".md"):
                media_type = "text/markdown"
            elif path.endswith(".sarif"):
                media_type = "application/sarif+json"
            records.append(artifact_record(path, value, media_type))
        return records

    @staticmethod
    def _write_bytes(path: Path, contents: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.parent.chmod(0o700)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(contents)
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        ReportWriter._write_bytes(path, canonical_json_bytes(payload))

    @staticmethod
    def _markdown_text(value: Any) -> str:
        text = str(value)
        for character in "\\`*_{}[]()<>#!|":
            text = text.replace(character, f"\\{character}")
        return text

    @staticmethod
    def _code_fence(code: str) -> str:
        longest = max(
            (len(match.group(0)) for match in re.finditer(r"`+", code)),
            default=0,
        )
        return "`" * max(3, longest + 1)

    @staticmethod
    def _markdown(
        manifest: dict[str, Any],
        findings_document: dict[str, Any],
        coverage: dict[str, Any],
    ) -> str:
        scan = manifest["scan"]
        target = scan["target"]
        findings = findings_document["findings"]
        files = coverage.get("files", {})
        limitations = coverage.get("limitations", [])
        lines = [
            "# Code Security Audit Report",
            "",
            f"- Scan: `{ReportWriter._markdown_text(scan['id'])}`",
            "- Status: `completed` (sealed)",
            f"- Target: `{ReportWriter._markdown_text(target['displayName'])}`",
            f"- Target kind: `{target['kind']}`",
            f"- Revision: `{ReportWriter._markdown_text(target.get('revision', 'not recorded'))}`",
            f"- Findings: **{len(findings)}**",
            f"- Coverage completeness: `{coverage['completeness']}`",
            (
                "- Effective source coverage: "
                f"**{files.get('effectiveCoveragePercent', 0):g}%** "
                f"({files.get('analyzed', 0)} analyzed, "
                f"{files.get('notApplicable', 0)} not applicable, "
                f"{files.get('failed', 0)} failed)"
            ),
            (
                "- Unexamined source files: **"
                f"{max(0, files.get('total', 0) - files.get('analyzed', 0) - files.get('notApplicable', 0) - files.get('failed', 0))}**"
            ),
            f"- Deferred work: **{len(coverage['deferred'])}**",
            f"- Static validation limitations: **{len(limitations)}**",
        ]
        knowledge_base = scan.get("knowledgeBase")
        if knowledge_base is not None:
            lines.extend(
                [
                    "- Audit mode: `knowledge-guided`",
                    f"- Knowledge base: `{ReportWriter._markdown_text(knowledge_base['displayName'])}`",
                    f"- Knowledge base SHA-256: `{knowledge_base['sha256']}`",
                    "- Knowledge base trust: `untrusted external hypothesis; not evidence`",
                ]
            )
        lines.extend(
            [
                "",
                "Canonical artifacts: `scan-manifest.json`, `findings.json`, and `coverage.json`. This report is a deterministic projection of those sealed files.",
                "",
                "## Scope",
                "",
                ReportWriter._markdown_text(scan["scope"]["summary"]),
                "",
                f"- Included: {', '.join(f'`{ReportWriter._markdown_text(path)}`' for path in coverage['includePaths']) or 'None'}",
                f"- Excluded patterns: **{len(coverage['excludePaths'])}**",
                f"- Runtime: {ReportWriter._markdown_text(scan['scope']['runtimeStatus'])}",
                f"- Validation: {ReportWriter._markdown_text(scan['scope']['validationMode'])}",
                "",
                "## Threat Model",
                "",
                ReportWriter._markdown_text(scan["threatModel"]["summary"]),
                "",
            ]
        )
        for heading, field in (
            ("Assets", "assets"),
            ("Trust Boundaries", "trustBoundaries"),
            ("Attacker Capabilities", "attackerCapabilities"),
            ("Security Objectives", "securityObjectives"),
            ("Assumptions", "assumptions"),
        ):
            lines.extend([f"### {heading}", ""])
            values = scan["threatModel"].get(field, [])
            lines.extend([f"- {ReportWriter._markdown_text(value)}" for value in values] or ["- None recorded."])
            lines.append("")

        lines.extend(
            [
                "## Reviewed Surfaces",
                "",
                "| Surface | Disposition | Notes | Receipt |",
                "| --- | --- | --- | --- |",
            ]
        )
        for surface in coverage["surfaces"]:
            receipt = surface["receiptRefs"][0] if surface["receiptRefs"] else "none"
            lines.append(
                "| "
                + " | ".join(
                    ReportWriter._markdown_text(value)
                    for value in (
                        surface["label"],
                        surface["disposition"],
                        surface.get("notes", ""),
                        receipt,
                    )
                )
                + " |"
            )

        lines.extend(["", "## Findings", ""])
        if not findings:
            lines.append("No independently confirmed reportable findings were recorded.")
        for number, finding in enumerate(findings, 1):
            severity = finding["severity"]
            confidence = finding["confidence"]
            cwe = ", ".join(finding["taxonomy"]["cwe"]) or "None established"
            lines.extend(
                [
                    f"### {number}. [{severity['level'].upper()}] {ReportWriter._markdown_text(finding['title'])}",
                    "",
                    f"- Finding ID: `{finding['findingId']}`",
                    f"- Occurrence ID: `{finding['occurrenceId']}`",
                    f"- Fingerprint: `{finding['fingerprints']['primary']}`",
                    f"- Rule: `{finding['ruleId']}`",
                    f"- CWE: {ReportWriter._markdown_text(cwe)}",
                    f"- Confidence: `{confidence['level']}` — {ReportWriter._markdown_text(confidence['rationale'])}",
                    "",
                    ReportWriter._markdown_text(finding["summary"]),
                    "",
                    "#### Severity",
                    "",
                    ReportWriter._markdown_text(severity.get("rationale", "No rationale recorded.")),
                    "",
                    "#### Root Cause",
                    "",
                    ReportWriter._markdown_text(finding["rootCause"]["summary"]),
                    "",
                ]
            )
            for evidence in finding.get("codeEvidence", []):
                lines.extend(
                    [
                        f"##### {ReportWriter._markdown_text(evidence['label'])}",
                        "",
                        f"`{ReportWriter._markdown_text(evidence['path'])}:{evidence['startLine']}-{evidence.get('endLine', evidence['startLine'])}` · `{ReportWriter._markdown_text(evidence.get('role', 'evidence'))}`",
                        "",
                        ReportWriter._markdown_text(evidence["explanation"]),
                        "",
                    ]
                )
                fence = ReportWriter._code_fence(evidence["code"])
                lines.extend(
                    [
                        f"{fence}{evidence.get('language', '')}",
                        evidence["code"],
                        fence,
                        "",
                    ]
                )
            validation = finding["validation"]
            attack_path = finding["attackPath"]
            lines.extend(
                [
                    "#### Validation",
                    "",
                    f"Method: {ReportWriter._markdown_text(validation['method'])}",
                    "",
                    ReportWriter._markdown_text(validation["summary"]),
                    "",
                    "#### Attack Path",
                    "",
                    ReportWriter._markdown_text(attack_path["summary"]),
                    "",
                    f"- Dataflow: {ReportWriter._markdown_text(attack_path['dataflow']['summary'])}",
                    f"- Reachability: {ReportWriter._markdown_text(attack_path['reachability']['summary'])}",
                    "",
                    "#### Remediation",
                    "",
                    ReportWriter._markdown_text(finding["remediation"]),
                    "",
                ]
            )
            if finding.get("remediationTests"):
                lines.extend(["Regression tests:", ""])
                lines.extend(f"- {ReportWriter._markdown_text(item)}" for item in finding["remediationTests"])
                lines.append("")
            if finding.get("preventiveControls"):
                lines.extend(["Preventive controls:", ""])
                lines.extend(f"- {ReportWriter._markdown_text(item)}" for item in finding["preventiveControls"])
                lines.append("")

        lines.extend(["## Static Validation Limitations", ""])
        if limitations:
            for item in limitations:
                follow_up = item.get("followUpPrompt")
                suffix = f" Follow-up: {ReportWriter._markdown_text(follow_up)}" if follow_up else ""
                lines.append(f"- `{item['category']}`: {ReportWriter._markdown_text(item['question'])}{suffix}")
        else:
            lines.append("No static validation limitations were recorded.")
        lines.extend(["", "## Deferred Work", ""])
        if coverage["deferred"]:
            for item in coverage["deferred"]:
                paths = ", ".join(item.get("paths", []))
                suffix = f" ({ReportWriter._markdown_text(paths)})" if paths else ""
                lines.append(f"- `{item['id']}`: {ReportWriter._markdown_text(item['reason'])}{suffix}")
        else:
            lines.append("No work was deferred.")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _sarif(
        manifest: dict[str, Any],
        findings_document: dict[str, Any],
    ) -> dict[str, Any]:
        scan = manifest["scan"]
        findings = sorted(
            findings_document["findings"],
            key=lambda item: item["occurrenceId"],
        )
        rule_ids = sorted({item["ruleId"] for item in findings})
        rule_index = {rule_id: index for index, rule_id in enumerate(rule_ids)}
        rules = [
            {
                "id": rule_id,
                "name": rule_id,
                "shortDescription": {"text": rule_id},
                "properties": {"tags": ["security"]},
            }
            for rule_id in rule_ids
        ]
        results = []
        for finding in findings:
            locations = [
                *finding["locations"],
                *[
                    {
                        "path": item["path"],
                        "startLine": item["startLine"],
                        "endLine": item.get("endLine", item["startLine"]),
                        "role": f"evidence:{item['id']}",
                    }
                    for item in finding.get("codeEvidence", [])
                ],
            ]
            unique_locations: dict[tuple[str, int, int], dict[str, Any]] = {}
            for location in locations:
                key = (
                    location["path"],
                    location["startLine"],
                    location.get("endLine", location["startLine"]),
                )
                unique_locations.setdefault(key, location)
            results.append(
                {
                    "ruleId": finding["ruleId"],
                    "ruleIndex": rule_index[finding["ruleId"]],
                    "level": SARIF_LEVELS[finding["severity"]["level"]],
                    "message": {"text": finding["summary"]},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": quote(location["path"], safe="/")},
                                "region": {
                                    "startLine": location["startLine"],
                                    "endLine": location.get("endLine", location["startLine"]),
                                },
                            },
                            "message": {"text": location.get("role", "source")},
                        }
                        for location in unique_locations.values()
                    ],
                    "partialFingerprints": {"codexSecurity/v1": finding["fingerprints"]["primary"]},
                    "properties": {
                        "category": finding["taxonomy"]["category"],
                        "confidence": finding["confidence"]["level"],
                        "findingId": finding["findingId"],
                        "occurrenceId": finding["occurrenceId"],
                        "severity": finding["severity"]["level"],
                        "candidateId": finding["extensions"]["candidateId"],
                    },
                }
            )
        return {
            "$schema": "https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "Flocks Code Security",
                            "version": PRODUCER_VERSION,
                            "rules": rules,
                        }
                    },
                    "automationDetails": {"id": scan["id"]},
                    "results": results,
                    "properties": {
                        "codexSecuritySchemaVersion": manifest["schemaVersion"],
                        "codexSecurityTargetKind": scan["target"]["kind"],
                    },
                }
            ],
        }
