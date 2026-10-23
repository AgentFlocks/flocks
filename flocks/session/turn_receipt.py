"""Where a prompt or a command a client sent stands.

The service accepts a prompt or a slash command and runs it later -- maybe
behind others in the session's queue, maybe on another worker.  Nothing about
the session as a whole tells a client when *its* turn is over: a direct
command runs without marking the session busy, a queued prompt waits while the
session finishes the one before it, ``/clear`` leaves no message to look for.

A client that wants to follow a turn names it (``turnID``); the service then
keeps a receipt for it in its store -- written before the turn is
dispatched, or the request is refused -- and moves it on as the turn goes:
accepted or queued, then running, then how it ended.  A receipt that ended
never changes again.  The worker that has the turn keeps the receipt fresh:
while it runs it, and, for prompts waiting in its queue (the queue is that
worker's), a keeper that refreshes them whatever runs ahead of them.  A turn
handed to a run already going on (another worker holds the session) stays
running until that run answered its message.  A receipt nobody refreshed for
STALE_S (the worker that had the turn went away) reads as lost.  The receipt
names the turn's user message once it exists: a client finds this turn's reply
by it.  Requests without a turn id (the WebUI follows events) get no receipt.

Receipts are records of the key-value store, changed under one lock per
process.  A database backend whose workers share sessions replaces this
module (``flocks.db_backend``) with one that changes them in the database.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import re
import time
from typing import Any, AsyncIterator, Callable, Dict, Optional, Tuple

from flocks.storage.storage import Storage
from flocks.utils.log import Log

log = Log.create(service="session.turn_receipt")

ACCEPTED = "accepted"
QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
REMOVED = "removed"
LOST = "lost"
TERMINAL = frozenset({DONE, FAILED, REMOVED})

#: How often a running or queued turn's receipt is refreshed.
HEARTBEAT_S = 10.0
#: A receipt not refreshed for this long reads as lost.
STALE_S = 60.0
#: Receipts are kept this long after their last change.
KEEP_S = 24 * 3600.0
_PRUNE_EVERY_S = 3600.0
#: A turn handed to a run going on elsewhere: how often its answer is looked
#: for, and how long the session may sit with no run at all before the turn
#: is taken as dropped.
HANDOFF_POLL_S = 1.0
HANDOFF_IDLE_S = 5.0
#: How long a turn that ran here keeps trying to read which reply answered
#: it (the receipt stays fresh meanwhile) before it ends as failed to tell.
ANSWER_LOOKUP_S = 60.0

_TURN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_PREFIX = "session_turn:"
_current: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar(
    "flocks_turn_receipt", default=None,
)
_last_prune = 0.0
_keeper: Optional[asyncio.Task] = None
# Every read-modify-write of a receipt happens under it.
_changes: Optional[asyncio.Lock] = None
_changes_loop: Any = None


def _lock() -> asyncio.Lock:
    global _changes, _changes_loop
    loop = asyncio.get_running_loop()
    if _changes is None or _changes_loop is not loop:
        _changes, _changes_loop = asyncio.Lock(), loop
    return _changes


class TurnIdInUse(ValueError):
    """The session already has a turn with this id."""


def turn_id_from(requested: Optional[str]) -> Optional[str]:
    """The turn id the client asked for (None: it follows nothing); ValueError for one that is not usable."""
    if requested is None or requested == "":
        return None
    if not _TURN_ID.match(requested):
        raise ValueError("turnID must be 1-64 letters, digits, '-' or '_'")
    return requested


def _key(session_id: str, turn_id: str) -> str:
    return f"{_PREFIX}{session_id}:{turn_id}"


def _now_ms() -> int:
    return int(time.time() * 1000)


async def create(session_id: str, turn_id: str, state: str, **fields: Any) -> None:
    """Write a new turn's receipt before anything is dispatched.

    TurnIdInUse when the session already has that turn; a storage failure
    raises as it is, and the caller then refuses the request: a turn its
    client cannot follow is not started.
    """
    receipt = {"state": state, "updatedAt": _now_ms(), **{k: v for k, v in fields.items() if v is not None}}
    key = _key(session_id, turn_id)
    async with _lock():
        if await Storage.get(key) is not None:
            raise TurnIdInUse(f"turn {turn_id} is already in use in this session")
        await Storage.set(key, receipt)
    await _prune()


async def _update(
    session_id: str, turn_id: str, applies: Callable[[Dict[str, Any]], bool], fields: Dict[str, Any],
) -> bool:
    patch = {"updatedAt": _now_ms(), **{k: v for k, v in fields.items() if v is not None}}
    key = _key(session_id, turn_id)
    async with _lock():
        receipt = await Storage.get(key)
        if not isinstance(receipt, dict) or not applies(receipt):
            return False
        await Storage.set(key, {**receipt, **patch})
    return True


async def advance(session_id: str, turn_id: str, state: str, **fields: Any) -> bool:
    """Move a turn on; a turn that already ended stays as it ended. Whether it moved."""
    return await _update(
        session_id, turn_id, lambda receipt: receipt.get("state") not in TERMINAL, {"state": state, **fields},
    )


async def advance_quietly(session_id: str, turn_id: str, state: str, **fields: Any) -> None:
    try:
        await advance(session_id, turn_id, state, **fields)
    except Exception as exc:
        # The turn goes on either way; a receipt nobody refreshes reads as lost.
        log.warning("session.turn_receipt.write_failed", {
            "sessionID": session_id, "state": state, "error_type": type(exc).__name__,
        })


async def name_queue_item(session_id: str, turn_id: str, queue_id: str) -> None:
    """Name the queue item of a queued turn, unless it already left the queue."""
    await _update(session_id, turn_id, lambda receipt: receipt.get("state") == QUEUED, {"queueID": queue_id})


async def refresh_queued(session_id: str, turn_id: str, queue_id: str) -> bool:
    """Refresh a turn still waiting as this queue item -- nothing that moved on or ended.

    One whose queue item was never named (that write failed) gets it here.
    """
    return await _update(
        session_id, turn_id,
        lambda receipt: receipt.get("state") == QUEUED and receipt.get("queueID") in (None, queue_id),
        {"queueID": queue_id},
    )


async def mark_removed(session_id: str, items: Any) -> None:
    """Queue items dropped without running: their turns are over."""
    for item in items:
        turn_id = (item.execution_context or {}).get("_turnID") if isinstance(item.execution_context, dict) else None
        if turn_id:
            await advance_quietly(session_id, turn_id, REMOVED, queueID=item.id)


async def get(session_id: str, turn_id: str) -> Optional[Dict[str, Any]]:
    value = await Storage.get(_key(session_id, turn_id))
    return dict(value) if isinstance(value, dict) else None


def is_stale(receipt: Dict[str, Any]) -> bool:
    """Not over, and not refreshed for STALE_S."""
    if receipt.get("state") in TERMINAL:
        return False
    updated = receipt.get("updatedAt")
    if not isinstance(updated, (int, float)):
        return True
    return time.time() * 1000 - updated > STALE_S * 1000


def note_user_message(message_id: str) -> None:
    """The turn running in this context created its user message (see ``running``)."""
    current = _current.get()
    if current is not None and not current.get("messageID"):
        current["messageID"] = message_id


def note_handed_off() -> None:
    """The turn running in this context left its message to a run going on already."""
    current = _current.get()
    if current is not None:
        current["handedOff"] = True


async def _answer_of(session_id: str, message_id: str) -> Tuple[bool, Optional[str]]:
    """Whether the session's run answered *message_id*, and with which reply.

    A run takes every message waiting when it gets to them and answers the
    latest (the session loop's own rule for a finished turn: ``finish`` other
    than tool-calls / unknown / summary, or an error), so a message handed to
    it together with a later one is answered by the reply to that one -- the
    earliest final reply to it or to a user message after it.  A direct
    command (``/help``) is no part of that: its user message is ignored and
    its reply comes from the ``builtin`` provider, so neither counts.  A
    message that is gone (history cleared) has nothing left to answer.
    """
    from flocks.session.message import Message

    messages = await Message.list(session_id, include_archived=True)

    def order(message: Any) -> Tuple[int, str]:
        created = (getattr(message, "time", None) or {}).get("created") or 0
        return int(created), str(message.id)

    def role(message: Any) -> str:
        value = getattr(message, "role", None)
        return str(getattr(value, "value", value) or "")

    asked = next((message for message in messages if message.id == message_id), None)
    if asked is None:
        return True, None
    later_users = {
        message.id
        for message in messages
        if role(message) == "user"
        and not getattr(message, "ignored", False)
        and order(message) >= order(asked)
    }
    replies = [
        message
        for message in messages
        if role(message) == "assistant"
        and (getattr(message, "providerID", None) or "") != "builtin"
        and (
            getattr(message, "error", None) is not None
            or (getattr(message, "finish", None) or "") not in ("", "tool-calls", "unknown", "summary")
        )
        and getattr(message, "parentID", None) in later_users
    ]
    if not replies:
        return False, None
    return True, min(replies, key=order).id


async def _wait_for_answer(session_id: str, message_id: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """A turn handed to a run going on elsewhere: over once that run answered its message.

    Returns (state, error, the reply that answered it).
    """
    if not message_id:
        return DONE, None, None
    from flocks.session.ownership import is_owned
    from flocks.session.session_loop import SessionLoop

    idle_since: Optional[float] = None
    while True:
        try:
            answered, reply_id = await _answer_of(session_id, message_id)
            if answered:
                return DONE, None, reply_id
            running_somewhere = SessionLoop.is_running(session_id) or await is_owned(session_id)
        except Exception as exc:
            # A read that failed says nothing about the run: keep waiting (the
            # heartbeat keeps the receipt fresh, or it goes stale if this
            # worker cannot reach the store at all).
            log.warning("session.turn_receipt.watch_failed", {
                "sessionID": session_id, "error_type": type(exc).__name__,
            })
            idle_since = None
            await asyncio.sleep(HANDOFF_POLL_S)
            continue
        if running_somewhere:
            idle_since = None
        else:
            now = time.monotonic()
            idle_since = now if idle_since is None else idle_since
            if now - idle_since >= HANDOFF_IDLE_S:
                return FAILED, "the run that took this turn's message ended without answering it", None
        await asyncio.sleep(HANDOFF_POLL_S)


async def _reply_that_answered(session_id: str, message_id: str) -> Tuple[str, Optional[str], Optional[str]]:
    """A turn that ran here: which reply answered it, read until the read works.

    The receipt is not written as over before that is known: a client tells a
    stopped turn from a finished one by that reply (with messages taken
    together, the stop lands on the reply to the latest of them).  A read
    that keeps failing for ANSWER_LOOKUP_S ends the turn as failed to tell.
    """
    deadline = time.monotonic() + ANSWER_LOOKUP_S
    while True:
        try:
            _answered_now, reply_id = await _answer_of(session_id, message_id)
            return DONE, None, reply_id
        except Exception as exc:
            log.warning("session.turn_receipt.answer_lookup_failed", {
                "sessionID": session_id, "error_type": type(exc).__name__,
            })
            if time.monotonic() >= deadline:
                return FAILED, "the turn ran, but which reply answered it could not be read", None
        await asyncio.sleep(HANDOFF_POLL_S)


@contextlib.asynccontextmanager
async def running(session_id: str, turn_id: Optional[str]) -> AsyncIterator[None]:
    """Run a turn under its receipt: running and kept fresh, then how it ended.

    No turn id (nobody follows the turn): nothing is written.
    """
    if not turn_id:
        yield
        return
    noted: Dict[str, Any] = {}
    previous = _current.set(noted)

    async def keep_fresh() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            await advance_quietly(session_id, turn_id, RUNNING, messageID=noted.get("messageID"))

    await advance_quietly(session_id, turn_id, RUNNING)
    heartbeat = asyncio.ensure_future(keep_fresh())
    outcome: str = DONE
    error: Optional[str] = None
    reply_id: Optional[str] = None
    try:
        try:
            yield
        finally:
            _current.reset(previous)
        if noted.get("handedOff"):
            outcome, error, reply_id = await _wait_for_answer(session_id, noted.get("messageID"))
        elif noted.get("messageID"):
            # The reply that answered it -- this run's, or, when messages
            # were taken together, the one to the latest of them.
            outcome, error, reply_id = await _reply_that_answered(session_id, noted["messageID"])
    except asyncio.CancelledError:
        outcome, error = FAILED, "cancelled"
        raise
    except Exception as exc:
        outcome, error = FAILED, f"{type(exc).__name__}: {exc}"[:500]
        raise
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await heartbeat
        await asyncio.shield(advance_quietly(
            session_id, turn_id, outcome, error=error, messageID=noted.get("messageID"), replyID=reply_id,
        ))


def ensure_queue_keeper() -> None:
    """Keep the receipts of prompts queued in this process fresh, whatever runs ahead of them."""
    global _keeper
    if _keeper is None or _keeper.done():
        _keeper = asyncio.ensure_future(_keep_queued_fresh())


async def _keep_queued_fresh() -> None:
    from flocks.session.interaction_queue import InteractionQueue

    while True:
        await asyncio.sleep(HEARTBEAT_S)
        waiting = False
        for session_id, item in InteractionQueue.held_items():
            turn_id = (item.execution_context or {}).get("_turnID") if isinstance(item.execution_context, dict) else None
            if not turn_id:
                continue
            waiting = True
            try:
                await refresh_queued(session_id, turn_id, item.id)
            except Exception as exc:
                log.warning("session.turn_receipt.queued_refresh_failed", {
                    "sessionID": session_id, "error_type": type(exc).__name__,
                })
        if not waiting:
            return


async def _prune() -> None:
    """Drop receipts older than KEEP_S, at most once per _PRUNE_EVERY_S in a process."""
    global _last_prune
    now = time.monotonic()
    if _last_prune and now - _last_prune < _PRUNE_EVERY_S:
        return
    _last_prune = now
    cutoff = _now_ms() - int(KEEP_S * 1000)
    try:
        async with _lock():
            for key, receipt in await Storage.list_entries(_PREFIX):
                updated = receipt.get("updatedAt") if isinstance(receipt, dict) else None
                if isinstance(updated, (int, float)) and updated < cutoff:
                    await Storage.delete(key)
    except Exception as exc:
        log.warning("session.turn_receipt.prune_failed", {"error_type": type(exc).__name__})
