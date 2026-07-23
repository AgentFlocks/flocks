"""Compaction summarization strategies — conversation history compression."""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable, Dict, Optional, Any

# Same shape as ``compaction.ProgressCallback``.  Re-declared locally to
# keep the summary module free of an upward import (``compaction.py``
# already imports ``summary`` — making it a one-way dependency avoids a
# cycle if anyone ever adds a strict ``from .compaction import ...``
# here in the future).
ProgressCallback = Callable[[str, Dict[str, Any]], Awaitable[None]]

from flocks.provider.provider import ChatMessage
from flocks.utils.log import Log
from flocks.session.prompt import SessionPrompt
from flocks.session.llm_hook_utils import (
    apply_hook_request_output,
    restore_text_with_replacements,
    serialize_chat_message,
    stream_text_replacements_from_hook_output,
    strip_think_blocks,
)
from .models import DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS

log = Log.create(service="session.compaction.summarization")


def build_iterative_prompt(prompt_text: str, previous_summary: Optional[str]) -> str:
    """Compose the per-call prompt with optional previous summary.

    When ``previous_summary`` is supplied, the iterative template wraps
    the conversation as "delta on top of an authoritative prior summary".
    Otherwise we return ``prompt_text`` unchanged.
    """
    if not previous_summary:
        return prompt_text
    return DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS.format(
        previous_summary=previous_summary.strip(),
    )


COMPACTION_TIMEOUT_SECONDS = 300


_REQUIRED_SECTIONS = [
    "## Decisions",
    "## Current Task",
    "## Open TODOs",
    "## Key Files & Identifiers",
]


def _build_focus_block(focus_instruction: Optional[str]) -> str:
    """Render the ``## User Focus`` block to append to summarisation prompts.

    The block is empty (``""``) when no focus instruction is supplied,
    so callers can unconditionally interpolate it without conditionals.
    The block is wrapped in delimiters that are obvious to the model so
    the user-supplied text cannot accidentally collide with our
    structural section headers (``## Decisions``…).
    """
    if not focus_instruction:
        return ""
    text = focus_instruction.strip()
    if not text:
        return ""
    return (
        "\n\n## User Focus (user-supplied; emphasise these aspects in the summary)\n"
        f"{text}\n"
    )


# ---------------------------------------------------------------------------
# Per-message serialization (mirrors hermes-agent _serialize_for_summary)
# ---------------------------------------------------------------------------
#
# Instead of truncating the *entire* conversation from the tail, we cap each
# individual message at _MSG_CONTENT_MAX chars while keeping a head and tail
# slice.  This preserves signal from every turn (user asks, early decisions,
# old tool outputs) rather than silently discarding the beginning of the session.
#
# Constants intentionally match hermes-agent's defaults so behaviour is
# comparable and easy to audit across projects:
#   _MSG_CONTENT_MAX  = 6 000 chars  (~1 500 tokens)
#   _MSG_CONTENT_HEAD = 4 000 chars  (leading context / file paths / commands)
#   _MSG_CONTENT_TAIL = 1 500 chars  (outcome / last line / error message)

_MSG_CONTENT_MAX  = 6_000
_MSG_CONTENT_HEAD = 4_000
_MSG_CONTENT_TAIL = 1_500


def _serialize_messages_per_truncate(chat_messages: list) -> str:
    """Serialize messages with per-message content truncation.

    Each message is capped at ``_MSG_CONTENT_MAX`` characters; messages that
    exceed the cap are split into a head slice + ``…[truncated]…`` + tail
    slice so the most informative parts of every turn are always present.

    This is the same strategy as hermes-agent ``_serialize_for_summary``
    and is strictly better than the old ``conversation_text[-target_chars:]``
    tail-cut when the session is long: the tail-cut silently discards
    every earlier user request and decision, whereas per-message truncation
    keeps at least a fragment from each turn.
    """
    parts: list[str] = []
    for msg in chat_messages:
        role = msg.role if hasattr(msg, "role") else "unknown"
        content = msg.content if hasattr(msg, "content") else ""
        if not isinstance(content, str):
            content = str(content)
        if len(content) > _MSG_CONTENT_MAX:
            content = (
                content[:_MSG_CONTENT_HEAD]
                + "\n…[truncated]…\n"
                + content[-_MSG_CONTENT_TAIL:]
            )
        parts.append(f"[{role.upper()}]: {content}")
    return "\n\n".join(parts)


def build_fallback_summary(chat_messages: list) -> str:
    """Build a structured fallback summary when LLM summarization fails.

    Extracts key information from the last few messages to produce a
    best-effort summary so the session can continue.
    """
    parts: list[str] = []
    parts.append("# Session Summary (auto-generated fallback)\n")
    parts.append("The previous conversation was compressed. Key points:\n")

    recent = chat_messages[-10:] if len(chat_messages) > 10 else chat_messages
    for msg in recent:
        role = msg.role if hasattr(msg, 'role') else 'unknown'
        content = msg.content if hasattr(msg, 'content') else str(msg)
        if not content:
            continue
        if not isinstance(content, str):
            content = str(content)
        snippet = content[:300]
        if len(content) > 300:
            snippet += "..."
        parts.append(f"- [{role}]: {snippet}")

    parts.append("\nPlease continue from where we left off.")
    return "\n".join(parts)


def validate_summary_quality(summary: str) -> tuple[bool, list[str]]:
    """Basic quality check: verify required structural sections are present.

    Returns (passed, missing_sections).
    """
    if not summary or len(summary.strip()) < 50:
        return False, ["summary too short"]

    missing = [s for s in _REQUIRED_SECTIONS if s not in summary]
    return len(missing) == 0, missing


async def _llm_chat_with_timeout(
    provider_client: Any,
    model_id: str,
    messages: list,
    max_tokens: int,
    timeout: int = COMPACTION_TIMEOUT_SECONDS,
    session_id: Optional[str] = None,
    purpose: str = "compaction_summary",
) -> Any:
    """Call provider_client.chat with a timeout guard."""
    provider_options: Dict[str, Any] = {"max_tokens": max_tokens}
    replacements: list[tuple[str, str]] = []
    if session_id:
        try:
            from flocks.hooks.pipeline import HookPipeline, HookStage

            provider_id = (
                getattr(provider_client, "provider_id", None)
                or getattr(provider_client, "id", None)
                or provider_client.__class__.__name__
            )
            llm_hook_metadata = {
                "sessionID": session_id,
                "agent": "session.compaction",
                "step": None,
                "model": {
                    "providerID": provider_id,
                    "modelID": model_id,
                },
                "purpose": purpose,
            }
            if await HookPipeline.has_stage_handlers(HookStage.LLM_BEFORE, llm_hook_metadata):
                llm_before_ctx = await HookPipeline.run_llm_before({
                    **llm_hook_metadata,
                    "request": {
                        "messageCount": len(messages),
                        "messages": [serialize_chat_message(message) for message in messages],
                        "toolCount": 0,
                        "tools": [],
                        "providerOptions": dict(provider_options),
                        "providerToolsEnabled": False,
                    },
                })
                hook_output = getattr(llm_before_ctx, "output", None) or {}
                messages, provider_options = apply_hook_request_output(
                    messages,
                    provider_options,
                    hook_output,
                )
                replacements = stream_text_replacements_from_hook_output(hook_output)
        except Exception as hook_err:
            log.error("compaction.llm_before_hook.error", {
                "session_id": session_id,
                "purpose": purpose,
                "error": str(hook_err),
            })
            raise RuntimeError("compaction llm_before hook failed; request was not sent") from hook_err

    provider_options.setdefault("max_tokens", max_tokens)
    response = await asyncio.wait_for(
        provider_client.chat(
            model_id=model_id,
            messages=messages,
            **provider_options,
        ),
        timeout=timeout,
    )
    if replacements and response is not None and isinstance(getattr(response, "content", None), str):
        response.content = restore_text_with_replacements(response.content, replacements)
    if response is not None and isinstance(getattr(response, "content", None), str):
        response.content = strip_think_blocks(response.content)
    return response


async def summarize_single_pass(
    conversation_text: str,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
    focus_instruction: Optional[str] = None,
    previous_summary: Optional[str] = None,
    chat_messages: Optional[list] = None,
    session_id: Optional[str] = None,
) -> Optional[str]:
    """Generate summary in a single LLM call.

    When ``chat_messages`` is supplied the conversation is serialized using
    per-message truncation (``_serialize_messages_per_truncate``) before the
    LLM call.  Each message is capped at ``_MSG_CONTENT_MAX`` chars
    (head + ``…[truncated]…`` + tail) so every turn contributes at least
    a fragment to the summary input.  This mirrors hermes-agent's strategy
    and is strictly better than the old tail-cut (``text[-target_chars:]``)
    for long sessions where the old approach silently discarded all early
    context.

    When ``chat_messages`` is not supplied (backward compat / callers that
    only have a pre-joined string) the old tail-cut behaviour is retained.

    ``focus_instruction``: optional free-form focus string from ``/compact``.
    ``previous_summary`` (E1): prior compaction summary; reframes the prompt
    as "merge new turns into the prior summary" rather than compressing from
    scratch.
    """
    if chat_messages:
        # Per-message truncation path (hermes-style): every turn contributes
        # a capped fragment (head + tail per message), so early decisions
        # and user requests are preserved instead of being silently dropped
        # by a whole-conversation tail-cut.
        #
        # We deliberately do NOT apply a secondary tail-cut to ``target_chars``
        # here.  A whole-conversation ``text[-target_chars:]`` would undo the
        # per-message head/tail preservation: the early head slices of every
        # turn would be silently dropped, defeating the entire reason for
        # per-message truncation.  The upstream
        # ``_prune_chat_messages_for_summary`` (MD5 dedup + 1-line summaries
        # for old middle messages) is the place where total budget gets
        # enforced; if that pass already ran and content is still over
        # ``target_chars``, the right action is to send it anyway and rely
        # on the model's full input window — modern models accept well
        # above ``usable_context × 0.5`` for a single user message.
        text = _serialize_messages_per_truncate(chat_messages)
    else:
        # Legacy path retained for callers that only have a pre-joined string.
        # Without per-message structure we have no choice but to tail-cut.
        text = conversation_text
        if len(text) > target_chars:
            text = "…(earlier conversation truncated)…\n\n" + text[-target_chars:]

    effective_prompt = build_iterative_prompt(prompt_text, previous_summary)
    request = f"{text}\n\n---\n\n{effective_prompt}{_build_focus_block(focus_instruction)}"
    try:
        response = await _llm_chat_with_timeout(
            provider_client,
            model_id=model_id,
            messages=[ChatMessage(role="user", content=request)],
            max_tokens=max_tokens,
            session_id=session_id,
            purpose="compaction_summary",
        )
    except asyncio.TimeoutError:
        log.error("compaction.single_pass.timeout", {
            "timeout_seconds": COMPACTION_TIMEOUT_SECONDS,
            "text_length": len(text),
        })
        return None

    if not response or not response.content:
        return None

    summary = response.content
    passed, missing = validate_summary_quality(summary)
    if not passed:
        log.warn("compaction.single_pass.quality_failed", {
            "missing_sections": missing,
            "summary_length": len(summary),
        })
    return summary


async def _safe_emit(
    callback: Optional[ProgressCallback],
    stage: str,
    data: Dict[str, Any],
) -> None:
    """Best-effort progress emit (mirrors ``compaction._emit_progress``).

    Defined locally so this module does not depend on ``compaction.py``
    (one-way import contract).  Any sink exception is logged WARN and
    swallowed: progress is observability, never a correctness contract.
    """
    if callback is None:
        return
    try:
        await callback(stage, data)
    except Exception as exc:
        log.warn("compaction.progress.emit_error", {
            "stage": stage,
            "error": str(exc),
        })


# ===========================================================================
# LEGACY chunked-summarisation path — NOT on the production hot path
# ===========================================================================
#
# The functions below (``_split_messages_into_text_chunks`` through
# ``summarize_in_stages``) implement the older multi-call chunked +
# three-stage degradation strategy.  ``compaction.process`` no longer
# invokes them (``use_chunked`` is hard-coded to ``False``); they are
# kept in the source tree solely because:
#
#   1. Several integration tests pin their behaviour
#      (``test_compaction_iterative_summary.py``,
#      ``test_compaction_stages_and_history.py``,
#      ``test_compaction_flush_dispatch.py``).
#   2. They remain a viable opt-in fallback if a future model with
#      very small input window ever needs sub-conversation chunking.
#
# Do NOT add new call sites here.  All production summarisation must go
# through ``summarize_single_pass`` above, which mirrors hermes-agent's
# single-LLM-call approach (see ``docs/design/context-compaction-v2.md``).
# ===========================================================================


# ---------------------------------------------------------------------------
# Internal: text-chunk splitter shared by parallel + iterative paths
# ---------------------------------------------------------------------------
def _split_messages_into_text_chunks(
    chat_messages: list,
    split_at: int,
) -> list[str]:
    """Group consecutive messages into text chunks ≤ ``split_at`` chars.

    Single messages larger than the cap are still emitted as their own
    chunk (the per-chunk truncation logic at call sites handles them).
    """
    chunks: list[str] = []
    current_chunk: list[str] = []
    current_len = 0
    for msg in chat_messages:
        role = msg.role if hasattr(msg, 'role') else 'unknown'
        content = msg.content if hasattr(msg, 'content') else ''
        line = f"[{role}]: {content}"
        line_len = len(line)
        if current_len + line_len > split_at and current_chunk:
            chunks.append("\n\n".join(current_chunk))
            current_chunk = []
            current_len = 0
        current_chunk.append(line)
        current_len += line_len
    if current_chunk:
        chunks.append("\n\n".join(current_chunk))
    return chunks


def _build_iterative_chunk_prompt(
    chunk_text: str,
    running_summary: str,
    *,
    is_first: bool,
    is_last: bool,
    final_prompt: str,
    focus_instruction: Optional[str],
) -> str:
    """Render the per-chunk prompt for ``summarize_chunked_iterative``.

    * First chunk with no running summary → ask the model to write a draft
      summary in the standard structured format.
    * Subsequent chunks → reuse the iterative prompt with ``running_summary``
      as the authoritative prior context that must be updated.
    * Last chunk → also append the *final* structural prompt + focus block
      so the trailing call enforces the final-summary schema.
    """
    focus_block = _build_focus_block(focus_instruction)
    if running_summary:
        base = DEFAULT_COMPACTION_PROMPT_WITH_PREVIOUS.format(
            previous_summary=running_summary.strip(),
        )
    else:
        intro = (
            "Summarize the following conversation segment into a structured "
            "compaction summary. Further segments will be appended later, so "
            "preserve all information needed to continue seamlessly."
            if not is_last
            else ""
        )
        # When there is no prior summary AND no further chunks, we want
        # the standard structural prompt (which is what ``final_prompt``
        # already carries).  Otherwise lead with the lightweight intro
        # plus the same structural schema so we never lose section headings.
        base = (intro + "\n\n" + final_prompt).strip() if intro else final_prompt

    # The last chunk should always produce the *final* structured output,
    # so we append the standard structural prompt at the tail when it
    # isn't already there.
    if is_last and final_prompt not in base:
        base = base + "\n\n---\n\n" + final_prompt

    return f"{chunk_text}\n\n---\n\n{base}{focus_block}"


async def summarize_chunked_iterative(
    chat_messages: list,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
    session_id: str,
    chunk_size: Optional[int] = None,
    focus_instruction: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    previous_summary: Optional[str] = None,
) -> Optional[str]:
    """Serial iterative summarisation (E2 — see design doc §E2).

    Each chunk is summarised in sequence on top of the running summary;
    every step's output supersedes the previous one.  Compared to the
    parallel + merge baseline (``summarize_chunked``):

    * **N LLM calls instead of N+1** — no separate merge round-trip.
    * Each call's input is bounded by ``target_chars`` (single chunk +
      one prior summary) rather than ``N × per-chunk summary`` at merge
      time, which on long sessions is the dominant cost.
    * Rate-limit risk drops because calls are serialised — no thundering
      herd against a 5-RPS provider.
    * Quality regression on partial chunk failures is bounded: a failed
      chunk is logged and SKIPPED, ``running_summary`` is not corrupted.

    ``previous_summary`` (E1) is the cross-compaction prior summary; when
    supplied, it seeds ``running_summary`` so the very first chunk is
    already framed as a delta on top of accumulated history.
    """
    from flocks.provider.provider import ChatMessage

    split_at = chunk_size if (chunk_size and chunk_size > 0) else target_chars
    chunks = _split_messages_into_text_chunks(chat_messages, split_at)

    log.info("compaction.iterative.start", {
        "session_id": session_id,
        "num_chunks": len(chunks),
        "total_messages": len(chat_messages),
        "split_at": split_at,
        "has_previous_summary": previous_summary is not None,
    })

    running_summary = (previous_summary or "").strip()

    for idx, chunk_text in enumerate(chunks):
        if len(chunk_text) > target_chars:
            chunk_text = chunk_text[:target_chars] + "\n…(truncated)"

        is_last = idx == len(chunks) - 1
        chunk_prompt = _build_iterative_chunk_prompt(
            chunk_text=chunk_text,
            running_summary=running_summary,
            is_first=idx == 0,
            is_last=is_last,
            final_prompt=prompt_text,
            focus_instruction=focus_instruction,
        )

        # Last chunk → full max_tokens (final structured summary).
        # Intermediate chunks → half the budget (running summary, shorter).
        chunk_max_tokens = max_tokens if is_last else max(1000, max_tokens // 2)

        started = time.perf_counter()
        try:
            resp = await _llm_chat_with_timeout(
                provider_client,
                model_id=model_id,
                messages=[ChatMessage(role="user", content=chunk_prompt)],
                max_tokens=chunk_max_tokens,
                timeout=COMPACTION_TIMEOUT_SECONDS,
                session_id=session_id,
                purpose="compaction_summary_chunk",
            )
            duration_ms = (time.perf_counter() - started) * 1000
            if resp and resp.content:
                running_summary = resp.content
                log.info("compaction.iterative.chunk_completed", {
                    "session_id": session_id,
                    "chunk": idx,
                    "total": len(chunks),
                    "duration_ms": round(duration_ms, 2),
                    "chunk_chars": len(chunk_text),
                    "summary_chars": len(running_summary),
                    "is_last": is_last,
                })
                await _safe_emit(progress_callback, "chunk_done", {
                    "chunk": idx,
                    "total": len(chunks),
                    "duration_ms": round(duration_ms, 2),
                    "ok": True,
                })
            else:
                log.warn("compaction.iterative.chunk_empty", {
                    "session_id": session_id,
                    "chunk": idx,
                    "duration_ms": round(duration_ms, 2),
                })
                await _safe_emit(progress_callback, "chunk_done", {
                    "chunk": idx,
                    "total": len(chunks),
                    "duration_ms": round(duration_ms, 2),
                    "ok": False,
                    "reason": "empty_response",
                })
                # Keep running_summary intact and continue to next chunk.
        except asyncio.TimeoutError:
            duration_ms = (time.perf_counter() - started) * 1000
            log.warn("compaction.iterative.chunk_timeout", {
                "session_id": session_id,
                "chunk": idx,
                "timeout": COMPACTION_TIMEOUT_SECONDS,
                "duration_ms": round(duration_ms, 2),
            })
            await _safe_emit(progress_callback, "chunk_done", {
                "chunk": idx,
                "total": len(chunks),
                "duration_ms": round(duration_ms, 2),
                "ok": False,
                "reason": "timeout",
            })
        except Exception as e:
            duration_ms = (time.perf_counter() - started) * 1000
            err_text = str(e)
            if len(err_text) > 200:
                err_text = err_text[:200] + "…(truncated)"
            log.warn("compaction.iterative.chunk_error", {
                "session_id": session_id,
                "chunk": idx,
                "duration_ms": round(duration_ms, 2),
                "error": err_text,
            })
            await _safe_emit(progress_callback, "chunk_done", {
                "chunk": idx,
                "total": len(chunks),
                "duration_ms": round(duration_ms, 2),
                "ok": False,
                "reason": "error",
            })

    if not running_summary:
        log.warn("compaction.iterative.empty_result", {
            "session_id": session_id,
            "num_chunks": len(chunks),
        })
        return None

    passed, missing = validate_summary_quality(running_summary)
    if not passed:
        log.warn("compaction.iterative.quality_failed", {
            "session_id": session_id,
            "missing_sections": missing,
            "summary_chars": len(running_summary),
        })

    return running_summary


async def summarize_chunked(
    chat_messages: list,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
    session_id: str,
    chunk_size: Optional[int] = None,
    focus_instruction: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
    previous_summary: Optional[str] = None,
) -> Optional[str]:
    """Backwards-compatible alias for :func:`summarize_chunked_iterative`.

    The legacy parallel-then-merge implementation has been retired in
    favour of serial iterative summarisation (see ``E2`` in
    ``docs/design/context-compaction-v2.md``).  This thin wrapper
    forwards everything to the iterative routine so external callers
    keep working without touching their imports.
    """
    return await summarize_chunked_iterative(
        chat_messages,
        prompt_text,
        target_chars,
        provider_client,
        model_id,
        max_tokens,
        session_id,
        chunk_size=chunk_size,
        focus_instruction=focus_instruction,
        progress_callback=progress_callback,
        previous_summary=previous_summary,
    )


# ---------------------------------------------------------------------------
# E3 — Three-stage degradation entrypoint
# ---------------------------------------------------------------------------
#
# When a session contains a single tool result so large that it alone
# exceeds the context window, the iterative path's per-chunk truncation
# can still produce an invalid prompt (the single oversize message lives
# inside one chunk and gets truncated mid-JSON).  ``summarize_in_stages``
# wraps the iterative call with two recovery stages so the caller is
# guaranteed a useful summary string regardless of provider weather:
#
#   Stage 1 — full iterative summarisation;
#   Stage 2 — partition out OVERSIZED messages, summarise the rest, then
#             append a placeholder describing what was skipped;
#   Stage 3 — deterministic fallback assembled from the latest messages.
# ---------------------------------------------------------------------------

# Heuristic: a single message whose content exceeds this many chars is
# considered "oversized" for Stage-2 purposes (≈ 40K tokens at 4 chars/token).
# Tunable via ``FLOCKS_COMPACTION_OVERSIZE_CHARS`` env override.
_DEFAULT_OVERSIZE_CHAR_THRESHOLD = 160_000


def _oversize_char_threshold() -> int:
    import os
    raw = os.getenv("FLOCKS_COMPACTION_OVERSIZE_CHARS")
    if not raw:
        return _DEFAULT_OVERSIZE_CHAR_THRESHOLD
    try:
        value = int(raw)
        return value if value > 0 else _DEFAULT_OVERSIZE_CHAR_THRESHOLD
    except (TypeError, ValueError):
        return _DEFAULT_OVERSIZE_CHAR_THRESHOLD


def _partition_oversized(
    chat_messages: list,
    threshold_chars: int,
) -> tuple[list, list]:
    """Split ``chat_messages`` into (safe, oversized).

    Oversized messages keep their relative order; safe messages also keep
    their relative order so the iterative summariser still sees a coherent
    chronology.
    """
    safe: list = []
    oversized: list = []
    for msg in chat_messages:
        content = msg.content if hasattr(msg, 'content') else ''
        if not isinstance(content, str):
            content = str(content)
        if len(content) >= threshold_chars:
            oversized.append(msg)
        else:
            safe.append(msg)
    return safe, oversized


def _build_oversized_placeholder_section(oversized: list) -> str:
    """Compose a stable, structured placeholder for messages we dropped."""
    if not oversized:
        return ""
    bullets: list[str] = []
    for msg in oversized:
        role = msg.role if hasattr(msg, 'role') else 'unknown'
        content = msg.content if hasattr(msg, 'content') else ''
        if not isinstance(content, str):
            content = str(content)
        head = content[:200].replace("\n", " ")
        bullets.append(f"- [{role}] {len(content):,} chars — preview: {head}…")
    return (
        "## Oversized Items Skipped\n"
        "The following messages were too large to include in the summary "
        "and were elided. The agent may need to re-issue the underlying "
        "tool call(s) on demand:\n"
        + "\n".join(bullets)
    )


async def summarize_in_stages(
    chat_messages: list,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
    session_id: str,
    *,
    chunk_size: Optional[int] = None,
    focus_instruction: Optional[str] = None,
    previous_summary: Optional[str] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> str:
    """Three-stage degradation: full → oversized-skip → deterministic.

    Always returns a usable summary string — the caller never has to
    handle ``None`` again.  Use this in place of direct
    ``summarize_chunked_iterative`` invocations from the orchestrator.
    """
    # ----- Stage 1: full iterative path --------------------------------
    try:
        result = await summarize_chunked_iterative(
            chat_messages, prompt_text, target_chars,
            provider_client, model_id, max_tokens, session_id,
            chunk_size=chunk_size,
            focus_instruction=focus_instruction,
            progress_callback=progress_callback,
            previous_summary=previous_summary,
        )
        if result:
            passed, missing = validate_summary_quality(result)
            if passed:
                return result
            log.warn("compaction.stage1_failed_quality", {
                "session_id": session_id,
                "missing_sections": missing,
                "summary_chars": len(result),
            })
            # Quality failed but we still have content — usable, but try
            # Stage 2 if oversized items might be polluting structure.
            stage1_fallback = result
        else:
            log.warn("compaction.stage1_empty", {"session_id": session_id})
            stage1_fallback = None
    except Exception as exc:
        log.warn("compaction.stage1_failed_exception", {
            "session_id": session_id, "error": str(exc),
        })
        stage1_fallback = None

    # ----- Stage 2: skip oversize messages -----------------------------
    safe_messages, oversized = _partition_oversized(
        chat_messages, _oversize_char_threshold(),
    )
    if oversized and safe_messages:
        log.info("compaction.stage2_oversized_partition", {
            "session_id": session_id,
            "oversized_count": len(oversized),
            "safe_count": len(safe_messages),
        })
        try:
            partial = await summarize_chunked_iterative(
                safe_messages, prompt_text, target_chars,
                provider_client, model_id, max_tokens, session_id,
                chunk_size=chunk_size,
                focus_instruction=focus_instruction,
                progress_callback=progress_callback,
                previous_summary=previous_summary,
            )
            if partial:
                placeholder = _build_oversized_placeholder_section(oversized)
                return partial + ("\n\n" + placeholder if placeholder else "")
        except Exception as exc:
            log.warn("compaction.stage2_failed_exception", {
                "session_id": session_id, "error": str(exc),
            })

    # Stage 1 produced *something* (just not schema-valid) — prefer it
    # over the deterministic fallback so we don't throw away an LLM
    # round-trip's worth of context.
    if stage1_fallback:
        return stage1_fallback

    # ----- Stage 3: deterministic fallback -----------------------------
    log.error("compaction.all_stages_failed", {"session_id": session_id})
    return build_fallback_summary(chat_messages)
