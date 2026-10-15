"""Find and delete what the data retention policy says has expired.

``collect_targets`` only reads; it backs the preview in the settings page.
``apply_policy`` checks every item again right before deleting it, because a
session may have become active (or a log file may have been written) since
the plan was made. Sessions that are running are never touched.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from flocks.retention.policy import RetentionPolicy
from flocks.utils.log import Log, get_log_dir, list_expired_log_paths

log = Log.create(service="retention")

DAY_MS = 86_400_000
_EXPIRED = "expired"
_SESSION_DELETED = "session_deleted"


@dataclass
class SessionTarget:
    """A root session and the descendants Session.delete removes with it."""

    project_id: str
    root_id: str
    session_ids: List[str]


@dataclass
class RecordTarget:
    path: Path
    reason: str  # _EXPIRED or _SESSION_DELETED
    size: int


@dataclass
class RetentionTargets:
    log_paths: List[Path] = field(default_factory=list)
    log_bytes: int = 0
    records: List[RecordTarget] = field(default_factory=list)
    sessions: List[SessionTarget] = field(default_factory=list)
    messages: Dict[str, List[str]] = field(default_factory=dict)
    skipped_busy: int = 0

    @property
    def session_count(self) -> int:
        return sum(len(target.session_ids) for target in self.sessions)

    @property
    def message_count(self) -> int:
        return sum(len(ids) for ids in self.messages.values())

    def summary(self) -> Dict[str, int]:
        return {
            "sessions": self.session_count,
            "messages": self.message_count,
            "logPaths": len(self.log_paths),
            "logBytes": self.log_bytes,
            "recordFiles": len(self.records),
            "recordBytes": sum(record.size for record in self.records),
            "skippedBusy": self.skipped_busy,
        }


@dataclass
class RetentionReport:
    started_at: int
    finished_at: int = 0
    sessions: int = 0
    messages: int = 0
    log_paths: int = 0
    record_files: int = 0
    freed_bytes: int = 0
    skipped_busy: int = 0
    errors: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "startedAt": self.started_at,
            "finishedAt": self.finished_at,
            "sessions": self.sessions,
            "messages": self.messages,
            "logPaths": self.log_paths,
            "recordFiles": self.record_files,
            "freedBytes": self.freed_bytes,
            "skippedBusy": self.skipped_busy,
            "errors": self.errors,
        }


def _path_size(path: Path) -> int:
    try:
        if not path.is_dir():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                pass
    return total


def _remove_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)
        return True
    except OSError as exc:
        log.warning("retention.remove_failed", {"path": str(path), "error": str(exc)})
        return False


def _session_last_activity_ms(session: Any) -> int:
    return max(int(session.time.updated or 0), int(session.time.created or 0))


def _session_is_busy(session: Any) -> bool:
    from flocks.session.core.status import SessionStatus
    from flocks.session.session_loop import SessionLoop

    if SessionLoop.is_running(session.id):
        return True
    try:
        status = SessionStatus.get_for_session(
            session.id,
            preferred_instance_id=session.directory,
        )
    except Exception:
        return True  # unknown means "leave it alone"
    return getattr(status, "type", "idle") != "idle"


def session_trees(sessions: Sequence[Any]) -> List[List[Any]]:
    """Group sessions the way Session.delete removes them: a root plus same-project descendants."""
    by_id = {session.id: session for session in sessions}
    children: Dict[str, List[Any]] = {}
    roots: List[Any] = []
    for session in sessions:
        parent = by_id.get(session.parent_id) if session.parent_id else None
        if parent is not None and parent.project_id == session.project_id:
            children.setdefault(parent.id, []).append(session)
        else:
            roots.append(session)

    trees: List[List[Any]] = []
    for root in roots:
        tree: List[Any] = []
        seen: set[str] = set()
        stack = [root]
        while stack:
            current = stack.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            tree.append(current)
            stack.extend(children.get(current.id, []))
        trees.append(tree)
    return trees


def _message_time_ms(message: Dict[str, Any]) -> Optional[int]:
    times = message.get("time")
    if not isinstance(times, dict):
        return None
    values = [
        value
        for value in (times.get("created"), times.get("updated"), times.get("completed"))
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return int(max(values)) if values else None


def expired_turn_prefix(
    messages: Iterable[Any],
    cutoff_ms: int,
    *,
    stop_at_message_id: Optional[str] = None,
) -> List[str]:
    """IDs of the oldest whole turns in which every message is older than ``cutoff_ms``.

    A turn starts at a user message. Deleting whole turns from the start keeps
    the rest of the conversation well formed: no reply is left without the
    prompt it answers. Stops at the first turn that is not fully expired, at a
    message without a usable timestamp or id, and at the session's revert point.
    """
    turns: List[List[Dict[str, Any]]] = []
    for message in messages:
        if not isinstance(message, dict):
            break
        if message.get("role") == "user" or not turns:
            turns.append([message])
        else:
            turns[-1].append(message)

    expired: List[str] = []
    for turn in turns:
        ids = [message.get("id") for message in turn]
        if any(not isinstance(message_id, str) or not message_id for message_id in ids):
            break
        if stop_at_message_id and stop_at_message_id in ids:
            break
        times = [_message_time_ms(message) for message in turn]
        if any(value is None for value in times) or max(times) >= cutoff_ms:
            break
        expired.extend(ids)
    return expired


async def _stored_messages(session_id: str) -> List[Any]:
    from flocks.storage.storage import Storage

    raw = await Storage.get(f"message:{session_id}")
    return raw if isinstance(raw, list) else []


def _expired_record_targets(
    directories: Sequence[Path],
    session_dir: Path,
    cutoff_s: Optional[float],
    deleted_session_ids: set[str],
) -> List[RecordTarget]:
    targets: List[RecordTarget] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.jsonl")):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
            except OSError:
                continue
            if directory == session_dir and path.stem in deleted_session_ids:
                targets.append(RecordTarget(path, _SESSION_DELETED, stat.st_size))
            elif cutoff_s is not None and stat.st_mtime < cutoff_s:
                targets.append(RecordTarget(path, _EXPIRED, stat.st_size))
    return targets


async def collect_targets(
    policy: RetentionPolicy,
    *,
    now: Optional[float] = None,
) -> RetentionTargets:
    """Work out what ``policy`` would delete right now, without deleting anything."""
    from flocks.session.recorder import Recorder
    from flocks.session.session import Session

    now = time.time() if now is None else now
    now_ms = int(now * 1000)
    targets = RetentionTargets()

    if policy.log_days:
        targets.log_paths = await asyncio.to_thread(
            list_expired_log_paths,
            get_log_dir(),
            policy.log_days,
            now=datetime.fromtimestamp(now),
        )
        targets.log_bytes = sum(
            [await asyncio.to_thread(_path_size, path) for path in targets.log_paths]
        )

    sessions = await Session.list_all_unfiltered()
    deleted_ids: set[str] = set()

    if policy.session_days:
        cutoff_ms = now_ms - policy.session_days * DAY_MS
        for tree in session_trees(sessions):
            if max(_session_last_activity_ms(session) for session in tree) >= cutoff_ms:
                continue
            if any(_session_is_busy(session) for session in tree):
                targets.skipped_busy += 1
                continue
            root = tree[0]
            targets.sessions.append(
                SessionTarget(root.project_id, root.id, [session.id for session in tree])
            )
            deleted_ids.update(session.id for session in tree)

    if policy.message_days:
        cutoff_ms = now_ms - policy.message_days * DAY_MS
        for session in sessions:
            if session.id in deleted_ids or int(session.time.created or 0) >= cutoff_ms:
                continue  # nothing in a session younger than the cutoff can have expired
            stop_at = session.revert.message_id if session.revert else None
            ids = expired_turn_prefix(
                await _stored_messages(session.id),
                cutoff_ms,
                stop_at_message_id=stop_at,
            )
            if not ids:
                continue
            if _session_is_busy(session):
                targets.skipped_busy += 1
                continue
            targets.messages[session.id] = ids

    if policy.log_days or deleted_ids:
        paths = Recorder.paths()
        cutoff_s = now - policy.log_days * 86_400 if policy.log_days else None
        targets.records = await asyncio.to_thread(
            _expired_record_targets,
            [paths.session_dir, paths.workflow_dir],
            paths.session_dir,
            cutoff_s,
            deleted_ids,
        )
    return targets


async def apply_policy(
    policy: RetentionPolicy,
    *,
    now: Optional[float] = None,
    should_continue: Optional[Callable[[], bool]] = None,
) -> RetentionReport:
    """Delete everything ``policy`` says has expired and report what went.

    ``should_continue`` lets the scheduler stop an in-flight round once the
    policy changes (e.g. an admin switched a kind to "keep forever"); it is
    checked before each destructive delete so no further data goes under the
    old, now-superseded policy.
    """
    from flocks.session.message import Message
    from flocks.session.recorder import Recorder
    from flocks.session.session import Session

    def _superseded() -> bool:
        return should_continue is not None and not should_continue()

    now = time.time() if now is None else now
    now_ms = int(now * 1000)
    report = RetentionReport(started_at=int(time.time() * 1000))
    targets = await collect_targets(policy, now=now)
    report.skipped_busy = targets.skipped_busy

    for path in targets.log_paths:
        if _superseded():
            break
        size = await asyncio.to_thread(_path_size, path)
        if await asyncio.to_thread(_remove_path, path):
            report.log_paths += 1
            report.freed_bytes += size
        else:
            report.errors += 1

    deleted_ids: set[str] = set()
    if targets.sessions:
        cutoff_ms = now_ms - policy.session_days * DAY_MS

        def _tree_retainable_for_delete(tree: Sequence[Any]) -> bool:
            """Keep the tree (abort the delete) if it was used since the plan or is busy.

            Passed to Session.delete, which runs it under the tree's lifecycle
            locks before bumping the generation, so a session that became busy
            (running, or a non-idle status such as compacting/dreaming with no
            live loop) is never stopped, cleared or deleted.
            """
            for session in tree:
                if _session_last_activity_ms(session) >= cutoff_ms:
                    return False
                if _session_is_busy(session):
                    return False
            return True

        for target in targets.sessions:
            if _superseded():
                break
            tree = await Session.collect_tree(target.project_id, target.root_id)
            if not tree:
                continue
            if not _tree_retainable_for_delete(tree):
                report.skipped_busy += 1  # used again or busy since the plan was made
                continue
            try:
                deleted = await Session.delete(
                    target.project_id, target.root_id,
                    precheck=_tree_retainable_for_delete,
                )
            except Exception as exc:
                log.warning("retention.session_delete_failed", {
                    "session_id": target.root_id,
                    "error": str(exc),
                })
                report.errors += 1
                continue
            if deleted:
                report.sessions += len(tree)
                deleted_ids.update(session.id for session in tree)
            else:
                report.skipped_busy += 1

    if targets.messages:
        cutoff_ms = now_ms - policy.message_days * DAY_MS

        def _expired_selector(stored, session):
            # Runs under the session lock in delete_selected, on the freshly
            # read stored messages and the live session's revert point.
            stop_at = session.revert.message_id if session is not None and session.revert else None
            return expired_turn_prefix(stored, cutoff_ms, stop_at_message_id=stop_at)

        for session_id in targets.messages:
            if _superseded():
                break
            try:
                deleted = await Message.delete_selected(
                    session_id, _expired_selector, is_busy=_session_is_busy,
                )
            except Exception as exc:
                log.warning("retention.message_delete_failed", {
                    "session_id": session_id,
                    "error": str(exc),
                })
                report.errors += 1
                continue
            if deleted < 0:
                report.skipped_busy += 1  # session was running
            else:
                report.messages += deleted

    cutoff_s = now - policy.log_days * 86_400 if policy.log_days else None
    for record in targets.records:
        if _superseded():
            break
        if record.reason == _SESSION_DELETED:
            if record.path.stem not in deleted_ids:
                continue  # its session was not deleted after all
            outcome, freed = await Recorder.delete_file(record.path)
        else:
            if cutoff_s is None:
                continue
            # delete_file re-checks the mtime under the record's append lock, so a
            # file written to since planning is left alone.
            outcome, freed = await Recorder.delete_file(record.path, older_than=cutoff_s)
        if outcome == "deleted":
            report.record_files += 1
            report.freed_bytes += freed
        elif outcome == "failed":
            report.errors += 1

    report.finished_at = int(time.time() * 1000)
    log.info("retention.applied", report.to_dict())
    return report
