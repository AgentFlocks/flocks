"""Restricted A1 workspace operations used by the production report Agent."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from flocks.session.session import Session

from .backend_sync import (
    BackendReportSyncError,
    BackendReportSynchronizer,
    MATERIAL_DETAIL_FIELDS,
    MaterialSourceType,
)
from .contracts import SAFE_IDENTIFIER
from .files import async_file_lock, atomic_write_bytes, atomic_write_json, read_json, session_root, utc_now
from .session_state import ReportSessionStateError, load_session_state


class ProductWorkspaceError(RuntimeError):
    """A production Agent attempted an invalid or stale workspace operation."""


_SELECTION_ONLY_MATERIAL_FIELDS = frozenset({
    "matched_factors",
    "pirs_id",
    "saved",
    "tier",
})


def _material_id(value: dict[str, Any]) -> str:
    source_type = value.get("source_type")
    source_id = value.get("source_id")
    if source_type not in {"REPORT", "VULN", "DARKWEB", "TELEGRAM"}:
        raise ProductWorkspaceError("Material has an invalid source_type")
    if not isinstance(source_id, str) or not source_id.strip():
        raise ProductWorkspaceError("Material has an invalid source_id")
    return f"{source_type}:{source_id.strip()}"


def _timestamp_iso_utc(value: Any) -> str | None:
    """Expose backend millisecond timestamps in a form models need not calculate."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        converted = datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
    return converted.isoformat(timespec="seconds").replace("+00:00", "Z")


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
    template = template_path.read_text(encoding="utf-8")
    required_headings = _template_h2(template)
    result = {
        "generationID": generation_id,
        "operation": request.get("operation"),
        "userInstruction": context.get("userInstruction"),
        "language": context.get("language"),
        "template": template,
        "templateContract": {
            "authority": "session_template_snapshot",
            "requiredH2": required_headings,
            "headingOrder": "template_order",
            "prohibitedLiterals": _template_prohibited_literals(template),
            "instruction": (
                "The complete template field is the authoritative specification for report "
                "structure, section content, formatting, counts, empty states, style, and "
                "prohibited expressions. Do not substitute rules from a built-in or previously "
                "seen template."
            ),
        },
        "materialCount": (context.get("materials") or {}).get("recordCount"),
        "baseReportAvailable": bool(context.get("baseReport")),
        "validationPolicy": {
            "reportTitleAllowed": False,
            "h1Count": 0,
            "preserveTemplateH2": True,
            "materialIDInReportAllowed": False,
            "evidenceMode": "internal_sidecar",
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
                value = {
                    **{
                        key: field_value
                        for key, field_value in value.items()
                        if key not in _SELECTION_ONLY_MATERIAL_FIELDS
                    },
                    "material_id": _material_id(value),
                    "published_at_iso_utc": _timestamp_iso_utc(value.get("published_at")),
                    "content_updated_at_iso_utc": _timestamp_iso_utc(
                        value.get("content_updated_at")
                    ),
                    "source_updated_at_iso_utc": _timestamp_iso_utc(
                        value.get("source_updated_at")
                    ),
                }
                rows.append(value)
    selected = rows[offset : offset + limit]
    source_type_counts: dict[str, int] = {}
    for row in rows:
        source_type = str(row["source_type"])
        source_type_counts[source_type] = source_type_counts.get(source_type, 0) + 1
    return {
        "offset": offset,
        "limit": limit,
        "total": len(rows),
        "hasMore": offset + len(selected) < len(rows),
        "nextOffset": offset + len(selected),
        "deterministicCounts": {
            "totalMaterials": len(rows),
            "bySourceType": source_type_counts,
        },
        "fieldSemantics": {
            "authoritativeEventFacts": [
                "title",
                "summary",
                "report",
                "vulnerability",
                "darkweb",
                "telegram",
                "detail returned by situation_product_source_read",
            ],
            "omittedSelectionMetadata": sorted(_SELECTION_ONLY_MATERIAL_FIELDS),
            "rule": (
                "Selection metadata explains why a record was selected or ranked; it is not "
                "evidence that a matched entity is the victim, actor, or subject of the event."
            ),
        },
        "materials": selected,
    }


async def read_material_detail(
    *,
    session_id: str,
    generation_id: str,
    material_id: str,
    reason: str,
    synchronizer: BackendReportSynchronizer | None = None,
) -> dict[str, Any]:
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ProductWorkspaceError("A specific conflict reason is required")
    if len(normalized_reason) > 2_000:
        raise ProductWorkspaceError("The conflict reason is too long")
    workspace_dir, request, _ = await _resolve_run(session_id, generation_id)
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
    material = matches[0]
    source_type = str(material["source_type"])
    source_id = str(material["source_id"]).strip()
    cache_key = hashlib.sha256(material_id.encode("utf-8")).hexdigest()
    cache_path = (
        workspace_dir
        / "runs"
        / generation_id
        / "evidence"
        / "material-details"
        / f"{cache_key}.json"
    )
    audit_path = workspace_dir / "runs" / generation_id / "audit" / "source_reads.jsonl"
    lock_path = workspace_dir / ".locks" / "source-read.lock"

    async with async_file_lock(lock_path):
        if cache_path.is_file():
            cached = read_json(cache_path)
            if (
                cached.get("materialID") != material_id
                or cached.get("sourceType") != source_type
                or cached.get("sourceId") != source_id
                or not isinstance(cached.get("detail"), dict)
            ):
                raise ProductWorkspaceError("Cached material detail identity is inconsistent")
            detail_payload = cached["detail"]
            detail_hash = str(cached.get("detailSHA256") or "")
            packed = json.dumps(
                detail_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if hashlib.sha256(packed.encode("utf-8")).hexdigest() != detail_hash:
                raise ProductWorkspaceError("Cached material detail SHA-256 mismatch")
            cache_hit = True
        else:
            session = await Session.get_by_id(session_id)
            if session is None:
                raise ProductWorkspaceError("Session was not found")
            from .webui_debug import (
                build_webui_debug_synchronizer,
                is_webui_debug_session,
            )

            resolved_synchronizer = synchronizer or (
                build_webui_debug_synchronizer()
                if is_webui_debug_session(session)
                else BackendReportSynchronizer()
            )
            request_seed = f"{request.get('requestID', '')}:{material_id}"
            detail_request_id = (
                "detail_"
                + hashlib.sha256(request_seed.encode("utf-8")).hexdigest()[:24]
            )
            try:
                detail = await resolved_synchronizer.get_material_detail(
                    session_id=session_id,
                    source_type=cast(MaterialSourceType, source_type),
                    source_id=source_id,
                    request_id=detail_request_id,
                )
            except BackendReportSyncError as exc:
                raise ProductWorkspaceError(
                    f"Selected material detail is unavailable: {exc}"
                ) from exc
            detail_payload = detail.model_dump(exclude_none=True)
            packed = json.dumps(
                detail_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            detail_hash = hashlib.sha256(packed.encode("utf-8")).hexdigest()
            atomic_write_json(
                cache_path,
                {
                    "schemaVersion": 1,
                    "materialID": material_id,
                    "sourceType": source_type,
                    "sourceId": source_id,
                    "detailSHA256": detail_hash,
                    "detail": detail_payload,
                    "retrievedAt": utc_now(),
                },
            )
            cache_hit = False

        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            os.chmod(audit_path, 0o600)
            handle.write(
                json.dumps(
                    {
                        "event": "material_detail_read",
                        "generationID": generation_id,
                        "materialID": material_id,
                        "reason": normalized_reason,
                        "detailSHA256": detail_hash,
                        "cacheHit": cache_hit,
                        "time": utc_now(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())

    detail_field = MATERIAL_DETAIL_FIELDS[source_type]
    return {
        "materialID": material_id,
        "reason": normalized_reason,
        "detailSHA256": detail_hash,
        "sourceType": source_type,
        "sourceId": source_id,
        "detailType": detail_field,
        "detail": detail_payload[detail_field],
        "cacheHit": cache_hit,
    }


async def write_candidate_report(
    *,
    session_id: str,
    generation_id: str,
    content: str,
    evidence_map: dict[str, list[str]],
    expected_sha256: str = "",
) -> dict[str, Any]:
    encoded = content.strip().encode("utf-8")
    if not encoded or len(encoded) > 10 * 1024 * 1024:
        raise ProductWorkspaceError("Candidate report must be non-empty and at most 10 MiB")
    workspace_dir, _, _ = await _resolve_run(session_id, generation_id)
    context = _load_generation_context(workspace_dir, generation_id)
    materials_path = _verified_context_file(
        workspace_dir,
        context.get("materials"),
        "Material snapshot",
    )
    declared_material_ids = {
        _material_id(json.loads(line))
        for line in materials_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    if not isinstance(evidence_map, dict):
        raise ProductWorkspaceError("evidence_map must be an object")
    normalized_evidence: dict[str, list[str]] = {}
    for material_id, sections in evidence_map.items():
        if material_id not in declared_material_ids:
            raise ProductWorkspaceError(
                f"evidence_map contains an undeclared material_id: {material_id}"
            )
        if not isinstance(sections, list) or not sections:
            raise ProductWorkspaceError(
                f"evidence_map sections must be a non-empty list for {material_id}"
            )
        normalized_sections: list[str] = []
        for section in sections:
            if not isinstance(section, str) or not section.strip():
                raise ProductWorkspaceError(
                    f"evidence_map contains an invalid section for {material_id}"
                )
            normalized = section.strip()
            if len(normalized) > 200:
                raise ProductWorkspaceError("evidence_map section names are too long")
            if normalized not in normalized_sections:
                normalized_sections.append(normalized)
        normalized_evidence[material_id] = normalized_sections
    path = workspace_dir / "work" / generation_id / "report.md"
    evidence_path = workspace_dir / "work" / generation_id / "evidence.json"
    async with async_file_lock(workspace_dir / ".locks" / "write.lock"):
        if path.exists():
            current_hash = file_sha256(path)
            if not expected_sha256 or current_hash != expected_sha256:
                raise ProductWorkspaceError("Candidate report changed; expected_sha256 is required")
        atomic_write_bytes(path, encoded + b"\n")
        atomic_write_json(
            evidence_path,
            {
                "schemaVersion": 1,
                "generationID": generation_id,
                "materials": normalized_evidence,
            },
        )
    return {
        "generationID": generation_id,
        "path": f"work/{generation_id}/report.md",
        "sizeBytes": path.stat().st_size,
        "sha256": file_sha256(path),
        "evidenceSHA256": file_sha256(evidence_path),
    }


def _template_h2(template: str) -> list[str]:
    structured = re.findall(r"^\d+\.\s+\*\*([^*]+)\*\*", template, flags=re.MULTILINE)
    if structured:
        return [
            re.sub(
                r"\s*[（(](?:必有|可空|可选|required|optional)[）)]\s*$",
                "",
                heading,
                flags=re.IGNORECASE,
            ).strip()
            for heading in structured
        ]
    return [line[3:].strip() for line in template.splitlines() if line.startswith("## ")]


def _template_prohibited_literals(template: str) -> list[str]:
    """Extract explicit literal bans without assuming a particular report template."""

    groups = re.findall(r"禁止[「『“]([^」』”]+)[」』”]", template)
    groups.extend(
        match.group(1)
        for match in re.finditer(
            r"(?:正文|报告正文)禁止\s+([^\n；。]+)",
            template,
            flags=re.IGNORECASE,
        )
    )
    literals: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in re.split(r"\s*(?:/|、|,|，|\|)\s*", group):
            normalized = value.strip().strip("`'\" ")
            if not normalized:
                continue
            identity = normalized.casefold()
            if identity in seen:
                continue
            seen.add(identity)
            literals.append(normalized)
    return literals


def _heading_sequence_issue(expected: list[str], actual: list[str]) -> dict[str, Any] | None:
    """Describe report H2 drift without assuming any particular report template."""

    missing = [heading for heading in expected if heading not in actual]
    unexpected = [heading for heading in actual if heading not in expected]
    expected_present = [heading for heading in expected if heading in actual]
    actual_expected = [heading for heading in actual if heading in expected]
    out_of_order = expected_present != actual_expected
    if not missing and not unexpected and not out_of_order:
        return None
    issue: dict[str, Any] = {
        "code": "template_headings",
        "expected": expected,
        "actual": actual,
    }
    if missing:
        issue["missing"] = missing
    if unexpected:
        issue["unexpected"] = unexpected
    if out_of_order:
        issue["outOfOrder"] = True
    return issue


def _declared_group_count_issues(report: str) -> list[dict[str, Any]]:
    """Check list counts explicitly declared by report subheadings."""

    lines = report.splitlines()
    issues: list[dict[str, Any]] = []
    heading_pattern = re.compile(
        r"^###\s+(.+?)[（(]\s*(\d+)\s*(?:起|条|项|个|events?|items?|records?)\s*[）)]\s*$",
        flags=re.IGNORECASE,
    )
    item_patterns = (
        re.compile(r"^\s*[-*]\s+\*\*标题\*\*[：:]"),
        re.compile(r"^\s*\d+[.、]\s+\*\*标题(?:\*\*)?[：:]"),
        re.compile(r"^\s*\d+[.、]\s+\*\*.+\*\*"),
        re.compile(r"^\s*\*\*\d+[.、]\s+.+\*\*"),
    )

    def event_table_counts(block: list[str]) -> list[int]:
        """Return record counts for Markdown tables with a semantic title column."""

        counts: list[int] = []
        row_index = 0
        title_headers = {"标题", "事件标题", "title", "event", "event title"}
        while row_index + 1 < len(block):
            header = block[row_index].strip()
            separator = block[row_index + 1].strip()
            if "|" not in header or "|" not in separator:
                row_index += 1
                continue
            header_cells = [
                re.sub(r"[*_`]", "", cell).strip().casefold()
                for cell in header.strip("|").split("|")
            ]
            separator_cells = [cell.strip() for cell in separator.strip("|").split("|")]
            is_separator = bool(separator_cells) and all(
                re.fullmatch(r":?-{3,}:?", cell) for cell in separator_cells
            )
            if not is_separator or not any(cell in title_headers for cell in header_cells):
                row_index += 1
                continue
            data_count = 0
            row_index += 2
            while row_index < len(block):
                candidate = block[row_index].strip()
                if not candidate or "|" not in candidate:
                    break
                data_count += 1
                row_index += 1
            counts.append(data_count)
        return counts

    for index, line in enumerate(lines):
        match = heading_pattern.match(line)
        if match is None:
            continue
        expected = int(match.group(2))
        end = index + 1
        while end < len(lines) and not re.match(r"^#{2,3}\s+", lines[end]):
            end += 1
        block = lines[index + 1 : end]
        list_count = sum(
            1
            for candidate in block
            if any(pattern.match(candidate) for pattern in item_patterns)
        )
        table_counts = event_table_counts(block)
        actual = max([list_count, *table_counts], default=0)
        if actual != expected:
            issues.append(
                {
                    "code": "declared_group_count",
                    "heading": line[4:].strip(),
                    "expected": expected,
                    "actual": actual,
                    "detail": (
                        "The count declared by this report subheading does not match its "
                        "listed records"
                    ),
                }
            )
    return issues


async def validate_candidate_report(*, session_id: str, generation_id: str) -> dict[str, Any]:
    workspace_dir, _, _ = await _resolve_run(session_id, generation_id)
    candidate_path = workspace_dir / "work" / generation_id / "report.md"
    evidence_path = workspace_dir / "work" / generation_id / "evidence.json"
    if not candidate_path.is_file():
        raise ProductWorkspaceError("Candidate report was not written")
    if not evidence_path.is_file():
        raise ProductWorkspaceError("Candidate evidence map was not written")
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
    prohibited_literals = _template_prohibited_literals(template)
    material_rows = [
        json.loads(line)
        for line in materials_path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    material_ids = [_material_id(value) for value in material_rows]
    h1_lines = [line for line in report.splitlines() if line.startswith("# ")]
    report_h2 = [line[3:].strip() for line in report.splitlines() if line.startswith("## ")]
    heading_issue = _heading_sequence_issue(_template_h2(template), report_h2)
    evidence = read_json(evidence_path)
    evidence_materials = evidence.get("materials")
    if not isinstance(evidence_materials, dict):
        raise ProductWorkspaceError("Candidate evidence map is invalid")
    missing_evidence = [
        material_id for material_id in material_ids if material_id not in evidence_materials
    ]
    unknown_evidence = [
        material_id for material_id in evidence_materials if material_id not in material_ids
    ]
    invalid_evidence_sections: dict[str, list[str]] = {}
    for material_id, sections in evidence_materials.items():
        if not isinstance(sections, list) or not sections:
            invalid_evidence_sections[material_id] = []
            continue
        invalid = [
            section
            for section in sections
            if not isinstance(section, str) or section not in report_h2
        ]
        if invalid:
            invalid_evidence_sections[material_id] = invalid
    leaked_material_ids = [material_id for material_id in material_ids if material_id in report]
    leaked_source_ids = [
        str(value["source_id"])
        for value in material_rows
        if _material_id(value) not in report and str(value["source_id"]) in report
    ]
    leaked_source_domains = sorted(
        {
            str(darkweb["source_domain"])
            for value in material_rows
            if isinstance((darkweb := value.get("darkweb")), dict)
            and isinstance(darkweb.get("source_domain"), str)
            and darkweb["source_domain"]
            and darkweb["source_domain"] in report
        }
    )
    internal_markers = (
        "generation_context_",
        f"work/{generation_id}/report.md",
        "templates/snapshots/",
        "materials/snapshots/",
    )
    leaked_internal_markers = sorted(marker for marker in internal_markers if marker in report)
    issues: list[dict[str, Any]] = []
    if h1_lines:
        issues.append(
            {
                "code": "report_title_forbidden",
                "detail": "Report-level H1 headings are not allowed",
            }
        )
    if heading_issue:
        issues.append(heading_issue)
    issues.extend(_declared_group_count_issues(report))
    if missing_evidence or unknown_evidence or invalid_evidence_sections:
        evidence_issue: dict[str, Any] = {"code": "evidence_map"}
        if missing_evidence:
            evidence_issue["missing"] = missing_evidence
        if unknown_evidence:
            evidence_issue["unknown"] = unknown_evidence
        if invalid_evidence_sections:
            evidence_issue["invalidSections"] = invalid_evidence_sections
        issues.append(evidence_issue)
    if leaked_material_ids:
        issues.append(
            {
                "code": "internal_material_id",
                "materialIDs": leaked_material_ids,
                "detail": "Internal material IDs must not appear in the report body",
            }
        )
    if leaked_source_ids:
        issues.append(
            {
                "code": "internal_source_id",
                "sourceIDs": leaked_source_ids,
                "detail": "Backend source IDs must not appear in the report body",
            }
        )
    if leaked_source_domains:
        issues.append(
            {
                "code": "backend_source_domain",
                "domains": leaked_source_domains,
                "detail": (
                    "Backend source-site domains are not original-source links and must not "
                    "appear in the report body"
                ),
            }
        )
    if leaked_internal_markers:
        issues.append({"code": "internal_path_leakage", "markers": leaked_internal_markers})
    if re.search(r"^```(?:markdown|md)?\s*$", report, flags=re.MULTILINE):
        issues.append({"code": "markdown_fence", "detail": "Do not wrap the complete report in a fence"})
    prohibited_matches = [
        literal
        for literal in prohibited_literals
        if re.search(re.escape(literal), report, flags=re.IGNORECASE)
    ]
    if prohibited_matches:
        issues.append(
            {
                "code": "template_prohibited_expression",
                "expressions": prohibited_matches,
                "detail": "The current Session template explicitly prohibits these expressions",
            }
        )

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
        "evidenceSHA256": file_sha256(evidence_path),
        "issues": issues,
        "validatedAt": utc_now(),
    }
    atomic_write_json(validation_path, result)
    return result
