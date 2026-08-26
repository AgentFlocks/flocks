from __future__ import annotations

from collections import Counter

from flocks_code_security.models import (
    RepositoryManifest,
    SnapshotFile,
    SnapshotOmission,
)
from flocks_code_security.orchestration import (
    MAX_SCOPES_PER_WORK_UNIT,
    MAX_WORK_UNITS_PER_BATCH,
    baseline_focus_exclusions,
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


def test_large_files_do_not_create_extra_baseline_workers() -> None:
    files = [
        _file(f"large/module_{index:02d}.py", size_bytes=20 * 1024 * 1024)
        for index in range(8)
    ]

    units = plan_baseline_units(_manifest(files))

    assert len(units) == 1
    assert units[0]["paths"] == ["."]
    assert units[0]["assigned_bytes"] == 160 * 1024 * 1024


def test_default_baseline_focus_excludes_noise_without_broadening_worker_scope() -> None:
    files = [
        _file("app.py"),
        _file("src/auth.py"),
        _file("src/tests/test_auth.py"),
        _file("docs/security.md"),
        _file("assets/logo.svg"),
        _file("assets/logo.SVG"),
    ]

    units = plan_baseline_units(_manifest(files))

    assert _assignment_counts(files, units) == Counter(
        {"app.py": 1, "assets/logo.SVG": 1, "src/auth.py": 1}
    )
    assert all("." not in unit["paths"] for unit in units)
    assert baseline_focus_exclusions(
        [item.relative_path for item in files],
        include_paths=(".",),
    ) == {
        "*.svg": "Excluded from baseline by the default production-source focus",
        "docs": "Excluded from baseline by the default production-source focus",
        "src/tests": "Excluded from baseline by the default production-source focus",
    }


def test_explicit_include_paths_disable_default_baseline_focus() -> None:
    files = [_file("tests/security_regression.py")]

    units = plan_baseline_units(
        _manifest(files),
        include_paths=("tests",),
    )

    assert units[0]["paths"] == ["."]
    assert _assignment_counts(files, units) == Counter(
        {"tests/security_regression.py": 1}
    )
    assert baseline_focus_exclusions(
        [item.relative_path for item in files],
        include_paths=("tests",),
    ) == {}


def test_file_count_split_still_balances_large_files_by_bytes() -> None:
    files = [
        *[
            _file(f"flat/large_{index:03d}.py", size_bytes=1024 * 1024)
            for index in range(400)
        ],
        *[_file(f"flat/small_{index:03d}.py") for index in range(400)],
    ]

    units = plan_baseline_units(_manifest(files))

    assert len(units) == 2
    assigned_bytes = [unit["assigned_bytes"] for unit in units]
    assert max(assigned_bytes) - min(assigned_bytes) <= 1024 * 1024
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_overweight_component_is_recursively_split_into_child_prefixes() -> None:
    files = [
        *[_file(f"services/auth/module_{index:03d}.py") for index in range(400)],
        *[_file(f"services/billing/module_{index:03d}.py") for index in range(400)],
    ]

    units = plan_baseline_units(_manifest(files))

    assert {tuple(unit["paths"]) for unit in units} == {
        ("services/auth",),
        ("services/billing",),
    }
    assert _assignment_counts(files, units) == Counter(
        {item.relative_path: 1 for item in files}
    )


def test_flat_50000_file_manifest_is_exact_balanced_and_reproducible() -> None:
    files = [_file(f"flat/module_{index:05d}.py") for index in range(50_000)]
    manifest = _manifest(files)

    first = plan_baseline_units(manifest)
    second = plan_baseline_units(manifest)

    assert first == second
    assert len(first) == MAX_WORK_UNITS_PER_BATCH
    assert all(1 <= len(unit["paths"]) <= MAX_SCOPES_PER_WORK_UNIT for unit in first)
    assert _assignment_counts(files, first) == Counter(
        {item.relative_path: 1 for item in files}
    )
    file_counts = [unit["assigned_file_count"] for unit in first]
    assert max(file_counts) - min(file_counts) <= 1


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
