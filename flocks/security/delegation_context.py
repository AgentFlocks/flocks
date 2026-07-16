"""Private, persisted delegation context for capability-ceiling inheritance."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping, Optional

from flocks.security.capability_pool import _safe_subject, sanitize_parent_ceiling
from flocks.storage.storage import Storage


_DELEGATION_CONTEXT_PREFIX = "security:delegation:"
_SAFE_CONTEXT_SCALAR_KEYS = (
    "entry",
    "permission_mode",
    "execution_mode",
    "development_mode",
    "network_profile",
)
_SAFE_CONTEXT_COLLECTION_KEYS = (
    "data_domains",
    "secret_scopes",
)


def _context_key(session_id: str) -> str:
    return f"{_DELEGATION_CONTEXT_PREFIX}{session_id}"


def normalize_delegation_security_context(value: Any) -> Optional[dict[str, Any]]:
    """Strip a persisted delegation record down to authorization attributes."""
    if not isinstance(value, Mapping):
        return None
    if "parent_ceiling" not in value:
        return None
    ceiling = sanitize_parent_ceiling(value.get("parent_ceiling"))
    if ceiling is None:
        return None
    normalized: dict[str, Any] = {"parent_ceiling": ceiling}
    subject = _safe_subject(value.get("subject"))
    if subject:
        normalized["subject"] = subject
    for key in _SAFE_CONTEXT_SCALAR_KEYS:
        raw_value = value.get(key)
        if isinstance(raw_value, str) and raw_value.strip():
            normalized[key] = raw_value.strip()
    for key in _SAFE_CONTEXT_COLLECTION_KEYS:
        raw_values = value.get(key)
        if not isinstance(raw_values, (list, tuple, set, frozenset)):
            continue
        cleaned_values = [item.strip() for item in raw_values if isinstance(item, str) and item.strip()]
        if len(cleaned_values) == len(raw_values):
            normalized[key] = cleaned_values
    return normalized


async def store_delegation_security_context(session_id: str, context: Mapping[str, Any]) -> None:
    """Persist a server-created, secret-free delegation context.

    This is intentionally outside ``SessionInfo.metadata``: session metadata is
    surfaced by several routes and must never become an authorization source.
    """
    normalized = normalize_delegation_security_context(context)
    if normalized is None:
        raise ValueError("delegation security context requires a parent ceiling")
    await Storage.set(
        _context_key(session_id),
        deepcopy(normalized),
        "delegation_security_context",
    )


async def load_delegation_security_context(session_id: str) -> Optional[dict[str, Any]]:
    """Load the internal record, returning an explicit invalid marker if corrupt."""
    raw = await Storage.get(_context_key(session_id))
    if raw is None:
        return None
    normalized = normalize_delegation_security_context(raw)
    if normalized is None:
        return {"parent_ceiling": {"invalid": True}}
    return deepcopy(normalized)


async def resolve_session_security_context(
    session_id: str,
    *,
    delegation_context_required: bool,
    supplied_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Restore a persisted child context without trusting continuation input."""
    resolved = deepcopy(dict(supplied_context or {}))
    if not delegation_context_required:
        return resolved
    try:
        stored = await load_delegation_security_context(session_id)
    except Exception:
        stored = None
    # A continuation of a marked child must preserve the original ceiling even
    # when a caller supplies its own context.  Do not copy arbitrary caller
    # fields into a child execution context: the stored record is already
    # secret-free and contains the complete delegated identity/routing set.
    # Missing/corrupt internal state is explicit so the active B3 hook fails
    # closed.
    return deepcopy(stored or {"parent_ceiling": {"invalid": True}})
