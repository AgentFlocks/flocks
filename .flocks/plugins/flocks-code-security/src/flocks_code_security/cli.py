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


def _emit(progress: ProgressCallback | None, event: str, payload: dict[str, Any]) -> None:
    if progress is not None:
        progress(event, payload)


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
) -> dict[str, Any]:
    while True:
        output = _require_success(
            await audit_wait_workers(ctx, batch_id, timeout_seconds=10)
        )
        _emit(progress, "batch.status", output)
        if output.get("status") in TERMINAL_BATCH_STATUSES:
            return output


async def _run_phase(
    ctx: ToolContext,
    scan_id: str,
    phase: str,
    progress: ProgressCallback | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = _require_success(await audit_run_workers(ctx, scan_id, phase))
    _emit(progress, "batch.started", batch)
    terminal = await _wait_for_batch(ctx, batch["batch_id"], progress)
    status = _require_success(await audit_status(ctx, scan_id))
    _emit(progress, "scan.status", status)
    return terminal, status


async def _run_pipeline(
    ctx: ToolContext,
    target: Path,
    progress: ProgressCallback | None,
) -> dict[str, Any]:
    scan_id: str | None = None
    try:
        prepared = _require_success(await audit_prepare(ctx, str(target)))
        scan_id = prepared["scan_id"]
        _emit(progress, "scan.prepared", prepared)

        _threat_batch, status = await _run_phase(
            ctx, scan_id, "threat_modeling", progress
        )
        if status.get("threat_model_status") != "completed":
            raise RuntimeError("Threat-modeling phase did not produce a trusted model")

        _baseline_batch, status = await _run_phase(
            ctx, scan_id, "baseline", progress
        )
        unverified = int(status.get("counts", {}).get("unverified_candidates", 0))
        while unverified > 0:
            _verification_batch, status = await _run_phase(
                ctx, scan_id, "verification", progress
            )
            remaining = int(
                status.get("counts", {}).get("unverified_candidates", 0)
            )
            if remaining >= unverified:
                raise RuntimeError("Verification phase made no progress")
            unverified = remaining

        finalized = _require_success(await audit_finalize(ctx, scan_id))
        _emit(progress, "scan.finalized", finalized)
        return finalized
    except BaseException:
        if scan_id is not None:
            try:
                cancelled = await asyncio.shield(audit_cancel(ctx, scan_id))
                if cancelled.success and isinstance(cancelled.output, dict):
                    _emit(progress, "scan.cancelled", cancelled.output)
            except Exception:
                pass
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
