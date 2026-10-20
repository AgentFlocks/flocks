"""
Shared-storage maintenance: files kept in the database as well as on disk.
"""

from __future__ import annotations

import asyncio
import json
from typing import List, Optional

import typer
from rich.console import Console
from rich.table import Table

storage_app = typer.Typer(help="Files kept in the database as well as on this instance's disk")
console = Console()

_AREA_HELP = "uploads, channel_media or memory (default: all)"


def _areas(area: Optional[str]) -> List[str]:
    from flocks.storage.file_backfill import AREAS

    if area is None:
        return list(AREAS)
    if area not in AREAS:
        console.print(f"[red]Unknown area {area!r}; one of {', '.join(AREAS)}[/red]")
        raise typer.Exit(2)
    return [area]


async def _init() -> None:
    from flocks.storage.storage import Storage

    await Storage.init()


def _print(states, as_json: bool, verbose: bool) -> None:
    from flocks.storage.file_backfill import SHARED, summarize

    if as_json:
        console.print_json(json.dumps({"files": [s.to_dict() for s in states], "summary": summarize(states)}))
        return
    shown = [s for s in states if verbose or s.state != SHARED]
    if shown:
        table = Table("area", "path", "state", "size", "sha256")
        for s in shown:
            table.add_row(s.area, s.path, s.state, "" if s.size is None else str(s.size), (s.sha256 or "")[:16])
        console.print(table)
    console.print(f"summary: {summarize(states)}")


@storage_app.command("files-check")
def files_check(
    area: Optional[str] = typer.Option(None, "--area", help=_AREA_HELP),
    as_json: bool = typer.Option(False, "--json", help="Print every file and its state as JSON (the mapping)"),
    verbose: bool = typer.Option(False, "--all", help="List files that are already shared too"),
):
    """Compare this disk with the database, file by file. Changes nothing."""
    from flocks.storage.file_backfill import check_area

    async def _run():
        await _init()
        states = []
        for name in _areas(area):
            states.extend(await check_area(name))
        return states

    _print(asyncio.run(_run()), as_json, verbose)


@storage_app.command("files-backfill")
def files_backfill(
    area: Optional[str] = typer.Option(None, "--area", help="uploads or channel_media (default: both)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would be stored, store nothing"),
    as_json: bool = typer.Option(False, "--json"),
):
    """Store files only this disk has, so every instance can read them.

    Never overwrites a stored copy and never brings back a file removed
    elsewhere; those are reported. Memory notes are stored by the memory sync.
    """
    from flocks.storage.file_backfill import WRITE_ONCE_AREAS, backfill_area

    names = [name for name in _areas(area) if name in WRITE_ONCE_AREAS]

    async def _run():
        await _init()
        states = []
        for name in names:
            states.extend(await backfill_area(name, dry_run=dry_run))
        return states

    _print(asyncio.run(_run()), as_json, False)


@storage_app.command("files-restore")
def files_restore(area: Optional[str] = typer.Option(None, "--area", help=_AREA_HELP)):
    """Write the stored files this disk lacks to it (a new or rebuilt instance)."""
    from flocks.storage.file_backfill import restore_area

    async def _run():
        await _init()
        return {name: await restore_area(name) for name in _areas(area)}

    for name, written in asyncio.run(_run()).items():
        console.print(f"{name}: {written} file(s) written")
