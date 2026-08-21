"""Validation for completed code-security artifact bundles."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from flocks_code_security.contract import validate_bundle
from flocks_code_security.paths import outputs_root


REQUIRED_ARTIFACTS = {
    "adjudication.json",
    "coverage.json",
    "findings.json",
    "report.md",
    "report.sarif",
    "threat-model.json",
}


@dataclass(frozen=True)
class ArtifactIntegrity:
    status: str
    digests: dict[str, str]
    errors: tuple[str, ...] = ()


def find_output_directory(scan_id: str) -> Path | None:
    if not scan_id.startswith("scan_") or not scan_id[5:].isalnum():
        return None
    root = outputs_root().resolve()
    if not root.is_dir():
        return None
    for day_dir in sorted(root.iterdir(), reverse=True):
        candidate = day_dir / "code-security" / scan_id
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    return None


def verify_artifact_bundle(
    scan_id: str,
    output_directory: Path | None = None,
) -> ArtifactIntegrity:
    output = (
        _validated_output_directory(output_directory)
        if output_directory is not None
        else find_output_directory(scan_id)
    )
    if output is None:
        return ArtifactIntegrity("invalid", {}, ("Completed scan output is missing",))

    manifest_path = output / "scan-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return ArtifactIntegrity("invalid", {}, ("Scan manifest is missing",))

    try:
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        scan = manifest["scan"]
        if scan.get("id") != scan_id:
            raise ValueError("Scan manifest ID does not match the requested scan")

        artifact_contents: dict[str, bytes] = {}
        digests: dict[str, str] = {"scan-manifest.json": hashlib.sha256(manifest_bytes).hexdigest()}
        for artifact in scan["artifacts"]:
            relative = _artifact_path(artifact.get("path"))
            candidate = output / Path(*relative.parts)
            if not candidate.is_file() or candidate.is_symlink():
                raise ValueError(f"Sealed artifact is missing: {relative.as_posix()}")
            path = candidate.resolve()
            path.relative_to(output)
            contents = path.read_bytes()
            digest = hashlib.sha256(contents).hexdigest()
            if digest != artifact.get("sha256"):
                raise ValueError(f"Sealed artifact digest mismatch: {relative.as_posix()}")
            artifact_contents[relative.as_posix()] = contents
            digests[relative.as_posix()] = digest

        missing = REQUIRED_ARTIFACTS - artifact_contents.keys()
        if missing:
            raise ValueError("Required sealed artifacts are missing: " + ", ".join(sorted(missing)))
        if (output / "dynamic-validation.json").exists() and "dynamic-validation.json" not in artifact_contents:
            raise ValueError("Dynamic validation artifact is not sealed")

        findings = _json_artifact(artifact_contents, "findings.json")
        coverage = _json_artifact(artifact_contents, "coverage.json")
        validate_bundle(manifest, findings, coverage, artifact_contents)
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        return ArtifactIntegrity("invalid", {}, (str(exc),))

    return ArtifactIntegrity("valid", digests)


def _validated_output_directory(path: Path) -> Path | None:
    candidate = path.expanduser()
    if not candidate.is_dir() or candidate.is_symlink():
        return None
    return candidate.resolve()


def _artifact_path(value: Any) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("Sealed artifact path is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"Sealed artifact path is invalid: {value}")
    return path


def _json_artifact(contents: dict[str, bytes], path: str) -> dict[str, Any]:
    payload = json.loads(contents[path])
    if not isinstance(payload, dict):
        raise ValueError(f"Sealed artifact must contain an object: {path}")
    return payload
