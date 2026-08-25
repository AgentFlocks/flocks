"""Host-computed source coverage and coverage-question normalization."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from flocks_code_security.models import (
    CoverageAttestation,
    CoverageRecord,
    SessionBinding,
)


OPEN_QUESTION_CATEGORIES = {
    "coverage_blocking",
    "validation_limitation",
    "security_hypothesis",
}
COVERAGE_POLICIES = {"evidence_backed_partial", "exhaustive"}
COVERAGE_CLAIMS = {"analyzed", "failed", "not_applicable"}
TERMINAL_COVERAGE_STATES = {"read_complete", "failed", "not_applicable"}


class CoverageSubmissionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        code: str,
        violations: list[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.code = code
        self.violations = violations


def normalize_open_questions(raw_items: Any) -> list[dict[str, Any]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list) or len(raw_items) > 100:
        raise ValueError("open_questions must be an array of at most 100 items")

    normalized: list[dict[str, Any]] = []
    for raw in raw_items:
        if isinstance(raw, str):
            raw = {
                "question": raw,
                "category": "coverage_blocking",
                "blocking": True,
            }
        if not isinstance(raw, dict):
            raise ValueError("Each open question must be an object")
        unknown = set(raw) - {
            "question",
            "category",
            "blocking",
            "related_paths",
            "follow_up",
        }
        if unknown:
            raise ValueError(
                "Unsupported open-question fields: " + ", ".join(sorted(unknown))
            )
        question = raw.get("question")
        category = raw.get("category")
        blocking = raw.get("blocking")
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Open-question text must be a non-empty string")
        if len(question) > 1_000:
            raise ValueError("Open-question text may contain at most 1000 characters")
        if category not in OPEN_QUESTION_CATEGORIES:
            raise ValueError("Unsupported open-question category")
        expected_blocking = category == "coverage_blocking"
        if not isinstance(blocking, bool) or blocking is not expected_blocking:
            raise ValueError(
                "blocking must be true exactly when category is coverage_blocking"
            )
        related_paths = raw.get("related_paths", [])
        if not isinstance(related_paths, list) or len(related_paths) > 100:
            raise ValueError("related_paths must be an array of at most 100 paths")
        if any(not isinstance(path, str) or not path for path in related_paths):
            raise ValueError("related_paths must contain non-empty strings")
        follow_up = raw.get("follow_up")
        if follow_up is not None and (
            not isinstance(follow_up, str)
            or not follow_up.strip()
            or len(follow_up) > 1_000
        ):
            raise ValueError("follow_up must be a non-empty string of at most 1000 characters")

        item: dict[str, Any] = {
            "question": question.strip(),
            "category": category,
            "blocking": blocking,
            "related_paths": list(dict.fromkeys(related_paths)),
        }
        if follow_up is not None:
            item["follow_up"] = follow_up.strip()
        normalized.append(item)
    return normalized


def public_open_question(item: dict[str, Any]) -> dict[str, Any]:
    output = {
        "question": item["question"],
        "category": item["category"],
        "blocking": item["blocking"],
        "relatedPaths": item.get("related_paths", []),
    }
    if item.get("follow_up"):
        output["followUpPrompt"] = item["follow_up"]
    return output


def normalize_dispositions(raw_items: Any) -> list[dict[str, str]]:
    if raw_items is None:
        return []
    if not isinstance(raw_items, list) or len(raw_items) > 2_000:
        raise ValueError("dispositions must be an array of at most 2000 items")
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(f"dispositions[{index}] must be an object")
        unknown = set(raw) - {"path", "claim", "reason"}
        if unknown:
            raise ValueError(
                f"dispositions[{index}] has unsupported fields: "
                + ", ".join(sorted(unknown))
            )
        path = raw.get("path")
        claim = raw.get("claim")
        if not isinstance(path, str) or not path.strip():
            raise ValueError(f"dispositions[{index}].path must be a non-empty string")
        path = path.strip().replace("\\", "/")
        if claim not in COVERAGE_CLAIMS:
            raise ValueError(f"dispositions[{index}].claim is unsupported")
        if path in seen:
            raise ValueError(f"Duplicate coverage disposition path: {path}")
        seen.add(path)
        item = {"path": path, "claim": claim}
        reason = raw.get("reason")
        if claim in {"failed", "not_applicable"}:
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(
                    f"dispositions[{index}].reason is required for {claim}"
                )
            if len(reason) > 1_000:
                raise ValueError(
                    f"dispositions[{index}].reason may contain at most 1000 characters"
                )
            item["reason"] = reason.strip()
        elif reason is not None:
            raise ValueError(
                f"dispositions[{index}].reason is only allowed for failed or not_applicable"
            )
        normalized.append(item)
    return normalized


def _canonical_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _in_assigned_scope(path: str, assigned: list[str]) -> bool:
    return any(
        scope == "." or path == scope or path.startswith(f"{scope}/")
        for scope in assigned
    )


def _receipt_digest(rows: list[dict[str, Any]]) -> str | None:
    receipts = sorted(
        {
            (
                str(row["operation"]),
                str(row["relative_path"]),
                str(row.get("blob_digest") or ""),
                row.get("start_line"),
                row.get("end_line"),
            )
            for row in rows
        }
    )
    return _canonical_digest(receipts) if receipts else None


class CoverageAttestationService:
    """The sole producer of trusted per-file coverage attestations."""

    def __init__(self, store: Any):
        self.store = store

    def attest(
        self,
        binding: SessionBinding,
        *,
        dispositions: list[dict[str, Any]] | None = None,
        open_questions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if binding.work_unit_id is None or binding.attempt_id is None:
            raise ValueError("Coverage requires a bound work attempt")
        unit = self.store.get_work_unit(binding.work_unit_id)
        scan = self.store.get_scan(binding.scan_id)
        if unit is None or scan is None or unit["scan_id"] != binding.scan_id:
            raise ValueError("Coverage work unit does not match the binding")
        policy = str(scan.get("coverage_policy") or "evidence_backed_partial")
        if policy not in COVERAGE_POLICIES:
            raise ValueError("Unsupported coverage policy")
        normalized_dispositions = normalize_dispositions(dispositions)
        questions = normalize_open_questions(open_questions)
        files = [
            item
            for item in self.store.list_snapshot_files(binding.snapshot_id)
            if _in_assigned_scope(item.relative_path, unit["paths"])
        ]
        omissions = [
            item
            for item in self.store.list_snapshot_omissions(binding.snapshot_id)
            if _in_assigned_scope(item.relative_path, unit["paths"])
        ]
        assigned_paths = {
            item.relative_path for item in [*files, *omissions]
        }
        invalid_paths = sorted(
            item["path"]
            for item in normalized_dispositions
            if item["path"] not in assigned_paths
        )
        if invalid_paths:
            violations = [
                {"path": path, "reason": "not_an_exact_assigned_snapshot_path"}
                for path in invalid_paths
            ]
            raise CoverageSubmissionError(
                "Coverage path is outside the exact work-unit scope: "
                + ", ".join(invalid_paths[:20]),
                code="SCOPE_PATH_INVALID",
                violations=violations,
            )
        claims = {item["path"]: item for item in normalized_dispositions}
        accesses = self.store.list_source_accesses(binding.attempt_id)
        accesses_by_path: dict[str, list[dict[str, Any]]] = {}
        for access in accesses:
            accesses_by_path.setdefault(access["relative_path"], []).append(access)

        records: list[CoverageRecord] = []
        overclaims: list[dict[str, Any]] = []
        for item in files:
            path_accesses = accesses_by_path.get(item.relative_path, [])
            matching_accesses = [
                row
                for row in path_accesses
                if row.get("blob_digest") in {None, item.blob_digest}
            ]
            inventory_rows = [
                row for row in matching_accesses if row["operation"] == "inventory"
            ]
            search_rows = [
                row for row in matching_accesses if row["operation"] == "search"
            ]
            read_rows = [
                row
                for row in matching_accesses
                if row["operation"] == "read"
                and row.get("blob_digest") == item.blob_digest
                and row.get("start_line") is not None
                and row.get("end_line") is not None
            ]
            state = "unexamined"
            reason: str | None = None
            supporting_rows: list[dict[str, Any]] = []
            if item.size_bytes == 0:
                state = "not_applicable"
                reason = "not_applicable_empty"
            elif read_rows:
                covered_until = 0
                for row in sorted(
                    read_rows,
                    key=lambda value: (value["start_line"], value["end_line"]),
                ):
                    if row["start_line"] > covered_until + 1:
                        break
                    covered_until = max(covered_until, row["end_line"])
                state = (
                    "read_complete"
                    if covered_until >= item.line_count
                    else "read_partial"
                )
                supporting_rows = read_rows
            elif search_rows:
                state = "located"
                supporting_rows = search_rows
            elif inventory_rows:
                state = "inventoried"
                supporting_rows = inventory_rows

            disposition = claims.get(item.relative_path)
            if disposition is not None and disposition["claim"] == "analyzed":
                if state != "read_complete":
                    overclaims.append(
                        {
                            "path": item.relative_path,
                            "claimed_state": "analyzed",
                            "actual_state": state,
                            "required_receipt": "read_complete",
                        }
                    )
            elif (
                disposition is not None
                and disposition["claim"] == "not_applicable"
                and item.size_bytes > 0
                and not item.is_binary
            ):
                overclaims.append(
                    {
                        "path": item.relative_path,
                        "claimed_state": "not_applicable",
                        "actual_state": state,
                        "required_receipt": "host_determined_not_applicable",
                    }
                )
            elif disposition is not None:
                state = disposition["claim"]
                reason = disposition["reason"]
                supporting_rows = []
            records.append(
                CoverageRecord(
                    relative_path=item.relative_path,
                    state=state,
                    reason=reason,
                    receipt_digest=_receipt_digest(supporting_rows),
                )
            )

        for omission in omissions:
            disposition = claims.get(omission.relative_path)
            state = "failed"
            reason = f"snapshot_omission:{omission.reason}"
            if disposition is not None and disposition["claim"] == "analyzed":
                overclaims.append(
                    {
                        "path": omission.relative_path,
                        "claimed_state": "analyzed",
                        "actual_state": "failed",
                        "required_receipt": "read_complete",
                    }
                )
            elif disposition is not None:
                state = disposition["claim"]
                reason = disposition["reason"]
            records.append(
                CoverageRecord(
                    relative_path=omission.relative_path,
                    state=state,
                    reason=reason,
                    receipt_digest=None,
                )
            )

        if overclaims:
            raise CoverageSubmissionError(
                "Coverage claims are not backed by complete snapshot source reads or host facts: "
                + ", ".join(item["path"] for item in overclaims[:20]),
                code="COVERAGE_OVERCLAIM",
                violations=overclaims,
            )

        records.sort(key=lambda item: item.relative_path)
        read_complete_count = sum(
            item.state == "read_complete" for item in records
        )
        failed_count = sum(item.state == "failed" for item in records)
        unexamined_count = sum(
            item.state not in TERMINAL_COVERAGE_STATES for item in records
        )
        has_blocking_question = any(item["blocking"] for item in questions)
        if unexamined_count == 0 and not has_blocking_question:
            completeness = "complete"
        elif policy == "exhaustive":
            completeness = "blocked"
        else:
            completeness = "partial"
        attestation_id = "attestation_" + hashlib.sha256(
            f"{binding.work_unit_id}\0{binding.attempt_id}".encode("utf-8")
        ).hexdigest()[:32]
        digest_payload = {
            "work_unit_id": binding.work_unit_id,
            "attempt_id": binding.attempt_id,
            "policy": policy,
            "completeness": completeness,
            "records": [item.public_dict() for item in records],
            "open_questions": questions,
        }
        attestation = CoverageAttestation(
            attestation_id=attestation_id,
            work_unit_id=binding.work_unit_id,
            attempt_id=binding.attempt_id,
            policy=policy,
            completeness=completeness,
            assigned_count=len(records),
            read_complete_count=read_complete_count,
            failed_count=failed_count,
            unexamined_count=unexamined_count,
            attestation_digest=_canonical_digest(digest_payload),
        )
        result = {
            **attestation.public_dict(),
            "records": [item.public_dict() for item in records],
            "open_questions": questions,
        }
        self.store.save_coverage_attestation(
            binding,
            attestation=attestation,
            records=records,
            open_questions=questions,
        )
        return result
