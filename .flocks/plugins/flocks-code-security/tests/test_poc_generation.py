from __future__ import annotations

from pathlib import Path

from flocks_code_security.models import SnapshotRef
from flocks_code_security.orchestration import plan_poc_units, poc_generator_prompt
from flocks_code_security.store import ScanStore


def test_poc_planning_is_candidate_bound_and_source_scoped() -> None:
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
            "paths": ["include/parser.h", "src/parser.c"],
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
