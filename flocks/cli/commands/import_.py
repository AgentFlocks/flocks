"""
Import CLI command

Imports session data from JSON file or URL
Ported from original cli/cmd/import.ts
"""

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

import typer
from rich.console import Console

from flocks.project.project import Project
from flocks.storage.storage import Storage


import_app = typer.Typer(
    name="import",
    help="Import session data",
)

console = Console()


# Pattern for share URLs
SHARE_URL_PATTERN = re.compile(r"https?://(?:opncd\.ai|flocks\.ai)/share/([a-zA-Z0-9_-]+)")


def _default_part_time(message_time: Optional[dict[str, Any]] = None) -> dict[str, int | None]:
    """Provide best-effort timestamps for legacy imported parts."""
    time_info = message_time if isinstance(message_time, dict) else {}
    start = time_info.get("created") or time_info.get("start") or 0
    end = time_info.get("updated") or time_info.get("completed") or time_info.get("end")
    if end is None and start:
        end = start
    return {"start": int(start), "end": int(end) if end is not None else None}


def _normalize_tool_state(
    raw_state: Any,
    *,
    message_time: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Convert legacy tool state payloads to the current shape."""
    fallback_time = _default_part_time(message_time)
    if isinstance(raw_state, dict):
        normalized = dict(raw_state)
    else:
        normalized = {}
        if isinstance(raw_state, str):
            normalized["status"] = raw_state

    status = normalized.get("status")
    if status not in {"pending", "running", "completed", "error"}:
        if "output" in normalized:
            status = "completed"
        elif "error" in normalized:
            status = "error"
        elif "time" in normalized:
            status = "running"
        else:
            status = "pending"
    normalized["status"] = status

    if status == "pending":
        normalized.setdefault("input", {})
        normalized.setdefault("raw", "")
    elif status == "running":
        normalized.setdefault("input", {})
        normalized.setdefault("time", fallback_time)
    elif status == "completed":
        normalized.setdefault("input", {})
        normalized.setdefault("output", "")
        normalized.setdefault("title", "")
        normalized.setdefault("metadata", {})
        normalized.setdefault("time", fallback_time)
    elif status == "error":
        normalized.setdefault("input", {})
        normalized.setdefault("error", "")
        normalized.setdefault("time", fallback_time)

    return normalized


def _normalize_part_data(
    part_data: dict[str, Any],
    *,
    message_time: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Normalize legacy/exported part payloads before persisting."""
    normalized = dict(part_data)
    part_type = normalized.get("type", "text")
    metadata = normalized.get("metadata")
    metadata_dict = metadata if isinstance(metadata, dict) else {}

    if "content" in normalized and "text" not in normalized:
        normalized["text"] = normalized.get("content", "")

    if part_type == "text":
        normalized.setdefault("text", "")
    elif part_type == "reasoning":
        normalized.setdefault("text", normalized.get("content", ""))
        normalized.setdefault("metadata", metadata_dict or None)
        normalized.setdefault("time", _default_part_time(message_time))
    elif part_type == "tool":
        normalized.setdefault("callID", metadata_dict.get("callID") or normalized.get("id", ""))
        normalized.setdefault("tool", metadata_dict.get("tool") or normalized.get("tool", "unknown"))
        raw_state = normalized.get("state")
        if raw_state is None and metadata_dict:
            raw_state = metadata_dict.get("state")
        normalized["state"] = _normalize_tool_state(raw_state, message_time=message_time)
        normalized.setdefault("metadata", metadata_dict or None)
    elif part_type == "file":
        normalized.setdefault("mime", metadata_dict.get("mime") or "application/octet-stream")
        normalized.setdefault("filename", metadata_dict.get("filename"))
        normalized.setdefault("url", metadata_dict.get("url") or normalized.get("content", ""))
    elif part_type == "snapshot":
        normalized.setdefault("snapshot", metadata_dict.get("snapshot") or normalized.get("content", ""))
    elif part_type == "patch":
        normalized.setdefault("hash", metadata_dict.get("hash") or "")
        normalized.setdefault("files", metadata_dict.get("files") or [])
    elif part_type == "step-finish":
        normalized.setdefault("reason", metadata_dict.get("reason") or "completed")
        normalized.setdefault("snapshot", metadata_dict.get("snapshot"))
        normalized.setdefault("cost", metadata_dict.get("cost") or 0.0)
        normalized.setdefault(
            "tokens",
            metadata_dict.get("tokens")
            or {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
    elif part_type == "agent":
        normalized.setdefault("name", metadata_dict.get("name") or normalized.get("content") or "agent")
    elif part_type == "subtask":
        normalized.setdefault("prompt", metadata_dict.get("prompt") or normalized.get("content", ""))
        normalized.setdefault("description", metadata_dict.get("description") or "")
        normalized.setdefault("agent", metadata_dict.get("agent") or "agent")
    elif part_type == "retry":
        normalized.setdefault("attempt", metadata_dict.get("attempt") or 1)
        normalized.setdefault("error", metadata_dict.get("error") or {})
        normalized.setdefault("time", metadata_dict.get("time") or _default_part_time(message_time))
    elif part_type == "compaction":
        normalized.setdefault("auto", bool(metadata_dict.get("auto", False)))

    return normalized


@import_app.callback(invoke_without_command=True)
def import_session(
    file_or_url: str = typer.Argument(..., help="Path to JSON file or share URL"),
    project: Optional[str] = typer.Option(
        None, "-p", "--project",
        help="Project ID (uses current project if not specified)"
    ),
):
    """
    Import session data from JSON file or URL
    
    Supports:
    - Local JSON files exported with 'flocks export'
    - Share URLs (https://opncd.ai/share/<slug> or https://flocks.ai/share/<slug>)
    """
    asyncio.run(_import_session(file_or_url, project))


async def _import_session(file_or_url: str, project_id: Optional[str]):
    """Internal import implementation"""
    await Storage.init()
    
    # Get project
    if not project_id:
        result = await Project.from_directory(os.getcwd())
        project_id = result["project"].id
    
    export_data = None
    
    # Check if URL or file
    is_url = file_or_url.startswith("http://") or file_or_url.startswith("https://")
    
    if is_url:
        # Handle share URL
        match = SHARE_URL_PATTERN.match(file_or_url)
        if not match:
            console.print(f"[red]Invalid URL format. Expected: https://opncd.ai/share/<slug> or https://flocks.ai/share/<slug>[/red]")
            raise typer.Exit(1)
        
        slug = match.group(1)
        
        console.print(f"[dim]Fetching share data for {slug}...[/dim]")
        
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                # Try opncd.ai first
                response = await client.get(f"https://opncd.ai/api/share/{slug}")
                
                if response.status_code != 200:
                    # Try flocks.ai
                    response = await client.get(f"https://flocks.ai/api/share/{slug}")
                
                if response.status_code != 200:
                    console.print(f"[red]Failed to fetch share data: {response.status_code}[/red]")
                    raise typer.Exit(1)
                
                data = response.json()
                
                if not data.get("info") or not data.get("messages"):
                    console.print(f"[red]Share not found: {slug}[/red]")
                    raise typer.Exit(1)
                
                # Convert share format to export format
                export_data = {
                    "info": data["info"],
                    "messages": [
                        {
                            "info": {k: v for k, v in msg.items() if k != "parts"},
                            "parts": msg.get("parts", []),
                        }
                        for msg in data.get("messages", {}).values()
                    ],
                }
        
        except ImportError:
            console.print("[red]httpx is required for URL imports. Install with: pip install httpx[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Failed to fetch share data: {e}[/red]")
            raise typer.Exit(1)
    
    else:
        # Handle local file
        file_path = Path(file_or_url)
        
        if not file_path.exists():
            console.print(f"[red]File not found: {file_or_url}[/red]")
            raise typer.Exit(1)
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                export_data = json.load(f)
        except json.JSONDecodeError as e:
            console.print(f"[red]Invalid JSON file: {e}[/red]")
            raise typer.Exit(1)
        except Exception as e:
            console.print(f"[red]Failed to read file: {e}[/red]")
            raise typer.Exit(1)
    
    if not export_data:
        console.print("[red]Failed to read session data[/red]")
        raise typer.Exit(1)
    
    # Validate structure
    if "info" not in export_data:
        console.print("[red]Invalid export format: missing 'info' field[/red]")
        raise typer.Exit(1)
    
    if "messages" not in export_data:
        console.print("[red]Invalid export format: missing 'messages' field[/red]")
        raise typer.Exit(1)
    
    # Import session
    console.print("[dim]Importing session...[/dim]")
    
    try:
        session_info = export_data["info"]
        
        # Ensure project_id matches
        session_info["projectID"] = project_id
        
        # Store session
        session_key = f"session:{project_id}:{session_info['id']}"
        await Storage.set(session_key, session_info, "session")
        
        # Store messages using the runtime's aggregated storage format.
        # WebUI/back-end reads `message:<session_id>` and
        # `message_parts:<session_id>` instead of legacy per-message keys.
        message_count = 0
        serialized_messages = []
        serialized_parts = {}
        for msg_data in export_data["messages"]:
            msg_info = msg_data.get("info", {})
            parts = msg_data.get("parts", [])

            serialized_messages.append(msg_info)
            if "id" in msg_info:
                serialized_parts[msg_info["id"]] = [
                    _normalize_part_data(part, message_time=msg_info.get("time"))
                    for part in parts
                ]

            message_count += 1

        session_id = session_info["id"]
        await Storage.set(f"message:{session_id}", serialized_messages, "message")
        await Storage.set(f"message_parts:{session_id}", serialized_parts, "message_parts")
        
        console.print(f"[green]Imported session: {session_info['id']}[/green]")
        console.print(f"  Title: {session_info.get('title', 'Untitled')}")
        console.print(f"  Messages: {message_count}")
    
    except Exception as e:
        console.print(f"[red]Failed to import session: {e}[/red]")
        raise typer.Exit(1)
