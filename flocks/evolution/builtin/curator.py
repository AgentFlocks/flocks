"""
Built-in L4 Curator - background skill maintenance.

Two passes:

  v1 (this file): pure-function active → stale → archived sweep based on
      tracker telemetry + the author manifest. No LLM calls. Triggered
      by the existing ``command:new`` hook (mirrors session_memory.py)
      with strict throttling so it fires at most once every
      ``min_idle_hours``.

  v2 (planned, see L4-curator-llm todo): LLM-driven review that uses
      ``BackgroundManager.launch()`` to spawn a hidden top-level session
      for the curator subagent.

State persistence
-----------------
``CuratorState`` lives at ``~/.flocks/data/evolution/curator/state.json``.
Reports are written under ``~/.flocks/data/evolution/curator/{stamp}/``
once v2 lands; v1 is bookkeeping-only and does not write per-run dirs.

Safety
------
The curator only touches skill names that appear in
``AuthorManifest.names()`` so hub-installed and bundled skills are
never modified or archived. Pinned rows (`UsageRow.pinned == True`) are
also exempt from any state transition.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from flocks.evolution.engine import EvolutionEngine
from flocks.evolution.manifest import AuthorManifest
from flocks.evolution.strategies import Curator
from flocks.evolution.types import (
    CurationReport,
    CuratorContext,
    CuratorState,
    TransitionCounts,
    UsageState,
)
from flocks.utils.log import Log

log = Log.create(service="evolution.curator")


def _curator_dir() -> Path:
    raw = os.getenv("FLOCKS_ROOT")
    base = Path(raw) if raw else (Path.home() / ".flocks")
    return base / "data" / "evolution" / "curator"


def _state_path() -> Path:
    return _curator_dir() / "state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


class BuiltinIdleCurator(Curator):
    """Default curator: pure-function lifecycle transitions + idle throttling."""

    name = "builtin"
    is_noop = False

    def __init__(
        self,
        min_idle_hours: float = 24.0,
        stale_after_days: float = 14.0,
        archive_after_days: float = 60.0,
    ) -> None:
        """
        Args:
            min_idle_hours:    Minimum gap between curator runs.
            stale_after_days:  Active rows untouched for this many days
                               are flipped to ``stale``.
            archive_after_days: Stale rows untouched for this many days
                                are flipped to ``archived``. Counted from
                                ``last_used_at`` (not the stale flip
                                time) so skills get a single timeline.
        """
        self.min_idle_hours = float(min_idle_hours)
        self.stale_after_days = float(stale_after_days)
        self.archive_after_days = float(archive_after_days)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Persistent scheduler state
    # ------------------------------------------------------------------

    def load_state(self) -> CuratorState:
        path = _state_path()
        if not path.exists():
            return CuratorState()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return CuratorState(**data)
        except (OSError, ValueError) as exc:
            log.warn("curator.state_read_failed", {"error": str(exc)})
            return CuratorState()

    def save_state(self, state: CuratorState) -> None:
        path = _state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(path.parent),
            prefix=f".{path.name}.tmp.",
            suffix="",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state.model_dump(), fh, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ------------------------------------------------------------------
    # Throttle
    # ------------------------------------------------------------------

    def should_run(self, ctx: CuratorContext) -> bool:
        """Return True only when the configured idle window has elapsed.

        Always-on safety net: ``paused=True`` in state.json blocks every
        run; users can flip it via ``flocks evolution status --pause``
        (see CLI todo).
        """
        state = self.load_state()
        if state.paused:
            return False
        last = _parse_iso(state.last_run_at)
        if last is None:
            return True
        elapsed_h = (_now() - last).total_seconds() / 3600.0
        return elapsed_h >= self.min_idle_hours

    # ------------------------------------------------------------------
    # Pure-function transitions (the heart of v1)
    # ------------------------------------------------------------------

    def apply_automatic_transitions(self) -> TransitionCounts:
        """Walk the tracker report and apply active → stale → archived.

        Idempotent: re-running with the same telemetry produces the same
        counts. Safe to call from CLI ``flocks evolution status
        --apply-transitions`` when the user wants to force a sweep
        without waiting for the throttle window.
        """
        engine = EvolutionEngine.get()
        tracker = engine.tracker
        if tracker.is_noop:
            return TransitionCounts()

        manifest_names = set(AuthorManifest.get().names())
        counts = TransitionCounts()
        now = _now()

        for row in tracker.report():
            counts.checked += 1

            # Only operate on agent-created skills (manifest gate).
            if row.name not in manifest_names:
                continue
            if row.pinned:
                continue

            last_used = _parse_iso(row.last_used_at) or _parse_iso(row.created_at)
            idle_days = (now - last_used).total_seconds() / 86400.0 if last_used else float("inf")

            current = row.state if isinstance(row.state, UsageState) else UsageState(row.state)

            target: Optional[UsageState] = None
            if current == UsageState.ACTIVE:
                if idle_days >= self.archive_after_days:
                    target = UsageState.ARCHIVED
                elif idle_days >= self.stale_after_days:
                    target = UsageState.STALE
            elif current == UsageState.STALE:
                if idle_days >= self.archive_after_days:
                    target = UsageState.ARCHIVED
                elif idle_days < self.stale_after_days:
                    # Bumped recently enough to graduate back. Tracker.bump_use
                    # already does this via inline reactivation, but keep
                    # the curator path symmetric so a stale row left after
                    # a hand-edit also recovers.
                    target = UsageState.ACTIVE
                    counts.reactivated += 1
            # Archived rows are terminal — only manual unarchive flips them back.

            if target is not None and target != current:
                tracker.set_state(row.name, target, scope=row.scope)
                if target == UsageState.STALE:
                    counts.marked_stale += 1
                elif target == UsageState.ARCHIVED:
                    counts.archived += 1

        log.info(
            "curator.transitions_applied",
            {
                "checked": counts.checked,
                "marked_stale": counts.marked_stale,
                "archived": counts.archived,
                "reactivated": counts.reactivated,
            },
        )
        return counts

    # ------------------------------------------------------------------
    # Top-level run() — throttle + transitions + state save
    # ------------------------------------------------------------------

    async def run(self, ctx: CuratorContext) -> CurationReport:
        """One curator pass. Honours throttling unless triggered_by=='cli'.

        v1 is fully synchronous (pure-function transitions only) but is
        declared async so v2's LLM path is a drop-in upgrade.
        """
        report = CurationReport(started_at=_now().isoformat())
        force = ctx.triggered_by == "cli"

        if not force and not self.should_run(ctx):
            report.finished_at = _now().isoformat()
            report.llm_summary = "skipped: throttled by min_idle_hours"
            log.debug("curator.skipped", {"trigger": ctx.triggered_by})
            return report

        with self._lock:
            started = _now()
            counts = self.apply_automatic_transitions()
            elapsed = (_now() - started).total_seconds()

            report.auto_transitions = counts
            report.finished_at = _now().isoformat()
            report.duration_seconds = elapsed
            report.llm_summary = (
                f"transitions checked={counts.checked} stale={counts.marked_stale} "
                f"archived={counts.archived} reactivated={counts.reactivated}"
            )

            state = self.load_state()
            state.last_run_at = _now().isoformat()
            state.last_run_duration_seconds = elapsed
            state.last_run_summary = report.llm_summary
            state.run_count = (state.run_count or 0) + 1
            self.save_state(state)

        log.info(
            "curator.run_complete",
            {"trigger": ctx.triggered_by, "summary": report.llm_summary},
        )
        return report

    # ------------------------------------------------------------------
    # v2: LLM-driven review via BackgroundManager
    # ------------------------------------------------------------------

    async def run_llm_review(self, ctx: CuratorContext) -> CurationReport:
        """Launch the curator subagent in a hidden top-level session.

        Requires the Flocks server runtime (BackgroundManager + Session
        store). The CLI surfaces this via ``flocks evolution curate --llm``;
        the auto-trigger hook (command:new) does NOT call this in v1
        because launching a hidden session on every new conversation is
        too noisy. Users opt in explicitly.

        Writes the launching prompt + report directory metadata to
        ``~/.flocks/data/evolution/curator/{stamp}/run.json``. The
        subagent itself writes ``REPORT.md`` to the same directory.
        """
        from flocks.task.background import LaunchInput, get_background_manager

        report = CurationReport(started_at=_now().isoformat())

        # Quick gate: nothing to curate ⇒ skip
        manifest_names = list(AuthorManifest.get().names())
        if not manifest_names:
            report.finished_at = _now().isoformat()
            report.llm_summary = "skipped: no agent-authored skills"
            return report

        # Per-run report dir
        stamp = _now().strftime("%Y%m%dT%H%M%S")
        run_dir = _curator_dir() / stamp
        run_dir.mkdir(parents=True, exist_ok=True)
        report_path = run_dir / "REPORT.md"

        prompt = self._build_llm_prompt(manifest_names, report_path)
        run_meta = {
            "stamp": stamp,
            "trigger": ctx.triggered_by,
            "session_id": ctx.session_id,
            "report_path": str(report_path),
            "manifest_count": len(manifest_names),
        }
        try:
            (run_dir / "run.json").write_text(
                json.dumps(run_meta, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warn("curator.llm.run_meta_write_failed", {"error": str(exc)})

        manager = get_background_manager()
        task = await manager.launch(LaunchInput(
            description=f"evolution curator pass ({len(manifest_names)} skills)",
            prompt=prompt,
            agent="evolution-curator",
            parent_session_id=None,  # hidden top-level session
            parent_message_id=None,
            parent_agent=None,
        ))

        report.finished_at = _now().isoformat()
        report.report_dir = str(run_dir)
        report.llm_summary = (
            f"launched curator subagent (task_id={task.id}, session_id={task.session_id});"
            f" report → {report_path}"
        )

        # Also bookkeep last_run_at so the v1 throttle treats this as a real run.
        state = self.load_state()
        state.last_run_at = _now().isoformat()
        state.last_run_summary = report.llm_summary
        state.run_count = (state.run_count or 0) + 1
        self.save_state(state)

        log.info(
            "curator.llm_launched",
            {"trigger": ctx.triggered_by, "task_id": task.id, "report_dir": str(run_dir)},
        )
        return report

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_llm_prompt(self, manifest_names: list, report_path: Path) -> str:
        """Render the launching prompt for the curator subagent."""
        lines = [
            "You are running as the L4 Evolution Curator background pass.",
            "",
            "Eligible skills (author manifest):",
            "",
        ]
        for n in manifest_names:
            lines.append(f"  - {n}")
        lines.extend([
            "",
            "Per-skill telemetry (from L3 tracker):",
            "",
        ])
        try:
            for row in EvolutionEngine.get().tracker.report():
                if row.name in manifest_names:
                    lines.append(
                        f"  - {row.name} ({row.scope}): use={row.use_count} "
                        f"view={row.view_count} patch={row.patch_count} "
                        f"state={row.state.value if hasattr(row.state, 'value') else row.state} "
                        f"pinned={row.pinned} last_used={row.last_used_at or '-'}"
                    )
        except Exception as exc:  # pragma: no cover
            lines.append(f"  (tracker report unavailable: {exc})")
        lines.extend([
            "",
            f"Write your report to: {report_path}",
            "",
            "Follow the workflow in your prompt. Do NOT touch any skill not listed above.",
        ])
        return "\n".join(lines)


__all__ = ["BuiltinIdleCurator"]
