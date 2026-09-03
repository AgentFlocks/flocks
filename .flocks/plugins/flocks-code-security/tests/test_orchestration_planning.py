from __future__ import annotations

from collections import Counter

import pytest

from flocks_code_security.models import (
    RepositoryManifest,
    SnapshotFile,
    SnapshotOmission,
)
from flocks_code_security.orchestration import (
    MAX_SCOPES_PER_WORK_UNIT,
    FollowUpPlanningError,
    build_follow_up_unit,
    cybergym_solver_prompt,
    plan_baseline_units,
    plan_verification_units,
)


def _file(path: str, *, size_bytes: int = 16) -> SnapshotFile:
    return SnapshotFile(
        relative_path=path,
        blob_digest=f"{len(path):064x}"[-64:],
        size_bytes=size_bytes,
        line_count=1,
        language="python",
        is_binary=False,
    )


def _manifest(
    files: list[SnapshotFile],
    *,
    manifest_digest: str = "a" * 64,
    omissions: tuple[SnapshotOmission, ...] = (),
) -> RepositoryManifest:
    return RepositoryManifest(
        manifest_id="manifest_test",
        snapshot_id="snapshot_test",
        manifest_digest=manifest_digest,
        file_count=len(files),
        total_bytes=sum(item.size_bytes for item in files),
        omitted_file_count=len(omissions),
        languages=(("python", len(files)),),
        components=(),
        created_at="2026-08-25T00:00:00+00:00",
        files=tuple(files),
        omissions=omissions,
    )


def _assignment_counts(
    files: list[SnapshotFile],
    units: list[dict],
) -> Counter[str]:
    counts: Counter[str] = Counter()
    file_paths = {item.relative_path for item in files}
    for unit in units:
        exact_files = set(unit["paths"]) & file_paths
        prefixes = [path for path in unit["paths"] if path not in exact_files]
        for item in files:
            if (
                "." in unit["paths"]
                or item.relative_path in exact_files
                or any(
                    item.relative_path.startswith(f"{prefix}/")
                    for prefix in prefixes
                )
            ):
                counts[item.relative_path] += 1
    return counts


def test_small_repository_stays_in_one_deterministic_work_unit() -> None:
    files = [_file("app.py"), _file("src/auth.py")]
    manifest = _manifest(files)

    first = plan_baseline_units(manifest)
    second = plan_baseline_units(manifest)

    assert first == second
    assert len(first) == 1
    assert first[0]["paths"] == ["."]
    assert len(first[0]["assignment_digest"]) == 64
    assert _assignment_counts(files, first) == Counter({"app.py": 1, "src/auth.py": 1})


def test_cybergym_solver_prompt_requires_contract_aware_preflight() -> None:
    prompt = cybergym_solver_prompt()

    assert "input_contract" in prompt
    assert "required_suffix_hex" in prompt
    assert prompt.index("replay") < prompt.index("fuzz")
    assert "already includes its replay" in prompt


def test_large_files_do_not_create_extra_baseline_workers() -> None:
    files = [
        _file(f"large/module_{index:02d}.py", size_bytes=20 * 1024 * 1024)
        for index in range(8)
    ]

    units = plan_baseline_units(_manifest(files))

    assert len(units) == 1
    assert units[0]["paths"] == ["."]
    assert units[0]["assigned_bytes"] == 160 * 1024 * 1024


def test_repository_baseline_keeps_one_snapshot_wide_scope() -> None:
    files = [
        _file("app.py"),
        _file("src/auth.py"),
        _file("src/tests/test_auth.py"),
        _file("docs/security.md"),
        _file("assets/logo.svg"),
        _file("assets/logo.SVG"),
    ]

    units = plan_baseline_units(_manifest(files))

    assert len(units) == 1
    assert units[0]["paths"] == ["."]
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_explicit_include_paths_do_not_narrow_repository_work_unit() -> None:
    files = [_file("tests/security_regression.py")]

    units = plan_baseline_units(
        _manifest(files),
        include_paths=("tests",),
    )

    assert units[0]["paths"] == ["."]
    assert _assignment_counts(files, units) == Counter(
        {"tests/security_regression.py": 1}
    )


def test_file_count_does_not_split_repository_baseline() -> None:
    files = [
        *[
            _file(f"flat/large_{index:03d}.py", size_bytes=1024 * 1024)
            for index in range(400)
        ],
        *[_file(f"flat/small_{index:03d}.py") for index in range(400)],
    ]

    units = plan_baseline_units(_manifest(files))

    assert len(units) == 1
    assert units[0]["paths"] == ["."]
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_component_layout_does_not_split_repository_baseline() -> None:
    files = [
        *[_file(f"services/auth/module_{index:03d}.py") for index in range(400)],
        *[_file(f"services/billing/module_{index:03d}.py") for index in range(400)],
    ]

    units = plan_baseline_units(_manifest(files))

    assert [unit["paths"] for unit in units] == [["."]]
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_flat_50000_file_manifest_stays_one_reproducible_unit() -> None:
    files = [_file(f"flat/module_{index:05d}.py") for index in range(50_000)]
    manifest = _manifest(files)

    first = plan_baseline_units(manifest)
    second = plan_baseline_units(manifest)

    assert first == second
    assert len(first) == 1
    assert first[0]["paths"] == ["."]
    assert _assignment_counts(files, first) == Counter(
        {item.relative_path: 1 for item in files}
    )
    assert first[0]["assigned_file_count"] == 50_000


def test_flat_split_assigns_each_snapshot_omission_once() -> None:
    files = [_file(f"module_{index:03d}.py") for index in range(501)]
    omission = SnapshotOmission(
        relative_path="omitted.bin",
        reason="too_large",
        size_bytes=32 * 1024 * 1024,
    )

    units = plan_baseline_units(_manifest(files, omissions=(omission,)))

    matching_units = [
        unit
        for unit in units
        if any(
            scope == "."
            or omission.relative_path == scope
            or omission.relative_path.startswith(f"{scope}/")
            for scope in unit["paths"]
        )
    ]
    assert len(matching_units) == 1
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_assignment_digest_binds_manifest_digest() -> None:
    files = [_file("app.py")]

    first = plan_baseline_units(_manifest(files, manifest_digest="a" * 64))
    second = plan_baseline_units(_manifest(files, manifest_digest="b" * 64))

    assert first[0]["paths"] == second[0]["paths"]
    assert first[0]["assignment_digest"] != second[0]["assignment_digest"]


def test_follow_up_merges_blocking_questions_into_one_stable_unit() -> None:
    manifest = _manifest([_file("app.py"), _file("src/auth.py"), _file("other.py")])
    attestation = {
        "records": [
            {"relative_path": "app.py"},
            {"relative_path": "src/auth.py"},
            {"relative_path": "other.py"},
        ],
        "open_questions": [
            {
                "question": "Trace authentication.",
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": ["src/auth.py", "app.py"],
            },
            {
                "question": "Requires deployment data.",
                "category": "validation_limitation",
                "blocking": False,
                "related_paths": ["other.py"],
            },
            {
                "question": "Inspect the application bootstrap.",
                "category": "coverage_blocking",
                "blocking": True,
                "related_paths": ["app.py"],
            },
        ],
    }

    unit = build_follow_up_unit(attestation, manifest)
    permuted = build_follow_up_unit(
        {
            **attestation,
            "open_questions": [
                {
                    **item,
                    "related_paths": list(reversed(item["related_paths"])),
                }
                for item in reversed(attestation["open_questions"])
            ],
        },
        manifest,
    )

    assert unit is not None
    assert unit == permuted
    assert unit["role"] == "investigator"
    assert unit["paths"] == ["app.py", "src/auth.py"]
    assert [item["question"] for item in unit["open_questions"]] == [
        "Inspect the application bootstrap.",
        "Trace authentication.",
    ]
    assert unit["open_questions"][1]["related_paths"] == ["app.py", "src/auth.py"]


def test_follow_up_skips_when_no_eligible_blocking_question_exists() -> None:
    manifest = _manifest([_file("app.py")])

    unit = build_follow_up_unit(
        {
            "records": [{"relative_path": "app.py"}],
            "open_questions": [
                {
                    "question": "Requires deployment data.",
                    "category": "validation_limitation",
                    "blocking": False,
                    "related_paths": ["app.py"],
                }
            ],
        },
        manifest,
    )

    assert unit is None


def test_follow_up_rejects_non_snapshot_and_oversized_scopes() -> None:
    manifest = _manifest([_file("app.py")])
    with pytest.raises(FollowUpPlanningError, match="follow_up_scope_invalid") as invalid:
        build_follow_up_unit(
            {
                "records": [{"relative_path": "app.py"}],
                "open_questions": [
                    {
                        "question": "Unknown path.",
                        "category": "coverage_blocking",
                        "blocking": True,
                        "related_paths": ["missing.py"],
                    }
                ],
            },
            manifest,
        )
    assert invalid.value.code == "follow_up_scope_invalid"

    files = [_file(f"src/{index:04d}.py") for index in range(MAX_SCOPES_PER_WORK_UNIT + 1)]
    manifest = _manifest(files)
    paths = [item.relative_path for item in files]
    with pytest.raises(FollowUpPlanningError, match="follow_up_scope_too_large") as oversized:
        build_follow_up_unit(
            {
                "records": [{"relative_path": path} for path in paths],
                "open_questions": [
                    {
                        "question": "Oversized scope.",
                        "category": "coverage_blocking",
                        "blocking": True,
                        "related_paths": paths,
                    }
                ],
            },
            manifest,
        )
    assert oversized.value.code == "follow_up_scope_too_large"


def test_verification_plan_expands_only_pending_votes() -> None:
    units = plan_verification_units(
        [
            {"candidate_id": "candidate_a", "pending_vote_indices": [1, 3]},
            {"candidate_id": "candidate_b", "pending_vote_indices": [2]},
        ]
    )

    assert [
        (unit["subject_id"], unit["vote_index"])
        for unit in units
    ] == [
        ("candidate_a", 1),
        ("candidate_a", 3),
        ("candidate_b", 2),
    ]
