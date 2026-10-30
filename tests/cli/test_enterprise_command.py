from pathlib import Path

import pytest
from typer.testing import CliRunner

import flocks.cli.main as cli_main
from flocks.updater import enterprise

runner = CliRunner()


def _situation(**overrides):
    base = {
        "available": True, "platform": "linux-arm64", "systemd": False, "root": False,
        "install_root": "/opt/flockspro", "install_root_ready": True, "prepare_command": None,
    }
    base.update(overrides)
    return base


@pytest.fixture
def offer():
    return enterprise.InstallerOffer(url="https://console/x.run", sha256="a" * 64,
                                     release_id="2026.11.21-linux-arm64", version="2026.11.21", size=50 << 20)


@pytest.fixture
def machine(monkeypatch, tmp_path, offer):
    """An open install with a licence, Console offering an installer; records what the command did."""
    did: dict[str, object] = {"downloaded": False, "ran": None}

    async def fetch_offer():
        return offer

    async def download(got, progress=None):
        did["downloaded"] = True
        if progress is not None:
            await progress(25 << 20, got.size)
            await progress(got.size, got.size)
        path = tmp_path / got.filename
        path.write_text("#!/bin/sh\n")
        return path

    def run_installer(path: Path) -> int:
        did["ran"] = path.name
        return 0

    monkeypatch.setattr(enterprise, "situation", lambda: _situation())
    monkeypatch.setattr(enterprise, "fetch_offer", fetch_offer)
    monkeypatch.setattr(enterprise, "download", download)
    monkeypatch.setattr(enterprise, "run_installer", run_installer)
    monkeypatch.setattr(enterprise, "can_run_installer_here", lambda: True)
    return did


def test_check_only_says_what_console_offers(machine):
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--check"])
    assert result.exit_code == 0, result.output
    assert "flockspro-2026.11.21-linux-arm64.run" in result.output and "50 MB" in result.output
    assert machine["downloaded"] is False and machine["ran"] is None


def test_upgrade_downloads_checks_and_runs_the_installer(machine):
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--yes"])
    assert result.exit_code == 0, result.output
    assert machine["downloaded"] is True and machine["ran"] == "flockspro-2026.11.21-linux-arm64.run"
    assert "下载 50%" in result.output and "已下载并校验" in result.output


def test_the_installers_exit_status_is_the_commands(machine, monkeypatch):
    monkeypatch.setattr(enterprise, "run_installer", lambda path: 3)
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--yes"])
    assert result.exit_code == 3


def test_asks_before_changing_anything(machine):
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade"], input="n\n")
    assert result.exit_code == 1 and "没有改动" in result.output
    assert machine["downloaded"] is False


def test_a_systemd_machine_gets_the_root_command(machine, monkeypatch):
    monkeypatch.setattr(enterprise, "can_run_installer_here", lambda: False)
    monkeypatch.setattr(enterprise, "installer_command", lambda path: f"sudo bash {path}")
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--yes"])
    assert result.exit_code == 1
    assert "sudo bash" in result.output and machine["ran"] is None


def test_an_account_that_cannot_write_the_install_root_is_told_what_root_runs(machine, monkeypatch):
    monkeypatch.setattr(enterprise, "situation", lambda: _situation(
        install_root_ready=False, prepare_command="sudo install -d -o alice -g alice -m 0755 /opt/flockspro"))
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--yes"])
    assert result.exit_code == 1 and "sudo install -d -o alice" in result.output
    assert machine["downloaded"] is False


def test_console_refusing_is_said_as_it_is(machine, monkeypatch):
    async def refused():
        raise enterprise.EnterpriseUpgradeError("这台机器上没有企业版授权")

    monkeypatch.setattr(enterprise, "fetch_offer", refused)
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade", "--yes"])
    assert result.exit_code == 1 and "没有企业版授权" in result.output


def test_an_enterprise_machine_is_pointed_at_its_own_commands(machine, monkeypatch):
    monkeypatch.setattr(enterprise, "situation", lambda: _situation(available=False))
    result = runner.invoke(cli_main.app, ["enterprise", "upgrade"])
    assert result.exit_code == 1 and "flocks downgrade" in result.output


def test_run_installer_runs_it_with_the_installers_environment(tmp_path, monkeypatch):
    script = tmp_path / "flockspro-x.run"
    script.write_text('#!/bin/sh\n[ -z "$VIRTUAL_ENV" ] && [ "$PWD" = "$(dirname "$0")" ] && exit 7\nexit 1\n')
    monkeypatch.setenv("VIRTUAL_ENV", "/opt/venv")
    assert enterprise.run_installer(script) == 7
