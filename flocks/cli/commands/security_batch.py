"""Batch CLI adapter; scheduling and child execution live outside Typer."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from flocks.security.batch import batch_status, clean_batch, prepare_batch, read_json, run_batch

batch_app = typer.Typer(help="Run isolated Arvo audits concurrently and resume interrupted batches.")


def _run(root: Path, retry_failed: bool = False) -> None:
    typer.echo(f"Run directory: {root}")
    try:
        result = asyncio.run(run_batch(root, retry_failed=retry_failed, progress=typer.echo))
    except KeyboardInterrupt:
        typer.echo("Batch stopped. Use security batch resume to continue.")
        raise typer.Exit(130) from None
    except (OSError, ValueError, RuntimeError) as exc:
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
    dynamic: bool = typer.Option(
        False, "--dynamic", help="Enable local fuzz/GDB validation using each task's cybergym.json."
    ),
    dynamic_concurrency: int = typer.Option(
        2, min=1, max=128, help="Maximum active dynamic containers per batch; each uses 1 CPU and 1024 MiB."
    ),
    phase_timeouts: Optional[Path] = typer.Option(
        None, exists=True, dir_okay=False,
        help="JSON file of cumulative per-phase budgets in seconds. Uses built-in phase budgets when neither timeout option is given; no overall task timeout.",
    ),
    task_timeout: Optional[int] = typer.Option(None, min=1, help="Explicit legacy overall task timeout; incompatible with --phase-timeouts."),
    max_snapshot_bytes: int = typer.Option(4 * 1024**3, min=1),
    max_snapshot_files: int = typer.Option(50_000, min=1, help="Maximum files included in each audit snapshot."),
    exclude_cyclic_symlinks: bool = typer.Option(False, help="Exclude cyclic source symlinks and record coverage exclusions."),
    run_dir: Optional[Path] = typer.Option(None),
    auto_exclude_external_symlinks: bool = typer.Option(
        False, "--auto-exclude-external-symlinks",
        help="Omit absolute symlinks without reading targets; marks source coverage partial. Default: reject.",
    ),
    skip_external_symlink: Optional[list[str]] = typer.Option(
        None, "--skip-external-symlink",
        help="Skip only this exact archive symlink PATH=/absolute/target; repeatable. Never reads the target.",
    ),
) -> None:
    """Discover numeric task folders containing repo-vul.tar.gz and description.txt.

    Each audit exposes standard file, bash, grep, webfetch, websearch and todo
    tools at every stage by default; the model chooses when to use them.
    No tool opt-in flags are needed. PoC generation is always enabled.

    With --dynamic, each folder must also contain a trusted cybergym.json with
    a prebuilt vulnerable_runner image, target_binary, fuzzer_target,
    input_contract, gdb_supported=true, fuzzer_supported=true and limits.
    Images must already be available locally. Execution is local validation;
    official CyberGym grading is not dispatched. Static concurrency and
    --dynamic-concurrency (active containers) are independent limits.
    """
    try:
        root = prepare_batch(
            source,
            run_dir=run_dir,
            concurrency=concurrency,
            model=model,
            dynamic=dynamic,
            dynamic_concurrency=dynamic_concurrency,
            task_timeout=task_timeout,
            phase_timeouts=read_json(phase_timeouts) if phase_timeouts else None,
            max_snapshot_bytes=max_snapshot_bytes,
            max_snapshot_files=max_snapshot_files,
            exclude_cyclic_symlinks=exclude_cyclic_symlinks,
            skip_external_symlinks=skip_external_symlink,
            auto_exclude_external_symlinks=auto_exclude_external_symlinks,
        )
    except (OSError, ValueError, RuntimeError) as exc:
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
    """Print task status, live or termination runtime snapshots, and WebUI links."""
    try:
        typer.echo(json.dumps(batch_status(run_dir), ensure_ascii=False, indent=2))
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@batch_app.command("clean")
def clean(run_dir: Path = typer.Argument(..., exists=True, file_okay=False, resolve_path=True)) -> None:
    """Remove stopped tasks' intermediate data, retaining reports, PoCs and the task UI."""
    try:
        result = asyncio.run(clean_batch(run_dir))
    except (OSError, ValueError, RuntimeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    if any(
        item.get("cleanup_status") == "failed" or item.get("source_cleanup_status") == "failed"
        for item in result["tasks"]
    ):
        raise typer.Exit(1)
