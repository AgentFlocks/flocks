"""
Reports this node still owes the Console.

A report that has to reach the Console even if the code that wrote it is gone
by the time it can be sent (an extension that took itself out, say) waits here
as a file of its own.  The heartbeat sends what is waiting, in the order it was
written, and removes a file only once the Console has taken that report, so a
report written while another one is on its way is never removed in its place.

One delivery runs at a time on a node (:class:`_OutboxLock`), so two
heartbeats never send the same report twice or out of order; whoever takes
reports back, or reports something that supersedes them, holds the outbox
(:func:`exclusive`) so no delivery slips in between.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Mapping, Optional

import httpx
from filelock import FileLock

from flocks.utils.log import Log

log = Log.create(service="console.outbox")

_ACCEPTED = frozenset({200, 201, 202})

#: A delivery waits this long for another one (in this process or another on
#: the node) before leaving the reports to the next heartbeat.
_DELIVERY_WAIT_S = 5.0
#: How long :func:`exclusive` waits for the outbox by default.
_EXCLUSIVE_WAIT_S = 60.0


def _console_unavailable(status: int) -> bool:
    # The Console is down, overloaded or no longer takes this session: the
    # reports after this one wait too, so none overtakes an older one.
    return status >= 500 or status in {401, 403, 408, 429}


def outbox_dir() -> Path:
    """``<runtime root>/run/console-outbox``: one JSON file per report."""
    raw = os.getenv("FLOCKS_ROOT") or str(Path.home() / ".flocks")
    return Path(raw).expanduser() / "run" / "console-outbox"


def _is_console_path(path: Any) -> bool:
    # Joined to the Console's base URL and sent with the session token, so it
    # has to stay a path on that host.
    return (
        isinstance(path, str)
        and path.startswith("/")
        and not path.startswith("//")
        and "\\" not in path
        and not any(char.isspace() or ord(char) < 0x20 for char in path)
    )


def _document(kind: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("a console report needs a kind")
    if not _is_console_path(path):
        raise ValueError(f"a console report goes to a path on the Console, not {path!r}")
    return {
        "kind": kind,
        "path": path,
        "payload": dict(payload),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _entry_name() -> str:
    # Name order is creation order, which is the order reports are sent in.
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')}-{uuid.uuid4().hex}.json"


def _write_private(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.tmp")
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=True, sort_keys=True)
    os.replace(pending, path)


def _read(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) else {}


def enqueue_report(*, kind: str, path: str, payload: Mapping[str, Any]) -> Path:
    """Leave a report for the Console (``POST <console>/<path>``); returns its file."""
    entry = outbox_dir() / _entry_name()
    _write_private(entry, _document(kind, path, payload))
    return entry


def stage_report(work_dir: Path, *, kind: str, path: str, payload: Mapping[str, Any]) -> tuple[Path, Path]:
    """A report written into *work_dir* now, and the outbox file it is to become.

    For an update channel's ``InstallPlan.success_files``: the report joins the
    outbox only once the install it describes has succeeded.
    """
    name = _entry_name()
    staged = Path(work_dir) / f"console-report-{name}"
    _write_private(staged, _document(kind, path, payload))
    return staged, outbox_dir() / name


def pending_reports(kind: str | None = None) -> list[Path]:
    """The waiting report files, oldest first (only those of *kind* when given)."""
    try:
        entries = sorted(
            entry
            for entry in outbox_dir().iterdir()
            if entry.suffix == ".json" and not entry.name.startswith(".") and entry.is_file()
        )
    except FileNotFoundError:
        return []
    if kind is None:
        return entries
    return [entry for entry in entries if _read(entry).get("kind") == kind]


def _release_if_acquired(node_lock: FileLock):
    def _done(future: "asyncio.Future[Any]") -> None:
        if not future.cancelled() and future.exception() is None:
            node_lock.release()

    return _done


class _OutboxLock:
    """One delivery or withdrawal at a time, across the processes of a node.

    An asyncio lock orders this process's coroutines, a file lock next to the
    reports orders the processes.  The task that holds it may take it again,
    so a holder can deliver what is older before reporting what supersedes it.
    """

    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._owner: Optional[asyncio.Task] = None

    def _lock_for_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock = asyncio.Lock()
            self._loop = loop
            self._owner = None
        return self._lock

    @asynccontextmanager
    async def hold(self, timeout: float) -> AsyncIterator[None]:
        """Raises ``TimeoutError`` when the outbox stays busy for *timeout* seconds."""
        lock = self._lock_for_loop()
        task = asyncio.current_task()
        if task is not None and self._owner is task:
            yield
            return
        await asyncio.wait_for(lock.acquire(), timeout)
        try:
            directory = outbox_dir()
            directory.mkdir(parents=True, exist_ok=True)
            # Acquired and released on worker threads: not a per-thread lock.
            node_lock = FileLock(str(directory / ".lock"), timeout=timeout, mode=0o600, thread_local=False)
            acquiring = asyncio.ensure_future(asyncio.to_thread(node_lock.acquire))
            try:
                await asyncio.shield(acquiring)
            except asyncio.CancelledError:
                # The worker thread may still take the file lock after this
                # task gave up waiting: hand it back as soon as it does.
                acquiring.add_done_callback(_release_if_acquired(node_lock))
                raise
            self._owner = task
            try:
                yield
            finally:
                self._owner = None
                await asyncio.to_thread(node_lock.release)
        finally:
            lock.release()


_LOCK = _OutboxLock()


@asynccontextmanager
async def exclusive(timeout: float = _EXCLUSIVE_WAIT_S) -> AsyncIterator[None]:
    """Hold the outbox: no delivery runs meanwhile, in this process or another of the node.

    For taking reports back (:func:`withdraw_reports`) and then reporting what
    supersedes them with no delivery in between.  The holder's own
    :func:`deliver_reports` goes ahead.  Raises ``TimeoutError`` when the
    outbox stays busy for *timeout* seconds.
    """
    async with _LOCK.hold(timeout):
        yield


def _set_aside(entry: Path, folder: str, document: Mapping[str, Any] | None = None) -> None:
    target = entry.parent / folder / entry.name
    if document is None:
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(entry, target)
        return
    _write_private(target, document)
    entry.unlink(missing_ok=True)


def withdraw_reports(
    kind: str,
    *,
    reason: str,
    where: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> int:
    """Take back the reports of *kind* not sent yet (kept under ``withdrawn/``); how many.

    *where*, when given, picks among them by payload: the others stay in line.
    Call it inside :func:`exclusive` while deliveries may run, or a report
    already on its way still reaches the Console.
    """
    count = 0
    for entry in pending_reports(kind):
        document = _read(entry)
        payload = document.get("payload")
        if where is not None and not (isinstance(payload, dict) and where(payload)):
            continue
        document["withdrawn_at"] = datetime.now(UTC).isoformat()
        document["withdraw_reason"] = reason
        _set_aside(entry, "withdrawn", document)
        count += 1
    return count


async def deliver_reports(
    *,
    client: httpx.AsyncClient,
    session: Mapping[str, Any],
    console_base: str,
) -> int:
    """Send the waiting reports, oldest first; how many the Console took.

    The node's identity (``fingerprint``, ``install_id``) comes from the
    session, as in a heartbeat.  Once the Console cannot take reports at all
    (unreachable, a server error, the session refused) the rest wait for the
    next heartbeat, so a newer report is never taken before an older one.  A
    report the Console refuses on its own (another 4xx) is set aside under
    ``rejected/``: sending it again would not change the answer, and it must
    not hold back what comes after it.  While another delivery runs (or the
    outbox is held, see :func:`exclusive`) this one waits a few seconds, then
    leaves everything to the next heartbeat.
    """
    token = str(session.get("console_session_token") or "").strip()  # secret-guard: allow 误报：读取 session 里的 token，非硬编码
    base = str(console_base or "").strip().rstrip("/")
    if not base or not token:
        return 0
    try:
        async with _LOCK.hold(_DELIVERY_WAIT_S):
            return await _deliver(client=client, session=session, base=base, token=token)
    except TimeoutError:
        log.info("console.outbox.busy", {})
        return 0


async def _deliver(*, client: httpx.AsyncClient, session: Mapping[str, Any], base: str, token: str) -> int:
    try:
        entries = pending_reports()
    except OSError as exc:
        log.warning("console.outbox.unreadable_dir", {"error": str(exc)})
        return 0
    delivered = 0
    for entry in entries:
        document = _read(entry)
        path = document.get("path")
        payload = document.get("payload")
        if not _is_console_path(path) or not isinstance(payload, dict):
            # Never sendable: out of the way, so it is not retried on every heartbeat.
            log.warning("console.outbox.invalid_report", {"entry": entry.name})
            try:
                _set_aside(entry, "invalid")
            except OSError:
                pass
            continue
        body = {
            **payload,
            "fingerprint": session.get("fingerprint"),
            "install_id": session.get("install_id"),
        }
        try:
            resp = await client.post(
                f"{base}{path}",
                json=body,
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.TransportError as exc:
            log.warning("console.outbox.unreachable", {"entry": entry.name, "error": str(exc)})
            break
        except Exception as exc:  # noqa: BLE001 - a report never fails the heartbeat
            # Not known to be refused: it stays first in line, and nothing
            # after it goes out before it does.
            log.warning("console.outbox.send_failed", {"entry": entry.name, "error": str(exc)})
            break
        if resp.status_code in _ACCEPTED:
            delivered += 1
            try:
                entry.unlink(missing_ok=True)
            except OSError as exc:
                # Taken, but still here: it would go out again.  Stop, so a
                # directory that cannot be changed repeats at most this one.
                log.warning("console.outbox.taken_not_removed", {"entry": entry.name, "error": str(exc)})
                break
            continue
        log.warning("console.outbox.not_taken", {"entry": entry.name, "status": resp.status_code})
        if _console_unavailable(resp.status_code):
            break
        try:
            _set_aside(entry, "rejected")
        except OSError as exc:
            log.warning("console.outbox.rejected_not_moved", {"entry": entry.name, "error": str(exc)})
    return delivered


__all__ = [
    "deliver_reports",
    "enqueue_report",
    "exclusive",
    "outbox_dir",
    "pending_reports",
    "stage_report",
    "withdraw_reports",
]
