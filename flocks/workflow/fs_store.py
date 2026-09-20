"""Shared filesystem-backed workflow lookup helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic_core import PydanticCustomError

from flocks.utils.log import Log

from .center import resolve_workflow_scan_roots

log = Log.create(service="workflow.fs-store")

_workspace_root: Optional[Path] = None
_SYSTEM_WORKFLOW_ROOT = Path(__file__).resolve().parents[2] / ".flocks" / "plugins" / "workflows"


def is_system_workflow_definition(wf_dir: Path) -> bool:
    """Check the actual selected source, never a project/native display flag."""
    for filename in ("workflow.json", "workflow.md", "workflow.edit.md"):
        source = wf_dir / filename
        if source.is_file():
            return source.resolve().is_relative_to(_SYSTEM_WORKFLOW_ROOT.resolve())
    return False

_EMPTY_DRAFT_WORKFLOW_JSON: Dict[str, Any] = {
    "start": "",
    "nodes": [],
    "edges": [],
}


def normalize_workflow_group(value: Any) -> str:
    """Validate a workflow's native meta.json group value."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PydanticCustomError("group_type", "group must be a string or null")
    value = value.strip()
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise PydanticCustomError("group_control", "group must not contain control characters")
    if len(value) > 32:
        raise PydanticCustomError("group_length", "group must be at most 32 characters")
    return value


def _markdown_title(markdown_content: Optional[str], fallback: str) -> str:
    if not markdown_content:
        return fallback
    for line in markdown_content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            title = stripped[2:].strip()
            if title:
                return title
    return fallback


def _coerce_localized_names(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    names: Dict[str, str] = {}
    for key, item in value.items():
        locale = str(key).strip()
        name = str(item).strip() if item is not None else ""
        if locale and name:
            names[locale] = name
    return names


def _localized_names_from_mapping(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}

    names: Dict[str, str] = {}
    for key in ("nameI18n", "names", "localizedNames", "displayNames"):
        names.update(_coerce_localized_names(value.get(key)))

    direct_aliases = {
        "zh-CN": ("nameZh", "nameCn", "zhName", "cnName"),
        "en-US": ("nameEn", "enName"),
    }
    for locale, aliases in direct_aliases.items():
        for alias in aliases:
            direct = value.get(alias)
            if isinstance(direct, str) and direct.strip():
                names.setdefault(locale, direct.strip())
                break
    return names


def _workflow_name_i18n(workflow_json: Dict[str, Any], meta: Dict[str, Any]) -> Dict[str, str]:
    metadata = workflow_json.get("metadata")
    names: Dict[str, str] = {}
    for source in (
        workflow_json,
        metadata if isinstance(metadata, dict) else None,
        meta,
    ):
        names.update(_localized_names_from_mapping(source))
    return names


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
    return resolve_workflow_scan_roots(find_workspace_root())


def read_workflow_dir(
    wf_dir: Path,
    workflow_id: str,
    source: str,
) -> Optional[Dict[str, Any]]:
    """Read a single workflow directory and return metadata plus JSON."""
    json_file = wf_dir / "workflow.json"
    md_file = wf_dir / "workflow.md"
    legacy_edit_md_file = wf_dir / "workflow.edit.md"
    has_markdown = md_file.is_file() or legacy_edit_md_file.is_file()
    if not json_file.is_file() and not has_markdown:
        return None

    try:
        if json_file.is_file():
            workflow_json = json.loads(json_file.read_text(encoding="utf-8"))
            json_mtime_ms = int(json_file.stat().st_mtime * 1000)
        else:
            workflow_json = dict(_EMPTY_DRAFT_WORKFLOW_JSON)
            json_mtime_ms = 0

        markdown_content: Optional[str] = None
        updated_candidates = [json_mtime_ms]
        if md_file.is_file():
            markdown_content = md_file.read_text(encoding="utf-8")
            updated_candidates.append(int(md_file.stat().st_mtime * 1000))
        elif legacy_edit_md_file.is_file():
            markdown_content = legacy_edit_md_file.read_text(encoding="utf-8")
            updated_candidates.append(int(legacy_edit_md_file.stat().st_mtime * 1000))

        meta_file = wf_dir / "meta.json"
        fallback_updated = max(updated_candidates)
        meta = {
            "name": workflow_json.get("name") or _markdown_title(markdown_content, workflow_id),
            "description": workflow_json.get("description"),
            "category": workflow_json.get("category", "default"),
            "status": "active" if json_file.is_file() else "draft",
            "createdBy": None,
            "createdAt": fallback_updated,
            "updatedAt": fallback_updated,
        }
        if meta_file.is_file():
            meta.update(json.loads(meta_file.read_text(encoding="utf-8")))
        if "group" in meta:
            meta["group"] = normalize_workflow_group(meta["group"])

        # metadata.group is an import/export transport field, not a second
        # local authority. Local readers always use this workflow's meta.json.
        if isinstance(workflow_json.get("metadata"), dict):
            workflow_json["metadata"].pop("group", None)

        name_i18n = _workflow_name_i18n(workflow_json, meta)
        if name_i18n:
            meta["nameI18n"] = name_i18n

        if meta_file.is_file():
            updated_candidates.append(int(meta_file.stat().st_mtime * 1000))
            updated_candidates.append(int(meta.get("updatedAt") or 0))
        meta = {**meta, "updatedAt": max(updated_candidates)}

        return {
            **meta,
            "id": workflow_id,
            "source": source,
            "group_readonly": is_system_workflow_definition(wf_dir),
            "workflowJson": workflow_json,
            "markdownContent": markdown_content,
            "editMarkdownContent": markdown_content,
        }
    except Exception as exc:
        log.warning(
            "workflow.fs.read.failed",
            {"id": workflow_id, "source": source, "error": str(exc)},
        )
        return None


def patch_workflow_metadata(
    wf_dir: Path, updates: Dict[str, Any], *, workflow_id: Optional[str] = None,
    workflow_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Patch an existing workflow's meta.json without touching JSON or Markdown.

    Also usable by the native installer after replacing a workflow package.
    Unknown metadata is retained, including when the new package has no meta.json.
    The directory must already contain a workflow definition or Markdown draft.
    Installers staging a package can provide its final workflow_id for defaults.
    """
    if not any((wf_dir / name).is_file() for name in ("workflow.json", "workflow.md", "workflow.edit.md")):
        raise FileNotFoundError(f"Workflow not found: {wf_dir}")
    meta_file = wf_dir / "meta.json"
    if meta_file.is_file():
        # Patch the stored mapping, not the derived response: defaults and
        # normalized/localized display names must not replace native values.
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    else:
        data = workflow_data or read_workflow_dir(wf_dir, workflow_id or wf_dir.name, "")
        if data is None:
            raise FileNotFoundError(f"Workflow not found: {wf_dir}")
        meta = {
            key: value
            for key, value in data.items()
            if key not in {"id", "source", "group_readonly", "workflowJson", "markdownContent", "editMarkdownContent", "stats"}
        }
    updates = dict(updates)
    updates.pop("group_readonly", None)
    if "group" in updates:
        updates["group"] = normalize_workflow_group(updates["group"])
        if is_system_workflow_definition(wf_dir) and updates["group"] != normalize_workflow_group(meta.get("group")):
            raise ValueError("System workflow group is read-only")
    meta.update(updates)
    (wf_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return meta


def resolve_workflow_from_fs(workflow_id: str) -> Optional[tuple[Path, Dict[str, Any]]]:
    """Resolve the selected directory and its data in one discovery pass."""
    for root, source in reversed(workflow_scan_dirs()):
        wf_dir = root / workflow_id
        data = read_workflow_dir(wf_dir, workflow_id, source)
        if data is not None:
            return wf_dir, data
    return None


def read_workflow_from_fs(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a workflow by ID from workflow directories on disk."""
    selected = resolve_workflow_from_fs(workflow_id)
    return selected[1] if selected is not None else None


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
        if len(parts) == 2 and parts[1] in {"workflow.json", "workflow.md", "workflow.edit.md"}:
            workflow_id = parts[0]
            if read_workflow_from_fs(workflow_id) is not None:
                return workflow_id
    return None
