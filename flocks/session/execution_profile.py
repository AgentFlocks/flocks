"""Session execution-profile helpers.

Profiles are persisted inside ``SessionInfo.metadata`` under a stable key so
all execution entrypoints can read one canonical, trusted envelope.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from flocks.session.session import SessionInfo

PROFILE_METADATA_KEY = "sessionExecutionProfile"
PROFILE_VERSION = "v1"


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
    visible = _as_list(visible_agents)
    default_agent_name = str(default_agent or "").strip() or str(session.agent or "").strip()
    if visible and default_agent_name and default_agent_name not in visible:
        default_agent_name = visible[0]
    return {
        "version": PROFILE_VERSION,
        "session_id": str(session.id),
        "project_id": str(session.project_id),
        "entry": str(entry or "interactive"),
        "visible_agents": visible,
        "default_agent": default_agent_name,
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
    profile["visible_agents"] = _as_list(profile.get("visible_agents"))
    profile["default_agent"] = str(
        profile.get("default_agent") or session.agent or ""
    ).strip()
    if profile["visible_agents"] and profile["default_agent"] not in profile["visible_agents"]:
        profile["default_agent"] = profile["visible_agents"][0]
    profile["entry"] = str(profile.get("entry") or "interactive")
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
        merged.get("default_agent") or session.agent or ""
    ).strip()
    if merged["visible_agents"] and merged["default_agent"] not in merged["visible_agents"]:
        merged["default_agent"] = merged["visible_agents"][0]
    merged["entry"] = str(merged.get("entry") or "interactive")
    merged["session_id"] = str(session.id)
    merged["project_id"] = str(session.project_id)
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
    metadata = with_profile_metadata(session.metadata, merged_profile)
    updated = await Session.update(
        session.project_id,
        session.id,
        metadata=metadata,
    )
    if updated is None:
        return None
    return profile_from_session(updated)
