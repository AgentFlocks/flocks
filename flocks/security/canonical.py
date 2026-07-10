"""
Canonicalization helpers for B3 policy pipeline.

This module only provides deterministic normalization primitives and hashes.
Security decisions remain in policy engine.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict
import shlex


CANONICAL_PARSER_VERSION = "b3-oss-1"


@dataclass
class CanonicalResult:
    status: str
    parser_version: str
    reason: str
    value: Any
    hash: str | None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "parser_version": self.parser_version,
            "reason": self.reason,
            "value": self.value,
            "hash": self.hash,
        }


def _is_json_compatible(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_compatible(item) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str) and _is_json_compatible(item)
            for key, item in value.items()
        )
    return False


def canonical_hash(value: Any) -> str | None:
    if not _is_json_compatible(value):
        return None
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def canonicalize_json(value: Any, *, parser_version: str = CANONICAL_PARSER_VERSION) -> CanonicalResult:
    payload_hash = canonical_hash(value)
    if payload_hash is None:
        return CanonicalResult(
            status="uncertain",
            parser_version=parser_version,
            reason="json_serialize_failed",
            value=None,
            hash=None,
        )
    return CanonicalResult(
        status="ok",
        parser_version=parser_version,
        reason="",
        value=value,
        hash=payload_hash,
    )


def canonicalize_path(path_value: str | None, *, parser_version: str = CANONICAL_PARSER_VERSION) -> CanonicalResult:
    if not isinstance(path_value, str) or not path_value.strip():
        return CanonicalResult(
            status="uncertain",
            parser_version=parser_version,
            reason="path_empty",
            value=None,
            hash=None,
        )
    try:
        resolved = str(Path(path_value).expanduser().resolve(strict=False))
    except Exception:
        return CanonicalResult(
            status="uncertain",
            parser_version=parser_version,
            reason="path_resolve_failed",
            value=None,
            hash=None,
        )
    payload_hash = canonical_hash({"path": resolved})
    return CanonicalResult(
        status="ok" if payload_hash else "uncertain",
        parser_version=parser_version,
        reason="" if payload_hash else "hash_failed",
        value={"path": resolved},
        hash=payload_hash,
    )


def canonicalize_command(command: str | None, *, parser_version: str = CANONICAL_PARSER_VERSION) -> CanonicalResult:
    if not isinstance(command, str) or not command.strip():
        return CanonicalResult(
            status="uncertain",
            parser_version=parser_version,
            reason="command_empty",
            value=None,
            hash=None,
        )
    try:
        argv = shlex.split(command, posix=True)
    except Exception:
        return CanonicalResult(
            status="uncertain",
            parser_version=parser_version,
            reason="command_parse_failed",
            value=None,
            hash=None,
        )
    normalized = " ".join(shlex.quote(part) for part in argv)
    value = {"argv": argv, "normalized": normalized}
    payload_hash = canonical_hash(value)
    return CanonicalResult(
        status="ok" if payload_hash else "uncertain",
        parser_version=parser_version,
        reason="" if payload_hash else "hash_failed",
        value=value,
        hash=payload_hash,
    )
