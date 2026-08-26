"""Verified snapshot download and content validation primitives."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from urllib.parse import urljoin, urlparse


class SnapshotDownloadError(RuntimeError):
    """A snapshot could not be securely downloaded or verified."""


def _configured_origin(base_url: str | None = None) -> tuple[str, str]:
    base_url = (
        base_url
        if base_url is not None
        else os.getenv("SITUATION_REPORT_BACKEND_BASE_URL", "")
    ).strip().rstrip("/")
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if base_url else ""
    return base_url, origin


def resolve_download_url(value: str, *, base_url: str | None = None) -> str:
    """Resolve a relative backend URL and enforce the configured origin allowlist."""

    base_url, allowed_origin = _configured_origin(base_url)
    parsed = urlparse(value)
    if parsed.scheme or parsed.netloc:
        resolved = value
    else:
        if not value.startswith("/") or not base_url:
            raise SnapshotDownloadError("Relative snapshot URLs require SITUATION_REPORT_BACKEND_BASE_URL")
        resolved = urljoin(f"{base_url}/", value)
    parsed = urlparse(resolved)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SnapshotDownloadError("Snapshot URL must use HTTP or HTTPS")
    if origin.rstrip("/") != allowed_origin:
        raise SnapshotDownloadError("Resource URL does not use SITUATION_REPORT_BACKEND_BASE_URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise SnapshotDownloadError("Snapshot URL cannot contain credentials or a fragment")
    return resolved


def _validate_template(path: Path) -> None:
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise SnapshotDownloadError("Template snapshot is not UTF-8") from exc
    if not text.strip() or "#" not in text:
        raise SnapshotDownloadError("Template snapshot is empty or not Markdown")


def _verify_file(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != expected_size or digest.hexdigest() != expected_sha256:
        raise SnapshotDownloadError(f"Stored snapshot verification failed: {path.name}")


def _validate_materials(path: Path) -> int:
    count = 0
    identities: set[str] = set()
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} is not an object")
                source_type = value.get("source_type")
                source_id = value.get("source_id")
                if source_type not in {"REPORT", "VULN", "DARKWEB", "TELEGRAM"}:
                    raise ValueError(f"line {line_number} has an invalid source_type")
                if not isinstance(source_id, str) or not source_id.strip():
                    raise ValueError(f"line {line_number} has an invalid source_id")
                identity = f"{source_type}:{source_id}"
                if identity in identities:
                    raise ValueError(f"line {line_number} duplicates material {identity}")
                identities.add(identity)
                count += 1
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotDownloadError(f"Material snapshot is not valid UTF-8 JSONL: {exc}") from exc
    return count
