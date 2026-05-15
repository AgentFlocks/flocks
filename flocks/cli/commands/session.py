"""
Session CLI commands

Provides session management commands: list, show, delete
Ported from original cli/cmd/session.ts
"""

import asyncio
import json
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from flocks.session.session import Session
from flocks.project.project import Project
from flocks.storage.storage import Storage
from flocks.utils.locale import Locale


session_app = typer.Typer(
    name="session",
    help="Manage sessions",
    no_args_is_help=True,
)

console = Console()


def _format_time(timestamp_ms: int) -> str:
    """Format timestamp for display"""
    return Locale.today_time_or_datetime(timestamp_ms)


def _truncate(text: str, max_width: int) -> str:
    """Truncate text to max width with ellipsis"""
    if len(text) <= max_width:
        return text
    return text[:max_width - 3] + "..."


@session_app.command("list")
def session_list(
    max_count: Optional[int] = typer.Option(
        None, "-n", "--max-count",
        help="Limit to N most recent sessions"
    ),
    format: str = typer.Option(
        "table", "--format",
        help="Output format: table or json"
    ),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Filter by project ID (empty for current project)"
    ),
):
    """
    List all sessions
    
    Shows sessions sorted by last updated time (newest first).
    Use --format json for machine-readable output.
    """
    asyncio.run(_list_sessions(max_count, format, project))


async def _list_sessions(
    max_count: Optional[int],
    format: str,
    project_filter: Optional[str]
):
    """Internal session list implementation"""
    await Storage.init()

    all_sessions = await Session.list_all()

    if project_filter == "":
        result = await Project.from_directory(os.getcwd())
        current_project_id = result["project"].id
        all_sessions = [s for s in all_sessions if s.project_id == current_project_id]
    elif project_filter:
        all_sessions = [s for s in all_sessions if s.project_id == project_filter]

    # Only include parent sessions (not child/forked sessions)
    all_sessions = [s for s in all_sessions if not s.parent_id]
    
    # Sort by updated time (newest first)
    all_sessions.sort(key=lambda s: s.time.updated, reverse=True)
    
    # Apply limit
    if max_count:
        all_sessions = all_sessions[:max_count]
    
    if not all_sessions:
        console.print("[dim]No sessions found[/dim]")
        return
    
    # Output
    if format == "json":
        json_data = [
            {
                "id": s.id,
                "title": s.title,
                "updated": s.time.updated,
                "created": s.time.created,
                "projectId": s.project_id,
                "directory": s.directory,
            }
            for s in all_sessions
        ]
        console.print(json.dumps(json_data, indent=2))
    else:
        # Table format
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("Session ID", style="dim")
        table.add_column("Title", style="")
        table.add_column("Updated", style="green")
        
        for session in all_sessions:
            table.add_row(
                session.id,
                _truncate(session.title, 50),
                _format_time(session.time.updated)
            )
        
        console.print(table)
        console.print(f"\n[dim]{len(all_sessions)} session(s)[/dim]")


async def _resolve_session(session_id: str, project_id: Optional[str]) -> Optional[tuple[str, object]]:
    """Resolve a session, defaulting to global session lookup."""
    if project_id:
        session = await Session.get(project_id, session_id)
        return (project_id, session) if session else None

    session = await Session.get_by_id(session_id)
    if not session:
        return None
    return session.project_id, session


@session_app.command("show")
def session_show(
    session_id: str = typer.Argument(..., help="Session ID to show"),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Project ID (uses current project if not specified)"
    ),
):
    """
    Show details of a specific session
    """
    asyncio.run(_show_session(session_id, project))


async def _show_session(session_id: str, project_id: Optional[str]):
    """Internal session show implementation"""
    await Storage.init()

    resolved = await _resolve_session(session_id, project_id)
    if not resolved:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)

    _, session = resolved
    
    # Display session info
    console.print()
    console.print(f"[bold cyan]Session: {session.id}[/bold cyan]")
    console.print(f"  Title:     {session.title}")
    console.print(f"  Project:   {session.project_id}")
    console.print(f"  Directory: {session.directory}")
    console.print(f"  Status:    {session.status}")
    console.print(f"  Agent:     {session.agent or 'default'}")
    
    if session.model:
        console.print(f"  Model:     {session.provider}/{session.model}")
    
    if session.parent_id:
        console.print(f"  Parent:    {session.parent_id}")
    
    console.print()
    console.print("[dim]Timestamps:[/dim]")
    console.print(f"  Created:   {_format_time(session.time.created)}")
    console.print(f"  Updated:   {_format_time(session.time.updated)}")
    
    if session.time.archived:
        console.print(f"  Archived:  {_format_time(session.time.archived)}")
    
    if session.summary:
        console.print()
        console.print("[dim]Summary:[/dim]")
        console.print(f"  Files:     {session.summary.files}")
        console.print(f"  Additions: +{session.summary.additions}")
        console.print(f"  Deletions: -{session.summary.deletions}")
    
    # Get message count
    message_count = await Session.get_message_count(session_id)
    console.print()
    console.print(f"[dim]Messages: {message_count}[/dim]")


@session_app.command("delete")
def session_delete(
    session_id: str = typer.Argument(..., help="Session ID to delete"),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Project ID (uses current project if not specified)"
    ),
    force: bool = typer.Option(
        False, "-f", "--force",
        help="Skip confirmation prompt"
    ),
):
    """
    Delete a session
    
    This performs a soft delete. The session data is marked as deleted
    but not permanently removed from storage.
    """
    asyncio.run(_delete_session(session_id, project, force))


async def _delete_session(session_id: str, project_id: Optional[str], force: bool):
    """Internal session delete implementation"""
    await Storage.init()

    resolved = await _resolve_session(session_id, project_id)
    if not resolved:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)

    project_id, session = resolved
    
    # Confirm deletion
    if not force:
        confirm = typer.confirm(
            f"Delete session '{session.title}'?",
            default=False
        )
        if not confirm:
            console.print("[yellow]Cancelled[/yellow]")
            return
    
    # Delete session
    success = await Session.delete(project_id, session_id)
    
    if success:
        console.print(f"[green]Deleted session: {session_id}[/green]")
    else:
        console.print(f"[red]Failed to delete session: {session_id}[/red]")
        raise typer.Exit(1)


@session_app.command("archive")
def session_archive(
    session_id: str = typer.Argument(..., help="Session ID to archive"),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Project ID (uses current project if not specified)"
    ),
):
    """
    Archive a session
    
    Archived sessions are hidden from normal listings but can be restored.
    """
    asyncio.run(_archive_session(session_id, project))


async def _archive_session(session_id: str, project_id: Optional[str]):
    """Internal session archive implementation"""
    await Storage.init()

    resolved = await _resolve_session(session_id, project_id)
    if not resolved:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)

    project_id, _ = resolved

    success = await Session.archive(project_id, session_id)
    
    if success:
        console.print(f"[green]Archived session: {session_id}[/green]")
    else:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)


@session_app.command("restore")
def session_restore(
    session_id: str = typer.Argument(..., help="Session ID to restore"),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Project ID (uses current project if not specified)"
    ),
):
    """
    Restore an archived session
    """
    asyncio.run(_restore_session(session_id, project))


async def _restore_session(session_id: str, project_id: Optional[str]):
    """Internal session restore implementation"""
    await Storage.init()

    resolved = await _resolve_session(session_id, project_id)
    if not resolved:
        console.print(f"[red]Failed to restore session (not found or not archived): {session_id}[/red]")
        raise typer.Exit(1)

    project_id, _ = resolved

    success = await Session.unarchive(project_id, session_id)
    
    if success:
        console.print(f"[green]Restored session: {session_id}[/green]")
    else:
        console.print(f"[red]Failed to restore session (not found or not archived): {session_id}[/red]")
        raise typer.Exit(1)
