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
            "[flocks] 后端运行中: PID=111 URL=http://127.0.0.1:8000",
            "[flocks] WebUI 运行中: PID=222 URL=http://127.0.0.1:5173",
        ],
    )

    result = runner.invoke(cli_main.app, ["doctor"])

    assert result.exit_code == 0, result.stdout
    assert "Flocks source directory:" in result.stdout
    assert "scripts/install.sh" in result.stdout
    assert "安装正常" in result.stdout
    assert "服务正常" in result.stdout
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
    assert "服务不正常，请执行 `flocks restart`" in result.stdout


def test_service_status_is_healthy_requires_backend_and_webui() -> None:
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
