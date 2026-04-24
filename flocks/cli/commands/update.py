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
    check: bool = typer.Option(False, "--check", help="仅检查是否有新版本，不执行升级"),
    yes: bool = typer.Option(False, "--yes", "-y", help="跳过确认直接升级"),
    force: bool = typer.Option(False, "--force", "-f", help="即使已是最新版本也强制重新安装"),
    region: str | None = typer.Option(
        None,
        "--region",
        help="升级镜像区域。设置为 cn 时优先使用中国大陆镜像源。",
    ),
):
    """
    Check for and upgrade Flocks to the latest version.

    Downloads the latest Release source archive from GitHub, backs up the
    current version to ~/.flocks/version/, extracts and replaces source files,
    re-syncs dependencies, then restarts the service automatically.
    """
    asyncio.run(_update(check=check, yes=yes, force=force, region=region))


async def _update(check: bool, yes: bool, force: bool = False, region: str | None = None) -> None:
    from flocks.updater import check_update, perform_update, detect_deploy_mode

    with console.status("[cyan]正在检查版本...[/cyan]", spinner="dots"):
        info = await check_update(region=region)

    if info.error:
        append_upgrade_text_log(f"ERROR version_check: {info.error}")
        console.print(f"[red]检查失败：{info.error}[/red]")
        raise typer.Exit(1)

    _print_version_table(info)

    if not info.has_update and not force:
        console.print("[green]✓ 已是最新版本，无需升级[/green]")
        return

    if detect_deploy_mode() == "docker":
        console.print(
            "\n[yellow]Docker deployment detected. In-place upgrade is not available.\n"
            "Please pull the latest image and restart the container to upgrade:\n\n"
            "  [bold]docker pull ghcr.io/agentflocks/flocks:latest[/bold]\n"
            "  [bold]docker restart <container>[/bold][/yellow]"
        )
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
        "applying":    f"应用 v{info.latest_version}",
        "syncing":     "同步依赖",
        "building":    "构建前端",
        "restarting":  "重启服务",
        "done":        "完成",
    }
    total_steps = 6
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
        restart=False,
        region=region,
    ):
        if progress.stage == "error":
            _finish_active(success=False)
            console.print(f"\n[red]✗ 升级失败：{progress.message}[/red]")
            raise typer.Exit(1)

        if progress.stage == "done":
            _finish_active(success=True)
            step += 1
            console.print(f"[cyan][{step}/{total_steps}] 完成[/cyan]  ", end="")
            console.print("[green]✓[/green]")
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
    console.print("[dim]如有后台服务正在运行，请执行 [bold]flocks restart[/bold] 重启服务[/dim]")


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
