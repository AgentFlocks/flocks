"""Deterministic reduction and report writing for stored audit facts."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from flocks_code_security.paths import output_dir
from flocks_code_security.store import ScanStore


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


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
        try:
            data = self.store.report_data(scan_id)
            evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
            for item in data["evidence"]:
                evidence_by_candidate.setdefault(item["candidate_id"], []).append(item)
            verdicts_by_candidate: dict[str, list[dict[str, Any]]] = {}
            for item in data["verifications"]:
                verdicts_by_candidate.setdefault(item["candidate_id"], []).append(item)

            confirmed_groups: dict[str, list[dict[str, Any]]] = {}
            rejected: list[str] = []
            pending: list[str] = []
            for candidate in data["candidates"]:
                candidate_id = candidate["candidate_id"]
                evidence = evidence_by_candidate.get(candidate_id, [])
                verdicts = verdicts_by_candidate.get(candidate_id, [])
                if not evidence:
                    rejected.append(candidate_id)
                    continue
                if len(verdicts) != 1 or verdicts[0]["verdict"] == "insufficient_evidence":
                    pending.append(candidate_id)
                    continue
                verification = verdicts[0]
                if verification["verdict"] == "rejected":
                    rejected.append(candidate_id)
                    continue
                payload = candidate["payload"]
                primary = evidence[0]
                fingerprint_material = "\0".join(
                    (
                        str(payload.get("rule_id") or ""),
                        primary["relative_path"],
                        str(primary["start_line"]),
                        str(payload.get("dangerous_operation") or ""),
                    )
                )
                fingerprint = hashlib.sha256(
                    fingerprint_material.encode("utf-8")
                ).hexdigest()
                confirmed_groups.setdefault(fingerprint, []).append(
                    {
                        "candidate": candidate,
                        "evidence": evidence,
                        "verification": verification,
                    }
                )

            findings = [
                self._merge_confirmed_group(fingerprint, group)
                for fingerprint, group in confirmed_groups.items()
            ]
            findings.sort(
                key=lambda item: (
                    SEVERITY_ORDER.get(item["severity"], 99),
                    item["fingerprint"],
                )
            )

            coverage = [item["payload"] for item in data["coverage"]]
            scan = data["scan"]
            snapshot = self.store.get_snapshot(scan["snapshot_id"])
            if snapshot is None:
                raise ValueError("Scan snapshot not found")
            incomplete_reasons, incomplete_details = self._incomplete_state(
                data,
                pending,
            )
            status = "partial" if incomplete_reasons else "completed"
            candidates_by_id = {
                item["candidate_id"]: item for item in data["candidates"]
            }
            pending_candidates = [
                {
                    **candidates_by_id[candidate_id],
                    "evidence": evidence_by_candidate.get(candidate_id, []),
                    "verifications": verdicts_by_candidate.get(candidate_id, []),
                }
                for candidate_id in sorted(pending)
            ]
            target = output_dir(scan_id)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            target.parent.chmod(0o700)
            if target.exists():
                raise ValueError("The audit output directory already exists")
            staging = Path(
                tempfile.mkdtemp(
                    prefix=f".{scan_id}-",
                    dir=target.parent,
                )
            )
            staging.chmod(0o700)
            published = False
            status_committed = False

            manifest = {
                "scan_id": scan_id,
                "mode": scan["mode"],
                "status": status,
                "snapshot": snapshot.public_dict(),
                "ruleset_digest": scan["ruleset_digest"],
                "threat_model_status": "not_implemented",
                "finding_count": len(findings),
                "pending_candidate_ids": sorted(pending),
                "rejected_candidate_ids": sorted(rejected),
                "incomplete_reasons": incomplete_reasons,
                "incomplete_details": incomplete_details,
            }
            publishing_manifest = {
                **manifest,
                "status": "publishing",
                "result_status": status,
            }
            sarif = self._sarif(findings)
            self._write_json(staging / "scan-manifest.json", publishing_manifest)
            self._write_json(staging / ".scan-manifest.final", manifest)
            self._write_json(
                staging / "findings.json",
                {
                    "findings": findings,
                    "pending_candidates": pending_candidates,
                    "rejected_candidate_ids": sorted(rejected),
                },
            )
            self._write_json(
                staging / "coverage.json",
                {"coverage": coverage, "omissions": data["omissions"]},
            )
            self._write_json(staging / "report.sarif", sarif)
            staging_markdown_path = staging / "report.md"
            staging_markdown_path.write_text(
                self._markdown(manifest, findings, pending_candidates, coverage),
                encoding="utf-8",
            )
            staging_markdown_path.chmod(0o600)
            staging.replace(target)
            published = True
            self.store.transition_scan_status(
                scan_id,
                from_statuses={"reducing"},
                to_status=status,
            )
            status_committed = True
            (target / ".scan-manifest.final").replace(
                target / "scan-manifest.json"
            )
            markdown_path = target / "report.md"
            return {
                "scan_id": scan_id,
                "status": status,
                "finding_count": len(findings),
                "pending_count": len(pending),
                "output_dir": str(target),
                "report_path": str(markdown_path),
                "sarif_path": str(target / "report.sarif"),
            }
        except Exception:
            if "staging" in locals() and staging.exists():
                shutil.rmtree(staging)
            if (
                "published" in locals()
                and published
                and not status_committed
                and target.exists()
            ):
                shutil.rmtree(target)
            if "status_committed" not in locals() or not status_committed:
                try:
                    self.store.transition_scan_status(
                        scan_id,
                        from_statuses={"reducing"},
                        to_status="failed",
                    )
                except ValueError:
                    pass
            raise

    @staticmethod
    def _merge_confirmed_group(
        fingerprint: str,
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
        evidence_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
        for item in group:
            for evidence in item["evidence"]:
                key = (
                    evidence["relative_path"],
                    evidence["blob_digest"],
                    evidence["start_line"],
                    evidence["end_line"],
                )
                evidence_by_key[key] = evidence
        evidence = [evidence_by_key[key] for key in sorted(evidence_by_key)]
        severities = sorted(
            {item["candidate"]["payload"]["severity"] for item in group},
            key=lambda value: SEVERITY_ORDER.get(value, 99),
        )
        return {
            "finding_id": f"finding_{fingerprint[:24]}",
            "fingerprint": fingerprint,
            "candidate_id": candidate["candidate_id"],
            "candidate_ids": sorted(item["candidate"]["candidate_id"] for item in group),
            "rule_id": payload["rule_id"],
            "title": payload["title"],
            "severity": payload["severity"],
            "severity_conflicts": severities if len(severities) > 1 else [],
            "confidence": payload["confidence"],
            "attack_path": payload["attack_path"],
            "dangerous_operation": payload["dangerous_operation"],
            "remediation": payload["remediation"],
            "verification_rationale": selected["verification"]["rationale"],
            "primary_evidence": selected["evidence"][0],
            "evidence": evidence,
        }

    def _incomplete_state(
        self,
        data: dict[str, Any],
        pending: list[str],
    ) -> tuple[list[str], dict[str, Any]]:
        reasons: list[str] = []
        details: dict[str, Any] = {}
        if pending:
            reasons.append("pending_candidates")
            details["pending_candidate_ids"] = sorted(pending)
        if data["omissions"]:
            reasons.append("snapshot_omissions")
            details["snapshot_omissions"] = data["omissions"]
        if data["verification_conflicts"]:
            reasons.append("verification_conflicts")
            details["verification_conflicts"] = data["verification_conflicts"]
        work_units = data["work_units"]
        if not work_units:
            reasons.append("no_work_units")
        elif any(item["status"] != "completed" for item in work_units):
            reasons.append("incomplete_work_units")
            details["incomplete_work_units"] = [
                {
                    "work_unit_id": item["work_unit_id"],
                    "role": item["role"],
                    "status": item["status"],
                    "paths": item["paths"],
                }
                for item in work_units
                if item["status"] != "completed"
            ]

        coverage_by_unit = {
            item["work_unit_id"]: item["payload"] for item in data["coverage"]
        }
        analysis_units = [
            item for item in work_units if item["role"] in {"baseline", "investigator"}
        ]
        if not analysis_units:
            reasons.append("no_analysis_work_units")
        missing_coverage = sorted(
            item["work_unit_id"]
            for item in analysis_units
            if item["work_unit_id"] not in coverage_by_unit
        )
        if missing_coverage:
            reasons.append("missing_coverage")
            details["missing_coverage_work_unit_ids"] = missing_coverage

        coverage = [coverage_by_unit[item["work_unit_id"]] for item in analysis_units if item["work_unit_id"] in coverage_by_unit]
        failed_paths = sorted(
            {
                path
                for item in coverage
                for path in item["failed_paths"]
            }
        )
        if failed_paths:
            reasons.append("failed_paths")
            details["failed_paths"] = failed_paths
        open_questions = sorted(
            {
                question
                for item in coverage
                for question in item["open_questions"]
            }
        )
        if open_questions:
            reasons.append("open_questions")
            details["open_questions"] = open_questions

        analyzed_paths = {
            path
            for item in coverage
            for path in item["analyzed_paths"]
        }
        snapshot_paths = {
            item.relative_path
            for item in self.store.list_snapshot_files(data["scan"]["snapshot_id"])
        }
        uncovered = {
            path
            for path in snapshot_paths
            if not any(
                scope == "." or path == scope or path.startswith(f"{scope}/")
                for scope in analyzed_paths
            )
        }
        if uncovered:
            reasons.append("uncovered_snapshot_paths")
            details["uncovered_snapshot_paths"] = sorted(uncovered)
        return sorted(set(reasons)), details

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)

    @staticmethod
    def _markdown_text(value: Any) -> str:
        text = str(value)
        for character in "\\`*_{}[]()<>#!|":
            text = text.replace(character, f"\\{character}")
        return text

    @staticmethod
    def _markdown(
        manifest: dict[str, Any],
        findings: list[dict[str, Any]],
        pending: list[dict[str, Any]],
        coverage: list[dict[str, Any]],
    ) -> str:
        lines = [
            "# Code Security Audit Report",
            "",
            f"- Scan: `{manifest['scan_id']}`",
            f"- Status: `{manifest['status']}`",
            f"- Snapshot: `{manifest['snapshot']['snapshot_id']}`",
            f"- Tree digest: `{manifest['snapshot']['tree_digest']}`",
            f"- Findings: **{len(findings)}**",
            f"- Pending candidates: **{len(pending)}**",
            f"- Incomplete reasons: **{len(manifest['incomplete_reasons'])}**",
            "",
            "## Completeness",
            "",
        ]
        if manifest["incomplete_reasons"]:
            lines.extend(
                f"- `{ReportWriter._markdown_text(reason)}`"
                for reason in manifest["incomplete_reasons"]
            )
        else:
            lines.append("All required work units and snapshot paths were completed.")
        for detail_name, detail_value in sorted(
            manifest.get("incomplete_details", {}).items()
        ):
            lines.extend(
                [
                    "",
                    f"### {ReportWriter._markdown_text(detail_name)}",
                    "",
                ]
            )
            values = detail_value if isinstance(detail_value, list) else [detail_value]
            for value in values[:100]:
                serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
                lines.append(f"- {ReportWriter._markdown_text(serialized)}")
            if len(values) > 100:
                lines.append(f"- … {len(values) - 100} additional entries in scan-manifest.json")
        lines.extend([
            "",
            "## Findings",
            "",
        ])
        if not findings:
            lines.append("No independently confirmed findings were recorded.")
        for finding in findings:
            title = ReportWriter._markdown_text(finding["title"])
            rule_id = ReportWriter._markdown_text(finding["rule_id"])
            attack_path = ReportWriter._markdown_text(finding["attack_path"])
            dangerous_operation = ReportWriter._markdown_text(
                finding["dangerous_operation"]
            )
            remediation = ReportWriter._markdown_text(finding["remediation"])
            lines.extend(
                [
                    f"### [{finding['severity'].upper()}] {title}",
                    "",
                    f"Rule: `{rule_id}`  ",
                    f"Confidence: `{finding['confidence']}`  ",
                    f"Attack path: {attack_path}  ",
                    f"Dangerous operation: {dangerous_operation}",
                    "",
                    "Evidence:",
                ]
            )
            for evidence in finding["evidence"]:
                evidence_path = ReportWriter._markdown_text(
                    evidence["relative_path"]
                )
                lines.append(
                    f"- `{evidence_path}:{evidence['start_line']}`"
                    f"–`{evidence['end_line']}` (`{evidence['blob_digest']}`)"
                )
            lines.extend(["", f"Remediation: {remediation}", ""])
        lines.extend(
            [
                "## Coverage",
                "",
                f"Coverage submissions: **{len(coverage)}**",
                "",
                "A missing coverage submission must not be interpreted as analyzed scope.",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _sarif(findings: list[dict[str, Any]]) -> dict[str, Any]:
        rules: dict[str, dict[str, Any]] = {}
        results: list[dict[str, Any]] = []
        levels = {"critical": "error", "high": "error", "medium": "warning", "low": "note", "info": "note"}
        for finding in findings:
            rules.setdefault(
                finding["rule_id"],
                {
                    "id": finding["rule_id"],
                    "shortDescription": {"text": finding["title"]},
                },
            )
            primary = finding["primary_evidence"]
            results.append(
                {
                    "ruleId": finding["rule_id"],
                    "level": levels.get(finding["severity"], "warning"),
                    "message": {"text": finding["attack_path"]},
                    "fingerprints": {"flocksCodeSecurity/v1": finding["fingerprint"]},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": primary["relative_path"]},
                                "region": {
                                    "startLine": primary["start_line"],
                                    "endLine": primary["end_line"],
                                },
                            }
                        }
                    ],
                }
            )
        return {
            "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
            "version": "2.1.0",
            "runs": [
                {
                    "tool": {
                        "driver": {
                            "name": "flocks-code-security",
                            "version": "0.2.0",
                            "rules": list(rules.values()),
                        }
                    },
                    "results": results,
                }
            ],
        }
