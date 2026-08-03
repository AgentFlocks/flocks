"""
Memory Flush - Pre-compaction memory save mechanism

Inspired by OpenClaw's memory flush design, triggers automatic memory
saves when the session approaches context limits.

Includes:
  - MemoryFlush: flush threshold logic and trigger helpers
  - extract_and_save: LLM-based memory extraction from conversation history
"""

from typing import Optional, Dict, Any, Callable, TYPE_CHECKING
from datetime import datetime

from flocks.memory.config import MemoryAutoFlushConfig
from flocks.utils.log import Log

if TYPE_CHECKING:
    from flocks.session.lifecycle.compaction import CompactionPolicy

log = Log.create(service="memory.flush")


class MemoryFlush:
    """
    Memory flush mechanism
    
    Triggers pre-compaction memory saves to ensure important information
    is preserved before context window compression.
    """
    
    @staticmethod
    def should_trigger(
        total_tokens: int,
        context_window: int,
        config: MemoryAutoFlushConfig,
        last_flush_compaction: Optional[int] = None,
        current_compaction: int = 0,
        policy: Optional["CompactionPolicy"] = None,
    ) -> bool:
        """
        Check if memory flush should be triggered
        
        When a ``CompactionPolicy`` is provided, uses its dynamically computed
        ``flush_trigger`` and ``flush_reserve`` thresholds instead of the fixed
        values from ``config``.  Otherwise falls back to the original logic.
        
        Args:
            total_tokens: Current total token count
            context_window: Model context window size
            config: Memory flush configuration
            last_flush_compaction: Last compaction count when flush ran
            current_compaction: Current compaction count
            policy: Optional CompactionPolicy for dynamic thresholds
            
        Returns:
            True if memory flush should run
        """
        if not config.enabled:
            return False
        
        if total_tokens <= 0 or context_window <= 0:
            return False
        
        # Resolve trigger/reserve from policy or config
        if policy is not None:
            trigger_tokens = policy.flush_trigger
            reserve_tokens = policy.flush_reserve
        else:
            trigger_tokens = config.trigger_tokens
            reserve_tokens = config.reserve_tokens
        
        # Calculate threshold
        # threshold = context_window - reserve_tokens - trigger_tokens
        threshold = max(
            0,
            context_window - reserve_tokens - trigger_tokens
        )
        
        if threshold <= 0:
            log.warn("flush.invalid_threshold", {
                "context_window": context_window,
                "reserve_tokens": reserve_tokens,
                "trigger_tokens": trigger_tokens,
                "source": "policy" if policy else "config",
            })
            return False
        
        # Check if we've crossed the threshold
        if total_tokens < threshold:
            log.debug("flush.below_threshold", {
                "total_tokens": total_tokens,
                "threshold": threshold,
                "remaining": threshold - total_tokens,
                "source": "policy" if policy else "config",
            })
            return False
        
        # Check if we've already flushed in this compaction cycle
        if last_flush_compaction is not None and last_flush_compaction == current_compaction:
            log.debug("flush.already_flushed", {
                "compaction_count": current_compaction,
            })
            return False
        
        log.info("flush.should_trigger", {
            "total_tokens": total_tokens,
            "threshold": threshold,
            "compaction_count": current_compaction,
            "source": "policy" if policy else "config",
        })
        
        return True
    
    @staticmethod
    def get_flush_prompts(
        config: MemoryAutoFlushConfig,
        today: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        Get memory flush prompts with date filled in
        
        Args:
            config: Memory flush configuration
            today: Today's date (YYYY-MM-DD format)
            
        Returns:
            Dict with system_prompt and user_prompt
        """
        if today is None:
            today = datetime.now().strftime("%Y-%m-%d")
        
        # Replace YYYY-MM-DD with actual date
        system_prompt = config.system_prompt
        user_prompt = config.user_prompt.replace("YYYY-MM-DD", today)
        
        return {
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "date": today,
        }
    
    @staticmethod
    async def trigger_flush(
        session_id: str,
        config: MemoryAutoFlushConfig,
        create_flush_message: callable,
        execute_agent_turn: callable,
    ) -> bool:
        """
        Trigger a memory flush turn
        
        This creates a special agent turn with flush prompts.
        The agent should save important memories before compaction.
        
        Args:
            session_id: Session ID
            config: Memory flush configuration
            create_flush_message: Callback to create flush user message
            execute_agent_turn: Callback to execute agent turn
            
        Returns:
            True if flush succeeded
        """
        log.info("flush.trigger", {
            "session_id": session_id,
        })
        
        try:
            # Get prompts with today's date
            prompts = MemoryFlush.get_flush_prompts(config)
            
            # Create flush user message
            flush_message = await create_flush_message(
                content=prompts["user_prompt"],
                metadata={
                    "memory_flush": True,
                    "date": prompts["date"],
                }
            )
            
            if not flush_message:
                log.error("flush.create_message_failed", {
                    "session_id": session_id,
                })
                return False
            
            # Execute agent turn with flush system prompt
            result = await execute_agent_turn(
                system_prompt_append=prompts["system_prompt"],
                is_memory_flush=True,
            )
            
            if result and result.get("success"):
                log.info("flush.success", {
                    "session_id": session_id,
                })
                return True
            else:
                log.warn("flush.turn_failed", {
                    "session_id": session_id,
                    "result": result,
                })
                return False
        
        except Exception as e:
            log.error("flush.error", {
                "session_id": session_id,
                "error": str(e),
            })
            return False
    
    @staticmethod
    def calculate_threshold(
        context_window: int,
        reserve_tokens: int,
        trigger_tokens: int,
        policy: Optional["CompactionPolicy"] = None,
    ) -> int:
        """
        Calculate memory flush threshold
        
        When a ``CompactionPolicy`` is provided, uses its dynamic values
        instead of the explicit reserve/trigger parameters.
        
        Args:
            context_window: Model context window size
            reserve_tokens: Reserved tokens (ignored when policy is set)
            trigger_tokens: Trigger threshold tokens (ignored when policy is set)
            policy: Optional CompactionPolicy for dynamic thresholds
            
        Returns:
            Threshold token count
        """
        if policy is not None:
            return max(0, context_window - policy.flush_reserve - policy.flush_trigger)
        return max(0, context_window - reserve_tokens - trigger_tokens)
    
    @staticmethod
    def get_stats(
        total_tokens: int,
        context_window: int,
        config: MemoryAutoFlushConfig,
        last_flush_compaction: Optional[int] = None,
        current_compaction: int = 0,
        policy: Optional["CompactionPolicy"] = None,
    ) -> Dict[str, Any]:
        """
        Get memory flush statistics
        
        Args:
            total_tokens: Current total token count
            context_window: Model context window size
            config: Memory flush configuration
            last_flush_compaction: Last flush compaction count
            current_compaction: Current compaction count
            policy: Optional CompactionPolicy for dynamic thresholds
            
        Returns:
            Dict with flush statistics
        """
        threshold = MemoryFlush.calculate_threshold(
            context_window,
            config.reserve_tokens,
            config.trigger_tokens,
            policy=policy,
        )
        
        should_flush = MemoryFlush.should_trigger(
            total_tokens,
            context_window,
            config,
            last_flush_compaction,
            current_compaction,
            policy=policy,
        )
        
        # Report the effective values (from policy or config)
        if policy is not None:
            effective_reserve = policy.flush_reserve
            effective_trigger = policy.flush_trigger
        else:
            effective_reserve = config.reserve_tokens
            effective_trigger = config.trigger_tokens
        
        return {
            "enabled": config.enabled,
            "total_tokens": total_tokens,
            "context_window": context_window,
            "threshold": threshold,
            "remaining_tokens": max(0, threshold - total_tokens),
            "should_flush": should_flush,
            "last_flush_compaction": last_flush_compaction,
            "current_compaction": current_compaction,
            "source": "policy" if policy else "config",
            "config": {
                "reserve_tokens": effective_reserve,
                "trigger_tokens": effective_trigger,
            },
        }


async def extract_and_save(
    session_id: str,
    summary: str,
    chat_messages: list,
    model_id: str,
    provider: Any,
    ChatMessage: Any,
    policy: Optional["CompactionPolicy"] = None,
    count_tokens: Optional[Callable[[str], int]] = None,
) -> None:
    """Extract key memories from the conversation and save to daily file.

    Uses the full conversation history (truncated to fit the model's
    context window) so that the LLM can extract the most accurate
    memories.  Falls back to saving the compaction summary if the
    extraction LLM call fails.

    Args:
        session_id: Session ID.
        summary: Compaction summary (used as fallback content).
        chat_messages: List of ChatMessage-like objects with role/content.
        model_id: Model ID for the extraction LLM call.
        provider: Provider client instance with a ``chat()`` method.
        ChatMessage: ChatMessage class for building requests.
        policy: Optional CompactionPolicy for context budget.
        count_tokens: Optional callable to count tokens in a string.
                      Defaults to a simple ``len(text) // 4`` heuristic.
    """
    try:
        from flocks.memory.daily import DailyMemory
    except ImportError:
        log.warn("extract_and_save.import_error", {"session_id": session_id})
        return

    if count_tokens is None:
        count_tokens = lambda text: len(text) // 4  # noqa: E731

    today = datetime.now().strftime("%Y-%m-%d")
    now_ts = datetime.now().strftime("%H:%M")

    usable = policy.usable_context if policy else 96_000
    reserve_tokens = 2000
    target_tokens = max(1000, usable - reserve_tokens)
    # tokens × 4 matches the chars/4 estimate used elsewhere in the
    # compaction stack.  Previously ``× 2`` silently halved the input
    # budget, forcing tail-cuts on conversations that fit comfortably.
    max_chars = max(3000, target_tokens * 4)

    conversation_parts: list[str] = []
    for msg in chat_messages:
        role = msg.role if hasattr(msg, "role") else "unknown"
        content = msg.content if hasattr(msg, "content") else str(msg)
        if content:
            conversation_parts.append(f"[{role}]: {content}")
    full_text = "\n\n".join(conversation_parts)

    if len(full_text) > max_chars:
        full_text = "…(earlier conversation truncated)…\n\n" + full_text[-max_chars:]

    for _ in range(5):
        actual_tokens = count_tokens(full_text)
        if actual_tokens <= target_tokens:
            break
        keep = int(len(full_text) * 0.8)
        full_text = "…(earlier conversation truncated)…\n\n" + full_text[-keep:]

    log.info("extract_and_save.context", {
        "session_id": session_id,
        "usable_context": usable,
        "target_tokens": target_tokens,
        "max_chars": max_chars,
        "full_text_len": len(full_text),
        "actual_tokens": count_tokens(full_text),
    })

    memory_prompt = (
        "Below is the conversation history of a session:\n\n"
        f"---\n{full_text}\n---\n\n"
        "From this conversation, extract the KEY MEMORIES worth "
        "persisting for future sessions. Focus on:\n"
        "- Important decisions made\n"
        "- Technical facts (APIs, configs, file paths, commands, etc.)\n"
        "- Action items / to-dos\n"
        "- User preferences or corrections\n\n"
        "Output a concise bullet list in Markdown. "
        "Use the same language as the conversation. "
        "Omit trivial greetings or small-talk. "
        "If there is nothing worth remembering, reply with NOTHING."
    )

    memory_text: Optional[str] = None
    try:
        mem_response = await provider.chat(
            model_id=model_id,
            messages=[ChatMessage(role="user", content=memory_prompt)],
            max_tokens=1500,
        )
        if mem_response and mem_response.content:
            content = mem_response.content.strip()
            if content.upper() != "NOTHING":
                memory_text = content
    except Exception as e:
        log.warn("extract_and_save.llm_error", {
            "session_id": session_id,
            "error": str(e),
        })

    if not memory_text:
        memory_text = summary

    daily = DailyMemory()
    header = f"\n## Session {session_id[:16]}… ({today} {now_ts})\n\n"
    content_to_write = header + memory_text + "\n"

    try:
        await daily.write_daily(
            content=content_to_write,
            date=today,
            append=True,
        )
        log.info("extract_and_save.saved", {
            "session_id": session_id,
            "date": today,
            "length": len(content_to_write),
        })
    except Exception as e:
        log.error("extract_and_save.write_error", {
            "session_id": session_id,
            "error": str(e),
        })
