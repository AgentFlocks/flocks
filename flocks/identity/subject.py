from __future__ import annotations

import contextvars
from typing import Any

from pydantic import BaseModel, Field


class Subject(BaseModel):
    """Opaque caller metadata supplied by an entrypoint or extension.

    This carrier is deliberately not an authorization principal in Flocks:
    hooks may populate it for tracing, audit, and downstream invocation
    context, but its fields and attributes grant no role, permission, tenant,
    or policy authority.  Authorization owners must obtain and validate their
    own inputs; Flocks does not maintain a trusted-hook allowlist here.
    """

    subject_id: str
    subject_type: str
    display_name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)


_current_subject: contextvars.ContextVar[Subject | None] = contextvars.ContextVar(
    "current_subject",
    default=None,
)


def set_current_subject(subject: Subject | None) -> contextvars.Token[Subject | None]:
    """Bind opaque, non-authoritative caller metadata to this execution."""

    return _current_subject.set(subject)


def reset_current_subject(token: contextvars.Token[Subject | None]) -> None:
    """Restore the subject context that preceded a binding."""

    _current_subject.reset(token)


def get_current_subject() -> Subject | None:
    """Return opaque, non-authoritative caller metadata for this execution."""

    return _current_subject.get()
