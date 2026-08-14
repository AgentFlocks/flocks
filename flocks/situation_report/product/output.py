"""Crash-recoverable immutable report publication."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from .files import async_file_lock, atomic_write_bytes, atomic_write_json, read_json, session_root, utc_now
from .session_state import load_session_state, save_session_state


class ReportPublicationError(RuntimeError):
    """A candidate is absent, stale, invalid, or cannot be published safely."""


def file_info(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {"sizeBytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _next_version(versions_dir: Path) -> str:
    maximum = 0
    if versions_dir.exists():
        for child in versions_dir.iterdir():
            match = re.fullmatch(r"frv_(\d{3,})", child.name)
            if match:
                maximum = max(maximum, int(match.group(1)))
    return f"frv_{maximum + 1:03d}"


async def publish_validated_candidate(
    *,
    session_id: str,
    generation_id: str,
) -> dict[str, Any]:
    """Publish a validated candidate in a fixed, idempotent success sequence."""

    workspace_dir = session_root(session_id)
    run_dir = workspace_dir / "runs" / generation_id
    candidate = workspace_dir / "work" / generation_id / "report.md"
    validation_path = run_dir / "validation.json"
    finalization_path = run_dir / "finalization.json"
    async with async_file_lock(workspace_dir / ".locks" / "write.lock"):
        if finalization_path.exists():
            completed = read_json(finalization_path)
            if completed.get("status") == "succeeded":
                return completed
        if not candidate.is_file() or not validation_path.is_file():
            raise ReportPublicationError("Candidate report or validation result is missing")
        validation = read_json(validation_path)
        candidate_info = file_info(candidate)
        if validation.get("status") != "passed":
            raise ReportPublicationError("Candidate report validation did not pass")
        if validation.get("candidateSHA256") != candidate_info["sha256"]:
            raise ReportPublicationError("Candidate changed after validation")

        request = read_json(run_dir / "request.json")
        state = load_session_state(session_id)
        report_state = state.report_state
        if not isinstance(report_state, dict):
            raise ReportPublicationError("Report Session state is missing")

        if finalization_path.exists():
            finalization = read_json(finalization_path)
            version = str(finalization.get("flocksReportVersion") or "")
            if not re.fullmatch(r"frv_\d{3,}", version):
                raise ReportPublicationError("Finalization recovery record is invalid")
        else:
            version = _next_version(workspace_dir / "output" / "versions")
            finalization = {
                "schemaVersion": 1,
                "generationID": generation_id,
                "flocksReportVersion": version,
                "status": "publishing",
                "candidateSHA256": candidate_info["sha256"],
                "createdAt": utc_now(),
            }
            atomic_write_json(finalization_path, finalization)

        version_dir = workspace_dir / "output" / "versions" / version
        immutable_report = version_dir / "report.md"
        if immutable_report.exists():
            if file_info(immutable_report) != candidate_info:
                raise ReportPublicationError("Immutable output version already contains different bytes")
        else:
            atomic_write_bytes(immutable_report, candidate.read_bytes())
        context = read_json(run_dir / "preprocessing" / "generation_context_001.json")
        context_template = context.get("template") or {}
        context_materials = context.get("materials") or {}
        metadata = {
            "schemaVersion": 1,
            "status": "succeeded",
            "generationID": generation_id,
            "requestID": request.get("requestID"),
            "operation": request.get("operation"),
            "baseBackendReportVersion": context.get("baseBackendReportVersion"),
            "effectiveBackendReportVersion": context.get("effectiveBackendReportVersion"),
            "language": context.get("language"),
            "templateVersion": context.get("templateVersion") or context_template.get("version"),
            "materialVersion": context.get("materialVersion") or context_materials.get("version"),
            "templateSnapshotID": context_template.get("snapshotID"),
            "materialSnapshotID": context_materials.get("snapshotID"),
            "flocksReportVersion": version,
            "output": {
                "path": f"output/versions/{version}/report.md",
                "format": "markdown",
                **candidate_info,
            },
            "publishedAt": utc_now(),
        }
        atomic_write_json(version_dir / "metadata.json", metadata)
        atomic_write_bytes(workspace_dir / "output" / "report.md", candidate.read_bytes())
        report_state["currentFlocksReportVersion"] = version
        report_state["currentOutputPath"] = f"output/versions/{version}/report.md"
        state.report_state = report_state
        save_session_state(state)
        atomic_write_json(
            workspace_dir / "output" / "status.json",
            {
                "generationID": generation_id,
                "operation": request.get("operation"),
                "status": "succeeded",
                "stage": "output_ready",
                "progress": 100,
                "flocksReportVersion": version,
                "output": metadata["output"],
                "updatedAt": utc_now(),
            },
        )
        finalization.update(
            {
                "status": "succeeded",
                "output": metadata["output"],
                "completedAt": utc_now(),
            }
        )
        atomic_write_json(finalization_path, finalization)
        return finalization
