"""Regression tests for CLI entry/service hardening found during the 2026-09 full test pass.

Covers:
- ``--log-level`` rejects unknown levels
- hidden ``acp`` / ``debug`` / ``run`` commands are registered (invocable, not listed);
  ``agent`` is listed under the Agent CLI panel (see ``tests/cli/test_service_commands.py``)
- ``flocks start`` reports ``ServiceConfigError`` / ``WebUIDistMissingError`` as CLI errors (no traceback)
- port options and ``FLOCKS_*PORT`` env values are validated against 1..65535
- ``[flocks]`` / ``[daemon]`` prefixes survive Rich markup rendering
- supervisor ``/logs?lines=0`` returns zero lines (consistent with ``flocks logs -n 0``)
- supervisor start-failure backoff escalates 1/2/5/10/30s
- overlong Unix control socket paths are rejected before the daemon is spawned
"""

from __future__ import annotations

import io
import json
import re
import shutil
import time
from pathlib import Path

import httpx
import pytest
from rich.console import Console
from typer.testing import CliRunner

import flocks.cli.main as cli_main
from flocks.cli import service_config, service_manager, service_supervisor
from flocks.server.static_webui import WebUIDistMissingError
from tests.helpers.service_supervisor import SleeperProcessAdapter, make_runtime_paths, make_short_runtime_root

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def _help_contains_command(help_output: str, command: str) -> bool:
    pattern = rf"^\s*│\s+{re.escape(command)}\s{{2,}}"
    return re.search(pattern, help_output, re.MULTILINE) is not None


@pytest.fixture(autouse=True)
def _isolated_cli(monkeypatch, tmp_path):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    for name in ("FLOCKS_PORT", "FLOCKS_PUBLIC_PORT", "FLOCKS_WEBUI_PORT", "FLOCKS_FRONTEND_PORT", "FLOCKS_SERVER_PORT", "FLOCKS_BACKEND_PORT"):
        monkeypatch.delenv(name, raising=False)


# --------------------------------------------------------------------------- --log-level
@pytest.mark.parametrize("level", ["VERBOSE", "trace", "5"])
def test_invalid_log_level_is_rejected(level) -> None:
    result = runner.invoke(cli_main.app, ["--log-level", level, "status"])

    assert result.exit_code == 2
    output = result.output
    assert "--log-level" in output
    assert "DEBUG" in output and "ERROR" in output


@pytest.mark.parametrize("level", ["DEBUG", "info", "Warn", "ERROR"])
def test_known_log_levels_accepted_case_insensitively(monkeypatch, level) -> None:
    seen = {}

    async def fake_init(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(cli_main.Log, "init", fake_init)
    monkeypatch.setattr(cli_main, "show_status", lambda *_args: None)

    result = runner.invoke(cli_main.app, ["--log-level", level, "status"])

    assert result.exit_code == 0, result.stdout
    assert seen["level"] == level.upper()


# --------------------------------------------------------------------------- hidden command groups
@pytest.mark.parametrize("command", ["acp", "debug", "run"])
def test_hidden_command_groups_are_invocable_but_not_listed(command) -> None:
    help_result = runner.invoke(cli_main.app, ["--help"])
    sub_help = runner.invoke(cli_main.app, [command, "--help"])

    assert help_result.exit_code == 0
    assert not _help_contains_command(help_result.stdout, command)
    assert sub_help.exit_code == 0, sub_help.stdout
    assert "Usage" in sub_help.stdout


def test_debug_info_and_agent_list_run() -> None:
    info = runner.invoke(cli_main.app, ["debug", "info"])
    agents = runner.invoke(cli_main.app, ["agent", "list", "--format", "json"])

    assert info.exit_code == 0, info.stdout
    assert "Version" in info.stdout
    assert agents.exit_code == 0, agents.stdout
    # importing the server app prints a "[OK] Loaded environment" line first; JSON starts at the array
    payload = agents.stdout[agents.stdout.index("[\n") :]
    assert json.loads(payload)


def test_run_command_delegates_to_session_runner(monkeypatch, tmp_path) -> None:
    captured = {}

    async def fake_run_session(**kwargs):
        captured.update(kwargs)

    import flocks.cli.session_runner as session_runner

    monkeypatch.setattr(session_runner, "run_session", fake_run_session)

    result = runner.invoke(cli_main.app, ["run", "hi there", "-d", str(tmp_path), "-m", "p/m", "-a", "rex", "-s", "ses_1", "-y"])

    assert result.exit_code == 0, result.stdout
    assert captured["message"] == "hi there"
    assert captured["directory"] == tmp_path.resolve()
    assert captured["model"] == "p/m"
    assert captured["agent"] == "rex"
    assert captured["session_id"] == "ses_1"
    assert captured["auto_confirm"] is True


# --------------------------------------------------------------------------- start error handling
def test_start_reports_env_port_config_error(monkeypatch) -> None:
    monkeypatch.setenv("FLOCKS_PORT", "abc")
    monkeypatch.setattr(cli_main, "start_all", lambda *_args: pytest.fail("start_all must not run"))

    result = runner.invoke(cli_main.app, ["start", "--no-browser"])

    assert result.exit_code == 1
    assert "error: public port from FLOCKS_PORT must be an integer." in result.stdout
    assert "Traceback" not in result.stdout


def test_start_reports_missing_webui_dist_as_cli_error(monkeypatch) -> None:
    def fake_start_all(config, console) -> None:
        raise WebUIDistMissingError("WebUI build output is missing. Run `cd webui && npm run build`.")

    monkeypatch.setattr(cli_main, "start_all", fake_start_all)

    result = runner.invoke(cli_main.app, ["start", "--skip-webui-build", "--no-browser"])

    assert result.exit_code == 1
    assert "error: WebUI build output is missing" in result.stdout
    assert "Traceback" not in result.stdout


def test_restart_reports_missing_webui_dist_as_cli_error(monkeypatch) -> None:
    def fake_restart_all(config, console) -> None:
        raise WebUIDistMissingError("WebUI build output is missing.")

    monkeypatch.setattr(cli_main, "restart_all", fake_restart_all)

    result = runner.invoke(cli_main.app, ["restart", "--skip-webui-build", "--no-browser"])

    assert result.exit_code == 1
    assert "error: WebUI build output is missing" in result.stdout


# --------------------------------------------------------------------------- port validation
@pytest.mark.parametrize("command", ["start", "restart"])
@pytest.mark.parametrize("option", ["--port", "--server-port", "--webui-port"])
@pytest.mark.parametrize("value", ["0", "-1", "65536"])
def test_port_options_out_of_range_rejected(monkeypatch, command, option, value) -> None:
    monkeypatch.setattr(cli_main, "start_all", lambda *_args: pytest.fail("must not start"))
    monkeypatch.setattr(cli_main, "restart_all", lambda *_args: pytest.fail("must not restart"))

    result = runner.invoke(cli_main.app, [command, option, value, "--no-browser"])

    assert result.exit_code == 2, result.stdout
    output = result.output
    assert "1" in output and "65535" in output


def test_port_option_in_range_is_accepted(monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(cli_main, "start_all", lambda config, console: captured.setdefault("config", config))

    result = runner.invoke(cli_main.app, ["start", "--port", "65535", "--no-browser", "--skip-webui-build"])

    assert result.exit_code == 0, result.stdout
    assert captured["config"].backend_port == 65535


@pytest.mark.parametrize("value", ["0", "-1", "65536"])
def test_env_port_out_of_range_raises_config_error(monkeypatch, value) -> None:
    monkeypatch.setenv("FLOCKS_PORT", value)

    with pytest.raises(service_config.ServiceConfigError, match="FLOCKS_PORT") as excinfo:
        service_config.build_service_config(default_server_host="127.0.0.1", default_server_port=8000)
    assert "65535" in str(excinfo.value)


def test_env_port_in_range_accepted(monkeypatch) -> None:
    monkeypatch.setenv("FLOCKS_PORT", "65535")

    config = service_config.build_service_config(default_server_host="127.0.0.1", default_server_port=8000)

    assert config.backend_port == 65535


# --------------------------------------------------------------------------- [flocks] prefix rendering
def test_service_console_keeps_bracket_prefixes(monkeypatch) -> None:
    buffer = io.StringIO()
    base = Console(file=buffer, force_terminal=False, width=200, color_system=None)
    monkeypatch.setattr(cli_main, "console", base)
    monkeypatch.setattr(cli_main, "show_status", lambda console: console.print("[flocks] Flocks daemon 未运行") or console.print("[daemon] --- /tmp/daemon.log ---"))

    result = runner.invoke(cli_main.app, ["status"])

    assert result.exit_code == 0
    rendered = buffer.getvalue()
    assert "[flocks] Flocks daemon 未运行" in rendered
    assert "[daemon] --- /tmp/daemon.log ---" in rendered


def test_service_error_message_keeps_prefix_and_text(monkeypatch) -> None:
    buffer = io.StringIO()
    base = Console(file=buffer, force_terminal=False, width=200, color_system=None)
    monkeypatch.setattr(cli_main, "console", base)
    monkeypatch.setattr(cli_main, "show_status", lambda console: (_ for _ in ()).throw(service_manager.ServiceError("端口 [5173] 已被占用")))

    result = runner.invoke(cli_main.app, ["status"])

    assert result.exit_code == 1
    assert "[flocks] error: 端口 [5173] 已被占用" in buffer.getvalue()


# --------------------------------------------------------------------------- supervisor /logs lines=0
def test_supervisor_logs_zero_lines_returns_empty(monkeypatch) -> None:
    short_root = make_short_runtime_root("flocks-logs0-")
    paths = make_runtime_paths(short_root)
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    paths.backend_log.write_text("a\nb\nc\n", encoding="utf-8")
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    daemon = service_supervisor.SupervisorDaemon(service_manager.ServiceConfig())
    daemon.paths = paths
    daemon._start_control_server()
    try:
        transport = httpx.HTTPTransport(uds=str(paths.run_dir / "service-daemon.sock"))
        with httpx.Client(base_url="http://flocks.local", transport=transport, trust_env=False, timeout=5.0) as client:
            zero = client.get("/logs", params={"service": "backend", "lines": "0"}).json()
            default = client.get("/logs", params={"service": "backend", "lines": "abc"}).json()
            negative = client.get("/logs", params={"service": "backend", "lines": "-5"}).json()
            two = client.get("/logs", params={"service": "backend", "lines": "2"}).json()
    finally:
        daemon._stop_control_server()
        shutil.rmtree(short_root, ignore_errors=True)

    assert zero["logs"]["flocks"]["lines"] == []
    assert default["logs"]["flocks"]["lines"] == ["a", "b", "c"]
    assert negative["logs"]["flocks"]["lines"] == ["a", "b", "c"]
    assert two["logs"]["flocks"]["lines"] == ["b", "c"]


# --------------------------------------------------------------------------- supervisor start backoff
def test_supervisor_start_failure_backoff_escalates(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)

    class FailingAdapter(SleeperProcessAdapter):
        def start(self, _config, _paths, *, built_once: bool = False):
            raise service_manager.ServiceError("server 端口 9995 已被占用")

    daemon = service_supervisor.SupervisorDaemon(
        service_manager.ServiceConfig(backend_port=9995, frontend_port=9995),
        interval=0.01,
        backend_adapter=FailingAdapter(),
    )
    daemon.paths = paths

    delays = []
    daemon.restart_all(reason="startup")
    delays.append(daemon.backend.next_restart_at - time.monotonic())
    for _ in range(5):
        daemon.backend.next_restart_at = 0.0  # 到期，允许再次尝试
        daemon.tick()
        delays.append(daemon.backend.next_restart_at - time.monotonic())

    assert daemon.backend.state == "degraded"
    assert [round(d) for d in delays] == [1, 2, 5, 10, 30, 30]


def test_supervisor_start_failure_backoff_resets_after_success(monkeypatch, tmp_path: Path) -> None:
    paths = make_runtime_paths(tmp_path)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)

    class FlakyAdapter(SleeperProcessAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.fail_next = 2

        def start(self, config, paths_, *, built_once: bool = False):
            if self.fail_next:
                self.fail_next -= 1
                raise service_manager.ServiceError("boom")
            return super().start(config, paths_, built_once=built_once)

    adapter = FlakyAdapter()
    daemon = service_supervisor.SupervisorDaemon(
        service_manager.ServiceConfig(backend_port=9995, frontend_port=9995),
        interval=0.01,
        backend_adapter=adapter,
    )
    daemon.paths = paths
    try:
        daemon.restart_all(reason="startup")
        daemon.backend.next_restart_at = 0.0
        daemon.tick()
        assert round(daemon.backend.next_restart_at - time.monotonic()) == 2
        daemon.backend.next_restart_at = 0.0
        daemon.tick()
        assert daemon.backend.state == "healthy"
        assert daemon.backend.start_failure_count == 0
    finally:
        daemon.shutdown_children()


# --------------------------------------------------------------------------- overlong UDS path
def test_start_rejects_overlong_control_socket_path(monkeypatch, tmp_path: Path) -> None:
    long_root = tmp_path / ("x" * 120)
    paths = make_runtime_paths(long_root)
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    monkeypatch.setattr(service_manager, "supervisor_is_running", lambda paths=None: False)
    monkeypatch.setattr(service_manager, "cleanup_legacy_runtime_processes", lambda *_a, **_k: None)
    monkeypatch.setattr(service_manager, "cleanup_orphan_service_ports", lambda *_a, **_k: None)
    monkeypatch.setattr(service_manager, "_ensure_webui_dist", lambda *_a, **_k: None)
    monkeypatch.setattr(service_manager, "_spawn_process", lambda *_a, **_k: pytest.fail("daemon must not be spawned"))

    class C:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def print(self, *args, **_kwargs) -> None:
            self.lines.append(" ".join(str(a) for a in args))

    with pytest.raises(service_manager.ServiceError, match="路径过长"):
        service_manager.start_all(service_manager.ServiceConfig(no_browser=True, skip_frontend_build=True), C())


# --------------------------------------------------------------------------- control socket permissions
@pytest.mark.skipif(not hasattr(__import__("socket"), "AF_UNIX"), reason="Unix domain socket control API only")
def test_control_socket_is_owner_only(monkeypatch) -> None:
    import os
    import stat

    short_root = make_short_runtime_root("flocks-sockperm-")
    paths = make_runtime_paths(short_root)
    paths.run_dir.mkdir(parents=True)
    paths.log_dir.mkdir(parents=True)
    monkeypatch.setattr(service_manager, "ensure_runtime_dirs", lambda: paths)
    old_umask = os.umask(0)
    daemon = service_supervisor.SupervisorDaemon(service_manager.ServiceConfig())
    try:
        daemon._start_control_server()
        mode = stat.S_IMODE(os.stat(paths.run_dir / "service-daemon.sock").st_mode)
    finally:
        os.umask(old_umask)
        daemon._stop_control_server()
        shutil.rmtree(short_root, ignore_errors=True)

    assert mode == 0o600


# --------------------------------------------------------------------------- wildcard bind → loopback probe host
@pytest.mark.parametrize("bind_host, expected", [("0.0.0.0", "127.0.0.1"), ("::", "::1"), ("::1", "::1"), ("127.0.0.1", "127.0.0.1"), ("10.0.0.5", "10.0.0.5")])
def test_loopback_host_maps_ipv6_wildcard_to_ipv6_loopback(bind_host, expected) -> None:
    """asyncio binds ``::`` with IPV6_V6ONLY, so probes/browser URLs must use ``::1`` (OPS-0034)."""
    assert service_config.loopback_host(bind_host) == expected
    assert service_manager.access_host(bind_host) == expected


def test_backend_url_for_ipv6_wildcard_uses_bracketed_loopback() -> None:
    config = service_config.ServiceConfig(backend_host="::", backend_port=5292, frontend_host="::", frontend_port=5292)
    assert config.backend_url == "http://[::1]:5292"


# --------------------------------------------------------------------------- unwritable FLOCKS_ROOT
def test_unwritable_runtime_root_is_reported_as_service_error(monkeypatch, tmp_path: Path) -> None:
    """A root-owned/read-only FLOCKS_ROOT must not surface as a PermissionError traceback (OPS-0322)."""
    import os
    import stat

    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    root = tmp_path / "ro-root"
    root.mkdir()
    root.chmod(stat.S_IRUSR | stat.S_IXUSR)
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    try:
        with pytest.raises(service_manager.ServiceError) as excinfo:
            service_manager.ensure_runtime_dirs()
        message = str(excinfo.value)
        assert "无法创建运行时目录" in message
        assert "FLOCKS_ROOT" in message and "chown" in message

        # `status` is read-only and reports "not running"; `start` must fail cleanly
        # before anything is spawned.
        result = runner.invoke(cli_main.app, ["start", "--no-browser", "--skip-webui-build", "--port", "5349"])
        assert result.exit_code == 1
        assert "[flocks] error: 无法" in result.output and "FLOCKS_ROOT" in result.output
        assert "Traceback" not in result.output and "PermissionError" not in result.output
    finally:
        root.chmod(stat.S_IRWXU)
