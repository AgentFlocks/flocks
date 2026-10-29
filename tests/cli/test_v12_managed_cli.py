"""In a managed install the runtime is the supervised service: CLI commands
that would run their own copy of it refuse or attach instead."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import flocks.cli.main as cli_main
from flocks.updater import deploy

runner = CliRunner()


@pytest.fixture
def runtime_root(monkeypatch, tmp_path: Path) -> Path:
    root = tmp_path / "flocks-root"
    (root / "config").mkdir(parents=True)
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
    monkeypatch.delenv("FLOCKS_OFFLINE_INSTALL", raising=False)
    deploy.detect_deploy_mode.cache_clear()
    yield root
    deploy.detect_deploy_mode.cache_clear()


def _declare(root: Path, mode: str) -> None:
    (root / "config" / "deployment.json").write_text(json.dumps({"mode": mode}), encoding="utf-8")
    deploy.detect_deploy_mode.cache_clear()


# ------------------------------------------------------------------ the signal
def test_declared_managed_reads_only_explicit_signals(monkeypatch, runtime_root) -> None:
    from flocks import license as license_mod

    class _Extension:
        @classmethod
        async def is_active(cls, feature=None):
            return True

    # A license extension declares nothing: a console-installed commercial
    # component is not supervised, and its upgrades are not refused either.
    monkeypatch.setattr(license_mod._LicenseService, "_checker", _Extension)
    monkeypatch.setattr(deploy.Path, "exists", lambda self: False)
    assert deploy.detect_deploy_mode() == "source"
    assert deploy.declared_managed() is False


def test_declared_managed_by_env_or_descriptor(monkeypatch, runtime_root) -> None:
    assert deploy.declared_managed() is False
    _declare(runtime_root, "managed")
    assert deploy.declared_managed() is True
    _declare(runtime_root, "source")
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    deploy.detect_deploy_mode.cache_clear()
    assert deploy.declared_managed() is True


@pytest.mark.parametrize(
    "override",
    [{"FLOCKS_DEPLOY_MODE": "source"}, {"FLOCKS_DEPLOY_MODE": "docker"}, {"FLOCKS_OFFLINE_INSTALL": "1"}],
)
def test_env_override_does_not_unmanage_a_declared_install(monkeypatch, runtime_root, override) -> None:
    """A variable set in a shell must not talk a managed install out of its supervisor.

    detect_deploy_mode() honours the override (it decides how upgrades are
    delivered); the security question does not.
    """
    _declare(runtime_root, "managed")
    for name, value in override.items():
        monkeypatch.setenv(name, value)
    deploy.detect_deploy_mode.cache_clear()

    assert deploy.detect_deploy_mode() != "managed"
    assert deploy.declared_managed() is True


# ------------------------------------------------------------------ flocks run
def test_run_refuses_in_a_managed_install(runtime_root, monkeypatch) -> None:
    _declare(runtime_root, "managed")
    started = []
    monkeypatch.setattr("flocks.cli.session_runner.run_session", lambda **kwargs: started.append(kwargs))

    result = runner.invoke(cli_main.app, ["run", "hello"])

    assert result.exit_code == 2
    assert "managed install" in result.output
    assert started == []


# ------------------------------------------------------------------ flocks tui
@pytest.fixture
def no_real_processes(monkeypatch):
    calls = SimpleNamespace(popen=[], run=[])

    def _fake_run(cmd, *args, **kwargs):
        calls.run.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"1.0", stderr=b"")

    def _fake_popen(cmd, *args, **kwargs):  # pragma: no cover - must not be reached when managed
        calls.popen.append(cmd)
        raise AssertionError("a managed install must not start its own server")

    monkeypatch.setattr("subprocess.run", _fake_run)
    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    return calls


def test_tui_attaches_to_the_managed_service(runtime_root, monkeypatch, no_real_processes, tmp_path) -> None:
    _declare(runtime_root, "managed")
    monkeypatch.setattr("httpx.get", lambda url, **kwargs: SimpleNamespace(status_code=200))

    result = runner.invoke(cli_main.app, ["tui", "--port", "5173", "--directory", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert no_real_processes.popen == []
    attach = [cmd for cmd in no_real_processes.run if "attach" in cmd]
    assert attach and "http://127.0.0.1:5173" in attach[0]


def test_tui_refuses_when_the_managed_service_is_not_answering(runtime_root, monkeypatch, no_real_processes, tmp_path) -> None:
    _declare(runtime_root, "managed")

    def _down(url, **kwargs):
        raise ConnectionRefusedError("nothing listening")

    monkeypatch.setattr("httpx.get", _down)

    result = runner.invoke(cli_main.app, ["tui", "--port", "5999", "--directory", str(tmp_path)])

    assert result.exit_code == 1
    assert "No Flocks service answered" in result.output
    assert no_real_processes.popen == []


# ------------------------------------------------------------------ flocks acp
def test_acp_refuses_in_a_managed_install(runtime_root) -> None:
    from flocks.cli.commands.acp import acp_app

    _declare(runtime_root, "managed")
    result = runner.invoke(acp_app, [])

    assert result.exit_code == 2
    assert "managed install" in result.output


# ------------------------------------------------- every other local runtime
@pytest.mark.parametrize(
    "argv",
    [
        ["exec", "hello"],
        ["session", "new", "hello"],
        ["session", "resume", "--last", "--prompt", "hello"],
        ["mcp", "serve"],
        ["workflow", "run", "some-workflow"],
    ],
)
def test_local_runtimes_refuse_in_a_managed_install(runtime_root, monkeypatch, argv) -> None:
    """Agent turns, workflow runs and the MCP server all run tools in this
    process: none of them starts beside the supervised service."""
    _declare(runtime_root, "managed")
    started = []

    async def _headless(options, emit=None):
        started.append("headless")
        return {"type": "result", "success": True}

    monkeypatch.setattr("flocks.cli.headless.run_headless", _headless)
    monkeypatch.setattr("flocks.cli.commands.exec.run_headless", _headless, raising=False)
    monkeypatch.setattr("flocks.cli.mcp_server.create_server", lambda *a, **k: started.append("mcp"))
    monkeypatch.setattr("flocks.workflow.runner.run_workflow", lambda **k: started.append("workflow"))

    result = runner.invoke(cli_main.app, argv)

    assert result.exit_code == 2, result.output
    assert "managed install" in result.output
    assert started == []


async def test_the_headless_runner_itself_refuses_in_a_managed_install(runtime_root, tmp_path) -> None:
    from flocks.cli.headless import ExecOptions, run_headless
    from flocks.session.runtime_controls import RuntimeControls

    _declare(runtime_root, "managed")

    result = await run_headless(ExecOptions(prompt="hello", directory=tmp_path, controls=RuntimeControls()))

    assert result["success"] is False
    assert "managed install" in result["error"]
