"""
flocks enterprise upgrade — from this open install to the enterprise edition, in the terminal.

The way the Flocks Pro page takes (flocks.updater.enterprise): ask Console for the installer built
for this machine's platform under the licence the machine has, download it and check its digest,
and run it -- here in the foreground, so its output is this command's. A machine run by systemd (the
open edition's offline package) is switched by root: the installer is downloaded and the root
command is given, as the page gives it.
"""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console

console = Console()

enterprise_app = typer.Typer(
    name="enterprise",
    help="换装企业版（和网页「Flocks Pro」页走同一条路）",
    no_args_is_help=True,
)


def _size(offer) -> str:
    return f"，{offer.size / (1 << 20):.0f} MB" if offer.size else ""


@enterprise_app.command("upgrade")
def upgrade_command(
    check: bool = typer.Option(False, "--check", help="只看 Console 给这台机器的安装包，不下载"),
    yes: bool = typer.Option(False, "--yes", "-y", help="不再确认"),
) -> None:
    """问 Console 要这台机器平台的企业版安装包（.run），下载、校验后运行：开源版停下，换成企业版。"""
    from flocks.updater import enterprise

    situation = enterprise.situation()
    if not situation["available"]:
        console.print("[yellow]这台机器已经是企业版了：升级用 flocks update，回到开源版用 flocks downgrade[/yellow]")
        raise typer.Exit(1)
    try:
        offer = asyncio.run(enterprise.fetch_offer())
    except enterprise.EnterpriseUpgradeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(
        f"Console 给这台机器（{situation['platform']}）的企业版安装包：{offer.filename}（{offer.version}{_size(offer)}）"
    )
    if check:
        return
    if situation["prepare_command"]:
        console.print(f"[yellow]企业版装在 {situation['install_root']}，这个账号写不了：先用 root 执行一次[/yellow]")
        console.print(f"  [bold]{situation['prepare_command']}[/bold]")
        raise typer.Exit(1)
    if not yes and not typer.confirm(f"下载并运行 {offer.filename}？开源版会停下，换成企业版", default=False):
        console.print("没有改动。")
        raise typer.Exit(1)

    shown = {"percent": -1}

    async def progress(received: int, total: int | None) -> None:
        if not total:
            return
        percent = received * 100 // total
        if percent >= shown["percent"] + 10:
            shown["percent"] = percent
            console.print(f"  下载 {percent}%")

    try:
        path = asyncio.run(enterprise.download(offer, progress))
    except enterprise.EnterpriseUpgradeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    console.print(f"[green]✓ 已下载并校验：{path}[/green]")
    if not enterprise.can_run_installer_here():
        console.print("[yellow]这台机器由 systemd 管（开源离线包），要以 root 运行安装包：[/yellow]")
        console.print(f"  [bold]{enterprise.installer_command(str(path))}[/bold]")
        raise typer.Exit(1)
    console.print(f"运行 {path.name}（开源版会停下，企业版起来后照旧用 flocks start|stop|status|update）")
    raise typer.Exit(enterprise.run_installer(path))
