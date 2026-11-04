"""
Export CLI command

Exports session data as JSON
Ported from original cli/cmd/export.ts
"""

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Prompt

from flocks.session.session import Session
from flocks.session.message import Message
from flocks.storage.storage import Storage


export_app = typer.Typer(
    name="export",
    help="Export session data",
    # The group has no subcommands: options given after SESSION_ID
    # (`flocks export ses_1 --no-pretty`) must reach the callback instead of
    # being looked up as a command name.
    context_settings={"allow_interspersed_args": True},
)

console = Console(stderr=True)  # Use stderr for prompts


@export_app.callback(invoke_without_command=True)
def export_session(
    session_id: Optional[str] = typer.Argument(None, help="Session ID to export"),
    output: Optional[str] = typer.Option(
        None, "-o", "--output",
        help="Output file path (defaults to stdout)"
    ),
    pretty: bool = typer.Option(
        True, "--pretty/--no-pretty",
        help="Pretty print JSON output"
    ),
):
    """
    Export session data as JSON
    
    If session_id is not provided, prompts for selection from available sessions.
    Output goes to stdout by default, use -o to specify a file.
    """
    asyncio.run(_export_session(session_id, output, pretty))


async def _export_session(
    session_id: Optional[str],
    output_path: Optional[str],
    pretty: bool,
):
    """Internal export implementation"""
    await Storage.init()
    
    # If no session_id, prompt for selection
    if not session_id:
        console.print("[bold cyan]Export session[/bold cyan]")
        console.print()
        
        sessions = await Session.list_all()
        
        if not sessions:
            console.print("[red]No sessions found[/red]")
            raise typer.Exit(1)
        
        # Sort by updated time
        sessions.sort(key=lambda s: s.time.updated, reverse=True)
        
        # Display options
        console.print("Select session to export:")
        for i, session in enumerate(sessions[:20], 1):
            from datetime import datetime
            updated = datetime.fromtimestamp(session.time.updated / 1000)
            console.print(f"  {i:2}. {session.title[:40]:<40} • {updated.strftime('%Y-%m-%d %H:%M')} • {session.id[-8:]}")
        
        if len(sessions) > 20:
            console.print(f"  [dim]... and {len(sessions) - 20} more[/dim]")
        
        # Prompt on stderr too: stdout must stay a clean JSON stream for pipes.
        choice = Prompt.ask("\nEnter number or session ID", console=console)
        
        # Check if it's a number
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(sessions):
                session_id = sessions[idx].id
            else:
                console.print("[red]Invalid selection[/red]")
                raise typer.Exit(1)
        except ValueError:
            # Assume it's a session ID
            session_id = choice
        
        console.print("[dim]Exporting session...[/dim]")
    
    # Get session
    session = await Session.get_by_id(session_id)
    
    if not session:
        console.print(f"[red]Session not found: {session_id}[/red]")
        raise typer.Exit(1)
    
    # Get messages with parts
    messages = await Message.list_with_parts(session_id)
    
    # Build export data matching Flocks format
    export_data = {
        "info": session.model_dump(by_alias=True),
        "messages": [
            {
                "info": msg.info.model_dump(by_alias=True),
                "parts": [part.model_dump() for part in msg.parts],
            }
            for msg in messages
        ],
    }
    
    # Output
    indent = 2 if pretty else None
    json_output = json.dumps(export_data, indent=indent, ensure_ascii=False)
    
    if output_path:
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(json_output)
                f.write("\n")
        except OSError as error:
            console.print(f"[red]Failed to write export file {output_path}: {error.strerror or error}[/red]")
            raise typer.Exit(1) from error
        console.print(f"[green]Exported to {output_path}[/green]")
    else:
        # Write to stdout (not stderr)
        print(json_output)
