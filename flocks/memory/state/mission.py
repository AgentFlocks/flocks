"""Read shared Mission State maintained with ordinary filesystem tools."""

from __future__ import annotations

import re
from pathlib import Path


MISSION_CONTEXT_LIMIT = 16_000
SHARED_CONTEXT_LIMIT = 8_000
ARTIFACT_INDEX_LIMIT = 4_000

MISSION_STATE_GUIDANCE = """### Mission State usage

Mission State is durable shared working state, not a transcript.

- `mission.md` is owned by the main Agent. Keep the goal, scope, task statuses,
  attention items, and current state concise and current. Update it after
  planning changes or after incorporating delegated work.
- `progress.md` is an append-oriented work log. After a meaningful attempt,
  append the task, Agent, action, outcome, and links to related findings or
  artifacts. Record failures as well as successes.
- `findings.md` contains useful discoveries and their evidence. Record what was
  observed, why it matters, the affected scope, remaining uncertainty, and
  evidence or artifact references. Correct stale findings explicitly instead
  of silently erasing history.
- `artifacts/` contains large or durable outputs. Keep raw logs, responses,
  reports, screenshots, and scripts there; add a short entry to
  `artifacts/INDEX.md` with the path, summary, source, and related task or
  finding.

Before changing the plan, delegating work, or claiming the Goal is complete,
the main Agent must use filesystem tools to re-read `mission.md` and the
relevant recent sections of `progress.md` and `findings.md`. After delegated
work returns, reconcile useful results and task status into `mission.md`.

Before editing a shared file, read its latest contents. Prefer precise edits or
append-only entries, never overwrite another Agent's work, and link to artifacts
instead of copying large outputs into State files.
""".strip()

SUBAGENT_STATE_GUIDANCE = """### Shared Mission State

Your assigned task is described above.

Shared State directory: `{state_dir}`

You must not read or edit `mission.md`. You may read `progress.md` and
`findings.md` when they help with your local task.

Before finishing:

- append your work, outcome, and Agent identity to `progress.md`;
- update `findings.md` when you made a useful discovery, including evidence;
- store large or durable outputs under `artifacts/` and update
  `artifacts/INDEX.md`;
- include a short Completion Report in your final response with the outcome
  and the State files or artifacts you changed.

Read a shared file immediately before editing it. Prefer precise edits or
append-only entries and do not overwrite another Agent's work.
""".strip()


def mission_dir(workspace_dir: str | Path, session_id: str) -> Path:
    """Return the deterministic State directory for a Session."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", session_id):
        raise ValueError(f"Invalid session id for Mission State: {session_id!r}")
    workspace = Path(workspace_dir).expanduser().resolve(strict=False)
    root = (workspace / ".flocks" / "missions").resolve(strict=False)
    path = (root / session_id).resolve(strict=False)
    if path != root and root not in path.parents:
        raise ValueError("Mission State path escapes the project workspace")
    return path


def mission_path(workspace_dir: str | Path, session_id: str) -> Path:
    """Return the main-Agent Mission file for a Session."""
    return mission_dir(workspace_dir, session_id) / "mission.md"


def _read_bounded(path: Path, limit: int, *, tail: bool = False) -> str:
    if not path.is_file():
        return ""
    content = path.read_text(encoding="utf-8")
    if len(content) <= limit:
        return content.strip()
    if tail:
        return ("…\n" + content[-limit:]).strip()
    return (content[:limit] + "\n…").strip()


def render_resume_snapshot(workspace_dir: str | Path, session_id: str) -> str:
    """Render bounded root State for Session or compaction recovery."""
    state_dir = mission_dir(workspace_dir, session_id)
    mission = _read_bounded(
        state_dir / "mission.md",
        MISSION_CONTEXT_LIMIT,
    )
    if not mission:
        return ""

    progress = _read_bounded(
        state_dir / "progress.md",
        SHARED_CONTEXT_LIMIT,
        tail=True,
    )
    findings = _read_bounded(
        state_dir / "findings.md",
        SHARED_CONTEXT_LIMIT,
        tail=True,
    )
    artifacts = _read_bounded(
        state_dir / "artifacts" / "INDEX.md",
        ARTIFACT_INDEX_LIMIT,
        tail=True,
    )

    parts = [
        "## Mission State Snapshot",
        f"- State directory: `{state_dir}`",
        "### Mission",
        mission,
    ]
    if findings:
        parts.extend(["### Findings", findings])
    if progress:
        parts.extend(["### Progress", progress])
    if artifacts:
        parts.extend(["### Artifact Index", artifacts])
    return "\n\n".join(parts)


def render_shared_updates(workspace_dir: str | Path, session_id: str) -> str:
    """Render shared State summaries after delegated work completes."""
    state_dir = mission_dir(workspace_dir, session_id)
    if not (state_dir / "mission.md").is_file():
        return ""

    progress = _read_bounded(
        state_dir / "progress.md",
        SHARED_CONTEXT_LIMIT,
        tail=True,
    )
    findings = _read_bounded(
        state_dir / "findings.md",
        SHARED_CONTEXT_LIMIT,
        tail=True,
    )
    artifacts = _read_bounded(
        state_dir / "artifacts" / "INDEX.md",
        ARTIFACT_INDEX_LIMIT,
        tail=True,
    )

    parts = [
        "## Delegated State Updates",
        f"- State directory: `{state_dir}`",
    ]
    if findings:
        parts.extend(["### Findings", findings])
    if progress:
        parts.extend(["### Progress", progress])
    if artifacts:
        parts.extend(["### Artifact Index", artifacts])
    if len(parts) == 2:
        parts.append("No shared State updates have been recorded yet.")
    return "\n\n".join(parts)


def render_subagent_handoff(
    workspace_dir: str | Path,
    owner_session_id: str,
) -> str:
    """Render shared paths and update requirements for a delegated Agent."""
    state_dir = mission_dir(workspace_dir, owner_session_id)
    if not (state_dir / "mission.md").is_file():
        return ""
    return SUBAGENT_STATE_GUIDANCE.format(state_dir=state_dir)


def render_state_snapshot(workspace_dir: str | Path, session_id: str) -> str:
    """Compatibility alias for the root recovery Snapshot."""
    return render_resume_snapshot(workspace_dir, session_id)


def render_hot_context(workspace_dir: str | Path, session_id: str) -> str:
    """Render Guidance and the current State Snapshot for compatibility."""
    snapshot = render_resume_snapshot(workspace_dir, session_id)
    if not snapshot:
        return ""
    return "\n\n".join([MISSION_STATE_GUIDANCE, snapshot])
