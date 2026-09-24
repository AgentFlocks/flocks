"""
VCS routes for Flocks TUI compatibility

Provides /vcs endpoint that Flocks SDK expects.
"""

import asyncio
import os
import subprocess
from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="vcs-routes")

# A hung ``git`` (slow network mount, stuck credential helper) must not pin
# the request forever; TimeoutExpired lands in the generic handler below.
_GIT_TIMEOUT_SECONDS = 5


class VcsResponse(BaseModel):
    """VCS information response"""
    type: Optional[str] = None  # "git" or None
    branch: Optional[str] = None
    root: Optional[str] = None
    dirty: bool = False


@router.get(
    "",
    response_model=VcsResponse,
    summary="Get VCS info",
    description="Retrieve version control system (VCS) information"
)
async def get_vcs(
    directory: Optional[str] = Query(None, description="Project directory"),
) -> VcsResponse:
    """Get VCS information for the project"""
    cwd = directory or os.getcwd()
    
    try:
        # Check if it's a git repo
        result = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        
        if result.returncode != 0:
            return VcsResponse()
        
        # Get branch name
        branch_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        
        # Get repo root
        root_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        root = root_result.stdout.strip() if root_result.returncode == 0 else None
        
        # Check if dirty
        status_result = await asyncio.to_thread(
            subprocess.run,
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
        dirty = bool(status_result.stdout.strip()) if status_result.returncode == 0 else False
        
        return VcsResponse(
            type="git",
            branch=branch,
            root=root,
            dirty=dirty,
        )
    except Exception as e:
        log.warn("vcs.error", {"error": str(e)})
        return VcsResponse()
