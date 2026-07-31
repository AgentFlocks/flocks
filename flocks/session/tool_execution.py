"""Core helpers for unified session tool-execution payloads."""

from __future__ import annotations

import os
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
    operation = _FILESYSTEM_TOOL_OPERATION_BY_NAME.get(str(tool_name or "").strip().lower())
    if operation is None:
        return None
    workspace_dir = str((profile.get("workspace_dir") or os.getcwd())).strip() or os.getcwd()
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
    workspace_root = str(WorkspaceManager.get_instance().get_workspace_dir())
    owner_username = _first_present(profile, ("owner_username",))
    output_root = str(
        WorkspaceManager.get_instance().get_default_outputs_dir(
            username=owner_username,
            include_today=False,
        )
    )
    flocks_root = os.path.expanduser(os.getenv("FLOCKS_ROOT", "~/.flocks"))
    plugins_root = os.path.join(flocks_root, "plugins")
    project_root = str(profile.get("project_root") or workspace_dir)
    project_id = _first_present(profile, ("project_id",))
    project_revision_raw = profile.get("project_revision")
    try:
        project_revision = int(project_revision_raw) if project_revision_raw is not None else None
    except Exception:
        project_revision = None
    code_roots = [workspace_dir]
    execution_context = (
        dict(tool_context_extra.get("execution_context"))
        if isinstance(tool_context_extra.get("execution_context"), Mapping)
        else {}
    )
    trace_id = _first_present(execution_context, ("trace_id", "traceId")) or message_id
    execution_id = _first_present(execution_context, ("execution_id", "executionId")) or message_id
    return {
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
            "root": project_root or None,
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
        "code_roots": code_roots,
        "flocks_root": flocks_root,
        "plugins_root": plugins_root,
    }


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
