"""Shared filesystem-backed workflow lookup helpers."""

from __future__ import annotations

import json
import os
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


def is_safe_workflow_id(workflow_id: Any) -> bool:
    """Return True when *workflow_id* can only address ``<root>/<id>/``.

    Workflow ids are used verbatim as directory names under the scan roots, so
    path separators, ``.``/``..`` and NUL bytes must never reach the
    filesystem (``<root>/..`` would resolve to the parent directory).
    """
    if not isinstance(workflow_id, str):
        return False
    value = workflow_id.strip()
    if not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value or "\x00" in value or os.sep in value:
        return False
    return True


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


#: ``source`` of a workflow a deployment content source serves (flocks.content.managed).
MANAGED_WORKFLOW_SOURCE = "managed"

# Keys of read_workflow_dir()'s result that are derived, never taken from a
# managed definition's metadata.
_DERIVED_KEYS = frozenset({
    "id", "source", "group_readonly", "managed", "version",
    "workflowJson", "markdownContent", "editMarkdownContent", "stats",
})


class ManagedWorkflowReadOnly(PermissionError):
    """A write was aimed at a workflow the deployment manages."""


def is_managed_workflow(workflow_id: str) -> bool:
    """Whether the deployment manages *workflow_id* (usable definition or not)."""
    from flocks.content.managed import managed_ids

    return workflow_id in managed_ids("workflow")


def refuse_managed_workflow_write(workflow_id: str) -> None:
    """Raise :class:`ManagedWorkflowReadOnly` for a managed *workflow_id*.

    Every write of a definition goes through here: writing one would put the
    deployment's definition on disk and leave a local copy under its id.
    """
    if is_managed_workflow(workflow_id):
        raise ManagedWorkflowReadOnly(
            f"Workflow {workflow_id} is managed by the deployment and is read-only"
        )


def managed_workflow_id_for_path(path: Any) -> Optional[str]:
    """The managed workflow a file path names, if it names one.

    A file inside ``<workflow root>/<id>/`` whose id the deployment manages is
    a local copy of that workflow; whoever hands its path over means the
    managed workflow, not the copy.
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, TypeError, ValueError):
        return None
    for root, _source in workflow_scan_dirs():
        try:
            relative = resolved.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        parts = relative.parts
        if parts and is_safe_workflow_id(parts[0]) and is_managed_workflow(parts[0]):
            return parts[0]
    return None


def managed_workflow_data(workflow_id: str) -> Optional[Dict[str, Any]]:
    """A managed workflow in read_workflow_dir()'s shape, or None when unusable.

    The definition is the workflow JSON as YAML or JSON, or an envelope
    ``{workflowJson, markdownContent, meta}``.  It is read-only: there is no
    directory behind it.
    """
    import yaml

    from flocks.content.managed import ManagedContent, managed_entry

    # A run keeps the version it first read (flocks.content.managed.task_scope).
    body = ManagedContent.task_body("workflow", workflow_id)
    if body is None:
        return None
    try:
        raw = yaml.safe_load(body.as_text())
        if not isinstance(raw, dict):
            raise ValueError("the definition is not a mapping")
        if "workflowJson" in raw:
            workflow_json, markdown, meta = raw.get("workflowJson"), raw.get("markdownContent"), raw.get("meta") or {}
        else:
            workflow_json, markdown, meta = raw, None, {}
        if not isinstance(workflow_json, dict) or not isinstance(meta, dict):
            raise ValueError("workflowJson and meta must be mappings")
        if markdown is not None and not isinstance(markdown, str):
            raise ValueError("markdownContent must be text")
    except Exception as exc:
        from flocks.content.managed import describe_parse_error

        # Never the message: a YAML error quotes the protected definition.
        log.error("workflow.managed.invalid", {"id": workflow_id, "source": body.source, **describe_parse_error(exc)})
        return None

    workflow_json = dict(workflow_json)
    if isinstance(workflow_json.get("metadata"), dict):
        workflow_json["metadata"] = {k: v for k, v in workflow_json["metadata"].items() if k != "group"}
    entry = managed_entry("workflow", workflow_id)
    data: Dict[str, Any] = {
        "name": workflow_json.get("name") or (entry.name if entry else "") or _markdown_title(markdown, workflow_id),
        "description": workflow_json.get("description") or (entry.summary if entry else None),
        "category": workflow_json.get("category", "default"),
        "status": "active",
        "createdBy": None,
        "createdAt": 0,
        "updatedAt": 0,
    }
    data.update({k: v for k, v in meta.items() if k not in _DERIVED_KEYS})
    if "group" in data:
        try:
            data["group"] = normalize_workflow_group(data["group"])
        except (ValueError, PydanticCustomError):
            data.pop("group", None)
    name_i18n = _workflow_name_i18n(workflow_json, data)
    if name_i18n:
        data["nameI18n"] = name_i18n
    return {
        **data,
        "id": workflow_id,
        "source": MANAGED_WORKFLOW_SOURCE,
        "group_readonly": False,
        "managed": True,
        "version": body.ref.version,
        "workflowJson": workflow_json,
        "markdownContent": markdown,
        "editMarkdownContent": markdown,
    }


def overlay_managed_workflows(by_id: Dict[str, Dict[str, Any]]) -> None:
    """Replace local workflows in a listing with the managed ones of the same id.

    A managed id never lists its local copy -- even when the managed definition
    is unusable, so a dropped-in directory cannot stand in for it.
    """
    from flocks.content.managed import managed_ids

    for workflow_id in managed_ids("workflow"):
        by_id.pop(workflow_id, None)
        data = managed_workflow_data(workflow_id)
        if data is not None:
            by_id[workflow_id] = data


def resolve_workflow_from_fs(workflow_id: str) -> Optional[tuple[Path, Dict[str, Any]]]:
    """Resolve the selected directory and its data in one discovery pass.

    A managed workflow resolves to nothing: it has no directory a writer
    could use, and no local directory may answer for its id.  Readers use
    :func:`read_workflow_from_fs`, which serves the managed definition.
    """
    if not is_safe_workflow_id(workflow_id):
        return None
    if is_managed_workflow(workflow_id):
        return None
    for root, source in reversed(workflow_scan_dirs()):
        wf_dir = root / workflow_id
        data = read_workflow_dir(wf_dir, workflow_id, source)
        if data is not None:
            return wf_dir, data
    return None


def read_workflow_from_fs(workflow_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a workflow by ID: the managed definition, else workflow directories on disk."""
    if is_safe_workflow_id(workflow_id) and is_managed_workflow(workflow_id):
        return managed_workflow_data(workflow_id)
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
