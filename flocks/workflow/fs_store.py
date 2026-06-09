"""Shared filesystem-backed workflow lookup helpers."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from flocks.utils.log import Log

from .center import resolve_global_workflow_roots, resolve_project_workflow_roots

log = Log.create(service="workflow.fs-store")

_workspace_root: Optional[Path] = None


def _is_cached_workspace_root_valid(current: Path, cached_root: Path) -> bool:
    """Return True when the cached root still applies to the current cwd."""
    if not (cached_root / ".flocks").is_dir():
        return False
    return current == cached_root or cached_root in current.parents


def find_workspace_root() -> Path:
    """Walk up from cwd until a directory containing `.flocks/` is found."""
    global _workspace_root
    current = Path.cwd().resolve()
    if (
        _workspace_root is not None
        and _is_cached_workspace_root_valid(current, _workspace_root)
    ):
        return _workspace_root
    for candidate in [current, *current.parents]:
        if (candidate / ".flocks").is_dir():
            _workspace_root = candidate
            return candidate
    _workspace_root = current
    return current


def workflow_scan_dirs() -> list[tuple[Path, str]]:
    """Return all workflow roots ordered from lowest to highest priority."""
    workspace = find_workspace_root()
    return [
        (root, "global") for root in resolve_global_workflow_roots()
    ] + [
        (root, "project") for root in resolve_project_workflow_roots(workspace)
    ]


def read_workflow_dir(
    wf_dir: Path,
    workflow_id: str,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Read a single workflow directory and return metadata plus JSON."""
    json_file = wf_dir / "workflow.json"
    if not json_file.is_file():
        return None

    try:
        workflow_json = json.loads(json_file.read_text(encoding="utf-8"))
        json_mtime_ms = int(json_file.stat().st_mtime * 1000)

        meta_file = wf_dir / "meta.json"
        if meta_file.is_file():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        else:
            meta = {
                "name": workflow_json.get("name", workflow_id),
                "description": workflow_json.get("description"),
                "category": workflow_json.get("category", "default"),
                "status": "active",
                "createdBy": None,
                "createdAt": json_mtime_ms,
                "updatedAt": json_mtime_ms,
            }

        md_file = wf_dir / "workflow.md"
        edit_md_file = wf_dir / "workflow.edit.md"
        markdown_content: Optional[str] = None
        edit_markdown_content: Optional[str] = None
        updated_candidates = [json_mtime_ms]
        if md_file.is_file():
            markdown_content = md_file.read_text(encoding="utf-8")
            updated_candidates.append(int(md_file.stat().st_mtime * 1000))
        if edit_md_file.is_file():
            edit_markdown_content = edit_md_file.read_text(encoding="utf-8")
            updated_candidates.append(int(edit_md_file.stat().st_mtime * 1000))
        if meta_file.is_file():
            updated_candidates.append(int(meta_file.stat().st_mtime * 1000))
            updated_candidates.append(int(meta.get("updatedAt") or 0))
        meta = {**meta, "updatedAt": max(updated_candidates)}

        return {
            **meta,
            "id": workflow_id,
            "source": source,
            "workflowJson": workflow_json,
            "markdownContent": markdown_content,
            "editMarkdownContent": edit_markdown_content,
        }
    except Exception as exc:
        log.warning(
            "workflow.fs.read.failed",
            {"id": workflow_id, "source": source, "error": str(exc)},
        )
        return None


def read_workflow_from_fs(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a workflow by ID from workflow directories on disk."""
    result = None
    for root, source in workflow_scan_dirs():
        data = read_workflow_dir(root / workflow_id, workflow_id, source)
        if data is not None:
            result = data
    return result


def resolve_workflow_id_from_source(workflow: Any) -> Optional[str]:
    """Resolve a canonical workflow ID from a tool/runtime workflow argument.

    This is intentionally conservative: only return an ID when it maps cleanly to
    a workflow already discoverable from the filesystem.
    """
    if isinstance(workflow, dict):
        candidate = workflow.get("id")
        if isinstance(candidate, str) and candidate.strip():
            workflow_id = candidate.strip()
            if read_workflow_from_fs(workflow_id) is not None:
                return workflow_id
        return None

    if isinstance(workflow, Path):
        workflow_path = workflow.expanduser()
    elif isinstance(workflow, str):
        raw = workflow.strip()
        if not raw:
            return None
        if read_workflow_from_fs(raw) is not None:
            return raw
        workflow_path = Path(raw).expanduser()
    else:
        return None

    if not workflow_path.is_file():
        return None

    try:
        resolved = workflow_path.resolve()
    except OSError:
        return None

    for root, _source in workflow_scan_dirs():
        try:
            relative = resolved.relative_to(root)
        except ValueError:
            continue
        parts = relative.parts
        if len(parts) == 2 and parts[1] == "workflow.json":
            workflow_id = parts[0]
            if read_workflow_from_fs(workflow_id) is not None:
                return workflow_id
    return None
