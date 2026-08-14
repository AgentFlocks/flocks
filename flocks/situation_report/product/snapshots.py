"""Verified snapshot download and content validation primitives."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlparse

import httpx


class SnapshotDownloadError(RuntimeError):
    """A snapshot could not be securely downloaded or verified."""


def _configured_origins() -> tuple[str, set[str]]:
    base_url = os.getenv("SITUATION_REPORT_BACKEND_BASE_URL", "").strip().rstrip("/")
    allowed = {
        item.strip().rstrip("/")
        for item in os.getenv("SITUATION_REPORT_DOWNLOAD_ORIGINS", "").split(",")
        if item.strip()
    }
    if base_url:
        parsed = urlparse(base_url)
        allowed.add(f"{parsed.scheme}://{parsed.netloc}")
    return base_url, allowed


def resolve_download_url(value: str) -> str:
    """Resolve a relative backend URL and enforce the configured origin allowlist."""

    base_url, allowed_origins = _configured_origins()
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
    if origin.rstrip("/") not in allowed_origins:
        raise SnapshotDownloadError("Snapshot URL origin is not allowlisted")
    if parsed.username or parsed.password or parsed.fragment:
        raise SnapshotDownloadError("Snapshot URL cannot contain credentials or a fragment")
    return resolved


class SnapshotDownloader:
    """Stream expected bytes to disk while checking expiry, size and SHA-256."""

    def __init__(self, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None) -> None:
        self._client_factory = client_factory

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False)

    async def download(
        self,
        *,
        url: str,
        expires_at: int,
        expected_size: int,
        expected_sha256: str,
        destination: Path,
    ) -> dict[str, Any]:
        if int(time.time() * 1000) >= expires_at:
            raise SnapshotDownloadError("Snapshot download reference has expired")
        resolved_url = resolve_download_url(url)
        headers: dict[str, str] = {}
        token = os.getenv("SITUATION_REPORT_BACKEND_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"

        temporary = destination.with_name(f".{destination.name}.download")
        digest = hashlib.sha256()
        size = 0
        try:
            async with self._client() as client:
                async with client.stream("GET", resolved_url, headers=headers) as response:
                    response.raise_for_status()
                    temporary.parent.mkdir(parents=True, exist_ok=True)
                    with temporary.open("wb") as handle:
                        os.chmod(temporary, 0o600)
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > expected_size:
                                raise SnapshotDownloadError("Snapshot is larger than declared")
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
            actual_sha256 = digest.hexdigest()
            if size != expected_size:
                raise SnapshotDownloadError(f"Snapshot size mismatch: expected {expected_size}, got {size}")
            if actual_sha256 != expected_sha256:
                raise SnapshotDownloadError("Snapshot SHA-256 mismatch")
            os.replace(temporary, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return {"sizeBytes": size, "sha256": actual_sha256}
        except httpx.HTTPError as exc:
            raise SnapshotDownloadError(f"Snapshot HTTP download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)


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
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} is not an object")
                count += 1
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise SnapshotDownloadError(f"Material snapshot is not valid UTF-8 JSONL: {exc}") from exc
    if count == 0:
        raise SnapshotDownloadError("Material snapshot contains no records")
    return count
