"""Compaction summarization strategies — conversation history compression."""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional, Any

from flocks.utils.log import Log
from flocks.session.prompt import SessionPrompt

log = Log.create(service="session.compaction.summarization")

COMPACTION_TIMEOUT_SECONDS = 300

# The merge step in ``summarize_chunked`` is a SHORT prompt (just the N
# chunk summaries) so it should normally complete well under the budget.
# We give it a tighter cap than the per-chunk timeout because if the
# upstream proxy (e.g. threatbook → minimax) is wedged, the
# chunked-fallback is already a perfectly usable summary — there is no
# point waiting the full 5-minute COMPACTION_TIMEOUT_SECONDS only to
# discover the gateway returned 504.  Field data showed merge calls
# hanging for 230s before the proxy gave up; a 120s ceiling still
# accommodates slower models / longer summaries while bailing out well
# before the typical gateway 504 window, and remains tunable per
# deployment via FLOCKS_COMPACTION_MERGE_TIMEOUT.
_DEFAULT_MERGE_TIMEOUT_SECONDS = 120


def _merge_timeout_seconds() -> float:
    raw = os.getenv("FLOCKS_COMPACTION_MERGE_TIMEOUT")
    if raw is None or raw.strip() == "":
        return float(_DEFAULT_MERGE_TIMEOUT_SECONDS)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.warn("compaction.merge_timeout.parse_error", {
            "raw": raw, "fallback": _DEFAULT_MERGE_TIMEOUT_SECONDS,
        })
        return float(_DEFAULT_MERGE_TIMEOUT_SECONDS)
    if value <= 0:
        log.warn("compaction.merge_timeout.non_positive", {
            "raw": raw, "fallback": _DEFAULT_MERGE_TIMEOUT_SECONDS,
        })
        return float(_DEFAULT_MERGE_TIMEOUT_SECONDS)
    return value


# Maximum number of chunk-summary LLM calls to run concurrently in
# ``summarize_chunked``.  Defaults to 4 which is a safe trade-off between
# throughput and provider rate-limits; can be tuned per deployment via the
# ``FLOCKS_COMPACTION_CHUNK_CONCURRENCY`` environment variable.
def _chunk_concurrency_limit() -> int:
    raw = os.getenv("FLOCKS_COMPACTION_CHUNK_CONCURRENCY", "4")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return 4
    return max(1, value)


_REQUIRED_SECTIONS = [
    "## Decisions",
    "## Current Task",
    "## Open TODOs",
    "## Key Files & Identifiers",
]


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
) -> Any:
    """Call provider_client.chat with a timeout guard."""
    return await asyncio.wait_for(
        provider_client.chat(
            model_id=model_id,
            messages=messages,
            max_tokens=max_tokens,
        ),
        timeout=timeout,
    )


async def summarize_single_pass(
    conversation_text: str,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
) -> Optional[str]:
    """Generate summary in a single LLM call (for short conversations)."""
    from flocks.provider.provider import ChatMessage

    text = conversation_text
    if len(text) > target_chars:
        text = "…(earlier conversation truncated)…\n\n" + text[-target_chars:]

    target_tokens = max(500, target_chars // 2)
    for _ in range(5):
        actual = SessionPrompt.count_tokens(text)
        if actual <= target_tokens:
            break
        keep = int(len(text) * 0.8)
        text = "…(earlier conversation truncated)…\n\n" + text[-keep:]

    request = f"{text}\n\n---\n\n{prompt_text}"
    try:
        response = await _llm_chat_with_timeout(
            provider_client,
            model_id=model_id,
            messages=[ChatMessage(role="user", content=request)],
            max_tokens=max_tokens,
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


async def summarize_chunked(
    chat_messages: list,
    prompt_text: str,
    target_chars: int,
    provider_client: Any,
    model_id: str,
    max_tokens: int,
    session_id: str,
    chunk_size: Optional[int] = None,
) -> Optional[str]:
    """Generate summary by chunking a long conversation.

    Splits messages into chunks of at most *chunk_size* characters
    (defaults to *target_chars* for backward compatibility), summarises
    each chunk in parallel, then merges all chunk summaries into a
    final combined summary.

    The split granularity (*chunk_size*) and the per-chunk truncation
    cap (*target_chars*) are intentionally separate so callers can
    request more / smaller chunks (better parallelism) without
    enlarging the per-chunk truncation envelope.
    """
    from flocks.provider.provider import ChatMessage

    split_at = chunk_size if (chunk_size and chunk_size > 0) else target_chars

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

    concurrency = _chunk_concurrency_limit()

    log.info("compaction.chunked_summarize.start", {
        "session_id": session_id,
        "num_chunks": len(chunks),
        "total_messages": len(chat_messages),
        "concurrency": concurrency,
        "split_at": split_at,
    })

    chunk_prompt = (
        "Summarize the following conversation segment concisely. "
        "Focus on: actions taken, decisions made, files modified, "
        "and key results. Keep the same language as the conversation."
    )

    # Per-chunk timeout is now the full compaction timeout — chunks run in
    # parallel and each has independent timing. Previous logic divided the
    # budget across chunks because they ran serially; that gave very little
    # time per chunk when N was large.
    per_chunk_timeout = COMPACTION_TIMEOUT_SECONDS

    semaphore = asyncio.Semaphore(concurrency)

    async def _summarize_one(idx: int, chunk_text: str) -> str:
        if len(chunk_text) > target_chars:
            chunk_text = chunk_text[:target_chars] + "\n…(truncated)"
        async with semaphore:
            # Record per-chunk timing so we can attribute slow chunked
            # summarisation to a specific provider call (vs. the merge
            # step) without having to subtract clocks across log lines.
            started = time.perf_counter()
            try:
                resp = await _llm_chat_with_timeout(
                    provider_client,
                    model_id=model_id,
                    messages=[ChatMessage(
                        role="user",
                        content=f"{chunk_text}\n\n---\n\n{chunk_prompt}",
                    )],
                    max_tokens=max(1000, max_tokens // 2),
                    timeout=per_chunk_timeout,
                )
                duration_ms = (time.perf_counter() - started) * 1000
                if resp and resp.content:
                    log.info("compaction.chunk_summary.completed", {
                        "session_id": session_id,
                        "chunk": idx,
                        "duration_ms": round(duration_ms, 2),
                        "chunk_chars": len(chunk_text),
                        "summary_chars": len(resp.content),
                    })
                    return f"## Part {idx + 1}\n{resp.content}"
                log.warn("compaction.chunk_summary.empty_response", {
                    "session_id": session_id,
                    "chunk": idx,
                    "duration_ms": round(duration_ms, 2),
                })
                return f"## Part {idx + 1}\n{chunk_text[:500]}…"
            except asyncio.TimeoutError:
                duration_ms = (time.perf_counter() - started) * 1000
                log.warn("compaction.chunk_summary_timeout", {
                    "session_id": session_id,
                    "chunk": idx,
                    "timeout": per_chunk_timeout,
                    "duration_ms": round(duration_ms, 2),
                })
                return f"## Part {idx + 1}\n{chunk_text[:500]}…"
            except Exception as e:
                duration_ms = (time.perf_counter() - started) * 1000
                log.warn("compaction.chunk_summary_error", {
                    "session_id": session_id,
                    "chunk": idx,
                    "error": str(e),
                    "duration_ms": round(duration_ms, 2),
                })
                return f"## Part {idx + 1}\n{chunk_text[:500]}…"

    parallel_started = time.perf_counter()
    chunk_summaries = await asyncio.gather(
        *(_summarize_one(i, c) for i, c in enumerate(chunks))
    )
    parallel_duration_ms = (time.perf_counter() - parallel_started) * 1000

    if not chunk_summaries:
        return None

    merged = "\n\n".join(chunk_summaries)

    log.info("compaction.chunked_summarize.parallel_done", {
        "session_id": session_id,
        "num_chunks": len(chunks),
        "parallel_duration_ms": round(parallel_duration_ms, 2),
        "merged_chars": len(merged),
    })

    if len(merged) <= target_chars:
        merge_request = (
            f"The following are summaries of different parts of a "
            f"conversation. Combine them into a single coherent summary.\n\n"
            f"{merged}\n\n---\n\n{prompt_text}"
        )
        merge_timeout = _merge_timeout_seconds()
        merge_started = time.perf_counter()
        try:
            resp = await _llm_chat_with_timeout(
                provider_client,
                model_id=model_id,
                messages=[ChatMessage(role="user", content=merge_request)],
                max_tokens=max_tokens,
                timeout=merge_timeout,
            )
            merge_duration_ms = (time.perf_counter() - merge_started) * 1000
            if resp and resp.content:
                summary = resp.content
                passed, missing = validate_summary_quality(summary)
                if not passed:
                    log.warn("compaction.chunked.quality_failed", {
                        "session_id": session_id,
                        "missing_sections": missing,
                    })
                log.info("compaction.merge_summary.completed", {
                    "session_id": session_id,
                    "duration_ms": round(merge_duration_ms, 2),
                    "summary_chars": len(summary),
                })
                return summary
        except asyncio.TimeoutError:
            merge_duration_ms = (time.perf_counter() - merge_started) * 1000
            log.warn("compaction.merge_summary_timeout", {
                "session_id": session_id,
                "timeout": merge_timeout,
                "duration_ms": round(merge_duration_ms, 2),
            })
        except Exception as e:
            merge_duration_ms = (time.perf_counter() - merge_started) * 1000
            # Many gateways respond with HTML error pages on 5xx; truncate
            # the error string so the log line stays readable.
            err_text = str(e)
            if len(err_text) > 200:
                err_text = err_text[:200] + "…(truncated)"
            log.warn("compaction.merge_summary_error", {
                "session_id": session_id,
                "duration_ms": round(merge_duration_ms, 2),
                "error": err_text,
            })

    return merged
