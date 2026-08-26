from __future__ import annotations

import pytest

from flocks_code_security.coverage import (
    CoverageAttestationService,
    CoverageSubmissionError,
    merge_analysis_coverage,
)
from flocks_code_security.models import SessionBinding, SnapshotFile, SnapshotOmission


def _attestation(
    work_unit_id: str,
    role: str,
    records: list[dict],
    *,
    phase: str | None = None,
    questions: list[dict] | None = None,
    policy: str = "evidence_backed_partial",
) -> dict:
    return {
        "attestation_id": f"attestation_{work_unit_id}",
        "work_unit_id": work_unit_id,
        "role": role,
        "phase": phase or ("investigation" if role == "investigator" else "baseline"),
        "policy": policy,
        "records": records,
        "open_questions": questions or [],
    }


def test_merge_uses_strongest_state_once_with_stable_order() -> None:
    baseline = _attestation(
        "baseline",
        "baseline",
        [
            {"relative_path": "b.py", "state": "failed", "reason": "timeout", "receipt_digest": None},
            {"relative_path": "a.py", "state": "read_partial", "reason": None, "receipt_digest": "a"},
        ],
    )
    investigator = _attestation(
        "investigator",
        "investigator",
        [
            {"relative_path": "a.py", "state": "read_complete", "reason": None, "receipt_digest": "b"},
            {"relative_path": "b.py", "state": "read_complete", "reason": None, "receipt_digest": "c"},
        ],
    )

    first = merge_analysis_coverage([baseline, investigator])
    second = merge_analysis_coverage([investigator, baseline])

    assert first == second
    assert [(item["relative_path"], item["state"]) for item in first["records"]] == [
        ("a.py", "read_complete"),
        ("b.py", "read_complete"),
    ]
    assert first["counts"]["assigned"] == 2
    assert first["completeness"] == "complete"


def test_investigator_replaces_assigned_baseline_question_and_resubmits_unresolved() -> None:
    baseline_question = {
        "question": "Trace the handler.",
        "category": "coverage_blocking",
        "blocking": True,
        "related_paths": ["app.py"],
    }
    unresolved = {
        "question": "Trace the handler through the wrapper.",
        "category": "coverage_blocking",
        "blocking": True,
        "related_paths": ["app.py"],
    }
    merged = merge_analysis_coverage(
        [
            _attestation(
                "baseline",
                "baseline",
                [{"relative_path": "app.py", "state": "read_partial"}],
                questions=[baseline_question],
                policy="exhaustive",
            ),
            _attestation(
                "investigator",
                "investigator",
                [{"relative_path": "app.py", "state": "read_complete"}],
                questions=[unresolved],
                policy="exhaustive",
            ),
        ]
    )

    assert merged["open_questions"] == [unresolved]
    assert merged["completeness"] == "blocked"


def test_investigator_does_not_replace_later_targeted_rescan_question() -> None:
    rescan_question = {
        "question": "Review the new call path.",
        "category": "coverage_blocking",
        "blocking": True,
        "related_paths": ["app.py"],
    }
    merged = merge_analysis_coverage(
        [
            _attestation(
                "investigator",
                "investigator",
                [{"relative_path": "app.py", "state": "read_complete"}],
            ),
            _attestation(
                "rescan",
                "baseline",
                [{"relative_path": "app.py", "state": "read_partial"}],
                phase="targeted_rescan",
                questions=[rescan_question],
            ),
        ]
    )

    assert merged["open_questions"] == [rescan_question]


def test_merge_normalizes_question_and_related_path_order() -> None:
    first_question = {
        "question": "Confirm the deployment control.",
        "category": "validation_limitation",
        "blocking": False,
        "related_paths": ["b.py", "a.py"],
    }
    second_question = {
        "question": "Check the unresolved hypothesis.",
        "category": "security_hypothesis",
        "blocking": False,
        "related_paths": ["a.py"],
    }
    records = [
        {"relative_path": "a.py", "state": "read_complete"},
        {"relative_path": "b.py", "state": "read_complete"},
    ]

    first = merge_analysis_coverage(
        [
            _attestation(
                "baseline",
                "baseline",
                records,
                questions=[first_question, second_question],
            )
        ]
    )
    second = merge_analysis_coverage(
        [
            _attestation(
                "baseline",
                "baseline",
                list(reversed(records)),
                questions=[
                    second_question,
                    {**first_question, "related_paths": ["a.py", "b.py"]},
                ],
            )
        ]
    )

    assert first == second
    by_question = {item["question"]: item for item in first["open_questions"]}
    assert by_question["Confirm the deployment control."]["related_paths"] == [
        "a.py",
        "b.py",
    ]


def test_partial_investigator_cannot_silently_drop_baseline_question() -> None:
    baseline_question = {
        "question": "Trace the unread handler flow.",
        "category": "coverage_blocking",
        "blocking": True,
        "related_paths": ["app.py"],
    }

    merged = merge_analysis_coverage(
        [
            _attestation(
                "baseline",
                "baseline",
                [{"relative_path": "app.py", "state": "read_partial"}],
                questions=[baseline_question],
            ),
            _attestation(
                "investigator",
                "investigator",
                [{"relative_path": "app.py", "state": "inventoried"}],
            ),
        ]
    )

    assert merged["open_questions"] == [baseline_question]
    assert merged["completeness"] == "partial"


def test_snapshot_omission_cannot_be_marked_not_applicable() -> None:
    class Store:
        @staticmethod
        def get_work_unit(_work_unit_id: str):
            return {"scan_id": "scan", "paths": ["."]}

        @staticmethod
        def get_scan(_scan_id: str):
            return {"coverage_policy": "exhaustive"}

        @staticmethod
        def list_snapshot_files(_snapshot_id: str):
            return [
                SnapshotFile(
                    relative_path="app.py",
                    blob_digest="a" * 64,
                    size_bytes=1,
                    line_count=1,
                    language="python",
                    is_binary=False,
                )
            ]

        @staticmethod
        def list_snapshot_omissions(_snapshot_id: str):
            return [
                SnapshotOmission(
                    relative_path="large.py",
                    reason="too_large",
                    size_bytes=999,
                )
            ]

        @staticmethod
        def list_source_accesses(_attempt_id: str):
            return [
                {
                    "operation": "read",
                    "relative_path": "app.py",
                    "blob_digest": "a" * 64,
                    "start_line": 1,
                    "end_line": 1,
                }
            ]

        @staticmethod
        def save_coverage_attestation(*_args, **_kwargs):
            return None

    service = CoverageAttestationService(Store())
    binding = SessionBinding(
        session_id="session",
        scan_id="scan",
        snapshot_id="snapshot",
        role="baseline",
        work_unit_id="unit",
        attempt_id="attempt",
    )

    with pytest.raises(CoverageSubmissionError) as rejected:
        service.attest(
            binding,
            dispositions=[
                {"path": "app.py", "claim": "analyzed"},
                {
                    "path": "large.py",
                    "claim": "not_applicable",
                    "reason": "too large to inspect",
                },
            ],
        )

    assert rejected.value.code == "COVERAGE_OVERCLAIM"
    assert rejected.value.violations == [
        {
            "path": "large.py",
            "claimed_state": "not_applicable",
            "actual_state": "failed",
            "required_receipt": "host_determined_snapshot_omission",
        }
    ]
    blocked = service.attest(
        binding,
        dispositions=[{"path": "app.py", "claim": "analyzed"}],
    )
    assert blocked["completeness"] == "blocked"
    assert blocked["counts"]["failed"] == 1
