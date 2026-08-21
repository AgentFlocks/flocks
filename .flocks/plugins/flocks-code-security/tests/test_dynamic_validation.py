from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import flocks_code_security.dynamic_validation as dynamic_module
from flocks_code_security.dynamic_validation import (
    MAX_LOG_BYTES,
    DockerDynamicRunner,
    validate_probe,
)
from flocks_code_security.store import ScanStore


def _runnable_probe(candidate_id: str = "cand_test") -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "runnable",
        "context_path": ".",
        "dockerfile_path": "Dockerfile",
        "control": {"script": "printf control", "timeout_seconds": 10},
        "attack": {"script": "printf attack", "timeout_seconds": 10},
        "expected_difference": "Attack output differs from control output.",
    }


def _successful_command(stdout: str = "") -> dict:
    return {
        "exit_code": 0,
        "duration_ms": 1,
        "stdout": stdout,
        "stderr": "",
        "timed_out": False,
        "truncated": False,
    }


def _build_identity(
    *,
    context_path: str = ".",
    dockerfile_path: str = "Dockerfile",
) -> dynamic_module.BuildIdentity:
    return dynamic_module.BuildIdentity(
        scan_id="scan_test",
        snapshot_id="snap_test",
        tree_digest="tree_test",
        context_path=context_path,
        dockerfile_path=dockerfile_path,
    )


def _shared_image_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    build_succeeds: bool = True,
    failed_control_candidate: str | None = None,
) -> tuple[DockerDynamicRunner, list[list[str]], list[tuple[str, str, dict]], str]:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    dockerfile = snapshot / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(dynamic_module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        dynamic_module,
        "docker_runtime_dir",
        lambda scan_id: runtime_root / scan_id,
    )
    completed: list[tuple[str, str, dict]] = []

    class Store:
        def get_scan(self, scan_id: str):
            return {"scan_id": scan_id, "snapshot_id": "snap_shared"}

        def get_snapshot(self, snapshot_id: str):
            return SimpleNamespace(
                root_path=str(snapshot),
                snapshot_id=snapshot_id,
                tree_digest="tree_shared",
            )

        def list_snapshot_files(self, _snapshot_id: str):
            data = dockerfile.read_bytes()
            return [
                SimpleNamespace(
                    relative_path="Dockerfile",
                    size_bytes=len(data),
                    blob_digest=hashlib.sha256(data).hexdigest(),
                )
            ]

        def complete_dynamic_run(self, candidate_id: str, status: str, facts: dict):
            completed.append((candidate_id, status, facts))

    runner = DockerDynamicRunner(Store())
    runner._build_backend = "buildkit"
    commands: list[list[str]] = []
    image_id = "sha256:" + "b" * 64

    async def command(argv: list[str], **_kwargs):
        commands.append(argv)
        if argv[1] == "build":
            if not build_succeeds:
                return {
                    **_successful_command(),
                    "exit_code": 1,
                    "stderr": "build failed",
                }
            iidfile = Path(argv[argv.index("--iidfile") + 1])
            iidfile.write_text(image_id, encoding="utf-8")
        if (
            argv[1] == "run"
            and failed_control_candidate is not None
            and f"flocks.code_security.candidate_id={failed_control_candidate}" in argv
            and "flocks.code_security.phase=control" in argv
        ):
            return {**_successful_command(), "exit_code": 1}
        return _successful_command()

    monkeypatch.setattr(runner, "_command", command)
    return runner, commands, completed, image_id


def test_probe_contract_validates_paths_scripts_and_dockerfile(tmp_path: Path) -> None:
    (tmp_path / "Dockerfile").write_text("FROM local/test:latest\n", encoding="utf-8")
    validated = validate_probe(
        _runnable_probe(),
        candidate_id="cand_test",
        snapshot_root=tmp_path,
        snapshot_files={"Dockerfile"},
    )

    assert validated["status"] == "runnable"
    assert validated["dockerfile_path"] == "Dockerfile"

    for field, value, message in (
        ("dockerfile_path", "../Dockerfile", "snapshot-relative"),
        ("dockerfile_path", "dir\\Dockerfile", "canonical"),
        ("dockerfile_path", "/Dockerfile", "snapshot-relative"),
    ):
        probe = _runnable_probe()
        probe[field] = value
        with pytest.raises(ValueError, match=message):
            validate_probe(
                probe,
                candidate_id="cand_test",
                snapshot_root=tmp_path,
                snapshot_files={"Dockerfile"},
            )

    oversized = _runnable_probe()
    oversized["attack"]["script"] = "x" * (16 * 1024 + 1)
    with pytest.raises(ValueError, match="16384-byte"):
        validate_probe(
            oversized,
            candidate_id="cand_test",
            snapshot_root=tmp_path,
            snapshot_files={"Dockerfile"},
        )

    unicode_description = _runnable_probe()
    unicode_description["expected_difference"] = "差" * 4_000
    validated = validate_probe(
        unicode_description,
        candidate_id="cand_test",
        snapshot_root=tmp_path,
        snapshot_files={"Dockerfile"},
    )
    assert len(validated["expected_difference"]) == 4_000
    unicode_description["expected_difference"] += "异"
    with pytest.raises(ValueError, match="4000-character"):
        validate_probe(
            unicode_description,
            candidate_id="cand_test",
            snapshot_root=tmp_path,
            snapshot_files={"Dockerfile"},
        )


@pytest.mark.parametrize(
    "contents",
    (
        "# syntax=docker/dockerfile:1\nFROM local/test\n",
        "# escape=`\nFROM local/test\n",
        "ARG BASE\nFROM $BASE\n",
        "FROM local/test\nADD https://example.test/file /tmp/file\n",
        'FROM local/test\nADD ["https://example.test/file", "/tmp/file"]\n',
        "FROM local/test\nADD git@example.test:repo.git /src\n",
        "FROM local/test\nONBUILD COPY --from=remote/image /src /src\n",
        "FROM local/test\nRUN --mount=type=bind,from=remote/image echo unsafe\n",
        "FROM local/test\nRUN --network=host echo unsafe\n",
        "FROM --platform=linux/amd64 local/test\n",
        "FROM local/test\nCOPY --chown=0 --from=$REMOTE /src /src\n",
        "FROM local/test AS $REMOTE\nCOPY --from=$REMOTE /src /src\n",
        "FROM local/test\nCOPY --parents /src /src\n",
        "FROM local/test\nCOPY <<EOF /tmp/file\ncontents\nEOF\n",
        "FROM local/test\nFUTUREFETCH local/artifact /tmp/file\n",
    ),
)
def test_probe_contract_rejects_unsupported_dockerfile_sources(
    tmp_path: Path,
    contents: str,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError):
        validate_probe(
            _runnable_probe(),
            candidate_id="cand_test",
            snapshot_root=tmp_path,
            snapshot_files={"Dockerfile"},
        )


def test_dockerfile_sources_support_scratch_and_find_copy_flag_sources() -> None:
    assert dynamic_module._dockerfile_base_images("FROM scratch\n") == []
    assert dynamic_module._dockerfile_base_images(
        "FROM local/base AS build\n"
        "RUN printf build\n"
        "FROM scratch\n"
        "COPY --chown=0 --from=build /src /src\n"
        "COPY --link --from=local/artifact:latest /bin/tool /bin/tool\n"
    ) == ["local/base", "local/artifact:latest"]


def test_probe_contract_rejects_symlinked_path_components(tmp_path: Path) -> None:
    real_context = tmp_path / "real"
    real_context.mkdir()
    (real_context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    try:
        (tmp_path / "linked").symlink_to(real_context, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symbolic links are unavailable: {exc}")
    probe = _runnable_probe()
    probe["context_path"] = "linked"
    probe["dockerfile_path"] = "linked/Dockerfile"

    with pytest.raises(ValueError, match="symbolic link"):
        validate_probe(
            probe,
            candidate_id="cand_test",
            snapshot_root=tmp_path,
            snapshot_files={"linked/Dockerfile"},
        )


def test_incremental_dynamic_schema_migration_is_idempotent(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE scans (
                scan_id TEXT PRIMARY KEY,
                parent_session_id TEXT NOT NULL,
                snapshot_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                status TEXT NOT NULL,
                ruleset_digest TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE adjudications (
                scan_id TEXT NOT NULL,
                adjudication_round INTEGER NOT NULL,
                action TEXT NOT NULL,
                accepted_candidate_ids_json TEXT NOT NULL,
                rejected_candidates_json TEXT NOT NULL,
                rescan_json TEXT,
                created_at TEXT NOT NULL,
                PRIMARY KEY (scan_id, adjudication_round)
            );
            """
        )

    store = ScanStore(database)
    store.initialize()
    store.initialize()

    with sqlite3.connect(database) as connection:
        scan_columns = {row[1] for row in connection.execute("PRAGMA table_info(scans)")}
        adjudication_columns = {row[1] for row in connection.execute("PRAGMA table_info(adjudications)")}
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "dynamic_enabled" in scan_columns
    assert "dynamic_assessments_json" in adjudication_columns
    assert "dynamic_runs" in tables


@pytest.mark.asyncio
async def test_command_truncates_logs_without_stopping_drain(tmp_path: Path) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    result = await runner._command(
        [sys.executable, "-c", f"print('x' * {MAX_LOG_BYTES * 2})"],
        timeout_seconds=10,
    )

    assert result["exit_code"] == 0
    assert len(result["stdout"].encode("utf-8")) == MAX_LOG_BYTES
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_command_timeout_runs_targeted_cleanup() -> None:
    removed: list[str] = []

    async def remove_exact_container() -> None:
        removed.append("flocks-exact-container")

    result = await DockerDynamicRunner(SimpleNamespace())._command(
        [sys.executable, "-c", "import time; time.sleep(1)"],
        timeout_seconds=0,
        on_timeout=remove_exact_container,
    )

    assert result["timed_out"] is True
    assert removed == ["flocks-exact-container"]


@pytest.mark.asyncio
async def test_command_finishes_cleanup_after_repeated_cancellation() -> None:
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()

    async def cleanup() -> None:
        cleanup_started.set()
        await asyncio.sleep(0.05)
        cleanup_finished.set()

    runner = DockerDynamicRunner(SimpleNamespace())
    task = asyncio.create_task(
        runner._command(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            timeout_seconds=10,
            on_timeout=cleanup,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    await cleanup_started.wait()
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_finished.is_set()


def test_candidate_preparation_reuses_verified_immutable_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    dockerfile = snapshot / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    data = dockerfile.read_bytes()
    record = SimpleNamespace(
        relative_path="Dockerfile",
        size_bytes=len(data),
        blob_digest=hashlib.sha256(data).hexdigest(),
    )

    class Store:
        def get_scan(self, _scan_id: str):
            return {"snapshot_id": "snap_test"}

        def get_snapshot(self, _snapshot_id: str):
            return SimpleNamespace(
                root_path=str(snapshot),
                snapshot_id="snap_test",
                tree_digest="tree_test",
            )

        def list_snapshot_files(self, _snapshot_id: str):
            return [record]

    runner = DockerDynamicRunner(Store())
    original_verify = runner._verify_context_contents
    verification_count = 0

    def verify_context(*args) -> None:
        nonlocal verification_count
        verification_count += 1
        original_verify(*args)

    monkeypatch.setattr(runner, "_verify_context_contents", verify_context)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    for candidate_id in ("cand_one", "cand_two"):
        runner._prepare_candidate(
            {
                "scan_id": "scan_test",
                "candidate_id": candidate_id,
                "probe": _runnable_probe(candidate_id),
            },
            runtime,
        )

    assert verification_count == 1


@pytest.mark.asyncio
async def test_runner_uses_offline_build_and_hardened_fresh_containers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "Dockerfile").write_text("FROM local/test:latest\n", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    monkeypatch.setattr(dynamic_module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(
        dynamic_module,
        "docker_runtime_dir",
        lambda scan_id: runtime_root / scan_id,
    )

    completed: list[tuple[str, str, dict]] = []
    spans: list[tuple[dict, object]] = []
    ended: list[dict] = []

    class Scope:
        def __init__(self) -> None:
            self.observation = object()

        def end(self, **kwargs) -> None:
            ended.append(kwargs)

    def start_span(**kwargs):
        scope = Scope()
        spans.append((kwargs, scope.observation))
        return scope

    class Store:
        def get_scan(self, scan_id: str):
            return {"scan_id": scan_id, "snapshot_id": "snap_test"}

        def get_snapshot(self, snapshot_id: str):
            return SimpleNamespace(
                root_path=str(snapshot),
                snapshot_id=snapshot_id,
                tree_digest="tree_test",
            )

        def list_snapshot_files(self, snapshot_id: str):
            data = (snapshot / "Dockerfile").read_bytes()
            return [
                SimpleNamespace(
                    relative_path="Dockerfile",
                    size_bytes=len(data),
                    blob_digest=hashlib.sha256(data).hexdigest(),
                )
            ]

        def complete_dynamic_run(self, candidate_id: str, status: str, facts: dict):
            completed.append((candidate_id, status, facts))

    root_observation = object()
    runner = DockerDynamicRunner(Store())
    runner._build_backend = "buildkit"
    commands: list[list[str]] = []
    build_environments: list[dict[str, str] | None] = []
    verification_threads: list[int] = []
    original_verify = runner._verify_context_contents

    def verify_context(*args) -> None:
        verification_threads.append(threading.get_ident())
        original_verify(*args)

    async def command(
        argv: list[str],
        *,
        timeout_seconds: int,
        on_timeout=None,
        env=None,
    ):
        del timeout_seconds, on_timeout
        commands.append(argv)
        if argv[1] == "build":
            build_environments.append(env)
            iidfile = Path(argv[argv.index("--iidfile") + 1])
            iidfile.write_text("sha256:" + "a" * 64, encoding="utf-8")
        return {
            "exit_code": 0,
            "duration_ms": 1,
            "stdout": "",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }

    monkeypatch.setattr(dynamic_module, "span_scope", start_span)
    monkeypatch.setattr(runner, "_command", command)
    monkeypatch.setattr(runner, "_verify_context_contents", verify_context)
    monkeypatch.setenv("BUILDX_BUILDER", "remote-builder")
    monkeypatch.setenv("BUILDKIT_HOST", "tcp://remote-builder.example:1234")
    await runner.run_all(
        [
            {
                "candidate_id": "cand_1234567890ab",
                "scan_id": "scan_1234567890abcdef",
                "status": "ready",
                "probe": _runnable_probe("cand_1234567890ab"),
                "run": None,
            }
        ],
        observation_parent=root_observation,
    )

    build = next(argv for argv in commands if argv[1] == "build")
    runs = [argv for argv in commands if argv[1] == "run"]
    assert ["--network", "none"] == build[
        build.index("--network") : build.index("--network") + 2
    ]
    assert "--pull=false" in build
    assert "--no-cache" in build
    assert build[build.index("--builder") + 1] == "default"
    assert "memory=512m" in build
    assert "memory-swap=512m" in build
    assert "cpu-period=100000" in build
    assert "cpu-quota=100000" in build
    assert build[build.index("--ulimit") + 1] == "nproc=128:128"
    build_environment = build_environments[0]
    assert build_environment is not None
    assert build_environment["DOCKER_BUILDKIT"] == "1"
    assert "BUILDX_BUILDER" not in build_environment
    assert "BUILDKIT_HOST" not in build_environment
    assert len(runs) == 2
    assert (
        runs[0][runs[0].index("--name") + 1]
        != runs[1][runs[1].index("--name") + 1]
    )
    for argv in runs:
        assert argv[argv.index("--network") + 1] == "none"
        assert argv[argv.index("--cap-drop") + 1] == "ALL"
        assert "--read-only" in argv
        assert argv[argv.index("--entrypoint") + 1] == "/bin/sh"
    assert completed[0][1] == "completed"
    assert completed[0][2]["runner_status"] == "completed"
    assert verification_threads and verification_threads[0] != threading.get_ident()
    assert runner._active_scan_ids == set()

    span_names = [kwargs["name"] for kwargs, _observation in spans]
    spans_by_name = {
        kwargs["name"]: (kwargs, observation)
        for kwargs, observation in spans
    }
    expected_spans = {
        "code-security.dynamic.runner",
        "code-security.dynamic.build_group",
        "code-security.dynamic.candidate",
        "code-security.dynamic.base_image_check",
        "code-security.dynamic.build",
        "code-security.dynamic.control",
        "code-security.dynamic.attack",
        "code-security.dynamic.image_cleanup",
        "code-security.dynamic.cleanup",
    }
    assert expected_spans <= spans_by_name.keys()
    assert all(span_names.count(name) == 1 for name in expected_spans)
    runner_observation = spans_by_name["code-security.dynamic.runner"][1]
    build_group = spans_by_name["code-security.dynamic.build_group"]
    candidate = spans_by_name["code-security.dynamic.candidate"]
    assert spans_by_name["code-security.dynamic.runner"][0]["parent"] is root_observation
    assert build_group[0]["parent"] is runner_observation
    assert candidate[0]["parent"] is build_group[1]
    for name in (
        "code-security.dynamic.base_image_check",
        "code-security.dynamic.build",
        "code-security.dynamic.image_cleanup",
    ):
        assert spans_by_name[name][0]["parent"] is build_group[1]
    for name in (
        "code-security.dynamic.control",
        "code-security.dynamic.attack",
    ):
        assert spans_by_name[name][0]["parent"] is candidate[1]

    trace_payload = repr([kwargs for kwargs, _ in spans]) + repr(ended)
    assert "printf control" not in trace_payload
    assert "printf attack" not in trace_payload
    assert "stdout" not in trace_payload
    assert "stderr" not in trace_payload
    assert str(snapshot) not in trace_payload


@pytest.mark.asyncio
async def test_runner_reuses_one_image_for_matching_build_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, commands, completed, image_id = _shared_image_runner(
        tmp_path,
        monkeypatch,
    )
    candidate_ids = (
        "cand_shared_000000000001",
        "cand_shared_000000000002",
    )
    await runner.run_all(
        [
            {
                "candidate_id": candidate_id,
                "scan_id": "scan_shared",
                "status": "ready",
                "probe": _runnable_probe(candidate_id),
                "run": None,
            }
            for candidate_id in candidate_ids
        ]
    )

    builds = [argv for argv in commands if argv[1] == "build"]
    runs = [argv for argv in commands if argv[1] == "run"]
    removals = [argv for argv in commands if argv[1:3] == ["image", "rm"]]
    assert len(builds) == 1
    assert len(runs) == 4
    assert len(removals) == 1
    build_labels = [
        builds[0][index + 1]
        for index, value in enumerate(builds[0])
        if value == "--label"
    ]
    assert "flocks.code_security.scan_id=scan_shared" in build_labels
    assert any(label.startswith("flocks.code_security.build_id=") for label in build_labels)
    assert not any("candidate_id=" in label for label in build_labels)
    assert all(image_id in argv for argv in runs)
    run_labels = {
        value
        for argv in runs
        for value in argv
        if value.startswith("flocks.code_security.candidate_id=")
    }
    assert run_labels == {
        f"flocks.code_security.candidate_id={candidate_id}"
        for candidate_id in candidate_ids
    }
    assert {item[0] for item in completed} == set(candidate_ids)
    assert all(item[1] == "completed" for item in completed)
    assert {item[2]["image_id"] for item in completed} == {image_id}
    assert len({item[2]["build_id"] for item in completed}) == 1


@pytest.mark.asyncio
async def test_shared_build_failure_marks_every_candidate_inconclusive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, commands, completed, _image_id = _shared_image_runner(
        tmp_path,
        monkeypatch,
        build_succeeds=False,
    )
    candidate_ids = ("cand_failed_one", "cand_failed_two")
    await runner.run_all(
        [
            {
                "candidate_id": candidate_id,
                "scan_id": "scan_failed",
                "status": "ready",
                "probe": _runnable_probe(candidate_id),
                "run": None,
            }
            for candidate_id in candidate_ids
        ]
    )

    assert len([argv for argv in commands if argv[1] == "build"]) == 1
    assert not any(argv[1] == "run" for argv in commands)
    assert {item[0] for item in completed} == set(candidate_ids)
    assert all(item[1] == "inconclusive" for item in completed)
    assert all(item[2]["failed_phase"] == "build" for item in completed)
    assert len({item[2]["build_id"] for item in completed}) == 1


@pytest.mark.asyncio
async def test_candidate_failure_does_not_stop_shared_image_siblings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failed_candidate = "cand_shared_000000000001"
    successful_candidate = "cand_shared_000000000002"
    runner, commands, completed, _image_id = _shared_image_runner(
        tmp_path,
        monkeypatch,
        failed_control_candidate=failed_candidate,
    )
    await runner.run_all(
        [
            {
                "candidate_id": candidate_id,
                "scan_id": "scan_shared",
                "status": "ready",
                "probe": _runnable_probe(candidate_id),
                "run": None,
            }
            for candidate_id in (failed_candidate, successful_candidate)
        ]
    )

    assert len([argv for argv in commands if argv[1] == "build"]) == 1
    assert len([argv for argv in commands if argv[1] == "run"]) == 3
    status_by_candidate = {candidate_id: status for candidate_id, status, _ in completed}
    assert status_by_candidate == {
        failed_candidate: "inconclusive",
        successful_candidate: "completed",
    }


@pytest.mark.asyncio
async def test_runner_separates_different_build_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    shared = _build_identity()
    distinct = _build_identity(
        context_path="service",
        dockerfile_path="service/Dockerfile",
    )
    prepared = [
        SimpleNamespace(candidate_id="cand_one", build_identity=shared),
        SimpleNamespace(candidate_id="cand_two", build_identity=shared),
        SimpleNamespace(candidate_id="cand_three", build_identity=distinct),
    ]
    groups: list[set[str]] = []

    async def prepare_runs(_runs: list[dict]) -> list[SimpleNamespace]:
        return prepared

    async def run_build_group(
        candidates: list[SimpleNamespace],
        *,
        semaphore: asyncio.Semaphore,
        observation_parent=None,
    ) -> None:
        del semaphore, observation_parent
        groups.append({candidate.candidate_id for candidate in candidates})

    async def cleanup(*, observation_parent=None) -> None:
        del observation_parent

    monkeypatch.setattr(runner, "_prepare_runs", prepare_runs)
    monkeypatch.setattr(runner, "_run_build_group", run_build_group)
    monkeypatch.setattr(runner, "cleanup", cleanup)

    await runner.run_all([{}, {}, {}])

    assert {frozenset(group) for group in groups} == {
        frozenset({"cand_one", "cand_two"}),
        frozenset({"cand_three"}),
    }


@pytest.mark.asyncio
async def test_runner_cancels_siblings_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    both_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    cleaned = asyncio.Event()
    started = 0
    cleanup_calls = 0

    identities = [
        _build_identity(dockerfile_path=f"Dockerfile.{index}")
        for index in range(2)
    ]
    prepared = [
        SimpleNamespace(candidate_id=candidate_id, build_identity=identity)
        for candidate_id, identity in zip(
            ("cand_failure", "cand_sibling"),
            identities,
            strict=True,
        )
    ]

    async def prepare_runs(_runs: list[dict]) -> list[SimpleNamespace]:
        return prepared

    async def run_build_group(
        candidates: list[SimpleNamespace],
        *,
        semaphore: asyncio.Semaphore,
        observation_parent=None,
    ) -> None:
        del semaphore, observation_parent
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        if candidates[0].candidate_id == "cand_failure":
            raise RuntimeError("candidate failed")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def cleanup(*, observation_parent=None) -> None:
        del observation_parent
        nonlocal cleanup_calls
        cleanup_calls += 1
        assert sibling_cancelled.is_set()
        cleaned.set()

    monkeypatch.setattr(runner, "_prepare_runs", prepare_runs)
    monkeypatch.setattr(runner, "_run_build_group", run_build_group)
    monkeypatch.setattr(runner, "cleanup", cleanup)

    with pytest.raises(ExceptionGroup, match="unhandled errors in a TaskGroup"):
        await runner.run_all(
            [
                {"candidate_id": "cand_failure"},
                {"candidate_id": "cand_sibling"},
            ]
        )

    assert cleaned.is_set()
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_runner_finishes_cleanup_after_repeated_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    candidate_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    cleanup_finished = asyncio.Event()
    cleanup_calls = 0

    identity = _build_identity()
    prepared = SimpleNamespace(candidate_id="cand_test", build_identity=identity)

    async def prepare_runs(_runs: list[dict]) -> list[SimpleNamespace]:
        return [prepared]

    async def run_build_group(
        _candidates: list[SimpleNamespace],
        *,
        semaphore: asyncio.Semaphore,
        observation_parent=None,
    ) -> None:
        del semaphore, observation_parent
        candidate_started.set()
        await asyncio.Event().wait()

    async def cleanup(*, observation_parent=None) -> None:
        del observation_parent
        nonlocal cleanup_calls
        cleanup_calls += 1
        cleanup_started.set()
        await asyncio.sleep(0.05)
        cleanup_finished.set()

    monkeypatch.setattr(runner, "_prepare_runs", prepare_runs)
    monkeypatch.setattr(runner, "_run_build_group", run_build_group)
    monkeypatch.setattr(runner, "cleanup", cleanup)

    task = asyncio.create_task(runner.run_all([{"candidate_id": "cand_test"}]))
    await candidate_started.wait()
    task.cancel()
    await cleanup_started.wait()
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_finished.is_set()
    assert cleanup_calls == 1


@pytest.mark.asyncio
async def test_preflight_pins_local_default_resource_limited_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    ended: list[dict] = []

    class Scope:
        observation = object()

        def end(self, **kwargs) -> None:
            ended.append(kwargs)

    async def command(
        argv: list[str],
        *,
        timeout_seconds: int,
        on_timeout=None,
        env=None,
    ):
        del timeout_seconds, on_timeout
        calls.append((argv, env))
        stdout = ""
        if argv[1:3] == ["context", "inspect"]:
            stdout = json.dumps("unix:///var/run/docker.sock")
        elif argv[1] == "version":
            stdout = "Docker version"
        elif argv[1:3] == ["buildx", "ls"]:
            stdout = json.dumps({"Name": "default", "Driver": "docker"})
        elif argv[1:] == ["build", "--help"]:
            stdout = "\n".join(
                ("--builder string", "--resource list", "--shm-size bytes", "--ulimit")
            )
        return _successful_command(stdout)

    monkeypatch.setattr(dynamic_module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(dynamic_module, "span_scope", lambda **_kwargs: Scope())
    monkeypatch.setattr(runner, "_command", command)
    monkeypatch.setenv("BUILDX_BUILDER", "remote-builder")
    monkeypatch.setenv("BUILDKIT_HOST", "tcp://remote-builder.example:1234")

    await runner.preflight(observation_parent=object())

    assert runner._build_backend == "buildkit"
    builder_call = next(call for call in calls if call[0][1:3] == ["buildx", "ls"])
    assert builder_call[0][3:] == ["--format", "{{json .}}"]
    builder_environment = builder_call[1]
    assert builder_environment is not None
    assert builder_environment["DOCKER_BUILDKIT"] == "1"
    assert "BUILDX_BUILDER" not in builder_environment
    assert "BUILDKIT_HOST" not in builder_environment
    assert ended == [{"output": {"status": "passed", "build_backend": "buildkit"}}]


@pytest.mark.asyncio
async def test_preflight_rejects_nonlocal_default_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())

    async def command(argv: list[str], **_kwargs):
        if argv[1:3] == ["context", "inspect"]:
            return _successful_command(json.dumps("unix:///var/run/docker.sock"))
        if argv[1] == "version":
            return _successful_command("Docker version")
        if argv[1:] == ["build", "--help"]:
            return _successful_command(
                "\n".join(
                    ("--builder string", "--resource list", "--shm-size bytes", "--ulimit")
                )
            )
        return _successful_command(
            json.dumps({"Name": "default", "Driver": "docker-container"})
        )

    monkeypatch.setattr(dynamic_module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(runner, "_command", command)

    with pytest.raises(RuntimeError, match="local default Docker builder"):
        await runner.preflight()
    assert runner._build_backend is None


@pytest.mark.asyncio
async def test_build_backend_falls_back_to_resource_limited_legacy_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    environments: list[dict[str, str]] = []

    async def command(argv: list[str], *, timeout_seconds: int, env=None):
        del argv, timeout_seconds
        environments.append(env)
        stdout = "Build options"
        if env["DOCKER_BUILDKIT"] == "0":
            stdout = "\n".join(
                (
                    "-m, --memory",
                    "--memory-swap",
                    "--cpu-period",
                    "--cpu-quota",
                    "--shm-size",
                    "--ulimit",
                )
            )
        return _successful_command(stdout)

    monkeypatch.setattr(runner, "_command", command)

    assert await runner._select_build_backend("docker") == "legacy"
    assert [env["DOCKER_BUILDKIT"] for env in environments] == ["1", "0"]
    legacy_limits = runner._build_limit_arguments("legacy")
    assert legacy_limits[legacy_limits.index("--memory") + 1] == "512m"
    assert legacy_limits[legacy_limits.index("--memory-swap") + 1] == "512m"
    assert legacy_limits[legacy_limits.index("--cpu-period") + 1] == "100000"
    assert legacy_limits[legacy_limits.index("--cpu-quota") + 1] == "100000"
    assert "--builder" not in legacy_limits


@pytest.mark.asyncio
async def test_preflight_rejects_remote_docker_host(monkeypatch: pytest.MonkeyPatch) -> None:
    spans: list[dict] = []
    ended: list[dict] = []

    class Scope:
        observation = object()

        def end(self, **kwargs) -> None:
            ended.append(kwargs)

    def start_span(**kwargs):
        spans.append(kwargs)
        return Scope()

    monkeypatch.setattr(dynamic_module.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(dynamic_module, "span_scope", start_span)
    remote_endpoint = "tcp://private-remote.example:2376"
    monkeypatch.setenv("DOCKER_HOST", remote_endpoint)
    with pytest.raises(RuntimeError, match="Remote Docker"):
        await DockerDynamicRunner(SimpleNamespace()).preflight(
            observation_parent=object()
        )

    assert spans[0]["name"] == "code-security.dynamic.preflight"
    trace_payload = repr(spans) + repr(ended)
    assert remote_endpoint not in trace_payload
    assert ended[0]["level"] == "ERROR"
    assert ended[0]["status_message"] == "RuntimeError"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "expected_status"),
    (
        (
            {
                "exit_code": 7,
                "duration_ms": 1,
                "stdout": "sensitive stdout",
                "stderr": "sensitive stderr",
                "timed_out": False,
                "truncated": False,
            },
            "failed",
        ),
        (
            {
                "exit_code": None,
                "duration_ms": 1,
                "stdout": "sensitive stdout",
                "stderr": "sensitive stderr",
                "timed_out": True,
                "truncated": False,
            },
            "timeout",
        ),
    ),
)
async def test_observed_command_marks_nonzero_and_timeout_as_errors(
    monkeypatch: pytest.MonkeyPatch,
    result: dict,
    expected_status: str,
) -> None:
    ended: list[dict] = []

    class Scope:
        observation = object()

        def end(self, **kwargs) -> None:
            ended.append(kwargs)

    async def command(*_args, **_kwargs):
        return result

    monkeypatch.setattr(dynamic_module, "span_scope", lambda **_kwargs: Scope())
    runner = DockerDynamicRunner(SimpleNamespace())
    monkeypatch.setattr(runner, "_command", command)

    returned = await runner._observed_command(
        object(),
        "build",
        ["docker", "build", "sensitive-argument"],
        timeout_seconds=1,
    )

    assert returned is result
    assert ended == [
        {
            "output": {
                "status": expected_status,
                "exit_code": result["exit_code"],
                "duration_ms": 1,
                "timed_out": result["timed_out"],
                "truncated": False,
            },
            "level": "ERROR",
            "status_message": expected_status,
        }
    ]
    assert "sensitive" not in repr(ended)


@pytest.mark.asyncio
@pytest.mark.docker
@pytest.mark.skipif(
    os.environ.get("FLOCKS_TEST_DOCKER") != "1",
    reason="set FLOCKS_TEST_DOCKER=1 to run Docker integration tests",
)
async def test_local_docker_builds_scratch_without_network(tmp_path: Path) -> None:
    docker = shutil.which("docker")
    if docker is None:
        pytest.fail("FLOCKS_TEST_DOCKER=1 requires the Docker CLI")
    (tmp_path / "Dockerfile").write_text(
        'FROM scratch\nLABEL flocks.test="dynamic-validation"\n',
        encoding="utf-8",
    )
    iidfile = tmp_path / "image.iid"
    runner = DockerDynamicRunner(SimpleNamespace())
    await runner.preflight()
    assert runner._build_backend is not None
    build = await runner._command(
        [
            docker,
            "build",
            *runner._build_limit_arguments(runner._build_backend),
            "--shm-size",
            "64m",
            "--ulimit",
            "nproc=128:128",
            "--network",
            "none",
            "--pull=false",
            "--iidfile",
            str(iidfile),
            str(tmp_path),
        ],
        timeout_seconds=60,
        env=runner._build_environment(runner._build_backend),
    )
    assert build["exit_code"] == 0, build["stderr"]
    image_id = iidfile.read_text(encoding="utf-8").strip()
    try:
        assert re.fullmatch(r"sha256:[a-f0-9]{64}", image_id)
    finally:
        await runner._command(
            [docker, "image", "rm", "-f", image_id],
            timeout_seconds=30,
        )
