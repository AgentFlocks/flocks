"""Codex Security v1 canonical artifact helpers.

The schemas and identity algorithms are adapted from the Apache-2.0-licensed
OpenAI Codex Security bundled plugin. Flocks supplies its own trusted scan facts.
"""

from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "1.0"
PRODUCER_NAME = "flocks-code-security"
PRODUCER_VERSION = "0.5.0"
FINGERPRINT_ALGORITHM = "codex-security/v1"
SNAPSHOT_ALGORITHM = "codex-security-snapshot/v1"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
SCHEMA_FILES = {
    "manifest": "scan-manifest.schema.json",
    "findings": "findings.schema.json",
    "coverage": "coverage.schema.json",
}


def canonical_json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))


def snapshot_digest(tree_digest: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", tree_digest):
        raise ValueError("Snapshot tree digest must be lowercase SHA-256")
    return f"{SNAPSHOT_ALGORITHM}:sha256:{tree_digest}"


def finding_fingerprint(
    target_id: str,
    rule_id: str,
    anchor: str,
    instance: str = "",
) -> str:
    for name, value in (("ruleId", rule_id), ("identity.anchor", anchor)):
        if not SLUG_RE.fullmatch(value):
            raise ValueError(f"{name} must be a stable lowercase semantic slug")
    if instance and not SLUG_RE.fullmatch(instance):
        raise ValueError("identity.instance must be a stable lowercase semantic slug")
    material = "\0".join(
        (FINGERPRINT_ALGORITHM, target_id, rule_id, anchor, instance)
    )
    return f"{FINGERPRINT_ALGORITHM}:sha256:{sha256_text(material)}"


def stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{sha256_text(chr(0).join(parts))[:24]}"


def finding_identity(
    scan_id: str,
    target_id: str,
    rule_id: str,
    anchor: str,
    instance: str = "",
) -> tuple[str, str, dict[str, str]]:
    fingerprint = finding_fingerprint(target_id, rule_id, anchor, instance)
    return (
        stable_id("csf", fingerprint),
        stable_id("occ", scan_id, fingerprint),
        {"algorithm": FINGERPRINT_ALGORITHM, "primary": fingerprint},
    )


def artifact_record(
    path: str,
    contents: bytes,
    media_type: str = "application/json",
) -> dict[str, str]:
    return {
        "path": path,
        "sha256": sha256_bytes(contents),
        "mediaType": media_type,
    }


@lru_cache(maxsize=None)
def _validator(document: str) -> Draft202012Validator:
    try:
        filename = SCHEMA_FILES[document]
    except KeyError as exc:
        raise ValueError(f"Unknown canonical document: {document}") from exc
    schema = json.loads(
        resources.files("flocks_code_security")
        .joinpath("schemas", filename)
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_document(document: str, payload: dict[str, Any]) -> None:
    errors = sorted(_validator(document).iter_errors(payload), key=lambda item: list(item.path))
    if not errors:
        return
    error = errors[0]
    location = ".".join(str(part) for part in error.absolute_path)
    prefix = f"{document}.{location}" if location else document
    raise ValueError(f"{prefix}: {error.message}")


def validate_bundle(
    manifest: dict[str, Any],
    findings: dict[str, Any],
    coverage: dict[str, Any],
    artifact_contents: dict[str, bytes],
) -> None:
    validate_document("findings", findings)
    validate_document("coverage", coverage)
    validate_document("manifest", manifest)
    scan = manifest["scan"]
    scan_id = scan["id"]
    if findings["scanId"] != scan_id or coverage["scanId"] != scan_id:
        raise ValueError("Canonical document scan IDs do not match")
    if coverage["includePaths"] != scan["scope"]["includePaths"]:
        raise ValueError("Coverage includePaths do not match manifest scope")
    if coverage["excludePaths"] != scan["scope"]["excludePaths"]:
        raise ValueError("Coverage excludePaths do not match manifest scope")

    artifact_paths: set[str] = set()
    for artifact in scan["artifacts"]:
        path = artifact["path"]
        if path in artifact_paths:
            raise ValueError(f"Duplicate sealed artifact path: {path}")
        artifact_paths.add(path)
        contents = artifact_contents.get(path)
        if contents is None:
            raise ValueError(f"Sealed artifact contents are unavailable: {path}")
        if artifact["sha256"] != sha256_bytes(contents):
            raise ValueError(f"Sealed artifact digest mismatch: {path}")

    required = {"findings.json", "coverage.json"}
    if not required <= artifact_paths:
        raise ValueError("Canonical findings and coverage must be sealed")
