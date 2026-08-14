"""Session-keyed durable state for the phase-one report runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from .files import atomic_write_json, read_json, session_root, utc_now, validate_session_id


class ReportSessionState(BaseModel):
    """Internal product state keyed by the public Session locator."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    schema_version: Literal[1] = Field(default=1, alias="schemaVersion")
    session_id: str = Field(alias="sessionID")
    report_state: Optional[dict[str, Any]] = Field(default=None, alias="reportState")
    created_at: str = Field(default_factory=utc_now, alias="createdAt")
    updated_at: str = Field(default_factory=utc_now, alias="updatedAt")


class ReportSessionStateError(RuntimeError):
    """Session product state is missing, malformed, or points elsewhere."""


def state_path(session_id: str) -> Path:
    return session_root(session_id) / "index.json"


def ensure_session_state(session_id: str) -> ReportSessionState:
    """Create the minimal state index on the first product turn."""

    validated = validate_session_id(session_id)
    path = state_path(validated)
    if path.exists():
        return load_session_state(validated)
    state = ReportSessionState(sessionID=validated)
    atomic_write_json(path, state.model_dump(by_alias=True, exclude_none=True))
    return state


def load_session_state(session_id: str) -> ReportSessionState:
    validated = validate_session_id(session_id)
    path = state_path(validated)
    if not path.is_file():
        raise ReportSessionStateError("Report Session state was not initialized")
    state = ReportSessionState.model_validate(read_json(path))
    if state.session_id != validated:
        raise ReportSessionStateError("Report Session state identity is inconsistent")
    return state


def save_session_state(state: ReportSessionState) -> None:
    state.updated_at = utc_now()
    atomic_write_json(
        state_path(state.session_id),
        state.model_dump(by_alias=True, exclude_none=True),
    )
