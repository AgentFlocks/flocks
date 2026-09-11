from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import flocks_code_security.snapshot as snapshot_module
from flocks_code_security.coverage import (
    CoverageAttestationService,
    CoverageSubmissionError,
)
from flocks_code_security.models import SnapshotRef
from flocks_code_security.runtime import build_runtime


def _git(repository: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.mark.parametrize("chunk_size", [1, 2, 3, 7, 64])
def test_bulk_line_counter_preserves_splitlines_semantics(chunk_size: int) -> None:
    for text in ("", "x", "\r\n", "\r\r\n", "one\r\ntwo\rthree\n", "\v\f\x1c\x1d\x1e\x85\u2028\u2029", "\nlast"):
        counter = snapshot_module._DecodedLineCounter()
        for offset in range(0, len(text), chunk_size):
            counter.feed(text[offset:offset + chunk_size])
            counter.feed("")
        assert counter.value == len(text.splitlines())


def test_snapshot_total_limit_is_checked_before_reading_content(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "first.c").write_bytes(b"1234")
    (target / "second.c").write_bytes(b"5678")
    runtime = build_runtime(tmp_path / "plugin-data")

    def unexpected_read(*args, **kwargs):
        pytest.fail("over-budget snapshot must fail before hashing any content")

    monkeypatch.setattr(runtime.snapshots, "_read_regular_file", unexpected_read)
    with pytest.raises(ValueError, match="max_total_bytes=7"):
        runtime.snapshots.create(str(target), max_total_bytes=7)


def test_wireshark_sized_direct_snapshot_can_create_a_scan(tmp_path: Path) -> None:
    target = tmp_path / "wireshark-sized"
    source_dir = target / "epan" / "dissectors"
    source_dir.mkdir(parents=True)
    for index in range(12_000):
        (source_dir / f"packet-{index}.c").write_bytes(b"int parse(void) { return 0; }\n")
    # Sparse files reproduce the reported sizes without allocating 1.6 GiB on disk.
    # The snapshot still reads and hashes every byte through the real I/O path.
    for filename, mib in (("libwireshark.a", 443), ("libwiretap.a", 408), ("capture.bin", 749)):
        with (target / filename).open("wb") as output:
            output.write(b"!<arch>\n\0")
            output.truncate(mib * 1024**2)
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target), copy_source=False)
    assert snapshot.file_count == 12_003
    assert snapshot.total_bytes > 1_600 * 1024**2
    assert snapshot.omitted_file_count == 0
    assert Path(snapshot.root_path) == target
    assert not (runtime.snapshots.snapshots_root / snapshot.snapshot_id).exists()
    scan_id = runtime.store.create_scan(
        parent_session_id="large-repository", snapshot_id=snapshot.snapshot_id,
        mode="standard", ruleset_digest="rules",
    )
    assert runtime.store.get_scan(scan_id)["status"] == "running"


def test_explicit_total_limit_and_file_omissions(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.c").write_bytes(b"1234")
    (target / "large.a").write_bytes(b"x" * 20)
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target), max_file_bytes=4, max_total_bytes=4)
    assert snapshot.total_bytes == 4
    assert snapshot.omitted_file_count == 1
    assert runtime.store.list_snapshot_omissions(snapshot.snapshot_id)[0].relative_path == "large.a"


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
    assert runtime.store.report_data(scan_id)["source_access_counts"] == {
        work_unit_id: {"inventory": 2, "read": 2, "search": 1}
    }


def test_snapshot_preserves_non_production_files_for_nonbaseline_phases(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    for directory in (
        ".github/workflows",
        "assets",
        "ci",
        "debian",
        "docs",
        "examples",
        "fuzzers",
        "packaging",
        "po",
        "project/tests",
        "src",
    ):
        (target / directory).mkdir(parents=True, exist_ok=True)
    files = {
        ".github/workflows/ci.yml": "name: ci\n",
        ".projection-complete": "done\n",
        "assets/logo.svg": "<svg/>\n",
        "assets/runtime.js": "export const enabled = true;\n",
        "ci/check.sh": "exit 0\n",
        "debian/rules": "build:\n",
        "docs/guide.md": "guide\n",
        "examples/demo.c": "int demo(void) { return 0; }\n",
        "fuzzers/fuzz.c": "int fuzz(void) { return 0; }\n",
        "packaging/build.sh": "exit 0\n",
        "po/fr.po": "msgid \"hello\"\n",
        "project/tests/test_app.c": "int test(void) { return 0; }\n",
        "security.xml": "<security enabled=\"true\"/>\n",
        "src/app.c": "int main(void) { return 0; }\n",
    }
    for relative_path, content in files.items():
        (target / relative_path).write_text(content, encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target))

    assert {
        item.relative_path
        for item in runtime.store.list_snapshot_files(snapshot.snapshot_id)
    } == set(files) - {".projection-complete"}
    assert snapshot.omitted_file_count == 0
    assert runtime.store.list_snapshot_omissions(snapshot.snapshot_id) == []


def test_explicit_snapshot_scope_can_select_a_test_tree(tmp_path: Path) -> None:
    target = tmp_path / "target"
    (target / "tests").mkdir(parents=True)
    (target / "tests" / "security_regression.py").write_text(
        "dangerous = True\n",
        encoding="utf-8",
    )
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target), include_paths=["tests"])

    assert snapshot.file_count == 1
    assert {
        item.relative_path
        for item in runtime.store.list_snapshot_files(snapshot.snapshot_id)
    } == {"tests/security_regression.py"}


def test_search_records_only_unique_matching_files(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "a.py").write_text("needle = 1\nneedle = 2\n", encoding="utf-8")
    (target / "b.py").write_text("safe = True\nneedle = 3\n", encoding="utf-8")
    (target / "unmatched.py").write_text("safe = True\n", encoding="utf-8")
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
    binding = runtime.store.require_binding("worker", {"baseline"})

    assert runtime.source.search("worker", "absent")["matches"] == []
    assert runtime.store.list_source_accesses(binding.attempt_id) == []

    result = runtime.source.search("worker", "needle")
    assert len(result["matches"]) == 3
    accesses = runtime.store.list_source_accesses(binding.attempt_id)
    assert {item["relative_path"] for item in accesses} == {"a.py", "b.py"}

    with pytest.raises(CoverageSubmissionError) as exc_info:
        CoverageAttestationService(runtime.store).attest(
            binding,
            dispositions=[
                {"path": "a.py", "claim": "analyzed"},
                {"path": "unmatched.py", "claim": "analyzed"},
            ],
        )
    assert {
        item["path"]: item["actual_state"]
        for item in exc_info.value.violations
    } == {
        "a.py": "located",
        "unmatched.py": "unexamined",
    }

    limited = runtime.source.search("worker", "needle", max_results=1)
    assert len(limited["matches"]) == 1
    assert len(runtime.store.list_source_accesses(binding.attempt_id)) == len(accesses) + 1


def test_direct_source_audit_reads_target_without_copy_and_never_deletes_it(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source_file = target / "app.py"
    source_file.write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target), copy_source=False)
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

    assert snapshot.copy_source is False
    assert snapshot.target_kind == "directory_snapshot"
    assert snapshot.root_path == str(target.resolve())
    assert snapshot.public_dict()["copy_source"] is False
    assert not (runtime.snapshots.snapshots_root / snapshot.snapshot_id).exists()
    assert runtime.source.read("worker", "app.py")["text"] == "safe = True"

    source_file.write_text("safe = None\n", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        runtime.source.read("worker", "app.py")

    runtime.store.delete_scan(scan_id)
    runtime.snapshots.delete(snapshot.snapshot_id)
    assert target.is_dir()
    assert source_file.read_text(encoding="utf-8") == "safe = None\n"
    assert runtime.store.get_snapshot(snapshot.snapshot_id) is None


def test_snapshot_delete_refuses_to_remove_a_root_outside_owned_storage(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source_file = target / "app.py"
    source_file.write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    runtime.store.save_snapshot(
        SnapshotRef(
            snapshot_id="snapshot_unsafe",
            repository_identity="repository-unsafe",
            source_revision=None,
            tree_digest="a" * 64,
            scope_digest="b" * 64,
            file_count=1,
            total_bytes=12,
            created_at="2026-08-21T00:00:00+00:00",
            root_path=str(target),
            copy_source=True,
        ),
        [],
    )

    with pytest.raises(OSError, match="outside owned storage"):
        runtime.snapshots.delete("snapshot_unsafe")

    assert target.is_dir()
    assert source_file.read_text(encoding="utf-8") == "safe = True\n"
    assert runtime.store.get_snapshot("snapshot_unsafe") is not None


def test_snapshot_delete_removes_an_owned_copy_only(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    source_file = target / "app.py"
    source_file.write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    snapshot_root = Path(snapshot.root_path)

    runtime.snapshots.delete(snapshot.snapshot_id)

    assert not snapshot_root.exists()
    assert source_file.read_text(encoding="utf-8") == "safe = True\n"
    assert runtime.store.get_snapshot(snapshot.snapshot_id) is None


def test_legacy_snapshots_migrate_to_copied_source_mode(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))
    with sqlite3.connect(runtime.store.database_path) as connection:
        connection.execute("ALTER TABLE snapshots DROP COLUMN copy_source")
        connection.execute("PRAGMA user_version = 0")

    runtime.store.initialize()

    migrated = runtime.store.get_snapshot(snapshot.snapshot_id)
    assert migrated is not None
    assert migrated.copy_source is True


def test_fresh_attempt_does_not_inherit_source_receipts(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("first\nsecond\n", encoding="utf-8")
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
        session_id="worker-1",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="baseline",
        work_unit_id=work_unit_id,
    )
    first_binding = runtime.store.require_binding("worker-1", {"baseline"})
    runtime.source.inventory("worker-1")
    runtime.source.read("worker-1", "app.py", start_line=1, end_line=2)
    runtime.store.finish_work_attempt(
        first_binding.attempt_id,
        status="failed",
        failure_class="agent_exited_no_facts",
    )

    second_attempt = runtime.store.create_work_attempt(
        work_unit_id=work_unit_id,
        session_id="worker-2",
        agent_name="code-security-baseline",
    )
    second_binding = runtime.store.require_binding("worker-2", {"baseline"})

    assert second_attempt["ordinal"] == 2
    assert second_binding.attempt_id == second_attempt["attempt_id"]
    assert runtime.store.list_source_accesses(second_binding.attempt_id) == []
    with runtime.store._connect() as connection:
        attempt_ids = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT attempt_id FROM source_access WHERE work_unit_id = ?",
                (work_unit_id,),
            ).fetchall()
        }
    assert attempt_ids == {first_binding.attempt_id}


def test_repository_manifest_is_stable_and_source_views_are_digest_bound(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    (target / "services" / "auth").mkdir(parents=True)
    (target / "services" / "auth" / "app.py").write_text(
        "safe = True\n",
        encoding="utf-8",
    )
    (target / "README.md").write_text("documentation\n", encoding="utf-8")
    runtime = build_runtime(tmp_path / "plugin-data")
    snapshot = runtime.snapshots.create(str(target))

    first = runtime.manifests.get_or_build(snapshot.snapshot_id)
    second = runtime.manifests.get_or_build(snapshot.snapshot_id)

    assert first.manifest_id == second.manifest_id
    assert first.manifest_digest == second.manifest_digest
    assert first.file_count == 2
    assert dict(first.languages) == {"other": 1, "python": 1}
    assert first.components[0].path == "."

    scan_id = runtime.store.create_scan(
        parent_session_id="coordinator",
        snapshot_id=snapshot.snapshot_id,
        mode="standard",
        ruleset_digest="rules",
    )
    work_unit_id = runtime.store.create_work_unit(
        scan_id=scan_id,
        phase="threat_modeling",
        role="threat_modeler",
        paths=["."],
    )
    runtime.store.bind_session(
        session_id="modeler",
        scan_id=scan_id,
        snapshot_id=snapshot.snapshot_id,
        role="threat_modeler",
        work_unit_id=work_unit_id,
    )

    binding = runtime.store.require_binding("modeler", {"threat_modeler"})
    runtime.store.record_source_access(
        binding,
        operation="repository_summary",
        relative_path=".",
        blob_digest=snapshot.tree_digest,
    )
    with pytest.raises(ValueError, match="Canonical repository summary"):
        runtime.store.require_repository_summary_consumed(binding)

    summary = runtime.source.repository_summary("modeler")
    inventory = runtime.source.inventory("modeler")
    runtime.store.require_repository_summary_consumed(binding)

    assert summary["manifest_id"] == first.manifest_id
    assert summary["manifest_digest"] == first.manifest_digest
    assert inventory["manifest_id"] == first.manifest_id
    assert inventory["manifest_digest"] == first.manifest_digest
    with runtime.store._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM repository_manifests WHERE snapshot_id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM manifest_access WHERE work_unit_id = ?",
            (work_unit_id,),
        ).fetchone()[0] == 1


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
    (target / "tests").mkdir()
    (target / ".gitignore").write_text("ignored.py\n", encoding="utf-8")
    (target / ".projection-complete").write_text("done\n", encoding="utf-8")
    (target / "app.py").write_text("safe = True\n", encoding="utf-8")
    (target / "ignored.py").write_text("ignored = True\n", encoding="utf-8")
    (target / "tests" / "test_app.py").write_text(
        "def test_app(): pass\n", encoding="utf-8"
    )
    _git(target, "add", ".gitignore", ".projection-complete", "app.py", "tests")
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
        "tests/test_app.py",
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
        "tests/test_app.py",
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


def test_snapshot_default_streams_files_larger_than_the_legacy_one_mib_cap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    contents = b"a" * (1_048_576 + 1)
    (target / "packet-ieee80211.c").write_bytes(contents)
    runtime = build_runtime(tmp_path / "plugin-data")
    monkeypatch.setattr(
        Path,
        "write_bytes",
        lambda *_args, **_kwargs: pytest.fail(
            "snapshot copies must not buffer a complete file through Path.write_bytes"
        ),
    )

    snapshot = runtime.snapshots.create(str(target))

    assert snapshot.file_count == 1
    assert snapshot.total_bytes == len(contents)
    assert snapshot.omitted_file_count == 0
    snapshot_file = runtime.store.list_snapshot_files(snapshot.snapshot_id)[0]
    assert snapshot_file.relative_path == "packet-ieee80211.c"
    assert snapshot_file.blob_digest == hashlib.sha256(contents).hexdigest()
    assert snapshot_file.line_count == 1
    assert (Path(snapshot.root_path) / snapshot_file.relative_path).read_bytes() == contents


def test_snapshot_binary_files_skip_text_line_decoding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target"
    target.mkdir()
    contents = b"\x00" + b"a" * (1024 * 1024)
    (target / "blob.bin").write_bytes(contents)

    class Decoder:
        def decode(self, *_args, **_kwargs):
            pytest.fail("binary snapshot should not decode text lines")

    monkeypatch.setattr(
        snapshot_module.codecs,
        "getincrementaldecoder",
        lambda _encoding: lambda **_kwargs: Decoder(),
    )
    runtime = build_runtime(tmp_path / "plugin-data")

    snapshot = runtime.snapshots.create(str(target))

    assert snapshot.file_count == 1
    snapshot_file = runtime.store.list_snapshot_files(snapshot.snapshot_id)[0]
    assert snapshot_file.is_binary is True
    assert snapshot_file.line_count == 0
    assert snapshot_file.blob_digest == hashlib.sha256(contents).hexdigest()


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
        connection.execute("PRAGMA user_version = 0")

    runtime.store.initialize()

    data = runtime.store.report_data(scan_id)
    assert len(data["verifications"]) == 1
    assert data["verification_conflicts"][0]["candidate_id"] == "candidate"
    assert {item["verdict"] for item in data["verification_conflicts"][0]["verifications"]} == {"confirmed", "rejected"}


@pytest.mark.parametrize("copy_source", [False, True])
def test_writable_source_copy_contains_only_verified_manifest_files(tmp_path, copy_source):
    target = tmp_path / "target"
    target.mkdir()
    (target / "app.py").write_text("original")
    (target / "excluded.bin").write_bytes(b"x" * 100)
    runtime = build_runtime(tmp_path / "data")
    snapshot = runtime.snapshots.create(target, copy_source=copy_source, max_file_bytes=20)
    destination = tmp_path / "working"
    runtime.source.copy_to(snapshot.snapshot_id, destination)
    assert sorted(p.name for p in destination.iterdir()) == ["app.py"]
    (destination / "app.py").write_text("modified")
    assert (target / "app.py").read_text() == "original"
    with pytest.raises(FileExistsError):
        runtime.source.copy_to(snapshot.snapshot_id, destination)
    assert (destination / "app.py").read_text() == "modified"
    if not copy_source:
        (target / "app.py").write_text("tampered")
        with pytest.raises(ValueError):
            runtime.source.copy_to(snapshot.snapshot_id, tmp_path / "failed")
        assert not (tmp_path / "failed").exists()


@pytest.mark.parametrize("copy_source", [False, True])
def test_working_copy_streams_content_and_preserves_executable_mode(tmp_path, monkeypatch, copy_source):
    target = tmp_path / "target"
    target.mkdir()
    script = target / "build.sh"
    script.write_text("#!/bin/sh\nprintf 'build-ok'\n")
    script.chmod(0o755)
    (target / "data").write_bytes(b"x" * (256 * 1024))
    runtime = build_runtime(tmp_path / "plugin")
    snapshot = runtime.snapshots.create(target, copy_source=copy_source)
    assert runtime.store.get_snapshot_file(snapshot.snapshot_id, "build.sh").executable_mode == 0o111
    restored = build_runtime(tmp_path / "plugin")
    monkeypatch.setattr(restored.source, "_verified_bytes", lambda *args: pytest.fail("copy must stream"))
    chunks = []
    original = restored.source._verified_chunks

    def streamed(*args):
        for chunk in original(*args):
            chunks.append(len(chunk))
            yield chunk

    monkeypatch.setattr(restored.source, "_verified_chunks", streamed)
    destination = tmp_path / "working"
    restored.source.copy_to(snapshot.snapshot_id, destination)
    assert max(chunks) <= 64 * 1024
    assert (destination / "build.sh").stat().st_mode & 0o111 == 0o111
    assert not (destination / "data").stat().st_mode & 0o111
    assert subprocess.run([str(destination / "build.sh")], check=True, capture_output=True).stdout == b"build-ok"


def test_snapshot_executable_metadata_migration_and_identity(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    script = target / "script.sh"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o644)
    runtime = build_runtime(tmp_path / "plugin")
    plain = runtime.snapshots.create(target)
    script.chmod(0o755)
    executable = runtime.snapshots.create(target)
    assert executable.tree_digest != plain.tree_digest
    with runtime.store._connect() as connection:
        connection.execute("ALTER TABLE snapshot_files DROP COLUMN executable_mode")
        connection.execute("ALTER TABLE scans DROP COLUMN bash_enabled")
        connection.execute("ALTER TABLE scans DROP COLUMN web_search_enabled")
        connection.execute("PRAGMA user_version = 8")
    restored = build_runtime(tmp_path / "plugin")
    # Old snapshots have no reliable record of the original execution mode.
    assert restored.store.get_snapshot_file(plain.snapshot_id, "script.sh").executable_mode == 0
    with restored.store._connect() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(scans)")}
        assert {"bash_enabled", "web_search_enabled"} <= columns
    new = restored.snapshots.create(target)
    assert restored.store.get_snapshot_file(new.snapshot_id, "script.sh").executable_mode == 0o111
