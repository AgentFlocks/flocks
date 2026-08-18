"""Deterministic CLI orchestration for standard code-security audits."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from flocks.config.config import Config
from flocks.project.project import Project
from flocks.provider.provider import Provider
from flocks.session.session import Session
from flocks.session.session_loop import SessionLoop
from flocks.storage.storage import Storage
from flocks.tool.registry import ToolContext, ToolResult
from flocks.utils.langfuse import (
    is_active as langfuse_is_active,
    span_scope,
    trace_scope,
)

from flocks_code_security.paths import outputs_root, runtime_dir
from flocks_code_security.runtime import get_runtime
from flocks_code_security.tools import (
    audit_cancel,
    audit_finalize,
    audit_prepare,
    audit_run_workers,
    audit_status,
    audit_wait_workers,
)


ProgressCallback = Callable[[str, dict[str, Any]], None]
TERMINAL_BATCH_STATUSES = {"completed", "partial", "failed", "cancelled"}


def _emit(
    progress: ProgressCallback | None,
    event: str,
    payload: dict[str, Any],
    *,
    observation_parent: Any = None,
) -> None:
    if progress is not None:
        progress(event, payload)
    if observation_parent is None:
        return
    try:
        observation = span_scope(
            parent=observation_parent,
            name=f"code-security.progress.{event}",
            input=payload,
            metadata={"event": event},
        )
        observation.end(output=payload)
    except Exception:
        # Telemetry is best-effort and must never change scan behavior.
        pass


def _start_phase_observation(parent: Any, phase: str) -> Any:
    if parent is None:
        return None
    try:
        return span_scope(
            parent=parent,
            name=f"code-security.phase.{phase}",
            input={"phase": phase},
            metadata={"phase": phase},
        )
    except Exception:
        return None


def _end_observation(scope: Any, **kwargs: Any) -> None:
    if scope is None:
        return
    try:
        scope.end(**kwargs)
    except Exception:
        pass


def _require_success(result: ToolResult) -> dict[str, Any]:
    if not result.success:
        raise RuntimeError(str(result.error or result.title or "Code audit operation failed"))
    return result.output if isinstance(result.output, dict) else {}


def _parse_model(model: str | None) -> tuple[str, str] | None:
    if model is None:
        return None
    provider_id, separator, model_id = model.strip().partition("/")
    if not separator or not provider_id or not model_id:
        raise ValueError("Model must use provider/model format")
    return provider_id, model_id


async def _resolve_model(model: str | None) -> tuple[str, str]:
    explicit = _parse_model(model)
    if explicit is not None:
        provider_id, model_id = explicit
    else:
        default = await Config.resolve_default_llm()
        if not default:
            raise RuntimeError("No default LLM is configured")
        provider_id = default["provider_id"]
        model_id = default["model_id"]

    await Provider.init()
    config = await Config.get()
    available, reason = await SessionLoop.validate_runtime_model(
        provider_id,
        model_id,
        config=config,
    )
    if not available:
        raise RuntimeError(
            f"LLM is not available: {provider_id}/{model_id} ({reason})"
        )
    return provider_id, model_id


async def _wait_for_batch(
    ctx: ToolContext,
    batch_id: str,
    progress: ProgressCallback | None,
    observation_parent: Any = None,
) -> dict[str, Any]:
    while True:
        output = _require_success(
            await audit_wait_workers(ctx, batch_id, timeout_seconds=10)
        )
        _emit(
            progress,
            "batch.status",
            output,
            observation_parent=observation_parent,
        )
        if output.get("status") in TERMINAL_BATCH_STATUSES:
            return output


async def _run_phase(
    ctx: ToolContext,
    scan_id: str,
    phase: str,
    progress: ProgressCallback | None,
    scan_observation: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    phase_scope = _start_phase_observation(scan_observation, phase)
    phase_parent = None if phase_scope is None else phase_scope.observation
    try:
        batch = _require_success(await audit_run_workers(ctx, scan_id, phase))
        _emit(
            progress,
            "batch.started",
            batch,
            observation_parent=phase_parent,
        )
        terminal = await _wait_for_batch(
            ctx,
            batch["batch_id"],
            progress,
            phase_parent,
        )
        status = _require_success(await audit_status(ctx, scan_id))
        _emit(
            progress,
            "scan.status",
            status,
            observation_parent=phase_parent,
        )
        _end_observation(
            phase_scope,
            output={"batch": terminal, "scan_status": status},
        )
        return terminal, status
    except BaseException as exc:
        _end_observation(
            phase_scope,
            output={"phase": phase, "error": str(exc)},
            level="ERROR",
            status_message=str(exc),
        )
        raise


async def _run_pipeline(
    ctx: ToolContext,
    target: Path,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    scan_id: str | None = None
    scan_scope = None
    try:
        prepared = _require_success(await audit_prepare(ctx, str(target)))
        scan_id = prepared["scan_id"]
        if langfuse_is_active():
            try:
                scan_scope = trace_scope(
                    name="code-security.scan",
                    session_id=scan_id,
                    tags=[
                        "feature:code-security",
                        f"scan:{scan_id}",
                        "mode:standard",
                    ],
                    input={
                        "scan_id": scan_id,
                        "target": str(target),
                        "mode": "standard",
                        "snapshot": prepared.get("snapshot", {}),
                    },
                    metadata={
                        "scan_id": scan_id,
                        "target_name": target.name,
                        "mode": "standard",
                    },
                )
            except Exception:
                scan_scope = None
        scan_observation = None if scan_scope is None else scan_scope.observation
        _emit(
            progress,
            "scan.prepared",
            prepared,
            observation_parent=scan_observation,
        )

        _threat_batch, status = await _run_phase(
            ctx,
            scan_id,
            "threat_modeling",
            progress,
            scan_observation,
        )
        if status.get("threat_model_status") != "completed":
            raise RuntimeError("Threat-modeling phase did not produce a trusted model")

        _baseline_batch, status = await _run_phase(
            ctx,
            scan_id,
            "baseline",
            progress,
            scan_observation,
        )
        unverified = int(status.get("counts", {}).get("unverified_candidates", 0))
        while unverified > 0:
            _verification_batch, status = await _run_phase(
                ctx,
                scan_id,
                "verification",
                progress,
                scan_observation,
            )
            remaining = int(
                status.get("counts", {}).get("unverified_candidates", 0)
            )
            if remaining >= unverified:
                raise RuntimeError("Verification phase made no progress")
            unverified = remaining

        finalized = _require_success(await audit_finalize(ctx, scan_id))
        _emit(
            progress,
            "scan.finalized",
            finalized,
            observation_parent=scan_observation,
        )
        _end_observation(scan_scope, output=finalized)
        return finalized
    except BaseException as exc:
        cancelled_output: dict[str, Any] | None = None
        if scan_id is not None:
            try:
                cancelled = await asyncio.shield(audit_cancel(ctx, scan_id))
                if cancelled.success and isinstance(cancelled.output, dict):
                    cancelled_output = cancelled.output
                    _emit(
                        progress,
                        "scan.cancelled",
                        cancelled_output,
                        observation_parent=(
                            None if scan_scope is None else scan_scope.observation
                        ),
                    )
            except Exception:
                pass
        _end_observation(
            scan_scope,
            output={
                "scan_id": scan_id,
                "error": str(exc),
                "cancellation": cancelled_output,
            },
            level="ERROR",
            status_message=str(exc),
        )
        raise


async def run_standard_audit(
    target_path: Path,
    *,
    model: str | None = None,
    progress: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Run the trusted standard audit pipeline and follow it to finalization."""
    target = target_path.expanduser().resolve()
    if not target.is_dir():
        raise ValueError(f"Audit target is not a directory: {target}")

    await Storage.init()
    provider_id, model_id = await _resolve_model(model)
    model_ref = {"providerID": provider_id, "modelID": model_id}
    project = (await Project.from_directory(str(runtime_dir())))["project"]
    parent = await Session.create(
        project_id=project.id,
        directory=str(runtime_dir()),
        title=f"Code security audit: {target.name}",
        agent="code-security",
        provider=provider_id,
        model=model_id,
        model_pinned=True,
    )
    ctx = ToolContext(
        session_id=parent.id,
        message_id=f"cli-audit-{uuid4().hex}",
        agent="code-security",
        extra={
            "agent_execution_session": True,
            "model": model_ref,
            "suppress_parent_completion": True,
        },
    )
    return await _run_pipeline(ctx, target, progress)


def scan_status(scan_id: str) -> dict[str, Any]:
    """Return persisted trusted progress without mutating worker state."""
    status = get_runtime().store.scan_status(scan_id)
    root = outputs_root()
    for day_dir in sorted(root.iterdir(), reverse=True) if root.is_dir() else ():
        candidate = day_dir / "code-security" / scan_id
        report = candidate / "report.md"
        if report.is_file():
            status["output_dir"] = str(candidate)
            status["report_path"] = str(report)
            status["sarif_path"] = str(candidate / "report.sarif")
            break
    return status
