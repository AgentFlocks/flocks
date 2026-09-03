from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

import flocks_code_security.cybergym_runtime as cybergym_runtime
from flocks_code_security.cybergym_runtime import (
    CommandResult,
    CyberGymManifestError,
    CyberGymRuntime,
    CyberGymTargetManifest,
    DockerCommandExecutor,
    OfficialCyberGymJudgeAdapter,
    _execution_status,
    _run_official_worker,
)
from flocks_code_security.models import SnapshotRef
from flocks_code_security.runtime import build_runtime
from flocks_code_security.store import ScanStore


def _manifest(*, fuzzer_supported: bool = True) -> dict:
    manifest = {
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
    if fuzzer_supported:
        manifest["input_contract"] = {}
    return manifest


def _store(tmp_path: Path, manifest: dict | None = None) -> tuple[ScanStore, str]:
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
    store.create_cybergym_task(scan_id, manifest or _manifest())
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


def test_manifest_input_contract_is_enforced_and_persisted_with_seed_provenance(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["input_contract"] = {"required_suffix_hex": "01 00 00 00"}

    parsed = CyberGymTargetManifest.from_dict(manifest)
    assert parsed.public_dict()["input_contract"] == {"required_prefix_hex": "", "required_suffix_hex": "01000000"}

    store, scan_id = _store(tmp_path, manifest)
    runtime = CyberGymRuntime(store)
    with pytest.raises(ValueError, match="input_contract"):
        runtime.artifact_create(scan_id, kind="seed", raw=b"font\x00\x00\x00\x00")

    seed = runtime.artifact_create(
        scan_id,
        kind="seed",
        raw=b"font\x01\x00\x00\x00",
        provenance={"operation": "fixture"},
    )

    assert seed["provenance"] == {
        "input_contract": {"required_prefix_hex": "", "required_suffix_hex": "01000000"},
        "operation": "fixture",
    }


def test_fuzzer_manifest_requires_structured_input_contract() -> None:
    missing_contract = _manifest()
    del missing_contract["input_contract"]
    with pytest.raises(CyberGymManifestError, match="requires input_contract"):
        CyberGymTargetManifest.from_dict(missing_contract)

    invalid_contract = _manifest()
    invalid_contract["input_contract"] = "trailing selector is one"
    with pytest.raises(CyberGymManifestError, match="input_contract must be an object"):
        CyberGymTargetManifest.from_dict(invalid_contract)


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


@pytest.mark.asyncio
async def test_fuzz_requires_vulnerable_replay_preflight(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    with pytest.raises(ValueError, match="replay preflight"):
        await runtime.fuzz_start(scan_id, [seed["artifact_id"]])

    assert store.cybergym_budget(scan_id)["fuzz"] == 0


@pytest.mark.asyncio
async def test_clean_replayed_seed_can_fuzz_without_gdb_target_reachability(tmp_path: Path) -> None:
    class _CleanExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    executor = _CleanExecutor()
    runtime = CyberGymRuntime(store, executor=executor)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])
    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    for _ in range(20):
        status = runtime.fuzz_status(scan_id, started["run_id"])
        if status["status"] != "running":
            break
        await asyncio.sleep(0)

    assert status["status"] == "completed"
    assert store.cybergym_budget(scan_id)["fuzz"] == 1


def test_no_artifact_is_terminal_and_does_not_consume_budget(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)

    active = store.start_cybergym_run(scan_id, "replay", {"artifact_id": "none"})
    with pytest.raises(ValueError, match="still running"):
        runtime.mark_failed_no_artifact(scan_id)
    store.finish_cybergym_run(active["run_id"], "failed", {"status": "runtime_error"})

    task = runtime.mark_failed_no_artifact(scan_id)

    assert task["status"] == "failed_no_artifact"
    assert task["local_validation"] == "failed_no_artifact"
    assert store.cybergym_budget(scan_id) == {
        "replay": 0,
        "gdb": 0,
        "fuzz": 0,
        "minimize": 0,
    }


@pytest.mark.asyncio
async def test_missing_artifact_is_rejected_before_budget_consumption(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())

    with pytest.raises(ValueError, match="not available"):
        await runtime.replay(scan_id, "missing-artifact")

    assert store.cybergym_budget(scan_id)["replay"] == 0


@pytest.mark.asyncio
async def test_replay_budget_exhaustion_is_persisted(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["limits"]["max_replay_runs"] = 2
    store, scan_id = _store(tmp_path, manifest)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    await runtime.replay(scan_id, artifact["artifact_id"])
    await runtime.replay(scan_id, artifact["artifact_id"])
    with pytest.raises(ValueError, match="budget is exhausted"):
        await runtime.replay(scan_id, artifact["artifact_id"])

    assert store.cybergym_budget(scan_id)["replay"] == 2


@pytest.mark.asyncio
async def test_dangerous_gdb_intent_is_rejected_before_execution(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())

    with pytest.raises(ValueError, match="allowed structured location"):
        await runtime.gdb(
            scan_id,
            "missing-artifact",
            {"breakpoints": [{"kind": "target", "location": "main; shell"}]},
        )

    assert store.cybergym_budget(scan_id)["gdb"] == 0


@pytest.mark.asyncio
async def test_unconfigured_judge_is_explicitly_recorded(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    submission = await runtime.submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="unverified",
        selection_reason="fixture artifact",
    )

    assert submission["official_result"] == {
        "status": "not_configured",
        "reason": "official_submitter_unconfigured",
    }
    assert store.get_cybergym_task(scan_id)["status"] == "submitted"


@pytest.mark.asyncio
async def test_judge_rejection_is_preserved_as_official_result(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    artifact = CyberGymRuntime(store).artifact_create(scan_id, kind="seed", raw=b"seed")

    async def reject(_manifest, _raw, _artifact):
        return {"status": "rejected", "reason": "fixture_judge_rejection"}

    submission = await CyberGymRuntime(store, submitter=reject).submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="unverified",
        selection_reason="fixture rejection",
    )

    assert submission["official_result"] == {
        "status": "rejected",
        "reason": "fixture_judge_rejection",
    }
    assert store.get_cybergym_task(scan_id)["status"] == "submitted"


@pytest.mark.asyncio
async def test_docker_daemon_errors_are_marked_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Process:
        returncode = 125

        async def communicate(self):
            return b"", b"permission denied while trying to connect to the Docker API at unix:///var/run/docker.sock"

    async def fake_create_process(*_command, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_process)

    result = await DockerCommandExecutor().run(["docker", "run", "fixture"], timeout_seconds=1)

    assert result.unavailable is True
    assert result.returncode == 125


@pytest.mark.asyncio
async def test_container_stderr_cannot_impersonate_docker_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Process:
        returncode = 139

        async def communicate(self):
            return b"", b"application error during connect to backend"

    async def fake_create_process(*_command, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_process)

    result = await DockerCommandExecutor().run(["docker", "run", "fixture"], timeout_seconds=1)

    assert result.unavailable is False
    assert _execution_status(result) == "crash"


@pytest.mark.asyncio
async def test_gdb_docker_harness_error_is_not_reported_as_completed(tmp_path: Path) -> None:
    class _DockerErrorExecutor:
        async def run(self, _command: list[str], *, timeout_seconds: int) -> CommandResult:
            assert timeout_seconds == 5
            return CommandResult(125, "", "docker: invalid argument")

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_DockerErrorExecutor())
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    result = await runtime.gdb(
        scan_id,
        artifact["artifact_id"],
        {"breakpoints": [{"kind": "target", "location": "target"}]},
    )

    assert result["status"] == "harness_error"
    assert result["exit_code"] == 125


@pytest.mark.asyncio
async def test_gdb_daemon_unavailable_is_not_reported_as_completed(tmp_path: Path) -> None:
    class _UnavailableExecutor:
        async def run(self, _command: list[str], *, timeout_seconds: int) -> CommandResult:
            assert timeout_seconds == 5
            return CommandResult(1, "", "docker daemon unavailable", unavailable=True)

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_UnavailableExecutor())
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    result = await runtime.gdb(
        scan_id,
        artifact["artifact_id"],
        {"breakpoints": [{"kind": "target", "location": "target"}]},
    )

    assert result["status"] == "gdb_unavailable"
    assert result["reason"] == "docker_unavailable"


def test_container_mount_is_writable_and_uses_valid_mount_syntax(tmp_path: Path) -> None:
    runtime = CyberGymRuntime(_store(tmp_path)[0])
    manifest = CyberGymTargetManifest.from_dict(_manifest())

    command = runtime._container_command(manifest, tmp_path / "scratch")

    mount = command[command.index("--mount") + 1]
    assert mount.startswith("type=bind,src=")
    assert ",dst=/cybergym" in mount
    assert not mount.endswith(",rw")


def test_container_command_omits_posix_user_on_non_posix_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = CyberGymRuntime(_store(tmp_path)[0])
    manifest = CyberGymTargetManifest.from_dict(_manifest())
    monkeypatch.delattr(cybergym_runtime.os, "getuid", raising=False)
    monkeypatch.delattr(cybergym_runtime.os, "getgid", raising=False)

    command = runtime._container_command(manifest, tmp_path / "scratch")

    assert "--user" not in command


def test_container_command_mounts_official_task_data_read_only(tmp_path: Path) -> None:
    data_dir = tmp_path / "server-data"
    out_dir = data_dir / "arvo" / "1304" / "vul" / "out"
    libs_dir = data_dir / "arvo" / "1304" / "vul" / "libs"
    out_dir.mkdir(parents=True)
    libs_dir.mkdir()
    target = out_dir / "fixture-target"
    fuzzer = out_dir / "fixture-fuzzer"
    target.write_bytes(b"target")
    fuzzer.write_bytes(b"fuzzer")

    manifest_data = _manifest()
    manifest_data.update({
        "task_id": "1304",
        "task_kind": "arvo",
        "target_binary": "/out/fixture-target",
        "fuzzer_target": "/out/fixture-fuzzer",
    })
    store, scan_id = _store(tmp_path, manifest_data)
    runtime = CyberGymRuntime(store, task_data_dir=data_dir)
    manifest = CyberGymTargetManifest.from_dict(manifest_data)

    command = runtime._container_command(manifest, tmp_path / "scratch")
    mount_specs = [command[index + 1] for index, item in enumerate(command[:-1]) if item == "--mount"]

    assert f"type=bind,src={target.resolve()},dst=/out/fixture-target,readonly" in mount_specs
    assert f"type=bind,src={fuzzer.resolve()},dst=/out/fixture-fuzzer,readonly" in mount_specs
    assert f"type=bind,src={libs_dir.resolve()},dst=/out-libs,readonly" in mount_specs
    assert ["--env", "LD_LIBRARY_PATH=/out-libs"] == command[command.index("--env"):command.index("--env") + 2]


def test_container_command_ignores_task_data_symlink_escape(tmp_path: Path) -> None:
    data_dir = tmp_path / "server-data"
    out_dir = data_dir / "arvo" / "1304" / "vul" / "out"
    out_dir.mkdir(parents=True)
    outside = tmp_path / "outside-target"
    outside.write_bytes(b"outside")
    (out_dir / "fixture-target").symlink_to(outside)

    manifest_data = _manifest()
    manifest_data.update({
        "task_id": "1304",
        "task_kind": "arvo",
        "target_binary": "/out/fixture-target",
        "fuzzer_target": "/out/fixture-target",
    })
    store, _scan_id = _store(tmp_path, manifest_data)
    runtime = CyberGymRuntime(store, task_data_dir=data_dir)
    manifest = CyberGymTargetManifest.from_dict(manifest_data)

    assert runtime._task_data_mounts(manifest) == []


@pytest.mark.asyncio
async def test_replay_writes_input_at_manifest_path_basename(tmp_path: Path) -> None:
    class _InputPathExecutor:
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            assert timeout_seconds == 5
            mount = next(item for item in command if item.startswith("type=bind,src="))
            scratch = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
            assert (scratch / "poc").read_bytes() == b"seed"
            assert "/cybergym/input/poc" in command
            return CommandResult(0, "clean", "")

    manifest_data = _manifest()
    manifest_data.update({"input_path": "/cybergym/input/poc"})
    store, scan_id = _store(tmp_path, manifest_data)
    runtime = CyberGymRuntime(store, executor=_InputPathExecutor())
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")

    result = await runtime.replay(scan_id, artifact["artifact_id"])

    assert result["status"] == "clean"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vul_exit_code", "fix_exit_code", "expected_status", "expected_confirmed"),
    [
        (77, 0, "accepted", True),
        (0, 0, "rejected", False),
        (1, 1, "rejected", False),
        (300, 0, "rejected", False),
    ],
    ids=["vulnerable-crashes-fixed-clean", "both-clean", "both-crash", "vulnerable-timeout"],
)
async def test_official_judge_uses_the_vul_fix_dynamic_oracle(
    tmp_path: Path,
    vul_exit_code: int,
    fix_exit_code: int,
    expected_status: str,
    expected_confirmed: bool,
) -> None:
    calls: list[tuple[str, str, int, int]] = []
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()

    def runner(task_id, poc_path, mode, actual_data_dir, docker_timeout, command_timeout):
        calls.append((task_id, mode, docker_timeout, command_timeout))
        assert actual_data_dir == data_dir.resolve()
        assert poc_path.read_bytes() == b"dynamic-poc"
        return (
            (vul_exit_code if mode == "vul" else fix_exit_code),
            (f"{mode} output").encode(),
        )

    manifest_data = _manifest()
    manifest_data.update({"task_id": "1065", "task_kind": "arvo"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)
    adapter = OfficialCyberGymJudgeAdapter(tmp_path / "official", data_dir, runner=runner)

    result = await adapter(manifest, b"dynamic-poc", {})

    assert result["status"] == expected_status
    assert result["dynamic_confirmed"] is expected_confirmed
    assert result["runner_task_id"] == "arvo:1065"
    assert result["vul_exit_code"] == vul_exit_code
    assert result["fix_exit_code"] == fix_exit_code
    assert calls == [("arvo:1065", "vul", 5, 5), ("arvo:1065", "fix", 5, 5)]


@pytest.mark.asyncio
async def test_official_judge_rejects_unsupported_task_kind_without_running(tmp_path: Path) -> None:
    def should_not_run(*_args):
        pytest.fail("unsupported task kinds must not invoke the official runner")

    manifest = CyberGymTargetManifest.from_dict(_manifest())
    result = await OfficialCyberGymJudgeAdapter(
        tmp_path / "official", tmp_path / "server-data", runner=should_not_run
    )(manifest, b"dynamic-poc", {})

    assert result == {
        "status": "not_configured",
        "reason": "unsupported_official_task_kind",
    }


@pytest.mark.asyncio
async def test_official_judge_rejects_unsafe_task_id_without_running(tmp_path: Path) -> None:
    def should_not_run(*_args):
        pytest.fail("unsafe official task IDs must not reach the official runner")

    manifest_data = _manifest()
    manifest_data.update({"task_id": "../escape", "task_kind": "arvo"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)
    result = await OfficialCyberGymJudgeAdapter(
        tmp_path / "official", tmp_path / "server-data", runner=should_not_run
    )(manifest, b"dynamic-poc", {})

    assert result == {
        "status": "not_configured",
        "reason": "invalid_official_task_id",
    }


@pytest.mark.asyncio
async def test_official_judge_reports_runner_failures_as_unavailable(tmp_path: Path) -> None:
    def runner(*_args):
        raise RuntimeError("docker daemon unavailable")

    manifest_data = _manifest()
    manifest_data.update({"task_id": "11244", "task_kind": "oss_fuzz"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)

    result = await OfficialCyberGymJudgeAdapter(
        tmp_path / "official", tmp_path / "server-data", runner=runner
    )(manifest, b"dynamic-poc", {})

    assert result["status"] == "unavailable"
    assert result["reason"] == "official_runner_error"
    assert result["runner_task_id"] == "oss-fuzz:11244"
    assert result["error_type"] == "RuntimeError"


@pytest.mark.asyncio
async def test_official_worker_protocol_bounds_large_outputs_for_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = json.dumps(
        {
            "task_id": "arvo:1065",
            "results": {
                "vul": {"exit_code": 77, "output": "v" * 40_000},
                "fix": {"exit_code": 0, "output": "f" * 40_000},
            },
        }
    ).encode()

    class _Process:
        returncode = 0

        async def communicate(self):
            return payload, b""

    async def fake_create_process(*_command, **_kwargs):
        return _Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_process)
    manifest_data = _manifest()
    manifest_data.update({"task_id": "1065", "task_kind": "arvo"})
    store, scan_id = _store(tmp_path, manifest_data)
    adapter = OfficialCyberGymJudgeAdapter(
        tmp_path / "official",
        tmp_path / "server-data",
        runner_python=Path(sys.executable),
    )
    runtime = CyberGymRuntime(store, submitter=adapter)
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"dynamic-poc")

    submission = await runtime.submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="unverified",
        selection_reason="large-output regression",
    )

    official_result = submission["official_result"]
    assert official_result["status"] == "accepted"
    assert len(official_result["vul_output"]) < 40_000
    assert len(official_result["fix_output"]) < 40_000
    assert len(json.dumps(official_result, ensure_ascii=False).encode()) <= 64 * 1024


@pytest.mark.asyncio
async def test_official_worker_invokes_both_modes_and_returns_json_protocol(tmp_path: Path) -> None:
    official_repo = tmp_path / "official"
    server_package = official_repo / "src" / "cybergym" / "server"
    server_package.mkdir(parents=True)
    for package in (official_repo / "src" / "cybergym", server_package):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (server_package / "server_utils.py").write_text(
        """
def run_container_binary(task_id, poc_path, mode, data_dir, *, docker_timeout, cmd_timeout):
    assert task_id == 'arvo:1065'
    assert poc_path.is_file()
    assert data_dir.is_dir()
    assert (docker_timeout, cmd_timeout) == (5, 5)
    return (77 if mode == 'vul' else 0, mode.encode())
""",
        encoding="utf-8",
    )
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()
    poc_path = tmp_path / "poc"
    poc_path.write_bytes(b"dynamic-poc")
    worker = Path(__file__).parents[1] / "src" / "flocks_code_security" / "cybergym_judge_worker.py"

    result = _run_official_worker(
        Path(sys.executable),
        worker,
        official_repo,
        "arvo:1065",
        poc_path,
        data_dir,
        5,
        5,
    )

    assert result == {
        "vul": {"exit_code": 77, "output": "vul"},
        "fix": {"exit_code": 0, "output": "fix"},
    }

    manifest_data = _manifest()
    manifest_data.update({"task_id": "1065", "task_kind": "arvo"})
    judge_result = await OfficialCyberGymJudgeAdapter(
        official_repo,
        data_dir,
        runner_python=Path(sys.executable),
    )(CyberGymTargetManifest.from_dict(manifest_data), b"dynamic-poc", {})

    assert judge_result["status"] == "accepted"
    assert judge_result["dynamic_confirmed"] is True
    assert judge_result["vul_exit_code"] == 77
    assert judge_result["fix_exit_code"] == 0


def test_official_judge_preserves_runner_venv_symlink(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    official_repo = tmp_path / "official"
    server_utils = official_repo / "src" / "cybergym" / "server" / "server_utils.py"
    server_utils.parent.mkdir(parents=True)
    server_utils.write_text("", encoding="utf-8")
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()
    real_python = tmp_path / "python-real"
    real_python.write_text("", encoding="utf-8")
    venv_python = official_repo / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(real_python)
    monkeypatch.setenv("FLOCKS_CYBERGYM_OFFICIAL_REPO", str(official_repo))
    monkeypatch.setenv("FLOCKS_CYBERGYM_DATA_DIR", str(data_dir))

    adapter = OfficialCyberGymJudgeAdapter.from_environment()

    assert adapter is not None
    assert adapter.runner_python == venv_python


def test_build_runtime_wires_the_official_judge_adapter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sentinel = object()
    monkeypatch.setattr(
        OfficialCyberGymJudgeAdapter,
        "from_environment",
        classmethod(lambda _cls: sentinel),
    )

    runtime = build_runtime(tmp_path / "plugin-data")

    assert runtime.cybergym.submitter is sentinel
