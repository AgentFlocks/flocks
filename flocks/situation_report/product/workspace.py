"""Restricted A1 workspace operations used by the production report Agent."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from flocks.session.session import Session

from .contracts import SAFE_IDENTIFIER
from .files import async_file_lock, atomic_write_bytes, atomic_write_json, read_json, session_root, utc_now
from .session_state import ReportSessionStateError, load_session_state


class ProductWorkspaceError(RuntimeError):
    """A production Agent attempted an invalid or stale workspace operation."""


def _material_id(value: dict[str, Any]) -> str:
    source_type = value.get("source_type")
    source_id = value.get("source_id")
    if source_type not in {"REPORT", "VULN", "DARKWEB", "TELEGRAM"}:
        raise ProductWorkspaceError("Material has an invalid source_type")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ProductWorkspaceError("Material has an invalid source_id")
    return f"{source_type}:{source_id}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _resolve_run(session_id: str, generation_id: str) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    if not SAFE_IDENTIFIER.fullmatch(generation_id):
        raise ProductWorkspaceError("generation_id is invalid")
    session = await Session.get_by_id(session_id)
    if session is None:
        raise ProductWorkspaceError("Session was not found")
    try:
        state = load_session_state(session_id)
    except ReportSessionStateError as exc:
        raise ProductWorkspaceError(str(exc)) from exc
    workspace_dir = session_root(session_id)
    request_path = workspace_dir / "runs" / generation_id / "request.json"
    if not request_path.is_file():
        raise ProductWorkspaceError("Generation request was not initialized")
    request = read_json(request_path)
    index = read_json(workspace_dir / "index.json")
    if request.get("generationID") != generation_id:
        raise ProductWorkspaceError("Generation request identity is inconsistent")
    if index.get("sessionID") != state.session_id:
        raise ProductWorkspaceError("Report workspace identity is inconsistent")
    return workspace_dir, request, index


def _load_generation_context(workspace_dir: Path, generation_id: str) -> dict[str, Any]:
    context_path = workspace_dir / "runs" / generation_id / "preprocessing" / "generation_context_001.json"
    if not context_path.is_file():
        raise ProductWorkspaceError("Generation context was not initialized")
    context = read_json(context_path)
    if context.get("generationID") != generation_id:
        raise ProductWorkspaceError("Generation context identity is inconsistent")
    return context


def _verified_context_file(workspace_dir: Path, metadata: Any, label: str) -> Path:
    if not isinstance(metadata, dict):
        raise ProductWorkspaceError(f"{label} metadata is missing")
    relative = Path(str(metadata.get("path") or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ProductWorkspaceError(f"{label} path is invalid")
    candidate = workspace_dir
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ProductWorkspaceError(f"{label} cannot use symlinks")
    path = candidate.resolve()
    try:
        path.relative_to(workspace_dir.resolve())
    except ValueError as exc:
        raise ProductWorkspaceError(f"{label} path escapes the report workspace") from exc
    if (
        not path.is_file()
        or path.stat().st_size != metadata.get("sizeBytes")
        or file_sha256(path) != metadata.get("sha256")
    ):
        raise ProductWorkspaceError(f"{label} verification failed")
    return path


async def read_generation_context(*, session_id: str, generation_id: str) -> dict[str, Any]:
    workspace_dir, request, _ = await _resolve_run(session_id, generation_id)
    context = _load_generation_context(workspace_dir, generation_id)
    template_info = context.get("template")
    template_path = _verified_context_file(workspace_dir, template_info, "Template snapshot")
    session = await Session.get_by_id(session_id)
    result = {
        "generationID": generation_id,
        "operation": request.get("operation"),
        "reportTitle": session.title if session is not None else None,
        "userInstruction": context.get("userInstruction"),
        "language": context.get("language"),
        "template": template_path.read_text(encoding="utf-8"),
        "materialCount": (context.get("materials") or {}).get("recordCount"),
        "baseReportAvailable": bool(context.get("baseReport")),
        "validationPolicy": {
            "oneH1": True,
            "preserveTemplateH2": True,
            "citeEveryMaterialID": True,
            "maxValidationAttempts": 3,
        },
    }
    base_report = context.get("baseReport")
    if isinstance(base_report, dict) and base_report.get("path"):
        base_path = _verified_context_file(workspace_dir, base_report, "Base report")
        result["baseReport"] = base_path.read_text(encoding="utf-8")
    return result


async def read_material_page(
    *,
    session_id: str,
    generation_id: str,
    offset: int = 0,
    limit: int = 20,
) -> dict[str, Any]:
    if offset < 0 or limit < 1 or limit > 50:
        raise ProductWorkspaceError("offset must be >= 0 and limit must be between 1 and 50")
    workspace_dir, _, _ = await _resolve_run(session_id, generation_id)
    context = _load_generation_context(workspace_dir, generation_id)
    materials_info = context.get("materials")
    materials_path = _verified_context_file(
        workspace_dir,
        materials_info,
        "Material snapshot",
    )
    rows: list[dict[str, Any]] = []
    with materials_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ProductWorkspaceError("Material snapshot contains a non-object record")
                value = {**value, "material_id": _material_id(value)}
                rows.append(value)
    selected = rows[offset : offset + limit]
    return {
        "offset": offset,
        "limit": limit,
        "total": len(rows),
        "hasMore": offset + len(selected) < len(rows),
        "nextOffset": offset + len(selected),
        "materials": selected,
    }


async def read_embedded_source(
    *,
    session_id: str,
    generation_id: str,
    material_id: str,
    reason: str,
) -> dict[str, Any]:
    if not reason.strip():
        raise ProductWorkspaceError("A specific conflict reason is required")
    page = await read_material_page(
        session_id=session_id,
        generation_id=generation_id,
        offset=0,
        limit=50,
    )
    rows = page["materials"]
    while page["hasMore"]:
        page = await read_material_page(
            session_id=session_id,
            generation_id=generation_id,
            offset=page["nextOffset"],
            limit=50,
        )
        rows.extend(page["materials"])
    matches = [row for row in rows if _material_id(row) == material_id]
    if len(matches) != 1:
        raise ProductWorkspaceError("material_id does not identify exactly one declared material")
    source_record = matches[0].get("source_record")
    if not isinstance(source_record, dict):
        raise ProductWorkspaceError(
            "The verified material snapshot does not embed this original source record; "
            "the conflict cannot be resolved safely"
        )
    expected_hash = str(matches[0].get("source_record_sha256") or "")
    packed = json.dumps(source_record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    actual_hash = hashlib.sha256(packed.encode("utf-8")).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
        raise ProductWorkspaceError("Embedded source record is missing a valid SHA-256")
    if actual_hash != expected_hash:
        raise ProductWorkspaceError("Embedded source record SHA-256 mismatch")
    return {
        "materialID": material_id,
        "reason": reason,
        "sourceSHA256": actual_hash,
        "sourceRecord": source_record,
    }


async def write_candidate_report(
    *,
    session_id: str,
    generation_id: str,
    content: str,
    expected_sha256: str = "",
) -> dict[str, Any]:
    encoded = content.strip().encode("utf-8")
    if not encoded or len(encoded) > 10 * 1024 * 1024:
        raise ProductWorkspaceError("Candidate report must be non-empty and at most 10 MiB")
    workspace_dir, _, _ = await _resolve_run(session_id, generation_id)
    path = workspace_dir / "work" / generation_id / "report.md"
    async with async_file_lock(workspace_dir / ".locks" / "write.lock"):
        if path.exists():
            current_hash = file_sha256(path)
            if not expected_sha256 or current_hash != expected_sha256:
                raise ProductWorkspaceError("Candidate report changed; expected_sha256 is required")
        atomic_write_bytes(path, encoded + b"\n")
    return {
        "generationID": generation_id,
        "path": f"work/{generation_id}/report.md",
        "sizeBytes": path.stat().st_size,
        "sha256": file_sha256(path),
    }


def _template_h2(template: str) -> list[str]:
    structured = re.findall(r"^\d+\.\s+\*\*([^*]+)\*\*", template, flags=re.MULTILINE)
    if structured:
        return [re.sub(r"（.*$", "", heading).strip() for heading in structured]
    return [line[3:].strip() for line in template.splitlines() if line.startswith("## ")]


async def validate_candidate_report(*, session_id: str, generation_id: str) -> dict[str, Any]:
    workspace_dir, _, _ = await _resolve_run(session_id, generation_id)
    candidate_path = workspace_dir / "work" / generation_id / "report.md"
    if not candidate_path.is_file():
        raise ProductWorkspaceError("Candidate report was not written")
    report = candidate_path.read_text(encoding="utf-8")
    context = _load_generation_context(workspace_dir, generation_id)
    template_info = context.get("template") or {}
    materials_info = context.get("materials") or {}
    template_path = _verified_context_file(
        workspace_dir,
        template_info,
        "Template snapshot",
    )
    materials_path = _verified_context_file(
        workspace_dir,
        materials_info,
        "Material snapshot",
    )
    template = template_path.read_text(encoding="utf-8")
    material_ids = [
        _material_id(value)
        for value in (
            json.loads(line) for line in materials_path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    ]
    h1_lines = [line for line in report.splitlines() if line.startswith("# ")]
    report_h2 = [line[3:].strip() for line in report.splitlines() if line.startswith("## ")]
    missing_headings = [
        heading for heading in _template_h2(template) if not any(heading in actual for actual in report_h2)
    ]
    missing_material_ids = [material_id for material_id in material_ids if material_id and material_id not in report]
    internal_markers = (
        "generation_context_",
        f"work/{generation_id}/report.md",
        "templates/snapshots/",
        "materials/snapshots/",
    )
    leaked_internal_markers = sorted(marker for marker in internal_markers if marker in report)
    issues: list[dict[str, Any]] = []
    if len(h1_lines) != 1:
        issues.append({"code": "h1_count", "detail": f"Expected one H1, found {len(h1_lines)}"})
    if missing_headings:
        issues.append({"code": "template_headings", "missing": missing_headings})
    if missing_material_ids:
        issues.append({"code": "material_evidence", "missing": missing_material_ids})
    if leaked_internal_markers:
        issues.append({"code": "internal_path_leakage", "markers": leaked_internal_markers})
    if re.search(r"^```(?:markdown|md)?\s*$", report, flags=re.MULTILINE):
        issues.append({"code": "markdown_fence", "detail": "Do not wrap the complete report in a fence"})

    validation_path = workspace_dir / "runs" / generation_id / "validation.json"
    previous_attempts = 0
    if validation_path.exists():
        previous_attempts = int(read_json(validation_path).get("attempt") or 0)
    attempt = previous_attempts + 1
    if attempt > 3:
        raise ProductWorkspaceError("Validation attempt budget is exhausted")
    result = {
        "schemaVersion": 1,
        "generationID": generation_id,
        "status": "passed" if not issues else "needs_revision",
        "attempt": attempt,
        "candidateSHA256": file_sha256(candidate_path),
        "issues": issues,
        "validatedAt": utc_now(),
    }
    atomic_write_json(validation_path, result)
    return result
