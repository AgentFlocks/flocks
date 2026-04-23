"""
Admin account maintenance commands.
"""

from __future__ import annotations

import asyncio
import secrets

import typer
from rich.console import Console
from rich.table import Table

from flocks.auth.service import AuthService
from flocks.config.config import Config
from flocks.security import get_secret_manager
from flocks.server.auth import API_TOKEN_SECRET_ID

admin_app = typer.Typer(help="管理员账号与安全维护命令")
console = Console()


@admin_app.command("list-users")
def list_users():
    """
    列出本机已创建的账号，便于管理员找回账号名。
    """

    async def _run():
        await AuthService.init()
        return await AuthService.list_users()

    try:
        users = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]获取账号列表失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    if not users:
        console.print("[yellow]当前未创建任何账号[/yellow]")
        return

    table = Table(title="账号列表")
    table.add_column("用户名", style="bold")
    table.add_column("角色")
    table.add_column("状态")
    table.add_column("最近登录")

    for user in users:
        table.add_row(
            user.username,
            user.role,
            user.status,
            user.last_login_at or "-",
        )

    console.print(table)


@admin_app.command("generate-api-token")
def generate_api_token(
    nbytes: int = typer.Option(32, "--bytes", "-b", min=16, max=128, help="随机字节数（建议 32）"),
):
    """
    生成并保存用于非浏览器调用的 API Token。
    """
    token = secrets.token_urlsafe(nbytes)
    get_secret_manager().set(API_TOKEN_SECRET_ID, token)

    secret_file = Config.get_secret_file()
    console.print("[yellow]已生成并保存 API Token（请妥善保存）[/yellow]")
    console.print(f"[bold]{token}[/bold]")
    console.print("")
    console.print(f"[dim]保存位置: {secret_file}[/dim]")
    console.print(f"[dim]secret_id: {API_TOKEN_SECRET_ID}[/dim]")


@admin_app.command("set-api-token")
def set_api_token(
    token: str = typer.Option(
        ...,
        "--token",
        "-t",
        prompt=True,
        hide_input=True,
        confirmation_prompt=True,
        help="要写入的 API Token",
    ),
):
    """
    将指定 API Token 写入本机 .secret.json（用于远程 CLI 客户端或服务端配置）。
    """
    normalized = token.strip()
    if len(normalized) < 16:
        console.print("[red]API Token 长度过短，至少 16 个字符[/red]")
        raise typer.Exit(1)

    get_secret_manager().set(API_TOKEN_SECRET_ID, normalized)
    secret_file = Config.get_secret_file()
    console.print("[yellow]API Token 已写入本机 secret 存储[/yellow]")
    console.print(f"[dim]保存位置: {secret_file}[/dim]")
    console.print(f"[dim]secret_id: {API_TOKEN_SECRET_ID}[/dim]")


@admin_app.command("generate-one-time-password")
def generate_one_time_password(
    username: str = typer.Option("admin", "--username", "-u", help="管理员用户名"),
):
    """
    在服务器上生成管理员一次性密码（首次登录需强制改密）。
    """

    async def _run() -> str:
        await AuthService.init()
        return await AuthService.generate_admin_temp_password(username=username)

    try:
        temp_password = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]生成一次性密码失败: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print("[yellow]管理员一次性密码已生成（24小时有效，首次登录需改密）[/yellow]")
    console.print(f"[bold]{temp_password}[/bold]")
