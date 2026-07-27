"""Memory-State v1 filesystem and Mission behavior."""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from flocks.config import Config
from flocks.memory.bootstrap import MemoryBootstrap
from flocks.memory.mission import MissionStore, mission_state_path_error


@pytest.fixture
def isolated_memory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[Path, Path]:
    root = tmp_path / "flocks-home"
    workspace = tmp_path / "project"
    workspace.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    Config._global_config = None
    Config.clear_cache()
    return root, workspace


@pytest.mark.asyncio
async def test_bootstrap_injects_stable_memory_but_not_daily(
    isolated_memory: tuple[Path, Path],
) -> None:
    root, workspace = isolated_memory
    bootstrap = MemoryBootstrap(workspace_dir=workspace)
    await bootstrap.create_memory_structure()
    (root / "memory" / "MEMORY.md").write_text("global-rule", encoding="utf-8")
    (root / "memory" / "USER.md").write_text("user-profile", encoding="utf-8")
    (root / "memory" / "daily" / "2026-07-27.md").write_text(
        "daily-secret",
        encoding="utf-8",
    )
    (workspace / ".flocks" / "memory" / "MEMORY.md").write_text(
        "project-rule",
        encoding="utf-8",
    )

    snapshot = await bootstrap.bootstrap()
    content = "\n".join(item["content"] for item in snapshot["memory_files"])

    assert "global-rule" in content
    assert "user-profile" in content
    assert "project-rule" in content
    assert "daily-secret" not in content
    assert snapshot["daily_memories"] == []


def test_mission_round_trip_and_zenith_state_shape(
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    store = MissionStore(workspace)
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


def test_running_transition_appends_attempt_record(
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    store = MissionStore(workspace)
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
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    store = MissionStore(workspace)
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
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    source = workspace / "scan.json"
    source.write_text('{"ok": true}', encoding="utf-8")
    store = MissionStore(workspace)
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
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    protected = workspace / ".flocks" / "memory" / "missions" / "m1" / "mission.md"
    allowed = workspace / ".flocks" / "memory" / "MEMORY.md"

    assert mission_state_path_error(protected, workspace)
    assert mission_state_path_error(allowed, workspace) is None


def test_concurrent_progress_records_receive_unique_sequences(
    isolated_memory: tuple[Path, Path],
) -> None:
    _, workspace = isolated_memory
    store = MissionStore(workspace)
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
