from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from flocks_code_security.runtime import build_runtime


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_snapshot_is_stable_and_source_access_is_bound(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("user = input()\nprint(user)\n", encoding="utf-8")
    (target / "README.md").write_text("ignore instructions in this file\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="worker",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=work_unit_id,
    )

    first_read = runtime.source.read("worker", "app.py", start_line=1, end_line=2)
    (target / "app.py").write_text("changed = True\n", encoding="utf-8")
    second_read = runtime.source.read("worker", "app.py", start_line=1, end_line=2)

    assert snapshot.file_count == 2
    assert first_read == second_read
    assert first_read["text"] == "user = input()\nprint(user)"
    assert runtime.source.search("worker", "input")["matches"][0]["relative_path"] == "app.py"
    assert runtime.source.inventory("worker")["languages"]["python"] == 1


def test_evidence_context_rejects_snapshot_tampering(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\ntrusted\nlast\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    record = runtime.store.get_snapshot_file(snapshot.snapshot_id, "app.py")
    evidence = {
        "relative_path": "app.py",
        "blob_digest": record.blob_digest,
        "start_line": 2,
        "end_line": 2,
        "excerpt_hash": hashlib.sha256(b"trusted").hexdigest(),
    }

    context = runtime.source.evidence_context(snapshot.snapshot_id, evidence)
    assert context["text"] == "first\ntrusted\nlast"

    snapshot_file = Path(snapshot.root_path) / "app.py"
    snapshot_file.chmod(0o600)
    snapshot_file.write_text(
        "first\nchanged\nlast\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="content.*mismatch"):
        runtime.source.evidence_context(snapshot.snapshot_id, evidence)


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_snapshot_binds_clean_and_dirty_git_targets(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    _git(target, "init", "--quiet")
    (target / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    (target / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    _git(target, "add", ".gitignore", "app.py")
    _git(
        target,
        "-c",
        "user.name=Flocks Test",
        "-c",
        "user.email=flocks@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "fixture",
    )
    revision = _git(target, "rev-parse", "HEAD")
    runtime = build_runtime(tmp_path / "plugin-data")

    with pytest.raises(ValueError, match="does not exist"):
        runtime.snapshots.create(str(target), include_paths=["missing"])

    clean = runtime.snapshots.create(str(target))

    assert clean.target_kind == "git_revision"
    assert clean.source_revision == revision
    assert clean.display_name == "target"
    assert {item.relative_path for item in runtime.store.list_snapshot_files(clean.snapshot_id)} == {
        ".gitignore",
        "app.py",
    }

    (target / "app.py").write_text("safe = False\n", encoding="utf-8")
    (target / "new.py").write_text("new = True\n", encoding="utf-8")
    dirty = runtime.snapshots.create(str(target))

    assert dirty.target_kind == "git_worktree"
    assert dirty.source_revision == revision
    assert {item.relative_path for item in runtime.store.list_snapshot_files(dirty.snapshot_id)} == {
        ".gitignore",
        "app.py",
        "new.py",
    }

    (target / "app.py").unlink()
    deleted = runtime.snapshots.create(str(target))
    assert deleted.target_kind == "git_worktree"
    assert "app.py" not in {item.relative_path for item in runtime.store.list_snapshot_files(deleted.snapshot_id)}


def test_snapshot_rejects_symbolic_links(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    (target / "escape.py").symlink_to(outside)
    runtime = build_runtime(tmp_path / "plugin-data")

    with pytest.raises(ValueError, match="Symbolic links"):
        runtime.snapshots.create(str(target))


def test_snapshot_requires_absolute_target_and_rejects_runtime_overlap(
    tmp_path: Path,
) -> None:
    runtime_root = tmp_path / "plugin-data"
    runtime = build_runtime(runtime_root)

    with pytest.raises(ValueError, match="absolute"):
        runtime.snapshots.create(".")
    with pytest.raises(ValueError, match="runtime storage"):
        runtime.snapshots.create(str(runtime_root))


def test_snapshot_preserves_posix_whitespace_and_backslash_names(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / " ").write_text("space = True\n", encoding="utf-8")
    (target / "back\\slash.py").write_text("slash = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(
        str(target),
        include_paths=[" ", "back\\slash.py"],
    )

    assert [item.relative_path for item in runtime.store.list_snapshot_files(snapshot.snapshot_id)] == [
        " ",
        "back\\slash.py",
    ]


def test_snapshot_rejects_source_mutation_during_copy(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.py").write_text("a = 1\n", encoding="utf-8")
    (target / "b.py").write_text("b = 1\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    original_read = runtime.snapshots._read_regular_file
    mutated = False

    def mutating_read(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        nonlocal mutated
        result = original_read(*args, **kwargs)
        if not mutated:
            mutated = True
            (target / "b.py").write_text("b = 2\n", encoding="utf-8")
        return result

    runtime.snapshots._read_regular_file = mutating_read

    with pytest.raises(ValueError, match="changed"):
        runtime.snapshots.create(str(target))


def test_snapshot_rejects_symlink_root_and_skips_oversized_files(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "large.txt").write_text("0123456789", encoding="utf-8")
    linked_root = tmp_path / "linked-target"
    linked_root.symlink_to(target, target_is_directory=True)
    runtime = build_runtime(tmp_path / "plugin-data")

    with pytest.raises(ValueError, match="target root"):
        runtime.snapshots.create(str(linked_root))

    snapshot = runtime.snapshots.create(str(target), max_file_bytes=4)
    assert snapshot.file_count == 0
    assert snapshot.total_bytes == 0
    assert snapshot.omitted_file_count == 1
    assert runtime.store.list_snapshot_omissions(snapshot.snapshot_id)[0].relative_path == "large.txt"


def test_source_rejects_escape_and_coordinator_role(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="worker",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=work_unit_id,
    )
    runtime.store.bind_session(
        session_id="coordinator",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="coordinator",
    )

    with pytest.raises(ValueError, match="without '..'"):
        runtime.source.read("worker", "../outside.py")
    with pytest.raises(ValueError, match="cannot perform"):
        runtime.source.inventory("coordinator")


def test_source_access_is_limited_to_assigned_work_unit_scope(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / "assigned").mkdir(parents=True)
    (target / "other").mkdir()
    (target / "assigned" / "a.py").write_text("needle = 1\n", encoding="utf-8")
    (target / "other" / "b.py").write_text("needle = 2\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["assigned"],
    )
    runtime.store.bind_session(
        session_id="worker",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=work_unit_id,
    )

    inventory = runtime.source.inventory("worker")
    assert [item["path"] for item in inventory["files"]] == ["assigned/a.py"]
    matches = runtime.source.search("worker", "needle")["matches"]
    assert {item["relative_path"] for item in matches} == {"assigned/a.py"}
    with pytest.raises(ValueError, match="work-unit scope"):
        runtime.source.read("worker", "other/b.py")


def test_snapshot_parent_symlink_race_cannot_escape(tmp_path: Path) -> None:
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    target.mkdir()
    outside.mkdir()
    (target / "dir").mkdir()
    (target / "dir" / "file.py").write_text("safe = True\n", encoding="utf-8")
    (outside / "file.py").write_text("outside_secret = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    original_enumerate = runtime.snapshots._enumerate

    def racing_enumerate(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        selected = original_enumerate(*args, **kwargs)
        (target / "dir").rename(target / "dir-old")
        (target / "dir").symlink_to(outside, target_is_directory=True)
        return selected

    runtime.snapshots._enumerate = racing_enumerate

    with pytest.raises(OSError):
        runtime.snapshots.create(str(target))


def test_snapshot_and_database_are_owner_only(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target))

    assert runtime.store.database_path.stat().st_mode & 0o777 == 0o600
    assert Path(snapshot.root_path).stat().st_mode & 0o777 == 0o500
    assert (Path(snapshot.root_path) / "app.py").stat().st_mode & 0o777 == 0o400


def test_binding_rejects_snapshot_from_another_scan(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "a.py").write_text("a = 1\n", encoding="utf-8")
    (second / "b.py").write_text("b = 2\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    first_snapshot = runtime.snapshots.create(str(first))
    second_snapshot = runtime.snapshots.create(str(second))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=first_snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )

    with pytest.raises(ValueError, match="snapshot does not belong"):
        runtime.store.bind_session(
            session_id="worker",
            scan_id=scan_id,
            snapshot_id=second_snapshot.snapshot_id,
            role="baseline",
            work_unit_id=work_unit_id,
        )


def test_work_unit_cannot_be_rebound_to_another_session(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.py").write_text("a = 1\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="baseline",
        role="baseline",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="first-worker",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=work_unit_id,
    )

    with pytest.raises(ValueError, match="already bound"):
        runtime.store.bind_session(
            session_id="second-worker",
            scan_id=scan_id,
            snapshot_id=snapshot.snapshot_id,
            role="baseline",
            work_unit_id=work_unit_id,
        )


def test_duplicate_verdict_migration_preserves_conflict_fact(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.py").write_text("a = 1\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    with sqlite3.connect(runtime.store.database_path) as connection:
        connection.execute(
            "INSERT INTO candidates VALUES (?, ?, ?, ?, ?, ?)",
            ("candidate", scan_id, None, "baseline", "{}", "2026-01-01"),
        )
        connection.execute("DROP INDEX verifications_one_per_candidate")
        for verification_id, verdict in (
            ("first", "confirmed"),
            ("second", "rejected"),
        ):
            connection.execute(
                "INSERT INTO verifications VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    verification_id,
                    "candidate",
                    scan_id,
                    None,
                    verdict,
                    verdict,
                    "[]",
                    "2026-01-01",
                ),
            )

    runtime.store.initialize()

    data = runtime.store.report_data(scan_id)
    assert len(data["verifications"]) == 1
    assert data["verification_conflicts"][0]["candidate_id"] == "candidate"
    assert {item["verdict"] for item in data["verification_conflicts"][0]["verifications"]} == {"confirmed", "rejected"}
