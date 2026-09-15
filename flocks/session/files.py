"""Application-owned files and context resources associated with sessions."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
import os
import re
import shutil
import stat as stat_module
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Iterable

from flocks.config.config import Config
from flocks.session.utils.file_extractor import file_url_to_path
from flocks.workspace.manager import WorkspaceManager


_CONTEXT_FOLDERS_METADATA_KEY = "contextFolders"
CONTEXT_PAGE_SIZE = 100
CONTEXT_MAX_PAGE_SIZE = 200
_MAX_OUTPUT_ATTACHMENTS = 32
_UPLOAD_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_INLINE_PREVIEW_MIME_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/svg+xml",
}


@dataclass(frozen=True)
class ResolvedSessionFile:
    """A Session-owned file resolved from a persisted message part."""

    resource_id: str
    path: Path
    filename: str
    mime_type: str
    origin: str
    source_message_id: str
    logical_path: str = ""
    created_at: int | None = None
    recorded_size: int | None = None
    recorded_modified_at: int | None = None


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        return candidate == root or candidate.is_relative_to(root)
    except (OSError, RuntimeError, ValueError):
        return False


def _safe_upload_id(upload_id: str) -> str:
    normalized = str(upload_id or "").strip()
    if not _UPLOAD_ID_PATTERN.fullmatch(normalized):
        raise ValueError("Invalid chat upload ID")
    return normalized


def public_resource_id(message_id: str, part_id: str) -> str:
    """Build an opaque URL-safe ID that supports direct lazy message lookup."""

    payload = json.dumps(
        [str(message_id), str(part_id)],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"res_{encoded}"


def parse_public_resource_id(resource_id: str) -> tuple[str, str]:
    normalized = str(resource_id or "").strip()
    if not normalized.startswith("res_"):
        raise FileNotFoundError("Session file resource not found")
    encoded = normalized[4:]
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise FileNotFoundError("Session file resource not found") from exc
    if (
        not isinstance(decoded, list)
        or len(decoded) != 2
        or not all(isinstance(item, str) and item for item in decoded)
    ):
        raise FileNotFoundError("Session file resource not found")
    return decoded[0], decoded[1]


def _owner_storage_component(owner_id: str) -> str:
    return hashlib.sha256(str(owner_id or "").encode("utf-8")).hexdigest()[:24]


def session_uploads_dir(session_id: str) -> Path:
    """Return the upload directory for one session, constrained to app data."""

    uploads_root = (Config.get_data_path() / "uploads").resolve()
    target = (uploads_root / session_id).resolve()
    if target == uploads_root or not target.is_relative_to(uploads_root):
        raise ValueError(f"Invalid session ID for upload path: {session_id}")
    return target


def chat_upload_staging_root(owner_id: str) -> Path:
    """Return the per-user staging root used before a Session exists."""

    return (
        Config.get_data_path()
        / "uploads"
        / "staging"
        / _owner_storage_component(owner_id)
    ).resolve()


def create_chat_upload_target(owner_id: str, upload_id: str, filename: str) -> Path:
    """Create a unique staging target for a chat document upload."""

    safe_id = _safe_upload_id(upload_id)
    suffix = Path(filename).suffix.lower()
    upload_dir = (chat_upload_staging_root(owner_id) / safe_id).resolve()
    staging_root = chat_upload_staging_root(owner_id)
    if not upload_dir.is_relative_to(staging_root):
        raise ValueError("Invalid chat upload path")
    upload_dir.mkdir(parents=True, exist_ok=False)
    return upload_dir / f"{safe_id}{suffix}"


def resolve_staged_chat_upload(owner_id: str, upload_id: str) -> Path:
    """Resolve one staged upload without accepting a client-provided path."""

    safe_id = _safe_upload_id(upload_id)
    staging_root = chat_upload_staging_root(owner_id)
    upload_dir = (staging_root / safe_id).resolve()
    if not upload_dir.is_relative_to(staging_root) or not upload_dir.is_dir():
        raise FileNotFoundError("Chat upload not found")
    files = [entry for entry in upload_dir.iterdir() if entry.is_file() and not entry.is_symlink()]
    if len(files) != 1:
        raise FileNotFoundError("Chat upload is unavailable")
    target = files[0].resolve(strict=True)
    if not target.is_relative_to(upload_dir):
        raise ValueError("Chat upload escaped its staging directory")
    return target


def remove_staged_chat_upload(owner_id: str, upload_id: str) -> bool:
    """Delete one unbound staged upload owned by the current user."""

    from flocks.session.interaction_queue import InteractionQueue

    safe_id = _safe_upload_id(upload_id)
    if InteractionQueue.references_upload(owner_id, safe_id):
        return False
    staging_root = chat_upload_staging_root(owner_id)
    upload_dir = (staging_root / safe_id).resolve()
    if not upload_dir.is_relative_to(staging_root) or not upload_dir.is_dir():
        return False
    shutil.rmtree(upload_dir)
    return True


def remove_staged_chat_uploads_from_parts(
    owner_id: str,
    parts: Iterable[dict[str, Any]],
) -> int:
    """Delete staged uploads referenced by queued prompt parts."""

    removed = 0
    for part in parts:
        upload_id = str(part.get("uploadID") or "").strip()
        if not upload_id:
            continue
        try:
            if remove_staged_chat_upload(owner_id, upload_id):
                removed += 1
        except ValueError:
            continue
    return removed


def resolve_chat_upload(session_id: str, owner_id: str, upload_id: str) -> Path:
    """Resolve an upload for initial submission or a retry in the same Session."""

    safe_id = _safe_upload_id(upload_id)
    destination_root = session_uploads_dir(session_id)
    existing = [
        entry.resolve(strict=True)
        for entry in destination_root.glob(f"{safe_id}.*")
        if entry.is_file() and not entry.is_symlink()
    ]
    if len(existing) > 1:
        raise ValueError("Chat upload is unavailable")
    if existing:
        if not existing[0].is_relative_to(destination_root):
            raise ValueError("Chat upload escaped its Session directory")
        return existing[0]
    return resolve_staged_chat_upload(owner_id, safe_id)


def bind_staged_chat_upload(session_id: str, owner_id: str, upload_id: str) -> Path:
    """Move a staged chat upload into the existing Session upload directory."""

    source = resolve_chat_upload(session_id, owner_id, upload_id)
    destination_root = session_uploads_dir(session_id)
    if source.parent == destination_root:
        return source
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source.name
    shutil.move(str(source), str(destination))
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return destination.resolve(strict=True)


def restore_chat_upload(source: Path, destination: Path) -> None:
    """Compensate only a new, not-yet-persisted move from this submission."""

    if destination.is_file() and not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(destination), str(source))


def remove_session_uploads(session_id: str) -> bool:
    """Remove one session's upload directory when it exists."""

    target = session_uploads_dir(session_id)
    if not target.is_dir():
        return False
    shutil.rmtree(target)
    return True


def _file_uri_path(url: str) -> Path | None:
    if not str(url or "").startswith("file:"):
        return None
    try:
        raw_path = file_url_to_path(url)
        return Path(raw_path).expanduser().resolve(strict=False) if raw_path else None
    except (OSError, RuntimeError, ValueError):
        return None


def output_file_attachments(tool_name: str, attachments: Any) -> list[dict[str, Any]] | None:
    """Project only lightweight output descriptors into message storage and HTTP."""

    if tool_name != "write" or not isinstance(attachments, list):
        return None
    result = []
    for item in attachments[:_MAX_OUTPUT_ATTACHMENTS]:
        if (
            not isinstance(item, dict)
            or item.get("type") != "file"
            or item.get("origin") != "agent_output"
        ):
            continue
        source = item.get("source")
        if not isinstance(source, dict) or source.get("root") != "workspace-output":
            continue
        relative = source.get("path")
        if (
            not isinstance(relative, str)
            or not relative
            or len(relative) > 4096
            or "\x00" in relative
            or relative.lower().startswith("data:")
        ):
            continue
        if Path(relative).is_absolute() or ".." in Path(relative).parts or not Path(relative).parts:
            continue
        strings = {key: item.get(key) for key in ("id", "filename", "mime")}
        if not all(
            isinstance(value, str)
            and 0 < len(value) <= 512
            and "\x00" not in value
            and not value.lower().startswith("data:")
            for value in strings.values()
        ):
            continue
        safe_source = {"root": "workspace-output", "path": relative}
        if "username" in source:
            username = source["username"]
            if username is not None and (
                not isinstance(username, str)
                or not username
                or len(username) > 256
                or WorkspaceManager.normalize_username_for_path(username) != username
            ):
                continue
            safe_source["username"] = username
        identifiers = {
            key: item[key]
            for key in ("sessionID", "messageID")
            if isinstance(item.get(key), str)
            and 0 < len(item[key]) <= 512
            and not item[key].lower().startswith("data:")
        }
        if "username" in source and len(identifiers) != 2:
            continue
        safe = {
            **strings,
            **identifiers,
            "type": "file",
            "origin": "agent_output",
            "source": safe_source,
        }
        for key in ("size", "modifiedAt"):
            value = item.get(key)
            if type(value) is int and 0 <= value <= 2**63 - 1:
                safe[key] = value
        result.append(safe)
    return result or None


def session_outputs_root(session: Any) -> Path:
    """Return the trusted Workspace outputs root for a Session owner."""

    username = str(getattr(session, "owner_username", "") or "").strip() or None
    return WorkspaceManager.get_instance().get_default_outputs_dir(
        username=username,
        include_today=False,
    ).resolve(strict=False)


def _session_file_part_path(session: Any, part: Any) -> Path | None:
    target = _file_uri_path(str(getattr(part, "url", "") or ""))
    if target is None:
        return None
    upload_root = session_uploads_dir(str(session.id)).resolve(strict=False)
    return target if _is_within(upload_root, target) else None


def _attachment_source_path(
    session: Any,
    attachment: dict[str, Any],
    *,
    message_id: str,
    part: Any,
) -> Path | None:
    source = attachment["source"]
    relative = Path(source["path"])
    try:
        if "username" in source:
            # New descriptors capture the write scope; downloaders cannot select it.
            if attachment.get("sessionID") != session.id or attachment.get("messageID") != message_id:
                return None
            manager = WorkspaceManager.get_instance()
            workspace = manager.get_workspace_dir().resolve(strict=False)
            root = (
                manager.get_user_workspace_dir(source["username"]) / "outputs"
                if source["username"] is not None
                else manager.get_workspace_dir() / "outputs"
            ).resolve(strict=False)
            if not _is_within(workspace, root):
                return None
        else:
            root = session_outputs_root(session)
        target = (root / relative).resolve(strict=False)
        if not _is_within(root, target):
            return None
        metadata = getattr(getattr(part, "state", None), "metadata", None)
        recorded_path = metadata.get("filepath") if isinstance(metadata, dict) else None
        if recorded_path and (
            not isinstance(recorded_path, str)
            or Path(recorded_path).expanduser().resolve(strict=False) != target
        ):
            return None
        return target
    except (OSError, RuntimeError, ValueError):
        return None


def _legacy_output_path(session: Any, part: Any) -> Path | None:
    if str(getattr(part, "tool", "") or "") != "write":
        return None
    state = getattr(part, "state", None)
    if getattr(state, "status", None) != "completed":
        return None
    metadata = getattr(state, "metadata", None)
    if not isinstance(metadata, dict):
        return None
    filepath = str(metadata.get("filepath") or "").strip()
    if not filepath:
        return None
    try:
        target = Path(filepath).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    root = session_outputs_root(session)
    return target if _is_within(root, target) else None


def _message_created_at(message: Any) -> int | None:
    raw_time = getattr(getattr(message, "info", None), "time", None)
    if isinstance(raw_time, dict):
        value = raw_time.get("created")
    else:
        value = getattr(raw_time, "created", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _preview_info(path: Path, mime_type: str, *, exists: bool) -> tuple[bool, bool, str]:
    is_text = exists and WorkspaceManager.is_text_file(path)
    if is_text:
        return True, True, "text"
    if mime_type in _INLINE_PREVIEW_MIME_TYPES:
        return True, False, "inline"
    return False, False, "unsupported"


def _resource_descriptor(
    *,
    resource_id: str,
    path: Path,
    filename: str,
    mime_type: str,
    origin: str,
    section: str,
    source_message_id: str,
    logical_path: str,
    created_at: int | None,
    recorded_size: int | None = None,
    recorded_modified_at: int | None = None,
) -> dict[str, Any]:
    try:
        stat = path.stat()
    except OSError:
        stat = None
    exists = stat is not None and stat_module.S_ISREG(stat.st_mode)
    size: int | None = None
    modified_at: int | None = None
    status = "missing"
    if exists:
        size = stat.st_size
        modified_at = int(stat.st_mtime * 1000)
        changed = (
            (recorded_size is not None and recorded_size != size)
            or (
                recorded_modified_at is not None
                and abs(recorded_modified_at - modified_at) > 1000
            )
        )
        status = "changed" if changed else "ready"
    can_preview, is_text, preview_status = _preview_info(path, mime_type, exists=exists)
    file_key = hashlib.sha256(f"{origin}:{os.path.normcase(str(path))}".encode("utf-8")).hexdigest()
    return {
        "resourceID": resource_id,
        "fileKey": file_key,
        "displayName": filename,
        "mimeType": mime_type,
        "size": size,
        "modifiedAt": modified_at,
        "createdAt": created_at,
        "status": status,
        "previewStatus": preview_status,
        "canPreview": can_preview,
        "isTextFile": is_text,
        "origin": origin,
        "section": section,
        "sourceMessageID": source_message_id,
        "logicalPath": logical_path,
    }


def _iter_completed_attachments(part: Any) -> Iterable[dict[str, Any]]:
    state = getattr(part, "state", None)
    if getattr(state, "status", None) != "completed":
        return []
    return output_file_attachments(
        str(getattr(part, "tool", "") or ""),
        getattr(state, "attachments", None),
    ) or []


def _todo_fallback(parts: Iterable[Any]) -> list[dict[str, Any]] | None:
    latest: list[dict[str, Any]] | None = None
    for part in parts:
        if str(getattr(part, "tool", "") or "") != "todo":
            continue
        state = getattr(part, "state", None)
        if getattr(state, "status", None) != "completed":
            continue
        candidates: list[Any] = []
        metadata = getattr(state, "metadata", None)
        if isinstance(metadata, dict):
            candidates.extend([metadata.get("newTodos"), metadata.get("todos")])
        state_input = getattr(state, "input", None)
        if isinstance(state_input, dict):
            candidates.append(state_input.get("todos"))
        for candidate in candidates:
            if isinstance(candidate, list) and all(isinstance(item, dict) for item in candidate):
                latest = [dict(item) for item in candidate]
                break
    return latest


def _skill_descriptors(parts: Iterable[Any]) -> list[dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for part in parts:
        tool_name = str(getattr(part, "tool", "") or "")
        if tool_name not in {"skill_load", "load_skill"}:
            continue
        state = getattr(part, "state", None)
        state_input = getattr(state, "input", None)
        if not isinstance(state_input, dict):
            continue
        name = str(state_input.get("name") or state_input.get("skill") or "").strip()
        if not name:
            continue
        skills[name] = {
            "name": name,
            "description": None,
            "status": "loaded" if getattr(state, "status", None) == "completed" else "loading",
        }
    return sorted(skills.values(), key=lambda item: item["name"].casefold())


def context_folder_entries_from_metadata(metadata: Any) -> list[dict[str, Any]]:
    """Normalize Context folder entries from Session metadata."""

    raw_items = metadata.get(_CONTEXT_FOLDERS_METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(raw_items, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        root_id = str(item.get("id") or "").strip()
        path = str(item.get("path") or "").strip()
        display_name = str(item.get("displayName") or "").strip()
        if root_id and path:
            entries.append({
                "id": root_id,
                "path": path,
                "displayName": display_name or Path(path).name or path,
                "createdAt": item.get("createdAt"),
            })
    return entries


def context_folder_entries(session: Any) -> list[dict[str, Any]]:
    return context_folder_entries_from_metadata(getattr(session, "metadata", None))


def _assert_safe_context_root(target: Path) -> None:
    if any(part.startswith(".") for part in target.parts if part not in {".", ".."}):
        raise ValueError("Hidden folders cannot be added to Session Context")
    forbidden = [
        Path.home().expanduser().resolve(strict=False) / ".ssh",
        Path(os.getenv("FLOCKS_ROOT", str(Path.home() / ".flocks"))).expanduser().resolve(strict=False),
        Path("/proc"),
        Path("/sys"),
        Path("/dev"),
    ]
    if target == target.parent or any(_is_within(root, target) for root in forbidden):
        raise ValueError("Folder cannot be added to Session Context")


def validate_context_folder(path: str) -> str:
    """Validate a user-selected read-only Context folder."""

    from flocks.project.project import Project

    normalized = Project.validate_worktree(path, create_if_missing=False)
    target = Path(normalized).resolve(strict=True)
    _assert_safe_context_root(target)
    return str(target)


def _session_project_directory(session: Any) -> str | None:
    """Only an active registered binding makes an execution directory a Project root."""

    from flocks.project.project import DEFAULT_PROJECT_ID, TASK_SESSION_GROUP_ID, Project

    project_id = str(getattr(session, "project_id", "") or "").strip()
    if not project_id or project_id in {DEFAULT_PROJECT_ID, TASK_SESSION_GROUP_ID}:
        return None
    # Project and Session owners may differ after an administrator moves a Session.
    if Project.get_owner_user_id(project_id) is None:
        return None
    return str(getattr(session, "directory", "") or "").strip() or None


def resolve_context_root(session: Any, root_id: str) -> tuple[Path, str, str]:
    """Resolve a trusted Project or Session metadata root by opaque ID."""

    if root_id == "project":
        raw_path = _session_project_directory(session)
        if not raw_path:
            raise FileNotFoundError("Project directory is unavailable")
        root = Path(raw_path).expanduser().resolve(strict=True)
        _assert_safe_context_root(root)
        return root, root.name or "Project", "project"

    entry = next(
        (item for item in context_folder_entries(session) if item["id"] == root_id),
        None,
    )
    if entry is None:
        raise FileNotFoundError("Context folder not found")
    root = Path(entry["path"]).expanduser().resolve(strict=True)
    _assert_safe_context_root(root)
    return root, entry["displayName"], "folder"


def _resolve_context_relative_path(root: Path, relative_path: str) -> Path:
    """Resolve a relative path within an already trusted Context root."""

    requested = Path(str(relative_path or ""))
    if requested.is_absolute():
        raise ValueError("Context path must be relative")
    if any(part in {".."} or part.startswith(".") for part in requested.parts):
        raise ValueError("Hidden and parent paths are not available in Session Context")
    target = (root / requested).resolve(strict=False)
    if not _is_within(root, target):
        raise ValueError("Context path escapes its root")
    resolved_relative = target.relative_to(root)
    if any(part.startswith(".") for part in resolved_relative.parts):
        raise ValueError("Hidden paths are not available in Session Context")
    return target


def resolve_context_root_path(session: Any, root_id: str, relative_path: str) -> Path:
    root, _display_name, _kind = resolve_context_root(session, root_id)
    return _resolve_context_relative_path(root, relative_path)


def list_context_root(
    session: Any,
    root_id: str,
    relative_path: str = "",
    *,
    offset: int = 0,
    limit: int = CONTEXT_PAGE_SIZE,
) -> dict[str, Any]:
    root, _display_name, _kind = resolve_context_root(session, root_id)
    base = _resolve_context_relative_path(root, relative_path)
    if not base.is_dir():
        raise FileNotFoundError("Context directory not found")
    if offset < 0 or not 1 <= limit <= CONTEXT_MAX_PAGE_SIZE:
        raise ValueError("Invalid directory page")

    # Offset counts directory entries, not visible rows. No stat/resolve is
    # performed for skipped entries; filtered empty pages still advance.
    with os.scandir(base) as entries:
        candidates = list(islice(entries, offset, offset + limit + 1))
    has_more = len(candidates) > limit
    items = []
    for entry in candidates[:limit]:
        if entry.name.startswith("."):
            continue
        try:
            child = Path(entry.path)
            target = child.resolve(strict=True)
            if not _is_within(root, target) or any(
                part.startswith(".") for part in target.relative_to(root).parts
            ):
                continue
            stat = target.stat()
        except (OSError, RuntimeError):
            continue
        is_dir = stat_module.S_ISDIR(stat.st_mode)
        if not is_dir and not stat_module.S_ISREG(stat.st_mode):
            continue
        items.append({
            "name": entry.name,
            "path": child.relative_to(root).as_posix(),
            "type": "directory" if is_dir else "file",
            "size": None if is_dir else stat.st_size,
            "modifiedAt": int(stat.st_mtime * 1000),
            "isTextFile": not is_dir and WorkspaceManager.is_text_file(target),
        })
    items.sort(key=lambda item: (item["type"] != "directory", item["name"].casefold()))
    return {"items": items, "hasMore": has_more, "nextOffset": offset + limit if has_more else None}


async def _messages_with_parts(
    session_id: str,
    *,
    before: str | None = None,
    limit: int = CONTEXT_PAGE_SIZE,
) -> tuple[list[Any], bool, str | None]:
    from flocks.session.message import Message

    if not 1 <= limit <= CONTEXT_MAX_PAGE_SIZE:
        raise ValueError("Invalid Context page size")
    if before and await Message.get(session_id, before) is None:
        raise ValueError("Invalid Context cursor")
    return await Message.list_recent_with_parts(
        session_id,
        limit=limit,
        before=before,
        include_archived=True,
    )


def _output_logical_path(relative_path: str, username: str | None) -> str:
    """Show an output's path relative to the Workspace root, including its user scope."""

    manager = WorkspaceManager.get_instance()
    workspace = manager.get_workspace_dir()
    username = str(username or "").strip()
    root = manager.get_user_workspace_dir(username) if username else workspace
    return (root / "outputs" / relative_path).relative_to(workspace).as_posix()


def _message_resources(session: Any, message: Any) -> Iterable[ResolvedSessionFile]:
    """Share eligibility and provenance rules between listings and downloads."""

    message_id = str(message.info.id)
    created_at = _message_created_at(message)
    for part in message.parts:
        if part.type == "file" and message.info.role == "user":
            path = _session_file_part_path(session, part)
            if path is not None:
                filename = part.filename or path.name
                yield ResolvedSessionFile(
                    public_resource_id(message_id, str(part.id)), path, filename,
                    part.mime or mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    "user_upload", message_id, f"Uploads/{filename}", created_at,
                )
        elif part.type == "tool" and part.tool == "write" and message.info.role == "assistant":
            attachments = list(_iter_completed_attachments(part))
            for attachment in attachments:
                path = _attachment_source_path(session, attachment, message_id=message_id, part=part)
                if path is None:
                    continue
                filename = attachment["filename"]
                source = attachment["source"]
                logical_path = _output_logical_path(
                    source["path"], source.get("username", getattr(session, "owner_username", None)),
                )
                yield ResolvedSessionFile(
                    public_resource_id(message_id, attachment["id"]), path, filename,
                    attachment["mime"], "agent_output", message_id,
                    logical_path, created_at,
                    attachment.get("size"), attachment.get("modifiedAt"),
                )
            # Legacy filepath is never a fallback for a rejected bound source.
            if not getattr(part.state, "attachments", None):
                path = _legacy_output_path(session, part)
                if path is not None:
                    relative = path.relative_to(session_outputs_root(session)).as_posix()
                    yield ResolvedSessionFile(
                        public_resource_id(message_id, str(part.id)), path, path.name,
                        mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                        "agent_output", message_id,
                        _output_logical_path(relative, getattr(session, "owner_username", None)), created_at,
                    )


def session_resource_metadata(resource: ResolvedSessionFile) -> dict[str, Any]:
    return _resource_descriptor(
        resource_id=resource.resource_id,
        path=resource.path,
        filename=resource.filename,
        mime_type=resource.mime_type,
        origin=resource.origin,
        section="outputs" if resource.origin == "agent_output" else "context",
        source_message_id=resource.source_message_id,
        logical_path=resource.logical_path,
        created_at=resource.created_at,
        recorded_size=resource.recorded_size,
        recorded_modified_at=resource.recorded_modified_at,
    )


async def build_session_context(
    session: Any,
    *,
    include_roots: bool,
    before: str | None = None,
    limit: int = CONTEXT_PAGE_SIZE,
) -> dict[str, Any]:
    """Build one page of Context resources using the existing message cursor."""

    messages, has_more, next_before = await _messages_with_parts(
        str(session.id), before=before, limit=limit,
    )
    resources_by_path: dict[tuple[str, Path], ResolvedSessionFile] = {}
    all_parts: list[Any] = []
    for message in messages:
        all_parts.extend(message.parts)
        for resource in _message_resources(session, message):
            resources_by_path[(resource.origin, resource.path)] = resource
    outputs = []
    context_files = []
    for resource in resources_by_path.values():
        descriptor = session_resource_metadata(resource)
        (outputs if resource.origin == "agent_output" else context_files).append(descriptor)

    from flocks.session.features.todo import Todo

    active_todos = await Todo.get_snapshot(str(session.id))
    progress = (
        [todo.model_dump(exclude_none=True) for todo in active_todos]
        if active_todos is not None
        else _todo_fallback(all_parts)
    )
    progress_known = progress is not None
    progress = progress or []

    roots: list[dict[str, Any]] = []
    if include_roots:
        project_path = _session_project_directory(session)
        if project_path:
            path = Path(project_path).expanduser()
            try:
                _assert_safe_context_root(path.resolve(strict=False))
            except ValueError:
                path = None
            if path is not None:
                status = "available" if path.is_dir() and os.access(path, os.R_OK | os.X_OK) else "missing"
                roots.append({
                    "id": "project",
                    "kind": "project",
                    "displayName": path.name or "Project",
                    "status": status,
                })
        for entry in context_folder_entries(session):
            path = Path(entry["path"]).expanduser()
            try:
                _assert_safe_context_root(path.resolve(strict=False))
            except ValueError:
                continue
            status = "available" if path.is_dir() and os.access(path, os.R_OK | os.X_OK) else "missing"
            roots.append({
                "id": entry["id"],
                "kind": "folder",
                "displayName": entry["displayName"],
                "status": status,
            })

    outputs.sort(
        key=lambda item: (item.get("modifiedAt") or item.get("createdAt") or 0),
        reverse=True,
    )
    context_files.sort(
        key=lambda item: (item.get("modifiedAt") or item.get("createdAt") or 0),
        reverse=True,
    )
    skills = _skill_descriptors(all_parts)
    count = len(outputs) + len(context_files) + len(roots) + (1 if progress else 0)
    return {
        "sessionID": str(session.id),
        "messageIDs": [str(message.info.id) for message in messages],
        "canManageFolders": include_roots,
        "hasMore": has_more,
        "nextBefore": next_before,
        "outputs": outputs,
        "contextFiles": context_files,
        "progress": progress,
        "progressKnown": progress_known,
        "roots": roots,
        "skills": skills,
        "counts": {
            "total": count,
            "outputs": len(outputs),
            "contextFiles": len(context_files),
            "roots": len(roots),
            "progress": 1 if progress else 0,
        },
    }


async def resolve_session_resource(session: Any, resource_id: str) -> ResolvedSessionFile:
    """Resolve a resource ID from this Session's persisted message parts."""

    from flocks.session.message import Message

    message_id, part_id = parse_public_resource_id(resource_id)
    message = await Message.get_with_parts_lazy(str(session.id), message_id)
    if message is None:
        raise FileNotFoundError("Session file resource not found")

    for resource in _message_resources(session, message):
        if parse_public_resource_id(resource.resource_id)[1] == part_id:
            return resource
    raise FileNotFoundError("Session file resource not found")
