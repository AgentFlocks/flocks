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
from flocks.net.http import async_client


import_app = typer.Typer(
    name="import",
    help="Import session data",
    # No subcommands: `flocks import file.json -p proj` must parse `-p` as an
    # option of the callback rather than a command name.
    context_settings={"allow_interspersed_args": True},
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


async def _adopt_part_upload(part: Any, session_id: str) -> Any:
    """A normalized file part of an imported copy, pointing at the copy's own upload.

    Any session upload on this machine counts, not only the exported
    session's: an older fork points at its ancestor's.  A legacy part's
    ``metadata.url`` names the same file and moves with it.
    """
    if not isinstance(part, dict) or part.get("type") != "file" or not part.get("url"):
        return part
    from flocks.session.files import adopt_session_upload

    adopted = dict(part)
    adopted["url"] = await adopt_session_upload(session_id, part["url"])
    metadata = adopted.get("metadata")
    if isinstance(metadata, dict) and metadata.get("url") == part["url"]:
        adopted["metadata"] = {**metadata, "url": adopted["url"]}
    if "sessionID" in adopted:
        adopted["sessionID"] = session_id
    return adopted


async def _imported_parts(parts: Any, *, message_time: Any, new_session_id: Optional[str]) -> list:
    """The parts as stored: normalized first (a legacy part's URL is in its
    metadata), then, for a copy on this machine, with uploads of its own."""
    normalized = [_normalize_part_data(part, message_time=message_time) for part in parts]
    if new_session_id is None:
        return normalized
    return [await _adopt_part_upload(part, new_session_id) for part in normalized]


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

    if part_type == "subtask":
        normalized["type"] = "text"
        normalized["text"] = ""
        normalized["ignored"] = True
        normalized["metadata"] = {
            **metadata_dict,
            "legacyPartType": "subtask",
        }
        part_type = "text"
        metadata_dict = normalized["metadata"]

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
    force: bool = typer.Option(
        False, "-f", "--force",
        help="Overwrite an existing session with the same ID instead of refusing",
    ),
    new_id: bool = typer.Option(
        False, "--new-id",
        help="Import as a new session with a freshly generated ID (never collides)",
    ),
    keep_directory: bool = typer.Option(
        False, "--keep-directory",
        help="Keep the exporting machine's session directory instead of relocating it to the current project directory",
    ),
):
    """
    Import session data from JSON file or URL
    
    Supports:
    - Local JSON files exported with 'flocks export'
    - Share URLs (https://opncd.ai/share/<slug> or https://flocks.ai/share/<slug>)

    An existing session with the same ID is not overwritten unless --force is
    given; --new-id imports the data under a fresh session ID instead. The
    session directory is relocated to the current project directory so the
    imported session shows up in session lists here (--keep-directory opts out).
    """
    asyncio.run(_import_session(file_or_url, project, force=force, new_id=new_id, keep_directory=keep_directory))


def _relocate_session_directory(session_info: dict, target_directory: str) -> Optional[str]:
    """Point the imported session at *target_directory*; return the previous value when it changed."""
    previous = session_info.get("directory")
    if previous == target_directory:
        return None
    session_info["directory"] = target_directory
    return previous


async def _import_session(
    file_or_url: str,
    project_id: Optional[str],
    *,
    force: bool = False,
    new_id: bool = False,
    keep_directory: bool = False,
):
    """Internal import implementation"""
    await Storage.init()
    
    # Get project
    target_directory = os.getcwd()
    if not project_id:
        result = await Project.from_directory(target_directory)
        project_id = result["project"].id
    
    export_data = None
    
    # Check if URL or file
    is_url = file_or_url.startswith("http://") or file_or_url.startswith("https://")
    
    if is_url:
        # Handle share URL
        match = SHARE_URL_PATTERN.fullmatch(file_or_url)
        if not match:
            console.print(f"[red]Invalid URL format. Expected: https://opncd.ai/share/<slug> or https://flocks.ai/share/<slug>[/red]")
            raise typer.Exit(1)
        
        slug = match.group(1)
        
        console.print(f"[dim]Fetching share data for {slug}...[/dim]")
        
        try:
            import httpx
            async with async_client() as client:
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

        original_id = session_info["id"]
        if new_id:
            from flocks.utils.id import Identifier

            session_info["id"] = Identifier.descending("session")

        # Sessions exported elsewhere carry that machine's working directory;
        # lists here filter by the current worktree, so an imported session
        # would be invisible (only reachable by ID) unless it is relocated.
        previous_directory = None
        if not keep_directory:
            previous_directory = _relocate_session_directory(session_info, target_directory)

        # Store session
        session_key = f"session:{project_id}:{session_info['id']}"
        existing = await Storage.get(session_key)
        if existing is not None and not force and not new_id:
            existing_title = existing.get("title", "Untitled") if isinstance(existing, dict) else "Untitled"
            console.print(
                f"[red]Session already exists: {session_info['id']} ('{existing_title}'). "
                "Re-run with --force to overwrite it or --new-id to import as a new session.[/red]"
            )
            raise typer.Exit(1)
        
        # Store messages using the runtime's per-message parts format.
        # WebUI/back-end still returns the same export JSON shape through
        # Message.list_with_parts(); only the storage layout changes.
        message_count = 0
        serialized_messages = []
        serialized_parts = {}
        for msg_data in export_data["messages"]:
            msg_info = msg_data.get("info", {})
            parts = msg_data.get("parts", [])
            if new_id and msg_info.get("sessionID") == original_id:
                msg_info["sessionID"] = session_info["id"]

            serialized_messages.append(msg_info)
            if "id" in msg_info:
                # A copy of a session on this machine gets its own copies of
                # the uploads it shows (trimming or deleting the original then
                # takes nothing from it); elsewhere the files are not here.
                serialized_parts[msg_info["id"]] = await _imported_parts(
                    parts, message_time=msg_info.get("time"), new_session_id=session_info["id"] if new_id else None,
                )

            message_count += 1

        session_id = session_info["id"]
        from flocks.session.message_pages import (
            SessionMessagesDeletedError, bump_revision, lock_session, replace_metadata,
        )

        async def lock_message_projection(db):
            try:
                await lock_session(db, session_id)
            except SessionMessagesDeletedError as exc:
                raise ValueError("Session was deleted; use --new-id to import a new session") from exc
            if not force:
                row = await db.fetch_one("SELECT 1 FROM runtime_state WHERE key = ?", (session_key,))
                if row is not None:
                    raise ValueError("Session already exists; use --new-id to import a new session")
            # The uploads the imported parts name: the same lock and tombstone
            # check as every other write of parts (an upload being removed is
            # either seen named here or refuses this import).
            stored_rows = await db.fetch_all(
                "SELECT value FROM runtime_state WHERE key = ANY(?::text[])",
                ([f"message_parts:{session_id}:{message_id}" for message_id in serialized_parts],),
            )
            stored = [
                part for row in stored_rows
                for part in (json.loads(row[0]) if isinstance(row[0], str) else (row[0] or []))
            ]
            await guard_new_upload_references(
                db, stored, [part for parts in serialized_parts.values() for part in parts],
            )

        async def update_message_projection(db):
            await replace_metadata(db, session_id, serialized_messages)
            await bump_revision(db, session_id)

        from flocks.session.message import _named_uploads, guard_new_upload_references

        if any(_named_uploads(parts) for parts in serialized_parts.values()):
            from flocks.storage.file_mirror import FileMirror

            await FileMirror._pool()  # the tombstone table, created outside the import's transaction
        await Storage.mutate_many(
            set_entries=[
                (session_key, session_info, "session"),
                (f"message:{session_id}", serialized_messages, "message"),
                *[
                    (f"message_parts:{session_id}:{message_id}", parts, "message_part")
                    for message_id, parts in serialized_parts.items()
                ],
            ],
            pre_transaction_hook=lock_message_projection,
            transaction_hook=update_message_projection,
        )
        
        console.print(f"[green]Imported session: {session_info['id']}[/green]")
        if new_id and session_info["id"] != original_id:
            console.print(f"  Original ID: {original_id}")
        console.print(f"  Title: {session_info.get('title', 'Untitled')}")
        console.print(f"  Messages: {message_count}")
        if previous_directory is not None:
            console.print(f"  Directory: {session_info['directory']} (was {previous_directory})")
    
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Failed to import session: {e}[/red]")
        raise typer.Exit(1)
