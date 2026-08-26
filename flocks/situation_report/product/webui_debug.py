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
