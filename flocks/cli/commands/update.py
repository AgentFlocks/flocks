"""
flocks update  — self-update CLI command
"""

import asyncio

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from flocks.utils.log import append_upgrade_text_log

console = Console()


def update_command(
    check: bool = typer.Option(False, "--check", help="仅检查是否有新版本，不执行升级（与 --channel 连用时只显示该通道将安装什么，不安装）"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接升级"),
    force: bool = typer.Option(False, "--force", "-f", help="即使已是最新版本也强制重新安装"),
    region: str | None = typer.Option(
        None,
        "--region",
        help="升级镜像区域。设置为 cn 时优先使用中国大陆镜像源。",
    ),
    channel: str | None = typer.Option(
        None,
        "--channel",
        help="改用已安装扩展提供的更新通道（不加就是本项目自己的发布）",
    ),
    no_restart: bool = typer.Option(
        False,
        "--no-restart",
        help="与 --channel 连用：只安装不替换源码的部分，不自动重启服务（之后自行执行 flocks restart --server-only）",
    ),
):
    """
    Check for and upgrade Flocks to the latest version.

    Downloads the latest Release source archive from GitHub, backs up the
    current version to ~/.flocks/version/, extracts and replaces source files,
    re-syncs dependencies, then restarts the service automatically.  With
    --channel, an update channel an installed extension provides decides what
    gets installed.
    """
    if channel:
        if check:
            asyncio.run(_check_channel(channel))
        else:
            asyncio.run(_install_from_channel(channel, restart=not no_restart))
        return
    asyncio.run(_update(check=check, yes=yes, force=force, region=region))


def _channel_or_exit(name: str):
    from flocks.updater import get_update_channel, update_channel_names

    update_channel = get_update_channel(name)
    if update_channel is None:
        known = "、".join(update_channel_names()) or "无"
        console.print(f"[red]没有名为 {name} 的更新通道（已安装的扩展提供的通道：{known}）[/red]")
        raise typer.Exit(1)
    return update_channel


async def _check_channel(name: str) -> None:
    """Show what a channel would install; never installs or restarts."""
    update_channel = _channel_or_exit(name)
    describe = getattr(update_channel, "describe", None)
    try:
        rows = await describe() if describe is not None else None
    except Exception as exc:
        console.print(f"[red]检查失败：{exc}[/red]")
        raise typer.Exit(1)
    if rows is None:
        from flocks.updater import check_update

        info = await check_update(channel=name)
        if info.error:
            append_upgrade_text_log(f"ERROR cli_channel_check channel={name}: {info.error}")
            console.print(f"[red]检查失败：{info.error}[/red]")
            raise typer.Exit(1)
        _print_version_table(info)
        console.print(f"[yellow]运行 [bold]flocks update --channel {name}[/bold] 安装（只检查，本次未安装任何东西）[/yellow]")
        return

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()
    for label, value in rows:
        table.add_row(str(label), str(value))
    console.print(table)
    console.print(f"[yellow]运行 [bold]flocks update --channel {name}[/bold] 安装（只检查，本次未安装任何东西）[/yellow]")


async def _install_from_channel(name: str, *, restart: bool) -> None:
    """Install what a channel offers now."""
    from flocks.updater import perform_update

    update_channel = _channel_or_exit(name)
    try:
        release = await update_channel.release(version=None)
    except Exception as exc:
        append_upgrade_text_log(f"ERROR cli_channel_release channel={name}: {exc}")
        console.print(f"[red]✗ 取不到 {name} 通道的发布：{exc}[/red]")
        raise typer.Exit(1)

    async for progress in perform_update(release.version, release=release, restart=restart):
        if progress.stage == "error":
            append_upgrade_text_log(f"ERROR cli_channel_install channel={name}: {progress.message}")
            console.print(f"[red]✗ 安装失败：{progress.message}[/red]")
            raise typer.Exit(1)
        if progress.stage == "fetching" and progress.percent is not None:
            continue
        console.print(f"[cyan]{progress.stage}[/cyan] {progress.message}")
        if progress.stage == "done":
            append_upgrade_text_log(f"OK cli_channel_installed channel={name}")
            console.print(f"[green]✓ 已安装 {release.label} {release.version}[/green]")


def _print_previous_edition_notice() -> None:
    """A machine with the Python enterprise package of before: say where the enterprise edition
    went, so an upgrade of the open part is not taken for the whole of it."""
    from flocks.updater import enterprise

    try:
        previous = enterprise.previous_edition()
    except Exception:
        return
    if previous is None:
        return
    what = f"老企业版（Python 版 {previous.get('version')}）" if previous.get("version") else "老企业版的授权"
    licence = f"，授权 {previous['license_id']}" if previous.get("license_id") else ""
    console.print(
        Panel(
            f"这台机器装着{what}{licence}。\n"
            "flocks update 只升级开源部分；企业版换成了单独的安装包（程序在 /opt/flockspro，数据和授权沿用这台机器的），"
            "老企业版不再更新。\n"
            "升级到新企业版：网页左下角的账号菜单 → Flocks Pro，或者把安装包拷到这台机器后执行\n"
            f"  [bold]{enterprise.situation()['manual_command']}[/bold]",
            title="企业版",
            border_style="yellow",
        )
    )


async def _update(check: bool, yes: bool, force: bool = False, region: str | None = None) -> None:
    from flocks.updater import check_update, detect_deploy_mode, perform_update
    from flocks.updater.deploy import update_refusal_reason
    from flocks.cli.install_profile import is_cn_install_language

    _print_previous_edition_notice()
    if region is None and is_cn_install_language():
        region = "cn"

    if detect_deploy_mode() == "offline":
        # Offline package deployments upgrade by running a new .run; say so before asking
        # about mirrors. check_update() does no network round trips in this mode (it reports
        # the local version only), so there is nothing to wait for or to fail on here.
        from flocks.updater.updater import OFFLINE_UPGRADE_REFUSED_MESSAGE

        console.print(f"[yellow]{OFFLINE_UPGRADE_REFUSED_MESSAGE}[/yellow]")
        _print_version_table(await check_update(region=region))
        return

    if not yes and not check and region is None:
        use_cn_mirror = typer.confirm("\n是否使用中国镜像进行升级？", default=False)
        if use_cn_mirror:
            region = "cn"

    async def _load_update_info(selected_region: str | None):
        with console.status("[cyan]正在检查版本...[/cyan]", spinner="dots"):
            info = await check_update(region=selected_region)

        if info.error:
            append_upgrade_text_log(f"ERROR version_check: {info.error}")
            console.print(f"[red]检查失败：{info.error}[/red]")
            raise typer.Exit(1)

        return info

    info = await _load_update_info(region)

    _print_version_table(info)

    if not info.has_update and not force:
        console.print("[green]✓ 已是最新版本，无需升级[/green]")
        return

    deploy_mode = detect_deploy_mode()
    if deploy_mode == "docker":
        console.print(
            "\n[yellow]Docker deployment detected. In-place upgrade is not available.\n"
            "Please pull the latest image and restart the container to upgrade:\n\n"
            "  [bold]docker pull ghcr.io/agentflocks/flocks:latest[/bold]\n"
            "  [bold]docker restart <container>[/bold][/yellow]"
        )
        return
    refusal = update_refusal_reason(deploy_mode)
    if refusal is not None or not info.update_allowed:
        # Managed release packages are upgraded by their supervisor; the built-in
        # updater must not replace sources or run `uv sync` inside the managed venv.
        append_upgrade_text_log(f"INFO update_refused deploy_mode={deploy_mode}")
        console.print(f"\n[yellow]{refusal or 'In-place upgrade is not available for this deployment.'}[/yellow]")
        return

    if check:
        command = "flocks update --force" if force else "flocks update"
        console.print(f"\n[yellow]运行 [bold]{command}[/bold] 执行升级[/yellow]")
        return

    version_to_apply = info.latest_version or info.current_version
    if not version_to_apply:
        console.print("[red]无法确定要升级到的版本[/red]")
        raise typer.Exit(1)

    if force and not info.has_update:
        console.print(f"[yellow]当前已是最新版本，仍将强制重新安装 v{version_to_apply}[/yellow]")

    if not yes:
        prompt = "\n是否立即升级？"
        if force and not info.has_update:
            prompt = "\n当前已是最新版本，是否仍强制重新安装？"
        confirmed = typer.confirm(prompt, default=False)
        if not confirmed:
            append_upgrade_text_log("INFO update_cancelled user_declined")
            console.print("[yellow]已取消[/yellow]")
            return

    console.print()
    stage_labels = {
        "fetching":    "下载最新源码包",
        "backing_up":  "备份当前版本",
        "applying":    "应用新版本",
        "restarting":  "重启服务",
        "done":        "完成",
    }
    total_steps = 3
    seen_stages: set[str] = set()
    step = 0
    active_stage: str | None = None

    def _finish_active(success: bool = True) -> None:
        """Mark the currently displayed line as done or failed."""
        nonlocal active_stage
        if active_stage is not None:
            if success:
                console.print("[green]✓[/green]")
            else:
                console.print("[red]✗[/red]")
            active_stage = None

    async for progress in perform_update(
        version_to_apply,
        zipball_url=info.zipball_url,
        tarball_url=info.tarball_url,
        bundle_sha256=info.bundle_sha256,
        bundle_format=info.bundle_format,
        restart=True,
        region=region,
        wait_for_handoff=True,
    ):
        if progress.stage == "error":
            _finish_active(success=False)
            console.print(f"\n[red]✗ 升级失败：{progress.message}[/red]")
            raise typer.Exit(1)

        if progress.stage == "done":
            _finish_active(success=True)
            continue

        if progress.stage == "restarting":
            continue

        if progress.stage not in seen_stages:
            _finish_active(success=True)
            seen_stages.add(progress.stage)
            step += 1
            label = stage_labels.get(progress.stage, progress.stage)
            console.print(f"[cyan][{step}/{total_steps}] {label}...[/cyan]  ", end="")
            active_stage = progress.stage

    append_upgrade_text_log(f"OK cli_update_completed version={version_to_apply}")
    console.print(f"\n[green]✓ 升级完成 → v{version_to_apply}[/green]")


def _print_version_table(info) -> None:
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="dim")
    table.add_column()

    table.add_row("当前版本", f"[bold]v{info.current_version}[/bold]")

    if info.latest_version:
        if info.has_update:
            latest_str = f"[bold green]v{info.latest_version}[/bold green]  [yellow]✨ 有新版本[/yellow]"
        else:
            latest_str = f"[bold]v{info.latest_version}[/bold]  [green]✓ 已是最新[/green]"
        table.add_row("最新版本", latest_str)

    console.print(table)

    if info.has_update and info.release_notes:
        notes = info.release_notes.strip()[:800]
        console.print(
            Panel(notes, title="发布说明", border_style="dim", padding=(0, 1))
        )
