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

REDACTED_VALUE = "[REDACTED]"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive(value: Any) -> Any:
    """Recursively redact common secret-bearing keys."""
    if isinstance(value, dict):
        redacted: Dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _SENSITIVE_KEYS:
                redacted[key] = REDACTED_VALUE
                continue
            redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def restore_redacted_echo(original: Any, patched: Any) -> Any:
    """Return *patched* with echoed redaction markers replaced by *original* values.

    Hooks only ever see the ``redact_sensitive`` view of a tool input.  A hook
    that hands the whole input back as ``validated_input_patch`` (for example
    to restore placeholders in one field) would otherwise overwrite every
    secret-bearing field with the literal marker.
    """
    if isinstance(patched, dict):
        base = original if isinstance(original, dict) else {}
        restored: Dict[str, Any] = {}
        for key, item in patched.items():
            normalized = str(key).strip().lower()
            if normalized in _SENSITIVE_KEYS and item == REDACTED_VALUE and key in base:
                restored[key] = base[key]
                continue
            restored[key] = restore_redacted_echo(base.get(key), item)
        return restored
    if isinstance(patched, list):
        base_list = original if isinstance(original, list) else []
        return [
            restore_redacted_echo(base_list[index] if index < len(base_list) else None, item)
            for index, item in enumerate(patched)
        ]
    return patched


def merge_redacted_rewrite(original: Any, rewritten: Any) -> Any:
    """Return *original* with only its secret-bearing values taken from *rewritten*.

    A hook written against the flat legacy ``tool.input`` rewrites it in place,
    outside the ``redact_sensitive`` view that policies judge.  A value at a
    redacted position reads ``[REDACTED]`` to every policy before and after such
    a rewrite, so it can be taken over (a redaction hook restores secrets
    this way); anything a policy can see has to arrive as
    ``validated_input_patch``.  Keys the rewrite added or dropped are ignored.
    """
    if isinstance(original, dict) and isinstance(rewritten, dict):
        merged: Dict[str, Any] = {}
        for key, item in original.items():
            if key not in rewritten:
                merged[key] = item
            elif str(key).strip().lower() in _SENSITIVE_KEYS:
                merged[key] = rewritten[key]
            else:
                merged[key] = merge_redacted_rewrite(item, rewritten[key])
        return merged
    if isinstance(original, list) and isinstance(rewritten, list) and len(original) == len(rewritten):
        return [merge_redacted_rewrite(item, rewritten[index]) for index, item in enumerate(original)]
    return original


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
