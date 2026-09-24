import os
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
from typer.testing import CliRunner

import flocks.cli.main as cli_main
import flocks.security as security_module

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
    assert "Agents must use `flocks restart" in result.stdout
    assert "--server-only`;" in result.stdout
    assert "bare restart stops the supervisor and terminates the running agent" in result.stdout
    for command in ("agent", "acp", "debug", "run", "serve", "service-watchdog", "service-daemon", "auth", "models"):
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


def test_serve_exposes_runtime_server_port(monkeypatch) -> None:
    import uvicorn

    monkeypatch.delenv("_FLOCKS_SERVER_PORT", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda *_args, **_kwargs: None)

    cli_main.serve(host="127.0.0.1", port=5173, reload=False)

    assert os.environ["_FLOCKS_SERVER_PORT"] == "5173"


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
    saved_secrets = {}

    class FakeSecrets:
        def get(self, secret_id: str):
            return saved_secrets.get(secret_id)

        def set(self, secret_id: str, value: str) -> None:
            saved_secrets[secret_id] = value

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
    monkeypatch.setattr(security_module, "get_secret_manager", lambda: FakeSecrets())
    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: SimpleNamespace(status_code=200))
    monkeypatch.setattr(time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(Path, "exists", fake_exists)

    result = runner.invoke(cli_main.app, ["tui"])

    assert result.exit_code == 0
    assert popen_calls["args"][:4] == [cli_main.sys.executable, "-m", "flocks.cli.main", "serve"]
    assert ["bun", "--version"] == run_calls[0][0]
    assert saved_secrets["server_api_token"]


def test_start_on_unwritable_flocks_root_reports_readable_error(monkeypatch, tmp_path) -> None:
    """OPS-0322：挂载卷属主为 root / 只读 FLOCKS_ROOT 时不能 traceback，要说清目录与权限并非 0 退出。"""
    ro_root = tmp_path / "ro-root"
    ro_root.mkdir()
    ro_root.chmod(0o555)
    monkeypatch.setenv("FLOCKS_ROOT", str(ro_root))
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(ro_root / "logs"))
    try:
        result = runner.invoke(cli_main.app, ["start", "--no-browser", "--skip-webui-build"])
    finally:
        ro_root.chmod(0o755)

    flat = result.stdout.replace("\n", "")  # rich 按终端宽度折行
    assert result.exit_code == 1
    assert "[flocks] error: 无法创建日志目录" in flat
    assert str(ro_root / "logs") in flat
    assert "属主/权限" in flat and "chown" in flat
    assert "Traceback" not in result.stdout


def test_runtime_dir_permission_error_is_reported_by_service_commands(monkeypatch, tmp_path) -> None:
    """日志目录可写但 run/ 目录建不了（同样是权限问题）时，start/stop/status 都走 [flocks] error 而不是 traceback。"""
    from flocks.cli import service_manager

    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    ro_root = tmp_path / "ro-root"
    ro_root.mkdir()
    paths = service_manager.RuntimePaths(
        root=ro_root,
        run_dir=ro_root / "run",
        log_dir=tmp_path / "logs",
        backend_pid=ro_root / "run" / "backend.pid",
        frontend_pid=ro_root / "run" / "frontend.pid",
        backend_log=tmp_path / "logs" / "backend.log",
        frontend_log=tmp_path / "logs" / "frontend.log",
    )
    ro_root.chmod(0o555)
    try:
        with __import__("pytest").raises(service_manager.ServiceError) as info:
            service_manager.ensure_runtime_dirs(paths)
    finally:
        ro_root.chmod(0o755)
    message = str(info.value)
    assert "无法创建运行时目录" in message and str(ro_root / "run") in message
    assert "Permission denied" in message and "chown" in message and ":Z" in message

    error = info.value
    for command, target in (("stop", "stop_all"), ("status", "show_status"), ("start", "start_all")):
        monkeypatch.setattr(cli_main, target, lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
        args = [command] if command != "start" else ["start", "--no-browser", "--skip-webui-build"]
        result = runner.invoke(cli_main.app, args)
        assert result.exit_code == 1, command
        assert "[flocks] error: 无法创建运行时目录" in result.stdout.replace("\n", ""), command
        assert "Traceback" not in result.stdout, command
