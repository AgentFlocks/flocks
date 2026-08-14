"""Internal one-Project-per-Session lifecycle for managed reports."""

from __future__ import annotations

from pathlib import Path

from flocks.auth.context import API_TOKEN_SERVICE_USER_ID
from flocks.project.project import Project, ProjectInfo
from flocks.session.session import Session, SessionInfo

from .files import session_root


REPORT_SESSION_CATEGORY = "situation-report"


class ReportProjectError(RuntimeError):
    """A report Session is not bound to its expected internal Project."""


async def create_report_project(*, session_id: str, title: str | None) -> ProjectInfo:
    """Create the private Project worktree for a preallocated Session ID."""

    worktree = session_root(session_id)
    name = (title or "Situation report").strip() or "Situation report"
    return await Project.create(
        owner_id=API_TOKEN_SERVICE_USER_ID,
        name=f"{name} [{session_id[-12:]}]",
        worktree=str(worktree),
    )


async def rollback_report_project(project: ProjectInfo) -> None:
    """Best-effort rollback when Session persistence fails after Project creation."""

    try:
        await Project.delete(project.id, owner_id=API_TOKEN_SERVICE_USER_ID)
    except Exception:
        pass
    worktree = Path(project.worktree)
    try:
        worktree.rmdir()
        worktree.parent.rmdir()
    except OSError:
        # Never delete a non-empty worktree during rollback.
        pass


async def require_report_project(session: SessionInfo) -> ProjectInfo:
    """Verify the internal Session -> Project -> worktree binding."""

    if session.category != REPORT_SESSION_CATEGORY:
        raise ReportProjectError("Session is not a managed situation-report Session")
    if session.owner_user_id != API_TOKEN_SERVICE_USER_ID:
        raise ReportProjectError("Managed report Session is not owned by the API service identity")

    project = await Project.get(session.project_id, owner_id=API_TOKEN_SERVICE_USER_ID)
    if project is None:
        raise ReportProjectError("Managed report Project is unavailable")

    expected = session_root(session.id).resolve()
    if Path(session.directory).expanduser().resolve() != expected:
        raise ReportProjectError("Managed report Session directory is inconsistent")
    if Path(project.worktree).expanduser().resolve() != expected:
        raise ReportProjectError("Managed report Project worktree is inconsistent")
    bound_sessions = [item for item in await Session.list(project.id) if item.status != "deleted"]
    if len(bound_sessions) != 1 or bound_sessions[0].id != session.id:
        raise ReportProjectError("Managed report Project must contain exactly one Session")
    return project
