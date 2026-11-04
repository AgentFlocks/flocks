"""Neutral guards for extensions that own active work inside a project."""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

DeletionGuard = Callable[[Any, str], Awaitable[None]]
_deletion_guards: dict[str, DeletionGuard] = {}


def register_project_deletion_guard(name: str, guard: DeletionGuard) -> None:
    """Replace a named guard; every guard must allow deletion to proceed."""
    _deletion_guards[name] = guard


def unregister_project_deletion_guard(name: str) -> None:
    _deletion_guards.pop(name, None)


async def require_project_deletion_allowed(project: Any, owner_id: str) -> None:
    from flocks.project.project import ProjectDeletionError

    for guard in tuple(_deletion_guards.values()):
        try:
            await asyncio.wait_for(guard(project, owner_id), timeout=10)
        except ProjectDeletionError:
            raise
        except Exception as exc:
            raise ProjectDeletionError("Project deletion cannot be checked while an extension is unavailable") from exc
