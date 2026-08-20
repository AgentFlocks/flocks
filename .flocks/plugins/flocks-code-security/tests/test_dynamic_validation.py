from __future__ import annotations

import asyncio
import hashlib
import sqlite3
import sys
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


def test_probe_contract_rejects_remote_or_dynamic_dockerfile_sources(tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    for contents in (
        "# syntax=docker/dockerfile:1\nFROM local/test\n",
        "ARG BASE\nFROM $BASE\n",
        "FROM local/test\nADD https://example.test/file /tmp/file\n",
    ):
        dockerfile.write_text(contents, encoding="utf-8")
        with pytest.raises(ValueError):
            validate_probe(
                _runnable_probe(),
                candidate_id="cand_test",
                snapshot_root=tmp_path,
                snapshot_files={"Dockerfile"},
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
            return SimpleNamespace(root_path=str(snapshot), snapshot_id=snapshot_id)

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
    runner = DockerDynamicRunner(
        Store(),
        observation_parent=root_observation,
    )
    commands: list[list[str]] = []

    async def command(argv: list[str], *, timeout_seconds: int, on_timeout=None):
        del timeout_seconds, on_timeout
        commands.append(argv)
        if argv[1] == "build":
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
    await runner.run_all(
        [
            {
                "candidate_id": "cand_1234567890ab",
                "scan_id": "scan_1234567890abcdef",
                "status": "ready",
                "probe": _runnable_probe("cand_1234567890ab"),
                "run": None,
            }
        ]
    )

    build = next(argv for argv in commands if argv[1] == "build")
    runs = [argv for argv in commands if argv[1] == "run"]
    assert ["--network", "none"] == build[
        build.index("--network") : build.index("--network") + 2
    ]
    assert "--pull=false" in build
    assert "--no-cache" in build
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

    spans_by_name = {
        kwargs["name"]: (kwargs, observation)
        for kwargs, observation in spans
    }
    expected_spans = {
        "code-security.dynamic.runner",
        "code-security.dynamic.candidate",
        "code-security.dynamic.base_image_check",
        "code-security.dynamic.build",
        "code-security.dynamic.control",
        "code-security.dynamic.attack",
        "code-security.dynamic.image_cleanup",
        "code-security.dynamic.cleanup",
    }
    assert expected_spans <= spans_by_name.keys()
    runner_observation = spans_by_name["code-security.dynamic.runner"][1]
    candidate = spans_by_name["code-security.dynamic.candidate"]
    assert spans_by_name["code-security.dynamic.runner"][0]["parent"] is root_observation
    assert candidate[0]["parent"] is runner_observation
    for name in (
        "code-security.dynamic.base_image_check",
        "code-security.dynamic.build",
        "code-security.dynamic.control",
        "code-security.dynamic.attack",
        "code-security.dynamic.image_cleanup",
    ):
        assert spans_by_name[name][0]["parent"] is candidate[1]

    trace_payload = repr([kwargs for kwargs, _ in spans]) + repr(ended)
    assert "printf control" not in trace_payload
    assert "printf attack" not in trace_payload
    assert "stdout" not in trace_payload
    assert "stderr" not in trace_payload
    assert str(snapshot) not in trace_payload


@pytest.mark.asyncio
async def test_runner_cancels_siblings_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DockerDynamicRunner(SimpleNamespace())
    both_started = asyncio.Event()
    sibling_cancelled = asyncio.Event()
    cleaned = asyncio.Event()
    started = 0

    async def run_candidate(run: dict, *, observation_parent=None) -> None:
        del observation_parent
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        if run["candidate_id"] == "cand_failure":
            raise RuntimeError("candidate failed")
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            sibling_cancelled.set()
            raise

    async def cleanup(*, observation_parent=None) -> None:
        del observation_parent
        assert sibling_cancelled.is_set()
        cleaned.set()

    monkeypatch.setattr(runner, "_run_candidate", run_candidate)
    monkeypatch.setattr(runner, "cleanup", cleanup)

    with pytest.raises(ExceptionGroup, match="unhandled errors in a TaskGroup"):
        await runner.run_all(
            [
                {"candidate_id": "cand_failure"},
                {"candidate_id": "cand_sibling"},
            ]
        )

    assert cleaned.is_set()


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
        await DockerDynamicRunner(
            SimpleNamespace(),
            observation_parent=object(),
        ).preflight()

    assert spans[0]["name"] == "code-security.dynamic.preflight"
    trace_payload = repr(spans) + repr(ended)
    assert remote_endpoint not in trace_payload
    assert ended[0]["level"] == "ERROR"
    assert ended[0]["status_message"] == "RuntimeError"
