"""Filesystem State v1 and Mission behavior."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from flocks.memory.state.mission import MissionStore, mission_state_path_error


@pytest.fixture
def state_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "project"
    workspace.mkdir()
    return workspace


def test_mission_round_trip_and_zenith_state_shape(
    state_workspace: Path,
) -> None:
    store = MissionStore(state_workspace)
    state = store.create(
        mission_id="mission-test",
        session_id="session-test",
        original_request="Implement the feature.",
        todos=[
            {"id": "inspect", "content": "Inspect the code", "status": "completed"},
            {"id": "build", "content": "Implement the feature", "status": "in_progress"},
            {"id": "verify", "content": "Verify with tests", "status": "pending"},
        ],
    )

    loaded = store.load("mission-test")
    mission_text = store.mission_path("mission-test").read_text(encoding="utf-8")

    assert state["meta"]["source_session_id"] == "session-test"
    assert [task["type"] for task in loaded["tasks"]] == [
        "work",
        "work",
        "validate",
    ]
    assert loaded["tasks"][0]["status"] == "cleared"
    assert loaded["tasks"][1]["status"] == "running"
    assert "# Contract" in mission_text
    assert "# Attention" in mission_text
    assert "# Closeout" in mission_text
    context = store.render_hot_context("mission-test")
    assert "Mission ID: `mission-test`" in context
    assert "`mission.md` is private to the main Agent" in context
    assert "share `progress.md`, `findings.md`, and `artifacts/`" in context


def test_running_transition_appends_attempt_record(
    state_workspace: Path,
) -> None:
    store = MissionStore(state_workspace)
    store.create(
        mission_id="mission-attempt",
        session_id="session-attempt",
        original_request="Run the planned work.",
        todos=[
            {"id": "one", "content": "First task", "status": "pending"},
            {"id": "two", "content": "Second task", "status": "pending"},
            {"id": "verify", "content": "Verify output", "status": "pending"},
        ],
    )

    store.sync_todos(
        "mission-attempt",
        [
            {"id": "one", "content": "First task", "status": "in_progress"},
            {"id": "two", "content": "Second task", "status": "pending"},
            {"id": "verify", "content": "Verify output", "status": "pending"},
        ],
        session_id="session-attempt",
    )

    progress = store._read_progress_entries("mission-attempt")
    attempts = [
        item
        for item in progress
        if item.get("task_id") == "one" and item.get("status") == "running"
    ]
    assert len(attempts) == 1


def test_completion_requires_validation_and_resolves_after_evidence(
    state_workspace: Path,
) -> None:
    store = MissionStore(state_workspace)
    store.create(
        mission_id="mission-gate",
        session_id="session-gate",
        original_request="Finish all work.",
        todos=[
            {"id": "one", "content": "First task", "status": "pending"},
            {"id": "two", "content": "Second task", "status": "pending"},
            {"id": "verify", "content": "Verify output", "status": "pending"},
        ],
    )

    gate = store.sync_todos(
        "mission-gate",
        [
            {"id": "one", "content": "First task", "status": "completed"},
            {"id": "two", "content": "Second task", "status": "completed"},
            {"id": "verify", "content": "Verify output", "status": "completed"},
        ],
        session_id="session-gate",
    )
    assert gate["completed"] is False
    assert any("validation" in gap.lower() for gap in gate["gaps"])

    store.record(
        "mission-gate",
        session_id="session-gate",
        kind="validation",
        summary="Independent checks passed",
        status="passed",
        source_refs=["test-output"],
    )
    completed = store.evaluate_completion(
        "mission-gate",
        session_id="session-gate",
    )

    assert completed["completed"] is True
    assert completed["state"]["meta"]["status"] == "completed"
    assert completed["state"]["closeout"]


def test_artifact_is_versioned_and_hash_verified(
    state_workspace: Path,
) -> None:
    source = state_workspace / "scan.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    store = MissionStore(state_workspace)
    store.create(
        mission_id="mission-artifact",
        session_id="session-artifact",
        original_request="Collect evidence.",
        todos=[
            {"id": "one", "content": "Collect", "status": "pending"},
            {"id": "two", "content": "Analyze", "status": "pending"},
            {"id": "verify", "content": "Verify evidence", "status": "pending"},
        ],
    )

    result = store.record(
        "mission-artifact",
        session_id="session-artifact",
        kind="artifact",
        summary="Scanner output",
        artifact_path=str(source),
    )
    stored = store.mission_dir("mission-artifact") / result["artifact_path"]

    assert stored.read_text(encoding="utf-8") == '{"ok": true}'
    assert hashlib.sha256(stored.read_bytes()).hexdigest() in (
        store.mission_dir("mission-artifact")
        / "artifacts"
        / "INDEX.md"
    ).read_text(encoding="utf-8")


def test_mission_paths_are_protected(
    state_workspace: Path,
) -> None:
    protected = state_workspace / ".flocks" / "missions" / "m1" / "mission.md"
    allowed = state_workspace / ".flocks" / "MEMORY.md"

    assert mission_state_path_error(protected, state_workspace)
    assert mission_state_path_error(allowed, state_workspace) is None


def test_concurrent_progress_records_receive_unique_sequences(
    state_workspace: Path,
) -> None:
    store = MissionStore(state_workspace)
    store.create(
        mission_id="mission-concurrent",
        session_id="session-concurrent",
        original_request="Record concurrent work.",
        todos=[
            {"id": "one", "content": "First task", "status": "pending"},
            {"id": "two", "content": "Second task", "status": "pending"},
            {"id": "verify", "content": "Verify output", "status": "pending"},
        ],
    )

    def record(index: int) -> int:
        result = store.record(
            "mission-concurrent",
            session_id=f"session-{index}",
            kind="progress",
            summary=f"Attempt {index}",
        )
        return int(result["sequence"])

    with ThreadPoolExecutor(max_workers=5) as pool:
        sequences = list(pool.map(record, range(10)))

    assert len(sequences) == len(set(sequences))
    assert sorted(sequences) == list(range(2, 12))
