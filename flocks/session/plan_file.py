"""Session-scoped plan files modeled after OpenCode's plan artifacts."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


PLAN_DIRECTORY = Path(".flocks") / "plans"
_SAFE_COMPONENT_RE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass(frozen=True)
class SessionPlanFile:
    """Resolved plan artifact for one session."""

    path: Path
    relative_path: str
    permission_path: str


def _safe_component(value: object, fallback: str) -> str:
    normalized = _SAFE_COMPONENT_RE.sub("-", str(value or "")).strip(".-")
    return normalized or fallback


def session_plan_file(
    session: Any,
    *,
    worktree: Optional[str] = None,
) -> SessionPlanFile:
    """Return the stable, project-local plan file for a session."""

    session_root = Path(str(session.directory)).expanduser().resolve(strict=False)
    root = (
        Path(worktree).expanduser().resolve(strict=False)
        if worktree and Path(worktree) != Path("/")
        else session_root
    )
    created = int(getattr(getattr(session, "time", None), "created", 0) or 0)
    slug = _safe_component(getattr(session, "slug", None), "session")
    filename = f"{created}-{slug}.md"
    path = root / PLAN_DIRECTORY / filename
    relative_path = Path(os.path.relpath(path, session_root)).as_posix()
    return SessionPlanFile(
        path=path,
        relative_path=relative_path,
        permission_path=(PLAN_DIRECTORY / filename).as_posix(),
    )


def context_plan_file(ctx: Any) -> Optional[SessionPlanFile]:
    """Resolve the plan artifact in the tool's host or sandbox workspace."""

    extra = getattr(ctx, "extra", {}) or {}
    relative_path = str(extra.get("plan_relative_path") or "").strip()
    permission_path = str(extra.get("plan_permission_path") or "").strip()
    sandbox = extra.get("sandbox")
    if isinstance(sandbox, dict) and sandbox.get("workspace_dir") and relative_path:
        path = (
            Path(str(sandbox["workspace_dir"])).expanduser()
            / Path(relative_path)
        ).resolve(strict=False)
    else:
        raw_path = str(extra.get("plan_file_path") or "").strip()
        if not raw_path:
            return None
        path = Path(raw_path).expanduser().absolute()
    if not relative_path:
        return None
    return SessionPlanFile(
        path=path,
        relative_path=relative_path,
        permission_path=permission_path,
    )


def _validation_root(ctx: Any) -> Path:
    extra = getattr(ctx, "extra", {}) or {}
    sandbox = extra.get("sandbox")
    if isinstance(sandbox, dict) and sandbox.get("workspace_dir"):
        return Path(str(sandbox["workspace_dir"])).expanduser().resolve(strict=False)
    workspace_dir = extra.get("workspace_dir")
    if workspace_dir:
        return Path(str(workspace_dir)).expanduser().resolve(strict=False)
    return Path.cwd().resolve(strict=False)


def _expected_plan_path(ctx: Any) -> Optional[Path]:
    extra = getattr(ctx, "extra", {}) or {}
    sandbox = extra.get("sandbox")
    relative_path = str(extra.get("plan_relative_path") or "").strip()
    if isinstance(sandbox, dict) and sandbox.get("workspace_dir") and relative_path:
        return _validation_root(ctx) / Path(relative_path)
    absolute_path = str(extra.get("plan_file_path") or "").strip()
    if absolute_path:
        return Path(absolute_path).expanduser().absolute()
    return None


def _normalize_tool_path(ctx: Any, raw_path: object) -> Optional[Path]:
    value = str(raw_path or "").strip()
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = _validation_root(ctx) / path
    return path.resolve(strict=False)


def is_current_plan_path(ctx: Any, raw_path: object) -> bool:
    """Return whether a tool path is exactly the current session plan file."""

    expected = _expected_plan_path(ctx)
    candidate = _normalize_tool_path(ctx, raw_path)
    if expected is None or candidate is None:
        return False
    # Keep the expected path lexical. If .flocks/plans or the file itself is a
    # symlink, resolving the candidate moves it elsewhere and the comparison
    # fails instead of granting an external write.
    return os.path.normcase(str(candidate)) == os.path.normcase(str(expected.absolute()))


def plan_edit_patterns_allowed(ctx: Any, patterns: Iterable[object]) -> bool:
    """Validate resolved edit permission patterns for Plan mode."""

    values = list(patterns)
    expected = _expected_plan_path(ctx)
    expected_is_safe = bool(
        expected
        and os.path.normcase(str(expected.resolve(strict=False)))
        == os.path.normcase(str(expected.absolute()))
    )
    extra = getattr(ctx, "extra", {}) or {}
    accepted_relative_paths = {
        Path(str(value)).as_posix()
        for value in (
            extra.get("plan_relative_path"),
            extra.get("plan_permission_path"),
        )
        if value
    }
    return bool(values) and all(
        (
            expected_is_safe
            and Path(str(value)).as_posix() in accepted_relative_paths
        )
        or is_current_plan_path(ctx, value)
        for value in values
    )


def plan_file_prompt(
    session: Any,
    *,
    plan: Optional[SessionPlanFile] = None,
) -> str:
    """Build the OpenCode-style per-turn plan file reminder."""

    plan = plan or session_plan_file(session)
    if plan.path.is_file():
        file_guidance = (
            f"A plan file already exists at `{plan.relative_path}`. Read it and "
            "update it incrementally with the edit or write tool."
        )
    else:
        file_guidance = (
            f"No plan file exists yet. Create it at `{plan.relative_path}` with "
            "the write tool."
        )
    return f"""## Plan File

{file_guidance}

This is the only file you may edit in Plan mode. Keep the decision-complete
implementation plan in this file, then call plan_exit.
"""
