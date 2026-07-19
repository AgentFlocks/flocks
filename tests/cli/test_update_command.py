from io import StringIO

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

import flocks.cli.commands.update as update_cmd
import flocks.cli.main as cli_main
import flocks.updater as updater_pkg
from flocks.updater.models import UpdateProgress, VersionInfo

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def test_updater_package_exports_shared_installer() -> None:
    from flocks.updater import updater as updater_module

    assert updater_pkg.install_or_repair_source is updater_module.install_or_repair_source


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


def test_update_uses_install_profile_language_as_default_region(monkeypatch, tmp_path) -> None:
    output = StringIO()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    (tmp_path / "install_profile.json").write_text('{"Language": "zh-CN"}', encoding="utf-8")
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    check_regions: list[str | None] = []

    async def fake_check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
        check_regions.append(region)
        return VersionInfo(
            current_version="2026.4.1",
            latest_version="2026.4.2",
            has_update=True,
            zipball_url="https://gitee.example.com/flocks.zip",
            tarball_url="https://gitee.example.com/flocks.tar.gz",
            deploy_mode="source",
            update_allowed=True,
        )

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")

    import asyncio

    asyncio.run(update_cmd._update(check=True, yes=False, force=False, region=None))

    assert check_regions == ["cn"]
    assert "flocks update" in output.getvalue()


def test_update_prompts_for_cn_mirror_before_upgrade_confirmation(monkeypatch, tmp_path) -> None:
    output = StringIO()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FLOCKS_INSTALL_LANGUAGE", raising=False)
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    check_regions: list[str | None] = []
    confirm_prompts: list[str] = []
    captured: dict[str, object] = {}
    answers = iter([True, True])

    async def fake_check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
        check_regions.append(region)
        zipball_url = "https://example.com/flocks.zip"
        tarball_url = "https://example.com/flocks.tar.gz"
        if region == "cn":
            zipball_url = "https://gitee.example.com/flocks.zip"
            tarball_url = "https://gitee.example.com/flocks.tar.gz"
        return VersionInfo(
            current_version="2026.4.1",
            latest_version="2026.4.2",
            has_update=True,
            zipball_url=zipball_url,
            tarball_url=tarball_url,
            deploy_mode="source",
            update_allowed=True,
        )

    async def fake_perform_update(
        latest_tag: str,
        *,
        zipball_url: str | None = None,
        tarball_url: str | None = None,
        bundle_sha256: str | None = None,
        bundle_format: str | None = None,
        restart: bool = True,
        locale: str | None = None,
        region: str | None = None,
        wait_for_handoff: bool = False,
    ):
        captured["latest_tag"] = latest_tag
        captured["zipball_url"] = zipball_url
        captured["tarball_url"] = tarball_url
        captured["bundle_sha256"] = bundle_sha256
        captured["bundle_format"] = bundle_format
        captured["perform_region"] = region
        captured["restart"] = restart
        captured["wait_for_handoff"] = wait_for_handoff
        async for step in _fake_progress():
            yield step

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        confirm_prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_cmd.typer, "confirm", fake_confirm)

    import asyncio

    asyncio.run(update_cmd._update(check=False, yes=False, force=False, region=None))

    assert check_regions == ["cn"]
    assert confirm_prompts == ["\n是否使用中国镜像进行升级？", "\n是否立即升级？"]
    assert captured == {
        "latest_tag": "2026.4.2",
        "zipball_url": "https://gitee.example.com/flocks.zip",
        "tarball_url": "https://gitee.example.com/flocks.tar.gz",
        "bundle_sha256": None,
        "bundle_format": None,
        "perform_region": "cn",
        "restart": True,
        "wait_for_handoff": True,
    }
    assert "已切换为中国镜像源" not in output.getvalue()


async def _fake_progress():
    yield UpdateProgress(stage="fetching", message="fetching")
    yield UpdateProgress(stage="backing_up", message="backing up")
    yield UpdateProgress(stage="applying", message="applying")
    yield UpdateProgress(stage="restarting", message="restarting")
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
        bundle_sha256: str | None = None,
        bundle_format: str | None = None,
        restart: bool = True,
        locale: str | None = None,
        region: str | None = None,
        wait_for_handoff: bool = False,
    ):
        captured["latest_tag"] = latest_tag
        captured["zipball_url"] = zipball_url
        captured["tarball_url"] = tarball_url
        captured["bundle_sha256"] = bundle_sha256
        captured["bundle_format"] = bundle_format
        captured["perform_region"] = region
        captured["restart"] = restart
        captured["wait_for_handoff"] = wait_for_handoff
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
        "bundle_sha256": None,
        "bundle_format": None,
        "check_region": "cn",
        "perform_region": "cn",
        "restart": True,
        "wait_for_handoff": True,
    }
    assert "强制重新安装 v2026.4.2" in output.getvalue()
    assert "[4/4] 重启服务...  ✓" in output.getvalue()
    assert "升级完成" in output.getvalue()


def test_update_delegates_stop_install_build_and_restart_to_handoff(monkeypatch, tmp_path) -> None:
    output = StringIO()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FLOCKS_INSTALL_LANGUAGE", raising=False)
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    confirm_prompts: list[str] = []
    answers = iter([False, True])
    events: list[str] = []

    async def fake_check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
        return VersionInfo(
            current_version="2026.4.1",
            latest_version="2026.4.2",
            has_update=True,
            zipball_url="https://example.com/flocks.zip",
            tarball_url="https://example.com/flocks.tar.gz",
            deploy_mode="source",
            update_allowed=True,
        )

    async def fake_perform_update(
        latest_tag: str,
        *,
        zipball_url: str | None = None,
        tarball_url: str | None = None,
        bundle_sha256: str | None = None,
        bundle_format: str | None = None,
        restart: bool = True,
        locale: str | None = None,
        region: str | None = None,
        wait_for_handoff: bool = False,
    ):
        events.append(f"perform_update:{wait_for_handoff}")
        async for step in _fake_progress():
            yield step

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        confirm_prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_cmd.typer, "confirm", fake_confirm)

    import asyncio

    asyncio.run(update_cmd._update(check=False, yes=False, force=False, region=None))

    assert confirm_prompts == ["\n是否使用中国镜像进行升级？", "\n是否立即升级？"]
    assert events == ["perform_update:True"]
    assert "已执行 flocks stop" not in output.getvalue()


def test_update_reports_handoff_preparation_failure(monkeypatch, tmp_path) -> None:
    output = StringIO()
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("FLOCKS_INSTALL_LANGUAGE", raising=False)
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    async def fake_check_update(*, locale: str | None = None, region: str | None = None) -> VersionInfo:
        return VersionInfo(
            current_version="2026.4.1",
            latest_version="2026.4.2",
            has_update=True,
            zipball_url="https://example.com/flocks.zip",
            tarball_url="https://example.com/flocks.tar.gz",
            deploy_mode="source",
            update_allowed=True,
        )

    async def fake_perform_update(
        latest_tag: str,
        *,
        zipball_url: str | None = None,
        tarball_url: str | None = None,
        bundle_sha256: str | None = None,
        bundle_format: str | None = None,
        restart: bool = True,
        locale: str | None = None,
        region: str | None = None,
        wait_for_handoff: bool = False,
    ):
        yield UpdateProgress(stage="error", message="handoff preparation failed", success=False)

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")

    import asyncio

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(update_cmd._update(check=False, yes=True, force=False, region=None))

    assert excinfo.value.exit_code == 1
    assert "handoff preparation failed" in output.getvalue()
