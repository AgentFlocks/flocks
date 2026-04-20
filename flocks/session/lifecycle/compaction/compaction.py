"""SessionCompaction — context overflow orchestrator.

Coordinates overflow detection, pruning, summarization, and memory flush
to manage context window limits.  Delegates heavy lifting to sibling modules.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import List, Optional, Dict, Any, Literal

from flocks.utils.log import Log
from flocks.session.prompt import SessionPrompt
from .policy import CompactionPolicy
from .models import (
    CompactionResult,
    DEFAULT_COMPACTION_PROMPT,
    PRESERVE_LAST_STEPS,
)
from . import pruning, summary

log = Log.create(service="session.compaction")


# ---------------------------------------------------------------------------
# Background memory-flush task tracking
# ---------------------------------------------------------------------------
#
# Memory flush (``_flush_memory_to_daily`` → ``extract_and_save``) issues a
# second LLM call after the summary has already been generated.  It writes to
# a daily memory file but is NOT required for the session to continue, so we
# run it in the background to avoid blocking the post-compaction continuation.
#
# A module-level set keeps a strong reference to in-flight tasks so they are
# not garbage-collected mid-flight (Python's ``asyncio.create_task`` only
# holds a weak reference).  The done-callback removes finished tasks from the
# set and logs any exception.
#
# Concurrency safety:
#   * ``daily.write_daily(append=True)`` is *not* atomic — it does a
#     read-modify-write on the daily memory file.  When the synchronous flush
#     was inlined into ``process()`` two flushes for the same session could
#     never overlap.  Now that we fire-and-forget, two consecutive
#     compactions on the same session would race and silently drop the older
#     memory.  We restore strict per-session serialisation via
#     ``_session_flush_locks`` while still letting *different* sessions run
#     in parallel.
#   * The background task is wrapped in ``asyncio.wait_for`` so that a stuck
#     provider call cannot hold ``chat_messages`` / ``provider`` references
#     forever.  See ``_DEFAULT_FLUSH_TIMEOUT_SECONDS`` (currently 90s) and
#     override per-deployment via ``FLOCKS_COMPACTION_FLUSH_TIMEOUT``.
#
# Set ``FLOCKS_COMPACTION_FLUSH_BACKGROUND=0`` to fall back to the legacy
# synchronous behaviour (useful for tests or debugging).

_FLUSH_TASK_NAME_PREFIX = "compaction.flush."

# Default upper bound on a single background flush.  ``extract_and_save``
# normally finishes in 5–30s; 90s gives us ~3x slack for cold provider
# starts / large contexts without holding ``chat_messages`` references for
# minutes when the LLM call truly wedges.  Override per deployment via
# ``FLOCKS_COMPACTION_FLUSH_TIMEOUT``.
_DEFAULT_FLUSH_TIMEOUT_SECONDS = 90

_pending_flush_tasks: set[asyncio.Task] = set()
_session_flush_locks: dict[str, asyncio.Lock] = {}


def _flush_in_background_enabled() -> bool:
    raw = os.getenv("FLOCKS_COMPACTION_FLUSH_BACKGROUND", "1").strip().lower()
    return raw not in {"0", "false", "no", "off", ""}


def _flush_timeout_seconds() -> float:
    raw = os.getenv("FLOCKS_COMPACTION_FLUSH_TIMEOUT")
    if raw is None or raw.strip() == "":
        return float(_DEFAULT_FLUSH_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warn("compaction.flush.timeout_parse_error", {
            "raw": raw,
            "fallback": _DEFAULT_FLUSH_TIMEOUT_SECONDS,
        })
        return float(_DEFAULT_FLUSH_TIMEOUT_SECONDS)
    if value <= 0:
        log.warn("compaction.flush.timeout_non_positive", {
            "raw": raw,
            "fallback": _DEFAULT_FLUSH_TIMEOUT_SECONDS,
        })
        return float(_DEFAULT_FLUSH_TIMEOUT_SECONDS)
    return value


# ---------------------------------------------------------------------------
# Chunked-summarization preemptive trigger
# ---------------------------------------------------------------------------
#
# Empirically a single ``summarize_single_pass`` call against a 10–14k char
# conversation can take 60–90s on slow OpenAI-compatible providers (e.g.
# minimax via threatbook).  ``summarize_chunked`` issues N small parallel
# calls + 1 short merge call, so it scales much better with provider
# latency even when the conversation would technically *fit* a single pass.
#
# The legacy hand-off rule was ``total_chars > target_chars * 2``, which
# means medium conversations (well below ~60k chars) always took the slow
# path.  We add a second, looser trigger that splits at a fraction of the
# target so even ~10k conversations get the parallel speedup.
#
# Tunables (all overridable via env for emergency tuning):
#   * ``FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO`` (default 0.2):
#     fraction of ``target_chars`` above which we proactively chunk.
#   * ``FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS`` (default 3):
#     desired number of chunks when preemptive chunking kicks in.
#   * ``FLOCKS_COMPACTION_MIN_CHUNK_CHARS`` (default 3000):
#     floor on chunk size so we never pay the merge-LLM tax for trivial
#     conversations (where single_pass is genuinely faster).

_DEFAULT_PREEMPTIVE_CHUNK_RATIO = 0.2
_DEFAULT_TARGET_PARALLEL_CHUNKS = 3
_DEFAULT_MIN_CHUNK_CHARS = 3000


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warn("compaction.env.parse_error", {
            "key": key, "raw": raw, "fallback": default,
        })
        return default
    if value <= 0:
        log.warn("compaction.env.non_positive", {
            "key": key, "raw": raw, "fallback": default,
        })
        return default
    return value


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warn("compaction.env.parse_error", {
            "key": key, "raw": raw, "fallback": default,
        })
        return default
    if value <= 0:
        log.warn("compaction.env.non_positive", {
            "key": key, "raw": raw, "fallback": default,
        })
        return default
    return value


def _decide_chunked_strategy(
    *, total_chars: int, target_chars: int,
) -> tuple[bool, Optional[int], str]:
    """Decide whether to use chunked summarisation and at what chunk size.

    Returns ``(use_chunked, chunk_size, reason)``:
      * ``use_chunked``: ``True`` if the caller should invoke
        ``summarize_chunked``; ``False`` for ``summarize_single_pass``.
      * ``chunk_size``: when chunked, the per-chunk char budget passed
        through to ``summarize_chunked``; ``None`` when not chunked.
      * ``reason``: short tag for observability (logged at the call site).

    Three branches:
      1. ``"oversize"``    — legacy rule, conversation is much bigger
         than the target; chunked is *required* for correctness.
      2. ``"preemptive"``  — conversation fits single-pass but is large
         enough that parallel chunked summarisation is faster than one
         big LLM call.  Triggered when ``total_chars`` exceeds both
         ``target_chars * ratio`` and ``min_chunk_chars * 2``.
      3. ``"single_pass"`` — small conversation; parallelism overhead
         (one extra merge LLM call) outweighs the speedup.
    """
    if total_chars > target_chars * 2:
        # Legacy hard requirement — content does not fit a single pass.
        ratio_hint = _env_float(
            "FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO",
            _DEFAULT_PREEMPTIVE_CHUNK_RATIO,
        )
        target_parallel = _env_int(
            "FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS",
            _DEFAULT_TARGET_PARALLEL_CHUNKS,
        )
        min_chunk = _env_int(
            "FLOCKS_COMPACTION_MIN_CHUNK_CHARS",
            _DEFAULT_MIN_CHUNK_CHARS,
        )
        # For oversize content keep the legacy behaviour: pass through
        # the original target_chars as the split cap (chunk_size=None
        # makes summarize_chunked default to target_chars).  Splitting
        # finer for already-huge conversations would explode chunk
        # count without obvious benefit.
        del ratio_hint, target_parallel, min_chunk
        return True, None, "oversize"

    ratio = _env_float(
        "FLOCKS_COMPACTION_PREEMPTIVE_CHUNK_RATIO",
        _DEFAULT_PREEMPTIVE_CHUNK_RATIO,
    )
    min_chunk = _env_int(
        "FLOCKS_COMPACTION_MIN_CHUNK_CHARS",
        _DEFAULT_MIN_CHUNK_CHARS,
    )
    target_parallel = _env_int(
        "FLOCKS_COMPACTION_TARGET_PARALLEL_CHUNKS",
        _DEFAULT_TARGET_PARALLEL_CHUNKS,
    )

    threshold = max(min_chunk * 2, int(target_chars * ratio))
    if total_chars < threshold:
        return False, None, "single_pass"

    # Aim for ``target_parallel`` chunks; never go below ``min_chunk``
    # so we don't fragment a 6k conversation into 6 tiny calls.
    raw_size = total_chars // max(1, target_parallel) + 1
    chunk_size = max(min_chunk, raw_size)
    return True, chunk_size, "preemptive"


def _get_flush_lock(session_id: str) -> asyncio.Lock:
    """Return (lazily creating) the per-session flush serialisation lock."""
    lock = _session_flush_locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        _session_flush_locks[session_id] = lock
    return lock


def _release_session_lock_if_idle(session_id: str, lock: asyncio.Lock) -> None:
    """Drop ``session_id``'s lock entry if no other task still needs it.

    Called after a background flush finishes.  Without this the
    ``_session_flush_locks`` registry would grow unboundedly across the
    lifetime of a long-running process (each new session_id is unique).

    Safe under asyncio's single-threaded scheduler:
      * we exclude the *current* task (its done-callback hasn't run yet
        so it is still in ``_pending_flush_tasks``);
      * we only pop the entry when it is *still* the same Lock instance
        we acquired, defending against an interleaved dispatch having
        replaced it (cannot happen today but cheap to be safe).

    Best-effort under cancellation: if two same-session tasks are
    cancelled and run their ``finally`` blocks before either is marked
    done, each will see the other as "still pending" and skip cleanup.
    The orphaned entry is bounded (only same-session shutdown races) and
    ``drain_pending_flush_tasks(cancel_on_timeout=True)`` is the
    documented shutdown path, after which the process is expected to
    terminate or the registry can be cleared explicitly.
    """
    current = asyncio.current_task()
    for t in _pending_flush_tasks:
        if t is current:
            continue
        if t.done():
            continue
        if _session_id_from_task(t) == session_id:
            return
    if _session_flush_locks.get(session_id) is lock:
        _session_flush_locks.pop(session_id, None)


def _session_id_from_task(task: asyncio.Task) -> Optional[str]:
    """Return the session_id encoded in a flush task's name, or None."""
    name = task.get_name() or ""
    if name.startswith(_FLUSH_TASK_NAME_PREFIX):
        return name[len(_FLUSH_TASK_NAME_PREFIX):]
    return None


def _on_flush_task_done(task: asyncio.Task) -> None:
    _pending_flush_tasks.discard(task)
    session_id = _session_id_from_task(task)
    payload: Dict[str, Any] = {"task": task.get_name()}
    if session_id is not None:
        payload["session_id"] = session_id
    if task.cancelled():
        log.info("compaction.flush.background.cancelled", payload)
        return
    exc = task.exception()
    if exc is not None:
        # Unexpected exception (network, disk, ...).  TimeoutError is
        # already absorbed inside ``_runner`` and never reaches here, so
        # anything we see is genuinely unplanned and worth ERROR level.
        log.error("compaction.flush.background.error", {
            **payload,
            "error": str(exc),
            "error_type": type(exc).__name__,
        })
    else:
        log.debug("compaction.flush.background.done", payload)


async def drain_pending_flush_tasks(
    timeout: float = 30.0,
    *,
    cancel_on_timeout: bool = False,
) -> int:
    """Await all in-flight background flush tasks.

    Intended for graceful process shutdown / test fixtures so that
    fire-and-forget flushes do not get cancelled with their daily-memory
    writes half-applied.

    Args:
        timeout: Wall-clock seconds to wait for pending tasks.
        cancel_on_timeout: When True, leftover tasks are cancelled and
            briefly awaited so the caller can close the event loop
            cleanly.  Use this from shutdown paths.  When False (default),
            leftover tasks are logged at WARN and left running — the
            caller is expected to keep the loop alive.

    Returns:
        The number of tasks that were *still pending* when the function
        returned (always 0 when ``cancel_on_timeout=True``).

    Note:
        The pending set is snapshotted at function entry; any new
        dispatches that occur *during* drain will NOT be awaited and
        will not be reflected in the returned leftover count.  Callers
        performing a true shutdown should arrange to stop new dispatches
        first (e.g. by flipping a "shutting down" flag the dispatcher
        consults before scheduling).
    """
    if not _pending_flush_tasks:
        return 0
    pending = list(_pending_flush_tasks)
    log.info("compaction.flush.drain.start", {
        "pending": len(pending),
        "timeout": timeout,
        "cancel_on_timeout": cancel_on_timeout,
    })
    done, still_pending = await asyncio.wait(pending, timeout=timeout)

    leftover = len(still_pending)
    if still_pending:
        names = [t.get_name() for t in still_pending]
        if cancel_on_timeout:
            for t in still_pending:
                t.cancel()
            # Absorb CancelledError so the loop can be torn down.
            await asyncio.gather(*still_pending, return_exceptions=True)
            log.warn("compaction.flush.drain.cancelled_pending", {
                "cancelled": leftover,
                "task_names": names,
            })
            leftover = 0
        else:
            log.warn("compaction.flush.drain.timeout_pending", {
                "still_pending": leftover,
                "task_names": names,
            })

    log.info("compaction.flush.drain.complete", {
        "drained": len(done),
        "still_pending": leftover,
    })
    return leftover


class SessionCompaction:
    """Session Compaction namespace.

    Handles context window overflow by:
    1. Checking if context is overflowing (is_overflow)
    2. Pruning old tool call outputs (prune)
    3. Creating summary messages to compress history (process)
    """

    # ------------------------------------------------------------------
    # Overflow detection
    # ------------------------------------------------------------------

    @classmethod
    async def is_overflow(
        cls,
        tokens: Dict[str, Any],
        model_context: int,
        model_input: Optional[int] = None,
        model_output: Optional[int] = None,
        auto_disabled: bool = False,
        policy: Optional[CompactionPolicy] = None,
    ) -> bool:
        """Check if context is overflowing."""
        if auto_disabled:
            return False

        if model_context == 0:
            return False

        input_tokens = tokens.get("input", 0)
        cache_read = tokens.get("cache", {}).get("read", 0) if isinstance(tokens.get("cache"), dict) else 0
        output_tokens = tokens.get("output", 0)
        count = input_tokens + cache_read + output_tokens

        if policy is not None:
            is_over = count > policy.overflow_threshold
            if is_over:
                log.info("compaction.overflow_detected", {
                    "count": count,
                    "overflow_threshold": policy.overflow_threshold,
                    "tier": policy.tier.value,
                })
            return is_over

        output_limit = model_output or SessionPrompt.OUTPUT_TOKEN_MAX
        effective_output = min(output_limit, SessionPrompt.OUTPUT_TOKEN_MAX)

        if model_input:
            usable = model_input
        else:
            usable = model_context - effective_output

        return count > usable

    # ------------------------------------------------------------------
    # Pruning (delegates to pruning module)
    # ------------------------------------------------------------------

    @classmethod
    async def prune(
        cls,
        session_id: str,
        prune_disabled: bool = False,
        policy: Optional[CompactionPolicy] = None,
    ) -> None:
        """Prune old tool call outputs from session messages."""
        await pruning.prune(session_id, prune_disabled, policy)

    # ------------------------------------------------------------------
    # Oversized tool output truncation (delegates to pruning module)
    # ------------------------------------------------------------------

    @classmethod
    async def truncate_oversized_tool_outputs(
        cls,
        session_id: str,
        context_window_tokens: int,
    ) -> int:
        """Scan session for oversized tool outputs and truncate in-place."""
        return await pruning.truncate_oversized_tool_outputs(
            session_id, context_window_tokens,
        )

    # ------------------------------------------------------------------
    # Memory flush (delegates to memory module, kept for backward compat)
    # ------------------------------------------------------------------

    @classmethod
    async def _flush_memory_to_daily(
        cls,
        session_id: str,
        summary: str,
        chat_messages: list,
        model_id: str,
        provider: Any,
        ChatMessage: Any,
        policy: Optional[CompactionPolicy] = None,
    ) -> None:
        """Extract key memories and save to daily file.

        Thin wrapper around ``memory.flush.extract_and_save`` so that
        existing test patches on ``SessionCompaction._flush_memory_to_daily``
        continue to work.
        """
        from flocks.memory.flush import extract_and_save

        await extract_and_save(
            session_id=session_id,
            summary=summary,
            chat_messages=chat_messages,
            model_id=model_id,
            provider=provider,
            ChatMessage=ChatMessage,
            policy=policy,
            count_tokens=SessionPrompt.count_tokens,
        )

    @classmethod
    async def _dispatch_memory_flush(
        cls,
        *,
        session_id: str,
        summary_text: str,
        chat_messages: list,
        model_id: str,
        provider_client: Any,
        ChatMessage: Any,
        policy: Optional[CompactionPolicy],
    ) -> None:
        """Run ``_flush_memory_to_daily`` either in the background or inline.

        Background scheduling is the default; the legacy synchronous mode is
        re-enabled by setting ``FLOCKS_COMPACTION_FLUSH_BACKGROUND=0``.

        The background path adds two safety guarantees over a naive
        ``create_task``:

        1. **Per-session serialisation** — the daily memory file is updated
           with a non-atomic read-modify-write, so two overlapping flushes
           for the same session would race.  We acquire a per-session
           ``asyncio.Lock`` so a second compaction's flush is queued behind
           the first.  Different sessions remain fully parallel.
        2. **Hard timeout** — wraps the flush in ``asyncio.wait_for`` so a
           stuck provider call cannot indefinitely retain ``chat_messages``
           / provider references.  Tunable via
           ``FLOCKS_COMPACTION_FLUSH_TIMEOUT`` (default
           ``_DEFAULT_FLUSH_TIMEOUT_SECONDS``, currently 90s).
        """
        kwargs = dict(
            session_id=session_id,
            summary=summary_text,
            chat_messages=chat_messages,
            model_id=model_id,
            provider=provider_client,
            ChatMessage=ChatMessage,
            policy=policy,
        )

        if not _flush_in_background_enabled():
            await cls._flush_memory_to_daily(**kwargs)
            return

        timeout_s = _flush_timeout_seconds()

        async def _runner() -> None:
            lock = _get_flush_lock(session_id)
            wait_started = time.perf_counter()
            try:
                async with lock:
                    wait_ms = (time.perf_counter() - wait_started) * 1000.0
                    run_started = time.perf_counter()
                    try:
                        await asyncio.wait_for(
                            cls._flush_memory_to_daily(**kwargs),
                            timeout=timeout_s,
                        )
                        duration_ms = (time.perf_counter() - run_started) * 1000.0
                        log.info("compaction.flush.background.completed", {
                            "session_id": session_id,
                            "wait_ms": round(wait_ms, 2),
                            "duration_ms": round(duration_ms, 2),
                        })
                    except asyncio.TimeoutError:
                        duration_ms = (time.perf_counter() - run_started) * 1000.0
                        # Known failure mode that we deliberately bound;
                        # WARN keeps it out of error-rate alerts while
                        # still surfacing in dashboards.
                        log.warn("compaction.flush.background.timeout", {
                            "session_id": session_id,
                            "wait_ms": round(wait_ms, 2),
                            "duration_ms": round(duration_ms, 2),
                            "timeout_s": timeout_s,
                        })
            finally:
                _release_session_lock_if_idle(session_id, lock)

        flush_task = asyncio.create_task(
            _runner(),
            name=f"{_FLUSH_TASK_NAME_PREFIX}{session_id}",
        )
        _pending_flush_tasks.add(flush_task)
        flush_task.add_done_callback(_on_flush_task_done)
        log.info("compaction.flush.scheduled_background", {
            "session_id": session_id,
            "timeout_s": timeout_s,
        })

    # ------------------------------------------------------------------
    # Main compaction process
    # ------------------------------------------------------------------

    @classmethod
    async def process(
        cls,
        session_id: str,
        parent_id: str,
        messages: List[Dict[str, Any]],
        model_id: str,
        provider_id: str,
        auto: bool = True,
        custom_prompt: Optional[str] = None,
        policy: Optional[CompactionPolicy] = None,
    ) -> Literal["continue", "stop"]:
        """Process compaction by generating a summary message.

        Creates an assistant message with a summary of the conversation
        to reduce token count while preserving context.
        """
        effective_summary_tokens = policy.summary_max_tokens if policy else 4000

        log.info("compaction.process.start", {
            "session_id": session_id,
            "auto": auto,
            "message_count": len(messages),
            "summary_max_tokens": effective_summary_tokens,
            "tier": policy.tier.value if policy else "legacy",
        })

        try:
            from flocks.provider.provider import Provider, ChatMessage
        except ImportError:
            log.error("compaction.process.import_error")
            return "stop"

        provider_client = Provider.get(provider_id)
        if not provider_client:
            log.error("compaction.process.provider_not_found", {
                "session_id": session_id,
                "provider_id": provider_id,
                "model_id": model_id,
            })
            return "stop"

        try:
            await Provider.apply_config(provider_id=provider_id)
        except Exception as e:
            log.warn("compaction.process.provider_apply_config_error", {
                "session_id": session_id,
                "provider_id": provider_id,
                "error": str(e),
            })

        prompt_text = custom_prompt or DEFAULT_COMPACTION_PROMPT

        # Load messages WITH their parts for text content
        try:
            from flocks.session.message import Message as MsgStore
            msgs_with_parts = await MsgStore.list_with_parts(session_id)
        except Exception as e:
            log.warn("compaction.process.load_parts_error", {"error": str(e)})
            msgs_with_parts = []

        chat_messages = cls._extract_chat_messages(msgs_with_parts, ChatMessage, session_id, policy)

        log.info("compaction.process.messages_loaded", {
            "session_id": session_id,
            "raw_count": len(messages),
            "with_parts_count": len(msgs_with_parts),
            "chat_messages_count": len(chat_messages),
            "total_chars": sum(len(m.content) for m in chat_messages),
        })

        # Summarization
        usable = policy.usable_context if policy else 96_000
        reserve_tokens = effective_summary_tokens + 1000
        target_tokens = max(1000, usable - reserve_tokens)
        target_chars = max(3000, target_tokens * 2)

        conversation_text = "\n\n".join(m.content for m in chat_messages)
        total_chars = len(conversation_text)

        log.info("compaction.process.context_prepared", {
            "session_id": session_id,
            "usable_context": usable,
            "target_tokens": target_tokens,
            "target_chars": target_chars,
            "conversation_chars": total_chars,
        })

        try:
            use_chunked, chunk_size, decision = _decide_chunked_strategy(
                total_chars=total_chars, target_chars=target_chars,
            )

            log.info("compaction.process.strategy", {
                "session_id": session_id,
                "decision": decision,
                "use_chunked": use_chunked,
                "chunk_size": chunk_size,
                "total_chars": total_chars,
                "target_chars": target_chars,
            })

            if not use_chunked:
                summary_text = await summary.summarize_single_pass(
                    conversation_text, prompt_text, target_chars,
                    provider_client, model_id, effective_summary_tokens,
                )
            else:
                summary_text = await summary.summarize_chunked(
                    chat_messages, prompt_text, target_chars,
                    provider_client, model_id, effective_summary_tokens,
                    session_id,
                    chunk_size=chunk_size,
                )

            log.info("compaction.process.complete", {
                "session_id": session_id,
                "summary_length": len(summary_text) if summary_text else 0,
                "summary_max_tokens": effective_summary_tokens,
                "chunked": use_chunked,
                "decision": decision,
            })

            if not summary_text:
                log.warn("compaction.process.empty_summary_fallback", {"session_id": session_id})
                summary_text = summary.build_fallback_summary(chat_messages)

            # Memory flush — issues another LLM call to extract durable
            # memories. The compacted session does NOT depend on it for
            # continuation, so we schedule it as a fire-and-forget task by
            # default and let the main flow proceed with archive + summary
            # write immediately.
            await cls._dispatch_memory_flush(
                session_id=session_id,
                summary_text=summary_text,
                chat_messages=chat_messages,
                model_id=model_id,
                provider_client=provider_client,
                ChatMessage=ChatMessage,
                policy=policy,
            )

            # Write summary and archive old messages
            return await cls._archive_and_write_summary(
                session_id=session_id,
                parent_id=parent_id,
                summary=summary_text,
                model_id=model_id,
                provider_id=provider_id,
                auto=auto,
                policy=policy,
            )

        except Exception as e:
            log.error("compaction.process.error", {
                "session_id": session_id,
                "error": str(e),
            })
            return "stop"

    # ------------------------------------------------------------------
    # Create compaction marker
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        session_id: str,
        agent: str,
        model_provider_id: str,
        model_id: str,
        auto: bool = True,
    ) -> None:
        """Create a user message with a compaction marker."""
        log.info("compaction.create", {
            "session_id": session_id,
            "agent": agent,
            "auto": auto,
        })

        try:
            from flocks.session.message import CompactionPart, Message, MessageRole
        except ImportError:
            log.warn("compaction.create.import_error")
            return

        msg = await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="[Compaction requested]",
            agent=agent,
            model={"providerID": model_provider_id, "modelID": model_id},
            synthetic=True,
        )
        await Message.add_part(
            session_id,
            msg.id,
            CompactionPart(
                sessionID=session_id,
                messageID=msg.id,
                auto=auto,
            ),
        )

        log.info("compaction.created", {"message_id": msg.id})

    # ------------------------------------------------------------------
    # Full compaction (prune + summarize)
    # ------------------------------------------------------------------

    @classmethod
    async def compact(
        cls,
        session_id: str,
        messages: List[Dict[str, Any]],
        context_limit: int,
        provider_id: str,
        model_id: str,
        auto: bool = True,
        policy: Optional[CompactionPolicy] = None,
    ) -> CompactionResult:
        """Perform full compaction on session (prune + summarize)."""
        try:
            from flocks.session.message import Message
        except ImportError:
            return CompactionResult(success=False)

        tokens_before = SessionPrompt.count_message_tokens(messages)
        msg_count_before = len(messages)

        await cls.prune(session_id, policy=policy)

        # Re-fetch messages and re-estimate tokens after pruning
        refreshed = await Message.list(session_id)
        tokens_after = await SessionPrompt.estimate_full_context_tokens(
            session_id, refreshed,
        )

        result = CompactionResult(
            tokens_before=tokens_before,
            tokens_after=tokens_after,
        )

        token_info = {
            "input": tokens_after,
            "output": 0,
            "cache": {"read": 0, "write": 0},
        }

        if await cls.is_overflow(token_info, context_limit, policy=policy):
            # Find the last user message for a valid parent_id
            parent_id = ""
            for m in reversed(refreshed):
                role = m.role.value if hasattr(m.role, "value") else m.role
                if role == "user":
                    parent_id = m.id
                    break

            refreshed_dicts = [
                m.model_dump() if hasattr(m, "model_dump") else m.__dict__
                for m in refreshed
            ]
            status = await cls.process(
                session_id=session_id,
                parent_id=parent_id,
                messages=refreshed_dicts,
                model_id=model_id,
                provider_id=provider_id,
                auto=auto,
                policy=policy,
            )

            if status == "continue":
                result.summary_created = True
                # Re-count after full compaction
                post_msgs = await Message.list(session_id)
                result.tokens_after = await SessionPrompt.estimate_full_context_tokens(
                    session_id, post_msgs,
                )
                result.messages_removed = max(0, msg_count_before - len(post_msgs))
                log.info("compaction.complete", {
                    "session_id": session_id,
                    "tokens_before": tokens_before,
                    "tokens_after": result.tokens_after,
                    "messages_removed": result.messages_removed,
                    "summary_created": True,
                })

        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    # Max chars for tool input/output in summarization context.
    # Scales with context window: larger windows get more detail preserved.
    _TOOL_CONTENT_MIN_CHARS = 500
    _TOOL_CONTENT_MAX_CHARS = 4000

    @classmethod
    def _tool_content_limit(cls, policy: Optional[CompactionPolicy] = None) -> int:
        """Compute per-tool content char limit based on context window size."""
        if not policy:
            return 1500
        usable = policy.usable_context
        limit = max(cls._TOOL_CONTENT_MIN_CHARS, usable // 40)
        return min(limit, cls._TOOL_CONTENT_MAX_CHARS)

    @classmethod
    def _strip_tool_content(cls, raw: str, limit: int) -> str:
        """Truncate tool content while preserving leading file paths / identifiers."""
        text = str(raw) if not isinstance(raw, str) else raw
        if len(text) <= limit:
            return text
        if limit < 60:
            return text[:limit]
        head_budget = min(limit // 2, 1000)
        tail_budget = limit - head_budget - 30
        if tail_budget <= 0:
            return text[:limit]
        return (
            text[:head_budget]
            + "\n…(content truncated)…\n"
            + text[-tail_budget:]
        )

    @classmethod
    def _extract_chat_messages(
        cls,
        msgs_with_parts: list,
        ChatMessage: Any,
        session_id: str = "",
        policy: Optional[CompactionPolicy] = None,
    ) -> list:
        """Convert stored messages with parts into ChatMessage objects."""
        content_limit = cls._tool_content_limit(policy)
        chat_messages = []
        for mwp in msgs_with_parts:
            role = mwp.info.role if hasattr(mwp.info, "role") else "user"
            text_parts = []
            part_types_seen = []
            for part in (mwp.parts or []):
                ptype = getattr(part, "type", None)
                part_types_seen.append(ptype)

                if ptype == "text":
                    t = getattr(part, "text", "") or ""
                    if t:
                        text_parts.append(t)
                elif ptype == "reasoning":
                    t = getattr(part, "text", "") or ""
                    if t:
                        text_parts.append(t)
                elif ptype == "tool":
                    state = getattr(part, "state", None)
                    if state is not None:
                        if hasattr(state, "model_dump"):
                            sd = state.model_dump()
                        elif isinstance(state, dict):
                            sd = state
                        else:
                            sd = {}

                        tool_name = getattr(part, "tool", "") or sd.get("tool", "")
                        tool_input = sd.get("input", "")
                        tool_output = sd.get("output", "")

                        header = f"[tool: {tool_name}]" if tool_name else "[tool]"
                        if tool_input:
                            text_parts.append(
                                f"{header} input: {cls._strip_tool_content(tool_input, content_limit)}"
                            )
                        if tool_output:
                            text_parts.append(
                                f"{header} output: {cls._strip_tool_content(tool_output, content_limit)}"
                            )

            content = "\n".join(text_parts)

            log.debug("compaction.process.msg_parts", {
                "session_id": session_id,
                "msg_id": mwp.info.id,
                "role": role,
                "part_types": part_types_seen,
                "content_len": len(content),
            })

            if role in ("user", "assistant", "system") and content:
                chat_messages.append(ChatMessage(role=role, content=content))

        return chat_messages

    @classmethod
    async def _archive_and_write_summary(
        cls,
        session_id: str,
        parent_id: str,
        summary: str,
        model_id: str,
        provider_id: str,
        auto: bool,
        policy: Optional[CompactionPolicy],
    ) -> Literal["continue", "stop"]:
        """Archive old messages and write the summary message."""
        try:
            from flocks.session.message import Message, MessageRole
        except ImportError:
            log.error("compaction.process.message_import_error")
            return "stop"

        all_msgs = await Message.list(session_id)

        step_count = 0
        cutoff_idx = 0
        for i in range(len(all_msgs) - 1, -1, -1):
            msg_i = all_msgs[i]
            role = msg_i.role.value if hasattr(msg_i.role, 'value') else msg_i.role
            if role == "assistant":
                finish = getattr(msg_i, 'finish', None)
                if finish == "summary":
                    continue
                step_count += 1
                if step_count >= PRESERVE_LAST_STEPS:
                    cutoff_idx = i
                    break

        if cutoff_idx == 0:
            preserve_last = policy.preserve_last if policy else 4
            if len(all_msgs) > preserve_last:
                cutoff_idx = len(all_msgs) - preserve_last

        to_delete = all_msgs[:cutoff_idx]

        deleted_count = 0
        archived_ids = set()
        for old_msg in to_delete:
            try:
                await Message.archive(session_id, old_msg.id)
                deleted_count += 1
                archived_ids.add(old_msg.id)
            except Exception as del_err:
                log.warn("compaction.process.archive_error", {
                    "message_id": old_msg.id,
                    "error": str(del_err),
                })

        preserved = [m for m in all_msgs if m.id not in archived_ids]
        await pruning.validate_preserved_messages(session_id, preserved)

        summary_msg = await Message.create(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=summary,
            parent_id=parent_id,
            model_id=model_id,
            provider_id=provider_id,
            summary=True,
            finish="summary",
        )

        log.info("compaction.process.summary_written", {
            "session_id": session_id,
            "summary_msg_id": summary_msg.id,
            "archived_messages": deleted_count,
            "preserved_steps": step_count,
            "total_preserved_messages": len(all_msgs) - deleted_count,
            "summary_tokens": SessionPrompt.estimate_tokens(summary),
        })

        if auto:
            try:
                post_compaction_text = await cls._build_post_compaction_context(
                    session_id, policy=policy,
                )
                await Message.create(
                    session_id=session_id,
                    role=MessageRole.USER,
                    content=post_compaction_text,
                    synthetic=True,
                )
                log.info("compaction.continuation_message_created", {
                    "session_id": session_id,
                })
            except Exception as cont_err:
                log.warn("compaction.continuation_message_error", {
                    "session_id": session_id,
                    "error": str(cont_err),
                })

        return "continue"

    @classmethod
    async def _build_post_compaction_context(
        cls,
        session_id: str,
        policy: Optional[CompactionPolicy] = None,
    ) -> str:
        """Build the post-compaction continuation message.

        Re-injects critical session context that may be lost after compaction,
        similar to OpenClaw's post-compaction system event injection.
        Only injects project rules for medium+ context window models to avoid
        bloating the context on small models.
        """
        from .policy import ContextTier

        parts = [
            "The conversation history has been compacted. "
            "A summary of previous context is provided above.",
            "",
            "Continue if you have next steps.",
        ]

        # Only inject project rules for models with sufficient context window.
        # Small-tier models cannot afford the extra tokens.
        tier = policy.tier if policy else ContextTier.MEDIUM
        if tier in (ContextTier.SMALL,):
            return "\n".join(parts)

        try:
            from flocks.session.session import Session
            session_info = await Session.get_by_id(session_id)
            if session_info and session_info.directory:
                import os
                max_rules_chars = 4000 if tier in (ContextTier.LARGE, ContextTier.XLARGE) else 2000
                for rules_file in ["AGENTS.md", ".flocks/rules/rules.md"]:
                    rules_path = os.path.join(session_info.directory, rules_file)
                    if os.path.isfile(rules_path):
                        with open(rules_path, "r", encoding="utf-8", errors="ignore") as f:
                            rules_content = f.read(max_rules_chars)
                        if rules_content.strip():
                            parts.append("")
                            parts.append(f"**Project rules** (from `{rules_file}`):")
                            parts.append(rules_content.strip())
                        break
        except Exception as e:
            log.debug("compaction.post_context.rules_error", {"error": str(e)})

        return "\n".join(parts)
