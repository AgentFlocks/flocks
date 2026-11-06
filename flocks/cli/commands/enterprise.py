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
    """问 Console 要这台机器平台的企业版安装包（.run；Windows 上是 .exe，会弹管理员确认），下载、校验后运行：开源版停下，换成企业版。"""
    from flocks.cli.service_manager import apply_console_default
    from flocks.updater import enterprise

    # The same Console the page asks: a shell has no address of its own.
    apply_console_default()
    situation = enterprise.situation()
    if not situation["available"]:
        console.print("[yellow]这台机器已经是企业版了：升级用 flocks update，回到开源版用 flocks downgrade[/yellow]")
        raise typer.Exit(1)
    if check:
        offer = _offer_or_exit(enterprise)
        console.print(_offer_line(situation, offer))
        return
    try:
        # The page's lock too: one upgrade of the machine at a time, wherever it is started.
        with enterprise.upgrade_lock():
            code = _upgrade(enterprise, situation, yes)
    except enterprise.EnterpriseUpgradeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None
    raise typer.Exit(code)


@enterprise_app.command("kept")
def kept_command() -> None:
    """列出企业版每次降级回开源版时留下的数据（数据库、配置、密钥）和大小。只列，不删。"""
    from flocks.updater import enterprise

    items = enterprise.kept_after_downgrades()
    if not items:
        console.print("没有降级留下的数据。")
        return
    # One line each with the whole path, never a table cell cut short: the path is what the
    # administrator copies to remove it.
    typer.echo(f"{'留下的时间（UTC）':<18}{'大小':>10}  路径")
    total = 0
    for item in items:
        if item["present"] is False:
            size = "已不在"
        elif item["size"] is None:
            size = "读不到"
        else:
            total += item["size"]
            size = _bytes(item["size"])
        typer.echo(f"{item['kept_at'] or '-':<18}{size:>10}  {item['path']}")
    console.print(f"合计 {_bytes(total)}（读得到的部分）")
    console.print(
        "这些是企业版运行期间的数据，降级时原样留着没删；开源版和以后再升级都不会用到它们。"
        "确认不要了，由管理员手动删除。"
    )


def _bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _offer_or_exit(enterprise):
    try:
        return asyncio.run(enterprise.fetch_offer())
    except enterprise.EnterpriseUpgradeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None


def _offer_line(situation, offer) -> str:
    return f"Console 给这台机器（{situation['platform']}）的企业版安装包：{offer.filename}（{offer.version}{_size(offer)}）"


def _upgrade(enterprise, situation, yes: bool) -> int:
    """The upgrade with the lock held: its exit status, or 1 where nothing was run."""
    current = enterprise.read_state()
    if current is not None and current.get("phase") == "installing":
        console.print("[yellow]安装包已经在运行了（页面或另一个终端起的）：等它跑完，再看 flocks status[/yellow]")
        return 1
    offer = asyncio.run(enterprise.fetch_offer())
    console.print(_offer_line(situation, offer))
    if situation["prepare_command"]:
        console.print(f"[yellow]企业版装在 {situation['install_root']}，这个账号写不了：先用 root 执行一次[/yellow]")
        console.print(f"  [bold]{situation['prepare_command']}[/bold]")
        return 1
    if not yes and not typer.confirm(f"下载并运行 {offer.filename}？开源版会停下，换成企业版", default=False):
        console.print("没有改动。")
        return 1

    shown = {"percent": -1}

    async def progress(received: int, total: int | None) -> None:
        if not total:
            return
        percent = received * 100 // total
        if percent >= shown["percent"] + 10:
            shown["percent"] = percent
            console.print(f"  下载 {percent}%")

    path = asyncio.run(enterprise.download(offer, progress))
    console.print(f"[green]✓ 已下载并校验：{path}[/green]")
    if not enterprise.can_run_installer_here():
        console.print("[yellow]这台机器由 systemd 管（开源离线包），要以 root 运行安装包：[/yellow]")
        console.print(f"  [bold]{enterprise.installer_command(str(path))}[/bold]")
        return 1
    console.print(f"运行 {path.name}（开源版会停下，企业版起来后照旧用 flocks start|stop|status|update）")
    return enterprise.run_installer(path, offer)
