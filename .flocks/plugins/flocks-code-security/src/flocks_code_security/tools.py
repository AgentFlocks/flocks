"""Flocks tool handlers and registration for static code audits."""

from __future__ import annotations

import asyncio
import hashlib
from importlib import resources
import sqlite3
from collections import Counter
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

from flocks_code_security.reporting import ReportWriter
from flocks_code_security.orchestration import (
    baseline_prompt,
    plan_baseline_units,
    plan_verification_units,
    verification_prompt,
)
from flocks_code_security.runtime import get_runtime


def _ruleset_digest() -> str:
    digest = hashlib.sha256()
    digest.update(b"flocks-code-security-rules-v1\0")
    package_root = resources.files("flocks_code_security")
    prompt_root = package_root.joinpath("prompts")
    for name in ("baseline.md", "coordinator.md", "investigator.md", "verifier.md"):
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(prompt_root.joinpath(name).read_bytes())
        digest.update(b"\0")
    digest.update(b"orchestration.py\0")
    digest.update(package_root.joinpath("orchestration.py").read_bytes())
    digest.update(b"\0")
    return digest.hexdigest()


RULESET_DIGEST = _ruleset_digest()
COORDINATOR_ROLE = {"coordinator"}
SOURCE_SUBMIT_ROLES = {"baseline", "investigator"}
VERIFIER_ROLE = {"verifier"}
ROLE_AGENTS = {
    "coordinator": "code-security",
    "baseline": "code-security-baseline",
    "investigator": "code-security-investigator",
    "verifier": "code-security-verifier",
}
STORE_ERRORS = (OSError, ValueError, sqlite3.Error)
WORKER_TERMINAL_STATUSES = {"completed", "partial", "failed", "cancelled"}
LAUNCHING_BATCH_IDS: set[str] = set()
REGISTERED_AUDIT_TOOLS: dict[
    str,
    tuple[Tool, Callable[..., Awaitable[ToolResult]]],
] = {}


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
        _require_agent_execution(ctx, {"baseline", "investigator", "verifier"})
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
        _require_agent_execution(ctx, {"baseline", "investigator", "verifier"})
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
        _require_agent_execution(ctx, {"baseline", "investigator", "verifier"})
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


async def audit_submit_candidate(ctx: ToolContext, candidate: dict[str, Any]) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, SOURCE_SUBMIT_ROLES)
        binding = runtime.store.require_binding(ctx.session_id, SOURCE_SUBMIT_ROLES)
        if not isinstance(candidate, dict):
            raise ValueError("candidate must be an object")
        required = (
            "rule_id",
            "title",
            "severity",
            "confidence",
            "attack_path",
            "dangerous_operation",
            "remediation",
            "evidence",
        )
        missing = [name for name in required if candidate.get(name) in (None, "")]
        if missing:
            raise ValueError("Missing candidate fields: " + ", ".join(missing))
        severity = str(candidate["severity"]).lower()
        if severity not in {"critical", "high", "medium", "low", "info"}:
            raise ValueError("Unsupported severity")
        confidence = float(candidate["confidence"])
        if not 0 <= confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        for field in required[:-1]:
            if len(str(candidate[field])) > 10_000:
                raise ValueError(f"Candidate field {field} is too long")
        if not isinstance(candidate["evidence"], list) or len(candidate["evidence"]) > 50:
            raise ValueError("A candidate may contain between 1 and 50 evidence references")
        evidence = await asyncio.to_thread(
            runtime.source.validate_evidence,
            binding,
            candidate["evidence"],
        )
        payload = {
            "rule_id": str(candidate["rule_id"]),
            "title": str(candidate["title"]),
            "severity": severity,
            "confidence": confidence,
            "attack_path": str(candidate["attack_path"]),
            "dangerous_operation": str(candidate["dangerous_operation"]),
            "remediation": str(candidate["remediation"]),
        }
        candidate_id = await asyncio.to_thread(runtime.store.save_candidate, binding, payload, evidence)
        return ToolResult(
            success=True,
            output={"candidate_id": candidate_id, "evidence_count": len(evidence)},
            title=f"Submitted candidate {candidate_id}",
        )
    except (OSError, TypeError, ValueError, sqlite3.Error) as exc:
        return _error(exc, title="Candidate submission failed")


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
    open_questions: list[str] | None = None,
) -> ToolResult:
    runtime = get_runtime()
    try:
        _require_agent_execution(ctx, SOURCE_SUBMIT_ROLES)
        binding = runtime.store.require_binding(ctx.session_id, SOURCE_SUBMIT_ROLES)
        path_groups = {
            "inventoried_paths": inventoried_paths or [],
            "analyzed_paths": analyzed_paths or [],
            "failed_paths": failed_paths or [],
        }
        if any(len(items) > 2_000 for items in path_groups.values()):
            raise ValueError("Each coverage path list may contain at most 2000 entries")
        questions = [str(item)[:1000] for item in (open_questions or [])]
        if len(questions) > 100:
            raise ValueError("At most 100 open questions are allowed")

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


async def audit_run_workers(ctx: ToolContext, scan_id: str, phase: str = "baseline") -> ToolResult:
    runtime = get_runtime()
    try:
        binding = _coordinator_binding(ctx, scan_id)
        if phase == "baseline":
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
        else:
            raise ValueError(
                "Focused investigation workers are not implemented in the standard audit"
            )
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
    child = await Session.create(
        project_id=parent.project_id,
        directory=parent.directory,
        title=f"Code security {phase} worker",
        parent_id=parent.id,
        agent=agent_name,
        category="task",
    )
    await asyncio.to_thread(
        runtime.store.bind_session,
        session_id=child.id,
        scan_id=scan_id,
        snapshot_id=snapshot_id,
        role=unit["role"],
        work_unit_id=unit["work_unit_id"],
    )
    if phase == "baseline":
        prompt = baseline_prompt(snapshot_id=snapshot_id, paths=unit["paths"])
    elif phase == "verification" and candidate is not None:
        prompt = verification_prompt(snapshot_id=snapshot_id, candidate=candidate)
    else:
        raise ValueError("Worker prompt data is incomplete")
    await Message.create(
        session_id=child.id,
        role=MessageRole.USER,
        content=prompt,
        agent=agent_name,
    )
    model = ctx.extra.get("model")
    model = model if isinstance(model, dict) else {}
    provider_id = model.get("providerID") or parent.provider
    model_id = model.get("modelID") or parent.model
    manager = _background_manager()
    task = await manager.run_existing_session(
        session_id=child.id,
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
            next_status = "failed"
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
    tool = Tool(
        info=ToolInfo(
            name=name,
            description=description,
            category=ToolCategory.CUSTOM,
            parameters=parameters,
            provider="flocks-code-security",
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
    string_array = {"type": "array", "items": {"type": "string"}}
    object_array = {"type": "array", "items": {"type": "object", "additionalProperties": True}}
    _register(
        "audit_prepare",
        "Create a reproducible read-only snapshot and initialize a standard static code audit. Never executes target code.",
        audit_prepare,
        [
            _parameter("target_path", ParameterType.STRING, "Local target directory to snapshot."),
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
        "audit_submit_candidate",
        "Submit a structured vulnerability candidate with digest-bound source evidence.",
        audit_submit_candidate,
        [_parameter("candidate", ParameterType.OBJECT, "Structured candidate and evidence references.", json_schema={"type": "object", "additionalProperties": True})],
    )
    _register(
        "audit_submit_verdict",
        "Submit an independent confirmed, rejected, or insufficient-evidence verdict.",
        audit_submit_verdict,
        [
            _parameter("candidate_id", ParameterType.STRING, "Candidate identifier from the bound scan."),
            _parameter("verdict", ParameterType.STRING, "Independent verdict.", enum=["confirmed", "rejected", "insufficient_evidence"]),
            _parameter("rationale", ParameterType.STRING, "Evidence-based rationale."),
            _parameter("counter_evidence", ParameterType.ARRAY, "Optional digest-bound counter-evidence.", required=False, json_schema=object_array),
        ],
    )
    _register(
        "audit_submit_coverage",
        "Submit analyzed, failed, and unresolved scope facts for the bound work unit.",
        audit_submit_coverage,
        [
            _parameter("inventoried_paths", ParameterType.ARRAY, "Inventoried snapshot paths.", required=False, json_schema=string_array),
            _parameter("analyzed_paths", ParameterType.ARRAY, "Analyzed snapshot paths.", required=False, json_schema=string_array),
            _parameter("failed_paths", ParameterType.ARRAY, "Paths that could not be analyzed.", required=False, json_schema=string_array),
            _parameter("open_questions", ParameterType.ARRAY, "Unresolved audit questions.", required=False, json_schema=string_array),
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
        "Create and launch isolated baseline or verification worker sessions for the bound scan.",
        audit_run_workers,
        [
            _parameter("scan_id", ParameterType.STRING, "Bound scan identifier."),
            _parameter("phase", ParameterType.STRING, "Worker phase.", required=False, default="baseline", enum=["baseline", "verification"]),
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
