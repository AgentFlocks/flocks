"""Durable, target-bound workflow notifications independent of business logic.

Delivery is at least once: a timeout cannot prove that a recipient saw nothing.
Pending messages retain their original session/channel until explicitly rebound.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4


def notifications_enabled(inputs: Mapping[str, Any]) -> bool:
    return str(inputs.get("notify_channel_enabled", True)).strip().lower() not in {
        "false", "0", "no", "off", "none", "",
    }


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def deliver_workflow_notifications(tool: Any, inputs: Mapping[str, Any],
                                   messages: list[dict[str, Any]], *, outbox_dir: Path | None = None,
                                   max_attempts: int = 5, now: float | None = None) -> dict[str, Any]:
    """Durably queue first, then retry delivery without rerunning business work.

    ``inputs`` accepts notify_session_id (explicit bound target), optional
    notify_channel_type (default auto, requiring one unique binding),
    notify_channel_enabled, and _workflow_id (outbox isolation scope).
    Each message supplies message text and an optional stable dedup_key.
    With max_attempts=0 this only persists messages for a later drain call.

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
    enabled = notifications_enabled(inputs)
    session = str(inputs.get("notify_session_id") or "").strip()
    channel = str(inputs.get("notify_channel_type") or "auto").strip().lower()
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
            success = result.get("success") is True
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
