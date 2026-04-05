import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from typer.testing import CliRunner

import flocks.cli.main as cli_main

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def _help_contains_command(help_output: str, command: str) -> bool:
    pattern = rf"^\s*│\s+{re.escape(command)}\s{{2,}}"
    return re.search(pattern, help_output, re.MULTILINE) is not None


def test_cli_help_lists_service_commands(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    result = runner.invoke(cli_main.app, ["--help"])

    assert result.exit_code == 0
    for command in ("start", "stop", "restart", "status", "logs", "session", "mcp", "task", "skills"):
        assert _help_contains_command(result.stdout, command)
    for command in ("agent", "acp", "debug", "run", "serve", "auth", "models"):
        assert not _help_contains_command(result.stdout, command)


def test_start_command_dispatches_to_service_manager(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    captured = {}

    def fake_start_all(config, console) -> None:
        captured["config"] = config
        captured["console"] = console

    monkeypatch.setattr(cli_main, "start_all", fake_start_all)

    result = runner.invoke(cli_main.app, ["start", "--no-browser", "--skip-webui-build"])

    assert result.exit_code == 0
    assert captured["config"].no_browser is True
    assert captured["config"].skip_frontend_build is True


def test_service_errors_exit_with_code_one(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    monkeypatch.setattr(cli_main, "show_status", lambda *_args: (_ for _ in ()).throw(cli_main.ServiceError("boom")))

    result = runner.invoke(cli_main.app, ["status"])

    assert result.exit_code == 1
    assert "boom" in result.stdout


def test_logs_command_passes_selection_flags(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    captured = {}

    def fake_show_logs(console, **kwargs) -> None:
        captured["kwargs"] = kwargs

    monkeypatch.setattr(cli_main, "show_logs", fake_show_logs)

    result = runner.invoke(cli_main.app, ["logs", "--backend", "--no-follow", "--lines", "12"])

    assert result.exit_code == 0
    assert captured["kwargs"] == {
        "backend": True,
        "webui": False,
        "follow": False,
        "lines": 12,
    }


def test_hidden_serve_command_is_not_listed_but_still_invocable(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    result = runner.invoke(cli_main.app, ["--help"])
    hidden_help = runner.invoke(cli_main.app, ["serve", "--help"])

    assert result.exit_code == 0
    assert not _help_contains_command(result.stdout, "serve")
    assert hidden_help.exit_code == 0
    assert "Start the Flocks API server" in hidden_help.stdout


def test_tui_starts_hidden_serve_command(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    monkeypatch.setattr(
        cli_main,
        "resolve_flocks_cli_command",
        lambda: [cli_main.sys.executable, "-m", "flocks.cli.main"],
    )

    tui_dir = tmp_path / "tui"
    node_modules = tui_dir / "node_modules"
    node_modules.mkdir(parents=True)

    original_exists = Path.exists

    def fake_exists(path: Path) -> bool:
        if path == node_modules:
            return True
        return original_exists(path)

    popen_calls = {}
    run_calls = []

    class DummyProcess:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_run(args, **kwargs):
        run_calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)

    def fake_popen(args, **kwargs):
        popen_calls["args"] = args
        popen_calls["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr(cli_main.Path, "cwd", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Path, "exists", fake_exists)

    result = runner.invoke(cli_main.app, ["tui"])

    assert result.exit_code == 0
    assert popen_calls["args"][:4] == [cli_main.sys.executable, "-m", "flocks.cli.main", "serve"]
    assert ["bun", "--version"] == run_calls[0][0]
