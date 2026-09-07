from __future__ import annotations

from pathlib import Path

from flocks_code_security.models import SnapshotRef
from flocks_code_security.orchestration import plan_poc_units, poc_generator_prompt
from flocks_code_security.store import ScanStore, _ranges_cover


def test_poc_planning_is_candidate_bound_and_has_repository_context() -> None:
    candidates = [
        {
            "candidate_id": "candidate_1",
            "evidence": [
                {"relative_path": "src/parser.c"},
                {"relative_path": "src/parser.c"},
                {"relative_path": "include/parser.h"},
            ],
        }
    ]

    assert plan_poc_units(candidates) == [
        {
            "role": "poc_generator",
            "paths": ["."],
            "subject_id": "candidate_1",
        }
    ]


def test_poc_prompt_preserves_target_language_delivery_distinction() -> None:
    prompt = poc_generator_prompt(
        snapshot_id="snapshot_1",
        candidate_id="candidate_1",
        knowledge_base_present=True,
    )

    assert "audit_poc_subject" in prompt
    assert "audit_repository_summary" in prompt
    assert "audit_knowledge_base" in prompt
    assert "PoC language does not have to match the target language" in prompt
    assert "source_harness" in prompt
    assert "later dynamic-validation consumer" in prompt


def test_poc_evidence_read_ranges_may_be_covered_in_chunks() -> None:
    assert _ranges_cover([(1, 400), (401, 500)], 1, 500)
    assert not _ranges_cover([(1, 400), (402, 500)], 1, 500)


def test_store_poc_flag_and_empty_generation_queue(tmp_path: Path) -> None:
    store = ScanStore(tmp_path / "audit.db")
    store.initialize()
    root = tmp_path / "snapshot"
    root.mkdir()
    store.save_snapshot(
        SnapshotRef(
            snapshot_id="snapshot_1",
            repository_identity="repo",
            source_revision=None,
            tree_digest="a" * 64,
            scope_digest="b" * 64,
            file_count=0,
            total_bytes=0,
            created_at="2026-09-07T00:00:00+00:00",
            root_path=str(root),
        ),
        [],
    )
    scan_id = store.create_scan(
        parent_session_id="session_1",
        snapshot_id="snapshot_1",
        mode="standard",
        ruleset_digest="rules",
        poc_enabled=True,
    )

    assert store.get_scan(scan_id)["poc_enabled"] is True
    assert store.list_confirmed_without_poc_record(scan_id) == []
