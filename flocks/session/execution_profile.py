"""Session execution-profile helpers.

Profiles are persisted inside ``SessionInfo.metadata`` under a stable key so
all execution entrypoints can read one canonical, trusted envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping

from flocks.workspace.manager import WorkspaceManager

if TYPE_CHECKING:
    from flocks.session.session import SessionInfo

PROFILE_METADATA_KEY = "sessionExecutionProfile"
PROFILE_VERSION = "v1"

_PERMISSION_MODES = {"readonly", "require-confirm", "auto-allow-all"}
_RUNTIME_MODES = {"dev-mode", "exe-mode"}
_NETWORK_MODES = {"auto-deny-all", "require-confirm", "auto-allow-all"}
_ENTRY_ALIASES = {
    "channel_webhook": "channel",
    "workflow_service": "workflow",
    "workflow_trigger": "workflow",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def normalize_entry(value: Any, *, default: str = "interactive") -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    return _ENTRY_ALIASES.get(normalized, normalized)


def _normalize_entry(value: Any) -> str:
    return normalize_entry(value, default="interactive")


def _workspace_root_dir() -> str:
    try:
        return str(WorkspaceManager.get_instance().get_workspace_dir())
    except Exception:
        return str((Path.home() / ".flocks" / "workspace").resolve())


def _default_permission_mode(entry: str) -> str:
    normalized = _normalize_entry(entry)
    if normalized in {"workflow", "api", "schedule", "task"}:
        return "auto-allow-all"
    if normalized == "channel":
        return "readonly"
    if normalized in {"webui", "cli", "tui", "interactive", "delegate"}:
        return "require-confirm"
    return "auto-allow-all"


def _default_runtime_mode(entry: str) -> str:
    normalized = _normalize_entry(entry)
    if normalized in {"webui", "cli", "tui", "interactive"}:
        return "dev-mode"
    return "exe-mode"


def default_network_mode(entry: str | None) -> str:
    normalized = _normalize_entry(entry)
    if normalized in {"webui", "interactive", "cli", "tui"}:
        return "require-confirm"
    if normalized in {
        "channel",
        "workflow",
        "api",
        "schedule",
        "task",
        "session.shell",
        "http_control_plane",
    }:
        return "auto-deny-all"
    if normalized == "unknown":
        return "require-confirm"
    if normalized == "delegate":
        # Delegate sessions inherit their mode from the parent in Pro.
        return "require-confirm"
    return "require-confirm"


def _normalize_permission_mode(value: Any, *, entry: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in _PERMISSION_MODES:
        return mode
    return _default_permission_mode(entry)


def _normalize_runtime_mode(value: Any, *, entry: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in _RUNTIME_MODES:
        return mode
    return _default_runtime_mode(entry)


def _normalize_network_mode(value: Any, *, entry: str) -> str:
    mode = str(value or "").strip().lower()
    if mode in _NETWORK_MODES:
        return mode
    return default_network_mode(entry)


def default_execution_profile(
    *,
    session: "SessionInfo",
    entry: str = "interactive",
    visible_agents: list[str] | None = None,
    default_agent: str | None = None,
    actor_role: str | None = None,
    actor_department: str | None = None,
    source: str = "session.create",
) -> dict[str, Any]:
    normalized_entry = _normalize_entry(entry)
    visible = _as_list(visible_agents)
    default_agent_name = str(default_agent or "").strip() or str(
        getattr(session, "agent", "") or ""
    ).strip()
    if visible and default_agent_name and default_agent_name not in visible:
        default_agent_name = visible[0]
    project_root = str(getattr(session, "directory", "") or "").strip()
    workspace_root = _workspace_root_dir()
    return {
        "version": PROFILE_VERSION,
        "session_id": str(session.id),
        "project_id": str(session.project_id),
        "workspace_dir": workspace_root,
        "project_root": project_root,
        "project_revision": getattr(
            getattr(session, "time", None),
            "updated",
            None,
        ),
        "owner_user_id": str(getattr(session, "owner_user_id", "") or "").strip() or None,
        "owner_username": str(getattr(session, "owner_username", "") or "").strip() or None,
        "entry": normalized_entry,
        "visible_agents": visible,
        "default_agent": default_agent_name,
        "permission_mode": _default_permission_mode(normalized_entry),
        "runtime_mode": _default_runtime_mode(normalized_entry),
        "network_mode": default_network_mode(normalized_entry),
        "actor_role": str(actor_role or "").strip() or None,
        "actor_department": str(actor_department or "").strip() or None,
        "revision": 1,
        "source": str(source or "session.create"),
        "updated_at": _now_iso(),
    }


def profile_from_session(session: "SessionInfo") -> dict[str, Any]:
    metadata = dict(getattr(session, "metadata", {}) or {})
    raw_profile = (
        metadata.get(PROFILE_METADATA_KEY)
        if isinstance(metadata.get(PROFILE_METADATA_KEY), Mapping)
        else {}
    )
    profile = dict(raw_profile)
    if not profile:
        profile = default_execution_profile(session=session)
    profile.setdefault("version", PROFILE_VERSION)
    profile["session_id"] = str(session.id)
    profile["project_id"] = str(session.project_id)
    project_root = str(getattr(session, "directory", "") or "").strip()
    workspace_root = _workspace_root_dir()
    profile["workspace_dir"] = workspace_root
    profile["project_root"] = project_root
    profile["project_revision"] = getattr(
        getattr(session, "time", None),
        "updated",
        None,
    )
    profile["owner_user_id"] = str(
        getattr(session, "owner_user_id", "") or ""
    ).strip() or None
    profile["owner_username"] = str(getattr(session, "owner_username", "") or "").strip() or None
    profile["visible_agents"] = _as_list(profile.get("visible_agents"))
    profile["default_agent"] = str(
        profile.get("default_agent") or getattr(session, "agent", "") or ""
    ).strip()
    if profile["visible_agents"] and profile["default_agent"] not in profile["visible_agents"]:
        profile["default_agent"] = profile["visible_agents"][0]
    profile["entry"] = _normalize_entry(profile.get("entry"))
    profile["permission_mode"] = _normalize_permission_mode(
        profile.get("permission_mode"),
        entry=profile["entry"],
    )
    profile["runtime_mode"] = _normalize_runtime_mode(
        profile.get("runtime_mode"),
        entry=profile["entry"],
    )
    profile["network_mode"] = _normalize_network_mode(
        profile.get("network_mode"),
        entry=profile["entry"],
    )
    profile["revision"] = int(profile.get("revision") or 1)
    profile["source"] = str(profile.get("source") or "session.create")
    profile.setdefault("updated_at", _now_iso())
    return profile


def merge_profile(
    session: "SessionInfo",
    *,
    patch: Mapping[str, Any],
    source: str,
) -> dict[str, Any]:
    current = profile_from_session(session)
    merged = dict(current)
    merged.update(dict(patch))
    merged["visible_agents"] = _as_list(merged.get("visible_agents"))
    merged["default_agent"] = str(
        merged.get("default_agent") or getattr(session, "agent", "") or ""
    ).strip()
    if merged["visible_agents"] and merged["default_agent"] not in merged["visible_agents"]:
        merged["default_agent"] = merged["visible_agents"][0]
    merged["entry"] = _normalize_entry(merged.get("entry"))
    merged["permission_mode"] = _normalize_permission_mode(
        merged.get("permission_mode"),
        entry=merged["entry"],
    )
    merged["runtime_mode"] = _normalize_runtime_mode(
        merged.get("runtime_mode"),
        entry=merged["entry"],
    )
    merged["network_mode"] = _normalize_network_mode(
        merged.get("network_mode"),
        entry=merged["entry"],
    )
    merged["session_id"] = str(session.id)
    merged["project_id"] = str(session.project_id)
    project_root = str(getattr(session, "directory", "") or "").strip()
    workspace_root = _workspace_root_dir()
    merged["workspace_dir"] = workspace_root
    merged["project_root"] = project_root
    merged["project_revision"] = getattr(
        getattr(session, "time", None),
        "updated",
        None,
    )
    merged["owner_user_id"] = str(
        getattr(session, "owner_user_id", "") or ""
    ).strip() or None
    merged["owner_username"] = str(getattr(session, "owner_username", "") or "").strip() or None
    merged["version"] = PROFILE_VERSION
    merged["revision"] = int(current.get("revision") or 1) + 1
    merged["source"] = str(source or "session.profile.update")
    merged["updated_at"] = _now_iso()
    return merged


def with_profile_metadata(
    metadata: Mapping[str, Any] | None,
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    merged = dict(metadata or {})
    merged[PROFILE_METADATA_KEY] = dict(profile)
    return merged


async def get_session_execution_profile(session_id: str) -> dict[str, Any] | None:
    from flocks.session.session import Session

    session = await Session.get_by_id(str(session_id or "").strip())
    if session is None:
        return None
    return profile_from_session(session)


async def upsert_session_execution_profile(
    session_id: str,
    *,
    patch: Mapping[str, Any],
    source: str,
) -> dict[str, Any] | None:
    from flocks.session.session import Session

    session = await Session.get_by_id(str(session_id or "").strip())
    if session is None:
        return None
    merged_profile = merge_profile(session, patch=patch, source=source)
    metadata = with_profile_metadata(getattr(session, "metadata", None), merged_profile)
    updated = await Session.update(
        session.project_id,
        session.id,
        metadata=metadata,
    )
    if updated is None:
        return None
    return profile_from_session(updated)
