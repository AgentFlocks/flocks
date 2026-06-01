"""Compaction pruning — tool output trimming and post-archival validation."""

from __future__ import annotations

import time
from typing import Optional, Any

from flocks.utils.log import Log
from flocks.session.prompt import SessionPrompt
from .policy import CompactionPolicy
from .models import (
    PRUNE_PROTECT,
    PRUNE_MINIMUM,
    resolve_tool_preserve_turns,
)

log = Log.create(service="session.compaction.pruning")


async def prune(
    session_id: str,
    prune_disabled: bool = False,
    policy: Optional[CompactionPolicy] = None,
) -> None:
    """Prune old tool call outputs from session messages.

    Goes backwards through messages, keeping recent tool calls intact
    but marking older tool calls as compacted to save tokens.
    """
    if prune_disabled:
        return

    effective_prune_protect = policy.prune_protect if policy else PRUNE_PROTECT
    effective_prune_minimum = policy.prune_minimum if policy else PRUNE_MINIMUM

    log.info("compaction.pruning", {
        "session_id": session_id,
        "prune_protect": effective_prune_protect,
        "prune_minimum": effective_prune_minimum,
        "tier": policy.tier.value if policy else "legacy",
    })

    try:
        from flocks.session.message import Message
    except ImportError:
        log.warn("compaction.prune.import_error", {"session_id": session_id})
        return

    messages = await Message.list(session_id)
    if not messages:
        return

    # ------------------------------------------------------------------
    # Retention model (T1 + T2)
    # ------------------------------------------------------------------
    # ``user_turns_seen`` counts user prompts (during reverse walk), and
    # ``resolve_tool_preserve_turns`` gives each tool an individual
    # retention window.  ``-1`` keeps a tool forever; ``0`` drops on the
    # same turn; ``1`` keeps just the in-progress turn.
    #
    # The per-tool policy does the heavy lifting, so we no longer apply
    # the global ``prune_protect`` token gate (it would otherwise spare
    # many small tool outputs that individually expired ages ago — e.g.
    # 20 × bash @ 1K each never trips a 40K threshold).
    # ------------------------------------------------------------------

    total = 0
    pruned = 0
    to_prune = []
    user_turns_seen = 0
    hit_compacted = False

    for msg in reversed(messages):
        if hit_compacted:
            break

        role = msg.role.value if hasattr(msg.role, 'value') else msg.role

        if role == "user":
            user_turns_seen += 1

        if hasattr(msg, "metadata") and msg.metadata.get("summary"):
            break

        msg_parts = await Message.parts(msg.id, session_id)
        for part in reversed(msg_parts):
            if part.type == "tool":
                state = getattr(part, 'state', None)
                if state is None:
                    continue
                status = getattr(state, 'status', None)
                if status == "completed":
                    tool_name = getattr(part, 'tool', "")
                    tool_keep_turns = resolve_tool_preserve_turns(tool_name)
                    if tool_keep_turns == -1:
                        continue   # never prune
                    # ``user_turns_seen`` counts how many ``user`` messages
                    # we have walked past during the reverse iteration.
                    # ``< keep`` keeps exactly the last ``keep`` user-turn
                    # windows (keep=1 → only the in-progress turn; keep=2
                    # → current + previous turn).
                    if user_turns_seen < tool_keep_turns:
                        continue

                    time_info = getattr(state, 'time', None) or {}
                    if isinstance(time_info, dict) and time_info.get("compacted"):
                        hit_compacted = True
                        break

                    output = getattr(state, 'output', "")
                    if not isinstance(output, str):
                        import json as _json
                        output = _json.dumps(output, ensure_ascii=False)
                    estimate = SessionPrompt.estimate_tokens(output)
                    total += estimate

                    # The per-tool retention window already screened out
                    # "still useful" outputs above, so prune unconditionally.
                    pruned += estimate
                    to_prune.append(part)

    log.info("compaction.prune.found", {
        "pruned": pruned,
        "total": total,
        "user_turns_seen": user_turns_seen,
    })

    if pruned > effective_prune_minimum:
        current_time = int(time.time() * 1000)
        affected_msg_ids: set[str] = set()
        for part in to_prune:
            state = getattr(part, 'state', None)
            if state and getattr(state, 'status', None) == "completed":
                time_dict = getattr(state, 'time', None)
                if time_dict is None:
                    time_dict = {}
                    state.time = time_dict
                time_dict["compacted"] = current_time
                mid = getattr(part, 'messageID', None)
                if mid:
                    affected_msg_ids.add(mid)

        try:
            for mid in affected_msg_ids:
                await Message._persist_parts(session_id, message_id=mid)
            if not affected_msg_ids:
                await Message._persist_parts(session_id)
        except Exception as persist_err:
            log.warn("compaction.prune.persist_error", {"error": str(persist_err)})

        # E6: per-message token cache is no longer valid for any message
        # whose tool parts we just compacted — drop those entries so the
        # next ``estimate_full_context_tokens`` re-counts them with the
        # ~10 token placeholder instead of the original output.
        if affected_msg_ids:
            try:
                SessionPrompt.invalidate_message_cache(affected_msg_ids)
            except Exception:  # noqa: BLE001 — cache invalidation never blocks
                pass

        log.info("compaction.pruned", {"count": len(to_prune)})


async def validate_preserved_messages(
    session_id: str,
    preserved: list,
) -> None:
    """Ensure preserved messages have valid tool_call/tool_result pairing.

    After archiving older messages, the first few preserved assistant
    messages may reference context that no longer exists.  This method:
    1. Marks un-compacted tool parts on boundary assistant messages as
       compacted so stale output is replaced with a placeholder.
    2. Repairs tool_use / tool_result pairing — removes orphan tool_result
       parts whose corresponding tool_use was archived, preventing API
       errors with providers like Anthropic that require strict pairing.
    """
    from flocks.session.message import Message
    import time as _time

    if not preserved:
        return

    now_ms = int(_time.time() * 1000)

    # ---- Phase 1: Repair tool_use / tool_result pairing ----
    await _repair_tool_pairing(session_id, preserved)

    # ---- Phase 2: Compact boundary assistant tool outputs ----
    seen_user = False
    boundary_assistant_ids: list[str] = []

    for msg in preserved:
        role = msg.role.value if hasattr(msg.role, 'value') else msg.role
        if role == "user":
            seen_user = True
        if role == "assistant":
            finish = getattr(msg, 'finish', None)
            if finish == "summary":
                continue
            if not seen_user:
                boundary_assistant_ids.append(msg.id)
            else:
                break

    if not boundary_assistant_ids:
        for msg in preserved:
            role = msg.role.value if hasattr(msg.role, 'value') else msg.role
            if role != "assistant":
                continue
            finish = getattr(msg, 'finish', None)
            if finish == "summary":
                continue
            boundary_assistant_ids.append(msg.id)
            break

    for mid in boundary_assistant_ids:
        try:
            parts = await Message.parts(mid, session_id)
            compacted_count = 0
            for part in parts:
                if part.type != "tool":
                    continue
                state = getattr(part, 'state', None)
                if not state:
                    continue
                if getattr(state, 'status', None) != "completed":
                    continue
                time_info = getattr(state, 'time', None)
                if isinstance(time_info, dict) and time_info.get("compacted"):
                    continue
                if time_info is None:
                    time_info = {}
                    state.time = time_info
                time_info["compacted"] = now_ms
                compacted_count += 1
            if compacted_count:
                try:
                    await Message._persist_parts(session_id, message_id=mid)
                except Exception as pe:
                    log.warn("compaction.boundary_persist_error", {"error": str(pe)})
                log.info("compaction.boundary_tools_compacted", {
                    "session_id": session_id,
                    "message_id": mid,
                    "count": compacted_count,
                })
        except Exception as e:
            log.warn("compaction.validate_preserved_error", {
                "session_id": session_id,
                "message_id": mid,
                "error": str(e),
            })


async def _repair_tool_pairing(
    session_id: str,
    preserved: list,
) -> None:
    """Compact orphan tool parts whose pairing context was archived.

    In flocks, each ToolPart lives on an assistant message and carries both
    ``state.input`` (the call) and ``state.output`` (the result) with a
    ``callID`` that the LLM uses for pairing.  After archiving, the first
    preserved assistant message may contain tool parts whose callID was
    referenced in conversation that is now gone.  We mark those completed
    tool outputs as compacted so the LLM sees a placeholder instead of
    stale context.

    This is already partly handled by the boundary-assistant logic in
    ``validate_preserved_messages`` phase 2, but this function additionally
    checks for cross-message callID references that may be orphaned when
    earlier user messages referencing a callID were archived.
    """
    from flocks.session.message import Message
    import time as _time

    now_ms = int(_time.time() * 1000)

    # Build a set of callIDs mentioned in preserved user message content
    # (some providers embed tool_use_id references in user messages).
    user_referenced_call_ids: set[str] = set()
    assistant_call_ids: set[str] = set()

    for msg in preserved:
        role = msg.role.value if hasattr(msg.role, "value") else msg.role
        parts = await Message.parts(msg.id, session_id)

        for part in parts:
            if part.type != "tool":
                continue
            call_id = getattr(part, "callID", None)
            if not call_id:
                continue
            if role == "assistant":
                assistant_call_ids.add(call_id)
            else:
                user_referenced_call_ids.add(call_id)

    # Orphan = referenced in a user message but the assistant tool part is gone
    orphan_ids = user_referenced_call_ids - assistant_call_ids
    if not orphan_ids:
        return

    repaired = 0
    affected_msg_ids: set[str] = set()
    for msg in preserved:
        role = msg.role.value if hasattr(msg.role, "value") else msg.role
        if role == "assistant":
            continue
        parts = await Message.parts(msg.id, session_id)
        for part in parts:
            if part.type != "tool":
                continue
            call_id = getattr(part, "callID", None)
            if call_id and call_id in orphan_ids:
                state = getattr(part, "state", None)
                if state:
                    time_info = getattr(state, "time", None)
                    if time_info is None:
                        time_info = {}
                        state.time = time_info
                    if not (isinstance(time_info, dict) and time_info.get("compacted")):
                        time_info["compacted"] = now_ms
                        repaired += 1
                        affected_msg_ids.add(msg.id)

    for mid in affected_msg_ids:
        try:
            await Message._persist_parts(session_id, message_id=mid)
        except Exception as pe:
            log.warn("compaction.repair_pairing_persist_error", {"error": str(pe)})

    if repaired:
        log.info("compaction.tool_pairing_repaired", {
            "session_id": session_id,
            "orphan_call_ids": len(orphan_ids),
            "parts_compacted": repaired,
        })


async def truncate_oversized_tool_outputs(
    session_id: str,
    context_window_tokens: int,
) -> int:
    """Scan session for oversized tool outputs and truncate them in-place.

    Two-pass strategy:
      Pass 1 — Truncate any single tool output exceeding the per-tool limit.
      Pass 2 — If total tool output chars still exceed a context-window-based
                budget, compact the OLDEST tool results into placeholders.

    Returns the number of tool outputs truncated or compacted.
    """
    from flocks.session.message import Message
    from flocks.tool.truncation import (
        calculate_max_tool_result_chars,
        truncate_tool_result_text_safe,
    )

    # T3: JSON-safe truncation is the default — tool results that happen
    # to be JSON survive as valid JSON; non-JSON inputs fall back to
    # character-level truncation inside ``truncate_tool_result_text_safe``.
    _truncate = truncate_tool_result_text_safe

    max_chars = calculate_max_tool_result_chars(context_window_tokens)
    messages = await Message.list(session_id)
    truncated_count = 0
    affected_msg_ids: set[str] = set()

    all_tool_parts: list[tuple[str, Any]] = []

    for msg in messages:
        role = msg.role.value if hasattr(msg.role, 'value') else msg.role
        if role != "assistant":
            continue

        parts = await Message.parts(msg.id, session_id)
        for part in parts:
            if part.type != "tool":
                continue
            state = getattr(part, 'state', None)
            if not state or getattr(state, 'status', None) != "completed":
                continue
            time_info = getattr(state, 'time', None)
            if isinstance(time_info, dict) and time_info.get("compacted"):
                continue

            output = getattr(state, 'output', '')
            if not isinstance(output, str):
                import json as _json
                try:
                    output = _json.dumps(output, ensure_ascii=False)
                except (TypeError, ValueError):
                    output = str(output)
                state.output = output

            if len(output) > max_chars:
                state.output = _truncate(output, max_chars)
                truncated_count += 1
                affected_msg_ids.add(msg.id)

            all_tool_parts.append((msg.id, part))

    # Pass 2: total budget enforcement
    COMPACT_PLACEHOLDER = "[compacted: tool output removed to free context]"
    total_budget = max(4_096, int(context_window_tokens * 4 * 0.75))

    total_tool_chars = 0
    for _, part in all_tool_parts:
        out = getattr(part.state, 'output', '') or ''
        total_tool_chars += len(out) if isinstance(out, str) else len(str(out))

    if total_tool_chars > total_budget:
        chars_to_free = total_tool_chars - total_budget
        freed = 0
        for mid, part in all_tool_parts:
            if freed >= chars_to_free:
                break
            out = getattr(part.state, 'output', '') or ''
            out_len = len(out) if isinstance(out, str) else len(str(out))
            if out_len <= len(COMPACT_PLACEHOLDER):
                continue
            part.state.output = COMPACT_PLACEHOLDER
            freed += out_len - len(COMPACT_PLACEHOLDER)
            truncated_count += 1
            affected_msg_ids.add(mid)

        log.info("compaction.total_budget_enforced", {
            "session_id": session_id,
            "total_tool_chars": total_tool_chars,
            "budget": total_budget,
            "freed": freed,
        })

    if truncated_count:
        try:
            for mid in affected_msg_ids:
                await Message._persist_parts(session_id, message_id=mid)
        except Exception as persist_err:
            log.warn("compaction.oversized_persist_error", {"error": str(persist_err)})

        # E6: drop cached token totals for any message we just mutated.
        try:
            SessionPrompt.invalidate_message_cache(affected_msg_ids)
        except Exception:  # noqa: BLE001 — cache invalidation never blocks
            pass

        log.info("compaction.oversized_truncated", {
            "session_id": session_id,
            "count": truncated_count,
            "max_chars": max_chars,
            "context_window": context_window_tokens,
        })

    return truncated_count
