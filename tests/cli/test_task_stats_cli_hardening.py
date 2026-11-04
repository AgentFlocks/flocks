"""Regression tests for `flocks task` / `flocks stats` defects found in the 2026-09 full test pass.

Covers:
- every ``flocks task`` command releases TaskStore/Storage so the process exits (it used to hang
  at interpreter shutdown on aiosqlite's non-daemon worker thread after printing its output)
- ``task list`` rejects ``-n 0`` / negative limits and unknown ``--format`` values
- ``task create`` rejects unknown ``--priority`` / ``--mode`` values with the allowed choices
- ``task retry`` refuses executions that are not failed
- ``task queue --pause/--resume`` drive the running service's queue API (a no-op in-process
  pause is not reported as success); without a service the command fails clearly
- ``stats --days`` rejects negative values
"""

from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest
from typer.testing import CliRunner

import flocks.cli.commands.task as task_cmd
from flocks.cli.commands.stats import stats_app

runner = CliRunner(mix_stderr=False)


def _flat(text: str) -> str:
    """Collapse Rich's 80-column soft wrapping (and box borders) for substring checks."""
    return " ".join(text.replace("│", " ").split())


async def _noop_init() -> None:
    return None


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("FLOCKS_SERVER_URL", raising=False)


# --------------------------------------------------------------------------- process exit
@pytest.mark.slow
def test_task_list_process_exits_after_printing(tmp_path) -> None:
    env = dict(os.environ)
    env["FLOCKS_ROOT"] = str(tmp_path / "root")
    env.pop("FLOCKS_SERVER_URL", None)
    proc = subprocess.run(
        [sys.executable, "-m", "flocks.cli.main", "task", "list"],
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    assert "Tasks (0 total)" in proc.stdout


async def test_with_store_cleanup_closes_stores_even_on_exit(monkeypatch) -> None:
    closed: list[str] = []

    async def fake_task_close():
        closed.append("task_store")

    async def fake_storage_shutdown():
        closed.append("storage")

    monkeypatch.setattr("flocks.task.store.TaskStore.close", fake_task_close)
    monkeypatch.setattr(task_cmd.Storage, "shutdown", fake_storage_shutdown)

    async def command():
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        await task_cmd._with_store_cleanup(command())

    assert closed == ["task_store", "storage"]


# --------------------------------------------------------------------------- option validation
@pytest.mark.parametrize("args, fragment", [
    (["list", "-n", "0"], "x>=1"),
    (["list", "-n", "-5"], "x>=1"),
    (["list", "--format", "xml"], "table | json"),
    (["create", "x", "--priority", "critical"], "urgent | high | normal | low"),
    (["create", "x", "--mode", "shell"], "agent | workflow"),
])
def test_task_options_are_validated_before_touching_the_store(monkeypatch, args, fragment) -> None:
    async def _boom():
        raise AssertionError("store must not be opened for invalid options")

    monkeypatch.setattr(task_cmd.Storage, "init", _boom)

    result = runner.invoke(task_cmd.task_app, args)

    assert result.exit_code == 2, result.output
    assert fragment in _flat(result.stderr)
    assert "Traceback" not in result.output


def test_stats_days_rejects_negative_values(monkeypatch) -> None:
    result = runner.invoke(stats_app, ["-d", "-3"])

    assert result.exit_code == 2
    assert "x>=0" in result.stderr


# --------------------------------------------------------------------------- retry
def test_task_retry_refuses_non_failed_execution(monkeypatch) -> None:
    from flocks.task.models import TaskStatus

    completed = SimpleNamespace(id="txe_done", title="Done", status=TaskStatus.COMPLETED)
    calls: list[str] = []

    async def fake_get_execution(execution_id: str):
        return completed

    async def fake_retry(execution_id: str):
        calls.append(execution_id)
        return completed

    monkeypatch.setattr(task_cmd.Storage, "init", _noop_init)
    monkeypatch.setattr("flocks.task.store.TaskStore.init", _noop_init)
    monkeypatch.setattr("flocks.task.store.TaskStore.close", _noop_init)
    monkeypatch.setattr(task_cmd.Storage, "shutdown", _noop_init)
    monkeypatch.setattr("flocks.task.manager.TaskManager.get_execution", fake_get_execution)
    monkeypatch.setattr("flocks.task.manager.TaskManager.retry_execution", fake_retry)

    result = runner.invoke(task_cmd.task_app, ["retry", "txe_done"])

    assert result.exit_code == 1
    assert "Task txe_done not found or not failed" in result.stdout
    assert calls == []


# --------------------------------------------------------------------------- queue control
def test_task_queue_pause_requires_running_service(monkeypatch) -> None:
    monkeypatch.setattr(task_cmd, "_service_base_url", lambda: None)
    monkeypatch.setattr(task_cmd.Storage, "shutdown", _noop_init)
    monkeypatch.setattr("flocks.task.store.TaskStore.close", _noop_init)

    result = runner.invoke(task_cmd.task_app, ["queue", "--pause"])

    assert result.exit_code == 1
    assert "Queue control requires the running Flocks service" in _flat(result.stdout)


def test_task_queue_pause_and_status_use_service_api(monkeypatch) -> None:
    requests: list[tuple[str, str, str | None]] = []
    paused = {"value": False}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    async def fake_request(self, method, path, headers=None):
        requests.append((method, path, (headers or {}).get("Authorization")))
        if path.endswith("/pause"):
            paused["value"] = True
            return _FakeResponse({"paused": True})
        if path.endswith("/resume"):
            paused["value"] = False
            return _FakeResponse({"paused": False})
        return _FakeResponse({"paused": paused["value"], "max_concurrent": 4, "running": 0, "queued": 2})

    monkeypatch.setenv("FLOCKS_SERVER_URL", "http://127.0.0.1:65500/")
    monkeypatch.setattr(httpx.AsyncClient, "request", fake_request)
    monkeypatch.setattr(task_cmd, "_service_api_headers", lambda: {"Authorization": "Bearer tok"})
    monkeypatch.setattr(task_cmd.Storage, "shutdown", _noop_init)
    monkeypatch.setattr("flocks.task.store.TaskStore.close", _noop_init)

    pause = runner.invoke(task_cmd.task_app, ["queue", "--pause"])
    status = runner.invoke(task_cmd.task_app, ["queue"])
    both = runner.invoke(task_cmd.task_app, ["queue", "--pause", "--resume"])
    resume = runner.invoke(task_cmd.task_app, ["queue", "--resume"])

    assert pause.exit_code == 0 and "Queue paused" in pause.stdout
    assert status.exit_code == 0 and "Paused:         True" in status.stdout and "Queued:         2" in status.stdout
    assert "Queue paused" in both.stdout and "resumed" not in both.stdout
    assert resume.exit_code == 0 and "Queue resumed" in resume.stdout
    assert [r[:2] for r in requests] == [
        ("POST", "/api/task-system/queue/pause"),
        ("GET", "/api/task-system/queue/status"),
        ("POST", "/api/task-system/queue/pause"),
        ("POST", "/api/task-system/queue/resume"),
    ]
    assert all(r[2] == "Bearer tok" for r in requests)


def test_service_base_url_prefers_explicit_env(monkeypatch) -> None:
    monkeypatch.setenv("FLOCKS_SERVER_URL", "http://10.0.0.5:5173/")
    assert task_cmd._service_base_url() == "http://10.0.0.5:5173"

    monkeypatch.delenv("FLOCKS_SERVER_URL")

    def fake_status(*, paths=None, timeout=2.0):
        return SimpleNamespace(backend=SimpleNamespace(state="healthy", host="0.0.0.0", port=6174))

    monkeypatch.setattr("flocks.cli.service_control.read_supervisor_status", fake_status)
    assert task_cmd._service_base_url() == "http://127.0.0.1:6174"
