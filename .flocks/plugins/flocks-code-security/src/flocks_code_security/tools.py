"""Flocks tool handlers and registration for static code audits."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any, Awaitable, Callable

from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)

from flocks_code_security.contract import SLUG_RE
from flocks_code_security.coverage import (
    normalize_open_questions,
    public_open_question,
)
from flocks_code_security.orchestration import (
    baseline_prompt,
    plan_baseline_units,
    plan_threat_model_units,
    plan_verification_units,
    targeted_rescan_prompt,
    threat_model_prompt,
    verification_prompt,
)
from flocks_code_security.reporting import ReportWriter
from flocks_code_security.runtime import get_runtime


ROLE_AGENTS = {
    "coordinator": "code-security",
    "threat_modeler": "code-security-threat-modeler",
    "baseline": "code-security-baseline",
    "verifier": "code-security-verifier",
}
_AGENT_DEFINITIONS_ROOT = Path(__file__).resolve().parent / "agents"


def _ruleset_digest() -> str:
    digest = hashlib.sha256()
    digest.update(b"flocks-code-security-rules-v1\0")
    package_root = resources.files("flocks_code_security")
    for agent_name in ROLE_AGENTS.values():
        for name in ("agent.yaml", "prompt.md"):
            relative_name = f"{agent_name}/{name}"
            digest.update(relative_name.encode("utf-8") + b"\0")
            digest.update((_AGENT_DEFINITIONS_ROOT / relative_name).read_bytes())
            digest.update(b"\0")
    for name in (
        "contract.py",
        "coverage.py",
        "orchestration.py",
        "reporting.py",
        "schemas/coverage.schema.json",
        "schemas/findings.schema.json",
        "schemas/scan-manifest.schema.json",
    ):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(package_root.joinpath(*name.split("/")).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


RULESET_DIGEST = _ruleset_digest()
COORDINATOR_ROLE = {"coordinator"}
THREAT_MODELER_ROLE = {"threat_modeler"}
SOURCE_SUBMIT_ROLES = {"baseline"}
THREAT_MODEL_CONSUMER_ROLES = {"baseline"}
VERIFIER_ROLE = {"verifier"}
EVIDENCE_ROLES = {
    "user_input",
    "entrypoint",
    "propagation",
    "root_control",
    "sink",
    "outcome",
    "expected_control",
}
STORE_ERRORS = (OSError, ValueError, sqlite3.Error)
WORKER_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
LAUNCHING_BATCH_IDS: set[str] = set()
REGISTERED_AUDIT_TOOLS: dict[
    str,
    tuple[Tool, Callable[..., Awaitable[ToolResult]]],
] = {}
AUDIT_TOOL_NAMES = (
    "audit_prepare",
    "audit_inventory",
    "audit_read",
    "audit_search",
    "audit_threat_model_context",
    "audit_submit_threat_model",
    "audit_verification_subject",
    "audit_submit_candidate",
    "audit_submit_verdict",
    "audit_submit_coverage",
    "audit_adjudication_context",
    "audit_submit_adjudication",
    "audit_status",
    "audit_finalize",
    "audit_cancel",
    "audit_run_workers",
    "audit_wait_workers",
)


def _error(error: Exception | str, *, title: str) -> ToolResult:
    return ToolResult(success=False, error=str(error), title=title)


def _require_agent_execution(ctx: ToolContext, roles: set[str]) -> None:
    if ctx.extra.get("agent_execution_session") is not True:
        raise ValueError("Audit tools require an agent execution session")
    expected_agents = {ROLE_AGENTS[role] for role in roles}
    if ctx.agent not in expected_agents:
        raise ValueError("Agent identity does not match the audit operation")


def _coordinator_binding(ctx: ToolContext, scan_id: str):
    runtime = get_runtime()
    _require_agent_execution(ctx, COORDINATOR_ROLE)
    binding = runtime.store.require_binding(ctx.session_id, COORDINATOR_ROLE)
    if binding.scan_id != scan_id:
        raise ValueError("scan_id does not belong to this coordinator session")
    return binding


async def audit_prepare(
    ctx: ToolContext,
    target_path: str,
    include_paths: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
    max_file_bytes: int = 1_048_576,
    mode: str = "standard",
) -> ToolResult:
    if mode != "standard":
        return _error("Only standard static audits are implemented in this version", title="Audit preparation")
    runtime = get_runtime()
    snapshot = None
    scan_id = None
    try:
        _require_agent_execution(ctx, COORDINATOR_ROLE)
        if runtime.store.resolve_binding(ctx.session_id) is not None:
            raise ValueError("This coordinator session already owns an audit scan")
        snapshot = await asyncio.to_thread(
            runtime.snapshots.create,
            target_path,
            include_paths=include_paths,
            exclude_patterns=exclude_patterns,
            max_file_bytes=max_file_bytes,
        )
        scan_id = await asyncio.to_thread(
            runtime.store.create_scan,
            parent_session_id=ctx.session_id,
            snapshot_id=snapshot.snapshot_id,
            mode=mode,
            ruleset_digest=RULESET_DIGEST,
        )
        await asyncio.to_thread(
            runtime.store.bind_session,
            session_id=ctx.session_id,
            scan_id=scan_id,
            snapshot_id=snapshot.snapshot_id,
            role="coordinator",
        )
        return ToolResult(
            success=True,
            output={"scan_id": scan_id, "status": "running", "snapshot": snapshot.public_dict()},
            title=f"Prepared code audit {scan_id}",
            metadata={"scan_id": scan_id, "snapshot_id": snapshot.snapshot_id},
        )
    except STORE_ERRORS as exc:
        if scan_id is not None:
            await asyncio.to_thread(runtime.store.delete_scan, scan_id)
        if snapshot is not None:
            await asyncio.to_thread(runtime.snapshots.delete, snapshot.snapshot_id)
        return _error(exc, title="Audit preparation failed")


async def audit_inventory(
    ctx: ToolContext,
    offset: int = 0,
    limit: int = 500,
) -> ToolResult:
    try:
        _require_agent_execution(ctx, {"threat_modeler", "baseline", "verifier"})
        output = await asyncio.to_thread(
            get_runtime().source.inventory,
            ctx.session_id,
            offset=offset,
            limit=limit,
        )
        return ToolResult(success=True, output=output, title="Snapshot inventory")
    except STORE_ERRORS as exc:
        return _error(exc, title="Snapshot inventory failed")


async def audit_read(
    ctx: ToolContext,
    relative_path: str,
    start_line: int = 1,
    end_line: int | None = None,
) -> ToolResult:
    try:
        _require_agent_execution(ctx, {"threat_modeler", "baseline", "verifier"})
        output = await asyncio.to_thread(
            get_runtime().source.read,
            ctx.session_id,
            relative_path,
            start_line=start_line,
            end_line=end_line,
        )
        return ToolResult(success=True, output=output, title=f"Read {relative_path}")
    except STORE_ERRORS as exc:
        return _error(exc, title="Snapshot read failed")


async def audit_search(
    ctx: ToolContext,
    query: str,
    path_glob: str | None = None,
    case_sensitive: bool = False,
    max_results: int = 100,
) -> ToolResult:
    try:
        _require_agent_execution(ctx, {"threat_modeler", "baseline", "verifier"})
        output = await asyncio.to_thread(
            get_runtime().source.search,
            ctx.session_id,
            query,
            path_glob=path_glob,
            case_sensitive=case_sensitive,
            max_results=max_results,
        )
        return ToolResult(success=True, output=output, title=f"Search snapshot for {query}")
    except STORE_ERRORS as exc:
        return _error(exc, title="Snapshot search failed")


async def audit_threat_model_context(ctx: ToolContext) -> ToolResult:
    try:
        _require_agent_execution(ctx, THREAT_MODEL_CONSUMER_ROLES)
        runtime = get_runtime()
        binding = runtime.store.require_binding(
            ctx.session_id,
            THREAT_MODEL_CONSUMER_ROLES,
        )
        output = await asyncio.to_thread(
            runtime.store.get_threat_model_for_binding,
            binding,
        )
        return ToolResult(
            success=True,
            output=output,
            title="Source-backed threat model",
        )
    except STORE_ERRORS as exc:
        return _error(exc, title="Threat-model context unavailable")


async def audit_submit_threat_model(
    ctx: ToolContext,
    threat_model: dict[str, Any],
) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, THREAT_MODELER_ROLE)
        binding = runtime.store.require_binding(ctx.session_id, THREAT_MODELER_ROLE)
        if not isinstance(threat_model, dict):
            raise ValueError("threat_model must be an object")
        canonical_fields = (
            "summary",
            "assets",
            "trustBoundaries",
            "attackerCapabilities",
            "securityObjectives",
            "assumptions",
        )
        missing = [
            field
            for field in canonical_fields
            if field not in threat_model or threat_model[field] is None
        ]
        if missing:
            raise ValueError("Missing threat-model fields: " + ", ".join(missing))
        allowed_fields = {*canonical_fields, "evidence"}
        unknown = sorted(set(threat_model) - allowed_fields)
        if unknown:
            raise ValueError("Unsupported threat-model fields: " + ", ".join(unknown))
        if not isinstance(threat_model["summary"], str):
            raise ValueError("Threat-model summary must be a string")
        summary = threat_model["summary"].strip()
        if not summary or len(summary) > 20_000:
            raise ValueError("Threat-model summary must contain 1 to 20000 characters")
        payload: dict[str, Any] = {"summary": summary}
        for field in canonical_fields[1:]:
            values = threat_model[field]
            if (
                not isinstance(values, list)
                or len(values) > 100
                or not all(isinstance(item, str) for item in values)
            ):
                raise ValueError(f"Threat-model field {field} must be an array of at most 100 strings")
            normalized = [item.strip() for item in values]
            if any(not item or len(item) > 4_000 for item in normalized):
                raise ValueError(
                    f"Threat-model field {field} contains an empty or oversized item"
                )
            if field != "assumptions" and not normalized:
                raise ValueError(f"Threat-model field {field} must not be empty")
            payload[field] = normalized
        if len(json.dumps(payload, ensure_ascii=False)) > 60_000:
            raise ValueError("Canonical threat model may contain at most 60000 characters")
        raw_evidence = threat_model.get("evidence")
        if not isinstance(raw_evidence, list) or len(raw_evidence) > 100:
            raise ValueError("Threat model requires between 1 and 100 evidence references")
        runtime.store.validate_threat_model_contract(payload, raw_evidence)
        evidence = await asyncio.to_thread(
            runtime.source.validate_evidence,
            binding,
            raw_evidence,
        )
        updated = await asyncio.to_thread(
            runtime.store.save_threat_model,
            binding,
            payload,
            evidence,
        )
        return ToolResult(
            success=True,
            output={
                "scan_id": binding.scan_id,
                "status": "submitted",
                "operation": "updated" if updated else "created",
                "evidence_count": len(evidence),
            },
            title="Submitted source-backed threat model",
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Threat-model submission failed")


async def audit_verification_subject(ctx: ToolContext) -> ToolResult:
    try:
        _require_agent_execution(ctx, VERIFIER_ROLE)
        runtime = get_runtime()
        binding = runtime.store.require_binding(ctx.session_id, VERIFIER_ROLE)
        output = await asyncio.to_thread(
            runtime.store.get_verification_subject,
            binding,
        )
        return ToolResult(
            success=True,
            output=output,
            title=f"Verification subject {output['candidate_id']}",
        )
    except STORE_ERRORS as exc:
        return _error(exc, title="Verification subject unavailable")


async def audit_submit_candidate(ctx: ToolContext, candidate: dict[str, Any]) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, SOURCE_SUBMIT_ROLES)
        binding = runtime.store.require_binding(ctx.session_id, SOURCE_SUBMIT_ROLES)
        await asyncio.to_thread(
            runtime.store.require_threat_model_consumed,
            binding,
        )
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        required = (
            "rule_id",
            "identity_anchor",
            "title",
            "summary",
            "severity",
            "severity_rationale",
            "confidence",
            "confidence_rationale",
            "category",
            "cwe",
            "attack_path",
            "dangerous_operation",
            "root_cause",
            "remediation",
            "evidence",
        )
        missing = [name for name in required if candidate.get(name) in (None, "")]
        if missing:
            raise ValueError("Missing candidate fields: " + ", ".join(missing))
        rule_id = str(candidate["rule_id"]).strip()
        anchor = str(candidate["identity_anchor"]).strip()
        instance = str(candidate.get("identity_instance") or "").strip()
        if not SLUG_RE.fullmatch(rule_id):
            raise ValueError("rule_id must be a stable lowercase vulnerability-family slug")
        if not SLUG_RE.fullmatch(anchor):
            raise ValueError("identity_anchor must be a stable lowercase semantic slug")
        if instance and not SLUG_RE.fullmatch(instance):
            raise ValueError("identity_instance must be a stable lowercase semantic slug")
        severity = str(candidate["severity"]).lower()
        if severity not in {"critical", "high", "medium", "low", "informational"}:
            raise ValueError("Unsupported severity")
        confidence = float(candidate["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for field in (
            "title",
            "summary",
            "severity_rationale",
            "confidence_rationale",
            "category",
            "dangerous_operation",
            "root_cause",
            "remediation",
        ):
            if not str(candidate[field]).strip():
                raise ValueError(f"Candidate field {field} must not be empty")
            if len(str(candidate[field])) > 10_000:
                raise ValueError(f"Candidate field {field} is too long")
        cwe = candidate["cwe"]
        if (
            not isinstance(cwe, list)
            or not cwe
            or len(cwe) > 20
            or not all(
                isinstance(item, str) and item.startswith("CWE-") and item[4:].isdigit()
                for item in cwe
            )
        ):
            raise ValueError("cwe must be an array of at most 20 CWE identifiers")
        attack_path = candidate["attack_path"]
        if not isinstance(attack_path, dict):
            raise ValueError("attack_path must be an object")
        for field in ("summary", "dataflow", "reachability"):
            if field not in attack_path:
                raise ValueError(f"attack_path.{field} is required")
        if not str(attack_path["summary"]).strip():
            raise ValueError("attack_path.summary must not be empty")
        for field in ("dataflow", "reachability"):
            section = attack_path[field]
            if (
                not isinstance(section, dict)
                or not str(section.get("summary") or "").strip()
            ):
                raise ValueError(f"attack_path.{field}.summary is required")
        if len(json.dumps(attack_path, ensure_ascii=False)) > 30_000:
            raise ValueError("attack_path may contain at most 30000 characters")
        if not isinstance(candidate["evidence"], list) or len(candidate["evidence"]) > 50:
            raise ValueError("A candidate may contain between 1 and 50 evidence references")
        evidence_metadata: list[dict[str, str]] = []
        for index, item in enumerate(candidate["evidence"]):
            if not isinstance(item, dict):
                raise ValueError("Each evidence reference must be an object")
            metadata: dict[str, str] = {}
            for field in ("label", "role", "explanation"):
                value = str(item.get(field) or "").strip()
                if not value or len(value) > 4_000:
                    raise ValueError(
                        f"evidence[{index}].{field} must contain 1 to 4000 characters"
                    )
                metadata[field] = value
            if metadata["role"] not in EVIDENCE_ROLES:
                raise ValueError(
                    f"evidence[{index}].role must be a canonical code-evidence role"
                )
            evidence_metadata.append(metadata)
        evidence = await asyncio.to_thread(
            runtime.source.validate_evidence,
            binding,
            candidate["evidence"],
            allowed_extra_fields=frozenset({"label", "role", "explanation"}),
        )
        payload = {
            "rule_id": rule_id,
            "identity_anchor": anchor,
            "identity_instance": instance,
            "title": str(candidate["title"]).strip(),
            "summary": str(candidate["summary"]).strip(),
            "severity": severity,
            "severity_rationale": str(candidate["severity_rationale"]).strip(),
            "confidence": confidence,
            "confidence_rationale": str(candidate["confidence_rationale"]).strip(),
            "category": str(candidate["category"]).strip(),
            "cwe": list(dict.fromkeys(cwe)),
            "attack_path": attack_path,
            "dangerous_operation": str(candidate["dangerous_operation"]).strip(),
            "root_cause": str(candidate["root_cause"]).strip(),
            "remediation": str(candidate["remediation"]).strip(),
            "remediation_tests": _optional_string_list(
                candidate.get("remediation_tests"),
                field="remediation_tests",
            ),
            "preventive_controls": _optional_string_list(
                candidate.get("preventive_controls"),
                field="preventive_controls",
            ),
            "evidence_metadata": evidence_metadata,
        }
        candidate_id = await asyncio.to_thread(runtime.store.save_candidate, binding, payload, evidence)
        return ToolResult(
            success=True,
            output={"candidate_id": candidate_id, "evidence_count": len(evidence)},
            title=f"Submitted candidate {candidate_id}",
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Candidate submission failed")


def _optional_string_list(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if (
        not isinstance(value, list)
        or len(value) > 50
        or not all(
            isinstance(item, str) and 0 < len(item.strip()) <= 4_000
            for item in value
        )
    ):
        raise ValueError(f"{field} must be an array of at most 50 non-empty strings")
    return [item.strip() for item in value]


async def audit_submit_verdict(
    ctx: ToolContext,
    candidate_id: str,
    verdict: str,
    rationale: str,
    counter_evidence: list[dict[str, Any]] | None = None,
) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, VERIFIER_ROLE)
        binding = runtime.store.require_binding(ctx.session_id, VERIFIER_ROLE)
        normalized_verdict = str(verdict or "").lower()
        if normalized_verdict not in {"confirmed", "rejected", "insufficient_evidence"}:
            raise ValueError("Unsupported verification verdict")
        if not str(rationale or "").strip():
            raise ValueError("rationale is required")
        if len(str(rationale)) > 10_000:
            raise ValueError("rationale may contain at most 10000 characters")
        if counter_evidence is not None and len(counter_evidence) > 50:
            raise ValueError("At most 50 counter-evidence references are allowed")
        validated_counter_evidence: list[dict[str, Any]] = []
        if counter_evidence:
            validated_counter_evidence = await asyncio.to_thread(
                runtime.source.validate_evidence,
                binding,
                counter_evidence,
            )
        verification_id = await asyncio.to_thread(
            runtime.store.save_verification,
            binding,
            candidate_id=candidate_id,
            verdict=normalized_verdict,
            rationale=str(rationale),
            counter_evidence=validated_counter_evidence,
        )
        return ToolResult(
            success=True,
            output={"verification_id": verification_id, "verdict": normalized_verdict},
            title=f"Submitted verdict {normalized_verdict}",
        )
    except STORE_ERRORS as exc:
        return _error(exc, title="Verdict submission failed")


async def audit_submit_coverage(
    ctx: ToolContext,
    inventoried_paths: list[str] | None = None,
    analyzed_paths: list[str] | None = None,
    failed_paths: list[str] | None = None,
    open_questions: list[dict[str, Any] | str] | None = None,
) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, SOURCE_SUBMIT_ROLES)
        binding = runtime.store.require_binding(ctx.session_id, SOURCE_SUBMIT_ROLES)
        await asyncio.to_thread(
            runtime.store.require_threat_model_consumed,
            binding,
        )
        path_groups = {
            "inventoried_paths": inventoried_paths or [],
            "analyzed_paths": analyzed_paths or [],
            "failed_paths": failed_paths or [],
        }
        if any(len(items) > 2_000 for items in path_groups.values()):
            raise ValueError("Each coverage path list may contain at most 2000 entries")
        questions = normalize_open_questions(open_questions)

        validated_inventoried = await asyncio.to_thread(
            runtime.source.validate_coverage_paths,
            binding,
            path_groups["inventoried_paths"],
            allow_omitted=True,
        )
        validated_analyzed = await asyncio.to_thread(
            runtime.source.validate_coverage_paths,
            binding,
            path_groups["analyzed_paths"],
            allow_omitted=False,
        )
        validated_failed = await asyncio.to_thread(
            runtime.source.validate_coverage_paths,
            binding,
            path_groups["failed_paths"],
            allow_omitted=True,
        )
        for question in questions:
            question["related_paths"] = await asyncio.to_thread(
                runtime.source.validate_coverage_paths,
                binding,
                question["related_paths"],
                allow_omitted=True,
            )
        await asyncio.to_thread(
            runtime.store.validate_coverage_access,
            binding,
            inventoried_paths=validated_inventoried,
            analyzed_paths=validated_analyzed,
        )
        payload = {
            "inventoried_paths": validated_inventoried,
            "analyzed_paths": validated_analyzed,
            "failed_paths": validated_failed,
            "open_questions": questions,
        }
        await asyncio.to_thread(runtime.store.save_coverage, binding, payload)
        return ToolResult(success=True, output=payload, title="Submitted audit coverage")
    except STORE_ERRORS as exc:
        return _error(exc, title="Coverage submission failed")


async def audit_adjudication_context(
    ctx: ToolContext,
    scan_id: str,
    candidate_id: str | None = None,
) -> ToolResult:
    runtime = get_runtime()
    try:
        binding = _coordinator_binding(ctx, scan_id)
        context = await asyncio.to_thread(
            runtime.store.get_adjudication_context,
            scan_id,
        )
        all_candidates = context.pop("candidates")

        def compact(value: Any, *, text_limit: int, list_limit: int = 20) -> Any:
            if isinstance(value, dict):
                return {
                    str(key): compact(
                        item,
                        text_limit=text_limit,
                        list_limit=list_limit,
                    )
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [
                    compact(
                        item,
                        text_limit=text_limit,
                        list_limit=list_limit,
                    )
                    for item in value[:list_limit]
                ]
            if isinstance(value, str):
                return value[:text_limit]
            return value

        async def enrich(
            evidence: dict[str, Any],
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            excerpt = await asyncio.to_thread(
                runtime.source.evidence_excerpt,
                binding.snapshot_id,
                evidence,
                max_characters=500,
            )
            output = {**evidence, **excerpt}
            if metadata is not None:
                output.update(compact(metadata, text_limit=300))
            return output

        base = {
            "scan_id": scan_id,
            "adjudication_round": context["adjudication_round"],
        }
        if candidate_id is None:
            failed_paths: list[str] = []
            open_questions: list[dict[str, Any]] = []
            for item in context["coverage"]:
                payload = item.get("payload", {})
                failed_paths.extend(payload.get("failed_paths", []))
                open_questions.extend(payload.get("open_questions", []))
            failed_paths = list(dict.fromkeys(failed_paths))
            questions_by_key = {
                json.dumps(question, sort_keys=True): question
                for question in open_questions
            }
            open_questions = list(questions_by_key.values())
            blocking_questions = [
                public_open_question(question)
                for question in open_questions
                if question["blocking"]
            ]
            limitations = [
                public_open_question(question)
                for question in open_questions
                if not question["blocking"]
            ]
            omissions = context["omissions"]
            output = {
                **base,
                "view": "overview",
                "threat_model": compact(
                    context["threat_model"]["threat_model"],
                    text_limit=500,
                ),
                "candidate_count": len(all_candidates),
                "candidates": [
                    {
                        "candidate_id": item["candidate_id"],
                        "title": compact(
                            item["payload"].get("title"),
                            text_limit=300,
                        ),
                        "severity": item["payload"].get("severity"),
                        "verdict": (
                            item["verification"].get("verdict")
                            if isinstance(item.get("verification"), dict)
                            else None
                        ),
                    }
                    for item in all_candidates
                ],
                "coverage_gaps": {
                    "failed_paths": failed_paths[:100],
                    "blocking_questions": blocking_questions[:100],
                    "omissions": omissions[:100],
                    "truncated": any(
                        len(items) > 100
                        for items in (failed_paths, blocking_questions, omissions)
                    ),
                },
                "validation_limitations": limitations[:100],
            }
        else:
            candidate = next(
                (
                    item
                    for item in all_candidates
                    if item["candidate_id"] == candidate_id
                ),
                None,
            )
            if candidate is None:
                raise ValueError("Candidate does not belong to this scan")
            payload = candidate["payload"]
            metadata_rows = payload.get("evidence_metadata", [])
            evidence = [
                await enrich(
                    item,
                    (
                        metadata_rows[int(item.get("ordinal", 0))]
                        if int(item.get("ordinal", 0)) < len(metadata_rows)
                        else None
                    ),
                )
                for item in candidate.get("evidence", [])
            ]
            verification = candidate.get("verification")
            compact_verification = None
            if isinstance(verification, dict):
                compact_verification = {
                    "verdict": verification.get("verdict"),
                    "rationale": compact(
                        verification.get("rationale"),
                        text_limit=4_000,
                    ),
                    "counter_evidence": [
                        await enrich(item)
                        for item in verification.get("counter_evidence", [])
                    ],
                }
            decision_payload = {
                key: value
                for key, value in payload.items()
                if key
                not in {
                    "evidence_metadata",
                    "remediation",
                    "remediation_tests",
                    "preventive_controls",
                }
            }
            decision_payload = compact(decision_payload, text_limit=4_000)
            decision_payload.update(
                {
                    "candidate_id": candidate["candidate_id"],
                    "evidence": evidence,
                    "verification": compact_verification,
                }
            )
            output = {
                **base,
                "view": "candidate",
                "candidate": decision_payload,
            }
        return ToolResult(
            success=True,
            output=output,
            title=f"Parent adjudication context for {scan_id}",
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Adjudication context unavailable")


async def audit_submit_adjudication(
    ctx: ToolContext,
    scan_id: str,
    decision: dict[str, Any],
) -> ToolResult:
    try:
        _coordinator_binding(ctx, scan_id)
        saved = await asyncio.to_thread(
            get_runtime().store.save_adjudication,
            scan_id,
            decision,
        )
        return ToolResult(
            success=True,
            output=saved,
            title=(
                f"Submitted round {saved['adjudication_round']} "
                f"{saved['action']} adjudication"
            ),
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Adjudication submission failed")


async def audit_status(ctx: ToolContext, scan_id: str) -> ToolResult:
    try:
        _coordinator_binding(ctx, scan_id)
        for batch in get_runtime().store.list_worker_batches(scan_id):
            await _refresh_worker_batch(batch["batch_id"])
        output = await asyncio.to_thread(get_runtime().store.scan_status, scan_id)
        return ToolResult(success=True, output=output, title=f"Audit status {scan_id}")
    except STORE_ERRORS as exc:
        return _error(exc, title="Audit status failed")


async def audit_finalize(ctx: ToolContext, scan_id: str) -> ToolResult:
    try:
        _coordinator_binding(ctx, scan_id)
        for batch in get_runtime().store.list_worker_batches(scan_id):
            await _refresh_worker_batch(batch["batch_id"])
        await asyncio.to_thread(get_runtime().store.ensure_ready_to_finalize, scan_id)
        output = await asyncio.to_thread(ReportWriter(get_runtime().store).write, scan_id)
        return ToolResult(success=True, output=output, title=f"Finalized audit {scan_id}")
    except STORE_ERRORS as exc:
        return _error(exc, title="Audit finalization failed")


async def audit_cancel(ctx: ToolContext, scan_id: str) -> ToolResult:
    try:
        _coordinator_binding(ctx, scan_id)
        await asyncio.to_thread(
            get_runtime().store.transition_scan_status,
            scan_id,
            from_statuses={"running"},
            to_status="cancelled",
        )
        task_ids = await asyncio.to_thread(
            get_runtime().store.cancel_scan_work,
            scan_id,
        )
        manager = _background_manager()
        cancelled_workers = sum(manager.cancel(task_id=task_id) for task_id in task_ids)
        return ToolResult(
            success=True,
            output={
                "scan_id": scan_id,
                "status": "cancelled",
                "cancelled_workers": cancelled_workers,
            },
            title=f"Cancelled audit {scan_id}",
        )
    except STORE_ERRORS as exc:
        return _error(exc, title="Audit cancellation failed")


async def audit_run_workers(
    ctx: ToolContext,
    scan_id: str,
    phase: str = "threat_modeling",
) -> ToolResult:
    runtime = get_runtime()
    try:
        binding = _coordinator_binding(ctx, scan_id)
        if phase == "threat_modeling":
            units = plan_threat_model_units()
            candidates_by_id = {}
        elif phase == "baseline":
            files = await asyncio.to_thread(
                runtime.store.list_snapshot_files,
                binding.snapshot_id,
            )
            units = plan_baseline_units(files)
            candidates_by_id: dict[str, dict[str, Any]] = {}
        elif phase == "verification":
            candidates = await asyncio.to_thread(
                runtime.store.list_unverified_candidates,
                scan_id,
                limit=32,
            )
            if not candidates:
                raise ValueError("No unverified candidates are available")
            units = plan_verification_units(candidates)
            candidates_by_id = {
                item["candidate_id"]: item for item in candidates
            }
        elif phase == "targeted_rescan":
            directive = await asyncio.to_thread(
                runtime.store.get_targeted_rescan_directive,
                scan_id,
            )
            units = [
                {
                    "role": "baseline",
                    "paths": list(directive["paths"]),
                    "subject_id": None,
                }
            ]
            candidates_by_id = {}
        else:
            raise ValueError("Unsupported standard-audit worker phase")
        batch = await asyncio.to_thread(
            runtime.store.create_worker_batch,
            scan_id=scan_id,
            phase=phase,
            units=units,
        )
        launched = 0
        launch_failures = 0
        LAUNCHING_BATCH_IDS.add(batch["batch_id"])
        try:
            for unit in batch["units"]:
                candidate = candidates_by_id.get(unit.get("subject_id"))
                try:
                    await _launch_worker(
                        ctx,
                        scan_id,
                        binding.snapshot_id,
                        phase,
                        unit,
                        candidate=candidate,
                    )
                    launched += 1
                except Exception:
                    launch_failures += 1
                    current_unit = await asyncio.to_thread(
                        runtime.store.get_work_unit,
                        unit["work_unit_id"],
                    )
                    if current_unit and current_unit["status"] in {"pending", "running"}:
                        await asyncio.to_thread(
                            runtime.store.update_work_unit_status,
                            unit["work_unit_id"],
                            "failed",
                        )
        finally:
            LAUNCHING_BATCH_IDS.discard(batch["batch_id"])
        current_batch = await asyncio.to_thread(
            runtime.store.get_worker_batch,
            batch["batch_id"],
        )
        if current_batch and current_batch["status"] == "pending":
            await asyncio.to_thread(
                runtime.store.update_worker_batch_status,
                batch["batch_id"],
                "running",
            )
        await _refresh_worker_batch(batch["batch_id"])
        output = await _public_batch_status(batch["batch_id"])
        output["launched_workers"] = launched
        output["launch_failures"] = launch_failures
        return ToolResult(
            success=True,
            output=output,
            title=f"Launched {phase} audit workers",
            metadata={"scan_id": scan_id, "batch_id": batch["batch_id"]},
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Worker launch failed")


async def audit_wait_workers(
    ctx: ToolContext,
    batch_id: str,
    timeout_seconds: int = 30,
) -> ToolResult:
    try:
        _require_agent_execution(ctx, COORDINATOR_ROLE)
        binding = get_runtime().store.require_binding(ctx.session_id, COORDINATOR_ROLE)
        batch = get_runtime().store.get_worker_batch(batch_id)
        if batch is None or batch["scan_id"] != binding.scan_id:
            raise ValueError("Worker batch does not belong to this coordinator session")
        timeout = max(0, min(int(timeout_seconds), 60))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            batch = await _refresh_worker_batch(batch_id)
            if batch["status"] in WORKER_TERMINAL_STATUSES:
                break
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(0.25, remaining))
        output = await _public_batch_status(batch_id)
        output["timed_out"] = output["status"] not in WORKER_TERMINAL_STATUSES
        return ToolResult(
            success=True,
            output=output,
            title=f"Worker batch {batch_id}",
        )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Worker wait failed")


def _background_manager():
    from flocks.task.background import get_background_manager

    return get_background_manager()


async def _launch_worker(
    ctx: ToolContext,
    scan_id: str,
    snapshot_id: str,
    phase: str,
    unit: dict[str, Any],
    *,
    candidate: dict[str, Any] | None,
) -> None:
    from flocks.session.message import Message, MessageRole
    from flocks.session.session import Session

    runtime = get_runtime()
    parent = await Session.get_by_id(ctx.session_id)
    if parent is None:
        raise ValueError("Coordinator session not found")
    agent_name = ROLE_AGENTS[unit["role"]]
    model = ctx.extra.get("model")
    model = model if isinstance(model, dict) else {}
    provider_id = model.get("providerID") or parent.provider
    model_id = model.get("modelID") or parent.model
    child_kwargs: dict[str, Any] = {}
    if provider_id and model_id:
        child_kwargs.update(
            provider=provider_id,
            model=model_id,
            model_pinned=True,
        )
    correlation_metadata = {
        "scan_id": scan_id,
        "snapshot_id": snapshot_id,
        "phase": phase,
        "role": unit["role"],
        "work_unit_id": unit["work_unit_id"],
        "assigned_paths": list(unit.get("paths", [])),
    }
    if candidate is not None:
        correlation_metadata["candidate_id"] = candidate["candidate_id"]
    trace_context = ctx.extra.get("langfuse_trace_context")
    trace_context = trace_context if isinstance(trace_context, dict) else None
    langfuse_metadata = {
        "session_id": scan_id,
        "trace_name": f"code-security.{phase}.model-step",
        "tags": [
            "feature:code-security",
            f"scan:{scan_id}",
            f"phase:{phase}",
            f"role:{unit['role']}",
            f"work-unit:{unit['work_unit_id']}",
        ],
        "metadata": correlation_metadata,
    }
    if trace_context:
        langfuse_metadata.update(
            root_trace_name="code-security.scan",
            trace_context=trace_context,
        )
    child = await Session.create(
        project_id=parent.project_id,
        directory=parent.directory,
        title=f"Code security {phase} worker",
        parent_id=parent.id,
        agent=agent_name,
        category="task",
        metadata={"langfuse": langfuse_metadata},
        **child_kwargs,
    )
    await asyncio.to_thread(
        runtime.store.bind_session,
        session_id=child.id,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role=unit["role"],
        work_unit_id=unit["work_unit_id"],
    )
    if phase == "threat_modeling":
        prompt = threat_model_prompt(snapshot_id=snapshot_id)
    elif phase == "baseline":
        prompt = baseline_prompt(snapshot_id=snapshot_id, paths=unit["paths"])
    elif phase == "targeted_rescan":
        prompt = targeted_rescan_prompt(snapshot_id=snapshot_id)
    elif phase == "verification" and candidate is not None:
        prompt = verification_prompt(
            snapshot_id=snapshot_id,
            candidate_id=candidate["candidate_id"],
        )
    else:
        raise ValueError("Worker prompt data is incomplete")
    await Message.create(
        session_id=child.id,
        role=MessageRole.USER,
        content=prompt,
        agent=agent_name,
    )
    manager = _background_manager()
    task = await manager.run_existing_session(
        session_id=child.id,
        parent_session_id=(
            None
            if ctx.extra.get("suppress_parent_completion") is True
            else parent.id
        ),
        description=f"Code security {phase} worker",
        agent=agent_name,
        allow_user_questions=False,
        provider_id=provider_id,
        model_id=model_id,
    )
    try:
        await asyncio.to_thread(
            runtime.store.set_work_unit_runtime,
            unit["work_unit_id"],
            session_id=child.id,
            background_task_id=task.id,
        )
    except BaseException:
        manager.cancel(task_id=task.id)
        raise


async def _refresh_worker_batch(batch_id: str) -> dict[str, Any]:
    runtime = get_runtime()
    batch = await asyncio.to_thread(runtime.store.get_worker_batch, batch_id)
    if batch is None:
        raise ValueError("Worker batch not found")
    manager = _background_manager()
    for unit in batch["units"]:
        if unit["status"] not in {"pending", "running"}:
            continue
        task_id = unit.get("background_task_id")
        task = manager.get_task(task_id) if task_id else None
        if task is None:
            if (
                unit["status"] == "running"
                or task_id
                or batch_id not in LAUNCHING_BATCH_IDS
            ):
                await asyncio.to_thread(
                    runtime.store.update_work_unit_status,
                    unit["work_unit_id"],
                    "failed",
                )
            continue
        if task.status == "completed":
            facts_complete = await asyncio.to_thread(
                runtime.store.work_unit_has_required_facts,
                unit["work_unit_id"],
                role=unit["role"],
            )
            next_status = "completed" if facts_complete else "failed"
        elif task.status == "cancelled":
            next_status = "cancelled"
        elif task.status == "error":
            facts_complete = await asyncio.to_thread(
                runtime.store.work_unit_has_required_facts,
                unit["work_unit_id"],
                role=unit["role"],
            )
            next_status = (
                "completed"
                if unit["role"] == "threat_modeler" and facts_complete
                else "failed"
            )
        else:
            continue
        await asyncio.to_thread(
            runtime.store.update_work_unit_status,
            unit["work_unit_id"],
            next_status,
        )

    refreshed = await asyncio.to_thread(runtime.store.get_worker_batch, batch_id)
    if refreshed is None:
        raise ValueError("Worker batch not found")
    statuses = [unit["status"] for unit in refreshed["units"]]
    if any(status in {"pending", "running"} for status in statuses):
        batch_status = "running"
    elif statuses and all(status == "completed" for status in statuses):
        batch_status = "completed"
    elif statuses and all(status == "cancelled" for status in statuses):
        batch_status = "cancelled"
    elif any(status == "completed" for status in statuses):
        batch_status = "partial"
    else:
        batch_status = "failed"
    if refreshed["status"] != batch_status:
        await asyncio.to_thread(
            runtime.store.update_worker_batch_status,
            batch_id,
            batch_status,
        )
        refreshed["status"] = batch_status
    return refreshed


async def _public_batch_status(batch_id: str) -> dict[str, Any]:
    batch = await asyncio.to_thread(get_runtime().store.get_worker_batch, batch_id)
    if batch is None:
        raise ValueError("Worker batch not found")
    counts = Counter(unit["status"] for unit in batch["units"])
    return {
        "batch_id": batch["batch_id"],
        "scan_id": batch["scan_id"],
        "phase": batch["phase"],
        "status": batch["status"],
        "worker_count": len(batch["units"]),
        "status_counts": dict(sorted(counts.items())),
        "workers": [
            {
                "work_unit_id": unit["work_unit_id"],
                "role": unit["role"],
                "assigned_paths": unit["paths"],
                "candidate_id": unit.get("subject_id"),
                "session_id": unit.get("session_id"),
                "status": unit["status"],
            }
            for unit in batch["units"]
        ],
    }


def _parameter(
    name: str,
    parameter_type: ParameterType,
    description: str,
    *,
    required: bool = True,
    default: Any = None,
    enum: list[Any] | None = None,
    json_schema: dict[str, Any] | None = None,
) -> ToolParameter:
    return ToolParameter(
        name=name,
        type=parameter_type,
        description=description,
        required=required,
        default=default,
        enum=enum,
        json_schema=json_schema,
    )


def _register(
    name: str,
    description: str,
    handler: Callable[..., Awaitable[ToolResult]],
    parameters: list[ToolParameter],
) -> None:
    existing = ToolRegistry.get(name)
    registered = REGISTERED_AUDIT_TOOLS.get(name)
    if existing is not None:
        if registered is not None and existing is registered[0]:
            return
        raise RuntimeError(f"Refusing to overwrite existing tool registration: {name}")
    tool = Tool(
        info=ToolInfo(
            name=name,
            description=description,
            category=ToolCategory.CUSTOM,
            parameters=parameters,
            source="plugin_py",
            native=False,
            always_load=False,
            tags=["security", "code-security", "static-analysis"],
        ),
        handler=handler,
    )
    ToolRegistry.register(tool)
    REGISTERED_AUDIT_TOOLS[name] = (tool, handler)


def is_registered_audit_tool(tool_info: Any) -> bool:
    """Return whether this is the intact tool registered by this plugin."""
    name = getattr(tool_info, "name", None)
    registered = REGISTERED_AUDIT_TOOLS.get(name)
    if registered is None:
        return False
    registered_tool, registered_handler = registered
    current_tool = ToolRegistry.get(name)
    return (
        current_tool is registered_tool
        and current_tool.info is tool_info
        and current_tool.handler is registered_handler
    )


def register_tools() -> None:
    for name in AUDIT_TOOL_NAMES:
        existing = ToolRegistry.get(name)
        registered = REGISTERED_AUDIT_TOOLS.get(name)
        if existing is not None and (
            registered is None or existing is not registered[0]
        ):
            raise RuntimeError(
                f"Refusing to overwrite existing tool registration: {name}"
            )
    string_array = {"type": "array", "items": {"type": "string"}}
    open_questions_schema = {
        "type": "array",
        "maxItems": 100,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "category", "blocking"],
            "properties": {
                "question": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1_000,
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "coverage_blocking",
                        "validation_limitation",
                        "security_hypothesis",
                    ],
                },
                "blocking": {"type": "boolean"},
                "related_paths": {
                    "type": "array",
                    "maxItems": 100,
                    "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1},
                },
                "follow_up": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 1_000,
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {
                            "category": {"const": "coverage_blocking"}
                        }
                    },
                    "then": {"properties": {"blocking": {"const": True}}},
                    "else": {"properties": {"blocking": {"const": False}}},
                }
            ],
        },
    }
    digest_bound_evidence_item = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "relative_path",
            "blob_digest",
            "start_line",
            "end_line",
        ],
        "properties": {
            "relative_path": {
                "type": "string",
                "minLength": 1,
                "maxLength": 1_024,
            },
            "blob_digest": {
                "type": "string",
                "pattern": "^[a-f0-9]{64}$",
            },
            "start_line": {"type": "integer", "minimum": 1},
            "end_line": {"type": "integer", "minimum": 1},
        },
    }
    threat_model_item = {
        "type": "string",
        "minLength": 1,
        "maxLength": 4_000,
        "pattern": "\\S",
    }
    threat_model_required_array = {
        "type": "array",
        "minItems": 1,
        "maxItems": 100,
        "items": threat_model_item,
    }
    threat_model_assumptions = {
        "type": "array",
        "maxItems": 100,
        "items": threat_model_item,
    }
    threat_model_evidence = {
        "type": "array",
        "minItems": 1,
        "maxItems": 100,
        "items": digest_bound_evidence_item,
    }
    counter_evidence_schema = {
        "type": "array",
        "minItems": 1,
        "maxItems": 50,
        "items": digest_bound_evidence_item,
    }
    evidence_schema = {
        "type": "array",
        "minItems": 1,
        "maxItems": 50,
        "items": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "relative_path",
                "blob_digest",
                "start_line",
                "end_line",
                "label",
                "role",
                "explanation",
            ],
            "properties": {
                "relative_path": {"type": "string"},
                "blob_digest": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
                "end_line": {"type": "integer", "minimum": 1},
                "label": {"type": "string"},
                "role": {"type": "string", "enum": sorted(EVIDENCE_ROLES)},
                "explanation": {"type": "string"},
            },
        },
    }
    attack_path_schema = {
        "type": "object",
        "additionalProperties": True,
        "required": ["summary", "dataflow", "reachability"],
        "properties": {
            "summary": {"type": "string"},
            "dataflow": {
                "type": "object",
                "additionalProperties": True,
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            },
            "reachability": {
                "type": "object",
                "additionalProperties": True,
                "required": ["summary"],
                "properties": {"summary": {"type": "string"}},
            },
        },
    }
    candidate_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "rule_id",
            "identity_anchor",
            "title",
            "summary",
            "severity",
            "severity_rationale",
            "confidence",
            "confidence_rationale",
            "category",
            "cwe",
            "attack_path",
            "dangerous_operation",
            "root_cause",
            "remediation",
            "evidence",
        ],
        "properties": {
            "rule_id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._/-]*$"},
            "identity_anchor": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._/-]*$"},
            "identity_instance": {"type": "string", "pattern": "^[a-z0-9][a-z0-9._/-]*$"},
            "title": {"type": "string"},
            "summary": {"type": "string"},
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low", "informational"],
            },
            "severity_rationale": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "confidence_rationale": {"type": "string"},
            "category": {"type": "string"},
            "cwe": {
                "type": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": {"type": "string", "pattern": "^CWE-[0-9]+$"},
            },
            "attack_path": attack_path_schema,
            "dangerous_operation": {"type": "string"},
            "root_cause": {"type": "string"},
            "remediation": {"type": "string"},
            "remediation_tests": string_array,
            "preventive_controls": string_array,
            "evidence": evidence_schema,
        },
    }
    rejected_candidate_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "reason"],
        "properties": {
            "candidate_id": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1, "maxLength": 4_000},
        },
    }
    adjudication_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["action"],
        "properties": {
            "action": {
                "type": "string",
                "enum": ["finalize", "targeted_rescan"],
            },
            "accepted_candidate_ids": {
                "type": "array",
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
                "description": "Required only when action is finalize.",
            },
            "rejected_candidates": {
                "type": "array",
                "items": rejected_candidate_schema,
                "description": "Required only when action is finalize.",
            },
            "rescan": {
                "type": "object",
                "additionalProperties": False,
                "required": ["reason", "paths", "questions"],
                "description": "Required only when action is targeted_rescan.",
                "properties": {
                    "reason": {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 4_000,
                    },
                    "paths": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "uniqueItems": True,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "pattern": "^(?!\\.$)(?!/)(?!.*(?:^|/)\\.\\.(?:/|$))(?!.*\\\\).+",
                        },
                    },
                    "questions": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 32,
                        "items": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 1_000,
                        },
                    },
                },
            },
        },
    }
    _register(
        "audit_prepare",
        "Create a reproducible read-only snapshot and initialize a standard static code audit. Never executes target code.",
        audit_prepare,
        [
            _parameter("target_path", ParameterType.STRING, "Absolute local target directory to snapshot."),
            _parameter("include_paths", ParameterType.ARRAY, "Optional relative files or directories to include.", required=False, json_schema=string_array),
            _parameter("exclude_patterns", ParameterType.ARRAY, "Optional relative glob patterns to exclude.", required=False, json_schema=string_array),
            _parameter("max_file_bytes", ParameterType.INTEGER, "Maximum bytes copied per file.", required=False, default=1_048_576),
            _parameter("mode", ParameterType.STRING, "Audit mode. Only standard is currently implemented.", required=False, default="standard", enum=["standard"]),
        ],
    )
    _register(
        "audit_inventory",
        "List a bounded page of files, digests, languages, sizes, and omissions in the session-bound snapshot.",
        audit_inventory,
        [
            _parameter("offset", ParameterType.INTEGER, "Zero-based inventory offset.", required=False, default=0),
            _parameter("limit", ParameterType.INTEGER, "Page size, capped at 500.", required=False, default=500),
        ],
    )
    _register(
        "audit_read",
        "Read at most 400 lines from a relative path in the session-bound snapshot.",
        audit_read,
        [
            _parameter("relative_path", ParameterType.STRING, "Snapshot-relative source path."),
            _parameter("start_line", ParameterType.INTEGER, "One-based first line.", required=False, default=1),
            _parameter("end_line", ParameterType.INTEGER, "Optional one-based last line.", required=False),
        ],
    )
    _register(
        "audit_search",
        "Search literal text in the session-bound snapshot without running target code.",
        audit_search,
        [
            _parameter("query", ParameterType.STRING, "Literal text to search for."),
            _parameter("path_glob", ParameterType.STRING, "Optional relative file glob.", required=False),
            _parameter("case_sensitive", ParameterType.BOOLEAN, "Whether matching is case-sensitive.", required=False, default=False),
            _parameter("max_results", ParameterType.INTEGER, "Maximum matches, capped at 200.", required=False, default=100),
        ],
    )
    _register(
        "audit_threat_model_context",
        "Return the completed source-backed threat model bound to this scan and record that the worker consumed it.",
        audit_threat_model_context,
        [],
    )
    _register(
        "audit_submit_threat_model",
        "Submit one canonical source-backed repository threat model for the bound immutable snapshot.",
        audit_submit_threat_model,
        [
            _parameter(
                "threat_model",
                ParameterType.OBJECT,
                "Canonical threat model with summary, assets, trustBoundaries, attackerCapabilities, securityObjectives, assumptions, and evidence.",
                json_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "summary",
                        "assets",
                        "trustBoundaries",
                        "attackerCapabilities",
                        "securityObjectives",
                        "assumptions",
                        "evidence",
                    ],
                    "properties": {
                        "summary": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 20_000,
                            "pattern": "\\S",
                        },
                        "assets": threat_model_required_array,
                        "trustBoundaries": threat_model_required_array,
                        "attackerCapabilities": threat_model_required_array,
                        "securityObjectives": threat_model_required_array,
                        "assumptions": threat_model_assumptions,
                        "evidence": threat_model_evidence,
                    },
                },
            )
        ],
    )
    _register(
        "audit_verification_subject",
        "Return the candidate and digest-bound evidence assigned to this verifier work unit.",
        audit_verification_subject,
        [],
    )
    _register(
        "audit_submit_candidate",
        "Submit canonical vulnerability semantics with stable identity, CWE taxonomy, attack path, root cause, and digest-bound source evidence.",
        audit_submit_candidate,
        [_parameter("candidate", ParameterType.OBJECT, "Canonical candidate and source evidence.", json_schema=candidate_schema)],
    )
    _register(
        "audit_submit_verdict",
        "Submit an independent confirmed, rejected, or insufficient-evidence verdict.",
        audit_submit_verdict,
        [
            _parameter("candidate_id", ParameterType.STRING, "Candidate identifier from the bound scan."),
            _parameter("verdict", ParameterType.STRING, "Independent verdict.", enum=["confirmed", "rejected", "insufficient_evidence"]),
            _parameter("rationale", ParameterType.STRING, "Evidence-based rationale."),
            _parameter(
                "counter_evidence",
                ParameterType.ARRAY,
                "Optional digest-bound counter-evidence using exact relative_path, blob_digest, start_line, and end_line fields.",
                required=False,
                json_schema=counter_evidence_schema,
            ),
        ],
    )
    _register(
        "audit_submit_coverage",
        "Submit analyzed, failed, and unresolved scope facts for the bound work unit.",
        audit_submit_coverage,
        [
            _parameter("inventoried_paths", ParameterType.ARRAY, "Exact snapshot paths returned by audit_inventory whose inventory page was consumed.", required=False, json_schema=string_array),
            _parameter("analyzed_paths", ParameterType.ARRAY, "Exact existing snapshot paths fully covered by this worker's audit_read or audit_search accesses.", required=False, json_schema=string_array),
            _parameter("failed_paths", ParameterType.ARRAY, "Exact inventory or omission paths that could not be analyzed; never invent paths.", required=False, json_schema=string_array),
            _parameter(
                "open_questions",
                ParameterType.ARRAY,
                "Structured unresolved questions. Use coverage_blocking/true only for incomplete assigned-source analysis; validation limitations and security hypotheses must use false.",
                required=False,
                json_schema=open_questions_schema,
            ),
        ],
    )
    _register(
        "audit_adjudication_context",
        "Return a compact overview or one evidence-backed candidate for parent adjudication.",
        audit_adjudication_context,
        [
            _parameter("scan_id", ParameterType.STRING, "Bound scan identifier."),
            _parameter(
                "candidate_id",
                ParameterType.STRING,
                "Optional candidate identifier. Omit for the overview; provide it to inspect exactly one candidate.",
                required=False,
            ),
        ],
    )
    _register(
        "audit_submit_adjudication",
        "Either classify every candidate and finalize, or direct the single allowed targeted rescan without classification.",
        audit_submit_adjudication,
        [
            _parameter("scan_id", ParameterType.STRING, "Bound scan identifier."),
            _parameter(
                "decision",
                ParameterType.OBJECT,
                "Final parent decision or one bounded targeted-rescan direction.",
                json_schema=adjudication_schema,
            ),
        ],
    )
    for name, description, handler in (
        ("audit_status", "Return trusted status and fact counts for this coordinator session's scan.", audit_status),
        ("audit_finalize", "Deterministically reduce verified candidates and write JSON, Markdown, and SARIF reports.", audit_finalize),
        ("audit_cancel", "Cancel the bound scan and its tracked background workers.", audit_cancel),
    ):
        _register(name, description, handler, [_parameter("scan_id", ParameterType.STRING, "Bound scan identifier.")])
    _register(
        "audit_run_workers",
        "Create and launch isolated standard-audit workers, including the one allowed parent-directed targeted rescan.",
        audit_run_workers,
        [
            _parameter("scan_id", ParameterType.STRING, "Bound scan identifier."),
            _parameter(
                "phase",
                ParameterType.STRING,
                "Worker phase.",
                required=False,
                default="threat_modeling",
                enum=[
                    "threat_modeling",
                    "baseline",
                    "verification",
                    "targeted_rescan",
                ],
            ),
        ],
    )
    _register(
        "audit_wait_workers",
        "Wait up to a bounded timeout for a bound audit worker batch and reconcile trusted status.",
        audit_wait_workers,
        [
            _parameter("batch_id", ParameterType.STRING, "Worker batch identifier."),
            _parameter(
                "timeout_seconds",
                ParameterType.INTEGER,
                "Wait timeout capped at 60 seconds.",
                required=False,
                default=30,
            ),
        ],
    )
