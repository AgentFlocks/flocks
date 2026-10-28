"""Opt-in, fingerprinted repairs for older user-owned GT workflows.

Preview never writes. Application preserves the original bytes in a separate
backup directory and checks the preview digest before replacing the definition.
Runtime helpers deliberately contain no customer addresses or channel settings.
"""
from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


_LEGACY_HASHES = {
    ("backlog_monitor_gt", "check_backlog"): "e16e004d9e174780d074d1511db8ee6ff3d11b699b0894c98e725b0906010b13",
    ("backlog_monitor_gt", "send_feishu_notify"): "7159c9181c2cceefa6fd75c84d7f0f05b4a7f37d84ac83cf06fb6693cbd7360b",
    ("stream_alert_dispose_gt", "join_evidence"): "c2cfd0b5128ee127f43f59d6b2d134e4398f666426ef1c2fa3c309dc7c08ab94",
    ("stream_alert_dispose_gt", "notify_dispose"): "38b37d73d028c751ab1ecab10f94a305ed6f310b5bf30a9aa529a511ac9af610",
}
_TRIAGE_HASHES = {
    "9c3134afac69cd4209e469db39962cc983e01e01c5414b807a74fec197fed099",
}
_TRIAGE_REPLAY_LOAD_HASHES = {
    "44ff3af1eff104920766d90dbab7b8663286bc2ff728710a56e650f5d2a266e3",
    "1906c6ef1ccc6a92749be2cc76fd8fcb4e15d9e8811082ab7f688e30f4ae25ab",
}
_TRIAGE_V1_ANALYSIS_HASHES = {
    "68d0321a34fc8533e52dccb8278448d35a633a778f9807cad3d003e830913dfc",
}
_LOCK_ACQUIRE_HASHES = {
    "2dbce359023b12b81d10e90e3787fc5a65915e49f847f9982c240335a2c7f1dd",
    "70c3d332d8ec8f7b3a8a30321ab997b7851ebf6218525fe075dca452f68bd40a",
}
_LOCK_RELEASE_HASH = "7170e85a5968d70aae76836765d625989c3d1b09111ed771f1484a4f5fec1e60"
_MARKER = "# flocks-gt-repair-v1"


@dataclass(frozen=True)
class RepairChange:
    node_id: str
    repair: str
    description: str


@dataclass
class RepairPlan:
    workflow: dict[str, Any]
    changes: list[RepairChange] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict[str, Any]:
        return {"changed": self.changed, "changes": [asdict(c) for c in self.changes],
                "warnings": list(self.warnings)}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _legacy(workflow: str, node: Mapping[str, Any]) -> bool:
    expected = _LEGACY_HASHES.get((workflow, str(node.get("id"))))
    return expected is not None and _sha(str(node.get("code", "")).encode()) == expected


def _repair_lock_functions(code: str) -> str:
    """Replace only the confirmed write-before-lock helpers, preserving callers."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code
    functions = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    acquire = functions.get("_acquire_lock")
    release = functions.get("_release_lock")
    if not acquire or not release:
        return code
    old = ast.get_source_segment(code, acquire) or ""
    # Fingerprint the complete helpers: superficially similar custom locking
    # may enforce additional application guarantees and must not be overwritten.
    old_release = ast.get_source_segment(code, release) or ""
    if _sha(old.encode()) not in _LOCK_ACQUIRE_HASHES or _sha(old_release.encode()) != _LOCK_RELEASE_HASH:
        return code
    lines = code.splitlines(keepends=True)
    replacements = [
        (acquire, "def _acquire_lock(lock_path):\n"
         "    from filelock import FileLock\n"
         "    lock = FileLock(lock_path, timeout=30)\n"
         "    lock.acquire()\n"
         "    return lock\n"),
        (release, "def _release_lock(lock):\n    lock.release()\n"),
    ]
    for node, replacement in sorted(replacements, key=lambda item: item[0].lineno, reverse=True):
        lines[node.lineno - 1:node.end_lineno] = [replacement]
    result = "".join(lines)
    ast.parse(result)
    return result


def preview_workflow_repairs(workflow: Mapping[str, Any]) -> RepairPlan:
    """Return a detached repaired definition; unknown custom code is left alone."""
    plan = RepairPlan(copy.deepcopy(dict(workflow)))
    name = str(workflow.get("name", ""))
    if name not in {"backlog_monitor_gt", "stream_alert_triage_gt", "stream_alert_dispose_gt"}:
        return plan
    for node in plan.workflow.get("nodes", []):
        node_id = str(node.get("id", ""))
        original = str(node.get("code", ""))
        code = original
        repairs: list[tuple[str, str]] = []
        if name == "stream_alert_triage_gt" and node_id == "load_dedup_incremental" and _sha(code.encode()) in _TRIAGE_REPLAY_LOAD_HASHES:
            code = (f"{_MARKER}\n# flocks-gt-repair-v2\n"
                    "from flocks.workflow.repairs import load_gt_dedup_incremental\n"
                    "outputs.update(load_gt_dedup_incremental(inputs))\n")
            repairs.append(("triage-backfill", "Read bounded uncommitted input across dates; retain checkpoints until analysis succeeds."))
        elif name == "backlog_monitor_gt" and _legacy(name, node):
            if node_id == "check_backlog":
                code = (f"{_MARKER}\nfrom flocks.workflow.repairs import check_backlog_progress\n"
                        "outputs.update(check_backlog_progress(inputs))\n")
                repairs.append(("bounded-backlog", "Bound history reads and report missing/unreadable data as unknown."))
            elif node_id == "send_feishu_notify":
                code = (f"{_MARKER}\nfrom flocks.workflow.repairs import notify_backlog_progress\n"
                        "outputs.update(notify_backlog_progress(tool, inputs))\n")
                repairs.append(("backlog-notify", "Notify on unknown monitor health and record actual delivery results."))
        elif name == "stream_alert_triage_gt" and node_id == "attack_analysis_single_structured":
            if _sha(code.encode()) in _TRIAGE_HASHES:
                bad = "        src_ip_val = parsed.get('src_ip', '')"
                fixed = "    src_ip_val = parsed.get('src_ip', '')"
                if code.count(bad) == 1:
                    code = code.replace(bad, fixed, 1)
                    ast.parse(code)
                    # Failed analysis or missing durable output must not reach the
                    # downstream byte-offset checkpoint, even when the old node
                    # caught the underlying exception for best-effort reporting.
                    code = "_gt_persistence_failed = False\n" + code
                    warning = "        print(f'[triage] WARNING: failed to persist triage_result jsonl:"
                    if code.count(warning) == 1:
                        code = code.replace(warning, "        _gt_persistence_failed = True\n" + warning, 1)
                    analysis_guard = ("\n" + _MARKER + "\n"
                                      "if stats.get('triage_failed', 0) or any(\n"
                                      "        item.get('triage_status') == 'failed' for item in enriched_with_triage):\n"
                                      "    raise RuntimeError('GT triage incomplete; input offsets were not committed')\n\n")
                    persistence_boundary = "if persist and enriched_with_triage:"
                    if code.count(persistence_boundary) != 1:
                        raise ValueError("Unsupported triage persistence layout")
                    # Reject known analysis failures before writing any JSONL.
                    # A mid-write storage error can still leave some records:
                    # retries are intentionally at least once, never data loss.
                    code = code.replace(persistence_boundary, analysis_guard + persistence_boundary, 1)
                    guard = ("\n" + _MARKER + "\n"
                             "if persist and enriched_with_triage and (_gt_persistence_failed or not output_paths):\n"
                             "    raise RuntimeError('GT triage output was not persisted; input offsets were not committed')\n\n")
                    target = "outputs['enriched_alerts_with_triage'] = enriched_with_triage"
                    if code.count(target) != 1:
                        raise ValueError("Unsupported triage checkpoint layout")
                    code = code.replace(target, guard + target, 1)
                    repairs.extend([
                        ("triage-indent", "Remove the confirmed unexpected indentation before src_ip_val."),
                        ("triage-checkpoint", "Stop before checkpointing failed analysis or failed result persistence."),
                    ])
        elif name == "stream_alert_dispose_gt" and _legacy(name, node):
            if node_id == "join_evidence":
                incoming = {e.get("from") for e in plan.workflow.get("edges", []) if e.get("to") == node_id}
                if incoming == {"query_threat_intel", "verify_deterministic"}:
                    node.update(join=True, join_mode="namespace", join_namespace_key="__gt_evidence_sources__")
                    code = (f"{_MARKER}\nfrom flocks.workflow.repairs import merge_dispose_evidence_inputs\n"
                            "inputs = merge_dispose_evidence_inputs(inputs)\n" + code)
                    repairs.append(("dispose-join", "Wait for both evidence branches and preserve their authoritative result fields."))
            elif node_id == "notify_dispose":
                boundary = "if not notify_channel_enabled or not notify_messages:"
                if code.count(boundary) == 1:
                    code = code.split(boundary)[0] + (
                        f"{_MARKER}\nfrom flocks.workflow.repairs import deliver_dispose_notifications\n"
                        "outputs.update(inputs)\n"
                        "outputs.update(deliver_dispose_notifications(tool, inputs, notify_messages))\n")
                    repairs.append(("dispose-notify", "Use the workflow tool facade and record delivery failures instead of claiming success."))
        if name in {"stream_alert_triage_gt", "stream_alert_dispose_gt"}:
            locked = _repair_lock_functions(code)
            if locked != code:
                code = locked
                repairs.append(("file-lock", "Use a bounded platform file lock without truncating/writing before acquisition."))
        if name == "stream_alert_triage_gt" and node_id == "attack_analysis_single_structured" and _sha(code.encode()) in _TRIAGE_V1_ANALYSIS_HASHES:
            old_status = "'ok' if source in ('triaged', 'no_dedup_key_triaged') else 'failed'"
            if code.count(old_status) != 1:
                raise ValueError("Unsupported triage outcome mapping")
            code = code.replace(old_status, "'ok' if source in ('triaged', 'no_dedup_key_triaged', 'whitelist') else 'failed'", 1)
            code += "\n# flocks-gt-repair-v2: whitelist is a successful skip, never an analysis failure.\n"
            repairs.append(("triage-whitelist-status", "Treat whitelist results as successful while retaining genuine failure guards."))
        if code != original:
            ast.parse(code)
            node["code"] = code
            plan.changes.extend(RepairChange(node_id, repair, description) for repair, description in repairs)
        elif node_id in {"load_dedup_incremental", "check_backlog", "send_feishu_notify", "attack_analysis_single_structured", "join_evidence", "notify_dispose"} and _MARKER not in code:
            plan.warnings.append(f"{node_id}: unrecognized custom code; left unchanged")
    return plan


def _atomic_bytes(path: Path, data: bytes, *, mode: int | None = None) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def repair_workflow_file(path: str | Path, *, apply: bool = False,
                         expected_sha256: str | None = None,
                         backup_dir: str | Path | None = None) -> dict[str, Any]:
    """Preview or back up and atomically apply repairs to one explicit file."""
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink workflow definition")
    raw = path.read_bytes()
    digest = _sha(raw)
    if expected_sha256 is not None and expected_sha256 != digest:
        raise ValueError("Workflow changed since preview; preview again before applying")
    plan = preview_workflow_repairs(json.loads(raw.decode("utf-8-sig")))
    report = {"path": str(path), "sha256": digest, "applied": False,
              "backup_path": None, **plan.to_dict()}
    if not apply or not plan.changed:
        return report
    # Serializes repair callers; the digest also catches edits between preview
    # and application. Workflow editors should be stopped during maintenance.
    from filelock import FileLock
    lock = FileLock(str(path) + ".repair.lock", timeout=10)
    with lock:
        if path.read_bytes() != raw:
            raise ValueError("Workflow changed while planning repairs; preview again")
        if backup_dir is None:
            from flocks.config import Config
            root = Config().get_global().data_dir.parent
            today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
            backup_dir = root / "workspace" / "outputs" / today / "workflow-repairs"
        destination = Path(backup_dir) / uuid4().hex
        destination.mkdir(parents=True, exist_ok=False)
        backup = destination / path.name
        # Exclusive creation and fsync complete before any workflow mutation.
        with backup.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        encoded = (json.dumps(plan.workflow, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        if path.read_bytes() != raw:
            raise ValueError("Workflow changed after backup; no repair was applied")
        _atomic_bytes(path, encoded, mode=path.stat().st_mode & 0o777)
        report.update(applied=True, backup_path=str(backup), sha256_after=_sha(encoded))
    return report


def repair_workflow_files(paths: list[str | Path], *, apply: bool = False,
                          backup_dir: str | Path | None = None) -> list[dict[str, Any]]:
    return [repair_workflow_file(path, apply=apply, backup_dir=backup_dir) for path in paths]


def merge_dispose_evidence_inputs(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """Keep branch-specific evidence from its producer despite pass-through keys."""
    result = dict(inputs)
    sources = result.pop("__gt_evidence_sources__", {})
    for source, keys in {
        "query_threat_intel": ("ti_results", "threat_intel_stats"),
        "verify_deterministic": ("evidence_results", "evidence_stats"),
    }.items():
        branch = sources.get(source)
        if not isinstance(branch, dict):
            raise ValueError(f"Missing evidence branch: {source}")
        for key in keys:
            if key in branch:
                result[key] = branch[key]
    return result


def _configured_session_bindings(session_id: str, *, deadline: float) -> list[Any]:
    from flocks.channel.inbound.session_binding import SessionBindingService
    from flocks.workflow._async_runtime import run_sync_cancellable

    try:
        return run_sync_cancellable(SessionBindingService().get_bindings_by_session(session_id),
                                    lambda: time.monotonic() >= deadline)
    except asyncio.CancelledError as exc:
        raise TimeoutError("Configured channel binding lookup timed out") from exc


class ConfiguredSessionTool:
    """Disable channel_message's fallback to a different user's latest session."""

    def __init__(self, tool: Any, session_id: str, channel_type: str, *, timeout_s: float = 10):
        self.tool, self.session_id, self.channel_type = tool, session_id, channel_type
        self.timeout_s = timeout_s

    def run_safe(self, name: str, **kwargs: Any) -> dict[str, Any]:
        try:
            if name != "channel_message" or kwargs.get("session_id") != self.session_id or not self.session_id:
                raise ValueError("Notification target does not match the explicitly configured session")
            deadline = time.monotonic() + self.timeout_s
            bindings = _configured_session_bindings(self.session_id, deadline=deadline)
            def value(binding: Any, key: str) -> Any:
                return binding.get(key) if isinstance(binding, dict) else getattr(binding, key, None)
            same_session = [b for b in bindings if value(b, "session_id") == self.session_id]
            matched = [b for b in same_session if self.channel_type in {"", "auto"}
                       or value(b, "channel_id") == self.channel_type]
            if len(matched) != 1:
                raise ValueError("Configured session has no unique binding for the requested channel; pending notification retained")
            binding = matched[0]
            account, chat = value(binding, "account_id"), value(binding, "chat_id")
            if not account or not chat:
                raise ValueError("Configured channel binding has no explicit account/chat target")
            if sum(value(b, "account_id") == account and value(b, "chat_id") == chat for b in same_session) != 1:
                raise ValueError("Configured session target is ambiguous across channels")
            # Without channel_type neither HTTP nor in-process delivery may use
            # latest_active_user_binding. Account/chat pin the validated binding.
            strict_kwargs = {k: v for k, v in kwargs.items() if k != "channel_type"}
            strict_kwargs.update(account_id=account, chat_id=chat)
            registry = getattr(self.tool, "registry", None)
            if registry is not None and callable(getattr(registry, "with_cancel_checker", None)):
                from flocks.workflow.tools import ToolFacade
                existing_cancel = getattr(registry, "cancel_checker", None)
                timed = registry.with_cancel_checker(
                    lambda: time.monotonic() >= deadline or bool(existing_cancel and existing_cancel()))
                return ToolFacade(timed).run_safe(name, **strict_kwargs)
            return self.tool.run_safe(name, **strict_kwargs)
        except Exception as exc:
            return {"success": False, "error": str(exc)[:500]}


def deliver_dispose_notifications(tool: Any, inputs: Mapping[str, Any],
                                  messages: list[dict[str, Any]], *, outbox_dir: Path | None = None,
                                  max_attempts: int = 5, now: float | None = None) -> dict[str, Any]:
    """Compatibility wrapper for repaired disposal nodes."""
    scoped = dict(inputs)
    scoped.setdefault("_workflow_id", "stream_alert_dispose_gt")
    return deliver_workflow_notifications(tool, scoped, messages, outbox_dir=outbox_dir,
                                          max_attempts=max_attempts, now=now)


def deliver_workflow_notifications(tool: Any, inputs: Mapping[str, Any],
                                   messages: list[dict[str, Any]], *, outbox_dir: Path | None = None,
                                   max_attempts: int = 5, now: float | None = None) -> dict[str, Any]:
    """Durably queue first, then retry only delivery independently of disposal.

    A pending item's original target never changes implicitly. Short leases
    prevent simultaneous notification rounds from sending the same item; an
    interrupted delivery remains retryable after the lease expires. Delivery
    is at least once (a transport timeout cannot prove the recipient saw none).
    """
    from filelock import FileLock

    now = time.time() if now is None else now
    if outbox_dir is None:
        from flocks.config import Config
        outbox_dir = Config().get_global().data_dir / "workflow-notifications"
    outbox_dir = Path(outbox_dir)
    outbox_dir.mkdir(parents=True, exist_ok=True)
    path = outbox_dir / "pending.json"
    lock = FileLock(outbox_dir / "pending.lock", timeout=10)
    enabled = str(inputs.get("notify_channel_enabled", True)).lower() not in {"false", "0", "no", "off"}
    session = str(inputs.get("notify_session_id") or "").strip()
    channel = str(inputs.get("notify_channel_type") or "feishu").strip().lower()
    workflow_scope = str(inputs.get("_workflow_id") or "")
    target = {"session_id": session, "channel_type": channel}
    strict_tool = ConfiguredSessionTool(tool, session, channel)
    sent, errors = 0, []
    skipped, reserved = 0, []
    lease_id = uuid4().hex
    with lock:
        state = _read_notification_outbox(path)
        pending, delivered = state["pending"], state["delivered"]
        if enabled:
            for message in messages:
                body = {"dedup_key": str(message.get("dedup_key") or ""),
                        "message": str(message["message"]),
                        "workflow_id": workflow_scope}
                content_id = _sha(json.dumps(body, sort_keys=True, ensure_ascii=False).encode())
                identity = _sha(json.dumps({"content_id": content_id, **target}, sort_keys=True).encode())
                if identity in delivered:
                    skipped += 1
                    continue
                # A changed configuration is not consent to redirect an older
                # failed message. Keep it held until an explicit rebind.
                if any(item.get("content_id") == content_id and item.get("target") != target
                       for item in pending.values()):
                    continue
                pending.setdefault(identity, {**body, "content_id": content_id, "target": target,
                                              "created_at": now, "attempts": 0, "next_attempt_at": now})
            for identity, item in sorted(pending.items(), key=lambda pair: pair[1]["created_at"]):
                if len(reserved) >= max(0, min(int(max_attempts), 20)):
                    break
                if item.get("workflow_id", "") != workflow_scope:
                    continue
                if (not session or item.get("target") != target or item.get("next_attempt_at", 0) > now
                        or item.get("lease_until", 0) > now):
                    continue
                item.update(lease_id=lease_id, lease_until=now + 180)
                reserved.append((identity, dict(item)))
        _atomic_bytes(path, (json.dumps(state, ensure_ascii=False) + "\n").encode(), mode=0o600)
    # Do not hold the outbox lock while a channel/network call is in flight.
    started = time.monotonic()
    for identity, item in reserved:
        if time.monotonic() - started > 20:
            break
        kwargs = {"message": item["message"], "session_id": session}
        if channel and channel != "auto":
            kwargs["channel_type"] = channel
        success, error = False, ""
        try:
            result = strict_tool.run_safe("channel_message", **kwargs)
            success = bool(result.get("success"))
            if not success:
                error = str(result.get("error") or "channel delivery failed")[:500]
        except Exception as exc:
            error = str(exc)[:500]
        with lock:
            state = _read_notification_outbox(path)
            current = state["pending"].get(identity)
            if current and current.get("lease_id") == lease_id:
                if success:
                    state["pending"].pop(identity)
                    state["delivered"][identity] = now
                    sent += 1
                else:
                    attempts = int(current.get("attempts", 0)) + 1
                    current.update(attempts=attempts, last_error=error,
                                   next_attempt_at=now + min(3600, 60 * 2 ** min(attempts - 1, 6)))
                    current.pop("lease_id", None)
                    current.pop("lease_until", None)
                    errors.append({"dedup_key": item["dedup_key"], "error": error})
                # Bound only successful deduplication history; never discard
                # undelivered notifications to satisfy a size limit.
                delivered = state["delivered"]
                for old in sorted(delivered, key=delivered.get)[:-4096]:
                    delivered.pop(old)
                _atomic_bytes(path, (json.dumps(state, ensure_ascii=False) + "\n").encode(), mode=0o600)
    with lock:
        state = _read_notification_outbox(path)
        # Release reservations that the per-round time budget left unattempted.
        for item in state["pending"].values():
            if item.get("lease_id") == lease_id:
                item.pop("lease_id", None)
                item.pop("lease_until", None)
        _atomic_bytes(path, (json.dumps(state, ensure_ascii=False) + "\n").encode(), mode=0o600)
        scoped_pending = [item for item in state["pending"].values() if item.get("workflow_id", "") == workflow_scope]
        queued = len(scoped_pending)
        withheld = sum(item.get("target") != target or not session for item in scoped_pending)
    return {"notify_messages": messages, "notify_sent": sent, "notify_failed": len(errors),
            "notify_queued": queued, "notify_withheld": withheld,
            "notify_skipped": len(messages) if not enabled else skipped, "notify_errors": errors}


def _read_notification_outbox(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "pending": {}, "delivered": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(state, dict) or state.get("version") != 1
            or not isinstance(state.get("pending"), dict) or not isinstance(state.get("delivered"), dict)):
        raise ValueError("Invalid notification outbox; preserve and repair it before retrying")
    return state


def rebind_pending_notifications(outbox_dir: Path, *, old_session_id: str,
                                 new_session_id: str, new_channel_type: str) -> int:
    """Explicit maintenance operation; never called by automatic delivery."""
    from filelock import FileLock

    if not new_session_id.strip() or not new_channel_type.strip():
        raise ValueError("An explicit non-empty replacement session and channel are required")
    path = Path(outbox_dir) / "pending.json"
    changed = 0
    with FileLock(Path(outbox_dir) / "pending.lock", timeout=10):
        state = _read_notification_outbox(path)
        for identity, item in list(state["pending"].items()):
            if item.get("target", {}).get("session_id") != old_session_id:
                continue
            if item.get("lease_until", 0) > time.time():
                raise ValueError("Notification delivery is in progress; retry rebind after its lease expires")
            target = {"session_id": new_session_id.strip(), "channel_type": new_channel_type.strip().lower()}
            new_identity = _sha(json.dumps({"content_id": item["content_id"], **target}, sort_keys=True).encode())
            item.update(target=target, next_attempt_at=0)
            item.pop("lease_id", None)
            item.pop("lease_until", None)
            state["pending"].pop(identity)
            if new_identity not in state["delivered"]:
                state["pending"].setdefault(new_identity, item)
            changed += 1
        _atomic_bytes(path, (json.dumps(state, ensure_ascii=False) + "\n").encode(), mode=0o600)
    return changed


def check_backlog_progress(inputs: Mapping[str, Any], *, root: Path | None = None,
                           today: str | None = None, max_bytes: int = 2 * 1024 * 1024,
                           max_files: int = 64, max_days: int = 7) -> dict[str, Any]:
    """Bounded tail inspection, independent of the total historical JSONL size.

    A tail is sufficient to establish current-day progress; incomplete/invalid
    evidence is reported as unknown rather than silently healthy. Byte and file
    budgets are shared by both workflows; no historical files are modified.
    """
    if root is None:
        from flocks.config import Config
        root = Config().get_global().data_dir.parent / "workspace" / "workflows"
    root = Path(root)
    today = today or datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    today_date = datetime.strptime(today, "%Y-%m-%d").date()
    remaining, files_read = max(0, max_bytes), 0
    result = dict(inputs)
    details, unknown = [], []
    for label, workflow in (("dedup", "stream_alert_dedup_gt"), ("triage", "stream_alert_triage_gt")):
        latest, latest_dir, issues, workflow_files = "", "", [], 0
        try:
            directories = sorted((p for p in (root / workflow).iterdir()
                                  if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)),
                                 key=lambda p: p.name, reverse=True)
        except OSError as exc:
            directories = []
            issues.append(type(exc).__name__)
        if directories:
            latest_dir = directories[0].name
        if len(directories) > max_days:
            issues.append("history_scan_limited")
        for directory in directories[:max_days]:
            patterns = ("dedup_result*.jsonl",) if label == "dedup" else ("triage_result_*.jsonl",)
            candidates = sorted({p for pattern in patterns for p in directory.glob(pattern)}, reverse=True)
            for path in candidates:
                if remaining <= 0 or files_read >= max_files:
                    issues.append("scan_budget_exhausted")
                    break
                try:
                    with path.open("rb") as stream:
                        size = os.fstat(stream.fileno()).st_size
                        amount = min(size, remaining, 256 * 1024)
                        start = size - amount
                        stream.seek(start)
                        data = stream.read(amount)
                    if start:
                        issues.append("tail_scan_only")
                    remaining -= len(data)
                    files_read += 1
                    workflow_files += 1
                    lines = data.splitlines()
                    if start and lines:
                        lines = lines[1:]
                    if data and not data.endswith(b"\n") and lines:
                        lines = lines[:-1]
                    valid = False
                    for line in lines:
                        try:
                            record = json.loads(line)
                            if not isinstance(record, dict) or record.get("_type") == "file_header":
                                continue
                            for key in ("time", "start_time", "_wrapper_start_time"):
                                value = str(record.get(key) or "")[:10]
                                try:
                                    date = datetime.strptime(value, "%Y-%m-%d").date()
                                except ValueError:
                                    continue
                                if date <= today_date:
                                    latest = max(latest, value)
                                    valid = True
                                break
                        except (ValueError, TypeError, UnicodeError):
                            continue
                    if not valid:
                        issues.append("no_valid_timestamp_in_tail")
                except OSError as exc:
                    issues.append(type(exc).__name__)
                if latest == today:
                    break
            if latest == today or remaining <= 0 or files_read >= max_files:
                break
        lag = (today_date - datetime.strptime(latest, "%Y-%m-%d").date()).days if latest else -1
        # Current progress is positive evidence even if an older file is broken.
        health = "healthy" if latest == today else ("unknown" if not latest or issues else "backlog")
        if health == "unknown":
            unknown.append(label)
        result.update({f"{label}_latest_date": latest_dir, f"{label}_latest_alert_date": latest,
                       f"{label}_lag_days": lag, f"{label}_health": health,
                       f"{label}_scan_errors": sorted(set(issues))})
        details.append(f"{workflow}: {health}; latest_alert={latest or 'unknown'}; inspected_files={workflow_files}")
    result.update(today=today, has_backlog=any(result[f"{k}_lag_days"] >= 1 for k in ("dedup", "triage")),
                  monitor_health="unknown" if unknown else "ok", monitor_unknown_workflows=unknown,
                  backlog_details="\n".join(details), workflow_root=str(root), dedup_summary={}, triage_summary={},
                  scan_bytes=max(0, max_bytes) - remaining, scan_files=files_read)
    return result


def notify_backlog_progress(tool: Any, inputs: Mapping[str, Any], *, outbox_dir: Path | None = None) -> dict[str, Any]:
    result = dict(inputs)
    needs_notice = bool(inputs.get("has_backlog")) or inputs.get("monitor_health") == "unknown"
    enabled = str(inputs.get("notify_channel_enabled", True)).lower() not in {"false", "0", "no", "off"}
    status, count, error = "no_backlog", 0, ""
    if needs_notice and not enabled:
        status = "disabled_by_config"
    elif needs_notice and not str(inputs.get("notify_session_id") or "").strip():
        status, error = "no_session_id", "notify_session_id is empty"
    elif enabled and str(inputs.get("notify_session_id") or "").strip():
        try:
            messages = ([{"dedup_key": "backlog:" + str(inputs.get("today", "")),
                          "message": "告警处理积压/巡检异常提醒\n" + str(inputs.get("backlog_details", ""))}]
                        if needs_notice else [])
            delivery = deliver_workflow_notifications(
                tool, {**inputs, "_workflow_id": "backlog_monitor_gt"}, messages, outbox_dir=outbox_dir)
            count = int(delivery.get("notify_sent", 0))
            queued = int(delivery.get("notify_queued", 0))
            status = "sent" if count else "failed" if delivery.get("notify_failed") else "queued" if queued else "no_backlog"
            error = "; ".join(item["error"] for item in delivery.get("notify_errors", []))[:500]
            result["feishu_notify_queued"] = queued
        except Exception as exc:
            status, error = "exception", str(exc)[:500]
    result.update(feishu_notify_status=status, feishu_notify_count=count, feishu_notify_error=error)
    return result


def load_gt_dedup_incremental(inputs: Mapping[str, Any], *, root: Path | None = None,
                              today: str | None = None) -> dict[str, Any]:
    """Select bounded complete records, oldest first, without committing offsets.

    Scheduled runs look back seven days by default. Explicit paths take
    precedence; a manual historical input_date retains single-day semantics.
    Longer recovery windows are opt-in via backfill_days (maximum 366).
    Default batches read at most ten records to leave room for downstream LLM
    work within the workflow's existing node deadlines.
    """
    from filelock import FileLock

    if root is None:
        from flocks.config import Config
        root = Config().get_global().data_dir.parent / "workspace" / "workflows"
    root = Path(root)
    today = today or datetime.now().strftime("%Y-%m-%d")
    day = str(inputs.get("input_date") or today).strip()
    anchor = datetime.strptime(day, "%Y-%m-%d").date()
    source = str(inputs.get("dedup_source_workflow_name") or "stream_alert_dedup_gt")
    state_name = str(inputs.get("triage_state_workflow_name") or "stream_alert_triage_gt")
    state_dir = root / state_name
    state_dir.mkdir(parents=True, exist_ok=True)
    offset_path, lock_path = state_dir / "processed_offset.json", state_dir / "processed_offset.lock"
    with FileLock(lock_path, timeout=30):
        offsets = json.loads(offset_path.read_text(encoding="utf-8-sig")).get("offsets", {}) if offset_path.exists() else {}
    known = {os.path.normcase(os.path.abspath(k)): int(v) for k, v in offsets.items()}
    default_days = 7 if inputs.get("_trigger") == "poller" or not inputs.get("input_date") else 1
    days = max(1, min(366, int(inputs.get("backfill_days") or default_days)))
    max_files = max(1, min(256, int(inputs.get("incremental_max_files") or 64)))
    max_bytes = max(1, min(64 * 1024 * 1024, int(inputs.get("incremental_max_bytes") or 4 * 1024 * 1024)))
    max_records = max(1, min(10000, int(inputs.get("incremental_max_records") or 10)))
    explicit = inputs.get("input_paths") or []
    paths = [explicit] if isinstance(explicit, str) else list(explicit)
    if inputs.get("input_path"):
        paths.append(inputs["input_path"])
    if paths:
        paths = [Path(os.path.expanduser(str(p))).resolve() for p in paths if p]
        for path in paths:
            if not path.is_file():
                raise FileNotFoundError(f"Configured dedup input not found: {path}")
    else:
        paths = []
        for age in range(days - 1, -1, -1):
            folder = root / source / (anchor - timedelta(days=age)).isoformat()
            paths.extend(sorted(folder.glob("dedup_result*.jsonl")))
    paths = list(dict.fromkeys(p.resolve() for p in paths))
    # Processed shards must not consume the file budget forever and starve
    # later unread shards. Stable sorting retains date order within each set.
    paths.sort(key=lambda p: known.get(os.path.normcase(str(p)), 0) >= p.stat().st_size)
    selected, skipped_alerts, file_stats, checkpoints = [], [], [], {}
    seen_ids: set[str] = set()
    used = read_records = skipped = duplicate_ids = 0
    limited = len(paths) > max_files

    def truth(value: Any) -> bool:
        return value is True or str(value).strip().lower() in {"1", "true", "yes", "y"}

    for path in paths[:max_files]:
        if used >= max_bytes or read_records >= max_records:
            limited = True
            break
        size = path.stat().st_size
        start = known.get(os.path.normcase(str(path)), 0)
        if start < 0 or start > size:
            raise ValueError(f"Dedup input changed size below saved offset; inspect before replay: {path}")
        end, records, picked = start, 0, 0
        with path.open("rb") as stream:
            stream.seek(start)
            while stream.tell() < size and used < max_bytes and read_records < max_records:
                position = stream.tell()
                raw = stream.readline(min(size - position, max_bytes - used))
                used += len(raw)
                if not raw.endswith(b"\n"):
                    # Neither partial writes nor byte-budget cuts are checkpoints.
                    if position == start and used >= max_bytes and read_records == 0:
                        raise ValueError("First dedup record exceeds incremental_max_bytes; increase the byte budget")
                    break
                try:
                    record = json.loads(raw.decode("utf-8")) if raw.strip() else None
                except (ValueError, UnicodeError) as exc:
                    raise ValueError(f"Invalid dedup JSONL at byte {position}: {path}") from exc
                end = stream.tell()
                if not raw.strip() or (isinstance(record, dict) and record.get("_type") == "file_header"):
                    continue
                read_records += 1
                records += 1
                if not isinstance(record, dict):
                    raise ValueError(f"Dedup record must be an object at byte {position}: {path}")
                if truth(record.get("is_duplicate")) and not truth(record.get("_triage_leader")):
                    skipped += 1
                    skipped_alerts.append({**record, "_triage_skip_reason": "duplicate_follower",
                                           "triage_result": "重复告警-无需处理",
                                           "triage_reason": "重复告警 follower，已由聚合 leader 代表研判",
                                           "triage_source": "triage_skipped", "triage_status": "skipped_duplicate_follower"})
                    continue
                raw_id = next((str(record.get(k)).strip() for k in
                               ("_wrapper_raw_log_id", "raw_log_id", "id", "log_id")
                               if record.get(k) is not None and str(record[k]).strip().lower() not in {"", "none", "null", "nan"}), "")
                if raw_id and raw_id in seen_ids:
                    duplicate_ids += 1
                    continue
                if raw_id:
                    seen_ids.add(raw_id)
                selected.append(record)
                picked += 1
        checkpoints[str(path)] = end
        file_stats.append({"path": str(path), "snapshot_size": size, "start_offset": start,
                           "end_offset": end, "records": records, "selected": picked})
        print(f"[load_slow] {path}: start_offset={start} end_offset={end} records={records} selected={picked}")
    limited = limited or used >= max_bytes or read_records >= max_records
    stats = {"source_workflow": source, "file_count": len(file_stats), "record_count": len(selected),
             "bad_lines": 0, "skipped_duplicate_raw_log_id": duplicate_ids, "skipped_policy": skipped,
             "files": file_stats, "backfill_days": days, "scan_bytes": used, "scan_limit_reached": limited,
             "source_health": "files_found" if paths else "no_files"}
    print(f"[load_slow] source={source} through={day} backfill_days={days} files={len(file_stats)} selected={len(selected)} limited={limited}")
    result = dict(inputs)
    result.update(enriched_alerts=selected, skipped_alerts_for_output=skipped_alerts, loaded_files=[s["path"] for s in file_stats],
                  load_stats=stats, input_date=day, file_offsets=checkpoints,
                  processed_offset_path=str(offset_path), processed_lock_path=str(lock_path),
                  dedup_source_workflow_name=source, triage_state_workflow_name=state_name,
                  concurrency=max(1, int(inputs.get("concurrency") or 1)),
                  max_triage_cache_size=int(inputs.get("max_triage_cache_size") or 100000),
                  persist_triage_output=str(inputs.get("persist_triage_output", True)).lower() not in {"false", "0", "no", "off"})
    return result
