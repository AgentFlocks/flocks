from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

import flocks.cli.commands.update as update_cmd
import flocks.cli.main as cli_main
import flocks.updater as updater_pkg
from flocks.updater.models import UpdateProgress, VersionInfo

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def test_update_cli_accepts_force_option(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    monkeypatch.setattr(cli_main.Log, "init", _noop_log_init)

    captured: dict[str, object] = {}

    async def fake_update(*, check: bool, yes: bool, force: bool, region: str | None) -> None:
        captured["check"] = check
        captured["yes"] = yes
        captured["force"] = force
        captured["region"] = region

    monkeypatch.setattr(update_cmd, "_update", fake_update)

    result = runner.invoke(cli_main.app, ["update", "--force", "--yes", "--region", "cn"])

    assert result.exit_code == 0, result.stdout
    assert captured == {"check": False, "yes": True, "force": True, "region": "cn"}


async def _fake_progress():
    yield UpdateProgress(stage="fetching", message="fetching")
    yield UpdateProgress(stage="done", message="done", success=True)


def test_update_force_reinstalls_latest_release_when_already_up_to_date(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    async def fake_check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
        captured["check_region"] = region
        return VersionInfo(
            current_version="2026.4.2",
            latest_version="2026.4.2",
            has_update=False,
            zipball_url="https://example.com/flocks.zip",
            tarball_url="https://example.com/flocks.tar.gz",
            deploy_mode="source",
            update_allowed=True,
        )

    captured: dict[str, object] = {}

    async def fake_perform_update(
        latest_tag: str,
        *,
        zipball_url: str | None = None,
        tarball_url: str | None = None,
        restart: bool = True,
        locale: str | None = None,
        region: str | None = None,
    ):
        captured["latest_tag"] = latest_tag
        captured["zipball_url"] = zipball_url
        captured["tarball_url"] = tarball_url
        captured["perform_region"] = region
        captured["restart"] = restart
        async for step in _fake_progress():
            yield step

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")

    import asyncio

    asyncio.run(update_cmd._update(check=False, yes=True, force=True, region="cn"))

    assert captured == {
        "latest_tag": "2026.4.2",
        "zipball_url": "https://example.com/flocks.zip",
        "tarball_url": "https://example.com/flocks.tar.gz",
        "check_region": "cn",
        "perform_region": "cn",
        "restart": False,
    }
    assert "强制重新安装 v2026.4.2" in output.getvalue()
    assert "升级完成" in output.getvalue()
