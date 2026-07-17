"""
Project management module

Handles project discovery, metadata, and lifecycle
"""

import os
import subprocess
from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field

from flocks.storage.storage import Storage
from flocks.utils.log import Log
from flocks.utils.id import Identifier

log = Log.create(service="project")


class ProjectNameConflictError(ValueError):
    """Raised when a project name is already used in the same worktree."""


class ProjectDeletionError(ValueError):
    """Raised when a project cannot be deleted safely."""


class ProjectIcon(BaseModel):
    """Project icon configuration"""
    url: Optional[str] = None
    override: Optional[str] = None
    color: Optional[str] = None


class ProjectTime(BaseModel):
    """Project time metadata"""
    created: int
    updated: int
    initialized: Optional[int] = None


class ProjectInfo(BaseModel):
    """Project information"""
    id: str
    worktree: str
    vcs: Optional[str] = None  # "git" or None
    name: Optional[str] = None
    icon: Optional[ProjectIcon] = None
    time: ProjectTime
    sandboxes: List[str] = Field(default_factory=list)


class Project:
    """Project management namespace"""
    
    _current: Optional[ProjectInfo] = None

    @staticmethod
    def _normalized_worktree(worktree: str) -> str:
        return os.path.normcase(os.path.realpath(worktree))

    @classmethod
    async def _ensure_unique_name(
        cls,
        *,
        name: str,
        worktree: str,
        exclude_project_id: Optional[str] = None,
    ) -> None:
        entries = await Storage.list_entries(prefix="project/", model=ProjectInfo)
        normalized_name = name.casefold()
        normalized_worktree = cls._normalized_worktree(worktree)

        for _, project in entries:
            if project.id == exclude_project_id or not project.name:
                continue
            if (
                project.name.strip().casefold() == normalized_name
                and cls._normalized_worktree(project.worktree) == normalized_worktree
            ):
                raise ProjectNameConflictError(
                    f"Project name '{name}' already exists in this workspace"
                )

    @classmethod
    async def create(
        cls,
        *,
        name: str,
        worktree: str,
        icon: Optional[ProjectIcon] = None,
    ) -> ProjectInfo:
        """
        Create a user-managed project grouping.

        User-managed projects share the provided worktree but get their own
        project ID so sessions can be organized independently from the default
        auto-discovered project.
        """
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Project name cannot be empty")

        await cls._ensure_unique_name(name=normalized_name, worktree=worktree)

        now = int(datetime.now().timestamp() * 1000)
        project_id = Identifier.ascending("slug").replace("slg_", "prj_", 1)
        project = ProjectInfo(
            id=project_id,
            worktree=worktree,
            vcs=None,
            name=normalized_name,
            icon=icon,
            sandboxes=[],
            time=ProjectTime(
                created=now,
                updated=now,
            ),
        )
        await Storage.write(["project", project_id], project)
        log.info("project.created", {"id": project_id})
        return project
    
    @classmethod
    async def from_directory(cls, directory: str) -> Dict[str, Any]:
        """
        Create or load project from directory
        
        Args:
            directory: Directory path
            
        Returns:
            Dict with 'project' and 'sandbox' keys
        """
        log.info("from_directory", {"directory": directory})
        
        # Find git repository
        git_info = await cls._find_git_repo(directory)
        
        project_id = git_info["id"]
        worktree = git_info["worktree"]
        sandbox = git_info["sandbox"]
        vcs = git_info["vcs"]
        
        # Try to load existing project
        existing = await Storage.read(["project", project_id], ProjectInfo)
        
        if not existing:
            # Create new project
            existing = ProjectInfo(
                id=project_id,
                worktree=worktree,
                vcs=vcs,
                sandboxes=[],
                time=ProjectTime(
                    created=int(datetime.now().timestamp() * 1000),
                    updated=int(datetime.now().timestamp() * 1000),
                )
            )
        
        # Update project info
        result = ProjectInfo(
            id=existing.id,
            worktree=worktree,
            vcs=vcs,
            name=existing.name,
            icon=existing.icon,
            time=ProjectTime(
                created=existing.time.created,
                updated=int(datetime.now().timestamp() * 1000),
                initialized=existing.time.initialized,
            ),
            sandboxes=existing.sandboxes.copy() if existing.sandboxes else [],
        )
        
        # Add sandbox if not in list
        if sandbox != result.worktree and sandbox not in result.sandboxes:
            result.sandboxes.append(sandbox)
        
        # Filter out non-existent sandboxes
        result.sandboxes = [s for s in result.sandboxes if os.path.exists(s)]
        
        # Save project
        await Storage.write(["project", project_id], result)
        
        cls._current = result
        
        return {
            "project": result,
            "sandbox": sandbox,
        }
    
    @classmethod
    async def _find_git_repo(cls, directory: str) -> Dict[str, Any]:
        """
        Find git repository information
        
        Args:
            directory: Starting directory
            
        Returns:
            Dict with id, worktree, sandbox, vcs
        """
        # Look for .git directory
        current = os.path.abspath(directory)
        git_dir = None
        
        while current != "/":
            git_path = os.path.join(current, ".git")
            if os.path.exists(git_path):
                git_dir = git_path
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
        
        if not git_dir:
            # No git repository found
            return {
                "id": "global",
                "worktree": directory,
                "sandbox": directory,
                "vcs": None,
            }
        
        sandbox = os.path.dirname(git_dir)
        
        # Try to get git root commit for ID
        try:
            result = subprocess.run(
                ["git", "rev-list", "--max-parents=0", "--all"],
                cwd=sandbox,
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode == 0:
                roots = [line.strip() for line in result.stdout.split("\n") if line.strip()]
                roots.sort()
                
                if roots:
                    project_id = roots[0]
                    
                    # Cache the ID
                    id_file = os.path.join(git_dir, "flocks")
                    try:
                        with open(id_file, "w") as f:
                            f.write(project_id)
                    except Exception:
                        pass
                    
                    # Get worktree
                    try:
                        result = subprocess.run(
                            ["git", "rev-parse", "--show-toplevel"],
                            cwd=sandbox,
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if result.returncode == 0:
                            worktree = result.stdout.strip()
                        else:
                            worktree = sandbox
                    except Exception:
                        worktree = sandbox
                    
                    return {
                        "id": project_id,
                        "worktree": worktree,
                        "sandbox": sandbox,
                        "vcs": "git",
                    }
        except Exception as e:
            log.warn("git.error", {"error": str(e)})
        
        # Check for cached ID
        id_file = os.path.join(git_dir, "flocks")
        if os.path.exists(id_file):
            try:
                with open(id_file, "r") as f:
                    project_id = f.read().strip()
                    if project_id:
                        return {
                            "id": project_id,
                            "worktree": sandbox,
                            "sandbox": sandbox,
                            "vcs": "git",
                        }
            except Exception:
                pass
        
        # Fallback to global
        return {
            "id": "global",
            "worktree": sandbox,
            "sandbox": sandbox,
            "vcs": "git",
        }
    
    @classmethod
    async def list(cls) -> List[ProjectInfo]:
        """
        List all projects
        
        Returns:
            List of project info
        """
        try:
            entries = await Storage.list_entries(prefix="project/", model=ProjectInfo)
            result = [project for _, project in entries]
            
            # Sort by updated time (newest first)
            result.sort(key=lambda p: p.time.updated, reverse=True)
            
            return result
        except Exception as e:
            log.error("project.list.error", {"error": str(e)})
            return []
    
    @classmethod
    async def get(cls, project_id: str) -> Optional[ProjectInfo]:
        """
        Get project by ID
        
        Args:
            project_id: Project ID
            
        Returns:
            Project info or None
        """
        try:
            return await Storage.read(["project", project_id], ProjectInfo)
        except Exception:
            return None
    
    @classmethod
    async def update(cls, project_id: str, **kwargs) -> ProjectInfo:
        """
        Update project
        
        Args:
            project_id: Project ID
            **kwargs: Fields to update
            
        Returns:
            Updated project info
        """
        project = await cls.get(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        if "name" in kwargs and kwargs["name"] is not None:
            normalized_name = str(kwargs["name"]).strip()
            if not normalized_name:
                raise ValueError("Project name cannot be empty")
            await cls._ensure_unique_name(
                name=normalized_name,
                worktree=project.worktree,
                exclude_project_id=project_id,
            )
            kwargs["name"] = normalized_name
        
        # Update fields
        update_data = project.model_dump()
        
        for key, value in kwargs.items():
            if key in update_data and value is not None:
                if key == "icon" and isinstance(value, dict):
                    update_data["icon"] = ProjectIcon(**value)
                else:
                    update_data[key] = value
        
        # Update timestamp
        update_data["time"]["updated"] = int(datetime.now().timestamp() * 1000)
        
        updated_project = ProjectInfo(**update_data)
        await Storage.write(["project", project_id], updated_project)
        
        if cls._current and cls._current.id == project_id:
            cls._current = updated_project
        
        log.info("project.updated", {"id": project_id})
        
        return updated_project

    @classmethod
    async def delete(cls, project_id: str) -> bool:
        """Delete an empty user-managed project."""
        project = await cls.get(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")
        if not project_id.startswith("prj_"):
            raise ProjectDeletionError("The default project cannot be deleted")

        from flocks.session.session import SessionInfo

        entries = await Storage.list_entries(
            prefix=f"session:{project_id}:",
            model=SessionInfo,
        )
        if any(session.status != "deleted" for _, session in entries):
            raise ProjectDeletionError(
                "Delete all conversations in this project before deleting it"
            )

        deleted = await Storage.delete(f"project/{project_id}")
        if cls._current and cls._current.id == project_id:
            cls._current = None
        log.info("project.deleted", {"id": project_id})
        return deleted
    
    @classmethod
    def current(cls) -> Optional[ProjectInfo]:
        """
        Get current project
        
        Returns:
            Current project info or None
        """
        return cls._current
    
    @classmethod
    def set_current(cls, project: ProjectInfo) -> None:
        """
        Set current project
        
        Args:
            project: Project info
        """
        cls._current = project
