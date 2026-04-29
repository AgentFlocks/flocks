"""
Evolution CLI commands

  flocks evolution status            – show active strategies + curator state
  flocks evolution list-strategies   – list registered factories per layer
  flocks evolution report            – dump tracker rows + manifest entries
  flocks evolution curate            – force-run the L4 curator (bypass throttle)
  flocks evolution pause / resume    – flip CuratorState.paused

Read-only by default; the only mutating subcommands are ``curate`` and
``pause``/``resume``. Designed to work even when the evolution module is
disabled in config — bootstrap is invoked manually so users can see what
is registered without starting the server.
"""

from __future__ import annotations

import asyncio
import json
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from flocks.evolution import EvolutionEngine
from flocks.evolution.manifest import AuthorManifest
from flocks.evolution.types import CuratorContext

evolution_app = typer.Typer(
    name="evolution",
    help="Inspect and control the pluggable evolution module",
    no_args_is_help=True,
)

console = Console()


# ---------------------------------------------------------------------------
# Internal: bootstrap on demand
# ---------------------------------------------------------------------------


async def _bootstrap_from_config() -> EvolutionEngine:
    from flocks.config import Config
    cfg = await Config.get()
    evo_cfg = getattr(cfg, "evolution", None)
    evo_dict = (
        evo_cfg.model_dump(exclude_none=False)
        if evo_cfg is not None and hasattr(evo_cfg, "model_dump")
        else {}
    )
    engine = EvolutionEngine.get()
    engine.bootstrap(evo_dict)
    return engine


def _run(coro):
    """asyncio.run wrapper that's friendly to typer."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@evolution_app.command("status")
def status_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
    apply_transitions: bool = typer.Option(
        False,
        "--apply-transitions",
        help="Force-run the curator's pure-function transitions before printing.",
    ),
):
    """Show active strategies, curator state, and recent counts."""
    engine = _run(_bootstrap_from_config())

    if apply_transitions and not engine.curator.is_noop:
        counts = engine.curator.apply_automatic_transitions()
        console.print(
            f"[cyan]applied transitions:[/cyan] checked={counts.checked} "
            f"stale+={counts.marked_stale} archived+={counts.archived} "
            f"reactivated+={counts.reactivated}"
        )

    state = engine.curator.load_state() if not engine.curator.is_noop else None
    payload = {
        "config": engine.config,
        "active": engine.status()["active"],
        "noop": engine.status()["noop"],
        "manifest": {
            "path": str(AuthorManifest.get().path()),
            "names": AuthorManifest.get().names(),
        },
        "curator_state": state.model_dump() if state else None,
    }

    if json_out:
        console.print_json(data=payload)
        return

    table = Table(title="Evolution status", show_header=True)
    table.add_column("Layer", style="bold cyan")
    table.add_column("Active", style="green")
    table.add_column("NoOp?", style="yellow")
    for layer, active in payload["active"].items():
        table.add_row(layer, str(active), "yes" if payload["noop"][layer] else "no")
    console.print(table)

    console.print(
        Panel(
            (
                f"[bold]Author manifest[/bold]\n"
                f"path: {payload['manifest']['path']}\n"
                f"agent-created skills: {len(payload['manifest']['names'])}\n"
                + ("\n".join(f"  - {n}" for n in payload['manifest']['names']) or "  (none)")
            ),
            title="L2 manifest",
        )
    )

    if state is not None:
        console.print(
            Panel(
                json.dumps(state.model_dump(), indent=2, ensure_ascii=False),
                title="L4 curator state",
            )
        )


# ---------------------------------------------------------------------------
# list-strategies
# ---------------------------------------------------------------------------


@evolution_app.command("list-strategies")
def list_strategies_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
):
    """List all strategy factories registered with EvolutionEngine."""
    _run(_bootstrap_from_config())
    data = EvolutionEngine.list_strategies()
    if json_out:
        console.print_json(data=data)
        return

    table = Table(title="Registered evolution strategies", show_header=True)
    table.add_column("Layer", style="bold cyan")
    table.add_column("Factories", style="green")
    for layer, info in data.items():
        registered = info.get("registered") or []
        table.add_row(layer, ", ".join(registered) if registered else "—")
    console.print(table)


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@evolution_app.command("report")
def report_cmd(
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table"),
):
    """Dump the L3 tracker report alongside manifest authorship gating."""
    engine = _run(_bootstrap_from_config())

    rows = engine.tracker.report() if not engine.tracker.is_noop else []
    manifest_names = set(AuthorManifest.get().names())

    if json_out:
        console.print_json(data={
            "rows": [r.model_dump() for r in rows],
            "manifest": sorted(manifest_names),
        })
        return

    table = Table(title="Skill usage report", show_header=True)
    table.add_column("Skill", style="bold cyan")
    table.add_column("Scope")
    table.add_column("Use")
    table.add_column("View")
    table.add_column("Patch")
    table.add_column("State", style="magenta")
    table.add_column("Pinned")
    table.add_column("Authored")
    table.add_column("Last used")

    for row in sorted(rows, key=lambda r: (r.scope, r.name)):
        table.add_row(
            row.name,
            row.scope,
            str(row.use_count),
            str(row.view_count),
            str(row.patch_count),
            row.state.value if hasattr(row.state, "value") else str(row.state),
            "yes" if row.pinned else "",
            "agent" if row.name in manifest_names else "external",
            row.last_used_at or "",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# curate (force-run)
# ---------------------------------------------------------------------------


@evolution_app.command("curate")
def curate_cmd(
    bypass_throttle: bool = typer.Option(
        True,
        "--throttled/--bypass-throttle",
        help="Honour curator min_idle_hours (default: bypass via cli trigger)",
    ),
    llm: bool = typer.Option(
        False,
        "--llm",
        help=(
            "Launch the LLM-driven curator subagent (v2). Requires the Flocks "
            "server runtime; produces a written report under "
            "~/.flocks/data/evolution/curator/{stamp}/REPORT.md."
        ),
    ),
):
    """Force the L4 curator to do a pass (pure transitions in v1, LLM review with --llm)."""
    engine = _run(_bootstrap_from_config())
    if engine.curator.is_noop:
        console.print("[yellow]curator is NoOp; enable evolution.curator.use=builtin[/yellow]")
        raise typer.Exit(0)

    trigger = "command:new" if not bypass_throttle else "cli"
    if llm:
        if not hasattr(engine.curator, "run_llm_review"):
            console.print(
                "[red]Active curator does not implement run_llm_review(); "
                "only builtin supports --llm.[/red]"
            )
            raise typer.Exit(2)
        report = _run(engine.curator.run_llm_review(CuratorContext(triggered_by=trigger)))
    else:
        report = _run(engine.curator.run(CuratorContext(triggered_by=trigger)))
    console.print(
        Panel(
            json.dumps(report.model_dump(), indent=2, ensure_ascii=False, default=str),
            title=f"Curator report (trigger={trigger}, llm={llm})",
        )
    )


# ---------------------------------------------------------------------------
# pause / resume
# ---------------------------------------------------------------------------


def _set_paused(value: bool) -> None:
    engine = _run(_bootstrap_from_config())
    if engine.curator.is_noop:
        console.print("[yellow]curator is NoOp; pause/resume has no effect[/yellow]")
        return
    state = engine.curator.load_state()
    state.paused = value
    engine.curator.save_state(state)
    console.print(f"curator paused={value}")


@evolution_app.command("pause")
def pause_cmd():
    """Stop the curator from running on command:new (state.paused=True)."""
    _set_paused(True)


@evolution_app.command("resume")
def resume_cmd():
    """Re-enable curator runs (state.paused=False)."""
    _set_paused(False)


__all__ = ["evolution_app"]
