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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse

from flocks.config.config import Config
from flocks.workspace.manager import WorkspaceManager


_CONTEXT_FOLDERS_METADATA_KEY = "contextFolders"
_CONTEXT_MESSAGE_LIMIT = 1_000
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

    safe_id = _safe_upload_id(upload_id)
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


def bind_staged_chat_upload(session_id: str, owner_id: str, upload_id: str) -> Path:
    """Move a staged chat upload into the existing Session upload directory."""

    safe_id = _safe_upload_id(upload_id)
    destination_root = session_uploads_dir(session_id)
    destination_root.mkdir(parents=True, exist_ok=True)

    existing = [
        entry.resolve(strict=True)
        for entry in destination_root.glob(f"{safe_id}.*")
        if entry.is_file() and not entry.is_symlink()
    ]
    if existing:
        return existing[0]

    source = resolve_staged_chat_upload(owner_id, safe_id)
    destination = destination_root / source.name
    shutil.move(str(source), str(destination))
    try:
        source.parent.rmdir()
    except OSError:
        pass
    return destination.resolve(strict=True)


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
    parsed = urlparse(url)
    raw_path = unquote(parsed.path or "")
    if os.name == "nt" and re.match(r"^/[A-Za-z]:/", raw_path):
        raw_path = raw_path[1:]
    if parsed.netloc and parsed.netloc not in {"", "localhost"}:
        raw_path = f"//{parsed.netloc}{raw_path}"
    if not raw_path:
        return None
    try:
        return Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return None


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


def _attachment_source_path(session: Any, attachment: dict[str, Any]) -> Path | None:
    source = attachment.get("source")
    if not isinstance(source, dict) or source.get("root") != "workspace-output":
        return None
    relative = Path(str(source.get("path") or ""))
    if not str(relative) or relative.is_absolute():
        return None
    root = session_outputs_root(session)
    target = (root / relative).resolve(strict=False)
    return target if _is_within(root, target) else None


def _legacy_output_path(session: Any, part: Any) -> Path | None:
    if str(getattr(part, "tool", "") or "") != "write":
        return None
    state = getattr(part, "state", None)
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


def _preview_info(path: Path, mime_type: str) -> tuple[bool, bool, str]:
    if path.exists() and path.is_file():
        is_text = WorkspaceManager.is_text_file(path)
    else:
        is_text = False
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
    exists = path.exists() and path.is_file()
    size: int | None = None
    modified_at: int | None = None
    status = "missing"
    if exists:
        stat = path.stat()
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
    can_preview, is_text, preview_status = _preview_info(path, mime_type)
    return {
        "resourceID": resource_id,
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
    attachments = getattr(state, "attachments", None)
    if not isinstance(attachments, list):
        return []
    return [item for item in attachments if isinstance(item, dict)]


def _todo_fallback(parts: Iterable[Any]) -> list[dict[str, Any]]:
    latest: list[dict[str, Any]] = []
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


def resolve_context_root(session: Any, root_id: str) -> tuple[Path, str, str]:
    """Resolve a trusted Project or Session metadata root by opaque ID."""

    if root_id == "project":
        raw_path = str(getattr(session, "directory", "") or "").strip()
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


def resolve_context_root_path(session: Any, root_id: str, relative_path: str) -> Path:
    root, _display_name, _kind = resolve_context_root(session, root_id)
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


def list_context_root(session: Any, root_id: str, relative_path: str = "") -> list[dict[str, Any]]:
    base = resolve_context_root_path(session, root_id, relative_path)
    root, _display_name, _kind = resolve_context_root(session, root_id)
    if not base.exists():
        raise FileNotFoundError("Context path not found")
    if not base.is_dir():
        raise ValueError("Context path is not a directory")

    items: list[dict[str, Any]] = []
    for child in sorted(
        (item for item in base.iterdir() if not item.name.startswith(".")),
        key=lambda item: (not item.is_dir(), item.name.casefold()),
    ):
        try:
            target = child.resolve(strict=True)
            if not _is_within(root, target):
                continue
            stat = target.stat()
        except (OSError, RuntimeError):
            continue
        relative = target.relative_to(root).as_posix()
        is_dir = target.is_dir()
        items.append({
            "name": target.name,
            "path": relative,
            "type": "directory" if is_dir else "file",
            "size": None if is_dir else stat.st_size,
            "modifiedAt": int(stat.st_mtime * 1000),
            "isTextFile": False if is_dir else WorkspaceManager.is_text_file(target),
        })
    return items


async def _messages_with_parts(session_id: str) -> tuple[list[Any], bool]:
    from flocks.session.message import Message

    items, has_more, _next_before = await Message.list_recent_with_parts(
        session_id,
        limit=_CONTEXT_MESSAGE_LIMIT,
        include_archived=True,
    )
    return items, has_more


async def build_session_context(session: Any, *, include_roots: bool) -> dict[str, Any]:
    """Build the Session Context snapshot from existing persisted state."""

    messages, history_truncated = await _messages_with_parts(str(session.id))
    outputs_by_path: dict[str, dict[str, Any]] = {}
    context_files: list[dict[str, Any]] = []
    all_parts: list[Any] = []

    for message in messages:
        message_id = str(getattr(message.info, "id", "") or "")
        created_at = _message_created_at(message)
        role_value = getattr(message.info, "role", "")
        role = getattr(role_value, "value", role_value)
        for part in message.parts:
            all_parts.append(part)
            if getattr(part, "type", None) == "file" and role == "user":
                path = _session_file_part_path(session, part)
                if path is None:
                    continue
                filename = str(getattr(part, "filename", "") or path.name)
                mime_type = str(
                    getattr(part, "mime", "")
                    or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                )
                context_files.append(_resource_descriptor(
                    resource_id=public_resource_id(message_id, str(part.id)),
                    path=path,
                    filename=filename,
                    mime_type=mime_type,
                    origin="user_upload",
                    section="context",
                    source_message_id=message_id,
                    logical_path=f"Uploads/{filename}",
                    created_at=created_at,
                ))
                continue

            if getattr(part, "type", None) != "tool" or str(getattr(part, "tool", "") or "") != "write":
                continue

            found_attachment = False
            for attachment in _iter_completed_attachments(part):
                if attachment.get("origin") != "agent_output":
                    continue
                path = _attachment_source_path(session, attachment)
                if path is None:
                    continue
                source = attachment.get("source") or {}
                relative = str(source.get("path") or path.name)
                resource = _resource_descriptor(
                    resource_id=public_resource_id(
                        message_id,
                        str(attachment.get("id") or part.id),
                    ),
                    path=path,
                    filename=str(attachment.get("filename") or path.name),
                    mime_type=str(
                        attachment.get("mime")
                        or mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream"
                    ),
                    origin="agent_output",
                    section="outputs",
                    source_message_id=message_id,
                    logical_path=f"Outputs/{relative}",
                    created_at=created_at,
                    recorded_size=attachment.get("size") if isinstance(attachment.get("size"), int) else None,
                    recorded_modified_at=(
                        attachment.get("modifiedAt")
                        if isinstance(attachment.get("modifiedAt"), int)
                        else None
                    ),
                )
                outputs_by_path[str(path)] = resource
                found_attachment = True

            if found_attachment or getattr(getattr(part, "state", None), "status", None) != "completed":
                continue
            path = _legacy_output_path(session, part)
            if path is None:
                continue
            try:
                relative = path.relative_to(session_outputs_root(session)).as_posix()
            except ValueError:
                continue
            outputs_by_path[str(path)] = _resource_descriptor(
                resource_id=public_resource_id(message_id, str(part.id)),
                path=path,
                filename=path.name,
                mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                origin="agent_output",
                section="outputs",
                source_message_id=message_id,
                logical_path=f"Outputs/{relative}",
                created_at=created_at,
            )

    from flocks.session.features.todo import Todo

    active_todos = await Todo.get(str(session.id))
    progress = [todo.model_dump(exclude_none=True) for todo in active_todos]
    if not progress:
        progress = _todo_fallback(all_parts)

    roots: list[dict[str, Any]] = []
    if include_roots:
        project_path = str(getattr(session, "directory", "") or "").strip()
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

    outputs = sorted(
        outputs_by_path.values(),
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
        "canManageFolders": include_roots,
        "historyTruncated": history_truncated,
        "outputs": outputs,
        "contextFiles": context_files,
        "progress": progress,
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

    role_value = getattr(message.info, "role", "")
    role = getattr(role_value, "value", role_value)
    for part in message.parts:
        if getattr(part, "type", None) == "file" and role == "user" and str(part.id) == part_id:
            path = _session_file_part_path(session, part)
            if path is None:
                break
            filename = str(getattr(part, "filename", "") or path.name)
            return ResolvedSessionFile(
                resource_id=resource_id,
                path=path,
                filename=filename,
                mime_type=str(
                    getattr(part, "mime", "")
                    or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                ),
                origin="user_upload",
                source_message_id=message_id,
            )

        if getattr(part, "type", None) != "tool" or str(getattr(part, "tool", "") or "") != "write":
            continue
        for attachment in _iter_completed_attachments(part):
            if str(attachment.get("id") or "") != part_id:
                continue
            path = _attachment_source_path(session, attachment)
            if path is None:
                break
            filename = str(attachment.get("filename") or path.name)
            return ResolvedSessionFile(
                resource_id=resource_id,
                path=path,
                filename=filename,
                mime_type=str(
                    attachment.get("mime")
                    or mimetypes.guess_type(filename)[0]
                    or "application/octet-stream"
                ),
                origin="agent_output",
                source_message_id=message_id,
            )
        if str(part.id) == part_id:
            path = _legacy_output_path(session, part)
            if path is not None:
                return ResolvedSessionFile(
                    resource_id=resource_id,
                    path=path,
                    filename=path.name,
                    mime_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                    origin="agent_output",
                    source_message_id=message_id,
                )
    raise FileNotFoundError("Session file resource not found")
