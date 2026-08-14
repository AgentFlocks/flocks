"""Strict wire contracts for phase-one report Session actions."""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
REPORT_REQUEST_SENTINEL = "SITUATION_REPORT_REQUEST_V1"


class SnapshotDownload(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    url: str
    expires_at: int = Field(alias="expiresAt", gt=0)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized or len(normalized) > 2048:
            raise ValueError("download.url is required and must not exceed 2048 characters")
        return normalized


class ReportAction(BaseModel):
    """One report action embedded in exactly one text Part."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    name: Literal[
        "situation_report.generate",
        "situation_report.modify",
        "situation_report.regenerate",
    ]
    version: Literal["1"]
    request_id: str = Field(alias="requestID")
    generation_id: str = Field(alias="generationID")
    base_backend_report_version: Optional[int] = Field(
        default=None,
        alias="baseBackendReportVersion",
        ge=0,
    )
    language: Optional[Literal["zh-CN", "en-US"]] = None

    @field_validator("request_id", "generation_id")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        if not SAFE_IDENTIFIER.fullmatch(value):
            raise ValueError("requestID and generationID must be safe identifiers")
        return value

    @model_validator(mode="after")
    def validate_operation_fields(self) -> "ReportAction":
        if self.name == "situation_report.generate":
            if self.language is None:
                raise ValueError("generate requires language")
            if self.base_backend_report_version is not None:
                raise ValueError("generate requires baseBackendReportVersion=null")
        else:
            if self.language is not None:
                raise ValueError("modify/regenerate must reuse Session language")
            if self.base_backend_report_version is None:
                raise ValueError("modify/regenerate require baseBackendReportVersion")
        return self

    @property
    def operation(self) -> Literal["generate", "modify", "regenerate"]:
        return self.name.rsplit(".", 1)[-1]  # type: ignore[return-value]


class ParsedReportPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    action: ReportAction


class ReportPromptEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ReportAction


def build_report_prompt_text(*, action: ReportAction, user_instruction: str) -> str:
    """Build the one-text-Part wire format used by the business backend."""

    instruction = user_instruction.strip()
    if not instruction:
        raise ValueError("Report action text cannot be empty")
    envelope = ReportPromptEnvelope(action=action).model_dump(
        by_alias=True,
        exclude_none=True,
    )
    packed = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    return f"{REPORT_REQUEST_SENTINEL}\n{packed}\n{instruction}"


def parse_report_prompt_parts(parts: list[dict[str, Any]]) -> ParsedReportPrompt:
    """Parse one strict text envelope without accepting workspace switching fields."""

    if len(parts) != 1 or parts[0].get("type") != "text":
        raise ValueError("Report actions require exactly one text part")
    part = parts[0]
    if set(part) - {"type", "text"}:
        raise ValueError("Report action text parts cannot contain metadata or extra fields")
    wire_text = part.get("text")
    if not isinstance(wire_text, str):
        raise ValueError("Report action text is required")
    sections = wire_text.split("\n", 2)
    if len(sections) != 3 or sections[0] != REPORT_REQUEST_SENTINEL:
        raise ValueError("Report action text envelope is invalid")
    if not sections[2].strip():
        raise ValueError("Report action text cannot be empty")
    try:
        raw_envelope = json.loads(sections[1])
    except json.JSONDecodeError as exc:
        raise ValueError("Report action JSON envelope is invalid") from exc
    envelope = ReportPromptEnvelope.model_validate(raw_envelope)
    return ParsedReportPrompt(text=sections[2].strip(), action=envelope.action)
