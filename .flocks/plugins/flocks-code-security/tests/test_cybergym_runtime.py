from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from flocks_code_security.cybergym_runtime import (
    CommandResult,
    CyberGymManifestError,
    CyberGymRuntime,
    CyberGymTargetManifest,
)
from flocks_code_security.models import SnapshotRef
from flocks_code_security.store import ScanStore


def _manifest(*, fuzzer_supported: bool = True) -> dict:
    return {
        "task_id": "fixture-level1",
        "task_kind": "other",
        "vulnerable_runner": "fixture:latest",
        "target_binary": "/opt/fixture-target",
        "argv_template": ["--input", "{input}"],
        "input_path": "/cybergym/input",
        "allow_empty_input": False,
        "fuzzer_supported": fuzzer_supported,
        "fuzzer_target": "/opt/fixture-fuzzer" if fuzzer_supported else None,
        "gdb_supported": True,
        "limits": {
            "replay_seconds": 5,
            "gdb_seconds": 5,
            "fuzz_seconds": 5,
            "max_artifact_bytes": 4096,
            "max_replay_runs": 8,
            "max_gdb_runs": 8,
            "max_fuzz_runs": 2,
            "max_minimize_runs": 2,
        },
    }


def _store(tmp_path: Path) -> tuple[ScanStore, str]:
    store = ScanStore(tmp_path / "audit.db")
    store.initialize()
    snapshot_root = tmp_path / "snapshot"
    snapshot_root.mkdir()
    store.save_snapshot(
        SnapshotRef(
            snapshot_id="snapshot_cybergym",
            repository_identity="fixture",
            source_revision=None,
            tree_digest="a" * 64,
            scope_digest="b" * 64,
            file_count=0,
            total_bytes=0,
            created_at="2026-09-01T00:00:00+00:00",
            root_path=str(snapshot_root),
        ),
        [],
    )
    scan_id = store.create_scan(
        parent_session_id="session-cybergym",
        snapshot_id="snapshot_cybergym",
        mode="cybergym_level1",
        ruleset_digest="rules",
    )
    store.create_cybergym_task(scan_id, _manifest())
    return store, scan_id


class _FixtureExecutor:
    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
        self.commands.append(command)
        mount = next(item for item in command if item.startswith("type=bind,src="))
        scratch = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
        if "gdb" in command:
            return CommandResult(0, "Breakpoint 1, fixture\nBreakpoint 2, fixture", "")
        if any(item.startswith("-exact_artifact_path=") for item in command):
            (scratch / "minimized").write_bytes(b"min")
            return CommandResult(1, "minimized", "")
        if "/opt/fixture-fuzzer" in command:
            findings = scratch / "findings"
            findings.mkdir(exist_ok=True)
            (findings / "crash-1").write_bytes(b"crash")
            return CommandResult(1, "crash", "")
        return CommandResult(1, "", "AddressSanitizer")


def test_manifest_rejects_root_mount_and_persists_only_valid_tasks(tmp_path: Path) -> None:
    invalid = _manifest()
    invalid["input_path"] = "/input"
    with pytest.raises(CyberGymManifestError, match="dedicated"):
        CyberGymTargetManifest.from_dict(invalid)

    store, scan_id = _store(tmp_path)
    task = store.get_cybergym_task(scan_id)
    assert task is not None
    assert task["manifest"]["target_binary"] == "/opt/fixture-target"
    assert task["status"] == "active"


@pytest.mark.asyncio
async def test_runtime_persists_artifacts_before_execution_and_submits_once(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    executor = _FixtureExecutor()
    submitted: list[bytes] = []

    async def submitter(_manifest, raw: bytes, _artifact: dict) -> dict:
        submitted.append(raw)
        return {"status": "accepted"}

    runtime = CyberGymRuntime(store, executor=executor, submitter=submitter)
    with pytest.raises(ValueError, match="empty"):
        runtime.artifact_create(scan_id, kind="seed", raw=b"")

    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    replay = await runtime.replay(scan_id, seed["artifact_id"])
    assert replay["crash"] is True
    assert store.get_cybergym_artifact(scan_id, seed["artifact_id"]) is not None

    gdb = await runtime.gdb(
        scan_id,
        seed["artifact_id"],
        {
            "breakpoints": [
                {"kind": "target", "location": "target"},
                {"kind": "vulnerable_branch", "location": "vulnerable"},
            ],
            "variables": ["length"],
        },
    )
    assert gdb["target_reached"] is True
    assert gdb["vulnerable_branch_reached"] is True
    gdb_command = executor.commands[-1]
    assert "shell" not in " ".join(gdb_command)
    assert "-nx" in gdb_command and "-batch" in gdb_command

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]], dictionary=["MAGIC"])
    for _ in range(20):
        status = runtime.fuzz_status(scan_id, started["run_id"])
        if status["status"] != "running":
            break
        await asyncio.sleep(0)
    assert status["status"] == "completed"
    crash = next(item for item in store.list_cybergym_artifacts(scan_id) if item["kind"] == "crash")

    minimized = await runtime.minimize(scan_id, crash["artifact_id"])
    assert minimized["replay"]["crash"] is True
    selection = runtime.select_final_artifact(scan_id)
    assert selection is not None
    assert selection["local_validation"] == "verified"

    active = store.start_cybergym_run(scan_id, "fuzz", {"seed_ids": [seed["artifact_id"]]})
    with pytest.raises(ValueError, match="still running"):
        await runtime.submit(
            scan_id,
            selection["artifact"]["artifact_id"],
            local_validation="verified",
            selection_reason="stable vulnerable-side crash replay",
        )
    store.finish_cybergym_run(active["run_id"], "completed", {"status": "clean"})

    submission = await runtime.submit(
        scan_id,
        selection["artifact"]["artifact_id"],
        local_validation="verified",
        selection_reason="stable vulnerable-side crash replay",
    )
    assert submission["official_result"] == {"status": "accepted"}
    assert submitted
    with pytest.raises(ValueError, match="finalized"):
        await runtime.submit(
            scan_id,
            selection["artifact"]["artifact_id"],
            local_validation="verified",
            selection_reason="second attempt",
        )
