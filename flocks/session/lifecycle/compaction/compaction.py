"""SessionCompaction — context overflow orchestrator.

Coordinates overflow detection, pruning, summarization, and memory flush
to manage context window limits.  Delegates heavy lifting to sibling modules.
"""

from __future__ import annotations

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
            if total_chars <= target_chars * 2:
                summary_text = await summary.summarize_single_pass(
                    conversation_text, prompt_text, target_chars,
                    provider_client, model_id, effective_summary_tokens,
                )
            else:
                summary_text = await summary.summarize_chunked(
                    chat_messages, prompt_text, target_chars,
                    provider_client, model_id, effective_summary_tokens,
                    session_id,
                )

            log.info("compaction.process.complete", {
                "session_id": session_id,
                "summary_length": len(summary_text) if summary_text else 0,
                "summary_max_tokens": effective_summary_tokens,
                "chunked": total_chars > target_chars * 2,
            })

            if not summary_text:
                log.warn("compaction.process.empty_summary_fallback", {"session_id": session_id})
                summary_text = summary.build_fallback_summary(chat_messages)

            # Memory flush
            await cls._flush_memory_to_daily(
                session_id=session_id,
                summary=summary_text,
                chat_messages=chat_messages,
                model_id=model_id,
                provider=provider_client,
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
