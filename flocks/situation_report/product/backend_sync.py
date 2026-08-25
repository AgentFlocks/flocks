"""Synchronize current report resources by Flocks Session ID."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ParsedReportPrompt
from .files import async_file_lock, atomic_write_json, read_json, session_root, utc_now
from .session_state import ensure_session_state, load_session_state, save_session_state
from .snapshots import (
    SnapshotDownloadError,
    _validate_materials,
    _validate_template,
    _verify_file,
    resolve_download_url,
)


class BackendReportSyncError(RuntimeError):
    """The backend current-resource contract could not be satisfied safely."""


class LatestResource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exists: bool
    changed: bool
    version: Optional[int] = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> "LatestResource":
        if not self.exists:
            if self.changed or self.version not in {None, 0}:
                raise ValueError("A missing resource cannot be changed or carry a positive version")
            # Some backend serializers use integer zero as the sentinel for
            # an absent resource. Keep the wire boundary tolerant while the
            # rest of Flocks retains one canonical missing-resource shape.
            self.version = None
            return self
        if self.version is None or self.version < 1:
            raise ValueError("An existing resource requires a positive version")
        return self


class LatestReportStateResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    session_id: str = Field(alias="sessionId")
    report: LatestResource
    template: LatestResource
    materials: LatestResource


@dataclass(frozen=True)
class DownloadedResource:
    resource: Literal["report", "template", "materials"]
    version: int
    path: Path
    size_bytes: int
    sha256: str


RESOURCE_DOWNLOADS: dict[str, tuple[str, str, int]] = {
    "report": ("report/download", "X-Report-Version", 10 * 1024 * 1024),
    "template": ("template/download", "X-Template-Version", 5 * 1024 * 1024),
    "materials": ("materials/download", "X-Material-Version", 64 * 1024 * 1024),
}


class BackendReportSynchronizer:
    def __init__(self, client_factory: Optional[Callable[[], httpx.AsyncClient]] = None) -> None:
        self._client_factory = client_factory

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=False)

    @staticmethod
    def _headers(request_id: str) -> dict[str, str]:
        token = os.getenv("SITUATION_REPORT_BACKEND_TOKEN", "").strip()
        if not token:
            raise BackendReportSyncError("SITUATION_REPORT_BACKEND_TOKEN is not configured")
        return {"X-Request-ID": request_id, "Authorization": f"Bearer {token}"}

    @staticmethod
    def _url(path: str) -> str:
        try:
            return resolve_download_url(path)
        except SnapshotDownloadError as exc:
            raise BackendReportSyncError(f"Backend resource URL is invalid: {exc}") from exc

    async def get_latest(
        self,
        *,
        session_id: str,
        known_report_version: int,
        known_template_version: int,
        known_material_version: int,
        request_id: str,
    ) -> LatestReportStateResponse:
        url = self._url(f"/internal/flocks/v1/report-sessions/{session_id}/state/latest")
        params = {
            "knownReportVersion": known_report_version,
            "knownTemplateVersion": known_template_version,
            "knownMaterialVersion": known_material_version,
        }
        try:
            async with self._client() as client:
                response = await client.get(url, params=params, headers=self._headers(request_id))
                response.raise_for_status()
                if response.headers.get("X-Request-ID") != request_id:
                    raise BackendReportSyncError("Latest-state response did not echo X-Request-ID")
                latest = LatestReportStateResponse.model_validate(response.json())
                if latest.session_id != session_id:
                    raise BackendReportSyncError("Latest-state response sessionId does not match the request")
                return latest
        except BackendReportSyncError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendReportSyncError(f"Latest backend report state check failed: {exc}") from exc

    async def download_current(
        self,
        *,
        session_id: str,
        resource: Literal["report", "template", "materials"],
        request_id: str,
        destination: Path,
    ) -> DownloadedResource:
        endpoint, version_header, size_limit = RESOURCE_DOWNLOADS[resource]
        url = self._url(f"/internal/flocks/v1/report-sessions/{session_id}/{endpoint}")
        temporary = destination.with_name(f".{destination.name}.download")
        digest = hashlib.sha256()
        size = 0
        try:
            async with self._client() as client:
                async with client.stream("GET", url, headers=self._headers(request_id)) as response:
                    response.raise_for_status()
                    if response.headers.get("X-Request-ID") != request_id:
                        raise BackendReportSyncError(f"{resource} response did not echo X-Request-ID")
                    try:
                        version = int(response.headers.get(version_header) or "")
                    except ValueError as exc:
                        raise BackendReportSyncError(f"{resource} response has an invalid {version_header}") from exc
                    if version < 1:
                        raise BackendReportSyncError(f"{resource} response has an invalid {version_header}")
                    media_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                    expected = "application/x-ndjson" if resource == "materials" else "text/markdown"
                    if media_type != expected:
                        raise BackendReportSyncError(f"{resource} response Content-Type must be {expected}")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with temporary.open("wb") as handle:
                        os.chmod(temporary, 0o600)
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > size_limit:
                                raise BackendReportSyncError(f"{resource} response exceeds its size limit")
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
            os.replace(temporary, destination)
            directory_fd = os.open(destination.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return DownloadedResource(resource, version, destination, size, digest.hexdigest())
        except BackendReportSyncError:
            raise
        except (httpx.HTTPError, OSError) as exc:
            raise BackendReportSyncError(f"Current {resource} download failed: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)


def _file_metadata(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    if not content.strip():
        raise BackendReportSyncError("Base report is empty")
    try:
        content.decode("utf-8")
    except UnicodeError as exc:
        raise BackendReportSyncError("Base report is not UTF-8 Markdown") from exc
    return {"sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _validate_latest_version(*, name: str, known_version: int, latest: LatestResource) -> int:
    if not latest.exists or latest.version is None:
        raise BackendReportSyncError(f"Backend {name} does not exist")
    if latest.version < known_version:
        raise BackendReportSyncError(f"Backend {name} version moved backwards")
    if latest.changed != (latest.version != known_version):
        raise BackendReportSyncError(f"Backend {name} changed flag does not match its version")
    return latest.version


def _local_snapshot_id(*, name: str, version: int, sha256: str) -> str:
    return f"{name}-v{version}-{sha256[:16]}"


def _commit_download(*, downloaded: DownloadedResource, destination: Path) -> None:
    if destination.exists():
        try:
            _verify_file(destination, expected_size=downloaded.size_bytes, expected_sha256=downloaded.sha256)
        except (OSError, SnapshotDownloadError) as exc:
            raise BackendReportSyncError("Existing local resource snapshot is inconsistent") from exc
        downloaded.path.unlink(missing_ok=True)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(downloaded.path, destination)
    directory_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _resource_payload(
    *,
    version: int,
    snapshot_id: str,
    format: str,
    path: str,
    size_bytes: int,
    sha256: str,
    record_count: Optional[int] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": version,
        "snapshotID": snapshot_id,
        "format": format,
        "path": path,
        "sizeBytes": size_bytes,
        "sha256": sha256,
    }
    if record_count is not None:
        payload["recordCount"] = record_count
    return payload


async def initialize_report_action(
    *,
    session_id: str,
    prompt: ParsedReportPrompt,
    synchronizer: Optional[BackendReportSynchronizer] = None,
) -> Path:
    """Download current resources, then atomically initialize one A1 run."""

    ensure_session_state(session_id)
    action = prompt.action
    root = session_root(session_id)
    run_dir = root / "runs" / action.generation_id
    request_path = run_dir / "request.json"
    context_path = run_dir / "preprocessing" / "generation_context_001.json"
    request_payload = {
        "schemaVersion": 1,
        "requestID": action.request_id,
        "generationID": action.generation_id,
        "operation": action.operation,
        "text": prompt.text,
        "action": action.model_dump(by_alias=True, exclude_none=True),
    }
    request_hash = hashlib.sha256(
        json.dumps(request_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()

    async with async_file_lock(root / ".locks" / "state.lock"):
        if request_path.exists():
            previous = read_json(request_path)
            if previous.get("requestHash") != request_hash:
                raise BackendReportSyncError("generationID was already used for different inputs")
            if context_path.exists():
                return context_path

        state = load_session_state(session_id)
        current = dict(state.report_state or {})
        if action.operation == "generate" and current.get("currentFlocksReportVersion"):
            raise BackendReportSyncError("generate is not valid after the Session's initial report was published")
        if action.operation != "generate" and not current:
            raise BackendReportSyncError("Report Session configuration is not initialized")

        known_report_version = max(
            int(current.get("syncedBackendReportVersion") or 0),
            int(current.get("observedBackendReportVersion") or 0),
        )
        known_template_version = int(current.get("templateVersion") or 0)
        known_material_version = int(current.get("materialVersion") or 0)
        resolved_sync = synchronizer or BackendReportSynchronizer()
        latest = await resolved_sync.get_latest(
            session_id=session_id,
            known_report_version=known_report_version,
            known_template_version=known_template_version,
            known_material_version=known_material_version,
            request_id=action.request_id,
        )

        state_template_version = _validate_latest_version(
            name="template", known_version=known_template_version, latest=latest.template
        )
        state_material_version = _validate_latest_version(
            name="materials", known_version=known_material_version, latest=latest.materials
        )
        if latest.report.exists:
            report_version = _validate_latest_version(
                name="report", known_version=known_report_version, latest=latest.report
            )
        elif action.operation == "generate" and not latest.report.changed:
            report_version = 0
        else:
            raise BackendReportSyncError("Backend report does not exist")
        if action.operation == "generate" and latest.report.exists:
            raise BackendReportSyncError("generate requires a Session without an existing backend report")
        if action.operation == "modify" and report_version != action.base_backend_report_version:
            raise BackendReportSyncError("Backend report version changed after the modification request was created")

        staging_dir = run_dir / "preprocessing" / "downloads"
        tasks: list[tuple[str, Any]] = [
            (
                "template",
                resolved_sync.download_current(
                    session_id=session_id,
                    resource="template",
                    request_id=action.request_id,
                    destination=staging_dir / "template.md",
                ),
            ),
            (
                "materials",
                resolved_sync.download_current(
                    session_id=session_id,
                    resource="materials",
                    request_id=action.request_id,
                    destination=staging_dir / "materials.jsonl",
                ),
            ),
        ]
        if action.operation == "modify":
            tasks.insert(
                0,
                (
                    "report",
                    resolved_sync.download_current(
                        session_id=session_id,
                        resource="report",
                        request_id=action.request_id,
                        destination=staging_dir / "report.md",
                    ),
                ),
            )
        results = await asyncio.gather(*(task for _, task in tasks), return_exceptions=True)
        failures = [value for value in results if isinstance(value, BaseException)]
        if failures:
            first = failures[0]
            if isinstance(first, BackendReportSyncError):
                raise first
            raise BackendReportSyncError(
                f"Backend resource download failed: {type(first).__name__}: {first}"
            ) from first
        downloaded = {name: value for (name, _), value in zip(tasks, results) if isinstance(value, DownloadedResource)}
        template_download = downloaded["template"]
        material_download = downloaded["materials"]
        if template_download.version < max(state_template_version, known_template_version):
            raise BackendReportSyncError("Downloaded template version moved backwards")
        if material_download.version < max(state_material_version, known_material_version):
            raise BackendReportSyncError("Downloaded material version moved backwards")
        template_version = template_download.version
        material_version = material_download.version

        try:
            _validate_template(template_download.path)
            material_count = _validate_materials(material_download.path)
        except (OSError, SnapshotDownloadError) as exc:
            raise BackendReportSyncError(f"Downloaded resource validation failed: {exc}") from exc

        template_snapshot_id = _local_snapshot_id(
            name="template", version=template_version, sha256=template_download.sha256
        )
        material_snapshot_id = _local_snapshot_id(
            name="materials", version=material_version, sha256=material_download.sha256
        )
        template_relative = f"templates/snapshots/{template_snapshot_id}/template.md"
        material_relative = f"materials/snapshots/{material_snapshot_id}/materials.jsonl"
        template_path = root / template_relative
        material_path = root / material_relative

        base_report: Optional[dict[str, Any]] = None
        if action.operation == "modify":
            report_download = downloaded["report"]
            if report_download.version != action.base_backend_report_version:
                raise BackendReportSyncError(
                    "Downloaded report version changed after the modification request was created"
                )
            report_version = report_download.version
            report_relative = f"input/backend-reports/{report_version}-{report_download.sha256[:16]}/report.md"
            report_path = root / report_relative
            report_metadata = _file_metadata(report_download.path)
            _commit_download(downloaded=report_download, destination=report_path)
            current["syncedBackendReportVersion"] = report_version
            current["syncedBackendReportPath"] = report_relative
            base_report = {"backendReportVersion": report_version, "path": report_relative, **report_metadata}

        _commit_download(downloaded=template_download, destination=template_path)
        _commit_download(downloaded=material_download, destination=material_path)

        language = action.language if action.operation == "generate" else current.get("language")
        if language not in {"zh-CN", "en-US"}:
            raise BackendReportSyncError("Report Session language is not initialized")
        current.update(
            {
                "language": language,
                "firstGenerationID": current.get("firstGenerationID") or action.generation_id,
                "observedBackendReportVersion": report_version,
                "templateVersion": template_version,
                "materialVersion": material_version,
                "templateSnapshotID": template_snapshot_id,
                "materialSnapshotID": material_snapshot_id,
                "template": _resource_payload(
                    version=template_version,
                    snapshot_id=template_snapshot_id,
                    format="markdown",
                    path=template_relative,
                    size_bytes=template_download.size_bytes,
                    sha256=template_download.sha256,
                ),
                "materials": _resource_payload(
                    version=material_version,
                    snapshot_id=material_snapshot_id,
                    format="jsonl",
                    path=material_relative,
                    size_bytes=material_download.size_bytes,
                    sha256=material_download.sha256,
                    record_count=material_count,
                ),
            }
        )
        state.report_state = current
        save_session_state(state)

        created_at = utc_now()
        request_payload.update({"requestHash": request_hash, "createdAt": created_at})
        atomic_write_json(request_path, request_payload)
        atomic_write_json(
            context_path,
            {
                "schemaVersion": 1,
                "generationID": action.generation_id,
                "operation": action.operation,
                "language": language,
                "userInstruction": prompt.text,
                "template": current["template"],
                "materials": current["materials"],
                "baseBackendReportVersion": action.base_backend_report_version,
                "effectiveBackendReportVersion": report_version,
                "templateVersion": template_version,
                "materialVersion": material_version,
                **({"baseReport": base_report} if base_report is not None else {}),
                "createdAt": created_at,
            },
        )
        return context_path
