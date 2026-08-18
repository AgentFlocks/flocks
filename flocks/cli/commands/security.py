"""One-command code-security audit and progress inspection."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.table import Table


security_app = typer.Typer(
    name="security",
    help="Run and inspect trusted static code-security audits",
    no_args_is_help=True,
)
console = Console()

AuditRunner = Callable[..., Any]
StatusReader = Callable[[str], dict[str, Any]]


def _load_plugin_cli() -> tuple[AuditRunner, StatusReader]:
    """Load the source plugin even when the audit target is the current directory."""
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    try:
        try:
            from flocks_code_security.entrypoint import register
        except Exception as load_error:
            raise RuntimeError(
                f"Failed to load the flocks-code-security plugin: {load_error}"
            ) from load_error
    except ModuleNotFoundError as first_error:
        source_root = (
            Path(__file__).resolve().parents[3]
            / ".flocks"
            / "plugins"
            / "flocks-code-security"
            / "src"
        )
        if not source_root.is_dir():
            raise RuntimeError(
                "The flocks-code-security source plugin is not available"
            ) from first_error
        source = str(source_root)
        if source not in sys.path:
            sys.path.insert(0, source)
        from flocks_code_security.entrypoint import register

    register()
    from flocks_code_security.cli import run_standard_audit, scan_status

    return run_standard_audit, scan_status


def _json_line(event: str, payload: dict[str, Any]) -> None:
    typer.echo(json.dumps({**payload, "event": event}, ensure_ascii=False, default=str))


def _progress_line(event: str, payload: dict[str, Any]) -> None:
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    if event == "scan.prepared":
        snapshot = payload.get("snapshot", {})
        console.print(
            f"[{timestamp}] [green]Scan prepared[/green]  "
            f"scan_id={payload.get('scan_id')}  "
            f"files={snapshot.get('file_count', 0)}  "
            f"omitted={snapshot.get('omitted_count', 0)}"
        )
        return
    if event == "batch.started":
        console.print(
            f"[{timestamp}] [cyan]Batch started[/cyan]  "
            f"phase={payload.get('phase')}  "
            f"batch_id={payload.get('batch_id')}  "
            f"workers={payload.get('launched_workers', 0)}"
        )
        return
    if event == "batch.status":
        counts = payload.get("status_counts", {})
        count_text = ", ".join(
            f"{name}={count}" for name, count in sorted(counts.items()) if count
        )
        console.print(
            f"[{timestamp}] [blue]Batch status[/blue]  "
            f"phase={payload.get('phase')}  status={payload.get('status')}  "
            f"{count_text or 'no work-unit updates'}"
        )
        return
    if event == "scan.status":
        counts = payload.get("counts", {})
        console.print(
            f"[{timestamp}] [magenta]Scan status[/magenta]  "
            f"candidates={counts.get('candidates', 0)}  "
            f"verified={counts.get('verifications', 0)}  "
            f"pending={counts.get('unverified_candidates', 0)}"
        )
        return
    if event == "scan.finalized":
        console.print(
            f"[{timestamp}] [bold green]Audit complete[/bold green]  "
            f"findings={payload.get('finding_count', 0)}  "
            f"report={payload.get('report_path')}"
        )
        return
    if event == "scan.cancelled":
        console.print(
            f"[{timestamp}] [yellow]Audit cancelled[/yellow]  "
            f"scan_id={payload.get('scan_id')}"
        )


def _render_status(status: dict[str, Any]) -> None:
    counts = status.get("counts", {})
    console.print(f"[bold]Scan:[/bold] {status.get('scan_id')}")
    console.print(
        f"[bold]Status:[/bold] {status.get('status')}  "
        f"[bold]Threat model:[/bold] {status.get('threat_model_status')}"
    )
    console.print(
        f"[bold]Progress:[/bold] candidates={counts.get('candidates', 0)}, "
        f"verified={counts.get('verifications', 0)}, "
        f"pending={counts.get('unverified_candidates', 0)}, "
        f"active_work_units={counts.get('active_work_units', 0)}"
    )

    batches = status.get("worker_batches", [])
    if batches:
        table = Table(title="Worker batches")
        table.add_column("Phase")
        table.add_column("Status")
        table.add_column("Batch ID")
        for batch in batches:
            table.add_row(
                str(batch.get("phase", "")),
                str(batch.get("status", "")),
                str(batch.get("batch_id", "")),
            )
        console.print(table)
    if status.get("report_path"):
        console.print(f"[bold]Report:[/bold] {status['report_path']}")


@security_app.command("audit")
def security_audit(
    target: Path = typer.Argument(
        ...,
        help="Source directory to audit",
        file_okay=False,
        dir_okay=True,
        resolve_path=True,
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Pinned LLM in provider/model form (defaults to configured LLM)",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit newline-delimited JSON progress events",
    ),
) -> None:
    """Run threat modeling, baseline scanning, verification, and reporting."""
    try:
        run_standard_audit, _scan_status = _load_plugin_cli()
        progress = _json_line if json_output else _progress_line
        result = asyncio.run(
            run_standard_audit(target, model=model, progress=progress)
        )
    except KeyboardInterrupt:
        if not json_output:
            console.print("[yellow]Audit interrupted; cancellation was requested.[/yellow]")
        raise typer.Exit(130) from None
    except (OSError, RuntimeError, ValueError) as exc:
        if json_output:
            _json_line("scan.error", {"error": str(exc)})
        else:
            console.print(f"[red]Audit failed:[/red] {exc}")
        raise typer.Exit(1) from None

    if json_output:
        _json_line("scan.result", result)


@security_app.command("status")
def security_status(
    scan_id: str = typer.Argument(..., help="Scan ID returned by security audit"),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON",
    ),
) -> None:
    """Read persisted audit progress without changing the running scan."""
    try:
        _run_standard_audit, read_status = _load_plugin_cli()
        status = read_status(scan_id)
    except (OSError, RuntimeError, ValueError) as exc:
        if json_output:
            _json_line("scan.error", {"scan_id": scan_id, "error": str(exc)})
        else:
            console.print(f"[red]Unable to read scan:[/red] {exc}")
        raise typer.Exit(1) from None

    if json_output:
        typer.echo(json.dumps(status, ensure_ascii=False, indent=2, default=str))
    else:
        _render_status(status)
