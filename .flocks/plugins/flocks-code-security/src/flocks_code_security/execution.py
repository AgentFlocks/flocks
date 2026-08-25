"""Immutable execution-capsule primitives and failure classification."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable


MAX_FRESH_ATTEMPTS = 2
MAX_SAME_SESSION_RESUMES = 1


class ExecutionCapsuleError(ValueError):
    def __init__(self, reason: str, *, code: str = "IDENTITY_CAPSULE_MISMATCH"):
        super().__init__(f"{code}: {reason}")
        self.code = code


def _digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def toolset_digest(tool_names: Iterable[str]) -> str:
    return _digest(sorted({str(name) for name in tool_names}))


def scope_digest(
    *,
    snapshot_id: str,
    manifest_digest: str,
    paths: list[str],
    assignment_digest: str | None,
) -> str:
    return _digest(
        {
            "snapshot_id": snapshot_id,
            "manifest_digest": manifest_digest,
            "assignment_digest": assignment_digest,
            "paths": paths,
        }
    )


def classify_execution_failure(error: str | None) -> str:
    message = str(error or "").casefold()
    if "identity_capsule_mismatch" in message or "snapshot_mismatch" in message:
        return "identity_capsule_mismatch"
    if "session" in message and any(
        marker in message for marker in ("not found", "missing", "does not exist")
    ):
        return "session_missing"
    if any(
        marker in message
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "rate limit",
            "stream interrupted",
            "connection reset",
            "timed out",
            "timeout",
            "无活跃交互",
        )
    ):
        return "transient_execution_failure"
    return "agent_exited_no_facts"
