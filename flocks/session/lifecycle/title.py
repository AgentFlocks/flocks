"""
Session Title generation module

Automatically generates titles for sessions using LLM after the first user message.
Based on Flocks' ported src/session/title.ts
"""

from typing import Any, Awaitable, Callable, Dict, List, Optional
import asyncio
import json
import re

from flocks.utils.log import Log
from flocks.provider.provider import ChatMessage
from flocks.session.llm_hook_utils import (
    apply_hook_request_output,
    restore_text_with_replacements,
    serialize_chat_message,
    stream_text_replacements_from_hook_output,
)

EventPublishCallback = Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]]

log = Log.create(service="session.title")


# Reuse the canonical title prompt from prompt_strings (single source of truth)
from flocks.session.prompt_strings import PROMPT_TITLE as _CANONICAL_TITLE_PROMPT


class SessionTitle:
    """Session title generation"""

    _TOOL_CALL_TITLE_PATTERNS = (
        re.compile(r"^\s*\[TOOL_CALL\]", re.IGNORECASE),
        re.compile(r"^\s*<tool[_ -]?call", re.IGNORECASE),
        re.compile(r"\btool\s*=>", re.IGNORECASE),
        re.compile(r"\bargs\s*=>", re.IGNORECASE),
        re.compile(r"^\s*\{?\s*(tool|name)\s*=>", re.IGNORECASE),
    )
    
    @classmethod
    async def generate_title_after_first_message(
        cls,
        session_id: str,
        model_id: str,
        provider_id: str,
        event_publish_callback: EventPublishCallback = None,
    ) -> Optional[str]:
        """
        Generate session title using LLM after the first user message
        
        Triggers when exactly 1 user message exists and the session has no
        meaningful title yet. Uses the first user message to generate a concise
        title via LLM, with a simple text-based fallback.
        
        Args:
            session_id: Session ID
            model_id: Model to use for generation
            provider_id: Provider to use for generation
            
        Returns:
            Generated title or None
        """
        try:
            from flocks.session.session import Session
            from flocks.session.message import Message
            from flocks.provider.provider import Provider
            
            log.info("title.generation.start", {
                "session_id": session_id,
                "model_id": model_id,
                "provider_id": provider_id,
            })
            
            # Check if session already has a non-default title
            session = await Session.get_by_id(session_id)
            if not session:
                log.warn("title.session_not_found", {"session_id": session_id})
                return None
            
            _DEFAULT_TITLES = {
                "new chat", "new session", "新对话", "新会话", "untitled",
            }
            
            log.debug("title.session_info", {
                "session_id": session_id,
                "current_title": session.title,
                "is_default": (not session.title) or session.title.lower().strip() in _DEFAULT_TITLES or Session.is_default_title(session.title),
            })
            
            # Skip if already has a meaningful title (not default placeholder or auto-generated timestamp title)
            if session.title and session.title.lower().strip() not in _DEFAULT_TITLES and not Session.is_default_title(session.title):
                log.debug("title.already_exists", {"session_id": session_id, "title": session.title})
                return session.title
            
            # Get all messages (use async version)
            messages = await Message.list(session_id)
            
            # Count user messages
            user_messages = [m for m in messages if str(m.role) == "user"]
            
            # Only generate title after first user message (exactly 1 user message)
            if len(user_messages) != 1:
                log.debug("title.not_first_message", {
                    "session_id": session_id,
                    "user_message_count": len(user_messages)
                })
                return None
            
            # Get the first user message content
            first_user_msg = user_messages[0]
            parts = await Message.parts(first_user_msg.id, session_id)
            question = ""
            for part in parts:
                if hasattr(part, 'type') and part.type == "text" and hasattr(part, 'text'):
                    question = part.text[:300]  # Truncate long messages
                    break
            
            if not question:
                log.warn("title.no_question", {"session_id": session_id})
                return None
            
            # Call LLM to generate title
            Provider._ensure_initialized()
            provider = Provider.get(provider_id)
            
            if not provider:
                log.warn("title.provider_not_found", {"provider_id": provider_id})
                return cls._generate_simple_title(question)
            
            log.info("title.generating", {
                "session_id": session_id,
                "provider_id": provider_id,
                "model_id": model_id,
            })
            
            # Send PROMPT_TITLE as system instruction, user question as user message
            title = ""
            try:
                title_messages = [
                    ChatMessage(role="system", content=_CANONICAL_TITLE_PROMPT),
                    ChatMessage(role="user", content=question),
                ]
                provider_options: Dict[str, Any] = {"max_tokens": 50}
                replacements: list[tuple[str, str]] = []
                try:
                    from flocks.hooks.pipeline import HookPipeline, HookStage

                    llm_hook_metadata = {
                        "sessionID": session_id,
                        "agent": "session.title",
                        "step": None,
                        "model": {
                            "providerID": provider_id,
                            "modelID": model_id,
                        },
                        "purpose": "title_generation",
                    }
                    if await HookPipeline.has_stage_handlers(HookStage.LLM_BEFORE, llm_hook_metadata):
                        llm_before_ctx = await HookPipeline.run_llm_before({
                            **llm_hook_metadata,
                            "request": {
                                "messageCount": len(title_messages),
                                "messages": [serialize_chat_message(message) for message in title_messages],
                                "toolCount": 0,
                                "tools": [],
                                "providerOptions": dict(provider_options),
                                "providerToolsEnabled": False,
                            },
                        })
                        title_messages, provider_options = apply_hook_request_output(
                            title_messages,
                            provider_options,
                            llm_before_ctx.output or {},
                        )
                        replacements = stream_text_replacements_from_hook_output(llm_before_ctx.output or {})
                except Exception as hook_err:
                    log.debug("title.llm_before_hook.error", {
                        "session_id": session_id,
                        "error": str(hook_err),
                    })

                provider_options.setdefault("max_tokens", 50)
                async for chunk in provider.chat_stream(
                    model_id,
                    title_messages,
                    **provider_options,
                ):
                    if hasattr(chunk, 'delta') and chunk.delta:
                        title += chunk.delta
                if replacements:
                    title = restore_text_with_replacements(title, replacements)
            except Exception as llm_err:
                log.warn("title.llm_failed", {
                    "session_id": session_id,
                    "error": str(llm_err),
                })
            
            title = cls._sanitize_generated_title(title)

            if not title:
                title = cls._generate_simple_title(question)
            
            # Update session with title
            await Session.update(session.project_id, session_id, title=title)
            
            log.info("title.generated", {
                "session_id": session_id,
                "title": title,
            })
            
            # Publish event for frontend to update (via injected callback)
            if event_publish_callback:
                await event_publish_callback("session.updated", {
                    "id": session_id,
                    "title": title,
                })
            
            return title
            
        except Exception as e:
            log.error("title.generation_error", {
                "error": str(e),
                "session_id": session_id,
            })
            return None
    
    @classmethod
    async def ensure_title(
        cls,
        session_id: str,
        model_id: str,
        provider_id: str,
        messages: List,
        event_publish_callback: EventPublishCallback = None,
    ) -> Optional[str]:
        """
        Ensure session has a title, generate if needed (legacy method)
        
        Args:
            session_id: Session ID
            model_id: Model to use for generation
            provider_id: Provider to use for generation
            messages: Session messages
            event_publish_callback: Optional SSE event publisher
            
        Returns:
            Generated title or None
        """
        return await cls.generate_title_after_first_message(
            session_id, model_id, provider_id,
            event_publish_callback=event_publish_callback,
        )
    
    @staticmethod
    def _generate_simple_title(text: str, max_length: int = 50) -> str:
        """
        Generate a simple title from text (fallback)
        
        Args:
            text: Input text
            max_length: Maximum title length
            
        Returns:
            Generated title
        """
        # Clean up text
        text = text.strip()
        
        # Remove "User: " prefix if present
        if text.startswith("User: "):
            text = text[6:]
        
        # Take first line or sentence
        lines = text.split('\n')
        first_line = lines[0].strip()
        
        # Truncate if too long
        if len(first_line) > max_length:
            title = first_line[:max_length - 3] + "..."
        else:
            title = first_line
        
        # Fallback to "New Chat" if empty
        if not title:
            title = "New Chat"
        
        return title

    @classmethod
    def _sanitize_generated_title(cls, title: str, max_length: int = 50) -> str:
        """Clean and validate a model-generated title candidate."""
        title = title.strip().strip('"').strip("'").strip()
        if not title:
            return ""

        if cls._looks_like_tool_call_title(title):
            log.warn("title.rejected_tool_call_candidate", {
                "candidate": title[:120],
            })
            return ""

        if len(title) > max_length:
            title = title[:max_length - 3] + "..."
        return title

    @classmethod
    def _looks_like_tool_call_title(cls, title: str) -> bool:
        """Return True when a title candidate is actually a tool-call payload."""
        candidate = title.strip()
        if not candidate:
            return False

        for pattern in cls._TOOL_CALL_TITLE_PATTERNS:
            if pattern.search(candidate):
                return True

        json_candidate = cls._strip_code_fence(candidate)
        if not json_candidate.startswith(("{", "[")):
            return False

        try:
            parsed = json.loads(json_candidate)
        except (TypeError, ValueError):
            return False

        return cls._json_has_tool_call_shape(parsed)

    @staticmethod
    def _strip_code_fence(text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("```"):
            return stripped

        lines = stripped.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return stripped

    @classmethod
    def _json_has_tool_call_shape(cls, value: Any) -> bool:
        if isinstance(value, dict):
            keys = {str(key).lower() for key in value.keys()}
            if {"tool", "args"} <= keys:
                return True
            if {"name", "arguments"} <= keys:
                return True
            if "function" in keys and ("arguments" in keys or "name" in keys):
                return True
            return any(cls._json_has_tool_call_shape(item) for item in value.values())

        if isinstance(value, list):
            return any(cls._json_has_tool_call_shape(item) for item in value)

        return False
