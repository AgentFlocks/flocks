"""Development-only WebUI bridge for managed report Sessions.

This module deliberately keeps the debug backend separate from the business
backend configuration.  A Session must carry the explicit metadata marker
before any debug behavior is enabled.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from flocks.session.session import SessionInfo

from .backend_sync import BackendReportSyncError, BackendReportSynchronizer
from .debug_datasets import LoadedDebugDataset


WEBUI_DEBUG_METADATA_KEY = "situationReportWebUIDebug"


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def webui_debug_enabled() -> bool:
    return _enabled(os.getenv("SITUATION_REPORT_WEBUI_DEBUG_ENABLED"))


def webui_debug_metadata() -> dict[str, Any]:
    return {
        WEBUI_DEBUG_METADATA_KEY: {
            "schemaVersion": 1,
            "backend": "development-mock",
        }
    }


def is_webui_debug_session(session: SessionInfo) -> bool:
    metadata = session.metadata if isinstance(session.metadata, dict) else {}
    marker = metadata.get(WEBUI_DEBUG_METADATA_KEY)
    return (
        session.category == "situation-report"
        and isinstance(marker, dict)
        and marker.get("schemaVersion") == 1
        and marker.get("backend") == "development-mock"
    )


def _debug_backend_config() -> tuple[str, str]:
    if not webui_debug_enabled():
        raise BackendReportSyncError("Situation-report WebUI debug mode is disabled")
    base_url = os.getenv("SITUATION_REPORT_WEBUI_DEBUG_BACKEND_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("SITUATION_REPORT_WEBUI_DEBUG_BACKEND_TOKEN", "").strip()
    if not base_url or not token:
        raise BackendReportSyncError("Situation-report WebUI debug backend is not configured")
    return base_url, token


def build_webui_debug_synchronizer() -> BackendReportSynchronizer:
    base_url, token = _debug_backend_config()
    return BackendReportSynchronizer(
        lambda: httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=False,
            trust_env=False,
        ),
        base_url=base_url,
        token=token,
    )


async def seed_webui_debug_dataset(
    *,
    session_id: str,
    dataset: LoadedDebugDataset,
    client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
) -> dict[str, Any]:
    """Install one validated frozen dataset into a new Mock-backed Session."""

    base_url, token = _debug_backend_config()
    headers = {"Authorization": f"Bearer {token}"}
    try:
        client_context = (
            client_factory()
            if client_factory is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=10.0),
                follow_redirects=False,
                trust_env=False,
            )
        )
        async with client_context as client:
            state_response = await client.get(
                f"{base_url}/__mock__/report-sessions/{session_id}/state",
                headers=headers,
            )
            state_response.raise_for_status()
            state = state_response.json()
            if (state.get("report") or {}).get("exists"):
                raise BackendReportSyncError(
                    "A frozen dataset cannot replace a debug Session that already has a report"
                )
            template_version = int((state.get("template") or {}).get("version") or 0) + 1
            material_version = int((state.get("materials") or {}).get("version") or 0) + 1

            material_response = await client.put(
                f"{base_url}/__mock__/report-sessions/{session_id}/resources/materials",
                params={"version": material_version},
                headers=headers,
                content=dataset.materials,
            )
            material_response.raise_for_status()
            material_value = material_response.json()
            if material_value.get("sha256") != dataset.manifest.materials_sha256:
                raise BackendReportSyncError("Debug backend stored an unexpected material digest")

            details_response = await client.put(
                f"{base_url}/__mock__/report-sessions/{session_id}/materials/details",
                params={"materialVersion": material_version},
                headers=headers,
                content=dataset.material_details,
            )
            details_response.raise_for_status()
            details_value = details_response.json()
            if details_value.get("sha256") != dataset.manifest.material_details_sha256:
                raise BackendReportSyncError("Debug backend stored an unexpected material-detail digest")

            template_response = await client.put(
                f"{base_url}/__mock__/report-sessions/{session_id}/resources/template",
                params={"version": template_version},
                headers=headers,
                content=dataset.template,
            )
            template_response.raise_for_status()
            template_value = template_response.json()
            if template_value.get("sha256") != dataset.manifest.template_sha256:
                raise BackendReportSyncError("Debug backend stored an unexpected template digest")
    except BackendReportSyncError:
        raise
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise BackendReportSyncError(f"Debug dataset installation failed: {exc}") from exc

    return {
        "datasetID": dataset.manifest.dataset_id,
        "templateVersion": template_version,
        "materialVersion": material_version,
        "materialCount": dataset.manifest.material_count,
        "materialDetailCount": dataset.manifest.material_detail_count,
        "language": dataset.manifest.language,
    }


async def publish_webui_debug_report(
    *,
    session_id: str,
    report_path: Path,
    client_factory: Optional[Callable[[], httpx.AsyncClient]] = None,
) -> int:
    """Publish one immutable generated report version to the debug Mock.

    Re-reading the current state before writing makes retries safe at the
    operation boundary.  The Mock remains the authority for the next backend
    version used by modify/regenerate requests.
    """

    base_url, token = _debug_backend_config()
    content = report_path.read_bytes()
    if not content.strip():
        raise BackendReportSyncError("Generated debug report is empty")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        client_context = (
            client_factory()
            if client_factory is not None
            else httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=10.0),
                follow_redirects=False,
                trust_env=False,
            )
        )
        async with client_context as client:
            state_response = await client.get(
                f"{base_url}/__mock__/report-sessions/{session_id}/state",
                headers=headers,
            )
            state_response.raise_for_status()
            report = state_response.json().get("report") or {}
            current_version = int(report.get("version") or 0) if report.get("exists") else 0
            next_version = current_version + 1
            response = await client.put(
                f"{base_url}/__mock__/report-sessions/{session_id}/resources/report",
                params={"version": next_version},
                headers=headers,
                content=content,
            )
            response.raise_for_status()
            published = response.json()
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        raise BackendReportSyncError(f"Debug report publication failed: {exc}") from exc

    if int(published.get("version") or 0) != next_version:
        raise BackendReportSyncError("Debug backend returned an unexpected report version")
    if published.get("sha256") != hashlib.sha256(content).hexdigest():
        raise BackendReportSyncError("Debug backend report digest does not match the generated output")
    return next_version
