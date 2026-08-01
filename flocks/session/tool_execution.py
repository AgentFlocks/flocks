"""Core helpers for unified session tool-execution payloads."""

from __future__ import annotations

import os
import re
from typing import Any, Mapping

from flocks.session.execution_profile import get_session_execution_profile
from flocks.workspace.manager import WorkspaceManager

_FILESYSTEM_TOOL_OPERATION_BY_NAME: dict[str, str] = {
    "read": "read",
    "glob": "search",
    "grep": "search",
    "doc_parser": "edit",
    "write": "write",
    "edit": "edit",
    "apply_patch": "apply_patch",
    "delete": "delete",
    "move": "move",
    "copy": "copy",
    "mkdir": "mkdir",
}


def _normalize_operation_path(path: str | None, *, cwd: str) -> str | None:
    text = str(path or "").strip()
    if not text:
        return None
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        return os.path.realpath(expanded)
    return os.path.realpath(os.path.join(cwd, expanded))


def _extract_apply_patch_action(
    *,
    patch_text: str,
    cwd: str,
) -> dict[str, str | None] | None:
    action: dict[str, str | None] | None = None
    update_pattern = re.compile(r"^\*\*\* Update File:\s*(.+)$")
    add_pattern = re.compile(r"^\*\*\* Add File:\s*(.+)$")
    delete_pattern = re.compile(r"^\*\*\* Delete File:\s*(.+)$")
    for raw_line in patch_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        update_match = update_pattern.match(line)
        if update_match:
            if action is not None:
                return None
            payload = update_match.group(1).strip()
            if " -> " in payload:
                source_raw, target_raw = [part.strip() for part in payload.split(" -> ", 1)]
                action = {
                    "operation": "move",
                    "source_path": _normalize_operation_path(source_raw, cwd=cwd),
                    "target_path": _normalize_operation_path(target_raw, cwd=cwd),
                }
            else:
                action = {
                    "operation": "edit",
                    "source_path": None,
                    "target_path": _normalize_operation_path(payload, cwd=cwd),
                }
            continue
        add_match = add_pattern.match(line)
        if add_match:
            if action is not None:
                return None
            target_raw = add_match.group(1).strip()
            action = {
                "operation": "write",
                "source_path": None,
                "target_path": _normalize_operation_path(target_raw, cwd=cwd),
            }
            continue
        delete_match = delete_pattern.match(line)
        if delete_match:
            if action is not None:
                return None
            target_raw = delete_match.group(1).strip()
            action = {
                "operation": "delete",
                "source_path": None,
                "target_path": _normalize_operation_path(target_raw, cwd=cwd),
            }
    return action


def _first_present(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _filesystem_action_payload(
    *,
    session_id: str,
    message_id: str,
    agent: str,
    tool_name: str,
    tool_input: Mapping[str, Any],
    profile: Mapping[str, Any],
    tool_context_extra: Mapping[str, Any],
) -> dict[str, Any] | None:
    if tool_context_extra.get("agent_execution_session") is not True:
        return None
    operation = _FILESYSTEM_TOOL_OPERATION_BY_NAME.get(str(tool_name or "").strip().lower())
    if operation is None:
        return None
    sandbox = tool_context_extra.get("sandbox")
    sandbox_workspace_dir = (
        str(sandbox.get("workspace_dir") or "").strip()
        if isinstance(sandbox, Mapping)
        else ""
    )
    workspace_dir = (
        sandbox_workspace_dir
        or str((profile.get("workspace_dir") or os.getcwd())).strip()
        or os.getcwd()
    )
    permission_mode = str(profile.get("permission_mode") or "readonly").strip().lower()
    runtime_mode = str(profile.get("runtime_mode") or "exe-mode").strip().lower()
    source_path = _first_present(
        tool_input,
        ("sourcePath", "source_path", "from", "src", "oldPath", "input_path", "inputPath"),
    )
    target_path = _first_present(
        tool_input,
        ("targetPath", "target_path", "to", "dst", "filePath", "path", "newPath", "output_path", "outputPath"),
    )
    if operation in {"read", "search", "write", "edit", "apply_patch", "delete", "mkdir"} and target_path is None:
        target_path = _first_present(tool_input, ("filePath", "path", "dirPath"))
    source_path = _normalize_operation_path(source_path, cwd=workspace_dir)
    target_path = _normalize_operation_path(target_path, cwd=workspace_dir)
    workspace_root = workspace_dir
    owner_username = _first_present(profile, ("owner_username",))
    output_root = str(
        WorkspaceManager.get_instance().get_default_outputs_dir(
            username=owner_username,
            include_today=False,
        )
    )
    flocks_root = os.path.expanduser(os.getenv("FLOCKS_ROOT", "~/.flocks"))
    plugins_root = os.path.join(flocks_root, "plugins")
    project_root = str(profile.get("project_root") or "").strip() or None
    project_id = _first_present(profile, ("project_id",))
    project_revision_raw = profile.get("project_revision")
    project_revision = None
    if project_revision_raw is not None:
        text_revision = str(project_revision_raw).strip()
        if text_revision:
            project_revision = text_revision
    execution_context = (
        dict(tool_context_extra.get("execution_context"))
        if isinstance(tool_context_extra.get("execution_context"), Mapping)
        else {}
    )
    trace_id = _first_present(execution_context, ("trace_id", "traceId")) or message_id
    execution_id = _first_present(execution_context, ("execution_id", "executionId")) or message_id
    payload: dict[str, Any] = {
        "trace_id": trace_id,
        "execution_id": execution_id,
        "session_id": session_id,
        "agent_execution_session": True,
        "workflow_execution": _first_present(profile, ("entry",)) == "workflow",
        "runtime_mode": runtime_mode,
        "operation": operation,
        "source_path": source_path,
        "target_path": target_path,
        "cwd": workspace_dir,
        "session_permission_mode": permission_mode,
        "owner_username": owner_username,
        "workspace": {
            "root": workspace_root,
            "output_owner_id": owner_username,
            "output_root": output_root,
        },
        "project": {
            "id": project_id,
            "root": project_root,
            "revision": project_revision,
        },
        "subject": {
            "id": _first_present(profile, ("subject_id",)),
            "type": _first_present(profile, ("subject_type",)),
            "role": _first_present(profile, ("actor_role",)),
        },
        "agent": {"id": agent},
        "flocks": {"root": flocks_root, "plugins_root": plugins_root},
        "workspace_root": workspace_root,
        "output_root": output_root,
        "project_root": project_root,
        "flocks_root": flocks_root,
        "plugins_root": plugins_root,
    }
    if operation == "apply_patch":
        patch_text = str(tool_input.get("patchText") or tool_input.get("patch_text") or "")
        payload["apply_patch_action"] = _extract_apply_patch_action(
            patch_text=patch_text,
            cwd=workspace_dir,
        )
    return payload


async def build_session_tool_execution_payload(
    *,
    session_id: str,
    message_id: str,
    agent: str,
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
    tool_context_extra: Mapping[str, Any] | None = None,
    execution_domain: str = "execution_runtime",
) -> dict[str, Any]:
    """Build one canonical payload used by all tool execution entrypoints."""
    extra = dict(tool_context_extra or {})
    if not isinstance(extra.get("session_execution_profile"), dict):
        profile = await get_session_execution_profile(session_id)
        if isinstance(profile, dict):
            extra["session_execution_profile"] = profile
    profile = (
        dict(extra.get("session_execution_profile"))
        if isinstance(extra.get("session_execution_profile"), Mapping)
        else {}
    )
    filesystem_action = _filesystem_action_payload(
        session_id=session_id,
        message_id=message_id,
        agent=agent,
        tool_name=tool_name,
        tool_input=tool_input or {},
        profile=profile,
        tool_context_extra=extra,
    )
    if filesystem_action is not None:
        extra["filesystem_action"] = filesystem_action
    return {
        "operation": "tool.execute",
        "execution_domain": str(execution_domain or "execution_runtime"),
        "entry": str(
            (
                (extra.get("session_execution_profile") or {}).get("entry")
                if isinstance(extra.get("session_execution_profile"), Mapping)
                else ""
            )
            or "unknown"
        ),
        "tool": {
            "name": tool_name,
            "input": dict(tool_input or {}),
        },
        "session_id": session_id,
        "message_id": message_id,
        "agent": agent,
        "tool_context_extra": extra,
    }
