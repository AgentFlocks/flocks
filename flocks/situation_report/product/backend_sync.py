"""Synchronize report, template, and material state by Flocks Session ID."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable, Literal, Optional

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import ParsedReportPrompt, SAFE_IDENTIFIER, SnapshotDownload
from .files import async_file_lock, atomic_write_json, read_json, session_root, utc_now
from .session_state import ensure_session_state, load_session_state, save_session_state
from .snapshots import (
    SnapshotDownloadError,
    SnapshotDownloader,
    _validate_materials,
    _validate_template,
    _verify_file,
    resolve_download_url,
)


class BackendReportSyncError(RuntimeError):
    """The backend latest-state contract could not be satisfied safely."""


class LatestResourceBase(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    exists: bool
    changed: bool
    version: Optional[int] = Field(default=None, ge=0)
    sha256: Optional[str] = None
    size_bytes: Optional[int] = Field(default=None, alias="sizeBytes", gt=0)
    download: Optional[SnapshotDownload] = None

    @model_validator(mode="after")
    def validate_common_state(self) -> "LatestResourceBase":
        if not self.exists:
            if self.changed or any(
                value is not None for value in (self.version, self.sha256, self.size_bytes, self.download)
            ):
                raise ValueError("A missing resource cannot carry version or download fields")
            return self
        if self.version is None or self.sha256 is None:
            raise ValueError("An existing resource requires version and sha256")
        if self.changed and (self.size_bytes is None or self.download is None):
            raise ValueError("A changed resource requires sizeBytes and download")
        normalized = self.sha256.lower()
        if len(normalized) != 64 or any(char not in "0123456789abcdef" for char in normalized):
            raise ValueError("Resource sha256 is invalid")
        self.sha256 = normalized
        return self


class LatestReportResource(LatestResourceBase):
    source: Optional[str] = None

    @model_validator(mode="after")
    def validate_report_limit(self) -> "LatestReportResource":
        if self.size_bytes is not None and self.size_bytes > 10 * 1024 * 1024:
            raise ValueError("Report resource is too large")
        return self


class LatestTemplateResource(LatestResourceBase):
    snapshot_id: Optional[str] = Field(default=None, alias="templateSnapshotID")
    format: Optional[Literal["markdown"]] = None

    @model_validator(mode="after")
    def validate_template_state(self) -> "LatestTemplateResource":
        if self.exists and (self.snapshot_id is None or self.format is None):
            raise ValueError("An existing template requires snapshot ID and format")
        if self.snapshot_id is not None and not SAFE_IDENTIFIER.fullmatch(self.snapshot_id):
            raise ValueError("templateSnapshotID is invalid")
        if self.exists and (self.version is None or self.version < 1):
            raise ValueError("An existing template requires a positive version")
        if self.size_bytes is not None and self.size_bytes > 5 * 1024 * 1024:
            raise ValueError("Template resource is too large")
        return self


class LatestMaterialResource(LatestResourceBase):
    snapshot_id: Optional[str] = Field(default=None, alias="materialSnapshotID")
    format: Optional[Literal["jsonl"]] = None

    @model_validator(mode="after")
    def validate_material_state(self) -> "LatestMaterialResource":
        if self.exists and (self.snapshot_id is None or self.format is None):
            raise ValueError("Existing materials require snapshot ID and format")
        if self.snapshot_id is not None and not SAFE_IDENTIFIER.fullmatch(self.snapshot_id):
            raise ValueError("materialSnapshotID is invalid")
        if self.exists and (self.version is None or self.version < 1):
            raise ValueError("Existing materials require a positive version")
        if self.size_bytes is not None and self.size_bytes > 64 * 1024 * 1024:
            raise ValueError("Material resource is too large")
        return self


class LatestReportStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: LatestReportResource
    template: LatestTemplateResource
    materials: LatestMaterialResource


class BackendReportSynchronizer:
    def __init__(
        self,
        client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
        downloader: Optional[SnapshotDownloader] = None,
    ) -> None:
        self._client_factory = client_factory
        self._downloader = downloader or SnapshotDownloader(client_factory)

    def _client(self) -> httpx.AsyncClient:
        if self._client_factory is not None:
            return self._client_factory()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=False,
        )

    async def get_latest(
        self,
        *,
        session_id: str,
        known_report_version: int,
        known_template_version: int,
        known_material_version: int,
        request_id: str,
    ) -> LatestReportStateResponse:
        url = resolve_download_url(f"/internal/flocks/v1/report-sessions/{session_id}/state/latest")
        headers = {"X-Request-ID": request_id}
        token = os.getenv("SITUATION_REPORT_BACKEND_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        params = {
            "knownReportVersion": known_report_version,
            "knownTemplateVersion": known_template_version,
            "knownMaterialVersion": known_material_version,
        }
        try:
            async with self._client() as client:
                response = await client.get(url, params=params, headers=headers)
                response.raise_for_status()
                return LatestReportStateResponse.model_validate(response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise BackendReportSyncError(f"Latest backend report state check failed: {exc}") from exc

    async def download_changed(
        self,
        *,
        latest: LatestResourceBase,
        destination: Path,
    ) -> None:
        if latest.download is None or latest.size_bytes is None or latest.sha256 is None:
            raise BackendReportSyncError("Changed resource download fields are missing")
        try:
            await self._downloader.download(
                url=latest.download.url,
                expires_at=latest.download.expires_at,
                expected_size=latest.size_bytes,
                expected_sha256=latest.sha256,
                destination=destination,
            )
        except SnapshotDownloadError as exc:
            raise BackendReportSyncError(str(exc)) from exc


def _file_metadata(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    if not content.strip():
        raise BackendReportSyncError("Base report is empty")
    try:
        content.decode("utf-8")
    except UnicodeError as exc:
        raise BackendReportSyncError("Base report is not UTF-8 Markdown") from exc
    return {"sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _validate_version(*, name: str, known_version: int, latest: LatestResourceBase) -> int:
    if not latest.exists or latest.version is None or latest.sha256 is None:
        raise BackendReportSyncError(f"Backend {name} does not exist")
    if latest.version < known_version:
        raise BackendReportSyncError(f"Backend {name} version moved backwards")
    if latest.changed and latest.version <= known_version:
        raise BackendReportSyncError(f"Backend {name} marked a non-newer version as changed")
    if not latest.changed and latest.version != known_version:
        raise BackendReportSyncError(f"Backend {name} changed flag does not match its version")
    return latest.version


def _verified_active_resource(
    *,
    root: Path,
    current: Any,
    name: str,
    version: int,
    snapshot_id: str,
    sha256: str,
) -> tuple[Path, str]:
    if not isinstance(current, dict):
        raise BackendReportSyncError(f"Local {name} state is missing")
    if (
        int(current.get("version") or 0) != version
        or current.get("snapshotID") != snapshot_id
        or current.get("sha256") != sha256
    ):
        raise BackendReportSyncError(f"Local {name} metadata does not match backend latest")
    relative = Path(str(current.get("path") or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise BackendReportSyncError(f"Local {name} path is invalid")
    path = root / relative
    try:
        _verify_file(
            path,
            expected_size=int(current.get("sizeBytes") or 0),
            expected_sha256=sha256,
        )
    except (OSError, SnapshotDownloadError) as exc:
        raise BackendReportSyncError(f"Local {name} verification failed: {exc}") from exc
    return path, str(relative)


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
    """Check all backend versions, then atomically initialize one A1 run."""

    ensure_session_state(session_id)
    action = prompt.action
    root = session_root(session_id)
    request_path = root / "runs" / action.generation_id / "request.json"
    context_path = root / "runs" / action.generation_id / "preprocessing" / "generation_context_001.json"
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
        if action.operation == "generate" and current.get("firstGenerationID") not in {None, action.generation_id}:
            raise BackendReportSyncError("generate is only valid for the Session's first generation")
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

        template_version = _validate_version(
            name="template", known_version=known_template_version, latest=latest.template
        )
        material_version = _validate_version(
            name="materials", known_version=known_material_version, latest=latest.materials
        )
        if latest.report.exists:
            report_version = _validate_version(name="report", known_version=known_report_version, latest=latest.report)
        elif action.operation == "generate" and not latest.report.changed:
            report_version = 0
        else:
            raise BackendReportSyncError("Backend report does not exist")
        if action.operation == "generate" and latest.report.exists:
            raise BackendReportSyncError("generate requires a Session without an existing backend report")
        if action.operation == "modify" and report_version != action.base_backend_report_version:
            raise BackendReportSyncError("Backend report version changed after the modification request was created")

        template = latest.template
        materials = latest.materials
        if template.snapshot_id is None or template.sha256 is None:
            raise BackendReportSyncError("Backend template metadata is incomplete")
        if materials.snapshot_id is None or materials.sha256 is None:
            raise BackendReportSyncError("Backend material metadata is incomplete")

        template_relative = f"templates/snapshots/{template.snapshot_id}/template.md"
        material_relative = f"materials/snapshots/{materials.snapshot_id}/materials.jsonl"
        template_path = root / template_relative
        material_path = root / material_relative
        downloads: list[Any] = []

        if template.changed:
            if template_path.exists():
                _verify_file(
                    template_path,
                    expected_size=int(template.size_bytes or 0),
                    expected_sha256=template.sha256,
                )
            else:
                downloads.append(resolved_sync.download_changed(latest=template, destination=template_path))
        else:
            template_path, template_relative = _verified_active_resource(
                root=root,
                current=current.get("template"),
                name="template",
                version=template_version,
                snapshot_id=template.snapshot_id,
                sha256=template.sha256,
            )

        if materials.changed:
            if material_path.exists():
                _verify_file(
                    material_path,
                    expected_size=int(materials.size_bytes or 0),
                    expected_sha256=materials.sha256,
                )
            else:
                downloads.append(resolved_sync.download_changed(latest=materials, destination=material_path))
        else:
            material_path, material_relative = _verified_active_resource(
                root=root,
                current=current.get("materials"),
                name="materials",
                version=material_version,
                snapshot_id=materials.snapshot_id,
                sha256=materials.sha256,
            )

        report_path: Optional[Path] = None
        report_relative: Optional[str] = None
        if action.operation == "modify":
            if latest.report.sha256 is None:
                raise BackendReportSyncError("Backend report metadata is incomplete")
            if latest.report.changed:
                report_relative = f"input/backend-reports/{report_version}/report.md"
                report_path = root / report_relative
                if report_path.exists():
                    if _file_metadata(report_path)["sha256"] != latest.report.sha256:
                        raise BackendReportSyncError("Existing immutable backend report is inconsistent")
                else:
                    downloads.append(resolved_sync.download_changed(latest=latest.report, destination=report_path))
            else:
                report_relative = str(current.get("syncedBackendReportPath") or "output/report.md")
                report_path = root / report_relative
                if not report_path.is_file():
                    raise BackendReportSyncError("No local report baseline is available for modification")

        if downloads:
            results = await asyncio.gather(*downloads, return_exceptions=True)
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                first = failures[0]
                if isinstance(first, BackendReportSyncError):
                    raise first
                raise BackendReportSyncError(
                    f"Backend resource download failed: {type(first).__name__}: {first}"
                ) from first

        try:
            _validate_template(template_path)
            template_size = template_path.stat().st_size
            _verify_file(template_path, expected_size=template_size, expected_sha256=template.sha256)
            material_count = _validate_materials(material_path)
            material_size = material_path.stat().st_size
            _verify_file(material_path, expected_size=material_size, expected_sha256=materials.sha256)
        except (OSError, SnapshotDownloadError) as exc:
            raise BackendReportSyncError(f"Downloaded resource validation failed: {exc}") from exc

        base_report: Optional[dict[str, Any]] = None
        if action.operation == "modify":
            assert report_path is not None and report_relative is not None and latest.report.sha256 is not None
            report_metadata = _file_metadata(report_path)
            if report_metadata["sha256"] != latest.report.sha256:
                raise BackendReportSyncError("Local base report does not match backend latest SHA-256")
            current["syncedBackendReportVersion"] = report_version
            current["syncedBackendReportPath"] = report_relative
            base_report = {
                "backendReportVersion": report_version,
                "path": report_relative,
                **report_metadata,
            }

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
                "templateSnapshotID": template.snapshot_id,
                "materialSnapshotID": materials.snapshot_id,
                "template": _resource_payload(
                    version=template_version,
                    snapshot_id=template.snapshot_id,
                    format="markdown",
                    path=template_relative,
                    size_bytes=template_size,
                    sha256=template.sha256,
                ),
                "materials": _resource_payload(
                    version=material_version,
                    snapshot_id=materials.snapshot_id,
                    format="jsonl",
                    path=material_relative,
                    size_bytes=material_size,
                    sha256=materials.sha256,
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
