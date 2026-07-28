"""Canonical scope and filesystem paths for persistent Memory."""

from __future__ import annotations

import re
from pathlib import Path

from flocks.memory.types import MemoryScope


GLOBAL_SCOPE_ID = ""
GLOBAL_MEMORY_FILENAME = "MEMORY.md"
USER_FILENAME = "USER.md"
PROJECT_MEMORY_INITIAL_CONTENT = "# Project Memory\n"

_REGISTERED_PROJECT_RE = re.compile(r"^prj_[A-Za-z0-9_-]+$")
_CURATED_PATHS = {
    USER_FILENAME.casefold(): USER_FILENAME,
    GLOBAL_MEMORY_FILENAME.casefold(): GLOBAL_MEMORY_FILENAME,
}


def is_registered_project_id(project_id: str) -> bool:
    """Return whether *project_id* is safe and belongs to a registered project."""
    return bool(_REGISTERED_PROJECT_RE.fullmatch(project_id))


def normalize_curated_path(path: str) -> str:
    """Normalize the only two model-writable curated Memory filenames."""
    if not path or Path(path).name != path:
        raise ValueError("path must be USER.md or MEMORY.md")
    normalized = _CURATED_PATHS.get(path.casefold())
    if normalized is None:
        raise ValueError("path must be USER.md or MEMORY.md")
    return normalized


def scope_id_for(scope: MemoryScope, project_id: str) -> str:
    """Derive the internal scope identifier from the current Session project."""
    if scope == MemoryScope.GLOBAL:
        return GLOBAL_SCOPE_ID
    if not is_registered_project_id(project_id):
        raise ValueError(
            "Project memory is only available for registered prj_* projects; "
            "the current default session has no project memory"
        )
    return project_id


def validate_scope_path(scope: MemoryScope, path: str) -> str:
    """Validate a public curated Memory scope/path combination."""
    normalized = normalize_curated_path(path)
    if scope == MemoryScope.PROJECT and normalized == USER_FILENAME:
        raise ValueError("project scope only supports MEMORY.md")
    return normalized


def memory_file_path(
    memory_root: Path,
    scope: MemoryScope,
    scope_id: str,
    path: str,
) -> Path:
    """Resolve a canonical curated Memory path without accepting raw subpaths."""
    normalized = validate_scope_path(scope, path)
    if scope == MemoryScope.GLOBAL:
        if scope_id:
            raise ValueError("global scope_id must be empty")
        return memory_root / normalized
    if not is_registered_project_id(scope_id):
        raise ValueError("project scope requires a registered prj_* scope_id")
    return memory_root / "projects" / scope_id / normalized


def classify_memory_path(
    memory_root: Path,
    file_path: Path,
) -> tuple[MemoryScope, str, str] | None:
    """Classify an indexed Markdown file into a canonical scope and path."""
    try:
        relative = file_path.relative_to(memory_root)
    except ValueError:
        return None

    parts = relative.parts
    if len(parts) == 3 and parts[0] == "projects":
        project_id, filename = parts[1], parts[2]
        if is_registered_project_id(project_id) and filename.casefold() == GLOBAL_MEMORY_FILENAME.casefold():
            return (
                MemoryScope.PROJECT,
                project_id,
                relative.as_posix(),
            )
        return None
    if parts and parts[0] == "projects":
        return None
    if file_path.suffix.casefold() != ".md":
        return None
    return MemoryScope.GLOBAL, GLOBAL_SCOPE_ID, relative.as_posix()
