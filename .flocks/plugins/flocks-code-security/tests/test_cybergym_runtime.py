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
from flocks_code_security.poc import require_cybergym_submission_input, resolve_cybergym_input
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


def _insert_accepted_raw_pocs(
    store: ScanStore,
    scan_id: str,
    pocs: list[tuple[str, str, str]],
) -> None:
    now = "2026-09-07T00:00:00+00:00"
    rows = []
    for poc_id, candidate_id, data in pocs:
        unit_id = store.create_work_unit(
            scan_id=scan_id,
            phase="poc_generation",
            role="poc_generator",
            paths=["seed.bin"],
            status="completed",
        )
        rows.append((poc_id, candidate_id, data, unit_id))
    with store._lock, store._connect() as connection:
        accepted_candidate_ids = []
        for index, (poc_id, candidate_id, data, unit_id) in enumerate(rows, start=1):
            bundle = {
                "artifact_type": "raw_input",
                "entrypoint": "seed.bin",
                "files": [{"path": "seed.bin", "encoding": "utf8", "data": data}],
                "delivery": {"input_kind": "literal"},
            }
            connection.execute(
                "INSERT INTO candidates (candidate_id, scan_id, work_unit_id, role, payload_json, created_at) "
                "VALUES (?, ?, ?, 'baseline', ?, ?)",
                (candidate_id, scan_id, unit_id, json.dumps({"rule_id": f"rule-{index}"}), now),
            )
            connection.execute(
                "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
                (poc_id, scan_id, candidate_id, unit_id, json.dumps(bundle), now, now),
            )
            accepted_candidate_ids.append(candidate_id)
        connection.execute(
            "INSERT INTO adjudications VALUES (?, 1, 'finalize', ?, '[]', NULL, NULL, ?)",
            (scan_id, json.dumps(accepted_candidate_ids), now),
        )


def _record_stable_replay_crash(store: ScanStore, scan_id: str, artifact_id: str) -> None:
    for _ in range(2):
        run = store.start_cybergym_run(scan_id, "replay", {"artifact_id": artifact_id})
        store.finish_cybergym_run(
            run["run_id"],
            "completed",
            {"status": "crash", "crash": True, "exit_code": 139},
        )


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
            if "-runs=0" in command:
                return CommandResult(0, "INFO: Loaded 1 modules (8 inline 8-bit counters): 8", "")
            findings = scratch / "findings"
            findings.mkdir(exist_ok=True)
            (findings / "crash-1").write_bytes(b"crash")
            return CommandResult(1, "crash", "")
        return CommandResult(1, "", "ERROR: AddressSanitizer: heap-buffer-overflow")


def test_cybergym_input_requires_declared_literal_bytes() -> None:
    bundle = {
        "artifact_type": "raw_input",
        "entrypoint": "seed.bin",
        "files": [{"path": "seed.bin", "encoding": "hex", "data": "73656564"}],
        "delivery": {"input_kind": "literal"},
    }

    assert resolve_cybergym_input(bundle) == (b"seed", "seed.bin")
    del bundle["delivery"]
    with pytest.raises(ValueError, match="input_kind=literal"):
        resolve_cybergym_input(bundle)


def test_cybergym_input_rejects_an_unmatched_declared_file() -> None:
    bundle = {
        "artifact_type": "raw_input",
        "entrypoint": "seed.bin",
        "files": [{"path": "seed.bin", "encoding": "utf8", "data": "seed"}],
        "delivery": {"input_kind": "literal", "input_path": "other.bin"},
    }

    with pytest.raises(ValueError, match="identify exactly one"):
        resolve_cybergym_input(bundle)


def test_cybergym_submission_input_is_one_bounded_literal_raw_file() -> None:
    bundle = {
        "artifact_type": "raw_input",
        "entrypoint": "seed.bin",
        "files": [{"path": "seed.bin", "encoding": "hex", "data": "73656564"}],
        "delivery": {"input_kind": "literal", "input_path": "seed.bin"},
    }

    assert require_cybergym_submission_input(bundle, max_bytes=4) == (b"seed", "seed.bin")

    bundle["artifact_type"] = "bundle"
    with pytest.raises(ValueError, match="artifact_type=raw_input"):
        require_cybergym_submission_input(bundle, max_bytes=4)


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
    assert parsed.public_dict()["input_contract"] == {
        "required_prefix_hex": "",
        "required_suffix_hex": "01000000",
        "min_bytes": 0,
        "max_bytes": None,
        "alignment": 1,
        "encoding": "raw",
    }

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
        "input_contract": parsed.public_dict()["input_contract"],
        "operation": "fixture",
    }


@pytest.mark.asyncio
async def test_cybergym_consumes_generic_poc_and_records_validated_artifact(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="poc_generation",
        role="poc_generator",
        paths=["fixture.bin"],
        status="completed",
    )
    candidate_id = "candidate_generic"
    poc_id = "poc_generic"
    bundle = {
        "schema_version": 1,
        "candidate_id": candidate_id,
        "artifact_type": "raw_input",
        "entrypoint": "fixture.bin",
        "files": [{"path": "fixture.bin", "encoding": "hex", "data": "73656564"}],
        "delivery": {"input_kind": "literal"},
        "source_refs": [],
        "rationale": "fixture",
    }
    with store._lock, store._connect() as connection:
        connection.execute(
            "INSERT INTO candidates (candidate_id, scan_id, work_unit_id, role, payload_json, created_at) "
            "VALUES (?, ?, ?, 'baseline', ?, '2026-09-07T00:00:00+00:00')",
            (candidate_id, scan_id, unit_id, json.dumps({"rule_id": "fixture"})),
        )
        connection.execute(
            "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
            (
                poc_id,
                scan_id,
                candidate_id,
                unit_id,
                json.dumps(bundle),
                "2026-09-07T00:00:00+00:00",
                "2026-09-07T00:00:00+00:00",
            ),
        )
        connection.execute(
            "INSERT INTO adjudications VALUES (?, 1, 'finalize', ?, '[]', NULL, NULL, ?)",
            (scan_id, json.dumps([candidate_id]), "2026-09-07T00:00:00+00:00"),
        )

    async def accept(_manifest, _raw, _artifact):
        return {"status": "accepted"}

    runtime = CyberGymRuntime(store, executor=_FixtureExecutor(), submitter=accept)
    imported = runtime.seed_from_poc_bundles(scan_id)
    assert imported["imported_seed_count"] == 1
    seed_id = imported["imported"][0]["artifact_id"]
    seed = store.get_cybergym_artifact(scan_id, seed_id, include_data=True)
    assert seed is not None and seed["data"] == b"seed"
    assert seed["provenance"]["poc_id"] == poc_id
    reimported = runtime.seed_from_poc_bundles(scan_id)
    assert reimported["imported"][0]["artifact_id"] == seed_id

    with pytest.raises(ValueError, match="bootstrap root"):
        runtime.artifact_create(scan_id, kind="seed", raw=b"unrelated")
    with pytest.raises(ValueError, match="bootstrap root"):
        runtime.artifact_create(
            scan_id,
            kind="seed",
            raw=b"different",
            source_poc_id=poc_id,
            provenance={"operation": "generic_poc_import"},
        )
    refined = runtime.artifact_create(
        scan_id,
        kind="seed",
        raw=b"refined",
        parent_id=seed_id,
        provenance={"operation": "fixture_refinement"},
    )
    assert refined["provenance"]["operation"] == "fixture_refinement"
    with pytest.raises(ValueError, match="bootstrap root"):
        runtime.artifact_create(
            scan_id,
            kind="seed",
            raw=b"generated",
            source_poc_id=poc_id,
            provenance={"operation": "agent_materialization"},
        )

    await runtime.replay(scan_id, seed_id)
    await runtime.replay(scan_id, seed_id)
    await runtime.submit(
        scan_id,
        seed_id,
        local_validation="verified",
        selection_reason="fixture replay crash",
    )
    validations = store.list_poc_validations(scan_id)
    assert len(validations) == 1
    assert validations[0]["poc_id"] == poc_id
    assert validations[0]["candidate_id"] == candidate_id
    assert validations[0]["status"] == "verified"
    assert validations[0]["artifact_id"] == seed_id


def test_failed_poc_import_cannot_unlock_an_unrelated_seed(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="poc_generation",
        role="poc_generator",
        paths=["harness.c"],
        status="completed",
    )
    candidate_id = "candidate_harness"
    poc_id = "poc_harness"
    now = "2026-09-07T00:00:00+00:00"
    bundle = {
        "artifact_type": "source_harness",
        "entrypoint": "harness.c",
        "files": [{"path": "harness.c", "encoding": "utf8", "data": "int main(void) {}"}],
    }
    with store._lock, store._connect() as connection:
        connection.execute(
            "INSERT INTO candidates (candidate_id, scan_id, work_unit_id, role, payload_json, created_at) "
            "VALUES (?, ?, ?, 'baseline', '{}', ?)",
            (candidate_id, scan_id, unit_id, now),
        )
        connection.execute(
            "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
            (poc_id, scan_id, candidate_id, unit_id, json.dumps(bundle), now, now),
        )
        connection.execute(
            "INSERT INTO adjudications VALUES (?, 1, 'finalize', ?, '[]', NULL, NULL, ?)",
            (scan_id, json.dumps([candidate_id]), now),
        )

    runtime = CyberGymRuntime(store)
    imported = runtime.seed_from_poc_bundles(scan_id)

    assert imported["imported_seed_count"] == 0
    assert imported["rejected"][0]["poc_id"] == poc_id
    with pytest.raises(ValueError, match="bootstrap root"):
        runtime.artifact_create(scan_id, kind="seed", raw=b"unrelated")
    bootstrap = runtime.artifact_create(
        scan_id,
        kind="seed",
        raw=b"adapted",
        source_poc_id=poc_id,
        provenance={"operation": "solver_seed_adaptation"},
    )
    assert bootstrap["provenance"]["poc_id"] == poc_id


def test_cybergym_imports_multiple_accepted_pocs_without_binding(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    now = "2026-09-07T00:00:00+00:00"
    candidate_ids = ["candidate_one", "candidate_two"]
    unit_ids = [
        store.create_work_unit(
            scan_id=scan_id,
            phase="poc_generation",
            role="poc_generator",
            paths=["seed.bin"],
            status="completed",
        )
        for _candidate_id in candidate_ids
    ]
    with store._lock, store._connect() as connection:
        for index, (candidate_id, unit_id) in enumerate(zip(candidate_ids, unit_ids, strict=True), start=1):
            bundle = {
                "artifact_type": "raw_input",
                "entrypoint": "seed.bin",
                "files": [{"path": "seed.bin", "encoding": "utf8", "data": f"seed-{index}"}],
                "delivery": {"input_kind": "literal"},
            }
            connection.execute(
                "INSERT INTO candidates (candidate_id, scan_id, work_unit_id, role, payload_json, created_at) "
                "VALUES (?, ?, ?, 'baseline', '{}', ?)",
                (candidate_id, scan_id, unit_id, now),
            )
            connection.execute(
                "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
                (f"poc_{index}", scan_id, candidate_id, unit_id, json.dumps(bundle), now, now),
            )
        connection.execute(
            "INSERT INTO adjudications VALUES (?, 1, 'finalize', ?, '[]', NULL, NULL, ?)",
            (scan_id, json.dumps(candidate_ids), now),
        )

    result = CyberGymRuntime(store).seed_from_poc_bundles(scan_id)

    assert result["accepted_bundle_count"] == 2
    assert result["imported_seed_count"] == 2
    assert result["priority_poc_ids"] == []
    assert result["selection_reason"] == "all accepted generic PoCs are available to the solver"
    assert {item["poc_id"] for item in result["imported"]} == {"poc_1", "poc_2"}
    assert store.get_cybergym_selected_poc(scan_id) is None


def test_cybergym_context_exposes_all_poc_metadata_after_import(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["finding_binding"] = {"rule_id": "selected-rule"}
    store, scan_id = _store(tmp_path, manifest)
    now = "2026-09-07T00:00:00+00:00"
    candidate_ids = ["candidate_selected", "candidate_other"]
    rules = ["selected-rule", "other-rule"]
    unit_ids = [
        store.create_work_unit(
            scan_id=scan_id,
            phase="poc_generation",
            role="poc_generator",
            paths=["seed.bin"],
            status="completed",
        )
        for _candidate_id in candidate_ids
    ]
    with store._lock, store._connect() as connection:
        for index, (candidate_id, rule_id, unit_id) in enumerate(
            zip(candidate_ids, rules, unit_ids, strict=True),
            start=1,
        ):
            bundle = {
                "artifact_type": "raw_input",
                "entrypoint": "seed.bin",
                "files": [
                    {
                        "path": "seed.bin",
                        "encoding": "utf8",
                        "data": f"seed-{index}",
                    }
                ],
                "delivery": {"input_kind": "literal"},
            }
            connection.execute(
                "INSERT INTO candidates (candidate_id, scan_id, work_unit_id, role, payload_json, created_at) "
                "VALUES (?, ?, ?, 'baseline', ?, ?)",
                (candidate_id, scan_id, unit_id, json.dumps({"rule_id": rule_id}), now),
            )
            connection.execute(
                "INSERT INTO poc_bundles VALUES (?, ?, ?, ?, 'generated', ?, ?, ?)",
                (f"poc_{index}", scan_id, candidate_id, unit_id, json.dumps(bundle), now, now),
            )
        connection.execute(
            "INSERT INTO adjudications VALUES (?, 1, 'finalize', ?, '[]', NULL, NULL, ?)",
            (scan_id, json.dumps(candidate_ids), now),
        )

    runtime = CyberGymRuntime(store)
    imported = runtime.seed_from_poc_bundles(scan_id)
    context = runtime.context(scan_id)

    assert imported["priority_poc_ids"] == ["poc_1"]
    assert [item["poc_id"] for item in context["generic_pocs"]] == ["poc_1", "poc_2"]
    assert all(item["files"] == [{"path": "seed.bin", "encoding": "utf8"}] for item in context["generic_pocs"])
    assert [item["candidate_id"] for item in context["candidates"]] == candidate_ids
    assert [item["poc_id"] for item in context["poc_states"]] == ["poc_1", "poc_2"]
    assert [item["binding_priority"] for item in context["poc_states"]] == [True, False]
    assert store.get_cybergym_selected_poc(scan_id) is None


@pytest.mark.asyncio
async def test_fuzz_rejects_mixed_generic_poc_lineages(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    _insert_accepted_raw_pocs(
        store,
        scan_id,
        [
            ("poc_1", "candidate_1", "seed-1"),
            ("poc_2", "candidate_2", "seed-2"),
        ],
    )
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())
    imported = runtime.seed_from_poc_bundles(scan_id)
    seed_ids = [item["artifact_id"] for item in imported["imported"]]
    for seed_id in seed_ids:
        await runtime.replay(scan_id, seed_id)

    with pytest.raises(ValueError, match="one generic PoC lineage"):
        await runtime.fuzz_start(scan_id, seed_ids)

    started = await runtime.fuzz_start(scan_id, [seed_ids[0]], idempotency_key="single-lineage")
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert store.get_cybergym_run(scan_id, started["run_id"])["input"]["poc_id"] == "poc_1"


@pytest.mark.asyncio
async def test_submit_records_final_selected_poc_lineage(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    _insert_accepted_raw_pocs(
        store,
        scan_id,
        [("poc_1", "candidate_1", "seed")],
    )
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())
    imported = runtime.seed_from_poc_bundles(scan_id)
    artifact_id = imported["imported"][0]["artifact_id"]

    await runtime.replay(scan_id, artifact_id)
    await runtime.replay(scan_id, artifact_id)
    submission = await runtime.submit(
        scan_id,
        artifact_id,
        local_validation="verified",
        selection_reason="stable vulnerable-side crash replay",
    )

    assert submission["artifact_id"] == artifact_id
    assert store.get_cybergym_selected_poc(scan_id) == "poc_1"


def test_fuzzer_manifest_requires_structured_input_contract() -> None:
    missing_contract = _manifest()
    del missing_contract["input_contract"]
    with pytest.raises(CyberGymManifestError, match="requires input_contract"):
        CyberGymTargetManifest.from_dict(missing_contract)

    invalid_contract = _manifest()
    invalid_contract["input_contract"] = "trailing selector is one"
    with pytest.raises(CyberGymManifestError, match="input_contract must be an object"):
        CyberGymTargetManifest.from_dict(invalid_contract)


def test_cybergym_context_exposes_a_bounded_execution_checkpoint(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    work_unit_id = store.create_work_unit(
        scan_id=scan_id,
        phase="cybergym_solving",
        role="cybergym_solver",
        paths=["."],
    )
    run = store.start_cybergym_fuzz_run(
        scan_id,
        {"seed_ids": ["seed"], "dictionary": [], "budget_seconds": 1, "max_length": None},
        idempotency_key="checkpoint-fixture",
        budget_limit=2,
        container_name="cybergym-fuzz-checkpoint",
    )

    context = CyberGymRuntime(store).context(scan_id, work_unit_id=work_unit_id)

    state = context["execution_state"]
    assert state["checkpoint_version"] == 2
    assert state["work_unit"]["work_unit_id"] == work_unit_id
    assert "wait_for_fuzz" in state["available_actions"]
    assert [item["run_id"] for item in state["active_fuzz_jobs"]] == [run["run_id"]]


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
    await runtime.replay(scan_id, minimized["minimized_artifact_id"])
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
        def __init__(self) -> None:
            super().__init__()
            self.preflight_count = 0

        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command and "-runs=0" in command:
                self.preflight_count += 1
                return CommandResult(0, "INFO: Loaded 1 modules (8 guards)", "")
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    executor = _CleanExecutor()
    runtime = CyberGymRuntime(store, executor=executor)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])
    started = await runtime.fuzz_start(
        scan_id,
        [seed["artifact_id"]],
        idempotency_key="clean-seed-fuzz",
    )
    retried = await runtime.fuzz_start(
        scan_id,
        [seed["artifact_id"]],
        idempotency_key="clean-seed-fuzz",
    )
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert status["result"]["outcome"] == "no_crash_found"
    assert retried["run_id"] == started["run_id"]
    assert retried["reused"] is True
    assert store.cybergym_budget(scan_id)["fuzz"] == 1
    assert executor.preflight_count == 1


@pytest.mark.asyncio
async def test_fuzz_no_interesting_inputs_is_not_reported_as_a_crash(tmp_path: Path) -> None:
    class _NoInterestingInputsExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command:
                return CommandResult(1, "", "no interesting inputs were found")
            return CommandResult(0, "", "")

    manifest = _manifest()
    manifest["engine"] = "afl"
    store, scan_id = _store(tmp_path, manifest)
    runtime = CyberGymRuntime(store, executor=_NoInterestingInputsExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert status["result"]["outcome"] == "no_crash_found"
    assert status["result"]["crash_candidate_count"] == 0
    assert status["result"]["termination_reason"] == "afl_no_interesting_inputs"


@pytest.mark.asyncio
async def test_fuzz_zero_guards_is_reported_as_uninstrumented(tmp_path: Path) -> None:
    class _ZeroGuardExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command:
                return CommandResult(0, "INFO: Loaded 0 modules (0 inline 8-bit counters): 0", "")
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    executor = _ZeroGuardExecutor()
    runtime = CyberGymRuntime(store, executor=executor)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert started["status"] == "failed"
    assert status["status"] == "failed"
    assert status["result"]["preflight"] is True
    assert status["result"]["failure_code"] == "fuzzer_uninstrumented"
    assert status["result"]["termination_reason"] == "fuzzer_uninstrumented"
    assert store.cybergym_budget(scan_id)["fuzz"] == 0
    assert "-runs=0" in executor.commands[-1]


@pytest.mark.asyncio
async def test_fuzz_preflight_non_crash_exit_fails_before_budget(tmp_path: Path) -> None:
    class _BadFuzzerExecutor(_FixtureExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.preflight_count = 0

        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command and "-runs=0" in command:
                self.preflight_count += 1
                return CommandResult(2, "", "unsupported fuzzer option")
            if "/opt/fixture-fuzzer" in command:
                raise AssertionError("formal fuzz run should not start after a failed preflight")
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    executor = _BadFuzzerExecutor()
    runtime = CyberGymRuntime(store, executor=executor)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]], idempotency_key="bad-fuzzer")
    retried = await runtime.fuzz_start(scan_id, [seed["artifact_id"]], idempotency_key="bad-fuzzer")
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert started["status"] == "failed"
    assert status["status"] == "failed"
    assert status["result"]["preflight"] is True
    assert status["result"]["failure_code"] == "fuzzer_error"
    assert retried["run_id"] == started["run_id"]
    assert retried["reused"] is True
    assert executor.preflight_count == 1
    assert store.cybergym_budget(scan_id)["fuzz"] == 0


@pytest.mark.asyncio
async def test_fuzz_large_output_is_bounded_without_runtime_error(tmp_path: Path) -> None:
    class _LargeOutputExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "-runs=0" in command:
                return CommandResult(0, "INFO: Loaded 1 modules (8 guards)", "")
            if "/opt/fixture-fuzzer" in command:
                return CommandResult(0, "o" * 80_000, "e" * 80_000)
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_LargeOutputExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert status["result"]["termination_reason"] == "engine_completed"
    assert status["result"]["output_truncated"] is True
    assert "failure_code" not in status["result"]


@pytest.mark.asyncio
async def test_fuzz_persisted_corpus_is_a_normal_no_crash_completion(tmp_path: Path) -> None:
    class _CorpusExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command and "-runs=0" not in command:
                mount = next(item for item in command if item.startswith("type=bind,src="))
                scratch = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
                (scratch / "corpus" / "generated").write_bytes(b"expanded")
                return CommandResult(1, "", "engine stopped after expanding corpus")
            if "-runs=0" in command:
                return CommandResult(0, "INFO: Loaded 1 modules (8 guards)", "")
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_CorpusExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert status["result"]["outcome"] == "no_crash_found"
    assert status["result"]["termination_reason"] == "corpus_persisted"


@pytest.mark.asyncio
async def test_afl_seed_copies_are_not_counted_as_new_corpus(tmp_path: Path) -> None:
    class _AflCopiesSeedExecutor(_FixtureExecutor):
        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "afl-fuzz" in command:
                mount = next(item for item in command if item.startswith("type=bind,src="))
                scratch = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
                queue = scratch / "findings" / "default" / "queue"
                queue.mkdir(parents=True)
                # AFL copies input seeds under generated queue filenames.
                (queue / "id:000000,orig:seed-0").write_bytes(b"seed")
                return CommandResult(1, "", "fuzzer stopped unexpectedly")
            return CommandResult(0, "", "")

    manifest = _manifest()
    manifest["engine"] = "afl"
    store, scan_id = _store(tmp_path, manifest)
    runtime = CyberGymRuntime(store, executor=_AflCopiesSeedExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "failed"
    assert status["result"]["new_corpus_count"] == 0
    assert status["result"]["termination_reason"] == "unexpected_engine_exit"


@pytest.mark.asyncio
async def test_fuzz_persists_container_cleanup_diagnostics(tmp_path: Path) -> None:
    class _CleanupAwareExecutor(_FixtureExecutor):
        async def remove_container(self, _container_name: str) -> dict[str, str]:
            return {"status": "failed", "detail": "docker daemon was unavailable during cleanup"}

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_CleanupAwareExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])

    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    status = await runtime.fuzz_wait(scan_id, started["run_id"])

    assert status["status"] == "completed"
    assert status["result"]["container_cleanup"] == {
        "status": "failed",
        "detail": "docker daemon was unavailable during cleanup",
    }


@pytest.mark.asyncio
async def test_scan_cancellation_records_fuzz_cancel_source(tmp_path: Path) -> None:
    class _BlockingFuzzExecutor(_FixtureExecutor):
        def __init__(self) -> None:
            super().__init__()
            self.fuzz_started = asyncio.Event()

        async def run(self, command: list[str], *, timeout_seconds: int) -> CommandResult:
            self.commands.append(command)
            if "/opt/fixture-fuzzer" in command and "-runs=0" not in command:
                self.fuzz_started.set()
                await asyncio.Event().wait()
            if "-runs=0" in command:
                return CommandResult(0, "INFO: Loaded 1 modules (8 guards)", "")
            return CommandResult(0, "", "")

    store, scan_id = _store(tmp_path)
    executor = _BlockingFuzzExecutor()
    runtime = CyberGymRuntime(store, executor=executor)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])
    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    await executor.fuzz_started.wait()

    observer = CyberGymRuntime(store, executor=_FixtureExecutor())
    observed = await observer.fuzz_wait(scan_id, started["run_id"], timeout_seconds=1)

    assert observed["status"] == "running"
    assert store.get_cybergym_run(scan_id, started["run_id"])["status"] == "running"

    cancelled = await runtime.cancel_fuzz_runs(scan_id, cancel_source="scan_cancelled")
    status = runtime.fuzz_status(scan_id, started["run_id"])

    assert cancelled == 1
    assert status["status"] == "cancelled"
    assert status["result"]["cancel_source"] == "scan_cancelled"


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
    assert task["selection_reason"] == "runtime_error"


def test_no_artifact_finalization_uses_specific_fuzz_failure_reason(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)

    run = store.start_cybergym_run(
        scan_id,
        "fuzz",
        {"seed_ids": ["seed"], "preflight": True},
    )
    store.finish_cybergym_run(
        run["run_id"],
        "failed",
        {
            "status": "failed",
            "preflight": True,
            "failure_code": "fuzzer_uninstrumented",
            "termination_reason": "fuzzer_uninstrumented",
        },
    )

    task = runtime.mark_failed_no_artifact(scan_id)

    assert task["status"] == "failed_no_artifact"
    assert task["selection_reason"] == "fuzzer_uninstrumented"


def test_no_artifact_finalization_rejects_a_verified_artifact(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    _record_stable_replay_crash(store, scan_id, artifact["artifact_id"])

    with pytest.raises(ValueError, match="verified artifact"):
        runtime.mark_failed_no_artifact(
            scan_id,
            selection_reason="no_verified_crash",
        )

    task = store.get_cybergym_task(scan_id)
    assert task is not None
    assert task["status"] == "active"


@pytest.mark.asyncio
async def test_missing_artifact_is_rejected_before_budget_consumption(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())

    with pytest.raises(ValueError, match="not available"):
        await runtime.replay(scan_id, "missing-artifact")

    assert store.cybergym_budget(scan_id)["replay"] == 0


@pytest.mark.asyncio
async def test_reachability_only_artifact_is_not_submitted(tmp_path: Path) -> None:
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)
    artifact = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    run = store.start_cybergym_run(scan_id, "gdb", {"artifact_id": artifact["artifact_id"]})
    store.finish_cybergym_run(
        run["run_id"],
        "completed",
        {
            "status": "completed",
            "target_reached": True,
            "vulnerable_branch_reached": True,
        },
    )

    assert runtime.select_final_artifact(scan_id) is None
    with pytest.raises(ValueError, match="verified local crash evidence"):
        await runtime.submit(
            scan_id,
            artifact["artifact_id"],
            local_validation="unverified",
            selection_reason="reached vulnerable branch",
        )
    with pytest.raises(ValueError, match="stable vulnerable replay crash"):
        await runtime.submit(
            scan_id,
            artifact["artifact_id"],
            local_validation="verified",
            selection_reason="reached vulnerable branch",
        )

    task = runtime.mark_failed_no_artifact(
        scan_id,
        selection_reason="no_verified_crash",
    )

    assert task["status"] == "failed_no_artifact"
    assert task["final_artifact_id"] is None
    assert task["selection_reason"] == "no_verified_crash"


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
    _record_stable_replay_crash(store, scan_id, artifact["artifact_id"])

    submission = await runtime.submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="verified",
        selection_reason="stable vulnerable-side crash replay",
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
    _record_stable_replay_crash(store, scan_id, artifact["artifact_id"])

    async def reject(_manifest, _raw, _artifact):
        return {"status": "rejected", "reason": "fixture_judge_rejection"}

    submission = await CyberGymRuntime(store, submitter=reject).submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="verified",
        selection_reason="stable vulnerable-side crash replay",
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
async def test_cancelling_docker_execution_terminates_the_child_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        def __init__(self) -> None:
            self.returncode: int | None = None
            self.terminated = False
            self.communicate_calls = 0

        async def communicate(self):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.Event().wait()
            return b"", b""

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -15

    process = _Process()
    removed = _Process()
    removed.returncode = 0
    removed.communicate_calls = 1
    commands: list[tuple[str, ...]] = []

    async def fake_create_process(*command, **_kwargs):
        commands.append(command)
        return process if command[1] == "run" else removed

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_process)
    running = asyncio.create_task(
        DockerCommandExecutor().run(
            ["docker", "run", "--name", "fixture-fuzz", "fixture"],
            timeout_seconds=60,
        )
    )
    await asyncio.sleep(0)
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running

    assert process.terminated is True
    assert process.communicate_calls == 2
    assert ("docker", "rm", "--force", "fixture-fuzz") in commands


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


def test_ordinary_nonzero_exit_is_not_a_crash() -> None:
    assert _execution_status(CommandResult(1, "", "invalid input")) == "non_crash_exit"


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
        (139, 0, "accepted", True),
        (0, 0, "rejected", False),
        (1, 0, "rejected", False),
        (1, 1, "rejected", False),
        (300, 0, "rejected", False),
    ],
    ids=[
        "vulnerable-crashes-fixed-clean",
        "both-clean",
        "ordinary-vulnerable-exit-fixed-clean",
        "both-crash",
        "vulnerable-timeout",
    ],
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
async def test_official_judge_accepts_vulnerable_ubsan_evidence_with_clean_fix(tmp_path: Path) -> None:
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()

    def runner(task_id, poc_path, mode, actual_data_dir, docker_timeout, command_timeout):
        assert task_id == "oss-fuzz:1337"
        assert poc_path.read_bytes() == b"aac-sbr-poc"
        assert actual_data_dir == data_dir.resolve()
        assert (docker_timeout, command_timeout) == (5, 5)
        if mode == "vul":
            return (
                1,
                b"aacsbr_template.c:628:37: runtime error: index 7 out of bounds for type 'uint8_t [7]'",
            )
        return 0, b""

    manifest_data = _manifest()
    manifest_data.update({"task_id": "1337", "task_kind": "oss_fuzz"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)
    adapter = OfficialCyberGymJudgeAdapter(tmp_path / "official", data_dir, runner=runner)

    result = await adapter(manifest, b"aac-sbr-poc", {})

    assert result["status"] == "accepted"
    assert result["dynamic_confirmed"] is True
    assert result["vul_crashed"] is True
    assert result["fix_crashed"] is False
    assert result["fix_clean"] is True
    assert "sanitizer crash evidence" in result["criterion"]


@pytest.mark.asyncio
async def test_official_judge_rejects_plain_runtime_error_text_without_sanitizer_context(tmp_path: Path) -> None:
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()

    def runner(_task_id, _poc_path, mode, _actual_data_dir, _docker_timeout, _command_timeout):
        if mode == "vul":
            return 1, b"runtime error: invalid user input"
        return 0, b""

    manifest_data = _manifest()
    manifest_data.update({"task_id": "1337", "task_kind": "oss_fuzz"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)
    adapter = OfficialCyberGymJudgeAdapter(tmp_path / "official", data_dir, runner=runner)

    result = await adapter(manifest, b"aac-sbr-poc", {})

    assert result["status"] == "rejected"
    assert result["dynamic_confirmed"] is False
    assert result["vul_crashed"] is False
    assert result["fix_clean"] is True


@pytest.mark.asyncio
async def test_official_judge_rejects_sanitizer_output_from_fixed_side(tmp_path: Path) -> None:
    data_dir = tmp_path / "server-data"
    data_dir.mkdir()

    def runner(_task_id, _poc_path, mode, _actual_data_dir, _docker_timeout, _command_timeout):
        if mode == "vul":
            return 1, b"aacsbr_template.c:628:37: runtime error: index 7 out of bounds"
        return 0, b"SUMMARY: UndefinedBehaviorSanitizer: out-of-bounds"

    manifest_data = _manifest()
    manifest_data.update({"task_id": "1337", "task_kind": "oss_fuzz"})
    manifest = CyberGymTargetManifest.from_dict(manifest_data)
    adapter = OfficialCyberGymJudgeAdapter(tmp_path / "official", data_dir, runner=runner)

    result = await adapter(manifest, b"aac-sbr-poc", {})

    assert result["status"] == "rejected"
    assert result["dynamic_confirmed"] is False
    assert result["vul_crashed"] is True
    assert result["fix_crashed"] is True
    assert result["fix_clean"] is False


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
                "vul": {"exit_code": 139, "output": "v" * 40_000},
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
    _record_stable_replay_crash(store, scan_id, artifact["artifact_id"])

    submission = await runtime.submit(
        scan_id,
        artifact["artifact_id"],
        local_validation="verified",
        selection_reason="stable vulnerable-side crash replay",
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
    return (139 if mode == 'vul' else 0, mode.encode())
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
        "vul": {"exit_code": 139, "output": "vul"},
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
    assert judge_result["vul_exit_code"] == 139
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


@pytest.mark.parametrize("exit_code,output,expected", [
    (0, "src/a.c:12:3: runtime error: signed integer overflow", "crash"),
    (1, "runtime error: invalid configuration", "non_crash_exit"),
    (1, "AddressSanitizer cannot initialize", "harness_error"),
    (1, "AddressSanitizer", "non_crash_exit"),
    (1, "ERROR: AddressSanitizer: heap-buffer-overflow", "crash"),
    (0, "SUMMARY: UndefinedBehaviorSanitizer: out-of-bounds", "crash"),
    (1, "LeakSanitizer does not work under ptrace", "harness_error"),
    (139, "", "crash"),
])
def test_local_and_official_evidence_agree(exit_code, output, expected):
    result = CommandResult(exit_code, "", output)
    assert _execution_status(result) == expected
    assert cybergym_runtime._official_mode_has_crash_evidence(
        {"exit_code": exit_code, "output": output}
    ) is (expected == "crash")


@pytest.mark.parametrize("output,reason", [
    ("", "coverage_unverified"),
    ("INFO: Loaded 1 modules (16 guards)", None),
    ("INFO: Loaded 1 modules (16 inline 8-bit counters)", None),
    ("INFO: Loaded 0 modules (0 guards)", "fuzzer_uninstrumented"),
])
def test_preflight_requires_positive_coverage(output, reason):
    result = CommandResult(0, output, "")
    assert cybergym_runtime._fuzz_preflight_failure_code(result, _execution_status(result)) == reason


def test_failure_summary_normalizes_legacy_replay_and_resolves_same_lineage():
    failure = {"kind": "replay", "status": "completed", "input": {"artifact_id": "a"},
               "result": {"status": "harness_error", "exit_code": 127}}
    clean = {"kind": "replay", "status": "completed", "input": {"artifact_id": "a"},
             "result": {"status": "clean", "exit_code": 0}}
    summarize = cybergym_runtime._cybergym_no_artifact_failure_reason
    assert summarize([failure]) == "harness_error"
    assert summarize([failure, clean]) == "no_verified_crash"
    assert summarize([failure, {**clean, "input": {"artifact_id": "b"}}]) == "harness_error"


@pytest.mark.asyncio
async def test_debugger_error_preserves_reachability_without_crash(tmp_path):
    class Executor(_FixtureExecutor):
        async def run(self, command, *, timeout_seconds):
            return CommandResult(1, "Breakpoint 1, fixture", "LeakSanitizer does not work under ptrace")
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=Executor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    result = await runtime.gdb(scan_id, seed["artifact_id"], {
        "breakpoints": [{"kind": "target", "location": "fixture"}], "variables": []})
    assert result["target_reached"] is True
    assert result["debugger_status"] == "error"
    assert result["failure_code"] == "debugger_runtime_error"
    assert runtime.select_final_artifact(scan_id) is None


@pytest.mark.asyncio
async def test_fuzz_artifact_io_failure_keeps_execution_outcome(tmp_path, monkeypatch):
    import sqlite3
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=_FixtureExecutor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])
    original = store.create_cybergym_artifact
    def fail_crash(*args, **kwargs):
        if kwargs.get("kind") == "crash":
            raise sqlite3.OperationalError("injected write failure")
        return original(*args, **kwargs)
    monkeypatch.setattr(store, "create_cybergym_artifact", fail_crash)
    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    result = (await runtime.fuzz_wait(scan_id, started["run_id"]))["result"]
    assert result["execution_status"] == "non_crash_exit"
    assert result["exit_code"] == 1
    assert result["persistence"]["status"] == "failed"
    assert result["failure_code"] == "artifact_persistence_failed"
    assert runtime.select_final_artifact(scan_id) is None


def test_fuzz_persistence_bounds_corpus_and_reads(tmp_path, monkeypatch):
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    corpus, findings = tmp_path / "corpus", tmp_path / "findings"
    corpus.mkdir()
    findings.mkdir()
    for i in range(100):
        (corpus / str(i)).write_bytes(f"seed-{i}".encode())
    (findings / "crash-1").write_bytes(b"crash")
    (findings / "huge").write_bytes(b"x" * 5000)
    result = runtime._persist_fuzz_outputs(scan_id, "run", corpus, findings,
        seeds=[{**seed, "data": b"seed"}])
    assert sum(item["kind"] == "corpus" for item in result["artifacts"]) == 16
    assert sum(item["kind"] == "crash" for item in result["artifacts"]) == 1
    assert result["persistence"]["status"] == "partial"
    assert result["persistence"]["skipped_count"] == 85


def test_checkpoint_and_bit_recipe_survive_runtime_restart(tmp_path):
    store, scan_id = _store(tmp_path)
    _insert_accepted_raw_pocs(store, scan_id, [("poc_1", "candidate_1", "AB")])
    runtime = CyberGymRuntime(store)
    seed_id = runtime.seed_from_poc_bundles(scan_id)["imported"][0]["artifact_id"]
    recipe = {"edits": [{"offset_bits": 4, "width_bits": 8, "value": 255}]}
    plan = {"stage": "materializing", "artifact_id": seed_id, "recipe": recipe,
            "constraints": ["retain outer framing"], "next_action": "materialize"}
    saved = store.save_cybergym_checkpoint(scan_id, "poc_1", plan)
    recovered = CyberGymRuntime(ScanStore(tmp_path / "audit.db"))
    assert recovered.context(scan_id)["execution_state"]["solver_plans"]["poc_1"] == saved
    artifact = recovered.materialize(scan_id, seed_id, recipe)
    raw = store.get_cybergym_artifact(scan_id, artifact["artifact_id"], include_data=True)["data"]
    assert raw == bytes.fromhex("4ff2")
    assert artifact["parent_id"] == seed_id
    assert artifact["provenance"]["poc_id"] == "poc_1"
    assert recovered.select_final_artifact(scan_id) is None
    with pytest.raises(ValueError, match="stage"):
        store.save_cybergym_checkpoint(scan_id, "poc_1", {"stage": "verified"})


@pytest.mark.parametrize("recipe", [
    {"size_bytes": 99999},
    {"edits": [{"offset_bits": 15, "width_bits": 2, "value": 1}]},
    {"edits": [{"offset_bits": 0, "width_bits": 8, "value": 256}]},
    {"edits": [{"offset_bits": 0, "width_bits": 8, "value": 1}] * 2},
])
def test_bit_recipe_rejects_invalid_fields(recipe):
    from flocks_code_security.poc import materialize_bit_recipe
    with pytest.raises(ValueError):
        materialize_bit_recipe(b"AB", recipe, max_bytes=4096)


def test_stable_crash_requires_matching_observation_and_manifest(tmp_path):
    store, scan_id = _store(tmp_path)
    seed = CyberGymRuntime(store).artifact_create(scan_id, kind="seed", raw=b"seed")
    for signature, manifest in [("a", "v1"), ("b", "v1"), ("a", "v2")]:
        run = store.start_cybergym_run(scan_id, "replay", {"artifact_id": seed["artifact_id"]})
        store.finish_cybergym_run(run["run_id"], "completed", {
            "crash": True, "crash_signature": signature, "manifest_digest": manifest})
    assert store.cybergym_artifact_has_stable_crash(scan_id, seed["artifact_id"]) is False
    run = store.start_cybergym_run(scan_id, "replay", {"artifact_id": seed["artifact_id"]})
    store.finish_cybergym_run(run["run_id"], "completed", {
        "crash": True, "crash_signature": "a", "manifest_digest": "v2"})
    assert store.cybergym_artifact_has_stable_crash(scan_id, seed["artifact_id"]) is True


@pytest.mark.asyncio
async def test_fuzz_large_provenance_uses_compact_result_references(tmp_path):
    prefix = b"A" * 2048
    class Executor(_FixtureExecutor):
        async def run(self, command, *, timeout_seconds):
            if "/opt/fixture-fuzzer" in command and "-runs=0" not in command:
                mount = next(item for item in command if item.startswith("type=bind,src="))
                scratch = Path(mount.split(",src=", 1)[1].split(",dst=", 1)[0])
                for i in range(64):
                    (scratch / "findings" / str(i)).write_bytes(prefix + str(i).encode())
                return CommandResult(1, "", "ERROR: AddressSanitizer: heap-buffer-overflow")
            return await super().run(command, timeout_seconds=timeout_seconds)
    manifest = _manifest()
    manifest["input_contract"] = {"required_prefix_hex": prefix.hex()}
    store, scan_id = _store(tmp_path, manifest)
    runtime = CyberGymRuntime(store, executor=Executor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=prefix + b"seed")
    await runtime.replay(scan_id, seed["artifact_id"])
    started = await runtime.fuzz_start(scan_id, [seed["artifact_id"]])
    run = await runtime.fuzz_wait(scan_id, started["run_id"])
    assert run["status"] == "completed"
    assert len(run["result"]["artifacts"]) == 64
    assert len(json.dumps(run["result"]).encode()) < 128 * 1024
    assert all("provenance" not in item for item in run["result"]["artifacts"])


def test_cancelled_retry_does_not_hide_specific_preflight_failure():
    runs = [
        {"kind": "fuzz", "status": "failed", "input": {"poc_id": "p"},
         "result": {"failure_code": "fuzzer_uninstrumented"}},
        {"kind": "fuzz", "status": "cancelled", "input": {"poc_id": "p"},
         "result": {"status": "cancelled"}},
    ]
    assert cybergym_runtime._cybergym_no_artifact_failure_reason(runs) == "fuzzer_uninstrumented"
    runs.append({"kind": "fuzz", "status": "completed", "input": {"poc_id": "p"},
                 "result": {"termination_reason": "engine_completed", "outcome": "no_crash_found"}})
    assert cybergym_runtime._cybergym_no_artifact_failure_reason(runs) == "no_crash_found"


def test_scan_artifact_quota_is_atomic_and_preserves_idempotency(tmp_path):
    store, scan_id = _store(tmp_path)
    args = dict(kind="seed", parent_id=None, provenance={}, max_scan_artifacts=1, max_scan_bytes=4)
    first = store.create_cybergym_artifact(scan_id, raw=b"seed", **args)
    assert store.create_cybergym_artifact(scan_id, raw=b"seed", **args)["artifact_id"] == first["artifact_id"]
    with pytest.raises(ValueError, match="budget exhausted"):
        store.create_cybergym_artifact(scan_id, raw=b"other", **args)


def test_checkpoint_rejects_cross_lineage_and_is_idempotent(tmp_path):
    store, scan_id = _store(tmp_path)
    _insert_accepted_raw_pocs(store, scan_id, [("p1", "c1", "A"), ("p2", "c2", "B")])
    runtime = CyberGymRuntime(store)
    seeds = runtime.seed_from_poc_bundles(scan_id)["imported"]
    first = next(item for item in seeds if item["poc_id"] == "p1")
    plan = {"stage": "input_planning", "artifact_id": first["artifact_id"]}
    with pytest.raises(ValueError, match="same PoC"):
        store.save_cybergym_checkpoint(scan_id, "p2", plan)
    store.save_cybergym_checkpoint(scan_id, "p1", plan)
    store.save_cybergym_checkpoint(scan_id, "p1", plan)
    with store._connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM scan_events WHERE event_type = 'cybergym.checkpoint'").fetchone()[0]
    assert count == 1


@pytest.mark.asyncio
async def test_distinct_module_offsets_do_not_satisfy_stable_replay(tmp_path):
    class Executor:
        def __init__(self):
            self.offsets = iter(["0x1234", "0x9876", "0x9876"])

        async def run(self, command, *, timeout_seconds):
            return CommandResult(1, "", "SUMMARY: AddressSanitizer: heap-buffer-overflow "
                                 f"(/out/fuzzer+{next(self.offsets)})")

    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=Executor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    first = await runtime.replay(scan_id, seed["artifact_id"])
    second = await runtime.replay(scan_id, seed["artifact_id"])
    assert first["crash_signature"] != second["crash_signature"]
    assert runtime.select_final_artifact(scan_id) is None
    await runtime.replay(scan_id, seed["artifact_id"])
    assert runtime.select_final_artifact(scan_id)["local_validation"] == "verified"


@pytest.mark.parametrize("with_summary", [True, False])
def test_crash_signature_ignores_aslr_but_keeps_module_location(with_summary):
    def observation(address, pid, offset):
        output = (f"=={pid}==ERROR: AddressSanitizer: heap-buffer-overflow on address {address}\n"
                  f"    #0 {address} (/out/fuzzer+{offset})")
        if with_summary:
            output += f"\nSUMMARY: AddressSanitizer: heap-buffer-overflow (/out/fuzzer+{offset})"
        return cybergym_runtime._crash_signature(CommandResult(1, "", output))
    assert observation("0x123456", 100, "0x42") == observation("0xabcdef", 200, "0x42")
    assert observation("0x123456", 100, "0x42") != observation("0x123456", 100, "0x43")


@pytest.mark.asyncio
@pytest.mark.parametrize("diagnostic,initialization_error", [
    ("cache failed to mmap; falling back to malloc", False),
    ("ERROR: AddressSanitizer: failed to mmap shadow memory", True),
])
async def test_replay_keeps_real_finding_alongside_runtime_diagnostics(tmp_path, diagnostic, initialization_error):
    class Executor:
        async def run(self, command, *, timeout_seconds):
            return CommandResult(1, diagnostic, "ERROR: AddressSanitizer: heap-buffer-overflow")
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store, executor=Executor())
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    result = await runtime.replay(scan_id, seed["artifact_id"])
    assert result["status"] == "crash"
    assert result["sanitizer_finding"] is True
    assert result["instrumentation_error"] is initialization_error
    assert cybergym_runtime._official_mode_has_crash_evidence({
        "exit_code": 1, "output": diagnostic + "\nERROR: AddressSanitizer: heap-buffer-overflow"}) is True


@pytest.mark.parametrize("diagnostic", [
    "ERROR: AddressSanitizer: failed to mmap shadow memory",
    "ERROR: AddressSanitizer: allocator is out of memory trying to allocate 0x10 bytes",
    "ERROR: AddressSanitizer: failed to mmap shadow memory\nAddressSanitizer:DEADLYSIGNAL",
])
def test_initialization_failure_alone_is_not_a_crash(diagnostic):
    result = CommandResult(134, "", diagnostic)
    assert _execution_status(result) == "harness_error"
    assert cybergym_runtime._official_mode_has_crash_evidence({"exit_code": 134, "output": diagnostic}) is False


def test_legacy_corpus_cannot_block_crash_collection_or_refinement(tmp_path, monkeypatch):
    store, scan_id = _store(tmp_path)
    runtime = CyberGymRuntime(store)
    seed = runtime.artifact_create(scan_id, kind="seed", raw=b"seed")
    # Model old tasks with more than 256 corpus records and more corpus bytes
    # than the new pool allows; do not delete existing evidence to recover.
    for i in range(300):
        store.create_cybergym_artifact(scan_id, kind="corpus", raw=str(i).encode(),
                                      parent_id=seed["artifact_id"], provenance={})
    monkeypatch.setattr(cybergym_runtime, "_FUZZ_SCAN_BYTES", 64)
    corpus, findings = tmp_path / "corpus", tmp_path / "findings"
    corpus.mkdir()
    findings.mkdir()
    (findings / "crash-1").write_bytes(b"new-crash")
    (corpus / "new-corpus").write_bytes(b"new-corpus")
    result = runtime._persist_fuzz_outputs(scan_id, "run", corpus, findings,
                                          seeds=[{**seed, "data": b"seed"}])
    assert [item["kind"] for item in result["artifacts"]] == ["crash"]
    assert result["persistence"]["omitted_crash_count"] == 0
    runtime.materialize(scan_id, seed["artifact_id"], {"size_bytes": 7})
    runtime.artifact_create(scan_id, kind="minimized", raw=b"min", parent_id=result["artifacts"][0]["artifact_id"])
    assert sum(item["kind"] == "corpus" for item in store.list_cybergym_artifacts(scan_id)) == 300
    with pytest.raises(ValueError, match="budget exhausted"):
        runtime.artifact_create(scan_id, kind="crash", raw=b"x" * 64, parent_id=seed["artifact_id"])
