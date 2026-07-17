"""Project registry routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from flocks.auth.context import AuthUser
from flocks.project.project import (
    DEFAULT_PROJECT_ID,
    Project,
    ProjectDeletionError,
    ProjectInfo,
    ProjectNameConflictError,
    ProjectPathConflictError,
)
from flocks.server.auth import require_user
from flocks.session.policy import SessionPolicy
from flocks.session.session import Session
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.project")

_MANAGER_CATEGORIES = {"user", "workflow", "entity-config"}


class ProjectUpdateRequest(BaseModel):
    """Editable project properties."""

    name: str


class ProjectCreateRequest(BaseModel):
    """Register an existing project directory."""

    worktree: str
    name: Optional[str] = None


class FolderEntry(BaseModel):
    """Directory row returned by the server-side folder picker."""

    name: str
    path: str


class FolderBrowserResponse(BaseModel):
    """Directory listing constrained to configured project roots."""

    path: str
    parent: Optional[str]
    roots: List[FolderEntry]
    entries: List[FolderEntry]


async def _list_project_summaries(user: AuthUser, search: Optional[str]) -> List[ProjectInfo]:
    owner_id = user.id
    projects = await Project.list(
        owner_id=owner_id,
        default_worktree=Project.default_worktree_candidate(),
    )
    term = search.strip().casefold() if search else None
    stats = Project.get_session_stats_cache(owner_id, term or "")
    if stats is None:
        registered_ids = {project.id for project in projects if not project.is_default}
        counts = {project.id: 0 for project in projects}
        matched_counts = {project.id: 0 for project in projects}
        last_activity: dict[str, int] = {}

        for session in await Session.list_all_unfiltered():
            if not SessionPolicy.can_read(session, user):
                continue
            metadata = session.metadata if isinstance(session.metadata, dict) else {}
            if metadata.get("hideFromSessionManager"):
                continue
            if session.parent_id or session.category not in _MANAGER_CATEGORIES:
                continue
            project_id = (
                session.project_id
                if session.project_id in registered_ids
                else DEFAULT_PROJECT_ID
            )
            counts[project_id] = counts.get(project_id, 0) + 1
            if term is None or term in session.title.casefold():
                matched_counts[project_id] = matched_counts.get(project_id, 0) + 1
            last_activity[project_id] = max(
                last_activity.get(project_id, 0),
                session.time.updated,
            )
        stats = {
            project.id: (
                counts.get(project.id, 0),
                matched_counts.get(project.id, 0),
                last_activity.get(project.id),
            )
            for project in projects
        }
        Project.set_session_stats_cache(owner_id, term or "", stats)

    return [
        project.model_copy(
            update={
                "session_count": stats.get(project.id, (0, 0, None))[0],
                "matched_session_count": stats.get(project.id, (0, 0, None))[1],
                "last_activity_at": stats.get(project.id, (0, 0, None))[2],
            },
        )
        for project in projects
    ]


@router.get("", response_model=List[ProjectInfo], include_in_schema=False)
@router.get("/", response_model=List[ProjectInfo], summary="List projects")
async def list_projects(request: Request, search: Optional[str] = Query(None)):
    """List the virtual default project and user-registered folders."""

    user = require_user(request)
    try:
        return await _list_project_summaries(user, search)
    except Exception as exc:
        log.error("project.list.error", {"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("", response_model=ProjectInfo, include_in_schema=False)
@router.post("/", response_model=ProjectInfo, summary="Create project")
async def create_project(request: Request, payload: ProjectCreateRequest):
    """Register an existing directory without writing project database rows."""

    user = require_user(request)
    try:
        return await Project.create(
            owner_id=user.id,
            name=payload.name,
            worktree=payload.worktree,
        )
    except ProjectPathConflictError as exc:
        return exc.project
    except ProjectNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        log.error("project.create.error", {"error": str(exc)})
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/current", response_model=ProjectInfo, summary="Get default project")
async def get_current_project(request: Request, project_id: Optional[str] = Query(None, alias="projectID")):
    """Return a requested registered project, falling back to default."""

    user = require_user(request)
    project = await Project.get(
        project_id or DEFAULT_PROJECT_ID,
        owner_id=user.id,
        default_worktree=Project.default_worktree_candidate(),
    )
    if project is None:
        project = await Project.get(
            DEFAULT_PROJECT_ID,
            owner_id=user.id,
            default_worktree=Project.default_worktree_candidate(),
        )
    if project is None:
        raise HTTPException(status_code=500, detail="Default project is unavailable")
    return project


@router.get("/folders", response_model=FolderBrowserResponse, summary="Browse project folders")
async def browse_project_folders(request: Request, path: Optional[str] = Query(None)):
    """List directories under configured roots without exposing file contents."""

    user = require_user(request)
    default_project = await Project.get(
        DEFAULT_PROJECT_ID,
        owner_id=user.id,
        default_worktree=Project.default_worktree_candidate(),
    )
    if default_project is None:
        raise HTTPException(status_code=500, detail="Default project is unavailable")

    roots = Project.allowed_roots(default_project.worktree)
    if not roots:
        raise HTTPException(status_code=400, detail="No project roots are available")

    try:
        current = Path(path).expanduser().resolve(strict=True) if path else Path(default_project.worktree).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail="Directory does not exist") from exc
    if not current.is_dir():
        raise HTTPException(status_code=400, detail="Path must be a directory")
    containing_root = next(
        (root for root in roots if current == root or current.is_relative_to(root)),
        None,
    )
    if containing_root is None:
        raise HTTPException(status_code=403, detail="Directory is outside the allowed roots")

    entries: List[FolderEntry] = []
    try:
        children = sorted(
            (child for child in current.iterdir() if child.is_dir()),
            key=lambda child: (child.name.startswith("."), child.name.casefold()),
        )
        for child in children:
            try:
                resolved = child.resolve(strict=True)
            except (OSError, RuntimeError):
                continue
            if not (resolved == containing_root or resolved.is_relative_to(containing_root)):
                continue
            if not os.access(resolved, os.R_OK | os.X_OK):
                continue
            entries.append(FolderEntry(name=child.name, path=str(resolved)))
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Directory is not readable") from exc

    parent = current.parent
    parent_value = (
        str(parent)
        if current != containing_root and (parent == containing_root or parent.is_relative_to(containing_root))
        else None
    )
    return FolderBrowserResponse(
        path=str(current),
        parent=parent_value,
        roots=[FolderEntry(name=root.name or str(root), path=str(root)) for root in roots],
        entries=entries,
    )


@router.patch("/{project_id}", response_model=ProjectInfo, summary="Rename project")
async def update_project(project_id: str, payload: ProjectUpdateRequest, request: Request):
    user = require_user(request)
    try:
        return await Project.update(project_id, owner_id=user.id, name=payload.name)
    except ProjectNameConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ProjectDeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{project_id}", response_model=bool, summary="Remove project")
async def delete_project(project_id: str, request: Request):
    """Remove a project registration while preserving sessions and files."""

    user = require_user(request)
    try:
        return await Project.delete(project_id, owner_id=user.id)
    except ProjectDeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{project_id}", response_model=ProjectInfo, summary="Get project")
async def get_project(project_id: str, request: Request):
    user = require_user(request)
    project = await Project.get(
        project_id,
        owner_id=user.id,
        default_worktree=Project.default_worktree_candidate(),
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
    return project
