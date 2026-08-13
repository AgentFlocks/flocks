"""Versioned hook contracts for normalized execution payloads."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive(value: Any) -> Any:
    """Recursively redact common secret-bearing keys."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SENSITIVE_KEYS:
                redacted[key] = "[REDACTED]"
                continue
            redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def schema_digest(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HookDecisionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"


class _FrozenContract(BaseModel):
    """Immutable, versioned data exposed to extension code."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class ToolDecision(_FrozenContract):
    action: HookDecisionAction = HookDecisionAction.ALLOW
    reason: Optional[str] = None
    labels: list[str] = Field(default_factory=list)
    validated_input_patch: Optional[dict[str, Any]] = None


class ToolExecutionOutcome(_FrozenContract):

    status: Literal["success", "deny", "confirm", "error", "cancelled", "stopped"]
    duration_ms: int = Field(ge=0, default=0)
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    result_summary: Optional[Any] = None


class ToolExecutionIdentity(_FrozenContract):
    id: str
    trace_id: Optional[str] = None
    tool_call_id: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    started_at: str


class ToolExecutionSessionProject(_FrozenContract):
    id: Optional[str] = None
    root: Optional[str] = None
    revision: Optional[str] = None


class ToolExecutionSession(_FrozenContract):
    id: str
    message_id: Optional[str] = None
    turn_id: Optional[str] = None
    step: int = Field(default=0, ge=0)
    entry: str = "unknown"
    workspace_root: Optional[str] = None
    project: ToolExecutionSessionProject = Field(default_factory=ToolExecutionSessionProject)


class ToolExecutionActorSubject(_FrozenContract):
    id: Optional[str] = None
    type: Optional[str] = None


class ToolExecutionActorAgent(_FrozenContract):
    id: Optional[str] = None


class ToolExecutionActor(_FrozenContract):
    subject: ToolExecutionActorSubject = Field(default_factory=ToolExecutionActorSubject)
    agent: ToolExecutionActorAgent = Field(default_factory=ToolExecutionActorAgent)


class ToolExecutionTool(_FrozenContract):
    name: str
    source: Optional[str] = None
    category: Optional[str] = None
    raw_input: dict[str, Any] = Field(default_factory=dict)
    validated_input: dict[str, Any] = Field(default_factory=dict)
    schema_digest: Optional[str] = None


class ToolExecutionSafetyMode(_FrozenContract):
    runtime_mode: str = "exe-mode"
    permission_mode: str = "readonly"
    network_mode: str = "require-confirm"


class ToolExecutionContext(_FrozenContract):

    schema_version: Literal["v1"] = "v1"
    execution: ToolExecutionIdentity
    session: ToolExecutionSession
    actor: ToolExecutionActor
    tool: ToolExecutionTool
    safety_mode: ToolExecutionSafetyMode = Field(default_factory=ToolExecutionSafetyMode)
    extension_context: dict[str, Any] = Field(default_factory=dict)
