"""Deterministic Codex Security v1 reduction, sealing, and projections."""

from __future__ import annotations

import copy
import hashlib
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from flocks_code_security.contract import (
    PRODUCER_NAME,
    PRODUCER_VERSION,
    SCHEMA_VERSION,
    artifact_record,
    canonical_json_bytes,
    finding_fingerprint,
    finding_identity,
    snapshot_digest,
    stable_id,
    validate_bundle,
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
            verdict_by_candidate = {
                item["candidate_id"]: item for item in data["verifications"]
            }

            confirmed_groups: dict[str, list[dict[str, Any]]] = {}
            outcomes: dict[str, str] = {}
            deferred_ids: list[str] = []
            for candidate in data["candidates"]:
                candidate_id = candidate["candidate_id"]
                evidence = evidence_by_candidate.get(candidate_id, [])
                verification = verdict_by_candidate.get(candidate_id)
                if not evidence or verification is None:
                    raise ValueError(
                        "Canonical reduction requires evidence and one verification per candidate"
                    )
                verdict = verification["verdict"]
                if verdict == "rejected":
                    outcomes[candidate_id] = "rejected"
                    continue
                if verdict == "insufficient_evidence":
                    outcomes[candidate_id] = "deferred"
                    deferred_ids.append(candidate_id)
                    continue
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
            artifact_contents = {
                "findings.json": findings_bytes,
                "coverage.json": coverage_bytes,
                **receipts,
            }
            artifacts = [
                artifact_record("findings.json", findings_bytes),
                artifact_record("coverage.json", coverage_bytes),
                *[
                    artifact_record(path, contents, "application/json")
                    for path, contents in sorted(receipts.items())
                ],
            ]
            manifest = self._manifest(
                scan,
                snapshot,
                threat_model_record["threat_model"],
                coverage_document,
                completed_at,
                artifacts,
            )
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
            staging = Path(
                tempfile.mkdtemp(prefix=f".{scan_id}-", dir=target.parent)
            )
            staging.chmod(0o700)
            for path, contents in receipts.items():
                self._write_bytes(staging / path, contents)
            self._write_json(staging / "findings.json", findings_document)
            self._write_json(staging / "coverage.json", coverage_document)
            self._write_json(
                staging / "threat-model.json",
                {
                    "scanId": scan_id,
                    "snapshotId": snapshot.snapshot_id,
                    "workUnitId": threat_model_record["work_unit_id"],
                    "createdAt": threat_model_record["created_at"],
                    "threatModel": threat_model_record["threat_model"],
                    "evidence": threat_model_record["evidence"],
                },
            )
            self._write_bytes(
                staging / "report.md",
                self._markdown(manifest, findings_document, coverage_document).encode(
                    "utf-8"
                ),
            )
            self._write_json(
                staging / "report.sarif",
                self._sarif(manifest, findings_document),
            )
            self._write_json(staging / "scan-manifest.json", manifest)
            staging.replace(target)
            published = True
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
                "pending_count": 0,
                "deferred_count": len(coverage_document["deferred"]),
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
            evidence_id = "evidence-" + hashlib.sha256(
                "\0".join(str(part) for part in key).encode("utf-8")
            ).hexdigest()[:16]
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
        if len(group) > 1:
            extensions["candidateIds"] = sorted(
                item["candidate"]["candidate_id"] for item in group
            )
        if len(severity_conflicts) > 1:
            extensions["severityConflicts"] = severity_conflicts
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
            "validation": {
                "method": "independent static source review",
                "summary": verification["rationale"],
                "conclusion": "confirmed",
                "evidenceRefs": evidence_refs,
                "counterevidence": verification["counter_evidence"],
                "limitations": [
                    "Validated by static source review; target code was not executed."
                ],
            },
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
        coverage_by_unit = {
            item["work_unit_id"]: item["payload"] for item in data["coverage"]
        }
        candidates_by_unit: dict[str, list[dict[str, Any]]] = {}
        candidates_by_id: dict[str, dict[str, Any]] = {}
        for candidate in data["candidates"]:
            candidates_by_id[candidate["candidate_id"]] = candidate
            if candidate.get("work_unit_id"):
                candidates_by_unit.setdefault(candidate["work_unit_id"], []).append(
                    candidate
                )

        surfaces: list[dict[str, Any]] = []
        receipts: dict[str, bytes] = {}
        deferred: list[dict[str, Any]] = []
        open_questions: list[dict[str, str]] = []
        surface_by_unit: dict[str, str] = {}
        analysis_units = [
            item
            for item in data["work_units"]
            if item["role"] in {"baseline", "investigator"}
        ]
        for unit in analysis_units:
            paths = unit["paths"]
            surface_id = "surface_" + hashlib.sha256(
                "\0".join((unit["work_unit_id"], *paths)).encode("utf-8")
            ).hexdigest()[:16]
            surface_by_unit[unit["work_unit_id"]] = surface_id
            coverage = coverage_by_unit.get(unit["work_unit_id"])
            candidates = candidates_by_unit.get(unit["work_unit_id"], [])
            unit_outcomes = [outcomes[item["candidate_id"]] for item in candidates]
            needs_follow_up = unit["status"] != "completed" or coverage is None
            if coverage is not None:
                needs_follow_up = needs_follow_up or bool(
                    coverage["failed_paths"] or coverage["open_questions"]
                )
            if "reported" in unit_outcomes:
                disposition = "reported"
            elif needs_follow_up or "deferred" in unit_outcomes:
                disposition = "needs_follow_up"
            elif "rejected" in unit_outcomes:
                disposition = "rejected"
            else:
                disposition = "no_issue_found"

            receipt_path = f"artifacts/03_coverage/{surface_id}.json"
            receipt = {
                "documentType": "flocks-code-security.coverage-receipt",
                "schemaVersion": "1.0",
                "scanId": scan_id,
                "surfaceId": surface_id,
                "workUnitId": unit["work_unit_id"],
                "workUnitStatus": unit["status"],
                "assignedPaths": paths,
                "inventory": [] if coverage is None else coverage["inventoried_paths"],
                "analyzed": [] if coverage is None else coverage["analyzed_paths"],
                "failed": [] if coverage is None else coverage["failed_paths"],
                "openQuestions": [] if coverage is None else coverage["open_questions"],
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
                    "label": (
                        "Repository source"
                        if paths == ["."]
                        else "Source scope: " + ", ".join(paths)
                    ),
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
            for failed_path in coverage["failed_paths"]:
                deferred.append(
                    {
                        "id": stable_id(
                            "deferred", unit["work_unit_id"], "failed", failed_path
                        ),
                        "reason": "Source path could not be fully analyzed",
                        "paths": [failed_path],
                        "surfaceIds": [surface_id],
                    }
                )
            for question in coverage["open_questions"]:
                deferred.append(
                    {
                        "id": stable_id(
                            "deferred", unit["work_unit_id"], "question", question
                        ),
                        "reason": question,
                        "surfaceIds": [surface_id],
                    }
                )
                open_questions.append({"question": question})

        for candidate_id in deferred_candidate_ids:
            candidate = candidates_by_id[candidate_id]
            surface_id = surface_by_unit.get(candidate.get("work_unit_id", ""))
            paths = sorted(
                {
                    item["relative_path"]
                    for item in data["evidence"]
                    if item["candidate_id"] == candidate_id
                }
            )
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
            deferred.append(
                {
                    "id": stable_id("deferred", "omission", omission["relative_path"]),
                    "reason": f"Snapshot omission: {omission['reason']}",
                    "paths": [omission["relative_path"]],
                }
            )

        analyzed_paths = {
            path
            for coverage in coverage_by_unit.values()
            for path in coverage["analyzed_paths"]
        }
        snapshot_paths = {
            item.relative_path
            for item in self.store.list_snapshot_files(snapshot.snapshot_id)
        }
        uncovered = sorted(
            path
            for path in snapshot_paths
            if not any(
                scope == "." or path == scope or path.startswith(f"{scope}/")
                for scope in analyzed_paths
            )
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
        explicit_exclusions = [
            {
                "pattern": pattern,
                "reason": (
                    "Excluded by the deterministic snapshot safety policy"
                    if pattern in DEFAULT_EXCLUDES
                    else "Excluded by the requested audit scope"
                ),
            }
            for pattern in dict.fromkeys(snapshot.exclude_patterns)
        ]
        if snapshot.target_kind.startswith("git_"):
            explicit_exclusions.append(
                {
                    "pattern": "<repository-git-ignore-rules>",
                    "reason": "Excluded by repository Git ignore rules during inventory",
                }
            )
        include_paths = list(snapshot.include_paths)
        exclude_paths = list(snapshot.exclude_patterns)
        scoped = include_paths != ["."]
        completeness = (
            "complete"
            if not deferred
            and all(item["disposition"] != "needs_follow_up" for item in surfaces)
            else "partial"
        )
        document: dict[str, Any] = {
            "documentType": "codex-security.coverage",
            "schemaVersion": SCHEMA_VERSION,
            "scanId": scan_id,
            "mode": "scoped_path" if scoped else "repository",
            "completeness": completeness,
            "inventoryStrategy": (
                "scoped_path"
                if scoped
                else "repository"
                if snapshot.target_kind.startswith("git_")
                else "directory"
            ),
            "includePaths": include_paths,
            "excludePaths": exclude_paths,
            "surfaces": sorted(surfaces, key=lambda item: item["id"]),
            "explicitExclusions": explicit_exclusions,
            "deferred": deferred,
        }
        if open_questions:
            document["openQuestions"] = open_questions
        return document, receipts

    @staticmethod
    def _manifest(
        scan: dict[str, Any],
        snapshot,
        threat_model: dict[str, Any],
        coverage: dict[str, Any],
        completed_at: str,
        artifacts: list[dict[str, str]],
    ) -> dict[str, Any]:
        target: dict[str, Any] = {
            "kind": snapshot.target_kind,
            "targetId": snapshot.repository_identity,
            "displayName": snapshot.display_name,
        }
        if snapshot.source_revision:
            target["revision"] = snapshot.source_revision
        if snapshot.target_kind != "git_revision":
            target["snapshotDigest"] = snapshot_digest(snapshot.tree_digest)
        limitations = [item["reason"] for item in coverage["deferred"][:100]]
        scope: dict[str, Any] = {
            "includePaths": coverage["includePaths"],
            "excludePaths": coverage["excludePaths"],
            "summary": (
                f"Static review of {snapshot.file_count} immutable snapshot files."
            ),
            "runtimeStatus": "Target code was not executed.",
            "validationMode": "Independent static source verification",
            "context": "Threat-model-guided standard source-code security audit.",
        }
        if limitations:
            scope["limitations"] = limitations
        return {
            "documentType": "codex-security.scan-manifest",
            "schemaVersion": SCHEMA_VERSION,
            "scan": {
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
            },
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
        code = "\n".join(
            lines[evidence["start_line"] - 1 : evidence["end_line"]]
        )
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
            f"- Deferred work: **{len(coverage['deferred'])}**",
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
        for heading, field in (
            ("Assets", "assets"),
            ("Trust Boundaries", "trustBoundaries"),
            ("Attacker Capabilities", "attackerCapabilities"),
            ("Security Objectives", "securityObjectives"),
            ("Assumptions", "assumptions"),
        ):
            lines.extend([f"### {heading}", ""])
            values = scan["threatModel"].get(field, [])
            lines.extend(
                [f"- {ReportWriter._markdown_text(value)}" for value in values]
                or ["- None recorded."]
            )
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
                lines.extend(
                    f"- {ReportWriter._markdown_text(item)}"
                    for item in finding["remediationTests"]
                )
                lines.append("")
            if finding.get("preventiveControls"):
                lines.extend(["Preventive controls:", ""])
                lines.extend(
                    f"- {ReportWriter._markdown_text(item)}"
                    for item in finding["preventiveControls"]
                )
                lines.append("")

        lines.extend(["## Deferred Work", ""])
        if coverage["deferred"]:
            for item in coverage["deferred"]:
                paths = ", ".join(item.get("paths", []))
                suffix = f" ({ReportWriter._markdown_text(paths)})" if paths else ""
                lines.append(
                    f"- `{item['id']}`: {ReportWriter._markdown_text(item['reason'])}{suffix}"
                )
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
                                "artifactLocation": {
                                    "uri": quote(location["path"], safe="/")
                                },
                                "region": {
                                    "startLine": location["startLine"],
                                    "endLine": location.get(
                                        "endLine", location["startLine"]
                                    ),
                                },
                            },
                            "message": {"text": location.get("role", "source")},
                        }
                        for location in unique_locations.values()
                    ],
                    "partialFingerprints": {
                        "codexSecurity/v1": finding["fingerprints"]["primary"]
                    },
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
