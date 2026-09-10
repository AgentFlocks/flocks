"""Batch CLI adapter; scheduling and child execution live outside Typer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from flocks.security.batch import batch_status, clean_batch, prepare_batch, run_batch

batch_app = typer.Typer(help="Run isolated Arvo audits concurrently and resume interrupted batches.")


def _run(root: Path, retry_failed: bool = False) -> None:
    typer.echo(f"Run directory: {root}")
    try:
        result = asyncio.run(run_batch(root, retry_failed=retry_failed, progress=typer.echo))
    except KeyboardInterrupt:
        typer.echo("Batch stopped. Use security batch resume to continue.")
        raise typer.Exit(130) from None
    except (OSError, ValueError) as exc:
        typer.echo(f"Batch error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if any(
        item["status"] != "completed"
        or item.get("cleanup_status") != "completed"
        or item.get("source_cleanup_status") == "failed"
        for item in result["tasks"]
    ):
        raise typer.Exit(1)


@batch_app.command("run")
def start(
    source: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    concurrency: int = typer.Option(30, min=1, max=128),
    model: Optional[str] = typer.Option(None),
    poc: bool = typer.Option(False, "--poc"),
    task_timeout: int = typer.Option(7200, min=1, help="Total seconds allowed per task, including preparation."),
    max_snapshot_bytes: int = typer.Option(4 * 1024**3, min=1),
    run_dir: Optional[Path] = typer.Option(None),
) -> None:
    """Discover numeric task folders containing repo-vul.tar.gz and description.txt."""
    try:
        root = prepare_batch(
            source,
            run_dir=run_dir,
            concurrency=concurrency,
            model=model,
            poc=poc,
            task_timeout=task_timeout,
            max_snapshot_bytes=max_snapshot_bytes,
        )
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    _run(root)


@batch_app.command("resume")
def resume(
    run_dir: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True),
    retry_failed: bool = typer.Option(False, "--retry-failed"),
) -> None:
    """Adopt live children and continue interrupted tasks using the saved configuration."""
    _run(run_dir, retry_failed)


@batch_app.command("status")
def status(run_dir: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True)) -> None:
    """Print durable task status and task-specific WebUI links."""
    try:
        typer.echo(json.dumps(batch_status(run_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@batch_app.command("clean")
def clean(run_dir: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True)) -> None:
    """Remove stopped tasks' intermediate data, retaining reports, PoCs and the task UI."""
    try:
        result = asyncio.run(clean_batch(run_dir))
    except (OSError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if any(
        item.get("cleanup_status") == "failed" or item.get("source_cleanup_status") == "failed"
        for item in result["tasks"]
    ):
        raise typer.Exit(1)
