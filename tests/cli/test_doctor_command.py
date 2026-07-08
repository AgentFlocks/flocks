from __future__ import annotations

from typer.testing import CliRunner

import flocks.cli.commands.doctor as doctor_cmd
import flocks.cli.main as cli_main

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def test_doctor_runs_source_installer_from_source_root(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    calls: list[tuple[list[str], object, bool]] = []

    def fake_run(command, *, cwd, check, env):
        _ = env
        calls.append((command, cwd, check))

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "flocks.cli.service_manager.build_status_lines",
        lambda: [
            "[flocks]   daemon: state=running PID=111",
            "[flocks]   flocks: state=healthy PID=222 URL=http://127.0.0.1:5173",
        ],
    )

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "Flocks source directory:" in result.stdout
    assert "scripts/install.sh" in result.stdout
    assert "安装正常" in result.stdout
    assert "运行状态正常" in result.stdout
    assert len(calls) == 1

    command, cwd, check = calls[0]
    assert command[0] == "bash"
    assert command[1].endswith("scripts/install.sh")
    assert cwd == doctor_cmd._find_source_root()
    assert check is True


def test_doctor_uses_cn_environment_for_zh_install_profile(monkeypatch, tmp_path) -> None:
    profile = tmp_path / "install_profile.json"
    profile.write_text('{"Language": "zh-CN"}', encoding="utf-8")
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, check, env):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check
        captured["env"] = env

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr("flocks.cli.service_manager.build_status_lines", lambda: ["[flocks] 后端未运行", "[flocks] WebUI 未运行"])

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["FLOCKS_INSTALL_LANGUAGE"] == "zh-CN"
    assert env["FLOCKS_UV_DEFAULT_INDEX"] == "https://mirrors.aliyun.com/pypi/simple"
    assert "运行状态异常，请执行 `flocks restart`" in result.stdout


def test_doctor_on_windows_starts_handoff_before_running_installer(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FLOCKS_DOCTOR_WINDOWS_HANDOFF", raising=False)
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    monkeypatch.setattr(doctor_cmd, "_is_windows", lambda: True)
    monkeypatch.setattr(doctor_cmd.os, "getpid", lambda: 4242)

    captured: dict[str, object] = {}

    def fake_popen(command, *, cwd, env, close_fds):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["env"] = env
        captured["close_fds"] = close_fds
        return object()

    def fail_run(*_args, **_kwargs):
        raise AssertionError("Windows doctor should hand off before running the installer")

    monkeypatch.setattr(doctor_cmd.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(doctor_cmd.subprocess, "run", fail_run)

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "scripts/install.ps1" in result.stdout
    assert "installer will continue in this console" in result.stdout
    assert captured["cwd"] == doctor_cmd._find_source_root()
    assert captured["close_fds"] is True
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["FLOCKS_DOCTOR_WINDOWS_HANDOFF"] == "1"
    command = captured["command"]
    assert isinstance(command, list)
    assert "-Command" in command
    assert "Wait-Process -Id 4242" in command[-1]
    assert "-m flocks.cli.main doctor" in command[-1]


def test_doctor_windows_handoff_child_runs_installer_synchronously(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("FLOCKS_DOCTOR_WINDOWS_HANDOFF", "1")
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)
    monkeypatch.setattr(doctor_cmd, "_is_windows", lambda: True)

    captured: dict[str, object] = {}

    def fake_run(command, *, cwd, check, env):
        captured["command"] = command
        captured["cwd"] = cwd
        captured["check"] = check
        captured["env"] = env

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("handoff child should run the installer directly")

    monkeypatch.setattr(doctor_cmd.subprocess, "run", fake_run)
    monkeypatch.setattr(doctor_cmd.subprocess, "Popen", fail_popen)
    monkeypatch.setattr(
        "flocks.cli.service_manager.build_status_lines",
        lambda: [
            "[flocks]   daemon: state=running PID=111",
            "[flocks]   flocks: state=healthy PID=222 URL=http://127.0.0.1:5173",
        ],
    )

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    command = captured["command"]
    assert isinstance(command, list)
    assert command[-1].endswith("scripts/install.ps1")
    assert captured["cwd"] == doctor_cmd._find_source_root()
    assert captured["check"] is True
    assert "安装正常" in result.stdout
    assert "运行状态正常" in result.stdout


def test_service_status_is_healthy_accepts_current_daemon_status() -> None:
    assert doctor_cmd._service_status_is_healthy(
        [
            "[flocks]   daemon: state=running PID=111",
            "[flocks]   flocks: state=healthy PID=222 URL=http://127.0.0.1:5173",
        ]
    )
    assert not doctor_cmd._service_status_is_healthy(
        [
            "[flocks]   daemon: state=running PID=111",
            "[flocks]   flocks: state=degraded PID=222 URL=http://127.0.0.1:5173",
        ]
    )


def test_service_status_is_healthy_accepts_legacy_backend_and_webui() -> None:
    assert doctor_cmd._service_status_is_healthy(
        [
            "[flocks] 后端运行中: PID=111 URL=http://127.0.0.1:8000",
            "[flocks] WebUI 运行中: PID=222 URL=http://127.0.0.1:5173",
        ]
    )
    assert not doctor_cmd._service_status_is_healthy(["[flocks] 后端运行中: PID=111", "[flocks] WebUI 未运行"])


def test_doctor_builds_windows_install_command(monkeypatch) -> None:
    monkeypatch.setattr(doctor_cmd.shutil, "which", lambda name: None)

    command = doctor_cmd._build_source_install_command(doctor_cmd._find_source_root() / "scripts" / "install.ps1")

    assert command == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(doctor_cmd._find_source_root() / "scripts" / "install.ps1"),
    ]
