"""
Project management routes

Routes for project listing, retrieval, and updates
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from flocks.project.project import Project, ProjectInfo, ProjectIcon
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.project")


class ProjectUpdateRequest(BaseModel):
    """Project update request"""
    name: Optional[str] = None
    icon: Optional[ProjectIcon] = None


class ProjectCreateRequest(BaseModel):
    """Project create request"""
    name: str
    worktree: Optional[str] = None
    icon: Optional[ProjectIcon] = None


@router.get("", response_model=List[ProjectInfo], include_in_schema=False)
@router.get("/", response_model=List[ProjectInfo], summary="List all projects")
async def list_projects():
    """
    List all projects
    
    Get a list of projects that have been opened with Flocks.
    """
    try:
        projects = await Project.list()
        return projects
    except Exception as e:
        log.error("project.list.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


@router.post("", response_model=ProjectInfo, include_in_schema=False)
@router.post("/", response_model=ProjectInfo, summary="Create project")
async def create_project(request: ProjectCreateRequest):
    """
    Create a user-managed project.

    User-managed projects organize sessions without changing the current
    workspace execution directory.
    """
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name cannot be empty")

    try:
        import os
        from flocks.project.instance import Instance

        worktree = request.worktree
        if not worktree:
            try:
                worktree = Instance.directory
            except Exception:
                worktree = os.getcwd()

        project = await Project.create(
            name=name,
            worktree=str(worktree),
            icon=request.icon,
        )
        return project
    except HTTPException:
        raise
    except Exception as e:
        log.error("project.create.error", {"error": str(e)})
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/current", response_model=ProjectInfo, summary="Get current project")
async def get_current_project():
    """
    Get current project
    
    Retrieve the currently active project that Flocks is working with.
    """
    current = Project.current()
    if not current:
        raise HTTPException(status_code=404, detail="No current project")
    
    return current


@router.patch("/{project_id}", response_model=ProjectInfo, summary="Update project")
async def update_project(project_id: str, update: ProjectUpdateRequest):
    """
    Update project
    
    Update project properties such as name, icon and color.
    """
    try:
        # Get existing project
        project = await Project.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        
        # Update fields
        update_data = {}
        if update.name is not None:
            update_data["name"] = update.name
        if update.icon is not None:
            update_data["icon"] = update.icon
        
        updated_project = await Project.update(project_id, **update_data)
        
        log.info("project.updated", {"id": project_id})
        
        return updated_project
    except HTTPException:
        raise
    except Exception as e:
        log.error("project.update.error", {"error": str(e), "id": project_id})
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}", response_model=ProjectInfo, summary="Get project")
async def get_project(project_id: str):
    """
    Get project
    
    Retrieve information about a specific project.
    """
    try:
        project = await Project.get(project_id)
        if not project:
            raise HTTPException(status_code=404, detail=f"Project {project_id} not found")
        
        return project
    except HTTPException:
        raise
    except Exception as e:
        log.error("project.get.error", {"error": str(e), "id": project_id})
        raise HTTPException(status_code=500, detail=str(e))
