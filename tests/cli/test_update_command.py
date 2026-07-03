from io import StringIO

import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

import flocks.cli.commands.update as update_cmd
import flocks.cli.main as cli_main
import flocks.cli.service_manager as service_manager
import flocks.updater as updater_pkg
from flocks.updater.models import UpdateProgress, VersionInfo

runner = CliRunner()


async def _noop_log_init(**_: object) -> None:
    return None


def test_updater_package_exports_build_updated_frontend() -> None:
    from flocks.updater import updater as updater_module

    assert updater_pkg.build_updated_frontend is updater_module.build_updated_frontend


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


def test_update_prompts_for_cn_mirror_before_upgrade_confirmation(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None, width=120),
    )

    check_regions: list[str | None] = []
    confirm_prompts: list[str] = []
    captured: dict[str, object] = {}
    stop_calls: list[str] = []
    build_calls: list[str | None] = []
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
    ):
        captured["latest_tag"] = latest_tag
        captured["zipball_url"] = zipball_url
        captured["tarball_url"] = tarball_url
        captured["perform_region"] = region
        captured["restart"] = restart
        async for step in _fake_progress():
            yield step

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        confirm_prompts.append(prompt)
        return next(answers)

    def fake_stop_all(console) -> None:
        stop_calls.append("stop")

    async def fake_build_updated_frontend(*, locale: str | None = None, region: str | None = None) -> None:
        build_calls.append(region)

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "build_updated_frontend", fake_build_updated_frontend)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_cmd.typer, "confirm", fake_confirm)
    monkeypatch.setattr(service_manager, "stop_all", fake_stop_all)

    import asyncio

    asyncio.run(update_cmd._update(check=False, yes=False, force=False, region=None))

    assert check_regions == ["cn"]
    assert confirm_prompts == ["\n是否使用中国镜像进行升级？", "\n是否立即升级？"]
    assert stop_calls == ["stop"]
    assert build_calls == ["cn"]
    assert captured == {
        "latest_tag": "2026.4.2",
        "zipball_url": "https://gitee.example.com/flocks.zip",
        "tarball_url": "https://gitee.example.com/flocks.tar.gz",
        "perform_region": "cn",
        "restart": False,
    }
    assert "已切换为中国镜像源" not in output.getvalue()


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
    stop_calls: list[str] = []
    build_calls: list[str | None] = []

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
    ):
        captured["latest_tag"] = latest_tag
        captured["zipball_url"] = zipball_url
        captured["tarball_url"] = tarball_url
        captured["bundle_sha256"] = bundle_sha256
        captured["bundle_format"] = bundle_format
        captured["perform_region"] = region
        captured["restart"] = restart
        async for step in _fake_progress():
            yield step

    def fake_stop_all(console) -> None:
        stop_calls.append("stop")

    async def fake_build_updated_frontend(*, locale: str | None = None, region: str | None = None) -> None:
        build_calls.append(region)

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "build_updated_frontend", fake_build_updated_frontend)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(service_manager, "stop_all", fake_stop_all)

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
        "restart": False,
    }
    assert stop_calls == ["stop"]
    assert build_calls == ["cn"]
    assert "强制重新安装 v2026.4.2" in output.getvalue()
    assert "升级完成" in output.getvalue()


def test_update_executes_flocks_stop_before_upgrade(monkeypatch) -> None:
    output = StringIO()
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
    ):
        events.append("perform_update")
        async for step in _fake_progress():
            yield step

    def fake_confirm(prompt: str, default: bool = False) -> bool:
        confirm_prompts.append(prompt)
        return next(answers)

    def fake_stop_all(console) -> None:
        events.append("stop")

    async def fake_build_updated_frontend(*, locale: str | None = None, region: str | None = None) -> None:
        events.append("build")

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "build_updated_frontend", fake_build_updated_frontend)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(update_cmd.typer, "confirm", fake_confirm)
    monkeypatch.setattr(service_manager, "stop_all", fake_stop_all)

    import asyncio

    asyncio.run(update_cmd._update(check=False, yes=False, force=False, region=None))

    assert confirm_prompts == ["\n是否使用中国镜像进行升级？", "\n是否立即升级？"]
    assert events == ["stop", "perform_update", "build"]
    assert "已执行 flocks stop" in output.getvalue()


def test_update_reports_frontend_build_failure_after_common_upgrade(monkeypatch) -> None:
    output = StringIO()
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
    ):
        async for step in _fake_progress():
            yield step

    def fake_stop_all(console) -> None:
        return None

    async def fake_build_updated_frontend(*, locale: str | None = None, region: str | None = None) -> None:
        raise RuntimeError("npm run build failed")

    monkeypatch.setattr(updater_pkg, "check_update", fake_check_update)
    monkeypatch.setattr(updater_pkg, "perform_update", fake_perform_update)
    monkeypatch.setattr(updater_pkg, "build_updated_frontend", fake_build_updated_frontend)
    monkeypatch.setattr(updater_pkg, "detect_deploy_mode", lambda: "source")
    monkeypatch.setattr(service_manager, "stop_all", fake_stop_all)

    import asyncio

    with pytest.raises(typer.Exit) as excinfo:
        asyncio.run(update_cmd._update(check=False, yes=True, force=False, region=None))

    assert excinfo.value.exit_code == 1
    assert "前端构建失败" in output.getvalue()
