"""Writable Bash directories scoped to individual audit sessions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, Any, Mapping

from flocks.session.session import Session
from flocks_code_security.paths import runtime_dir

if TYPE_CHECKING:
    from flocks_code_security.runtime import PluginRuntime


async def prepare_bash_workspace(
    runtime: PluginRuntime, scan: Mapping[str, Any], session_id: str,
) -> None:
    if not scan.get("bash_enabled"):
        return
    session = await Session.get_by_id(session_id)
    if session is None:
        raise RuntimeError("Audit session not found")
    destination = runtime_dir().resolve() / "shell" / scan["scan_id"] / session_id
    if Path(session.directory) == destination and destination.is_dir():
        return
    cancel = Event()
    copying = asyncio.create_task(asyncio.to_thread(runtime.source.copy_to, scan["snapshot_id"], destination, cancel))
    try:
        await asyncio.shield(copying)
    except asyncio.CancelledError:
        cancel.set()
        # A cancelled await does not stop its thread. Join before scan cleanup.
        while not copying.done():
            try:
                await asyncio.shield(copying)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not copying.cancelled():
            copying.exception()
        raise
    updated = await Session.update(session.project_id, session_id, directory=str(destination))
    if updated is None:
        raise RuntimeError("Cannot persist audit Bash working directory")
