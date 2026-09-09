"""
Workspace data models
"""

from typing import Literal, Optional
from pydantic import BaseModel


class WorkspaceNode(BaseModel):
    """A file or directory node in the workspace"""
    name: str
    path: str
    type: Literal["file", "directory"]
    size: Optional[int] = None
    modified_at: Optional[float] = None
    is_text_file: bool = False
    editable: bool = False
    children: Optional[list["WorkspaceNode"]] = None


class WorkspaceStats(BaseModel):
    """Workspace statistics"""
    file_count: int
    dir_count: int
    total_size_bytes: int
    memory_file_count: int
    memory_total_size_bytes: int
