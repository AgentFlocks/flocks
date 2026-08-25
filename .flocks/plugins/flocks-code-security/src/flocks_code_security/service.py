"""Unified service for CLI, public-tool, and WebUI code-security audits."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from flocks.project.project import Project
from flocks.session.session import Session
from flocks.storage.storage import Storage
from flocks.tool.registry import ToolContext
from flocks.workspace.manager import WorkspaceManager

from flocks_code_security.cli import (
    AuditOrchestrator,
    _require_enabled_audit_tools,
    _require_success,
    _resolve_model,
)
from flocks_code_security.artifact_integrity import find_output_directory
from flocks_code_security.paths import data_dir, outputs_root, runtime_dir
from flocks_code_security.reporting import ReportWriter
from flocks_code_security.runtime import get_runtime
from flocks_code_security.store import process_identity
from flocks_code_security.tools import audit_cancel, audit_prepare


logger = logging.getLogger(__name__)

PUBLIC_SCHEMA_VERSION = "flocks.code-security.tool.v1"
MAX_PROJECTED_EVENT_BYTES = 60 * 1024
MAX_KNOWLEDGE_BASE_BYTES = 32 * 1024
TERMINAL_SCAN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
PUBLIC_SCAN_STATUSES = {"running", *TERMINAL_SCAN_STATUSES}
PUBLIC_PHASES = {
    "probing": "dynamic_validation",
}
ARTIFACT_FILENAMES = {
    "report_markdown": "report.md",
    "sarif": "report.sarif",
    "findings": "findings.json",
    "coverage": "coverage.json",
    "scan_manifest": "scan-manifest.json",
    "threat_model": "threat-model.json",
    "adjudication": "adjudication.json",
    "dynamic_validation": "dynamic-validation.json",
}
PROJECTED_ARTIFACT_KINDS = {
    "snapshot_summary",
    "threat_model",
    "candidate_index",
    "verification_index",
    "dynamic_validation",
    "adjudication",
    "coverage",
}
EVENT_TITLES = {
    "scan.prepared": "不可变源码快照已创建",
    "batch.started": "审计阶段已开始",
    "batch.status": "审计阶段状态已更新",
    "scan.status": "扫描状态已更新",
    "dynamic.started": "动态验证已开始",
    "dynamic.preflight_started": "动态验证预检已开始",
    "dynamic.preflight_completed": "动态验证预检已通过",
    "dynamic.execution_started": "受限 Docker 探测已开始",
    "dynamic.execution_completed": "受限 Docker 探测已完成",
    "dynamic.completed": "动态验证已完成",
    "dynamic.failed": "动态验证执行失败",
    "dynamic.cancelled": "动态验证已取消",
    "adjudication.started": "主智能体裁决已开始",
    "adjudication.failed": "主智能体裁决失败",
    "scan.adjudicated": "主智能体已提交裁决",
    "scan.finalized": "最终产物已完成完整性校验",
    "scan.cancelled": "代码审计已取消",
}


class AuditServiceError(RuntimeError):
    """Stable service error mapped by public tools and HTTP routes."""

    def __init__(self, code: str, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AuditCaller:
    subject: str
    source: str
    is_admin: bool = False
    workspace_ref: str | None = None
    authorized_root: Path | None = None


@dataclass(frozen=True)
class KnowledgeBaseInput:
    display_name: str
    content: str


@dataclass(frozen=True)
class StartScanRequest:
    target_path: Path
    model: str | None = None
    include_paths: tuple[str, ...] = (".",)
    exclude_patterns: tuple[str, ...] = ()
    max_file_bytes: int = 1_048_576
    dynamic_enabled: bool = False
    coverage_policy: str = "evidence_backed_partial"
    verification_votes: int = 1
    idempotency_key: str | None = None
    knowledge_base: KnowledgeBaseInput | None = None


@dataclass
class _ActiveScan:
    ctx: ToolContext
    task: asyncio.Task[dict[str, Any]]
    owner_token: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _elapsed_ms(started_at: str, finished_at: str | None = None) -> int:
    start = datetime.fromisoformat(started_at)
    finish = datetime.fromisoformat(finished_at) if finished_at else _utc_now()
    return max(0, int((finish - start).total_seconds() * 1000))


def _public_lifecycle(status: str) -> str:
    return "running" if status == "reducing" else status


def _public_phase(phase: str | None) -> str | None:
    if phase is None:
        return None
    return PUBLIC_PHASES.get(phase, phase)


def _effective_finished_at(scan: dict[str, Any]) -> str | None:
    finished_at = scan.get("finished_at")
    if isinstance(finished_at, str) and finished_at:
        return finished_at
    if scan.get("status") in TERMINAL_SCAN_STATUSES:
        updated_at = scan.get("updated_at")
        if isinstance(updated_at, str) and updated_at:
            return updated_at
    return None


def _final_finding_metric(
    *,
    lifecycle_status: str,
    integrity_status: str,
    dynamic_enabled: bool,
    finding_summary: dict[str, Any],
    dynamic_summary: dict[str, Any],
) -> dict[str, Any]:
    if lifecycle_status != "completed":
        return {"final_finding_count": None, "final_finding_basis": "审计完成后确定"}
    if integrity_status != "valid":
        return {"final_finding_count": None, "final_finding_basis": "最终结果不可用"}

    dynamic_execution_count = 0
    for key in ("completed", "inconclusive"):
        value = dynamic_summary.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            dynamic_execution_count += value
    uses_dynamic_result = dynamic_enabled and dynamic_summary.get("status") != "skipped" and dynamic_execution_count > 0
    value = finding_summary.get("dynamic_reproduced" if uses_dynamic_result else "total")
    if not isinstance(value, int) or isinstance(value, bool):
        return {"final_finding_count": None, "final_finding_basis": "最终结果不可用"}
    return {
        "final_finding_count": max(0, value),
        "final_finding_basis": "动态验证复现" if uses_dynamic_result else "静态验证确认",
    }


def _remove_owned_tree(path: Path | None, *, root: Path, expected_name: str) -> None:
    if path is None or not path.exists():
        return
    if path.is_symlink():
        raise OSError(f"Refusing to delete symbolic link: {path}")
    resolved_root = root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if resolved.name != expected_name or not resolved.is_relative_to(resolved_root):
        raise OSError(f"Refusing to delete path outside owned storage: {resolved}")
    for child in resolved.rglob("*"):
        if child.is_symlink():
            continue
        child.chmod(0o700 if child.is_dir() else 0o600)
    resolved.chmod(0o700)
    shutil.rmtree(resolved)


class _ProgressRecorder:
    """Project trusted orchestrator callbacks into durable phases and events."""

    def __init__(
        self,
        scan_id: str,
        *,
        dynamic_enabled: bool,
        downstream: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.scan_id = scan_id
        self.dynamic_enabled = dynamic_enabled
        self.store = get_runtime().store
        self.phase_runs: dict[str, str] = {}
        self.prepared_recorded = False
        self.downstream = downstream

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._record(event, self._safe_payload(payload))
        except Exception as exc:
            # UI projection must never change the trusted audit execution.
            if event == "scan.finalized":
                self._record_terminal_fallback(exc)
        if self.downstream is not None:
            try:
                self.downstream(event, payload)
            except Exception:
                # Progress rendering is best-effort and cannot abort an audit.
                pass

    def _record(self, event: str, payload: dict[str, Any]) -> None:
        phase_run_id: str | None = None
        event_type = event
        level = "info"

        if event == "scan.prepared":
            if self.prepared_recorded:
                return
            self.prepared_recorded = True
            snapshot_phase = self.store.start_phase_run(
                self.scan_id,
                "snapshot",
                summary=payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {},
            )
            phase_run_id = snapshot_phase["phase_run_id"]
            self.store.finish_phase_run(phase_run_id, "completed")
            snapshot_event = self.store.append_scan_event(
                self.scan_id,
                "scan.snapshot_ready",
                EVENT_TITLES[event],
                payload,
                phase_run_id=phase_run_id,
            )
            latest_seq = snapshot_event["seq"]
            if not self.dynamic_enabled:
                skipped = self.store.start_phase_run(
                    self.scan_id,
                    "dynamic_validation",
                    summary={"reason": "disabled_by_request"},
                )
                self.store.finish_phase_run(
                    skipped["phase_run_id"],
                    "skipped",
                    summary={"reason": "disabled_by_request"},
                )
                skipped_event = self.store.append_scan_event(
                    self.scan_id,
                    "phase.skipped",
                    "动态验证已跳过",
                    {"phase": "dynamic_validation", "reason": "disabled_by_request"},
                    phase_run_id=skipped["phase_run_id"],
                )
                latest_seq = skipped_event["seq"]
                self.store.set_current_phase(self.scan_id, "snapshot")
            self._publish_change(latest_seq)
            return
        elif event == "dynamic.started":
            run = self.store.start_phase_run(
                self.scan_id,
                "dynamic_validation",
                summary=payload,
            )
            phase_run_id = run["phase_run_id"]
            self.phase_runs["dynamic_validation"] = phase_run_id
            event_type = "phase.started"
        elif event == "batch.started":
            phase = _public_phase(str(payload.get("phase") or ""))
            if phase:
                phase_run_id = (
                    self.phase_runs.get(phase)
                    if phase == "dynamic_validation"
                    else None
                )
                if phase_run_id is None:
                    attempt_ordinal = payload.get("attempt_ordinal")
                    run = self.store.start_phase_run(
                        self.scan_id,
                        phase,
                        summary=payload,
                        ordinal=(
                            attempt_ordinal
                            if isinstance(attempt_ordinal, int) and attempt_ordinal > 0
                            else None
                        ),
                    )
                    phase_run_id = run["phase_run_id"]
                    self.phase_runs[phase] = phase_run_id
                self.phase_runs[str(payload.get("batch_id") or phase)] = phase_run_id
                event_type = "dynamic.planning_started" if phase == "dynamic_validation" else "phase.started"
        elif event == "batch.status":
            phase = _public_phase(str(payload.get("phase") or ""))
            batch_key = str(payload.get("batch_id") or phase or "")
            phase_run_id = self.phase_runs.get(batch_key)
            status = str(payload.get("status") or "running")
            if phase == "dynamic_validation" and status in {
                "completed",
                "partial",
                "failed",
                "cancelled",
            }:
                event_type = f"dynamic.planning_{status}"
                level = "error" if status == "failed" else "warning" if status != "completed" else "info"
            elif phase_run_id and status in {"completed", "partial", "failed", "cancelled"}:
                self.store.finish_phase_run(phase_run_id, status, summary=payload)
                event_type = f"phase.{status}"
                level = "error" if status == "failed" else "warning" if status == "partial" else "info"
            else:
                event_type = "phase.progress"
        elif event in {
            "dynamic.preflight_started",
            "dynamic.preflight_completed",
            "dynamic.execution_started",
            "dynamic.execution_completed",
        }:
            phase_run_id = self.phase_runs.get("dynamic_validation")
        elif event in {"dynamic.completed", "dynamic.failed", "dynamic.cancelled"}:
            phase_run_id = self.phase_runs.get("dynamic_validation")
            terminal = event.removeprefix("dynamic.")
            if phase_run_id:
                self.store.finish_phase_run(
                    phase_run_id,
                    terminal,
                    summary=payload,
                )
            level = "error" if terminal == "failed" else "warning" if terminal == "cancelled" else "info"
        elif event == "adjudication.started":
            run = self.store.start_phase_run(self.scan_id, "adjudication", summary=payload)
            phase_run_id = run["phase_run_id"]
            self.phase_runs["adjudication"] = phase_run_id
            event_type = "phase.started"
        elif event == "adjudication.failed":
            phase_run_id = self.phase_runs.get("adjudication")
            if phase_run_id:
                self.store.finish_phase_run(phase_run_id, "failed", summary=payload)
            level = "error"
        elif event == "scan.adjudicated":
            phase_run_id = self.phase_runs.get("adjudication")
            if phase_run_id is None:
                run = self.store.start_phase_run(self.scan_id, "adjudication", summary=payload)
                phase_run_id = run["phase_run_id"]
            self.store.finish_phase_run(phase_run_id, "completed", summary=payload)
            event_type = "adjudication.submitted"
            if payload.get("action") == "finalize":
                finalization = self.store.start_phase_run(self.scan_id, "finalization")
                self.phase_runs["finalization"] = finalization["phase_run_id"]
        elif event == "scan.finalized":
            phase_run_id = self.phase_runs.get("finalization")
            if phase_run_id:
                self.store.finish_phase_run(phase_run_id, "completed", summary=payload)
            event_type = "scan.completed"
        elif event == "scan.cancelled":
            # The service records cancellation only after it knows it was user initiated.
            return

        stored = self.store.append_scan_event(
            self.scan_id,
            event_type,
            EVENT_TITLES.get(event, EVENT_TITLES.get(event_type, "代码审计状态已更新")),
            payload,
            level=level,
            phase_run_id=phase_run_id,
        )
        self._publish_change(stored["seq"])

    def _publish_change(self, latest_seq: int) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def publish() -> None:
            try:
                from flocks.server.routes.event import publish_event

                await publish_event(
                    "code-security.scan.changed",
                    {"scanId": self.scan_id, "latestEventSeq": latest_seq},
                )
            except Exception:
                return

        loop.create_task(publish())

    def _record_terminal_fallback(self, exc: Exception) -> None:
        try:
            stored = self.store.append_scan_event(
                self.scan_id,
                "scan.completed",
                EVENT_TITLES["scan.finalized"],
                {
                    "scan_id": self.scan_id,
                    "status": "completed",
                    "payload_truncated": True,
                    "projection_error": type(exc).__name__,
                },
            )
        except Exception:
            return
        self._publish_change(stored["seq"])

    @classmethod
    def _safe_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "scan_id",
            "status",
            "dynamic_enabled",
            "coverage_policy",
            "snapshot",
            "batch_id",
            "phase",
            "attempt_ordinal",
            "status_counts",
            "launched_workers",
            "counts",
            "threat_model_status",
            "integrity_status",
            "adjudication_round",
            "execution",
            "action",
            "accepted_candidate_ids",
            "rejected_candidates",
            "rescan",
            "finding_count",
            "finding_summaries",
            "pending_count",
            "deferred_count",
            "coverage_completeness",
            "cancelled_workers",
            "candidate_count",
            "error_type",
            "reason",
        }
        safe = {key: value for key, value in payload.items() if key in allowed}
        if cls._payload_size(safe) <= MAX_PROJECTED_EVENT_BYTES:
            return safe

        compact: dict[str, Any] = {}
        truncated_fields: list[str] = []
        for key, value in safe.items():
            if isinstance(value, str):
                compact[key] = value[:1_000]
            elif value is None or isinstance(value, (bool, int, float)):
                compact[key] = value
            elif key in {"counts", "status_counts"} and cls._payload_size({key: value}) <= 8 * 1024:
                compact[key] = value
            else:
                truncated_fields.append(key)
        compact["payload_truncated"] = True
        compact["truncated_fields"] = truncated_fields
        return compact

    @staticmethod
    def _payload_size(payload: dict[str, Any]) -> int:
        return len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))


class AuditService:
    """Single execution and read-model boundary for code-security audits."""

    def __init__(self) -> None:
        self.runtime = get_runtime()
        self.store = self.runtime.store
        self._active: dict[str, _ActiveScan] = {}
        self._start_lock = asyncio.Lock()

    def recover_orphaned_scans(self) -> list[str]:
        scan_ids = self.store.recover_interrupted_scans(
            active_owner_tokens={item.owner_token for item in self._active.values()},
        )
        for scan_id in scan_ids:
            for phase in self.store.list_phase_runs(scan_id):
                if phase["status"] != "running":
                    continue
                failed_phase = self.store.finish_phase_run(
                    phase["phase_run_id"],
                    "failed",
                    summary={"code": "scan_interrupted"},
                )
                self.store.append_scan_event(
                    scan_id,
                    "phase.failed",
                    "审计阶段因进程中断而停止",
                    {"phase": failed_phase["phase"], "code": "scan_interrupted"},
                    level="warning",
                    phase_run_id=failed_phase["phase_run_id"],
                )
            event = self.store.append_scan_event(
                scan_id,
                "scan.interrupted",
                "审计进程已中断",
                {"code": "scan_interrupted"},
                level="warning",
            )
            _ProgressRecorder(scan_id, dynamic_enabled=False)._publish_change(event["seq"])
        return scan_ids

    async def start_scan(
        self,
        request: StartScanRequest,
        caller: AuditCaller,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if not caller.subject.strip() or len(caller.subject) > 256:
            raise AuditServiceError("invalid_caller", "Audit caller identity is invalid", status_code=403)
        if not caller.is_admin:
            raise AuditServiceError(
                "scan_start_forbidden",
                "Only administrators may start code-security audits",
                status_code=403,
            )
        target = self._validate_target(request.target_path)
        self._require_authorized_target(target, caller.authorized_root)
        normalized = StartScanRequest(
            target_path=target,
            model=(request.model or "").strip() or None,
            include_paths=self._validate_relative_paths(request.include_paths, "include_paths"),
            exclude_patterns=self._validate_exclude_patterns(request.exclude_patterns),
            max_file_bytes=request.max_file_bytes,
            dynamic_enabled=bool(request.dynamic_enabled),
            coverage_policy=str(request.coverage_policy or "").strip(),
            verification_votes=request.verification_votes,
            idempotency_key=(request.idempotency_key or "").strip() or None,
            knowledge_base=self._validate_knowledge_base(request.knowledge_base),
        )
        if normalized.max_file_bytes < 1 or normalized.max_file_bytes > 50 * 1024 * 1024:
            raise AuditServiceError(
                "invalid_parameter",
                "max_file_bytes must be between 1 byte and 50 MiB",
            )
        if normalized.model and len(normalized.model) > 256:
            raise AuditServiceError("invalid_parameter", "model may contain at most 256 characters")
        if normalized.coverage_policy not in {
            "evidence_backed_partial",
            "exhaustive",
        }:
            raise AuditServiceError("invalid_parameter", "Unsupported coverage_policy")
        if (
            not isinstance(normalized.verification_votes, int)
            or isinstance(normalized.verification_votes, bool)
            or not 1 <= normalized.verification_votes <= 5
        ):
            raise AuditServiceError(
                "invalid_parameter",
                "verification_votes must be between 1 and 5",
            )
        if normalized.idempotency_key and len(normalized.idempotency_key) > 256:
            raise AuditServiceError("invalid_parameter", "idempotency_key may contain at most 256 characters")
        digest = self._request_digest(normalized)

        async with self._start_lock:
            existing = self._idempotent_scan(normalized, caller, digest)
            if existing is not None:
                return await self.get_scan(existing["scan_id"], caller)

            ctx = await self._create_execution_context(normalized, caller)
            prepare_result = await audit_prepare(
                ctx,
                str(normalized.target_path),
                include_paths=list(normalized.include_paths),
                exclude_patterns=list(normalized.exclude_patterns) or None,
                max_file_bytes=normalized.max_file_bytes,
                dynamic_enabled=normalized.dynamic_enabled,
                coverage_policy=normalized.coverage_policy,
                verification_votes=normalized.verification_votes,
            )
            try:
                prepared = _require_success(prepare_result)
            except RuntimeError as exc:
                raise AuditServiceError("preparation_failed", str(exc)) from exc

            scan_id = str(prepared["scan_id"])
            owner_token = f"task_{uuid4().hex}"
            knowledge_base = self._knowledge_base_record(normalized.knowledge_base)
            try:
                self.store.set_scan_request_metadata(
                    scan_id,
                    owner_subject=caller.subject,
                    request_source=caller.source,
                    workspace_ref=caller.workspace_ref,
                    idempotency_key=normalized.idempotency_key,
                    request_digest=digest,
                    task_owner_pid=os.getpid(),
                    task_owner_token=owner_token,
                    task_owner_identity=process_identity(os.getpid()),
                    knowledge_base=knowledge_base,
                )
            except ValueError as exc:
                self._discard_prepared_scan(scan_id, prepared)
                existing = self._idempotent_scan(normalized, caller, digest)
                if existing is not None:
                    return await self.get_scan(existing["scan_id"], caller)
                raise AuditServiceError("idempotency_conflict", str(exc), status_code=409) from exc

            if knowledge_base is not None:
                prepared = {
                    **prepared,
                    "audit_mode": "knowledge_guided",
                    "knowledge_base": {key: value for key, value in knowledge_base.items() if key != "content"},
                }

            recorder = _ProgressRecorder(
                scan_id,
                dynamic_enabled=normalized.dynamic_enabled,
                downstream=progress,
            )
            recorder("scan.prepared", prepared)
            task = asyncio.create_task(
                self._run_background(normalized, ctx, prepared, recorder),
                name=f"code-security:{scan_id}",
            )
            task.add_done_callback(self._retrieve_background_task_result)
            self._active[scan_id] = _ActiveScan(
                ctx=ctx,
                task=task,
                owner_token=owner_token,
            )

        return await self.get_scan(scan_id, caller)

    async def run_scan(
        self,
        request: StartScanRequest,
        caller: AuditCaller,
        *,
        progress: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        started = await self.start_scan(request, caller, progress=progress)
        scan_id = started["scan"]["scan_id"]
        active = self._active.get(scan_id)
        if active is not None:
            return await active.task
        return await self.get_result(scan_id, caller)

    @staticmethod
    def _retrieve_background_task_result(
        task: asyncio.Task[dict[str, Any]],
    ) -> None:
        if not task.cancelled():
            task.exception()

    async def _run_background(
        self,
        request: StartScanRequest,
        ctx: ToolContext,
        prepared: dict[str, Any],
        recorder: _ProgressRecorder,
    ) -> dict[str, Any]:
        scan_id = str(prepared["scan_id"])
        try:
            result = await AuditOrchestrator(
                ctx,
                request.target_path,
                recorder,
                dynamic_enabled=request.dynamic_enabled,
                prepared=prepared,
            ).run()
            return result
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            summary = str(exc).strip()[:1_000] or type(exc).__name__
            failure_code = self._failure_code(exc)
            terminal_changed = self.store.mark_scan_terminal(
                scan_id,
                "failed",
                failure_code=failure_code,
                failure_summary=summary,
            )
            if terminal_changed:
                for phase in self.store.list_phase_runs(scan_id):
                    if phase["status"] != "running":
                        continue
                    failed_phase = self.store.finish_phase_run(
                        phase["phase_run_id"],
                        "failed",
                        summary={"code": failure_code, "summary": summary},
                    )
                    self.store.append_scan_event(
                        scan_id,
                        "phase.failed",
                        "代码审计阶段执行失败",
                        {"phase": failed_phase["phase"], "code": failure_code},
                        level="error",
                        phase_run_id=failed_phase["phase_run_id"],
                    )
                event = self.store.append_scan_event(
                    scan_id,
                    "scan.failed",
                    "代码审计执行失败",
                    {"code": failure_code, "summary": summary},
                    level="error",
                )
                recorder._publish_change(event["seq"])
            raise
        finally:
            self._active.pop(scan_id, None)

    async def get_scan(self, scan_id: str, caller: AuditCaller) -> dict[str, Any]:
        return await asyncio.to_thread(self._build_scan_detail, scan_id, caller)

    def _build_scan_detail(self, scan_id: str, caller: AuditCaller) -> dict[str, Any]:
        scan = self._require_visible_scan(scan_id, caller)
        status = self.store.scan_status(scan_id)
        if scan["status"] == "completed" and status.get("integrity_status") == "invalid":
            repaired = self._reseal_legacy_bundle(scan_id)
            if repaired:
                status = self.store.scan_status(scan_id)
        snapshot = self.store.get_snapshot(scan["snapshot_id"])
        if snapshot is None:
            raise AuditServiceError("scan_invalid", "Scan snapshot is unavailable", status_code=500)
        report_data = self.store.report_data(scan_id)
        workers = self._public_workers(scan_id, report_data=report_data)
        phases = [self._public_phase_run(item) for item in self.store.list_phase_runs(scan_id)]
        if not phases and workers:
            phases = self._phase_runs_from_workers(scan_id, workers)
        events = self.store.list_scan_events(scan_id, after_seq=0, limit=1)
        finished_at = _effective_finished_at(scan)
        current_phase = _public_phase(scan.get("current_phase"))
        if current_phase is None and phases:
            running_phase = next(
                (item["phase"] for item in phases if item["status"] == "running"),
                None,
            )
            current_phase = running_phase or phases[-1]["phase"]
        integrity_artifacts = status.get("integrity_artifacts", {})
        knowledge_base = self.store.get_knowledge_base_metadata(scan_id)
        public_scan = {
            "scan_id": scan_id,
            "lifecycle_status": _public_lifecycle(str(scan["status"])),
            "current_phase": current_phase,
            "integrity_status": status.get("integrity_status", "pending"),
            "integrity_errors": status.get("integrity_errors", []),
            "coverage_status": self._coverage_status(
                scan_id,
                verified_artifacts=integrity_artifacts,
                report_data=report_data,
            ),
            "dynamic_enabled": bool(scan["dynamic_enabled"]),
            "coverage_policy": scan["coverage_policy"],
            "verification_votes": scan["verification_vote_count"],
            "created_at": scan["created_at"],
            "started_at": scan["created_at"],
            "finished_at": finished_at,
            "elapsed_ms": _elapsed_ms(scan["created_at"], finished_at),
            "latest_event_seq": events["latest_seq"],
            "can_cancel": scan["status"] == "running",
            "failure_code": scan.get("failure_code"),
            "failure_summary": scan.get("failure_summary"),
            "request_source": scan.get("request_source"),
            "workspace_ref": scan.get("workspace_ref"),
            "audit_mode": "knowledge_guided" if knowledge_base else "standard",
            "knowledge_base": knowledge_base,
        }
        return {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "scan": public_scan,
            "target": snapshot.public_dict(),
            "counts": status.get("counts", {}),
            "finding_summary": self._finding_summary(
                scan_id,
                verified_artifacts=integrity_artifacts,
            ),
            "coverage_summary": self._coverage_summary(
                scan_id,
                verified_artifacts=integrity_artifacts,
                report_data=report_data,
            ),
            "dynamic_validation": self._dynamic_summary(
                status,
                enabled=bool(scan["dynamic_enabled"]),
                report_data=report_data,
            ),
            "phase_runs": phases,
            "workers": workers,
            "artifacts": self._artifact_index(
                scan_id,
                scan,
                verified_artifacts=integrity_artifacts,
                report_data=report_data,
            ),
            "server_time": _utc_now().isoformat(),
            "workspace_url": (f"/contracts/webui/workspaces/code_security/code-security-workspace?scan_id={scan_id}"),
        }

    def _reseal_legacy_bundle(self, scan_id: str) -> bool:
        output = find_output_directory(scan_id)
        if output is None:
            return False
        try:
            return ReportWriter(self.store).reseal_legacy_bundle(scan_id, output)
        except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
            return False

    async def list_scans(
        self,
        caller: AuditCaller,
        *,
        statuses: set[str] | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self._list_scans,
            caller,
            statuses=statuses,
            cursor=cursor,
            limit=limit,
        )

    def _list_scans(
        self,
        caller: AuditCaller,
        *,
        statuses: set[str] | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if limit < 1 or limit > 100:
            raise AuditServiceError("invalid_parameter", "limit must be between 1 and 100")
        if cursor and len(cursor) > 4_096:
            raise AuditServiceError("invalid_parameter", "cursor is too long")
        invalid_statuses = set(statuses or ()) - PUBLIC_SCAN_STATUSES
        if invalid_statuses:
            raise AuditServiceError(
                "invalid_parameter",
                "Unsupported scan status: " + ", ".join(sorted(invalid_statuses)),
            )
        persisted_statuses = set(statuses or ())
        if "running" in persisted_statuses:
            persisted_statuses.update({"reducing", "cancelling"})
        try:
            page = self.store.list_scans(
                owner_subject=caller.subject,
                include_all=caller.is_admin,
                statuses=persisted_statuses,
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise AuditServiceError("invalid_parameter", str(exc)) from exc
        for item in page["items"]:
            item["finished_at"] = _effective_finished_at(item)
            item["lifecycle_status"] = _public_lifecycle(item.pop("status"))
            item["current_phase"] = _public_phase(item.get("current_phase"))
            metric = _final_finding_metric(
                lifecycle_status=item["lifecycle_status"],
                integrity_status="pending",
                dynamic_enabled=bool(item["dynamic_enabled"]),
                finding_summary={},
                dynamic_summary={},
            )
            if item["lifecycle_status"] == "completed":
                status = self.store.scan_status(item["scan_id"])
                integrity_status = str(status.get("integrity_status") or "pending")
                integrity_artifacts = status.get("integrity_artifacts", {})
                finding_summary: dict[str, Any] = {}
                dynamic_summary: dict[str, Any] = {}
                if integrity_status == "valid":
                    finding_summary = self._finding_summary(
                        item["scan_id"],
                        verified_artifacts=integrity_artifacts,
                    )
                    dynamic_summary = self._dynamic_summary(
                        status,
                        enabled=bool(item["dynamic_enabled"]),
                    )
                metric = _final_finding_metric(
                    lifecycle_status=item["lifecycle_status"],
                    integrity_status=integrity_status,
                    dynamic_enabled=bool(item["dynamic_enabled"]),
                    finding_summary=finding_summary,
                    dynamic_summary=dynamic_summary,
                )
            item.update(metric)
            item.pop("parent_session_id", None)
            item.pop("snapshot_id", None)
            item.pop("ruleset_digest", None)
            item.pop("request_digest", None)
            item.pop("owner_subject", None)
            item.pop("idempotency_key", None)
            item.pop("task_owner_pid", None)
            item.pop("task_owner_token", None)
            item.pop("task_owner_identity", None)
            item.pop("output_dir", None)
        return {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "items": page["items"],
            "next_cursor": page["next_cursor"],
        }

    async def wait_scan(
        self,
        scan_id: str,
        caller: AuditCaller,
        *,
        after_seq: int = 0,
        timeout_seconds: int = 30,
    ) -> dict[str, Any]:
        if timeout_seconds < 0 or timeout_seconds > 60:
            raise AuditServiceError("invalid_parameter", "timeout_seconds must be between 0 and 60")
        self._require_visible_scan(scan_id, caller)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        timed_out = False
        while True:
            events = self.store.list_scan_events(scan_id, after_seq=after_seq, limit=200)
            scan = self.store.get_scan(scan_id)
            if events["items"] or scan is None or scan["status"] in TERMINAL_SCAN_STATUSES:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                timed_out = True
                break
            await asyncio.sleep(min(0.2, remaining))
        detail = await self.get_scan(scan_id, caller)
        return {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "scan_id": scan_id,
            "timed_out": timed_out,
            "latest_event_seq": events["latest_seq"],
            "events": events["items"],
            "scan": detail["scan"],
        }

    async def cancel_scan(self, scan_id: str, caller: AuditCaller) -> dict[str, Any]:
        scan = self._require_visible_scan(scan_id, caller)
        if scan["status"] != "running":
            raise AuditServiceError("scan_not_running", "Scan is not running", status_code=409)
        active = self._active.get(scan_id)
        ctx = active.ctx if active else self._context_for_scan(scan)
        result = await audit_cancel(ctx, scan_id)
        if not result.success:
            raise AuditServiceError("cancel_failed", str(result.error or "Unable to cancel scan"))
        self.store.mark_scan_terminal(scan_id, "cancelled")
        for phase in self.store.list_phase_runs(scan_id):
            if phase["status"] != "running":
                continue
            cancelled_phase = self.store.finish_phase_run(
                phase["phase_run_id"],
                "cancelled",
                summary={"reason": "cancelled_by_user"},
            )
            self.store.append_scan_event(
                scan_id,
                "phase.cancelled",
                "代码审计阶段已取消",
                {"phase": cancelled_phase["phase"], "reason": "cancelled_by_user"},
                level="warning",
                phase_run_id=cancelled_phase["phase_run_id"],
            )
        event = self.store.append_scan_event(
            scan_id,
            "scan.cancelled",
            "代码审计已取消",
            {"cancelled_workers": (result.output or {}).get("cancelled_workers", 0)},
            level="warning",
        )
        _ProgressRecorder(scan_id, dynamic_enabled=bool(scan["dynamic_enabled"]))._publish_change(event["seq"])
        if active and not active.task.done():
            active.task.cancel()
        return await self.get_scan(scan_id, caller)

    async def delete_scan(self, scan_id: str, caller: AuditCaller) -> None:
        if not caller.is_admin:
            raise AuditServiceError(
                "scan_delete_forbidden",
                "Only administrators may delete code-security audits",
                status_code=403,
            )
        scan = self._require_visible_scan(scan_id, caller)
        if scan["status"] not in TERMINAL_SCAN_STATUSES:
            raise AuditServiceError(
                "scan_delete_conflict",
                "Please cancel the audit and wait for it to stop before deleting it",
                status_code=409,
            )

        output = find_output_directory(scan_id)
        try:
            deleted = await asyncio.to_thread(self.store.delete_terminal_scan, scan_id)
        except ValueError as exc:
            current = self.store.get_scan(scan_id)
            if current is None:
                raise AuditServiceError(
                    "scan_not_found",
                    "Scan was not found",
                    status_code=404,
                ) from exc
            raise AuditServiceError(
                "scan_delete_conflict",
                "Please cancel the audit and wait for it to stop before deleting it",
                status_code=409,
            ) from exc
        if output is None and deleted["output_dir"]:
            output = Path(str(deleted["output_dir"]))
        cleanup_targets = (
            (
                output,
                outputs_root(),
                scan_id,
            ),
            (
                Path(deleted["snapshot_root"]) if deleted["snapshot_root"] else None,
                self.runtime.snapshots.snapshots_root,
                str(deleted["snapshot_id"]),
            ),
            (
                runtime_dir() / "docker" / scan_id,
                runtime_dir() / "docker",
                scan_id,
            ),
        )
        for path, root, expected_name in cleanup_targets:
            try:
                await asyncio.to_thread(
                    _remove_owned_tree,
                    path,
                    root=root,
                    expected_name=expected_name,
                )
            except OSError:
                logger.warning(
                    "Failed to remove code-security storage after deleting scan %s: %s",
                    scan_id,
                    path,
                    exc_info=True,
                )

    async def get_result(self, scan_id: str, caller: AuditCaller) -> dict[str, Any]:
        detail = await self.get_scan(scan_id, caller)
        lifecycle = detail["scan"]["lifecycle_status"]
        integrity = detail["scan"]["integrity_status"]
        if lifecycle == "completed" and integrity == "valid":
            result_state = "sealed"
        elif lifecycle in {"completed", "failed", "interrupted"}:
            result_state = "invalid"
        elif detail["counts"].get("candidates"):
            result_state = "partial"
        else:
            result_state = "not_ready"
        finding_summary = detail["finding_summary"]
        coverage = detail["coverage_summary"]
        artifacts = []
        for item in detail["artifacts"]:
            if item["state"] != "sealed":
                continue
            artifact = dict(item)
            path = self._artifact_file(scan_id, item["kind"])
            if path and caller.source != "webui":
                artifact["path"] = str(path)
            artifacts.append(artifact)
        return {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "action": "result",
            "scan_id": scan_id,
            "result_state": result_state,
            "lifecycle_status": lifecycle,
            "integrity_status": integrity,
            "coverage": coverage,
            "finding_summary": finding_summary,
            "artifacts": artifacts,
        }

    async def list_events(
        self,
        scan_id: str,
        caller: AuditCaller,
        *,
        after_seq: int = 0,
        before_seq: int | None = None,
        limit: int = 200,
        recent: bool = False,
    ) -> dict[str, Any]:
        self._require_visible_scan(scan_id, caller)
        if before_seq is not None and (recent or after_seq):
            raise AuditServiceError(
                "invalid_parameter",
                "before_seq cannot be combined with recent or after_seq",
            )
        if recent and after_seq:
            raise AuditServiceError(
                "invalid_parameter",
                "recent and after_seq cannot be used together",
            )
        try:
            if before_seq is not None:
                page = self.store.list_scan_events_before(
                    scan_id,
                    before_seq=before_seq,
                    limit=limit,
                )
            elif recent:
                page = self.store.list_recent_scan_events(scan_id, limit=limit)
            else:
                page = self.store.list_scan_events(scan_id, after_seq=after_seq, limit=limit)
        except ValueError as exc:
            raise AuditServiceError("invalid_parameter", str(exc)) from exc
        return {
            "items": page["items"],
            "latestSeq": page["latest_seq"],
            "hasMore": page["has_more"],
        }

    async def get_artifact(
        self,
        scan_id: str,
        kind: str,
        caller: AuditCaller,
    ) -> dict[str, Any]:
        scan = self._require_visible_scan(scan_id, caller)
        if kind in PROJECTED_ARTIFACT_KINDS:
            file_path = self._artifact_file(scan_id, kind)
            if file_path is not None:
                status = await asyncio.to_thread(self.store.scan_status, scan_id)
                if status.get("integrity_status") == "valid" and file_path.name in status.get(
                    "integrity_artifacts", {}
                ):
                    contents = await asyncio.to_thread(
                        self._read_verified_artifact,
                        scan,
                        file_path,
                    )
                    return {"kind": kind, "state": "sealed", "content": json.loads(contents)}
            return self._projected_artifact(scan_id, kind)

        file_path = self._artifact_file(scan_id, kind)
        if file_path:
            contents = await asyncio.to_thread(
                self._read_verified_artifact,
                scan,
                file_path,
            )
            if kind == "report_markdown":
                return {"kind": kind, "state": "sealed", "content": contents.decode("utf-8")}
            return {"kind": kind, "state": "sealed", "content": json.loads(contents)}

        raise AuditServiceError("artifact_not_found", "Artifact is not available", status_code=404)

    def _projected_artifact(self, scan_id: str, kind: str) -> dict[str, Any]:
        """Read structured audit facts without weakening sealed-file validation."""
        data = self.store.report_data(scan_id)
        if kind == "snapshot_summary":
            snapshot = self.store.get_snapshot(data["scan"]["snapshot_id"])
            return {"kind": kind, "state": "available", "content": snapshot.public_dict() if snapshot else {}}
        if kind == "threat_model":
            return {"kind": kind, "state": "partial", "content": data["threat_model"] or {}}
        if kind == "candidate_index":
            candidates = []
            verifications = {item["candidate_id"]: item for item in data["verifications"]}
            evidence_by_candidate: dict[str, list[dict[str, Any]]] = {}
            for evidence in data["evidence"]:
                candidate_id = str(evidence["candidate_id"])
                evidence_by_candidate.setdefault(candidate_id, []).append(
                    {
                        "evidence_id": evidence["evidence_id"],
                        "relative_path": evidence["relative_path"],
                        "start_line": evidence["start_line"],
                        "end_line": evidence["end_line"],
                        "content_url": (f"/api/code-security/v1/scans/{scan_id}/evidence/{evidence['evidence_id']}"),
                    }
                )
            accepted = set()
            if data["adjudications"] and data["adjudications"][-1]["action"] == "finalize":
                accepted = set(data["adjudications"][-1]["accepted_candidate_ids"])
            for item in data["candidates"]:
                candidate_id = item["candidate_id"]
                verification = verifications.get(candidate_id)
                candidates.append(
                    {
                        **item,
                        "verification_status": verification.get("verdict") if verification else "pending",
                        "final_finding": candidate_id in accepted
                        and verification is not None
                        and verification.get("verdict") == "confirmed",
                        "evidence": evidence_by_candidate.get(candidate_id, []),
                    }
                )
            return {"kind": kind, "state": "partial", "content": candidates}
        if kind == "verification_index":
            return {
                "kind": kind,
                "state": "partial",
                "content": {
                    "verifications": data["verifications"],
                    "votes": data["verification_votes"],
                    "conflicts": data["verification_conflicts"],
                },
            }
        if kind == "dynamic_validation":
            return {"kind": kind, "state": "partial", "content": self._public_dynamic_runs(data["dynamic_runs"])}
        if kind == "adjudication":
            return {"kind": kind, "state": "partial", "content": data["adjudications"]}
        if kind == "coverage":
            return {"kind": kind, "state": "partial", "content": data["coverage"]}
        raise AuditServiceError("artifact_not_found", "Artifact is not available", status_code=404)

    async def get_evidence(
        self,
        scan_id: str,
        evidence_id: str,
        caller: AuditCaller,
    ) -> dict[str, Any]:
        scan = self._require_visible_scan(scan_id, caller)
        evidence = self.store.get_evidence_record(scan_id, evidence_id)
        if evidence is None:
            raise AuditServiceError("evidence_not_found", "Evidence is not available", status_code=404)
        snapshot = self.store.get_snapshot(scan["snapshot_id"])
        if snapshot is None:
            raise AuditServiceError("scan_invalid", "Scan snapshot is unavailable", status_code=500)
        try:
            context = await asyncio.to_thread(
                self.runtime.source.evidence_context,
                snapshot.snapshot_id,
                evidence,
                context_lines=8,
                max_bytes=64 * 1024,
            )
        except (OSError, TypeError, ValueError) as exc:
            raise AuditServiceError(
                "evidence_integrity_invalid",
                "Evidence no longer matches the immutable snapshot",
                status_code=409,
            ) from exc
        return {
            "evidence_id": evidence_id,
            "relative_path": context["relative_path"],
            "start_line": context["start_line"],
            "end_line": context["end_line"],
            "excerpt": context["text"],
            "truncated": context["text_truncated"],
        }

    async def download_artifact(
        self,
        scan_id: str,
        artifact_name: str,
        caller: AuditCaller,
    ) -> tuple[str, bytes]:
        scan = self._require_visible_scan(scan_id, caller)
        kind = next((key for key, filename in ARTIFACT_FILENAMES.items() if filename == artifact_name), None)
        if kind is None:
            raise AuditServiceError("artifact_not_allowed", "Artifact is not downloadable", status_code=404)
        path = self._artifact_file(scan_id, kind)
        if path is None:
            raise AuditServiceError("artifact_not_found", "Artifact is not available", status_code=404)
        contents = await asyncio.to_thread(self._read_verified_artifact, scan, path)
        return path.name, contents

    async def _create_execution_context(
        self,
        request: StartScanRequest,
        caller: AuditCaller,
    ) -> ToolContext:
        try:
            _require_enabled_audit_tools(
                dynamic_enabled=request.dynamic_enabled,
                knowledge_base_enabled=request.knowledge_base is not None,
            )
            await Storage.init()
            provider_id, model_id = await _resolve_model(request.model)
        except ValueError as exc:
            raise AuditServiceError("invalid_parameter", str(exc)) from exc
        except RuntimeError as exc:
            code = self._failure_code(exc)
            status_code = 503 if code in {"model_unavailable", "required_tool_disabled"} else 500
            raise AuditServiceError(code, str(exc), status_code=status_code) from exc
        project = (await Project.from_directory(str(runtime_dir())))["project"]
        parent = await Session.create(
            project_id=project.id,
            directory=str(runtime_dir()),
            title=f"Code security audit: {request.target_path.name}",
            agent="code-security",
            provider=provider_id,
            model=model_id,
            model_pinned=True,
        )
        return ToolContext(
            session_id=parent.id,
            message_id=f"audit-service-{uuid4().hex}",
            agent="code-security",
            extra={
                "agent_execution_session": True,
                "model": {"providerID": provider_id, "modelID": model_id},
                "suppress_parent_completion": True,
                "audit_owner_subject": caller.subject,
            },
        )

    @staticmethod
    def _context_for_scan(scan: dict[str, Any]) -> ToolContext:
        return ToolContext(
            session_id=str(scan["parent_session_id"]),
            message_id=f"audit-cancel-{uuid4().hex}",
            agent="code-security",
            extra={"agent_execution_session": True, "suppress_parent_completion": True},
        )

    def _require_visible_scan(self, scan_id: str, caller: AuditCaller) -> dict[str, Any]:
        scan = self.store.get_scan(scan_id)
        if scan is None or (not caller.is_admin and scan.get("owner_subject") != caller.subject):
            raise AuditServiceError("scan_not_found", "Scan was not found", status_code=404)
        return scan

    def _idempotent_scan(
        self,
        request: StartScanRequest,
        caller: AuditCaller,
        digest: str,
    ) -> dict[str, Any] | None:
        if not request.idempotency_key:
            return None
        existing = self.store.find_scan_by_idempotency(caller.subject, request.idempotency_key)
        if existing is None:
            return None
        if existing.get("request_digest") != digest:
            raise AuditServiceError(
                "idempotency_conflict",
                "The idempotency key was already used with different parameters",
                status_code=409,
            )
        return existing

    def _discard_prepared_scan(self, scan_id: str, prepared: dict[str, Any]) -> None:
        snapshot = prepared.get("snapshot") if isinstance(prepared.get("snapshot"), dict) else {}
        snapshot_id = snapshot.get("snapshot_id")
        self.store.delete_scan(scan_id)
        if isinstance(snapshot_id, str):
            self.runtime.snapshots.delete(snapshot_id)

    @staticmethod
    def _validate_target(target_path: Path) -> Path:
        target = target_path.expanduser().resolve()
        if not target.is_dir():
            raise AuditServiceError("target_not_directory", "Audit target is not a directory")
        broad_targets = {Path("/").resolve(), Path.home().resolve()}
        flocks_root = (Path.home() / ".flocks").resolve()
        workspace_root = WorkspaceManager.get_instance().get_workspace_dir().expanduser().resolve()
        protected_roots = {
            data_dir().resolve(),
            runtime_dir().resolve(),
            outputs_root().resolve(),
        }
        inside_flocks = target == flocks_root or target.is_relative_to(flocks_root)
        inside_source_workspace = target != workspace_root and target.is_relative_to(workspace_root)
        overlaps_runtime = any(
            target == root or target.is_relative_to(root) or root.is_relative_to(target) for root in protected_roots
        )
        if (
            target in broad_targets
            or target == workspace_root
            or (inside_flocks and not inside_source_workspace)
            or overlaps_runtime
        ):
            raise AuditServiceError(
                "unsafe_target_scope", "Audit target is too broad or belongs to Flocks runtime data"
            )
        return target

    @staticmethod
    def _require_authorized_target(target: Path, authorized_root: Path | None) -> None:
        if authorized_root is None:
            raise AuditServiceError(
                "target_authorization_required",
                "Audit target authorization is unavailable",
                status_code=403,
            )
        root = authorized_root.expanduser().resolve()
        if target != root and not target.is_relative_to(root):
            raise AuditServiceError(
                "target_not_authorized",
                "Audit target must stay inside the authorized workspace",
                status_code=403,
            )

    @staticmethod
    def _validate_relative_paths(paths: tuple[str, ...], field: str) -> tuple[str, ...]:
        if len(paths) > 1_000 or any(not isinstance(path, str) or len(path) > 4_096 for path in paths):
            raise AuditServiceError("invalid_parameter", f"{field} contains too many or oversized entries")
        normalized = tuple(path.strip().replace("\\", "/") for path in paths if path.strip())
        if not normalized:
            return (".",)
        for value in normalized:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise AuditServiceError("invalid_parameter", f"{field} must contain snapshot-relative paths")
        return normalized

    @staticmethod
    def _validate_exclude_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
        if len(patterns) > 1_000 or any(not isinstance(pattern, str) or len(pattern) > 4_096 for pattern in patterns):
            raise AuditServiceError(
                "invalid_parameter",
                "exclude_patterns contains too many or oversized entries",
            )
        normalized = tuple(pattern.strip().replace("\\", "/") for pattern in patterns if pattern.strip())
        for value in normalized:
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts:
                raise AuditServiceError(
                    "invalid_parameter",
                    "exclude_patterns must contain snapshot-relative glob patterns",
                )
        return normalized

    @staticmethod
    def _validate_knowledge_base(
        value: KnowledgeBaseInput | None,
    ) -> KnowledgeBaseInput | None:
        if value is None:
            return None
        if not isinstance(value, KnowledgeBaseInput):
            raise AuditServiceError("invalid_parameter", "knowledge_base is invalid")
        if not isinstance(value.display_name, str) or not isinstance(value.content, str):
            raise AuditServiceError("invalid_parameter", "knowledge_base must contain text")
        display_name = value.display_name.strip()
        if (
            not display_name
            or display_name in {".", ".."}
            or "/" in display_name
            or "\\" in display_name
            or len(display_name) > 255
            or any(ord(character) < 32 or ord(character) == 127 for character in display_name)
        ):
            raise AuditServiceError(
                "invalid_parameter",
                "knowledge_base display_name must be a plain file name",
            )
        content = value.content.removeprefix("\ufeff")
        if "\0" in content:
            raise AuditServiceError(
                "invalid_parameter",
                "knowledge_base must be UTF-8 text without NUL characters",
            )
        if not content.strip():
            raise AuditServiceError("invalid_parameter", "knowledge_base must not be empty")
        if len(content.encode("utf-8")) > MAX_KNOWLEDGE_BASE_BYTES:
            raise AuditServiceError(
                "invalid_parameter",
                "knowledge_base may contain at most 32 KiB",
            )
        return KnowledgeBaseInput(display_name=display_name, content=content)

    @staticmethod
    def _knowledge_base_record(
        value: KnowledgeBaseInput | None,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        contents = value.content.encode("utf-8")
        return {
            "display_name": value.display_name,
            "content": value.content,
            "sha256": hashlib.sha256(contents).hexdigest(),
            "byte_length": len(contents),
        }

    @staticmethod
    def _request_digest(request: StartScanRequest) -> str:
        knowledge_base = AuditService._knowledge_base_record(request.knowledge_base)
        payload = {
            "target_path": str(request.target_path),
            "model": request.model,
            "include_paths": list(request.include_paths),
            "exclude_patterns": list(request.exclude_patterns),
            "max_file_bytes": request.max_file_bytes,
            "dynamic_enabled": request.dynamic_enabled,
            "coverage_policy": request.coverage_policy,
            "verification_votes": request.verification_votes,
            "knowledge_base": (
                {
                    "display_name": knowledge_base["display_name"],
                    "sha256": knowledge_base["sha256"],
                }
                if knowledge_base is not None
                else None
            ),
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _coverage_status(
        self,
        scan_id: str,
        *,
        verified_artifacts: dict[str, str],
        report_data: dict[str, Any] | None = None,
    ) -> str:
        del verified_artifacts
        data = report_data if report_data is not None else self.store.report_data(scan_id)
        return self._coverage_attestation_summary(data)["completeness"]

    @staticmethod
    def _coverage_attestation_summary(data: dict[str, Any]) -> dict[str, Any]:
        attestations = data["coverage"]
        analysis_units = {
            item["work_unit_id"]
            for item in data["work_units"]
            if item.get("role") in {"baseline", "investigator"}
        }
        covered_units = {
            item["work_unit_id"]
            for item in data["coverage"]
            if item.get("work_unit_id") in analysis_units
        }
        completeness_values = {
            str(item.get("completeness")) for item in attestations
        }
        if not attestations:
            completeness = "pending"
        elif "blocked" in completeness_values:
            completeness = "blocked"
        elif "partial" in completeness_values or covered_units != analysis_units:
            completeness = "partial"
        else:
            completeness = "complete"
        totals = {
            "assigned_count": 0,
            "read_complete_count": 0,
            "failed_count": 0,
            "unexamined_count": 0,
        }
        count_fields = {
            "assigned_count": "assigned",
            "read_complete_count": "read_complete",
            "failed_count": "failed",
            "unexamined_count": "unexamined",
        }
        for attestation in attestations:
            counts = attestation.get("counts")
            if not isinstance(counts, dict):
                continue
            for output_key, source_key in count_fields.items():
                value = counts.get(source_key)
                if isinstance(value, int) and not isinstance(value, bool):
                    totals[output_key] += value
        return {
            "policy": data["scan"].get(
                "coverage_policy", "evidence_backed_partial"
            ),
            "completeness": completeness,
            **totals,
        }

    def _finding_summary(
        self,
        scan_id: str,
        *,
        verified_artifacts: dict[str, str] | None = None,
    ) -> dict[str, int]:
        summary = {
            "total": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "dynamic_reproduced": 0,
        }
        path = self._artifact_file(scan_id, "findings")
        if path is None or path.name not in (verified_artifacts or {}):
            return summary
        try:
            findings = json.loads(path.read_text(encoding="utf-8")).get("findings", [])
        except (OSError, json.JSONDecodeError, AttributeError):
            return summary
        if not isinstance(findings, list):
            return summary
        summary["total"] = len(findings)
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            severity = finding.get("severity")
            level = severity.get("level") if isinstance(severity, dict) else None
            if level in summary and level != "total":
                summary[level] += 1
            validation = finding.get("validation")
            if isinstance(validation, dict) and validation.get("dynamicConclusion") == "reproduced":
                summary["dynamic_reproduced"] += 1
        return summary

    def _coverage_summary(
        self,
        scan_id: str,
        *,
        verified_artifacts: dict[str, str] | None = None,
        report_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._artifact_file(scan_id, "coverage")
        data = report_data if report_data is not None else self.store.report_data(scan_id)
        attestation_summary = self._coverage_attestation_summary(data)
        if path is None or path.name not in (verified_artifacts or {}):
            return {
                **attestation_summary,
                "deferred_count": 0,
                "open_question_count": sum(
                    len(item.get("open_questions", [])) for item in data["coverage"]
                ),
            }
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {
                **attestation_summary,
                "deferred_count": 0,
                "open_question_count": 0,
            }
        return {
            **attestation_summary,
            "deferred_count": len(document.get("deferred", [])),
            "open_question_count": len(document.get("openQuestions", [])),
        }

    def _dynamic_summary(
        self,
        status: dict[str, Any],
        *,
        enabled: bool,
        report_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        counts = status.get("counts") if isinstance(status.get("counts"), dict) else {}
        if not enabled:
            return {
                "status": "skipped",
                "ready": 0,
                "completed": 0,
                "inconclusive": 0,
                "not_runnable": 0,
            }
        scan_id = str(status.get("scan_id") or "")
        if report_data is not None:
            data = report_data
        elif scan_id:
            data = self.store.report_data(scan_id)
        else:
            data = {"dynamic_runs": []}
        run_counts = {"ready": 0, "completed": 0, "inconclusive": 0, "not_runnable": 0}
        for run in data["dynamic_runs"]:
            run_status = run.get("status")
            if run_status in run_counts:
                run_counts[run_status] += 1
        if run_counts["ready"]:
            lifecycle = "running"
        elif run_counts["not_runnable"] and not run_counts["completed"] and not run_counts["inconclusive"]:
            lifecycle = "not_runnable"
        elif counts.get("confirmed_without_dynamic_record"):
            lifecycle = "waiting_for_probe_plan"
        elif status.get("status") == "completed":
            lifecycle = "completed"
        elif counts.get("unverified_candidates") or not data["dynamic_runs"]:
            lifecycle = "waiting_for_static_confirmation"
        else:
            lifecycle = "completed"
        return {"status": lifecycle, **run_counts}

    def _public_workers(
        self,
        scan_id: str,
        *,
        report_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return bounded work-unit metadata without session or task identifiers."""
        data = report_data if report_data is not None else self.store.report_data(scan_id)
        candidate_ids: dict[str, set[str]] = {}
        candidate_summaries: dict[str, dict[str, Any]] = {}
        record_counts: dict[str, dict[str, int]] = {}
        coverage_by_unit = {
            item["work_unit_id"]: {
                key: item.get(key)
                for key in (
                    "attestation_id",
                    "attempt_id",
                    "policy",
                    "completeness",
                    "counts",
                    "attestation_digest",
                )
            }
            for item in data["coverage"]
        }
        recent_rejection_by_unit = {
            item["work_unit_id"]: {
                "rejection_id": item.get("rejection_id"),
                "attempt_id": item.get("attempt_id"),
                "tool_name": item.get("tool_name"),
                "error_code": item.get("error_code"),
                "retryable": bool(item.get("retryable")),
                "violation_count": len(item.get("violations", [])),
                "created_at": item.get("created_at"),
            }
            for item in data.get("submission_rejections", [])
            if isinstance(item.get("work_unit_id"), str)
        }
        attempts_by_unit: dict[str, list[dict[str, Any]]] = {}
        for item in data.get("work_attempts", []):
            work_unit_id = item.get("work_unit_id")
            if not isinstance(work_unit_id, str) or not work_unit_id:
                continue
            attempts_by_unit.setdefault(work_unit_id, []).append(
                {
                    "ordinal": item.get("ordinal"),
                    "provider_id": item.get("provider_id"),
                    "model_id": item.get("model_id"),
                    "status": item.get("status"),
                    "resume_count": item.get("resume_count") or 0,
                }
            )

        def record(work_unit_id: Any, kind: str, candidate_id: Any = None) -> None:
            if not isinstance(work_unit_id, str) or not work_unit_id:
                return
            counts = record_counts.setdefault(work_unit_id, {})
            counts[kind] = counts.get(kind, 0) + 1
            if isinstance(candidate_id, str) and candidate_id:
                candidate_ids.setdefault(work_unit_id, set()).add(candidate_id)

        for item in data["candidates"]:
            record(item.get("work_unit_id"), "candidates", item.get("candidate_id"))
            candidate_id = item.get("candidate_id")
            payload = item.get("payload")
            if isinstance(candidate_id, str) and isinstance(payload, dict):
                candidate_summaries[candidate_id] = {
                    "candidate_id": candidate_id,
                    "title": payload.get("title") if isinstance(payload.get("title"), str) else None,
                    "severity": payload.get("severity") if isinstance(payload.get("severity"), str) else None,
                    "verdict": None,
                    "rationale": None,
                }
        for item in data["verifications"]:
            record(item.get("work_unit_id"), "verifications", item.get("candidate_id"))
            candidate_id = item.get("candidate_id")
            summary = candidate_summaries.get(candidate_id) if isinstance(candidate_id, str) else None
            if summary is not None:
                rationale = item.get("rationale")
                rationale = rationale if isinstance(rationale, str) else None
                summary.update(
                    {
                        "verdict": item.get("verdict") if isinstance(item.get("verdict"), str) else None,
                        "rationale": rationale,
                    }
                )
        vote_summary_by_unit: dict[str, dict[str, Any]] = {}
        for item in data.get("verification_votes", []):
            work_unit_id = item.get("work_unit_id")
            candidate_id = item.get("candidate_id")
            summary = candidate_summaries.get(candidate_id) if isinstance(candidate_id, str) else None
            if not isinstance(work_unit_id, str) or summary is None:
                continue
            rationale = item.get("rationale")
            rationale = rationale if isinstance(rationale, str) else None
            vote_summary_by_unit[work_unit_id] = {
                **summary,
                "verdict": item.get("verdict") if isinstance(item.get("verdict"), str) else None,
                "rationale": rationale,
            }
        for item in data["coverage"]:
            record(item.get("work_unit_id"), "coverage")
        dynamic_statuses: dict[str, set[str]] = {}
        for item in data["dynamic_runs"]:
            probe_work_unit_id = item.get("probe_work_unit_id")
            record(
                probe_work_unit_id,
                "dynamic_runs",
                item.get("candidate_id"),
            )
            run_status = item.get("status")
            if isinstance(probe_work_unit_id, str) and isinstance(run_status, str):
                dynamic_statuses.setdefault(probe_work_unit_id, set()).add(run_status)

        workers: list[dict[str, Any]] = []
        for batch in self.store.list_worker_batches(scan_id):
            for item in batch["units"]:
                work_unit_id = str(item["work_unit_id"])
                paths = [str(path) for path in item.get("paths", [])]
                subject_id = item.get("subject_id")
                related = set(candidate_ids.get(work_unit_id, set()))
                if isinstance(subject_id, str) and subject_id:
                    related.add(subject_id)
                worker_status = str(item.get("status") or "pending")
                if item.get("role") == "prober" and dynamic_statuses.get(work_unit_id) == {"not_runnable"}:
                    worker_status = "not_runnable"
                started_at = item.get("started_at")
                terminal_statuses = {"completed", "failed", "cancelled", "not_runnable"}
                if not started_at and worker_status in terminal_statuses:
                    started_at = item.get("created_at")
                finished_at = item.get("finished_at")
                if not finished_at and worker_status in terminal_statuses:
                    finished_at = item.get("updated_at")
                vote_summary = vote_summary_by_unit.get(work_unit_id)
                workers.append(
                    {
                        "work_unit_id": work_unit_id,
                        "phase": _public_phase(item.get("phase")),
                        "role": item.get("role"),
                        "status": worker_status,
                        "started_at": started_at,
                        "finished_at": finished_at,
                        "elapsed_ms": (_elapsed_ms(started_at, finished_at) if isinstance(started_at, str) else None),
                        "path_count": len(paths),
                        "paths": paths[:50],
                        "paths_truncated": len(paths) > 50,
                        "candidate_ids": sorted(related),
                        "candidate_summaries": [
                            (
                                vote_summary
                                if vote_summary is not None
                                and vote_summary["candidate_id"] == candidate_id
                                else candidate_summaries[candidate_id]
                            )
                            for candidate_id in sorted(related)
                            if candidate_id in candidate_summaries
                        ],
                        "activity_counts": data.get("source_access_counts", {}).get(work_unit_id, {}),
                        "record_counts": record_counts.get(work_unit_id, {}),
                        "attempts": attempts_by_unit.get(work_unit_id, []),
                        "coverage": coverage_by_unit.get(work_unit_id),
                        "recent_rejection": recent_rejection_by_unit.get(
                            work_unit_id
                        ),
                    }
                )
        return workers

    @staticmethod
    def _phase_runs_from_workers(
        scan_id: str,
        workers: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Project legacy worker history when durable phase rows do not exist."""
        groups: list[tuple[str, list[dict[str, Any]]]] = []
        for worker in workers:
            phase = str(worker.get("phase") or "unknown")
            if groups and groups[-1][0] == phase:
                groups[-1][1].append(worker)
            else:
                groups.append((phase, [worker]))

        phases: list[dict[str, Any]] = []
        phase_ordinals: dict[str, int] = {}
        terminal_worker_statuses = {"completed", "failed", "cancelled", "not_runnable"}
        for phase, phase_workers in groups:
            ordinal = phase_ordinals.get(phase, 0) + 1
            phase_ordinals[phase] = ordinal
            status_counts: dict[str, int] = {}
            for worker in phase_workers:
                worker_status = str(worker.get("status") or "pending")
                status_counts[worker_status] = status_counts.get(worker_status, 0) + 1

            if status_counts.get("running"):
                status = "running"
            elif status_counts.get("pending"):
                status = "pending" if status_counts["pending"] == len(phase_workers) else "partial"
            elif status_counts.get("completed") == len(phase_workers):
                status = "completed"
            elif status_counts.get("completed"):
                status = "partial"
            elif status_counts.get("not_runnable") == len(phase_workers):
                status = "not_runnable"
            elif status_counts.get("failed"):
                status = "failed"
            else:
                status = "cancelled"

            started_values = [
                str(worker["started_at"])
                for worker in phase_workers
                if isinstance(worker.get("started_at"), str) and worker["started_at"]
            ]
            finished_values = [
                str(worker["finished_at"])
                for worker in phase_workers
                if isinstance(worker.get("finished_at"), str) and worker["finished_at"]
            ]
            started_at = min(started_values) if started_values else None
            all_terminal = all(worker.get("status") in terminal_worker_statuses for worker in phase_workers)
            finished_at = max(finished_values) if all_terminal and len(finished_values) == len(phase_workers) else None
            duration_ms = (
                _elapsed_ms(started_at, finished_at) if started_at and (finished_at or status == "running") else None
            )
            phases.append(
                {
                    "phase_run_id": f"projected_{scan_id}_{phase}_{ordinal}",
                    "phase": phase,
                    "ordinal": ordinal,
                    "status": status,
                    "created_at": started_at,
                    "started_at": started_at,
                    "finished_at": finished_at,
                    "duration_ms": duration_ms,
                    "worker_count": len(phase_workers),
                    "worker_status_counts": status_counts,
                    "summary": {
                        "projection_source": "worker_history",
                        "launched_workers": len(phase_workers),
                        "status_counts": status_counts,
                    },
                }
            )
        return phases

    def _artifact_index(
        self,
        scan_id: str,
        scan: dict[str, Any],
        *,
        verified_artifacts: dict[str, str],
        report_data: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        data = report_data if report_data is not None else self.store.report_data(scan_id)
        available = {
            "snapshot_summary": True,
            "threat_model": data["threat_model"] is not None,
            "candidate_index": bool(data["candidates"]),
            "verification_index": bool(data["verifications"]),
            "dynamic_validation": bool(data["dynamic_runs"]) or bool(scan["dynamic_enabled"]),
            "adjudication": bool(data["adjudications"]),
            "coverage": bool(data["coverage"]),
        }
        items = {
            kind: {
                "kind": kind,
                "state": "partial" if value and kind != "snapshot_summary" else "available" if value else "pending",
                "content_url": f"/api/code-security/v1/scans/{scan_id}/artifacts/{kind}",
            }
            for kind, value in available.items()
        }
        for kind, filename in ARTIFACT_FILENAMES.items():
            path = self._artifact_file(scan_id, kind)
            if path is None:
                continue
            digest = verified_artifacts.get(filename)
            if digest is None:
                if kind in PROJECTED_ARTIFACT_KINDS:
                    continue
                items[kind] = {
                    "kind": kind,
                    "state": "invalid" if scan["status"] == "completed" else "pending",
                }
                continue
            items[kind] = {
                "kind": kind,
                "state": "sealed",
                "media_type": "text/markdown"
                if filename.endswith(".md")
                else "application/sarif+json"
                if filename.endswith(".sarif")
                else "application/json",
                "size_bytes": path.stat().st_size,
                "sha256": digest,
                "content_url": f"/api/code-security/v1/scans/{scan_id}/artifacts/{kind}",
                "download_url": f"/api/code-security/v1/scans/{scan_id}/downloads/{filename}",
            }
        return list(items.values())

    @staticmethod
    def _public_phase_run(item: dict[str, Any]) -> dict[str, Any]:
        public = dict(item)
        public["phase"] = _public_phase(public.get("phase"))
        if public.get("status") == "running" and public.get("started_at"):
            public["duration_ms"] = _elapsed_ms(str(public["started_at"]))
        summary = public.get("summary") if isinstance(public.get("summary"), dict) else {}
        public["worker_count"] = summary.get("launched_workers")
        public["worker_status_counts"] = summary.get("status_counts", {})
        return public

    @staticmethod
    def _public_dynamic_runs(runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output = []
        for item in runs:
            run = item.get("run") if isinstance(item.get("run"), dict) else {}
            output.append(
                {
                    "candidate_id": item.get("candidate_id"),
                    "status": item.get("status"),
                    "reason": (item.get("probe") or {}).get("reason") if isinstance(item.get("probe"), dict) else None,
                    "runner_status": run.get("runner_status"),
                    "build": run.get("build"),
                    "control": run.get("control"),
                    "attack": run.get("attack"),
                    "cleanup": run.get("cleanup"),
                    "output_truncated": run.get("output_truncated"),
                    "timed_out": run.get("timed_out"),
                }
            )
        return output

    def _artifact_file(self, scan_id: str, kind: str) -> Path | None:
        filename = ARTIFACT_FILENAMES.get(kind)
        if filename is None:
            return None
        scan = self.store.get_scan(scan_id)
        stored_output = scan.get("output_dir") if scan else None
        output = None
        if stored_output:
            candidate = Path(str(stored_output)).expanduser()
            if candidate.is_dir() and not candidate.is_symlink():
                output = candidate.resolve()
        if output is None:
            output = find_output_directory(scan_id)
        if output is None:
            return None
        candidate = output / filename
        if not candidate.is_file() or candidate.is_symlink():
            return None
        resolved = candidate.resolve()
        try:
            resolved.relative_to(output)
        except ValueError:
            return None
        return resolved

    def _read_verified_artifact(
        self,
        scan: dict[str, Any],
        path: Path,
    ) -> bytes:
        try:
            contents = path.read_bytes()
        except OSError as exc:
            raise AuditServiceError(
                "artifact_integrity_invalid",
                "Artifact bundle did not pass integrity validation",
                status_code=409,
            ) from exc
        status = self.store.scan_status(str(scan["scan_id"]))
        expected_digest = status.get("integrity_artifacts", {}).get(path.name)
        actual_digest = hashlib.sha256(contents).hexdigest()
        if not (
            scan["status"] == "completed"
            and status.get("integrity_status") == "valid"
            and isinstance(expected_digest, str)
            and hmac.compare_digest(actual_digest, expected_digest)
        ):
            raise AuditServiceError(
                "artifact_integrity_invalid",
                "Artifact bundle did not pass integrity validation",
                status_code=409,
            )
        return contents

    @staticmethod
    def _failure_code(exc: BaseException) -> str:
        text = str(exc).casefold()
        if ("model" in text or "llm" in text) and ("available" in text or "configured" in text):
            return "model_unavailable"
        if "docker" in text or "preflight" in text:
            return "dynamic_preflight_failed"
        if "enabled tools" in text:
            return "required_tool_disabled"
        return "audit_execution_failed"


_SERVICE: AuditService | None = None


def get_audit_service() -> AuditService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = AuditService()
    return _SERVICE
