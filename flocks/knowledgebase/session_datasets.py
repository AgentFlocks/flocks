"""Read and write the current Session's Dataset ID list."""

from __future__ import annotations

import re
from typing import Any

from flocks.session.policy import SessionPolicy
from flocks.session.session import Session

from .errors import KnowledgebaseError

_KEY = "knowledgebase"
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def dataset_ids_from_metadata(metadata: Any) -> list[str]:
    if not isinstance(metadata, dict):
        return []
    raw = metadata.get(_KEY)
    if not isinstance(raw, dict):
        return []
    values = raw.get("dataset_ids", [])
    if not isinstance(values, list) or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in values):
        return []
    return list(values)


def validate_dataset_ids(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > 50:
        raise KnowledgebaseError(422, "invalid_dataset_ids", "Provide at most 50 Dataset IDs.")
    if any(not isinstance(item, str) or not _ID.fullmatch(item) for item in value) or len(set(value)) != len(value):
        raise KnowledgebaseError(422, "invalid_dataset_ids", "Dataset IDs must be unique resource identifiers.")
    return list(value)


async def _session(session_id: str, user: Any, *, write: bool):
    session = await Session.get_by_id(session_id)
    if session is None:
        raise KnowledgebaseError(404, "session_not_found", "The Session does not exist.")
    allowed = SessionPolicy.can_write(session, user) if write else SessionPolicy.can_read(session, user)
    if not allowed:
        raise KnowledgebaseError(403, "session_forbidden", "This Session is not available.")
    return session


async def get_selection(session_id: str, user: Any) -> dict[str, Any]:
    session = await _session(session_id, user, write=False)
    return {"session_id": session.id, "dataset_ids": dataset_ids_from_metadata(session.metadata)}


async def detach_dataset(dataset_id: str) -> None:
    """Drop a deleted Dataset ID from every Session that still lists it."""
    if not isinstance(dataset_id, str) or not _ID.fullmatch(dataset_id):
        return
    try:
        sessions = await Session.list_all_unfiltered()
    except Exception:
        return
    for session in sessions:
        current = dataset_ids_from_metadata(getattr(session, "metadata", None))
        if dataset_id not in current:
            continue
        remaining = [item for item in current if item != dataset_id]

        def mutate(metadata: dict[str, Any], kept: list[str] = remaining) -> dict[str, Any]:
            metadata[_KEY] = {"dataset_ids": kept}
            return metadata

        try:
            await Session.mutate_metadata(session.project_id, session.id, mutate)
        except Exception:
            continue


async def set_selection(session_id: str, user: Any, dataset_ids: list[str]) -> dict[str, Any]:
    identifiers = validate_dataset_ids(dataset_ids)
    session = await _session(session_id, user, write=True)

    def mutate(metadata: dict[str, Any]) -> dict[str, Any]:
        metadata[_KEY] = {"dataset_ids": identifiers}
        return metadata

    updated = await Session.mutate_metadata(session.project_id, session.id, mutate)
    if updated is None:
        raise KnowledgebaseError(409, "session_not_active", "The Session cannot be updated.")
    return {"session_id": updated.id, "dataset_ids": identifiers}
