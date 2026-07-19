from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Subject(BaseModel):
    """Opaque identity data supplied by an entrypoint or extension."""

    subject_id: str
    subject_type: str
    display_name: str | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
