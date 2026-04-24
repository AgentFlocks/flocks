"""
Admin account maintenance commands.
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Dict, Optional

import typer
from rich.console import Console
from rich.table import Table

from flocks.auth.service import AuthService, TEMP_PASSWORD_TTL_HOURS
from flocks.config.config import Config
from flocks.security import get_secret_manager
from flocks.server.auth import API_TOKEN_SECRET_ID

admin_app = typer.Typer(help="Admin account and security maintenance commands")
console = Console()


@admin_app.command("list-users")
def list_users():
    """
    List all local accounts. Useful for recovering a forgotten username.
    """

    async def _run():
        await AuthService.init()
        return await AuthService.list_users()

    try:
        users = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Failed to load accounts: {exc}[/red]")
        raise typer.Exit(1) from exc

    if not users:
        console.print("[yellow]No local accounts have been created yet[/yellow]")
        return

    table = Table(title="Local Accounts")
    table.add_column("Username", style="bold")
    table.add_column("Role")
    table.add_column("Status")
    table.add_column("Last login")

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
    nbytes: int = typer.Option(32, "--bytes", "-b", min=16, max=128, help="Random byte length (32 recommended)"),
):
    """
    Generate and persist an API token for non-browser clients.
    """
    token = secrets.token_urlsafe(nbytes)
    get_secret_manager().set(API_TOKEN_SECRET_ID, token)

    secret_file = Config.get_secret_file()
    console.print("[yellow]API token generated and saved (keep it safe)[/yellow]")
    console.print(f"[bold]{token}[/bold]")
    console.print("")
    console.print(f"[dim]Stored at: {secret_file}[/dim]")
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
        help="API token value to store",
    ),
):
    """
    Write the provided API token to the local .secret.json store
    (used by remote CLI clients or server configuration).
    """
    normalized = token.strip()
    if len(normalized) < 16:
        console.print("[red]API token too short: must be at least 16 characters[/red]")
        raise typer.Exit(1)

    get_secret_manager().set(API_TOKEN_SECRET_ID, normalized)
    secret_file = Config.get_secret_file()
    console.print("[yellow]API token written to local secret store[/yellow]")
    console.print(f"[dim]Stored at: {secret_file}[/dim]")
    console.print(f"[dim]secret_id: {API_TOKEN_SECRET_ID}[/dim]")


@admin_app.command("reassign-orphan-sessions")
def reassign_orphan_sessions(
    username: str = typer.Option(
        "admin", "--username", "-u", help="Target admin username to claim orphan sessions"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Only report counts; do not write changes",
    ),
):
    """
    Claim every session whose owner_user_id is empty for the given admin.

    CLI / background / inbound-channel workers create sessions without an
    auth context, so owner_user_id is left empty. While the system has
    only the bootstrap admin this is harmless, but as soon as a member
    account is added those sessions become invisible to it. Run this to
    backfill the owner.
    """

    async def _run() -> tuple[Optional[str], Dict[str, int]]:  # noqa: UP006 — keep tuple form
        await AuthService.init()
        info = await AuthService.get_user_by_username(username)
        if not info:
            return username, {}
        user, _, _ = info
        if user.role != "admin":
            return username, {"role": -1}
        summary = await AuthService.reassign_orphan_sessions(user.id, dry_run=dry_run)
        return user.username, summary

    try:
        resolved_username, summary = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Reassign failed: {exc}[/red]")
        raise typer.Exit(1) from exc

    if not summary:
        console.print(f"[red]Admin user '{resolved_username}' not found[/red]")
        raise typer.Exit(1)
    if summary.get("role") == -1:
        console.print(f"[red]'{resolved_username}' is not an admin[/red]")
        raise typer.Exit(1)

    mode = "[dim](dry-run)[/dim]" if dry_run else ""
    console.print(
        f"Scanned [bold]{summary['scanned']}[/bold] sessions, "
        f"found [bold]{summary['orphaned']}[/bold] orphans, "
        f"reassigned [bold]{summary['reassigned']}[/bold] to '{resolved_username}' {mode}".rstrip()
    )


@admin_app.command("generate-one-time-password")
def generate_one_time_password(
    username: str = typer.Option("admin", "--username", "-u", help="Admin username"),
):
    """
    Generate a one-time admin password on this host
    (must be changed on first login).
    """

    async def _run() -> str:
        await AuthService.init()
        return await AuthService.generate_admin_temp_password(username=username)

    try:
        temp_password = asyncio.run(_run())
    except Exception as exc:
        console.print(f"[red]Failed to generate one-time password: {exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(
        f"[yellow]One-time admin password generated "
        f"(valid for {TEMP_PASSWORD_TTL_HOURS} hours, password change required on first login)[/yellow]"
    )
    console.print(f"[bold]{temp_password}[/bold]")
