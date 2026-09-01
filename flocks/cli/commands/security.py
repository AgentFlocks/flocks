"""One-command code-security audit and progress inspection."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import typer
from rich.console import Console
from rich.table import Table

from flocks.utils.langfuse import shutdown as shutdown_langfuse


security_app = typer.Typer(
    name="security",
    help="Run and inspect trusted code-security audits",
    no_args_is_help=True,
)
console = Console()

AuditRunner = Callable[..., Any]
StatusReader = Callable[[str], dict[str, Any]]
MAX_KNOWLEDGE_BASE_BYTES = 32 * 1024


def _read_knowledge_base(
    path: Path,
    *,
    audited_target: Path | None = None,
) -> dict[str, str]:
    """Capture one small UTF-8 guidance file without following a final symlink."""
    source = path.expanduser()
    initial = source.lstat()
    if stat.S_ISLNK(initial.st_mode):
        raise ValueError("knowledge base must not be a symbolic link")
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError("knowledge base must be a regular file")

    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("knowledge base must be a regular file")
        if (initial.st_dev, initial.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("knowledge base changed while it was opened")

        contents = bytearray()
        while len(contents) <= MAX_KNOWLEDGE_BASE_BYTES:
            chunk = os.read(descriptor, MAX_KNOWLEDGE_BASE_BYTES + 1 - len(contents))
            if not chunk:
                break
            contents.extend(chunk)
        after = os.fstat(descriptor)
        resolved_source = source.resolve(strict=True)
        resolved = resolved_source.stat()
    finally:
        os.close(descriptor)

    if len(contents) > MAX_KNOWLEDGE_BASE_BYTES:
        raise ValueError("knowledge base may contain at most 32 KiB")
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("knowledge base changed while it was read")
    if (resolved.st_dev, resolved.st_ino) != (before.st_dev, before.st_ino):
        raise ValueError("knowledge base path changed while it was read")
    if audited_target is not None and resolved_source.is_relative_to(audited_target.resolve()):
        raise ValueError("knowledge base must be outside the audited source directory")
    if b"\0" in contents:
        raise ValueError("knowledge base must be a UTF-8 text file")
    try:
        content = bytes(contents).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("knowledge base must be valid UTF-8 text") from exc
    if not content.strip():
        raise ValueError("knowledge base must not be empty")
    return {"display_name": source.name, "content": content}


def _read_cybergym_manifest(path: Path) -> dict[str, Any]:
    """Read one host-approved Level 1 manifest without treating it as executable input."""
    source = path.expanduser()
    if not source.is_file() or source.is_symlink():
        raise ValueError("CyberGym manifest must be a regular file")
    if source.stat().st_size > 64 * 1024:
        raise ValueError("CyberGym manifest may contain at most 64 KiB")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("CyberGym manifest must be a valid UTF-8 JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("CyberGym manifest must be a JSON object")
    return payload


def _load_plugin_cli() -> tuple[AuditRunner, StatusReader]:
    """Load the source plugin even when the audit target is the current directory."""
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    try:
        try:
            from flocks_code_security.entrypoint import register
        except ModuleNotFoundError:
            raise
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
        for worker in payload.get("workers", []):
            paths = ", ".join(worker.get("assigned_paths", [])) or "."
            candidate = worker.get("candidate_id")
            subject = f"  candidate={candidate}" if candidate else ""
            console.print(
                f"           [dim]worker={worker.get('work_unit_id')}  "
                f"role={worker.get('role')}  scope={paths}{subject}[/dim]"
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
    if event == "scan.adjudicated":
        action = payload.get("action")
        round_number = payload.get("adjudication_round")
        console.print(
            f"[{timestamp}] [bold cyan]Parent adjudication[/bold cyan]  "
            f"round={round_number}  action={action}"
        )
        if action == "targeted_rescan":
            paths = ", ".join((payload.get("rescan") or {}).get("paths", []))
            console.print(f"           [dim]scope={paths or 'unspecified'}[/dim]")
        return
    if event == "scan.finalized":
        console.print(
            f"[{timestamp}] [bold green]Audit complete[/bold green]  "
            f"findings={payload.get('finding_count', 0)}  "
            f"report={payload.get('report_path')}"
        )
        for finding in payload.get("finding_summaries", []):
            locations = finding.get("locations", [])
            location = locations[0] if locations else {}
            path = location.get("path", "unknown")
            line = location.get("startLine", "?")
            console.print(
                f"           [{finding.get('severity', 'unknown')}] "
                f"{finding.get('title', 'Untitled finding')}  {path}:{line}"
            )
        return
    if event == "scan.coverage_blocked":
        console.print(
            f"[{timestamp}] [bold red]Coverage blocked[/bold red]  "
            f"scan_id={payload.get('scan_id')}  "
            f"failure_code={payload.get('failure_code', 'coverage_blocked')}"
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
        f"[bold]Threat model:[/bold] {status.get('threat_model_status')}  "
        f"[bold]Dynamic:[/bold] {'enabled' if status.get('dynamic_enabled') else 'disabled'}"
    )
    adjudication = status.get("adjudication")
    if adjudication:
        console.print(
            f"[bold]Parent adjudication:[/bold] "
            f"round={adjudication.get('adjudication_round')}  "
            f"action={adjudication.get('action')}"
        )
    if status.get("integrity_status") == "invalid":
        console.print("[bold red]Integrity: invalid — do not use this audit result[/bold red]")
        for error in status.get("integrity_errors", []):
            console.print(f"[red]- {error}[/red]")
    console.print(
        f"[bold]Progress:[/bold] candidates={counts.get('candidates', 0)}, "
        f"verified={counts.get('verifications', 0)}, "
        f"pending={counts.get('unverified_candidates', 0)}, "
        f"dynamic_terminal={counts.get('terminal_dynamic_runs', 0)}, "
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
    dynamic: bool = typer.Option(
        False,
        "--dynamic",
        help="Execute validated probes in a network-isolated local Docker runtime",
    ),
    copy_source: bool = typer.Option(
        True,
        "--copy/--no-copy",
        help="Copy source into a read-only snapshot (use --no-copy to audit the source directory directly)",
    ),
    coverage_policy: str = typer.Option(
        "evidence_backed_partial",
        "--coverage-policy",
        help="Coverage policy: evidence_backed_partial or exhaustive",
    ),
    verification_votes: int = typer.Option(
        1,
        "--verification-votes",
        min=1,
        max=5,
        help="Independent verifier votes required per candidate",
    ),
    knowledge_base: Optional[Path] = typer.Option(
        None,
        "--knowledge-base",
        help="Optional untrusted UTF-8 vulnerability target specification",
    ),
    cybergym_manifest: Optional[Path] = typer.Option(
        None,
        "--cybergym-manifest",
        help="Trusted Level 1 JSON execution manifest; enables cybergym_level1 mode",
    ),
) -> None:
    """Run the host-orchestrated audit with parent-Agent adjudication."""
    try:
        run_standard_audit, _scan_status = _load_plugin_cli()
        progress = _json_line if json_output else _progress_line
        audit_kwargs = {"model": model, "progress": progress}
        if not copy_source:
            audit_kwargs["copy_source"] = False
        if dynamic:
            audit_kwargs["dynamic_enabled"] = True
        if coverage_policy not in {"evidence_backed_partial", "exhaustive"}:
            raise ValueError("Unsupported coverage policy")
        if coverage_policy != "evidence_backed_partial":
            audit_kwargs["coverage_policy"] = coverage_policy
        if verification_votes != 1:
            audit_kwargs["verification_votes"] = verification_votes
        if knowledge_base is not None:
            audit_kwargs["knowledge_base"] = _read_knowledge_base(
                knowledge_base,
                audited_target=target,
            )
        if cybergym_manifest is not None:
            audit_kwargs["scan_mode"] = "cybergym_level1"
            audit_kwargs["cybergym_manifest"] = _read_cybergym_manifest(cybergym_manifest)
        result = asyncio.run(run_standard_audit(target, **audit_kwargs))
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
    finally:
        shutdown_langfuse()

    if json_output:
        _json_line("scan.result", result)
    if result.get("status") == "failed":
        if not json_output and result.get("failure_code") != "coverage_blocked":
            console.print(
                f"[red]Audit failed:[/red] "
                f"{result.get('failure_code', 'unknown failure')}"
            )
        raise typer.Exit(1)


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
