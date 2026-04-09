"""
Session runner module.

Core session execution logic including:
- Session loop (message processing)
- Tool resolution and execution  
- LLM interaction with tool support

Implements session/prompt.ts SessionPrompt namespace pattern.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable, Set, Tuple
from dataclasses import dataclass, field

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.session import Session, SessionInfo
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.prompt import SystemPrompt, SessionPrompt
from flocks.session.core.status import SessionStatus, SessionStatusRetry, SessionStatusBusy
from flocks.session.lifecycle.retry import SessionRetry
from flocks.session.lifecycle.compaction import SessionCompaction, CompactionPolicy
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.session.streaming.stream_events import (
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    FinishEvent,
    ReasoningStartEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
)
from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.agent.registry import Agent
from flocks.agent.agent import AgentInfo
from flocks.agent.toolset import agent_declares_tool
from flocks.provider.provider import Provider, ChatMessage
from flocks.tool.catalog import get_tool_catalog_metadata, list_tool_catalog_infos
from flocks.tool.registry import ToolRegistry, ToolResult
from flocks.utils.langfuse import generation_scope, trace_scope
from flocks.session.utils.file_extractor import (
    read_file_part_bytes,
    is_text_extractable_mime,
    extract_file_text,
)


log = Log.create(service="session.runner")

TOOL_RESULT_CHAR_BUDGET_RATIO = 0.70
TOOL_RESULT_TURN_BUDGET_RATIO = 0.35
TOOL_RESULT_MIN_CHAR_BUDGET = 12_000
TOOL_RESULT_MIN_TURN_BUDGET = 6_000
TOOL_RESULT_PREVIEW_CHARS = 160

# Maximum seconds to wait for each chunk (including the first) from the LLM
# stream.  If the model hangs without returning any data, the stream times out
# and the session surfaces a clear error rather than hanging forever.
LLM_STREAM_CHUNK_TIMEOUT_S = 60


async def _iter_with_chunk_timeout(aiter, timeout_s: float):
    """Yield chunks from an async generator, raising TimeoutError if any
    individual chunk (including the first) takes longer than *timeout_s*."""
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=timeout_s)
                yield chunk
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"LLM stream timed out after {timeout_s:.0f}s with no response. "
                    "The model may be overloaded or incompatible. Please try again or switch models."
                )
    finally:
        try:
            await aiter.aclose()
        except Exception:
            pass


@dataclass
class ToolCall:
    """Tool call from LLM response."""
    id: str
    name: str
    arguments: Dict[str, Any]


from flocks.session.core.defaults import DOOM_LOOP_THRESHOLD


@dataclass
class StepResult:
    """Result of a single processing step."""
    action: str  # "stop", "continue", "compact"
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class RunnerCallbacks:
    """Callbacks for runner events."""
    on_step_start: Optional[Callable[[int], Awaitable[None]]] = None
    on_step_end: Optional[Callable[[int], Awaitable[None]]] = None
    on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_reasoning_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    on_tool_end: Optional[Callable[[str, ToolResult], Awaitable[None]]] = None
    on_permission_request: Optional[Callable[[Any], Awaitable[bool]]] = None
    on_error: Optional[Callable[[str], Awaitable[None]]] = None
    # SSE event publishing callback (for TUI/WebUI real-time updates)
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None


class SessionRunner:
    """
    Core session runner.
    
    Manages the session execution loop:
    1. Get messages from session
    2. Check if LLM response is needed
    3. Call LLM with tools
    4. Execute tool calls
    5. Loop until complete
    
    Implements SessionPrompt.loop()
    """
    
    # Class-level state for active sessions
    _active_sessions: Dict[str, 'SessionRunner'] = {}
    
    def __init__(
        self,
        session: SessionInfo,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[RunnerCallbacks] = None,
        abort_event: Optional[asyncio.Event] = None,
        session_ctx: Optional[Any] = None,  # SessionContext interface
        memory_bootstrap_data: Optional[Dict[str, Any]] = None,
        static_cache: Optional[Dict[str, Any]] = None,
    ):
        self.session = session
        from flocks.session.core.defaults import fallback_provider_id, fallback_model_id
        self.provider_id = provider_id or fallback_provider_id()
        self.model_id = model_id or fallback_model_id()
        self.agent_name = agent_name or "rex"
        self.callbacks = callbacks or RunnerCallbacks()
        self._abort = asyncio.Event()
        self._external_abort = abort_event  # External abort event (e.g. from SessionLoop)
        self._step = 0
        self._recent_tool_calls: List[tuple[str, str]] = []  # Track recent (tool_name, args_json) for doom loop
        self.session_ctx = session_ctx  # SessionContext interface for decoupled access
        self._memory_bootstrap_data: Optional[Dict[str, Any]] = memory_bootstrap_data
        self._static_cache = static_cache if static_cache is not None else {}

    async def _list_callable_tool_infos_for_turn(
        self,
        agent: AgentInfo,
        messages: List[MessageInfo],
    ) -> Tuple[List[Any], Dict[str, Any]]:
        result = await list_session_callable_tool_infos(
            session_id=self.session.id,
            declared_tool_names=getattr(agent, "tools", None),
            step=self._step,
            event_publish_callback=self.callbacks.event_publish_callback,
        )
        return result.tool_infos, dict(result.metadata)

    async def _publish_turn_tools_event(self, selection_metadata: Dict[str, Any]) -> None:
        if not self.callbacks.event_publish_callback:
            return
        try:
            await self.callbacks.event_publish_callback("turn.tools_selected", {
                "sessionID": self.session.id,
                "step": self._step,
                **selection_metadata,
            })
        except Exception as exc:
            log.debug("runner.turn_tools_selected.publish_failed", {"error": str(exc)})

    def _tool_compact_placeholder(self, tool_name: str, text: str) -> Tuple[str, str]:
        normalized = " ".join(text.split())
        preview = normalized[:TOOL_RESULT_PREVIEW_CHARS]
        suffix = "..." if len(normalized) > TOOL_RESULT_PREVIEW_CHARS else ""
        if preview:
            placeholder = (
                f"[Context compacted: `{tool_name}` output omitted to save space. "
                f"Preview: {preview}{suffix}]"
            )
        else:
            placeholder = f"[Context compacted: `{tool_name}` output omitted to save space.]"
        return placeholder, preview

    def _get_persisted_tool_placeholder(self, part: Any, fallback_tool_name: str) -> Optional[str]:
        state = getattr(part, "state", None)
        metadata = getattr(state, "metadata", None) or {}
        placeholder = metadata.get("context_compact_placeholder")
        if placeholder:
            return str(placeholder)
        time_info = getattr(state, "time", None) or {}
        if isinstance(time_info, dict) and time_info.get("compacted"):
            return f"[Context compacted: `{fallback_tool_name}` output omitted to save space.]"
        return None

    async def _persist_tool_compaction(self, refs: List[Dict[str, Any]]) -> int:
        dirty_refs = [ref for ref in refs if ref.get("dirty")]
        if not dirty_refs:
            return 0

        updated_count = 0
        for ref in dirty_refs:
            part = ref["part"]
            state = getattr(part, "state", None)
            if state is None:
                continue
            await Message.update_part(
                self.session.id,
                ref["message_id"],
                part.id,
                state=state.model_dump() if hasattr(state, "model_dump") else state,
            )
            ref["dirty"] = False
            updated_count += 1
        return updated_count

    async def _compact_tool_ref(self, ref: Dict[str, Any], reason: str) -> bool:
        current_content = ref["chat_message"].content or ""
        if ref.get("compacted") or len(current_content) <= TOOL_RESULT_PREVIEW_CHARS:
            return False

        placeholder, preview = self._tool_compact_placeholder(ref["tool_name"], current_content)
        ref["chat_message"].content = placeholder
        ref["char_count"] = len(placeholder)
        ref["compacted"] = True

        part = ref["part"]
        state = getattr(part, "state", None)
        if state is not None:
            metadata = dict(getattr(state, "metadata", None) or {})
            metadata.update({
                "context_compacted": True,
                "context_compact_reason": reason,
                "context_compact_preview": preview,
                "context_compact_placeholder": placeholder,
                "context_compacted_step": self._step,
            })
            state.metadata = metadata
            time_info = dict(getattr(state, "time", None) or {})
            time_info["compacted"] = int(datetime.now().timestamp() * 1000)
            state.time = time_info
            ref["dirty"] = True

        return True

    async def _apply_tool_result_budget(
        self,
        tool_result_refs: List[Dict[str, Any]],
        ctx_window_tokens: int,
    ) -> Dict[str, int]:
        if not tool_result_refs:
            return {"compacted": 0, "persisted": 0}

        total_budget = max(
            TOOL_RESULT_MIN_CHAR_BUDGET,
            int(ctx_window_tokens * 4 * TOOL_RESULT_CHAR_BUDGET_RATIO),
        )
        per_turn_budget = max(
            TOOL_RESULT_MIN_TURN_BUDGET,
            int(total_budget * TOOL_RESULT_TURN_BUDGET_RATIO),
        )
        compacted = 0

        latest_turn = max(ref["turn_index"] for ref in tool_result_refs)
        latest_turn_refs = [ref for ref in tool_result_refs if ref["turn_index"] == latest_turn]
        latest_turn_chars = sum(ref["char_count"] for ref in latest_turn_refs)
        for ref in latest_turn_refs[:-1]:
            if latest_turn_chars <= per_turn_budget:
                break
            if await self._compact_tool_ref(ref, "tool_result_budget"):
                latest_turn_chars = sum(item["char_count"] for item in latest_turn_refs)
                compacted += 1

        total_chars = sum(ref["char_count"] for ref in tool_result_refs)
        for ref in tool_result_refs:
            if total_chars <= total_budget:
                break
            if await self._compact_tool_ref(ref, "context_budget"):
                total_chars = sum(item["char_count"] for item in tool_result_refs)
                compacted += 1

        persisted = await self._persist_tool_compaction(tool_result_refs)
        if compacted and self.callbacks.event_publish_callback:
            try:
                await self.callbacks.event_publish_callback("context.compacted", {
                    "sessionID": self.session.id,
                    "step": self._step,
                    "reason": "tool_result_budget",
                    "compactedToolResults": compacted,
                    "persistedToolResults": persisted,
                })
            except Exception as exc:
                log.debug("runner.context_compacted.publish_failed", {"error": str(exc)})

        return {"compacted": compacted, "persisted": persisted}

    def _supports_multimodal_user_content(self) -> bool:
        return self.provider_id in {"anthropic", "openai", "openai-compatible"}

    def _append_file_content_block(
        self,
        blocks: list[dict[str, Any]],
        text_fallbacks: list[str],
        *,
        mime: str,
        filename: str,
        url: str,
    ) -> None:
        """Append an appropriate content block for *url* into *blocks*.

        Images are embedded as base64 for multimodal-capable providers; other
        file types are extracted as text.  Falls back to a plain markdown link
        when extraction is not possible.
        """
        if self._supports_multimodal_user_content() and mime.startswith("image/"):
            import base64 as _b64
            data = read_file_part_bytes(url)
            if data:
                blocks.append({
                    "type": "image",
                    "mimeType": mime,
                    "data": _b64.b64encode(data).decode("utf-8"),
                })
                return

        extracted_text = extract_file_text(mime=mime, filename=filename, url=url)
        if extracted_text:
            text_fallbacks.append(extracted_text)
            blocks.append({"type": "text", "text": extracted_text})
            return

        text_fallbacks.append(
            f"[File: {filename}]({url})" if url else f"[File: {filename}]"
        )

    @classmethod
    async def loop(cls, session_id: str) -> Optional['MessageInfo']:
        """
        Start or continue session processing loop.
        
        This is the main entry point for session execution,
        matching Flocks' SessionPrompt.loop() behavior.
        
        Now delegates to SessionLoop for better separation of concerns.
        
        Args:
            session_id: Session ID to process
            
        Returns:
            Last assistant message with parts
        """
        # Delegate to SessionLoop (new architecture)
        from flocks.session.session_loop import SessionLoop
        
        result = await SessionLoop.run(session_id)
        return result.last_message
    
    @classmethod
    def cancel(cls, session_id: str) -> bool:
        """
        Cancel a running session.
        
        Args:
            session_id: Session ID to cancel
            
        Returns:
            True if session was cancelled
        """
        from flocks.session.core.status import SessionStatus
        
        runner = cls._active_sessions.get(session_id)
        if runner:
            runner.abort()
            del cls._active_sessions[session_id]
            log.info("runner.cancelled", {"session_id": session_id})
        
        # Set status to idle (Flocks compatibility)
        from flocks.session.core.status import SessionStatusIdle
        SessionStatus.set(session_id, SessionStatusIdle())
        return True
    
    @classmethod
    def cancel_children(cls, parent_session_id: str) -> int:
        """Cancel all runners whose session.parent_id matches, recursively."""
        from flocks.session.core.status import SessionStatus, SessionStatusIdle
        
        cancelled = 0
        child_ids = [
            sid for sid, runner in cls._active_sessions.items()
            if getattr(runner.session, 'parent_id', None) == parent_session_id
        ]
        for sid in child_ids:
            runner = cls._active_sessions.pop(sid, None)
            if runner:
                runner.abort()
                SessionStatus.set(sid, SessionStatusIdle())
                cancelled += 1
                log.info("runner.child_cancelled", {
                    "session_id": sid,
                    "parent_session_id": parent_session_id,
                })
            cancelled += cls.cancel_children(sid)
        return cancelled
    
    @classmethod
    async def command(
        cls,
        session_id: str,
        command: str,
        arguments: str = "",
        message_id: Optional[str] = None,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a slash command in a session.
        
        Args:
            session_id: Session ID
            command: Command name (e.g., "init", "help")
            arguments: Command arguments
            message_id: Optional message ID
            agent: Optional agent name
            model: Optional model string (provider/model)
            variant: Optional model variant
            
        Returns:
            Command execution result
        """
        from flocks.command.command import Command
        
        # Get command definition
        cmd = Command.get(command)
        if not cmd:
            raise ValueError(f"Command '{command}' not found")
        
        # Parse model if provided
        provider_id, model_id = None, None
        if model:
            parts = model.split("/", 1)
            if len(parts) == 2:
                provider_id, model_id = parts
        
        # Execute command template
        template = cmd.template
        
        # Replace placeholders
        template = template.replace("$ARGUMENTS", arguments)
        
        # Create prompt request
        parts = [{"type": "text", "text": template}]
        
        log.info("runner.command", {
            "session_id": session_id,
            "command": command,
            "arguments": arguments[:50] if arguments else "",
        })
        
        return {
            "command": command,
            "arguments": arguments,
            "template": template,
        }
    
    @classmethod
    async def shell(
        cls,
        session_id: str,
        agent: str,
        command: str,
        model: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a shell command in session context.
        
        Args:
            session_id: Session ID
            agent: Agent name
            command: Shell command to execute
            model: Optional model info
            
        Returns:
            Shell execution result
        """
        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        cwd = session.directory or os.getcwd()
        
        user_msg = await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="The following tool was executed by the user",
            agent=agent,
        )
        
        assistant_msg = await Message.create(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="",
            agent=agent,
            parent_id=user_msg.id,
        )
        
        start_time = asyncio.get_event_loop().time()
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=300,
            )
            output = (stdout_bytes or b"").decode("utf-8", errors="replace") + \
                     (stderr_bytes or b"").decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            output = "Command timed out after 300 seconds"
            exit_code = -1
            try:
                proc.kill()
            except Exception as _kill_err:
                log.debug("runner.shell.kill_failed", {"error": str(_kill_err)})
        except Exception as e:
            output = f"Error executing command: {str(e)}"
            exit_code = -1
        
        end_time = asyncio.get_event_loop().time()
        
        log.info("runner.shell", {
            "session_id": session_id,
            "command": command[:50],
            "exit_code": exit_code,
            "duration_ms": int((end_time - start_time) * 1000),
        })
        
        return {
            "info": {
                "id": assistant_msg.id,
                "sessionID": session_id,
                "role": "assistant",
                "agent": agent,
            },
            "parts": [{
                "id": Identifier.create("part"),
                "messageID": assistant_msg.id,
                "sessionID": session_id,
                "type": "tool",
                "tool": "bash",
                "state": {
                    "status": "completed",
                    "input": {"command": command},
                    "output": output,
                },
            }],
        }
    
    def abort(self) -> None:
        """Signal abort to stop the loop."""
        self._abort.set()
    
    @property
    def is_aborted(self) -> bool:
        """Check if abort was signaled (internal or external)."""
        if self._abort.is_set():
            return True
        if self._external_abort is not None and self._external_abort.is_set():
            return True
        return False
    
    async def _process_step(
        self,
        messages: List[MessageInfo],
        last_user: MessageInfo,
    ) -> StepResult:
        """Process a single step in the loop with retry logic."""
        # Check for CLI callbacks (if running in CLI mode)
        # Only use CLI fallback if no callbacks were explicitly provided via constructor
        has_explicit_callbacks = any([
            self.callbacks.on_text_delta,
            self.callbacks.on_tool_start,
            self.callbacks.on_tool_end,
            self.callbacks.on_error,
            self.callbacks.event_publish_callback,
        ])
        if not has_explicit_callbacks:
            try:
                from flocks.cli.session_runner import _get_cli_callbacks
                cli_callbacks = _get_cli_callbacks()
                if cli_callbacks:
                    self.callbacks = cli_callbacks
            except ImportError:
                pass
        
        # Resolve agent
        agent_name = last_user.agent or self.agent_name
        agent = await Agent.get(agent_name) or await Agent.get("rex")

        # Track session agent (Flocks compatibility)
        try:
            from flocks.session.core.session_state import set_session_agent
            set_session_agent(self.session.id, agent.name)
        except Exception as e:
            log.debug("runner.session_agent.error", {"error": str(e)})
        
        # Check if we've reached max steps (matching Flocks logic)
        max_steps = agent.steps if hasattr(agent, 'steps') and agent.steps is not None else float('inf')
        is_last_step = self._step >= max_steps
        
        # Get provider
        provider = Provider.get(self.provider_id)
        if not provider:
            error = f"Provider {self.provider_id} not found"
            if self.callbacks.on_error:
                await self.callbacks.on_error(error)
            return StepResult(action="stop", error=error)

        # Apply config-based provider options (api_key/base_url)
        try:
            await Provider.apply_config(provider_id=self.provider_id)
        except Exception as e:
            log.debug("runner.provider.apply_config.error", {
                "provider": self.provider_id,
                "error": str(e),
            })
        
        if not provider.is_configured():
            error = f"Provider {self.provider_id} not configured"
            if self.callbacks.on_error:
                await self.callbacks.on_error(error)
            return StepResult(action="stop", error=error)
        
        # Build prompts and tools
        system_prompts = await self._build_system_prompts(agent)
        tools = await self._build_callable_tool_schema(agent, messages)
        if self._should_use_text_tool_call_mode() and tools:
            text_tool_catalog = self._build_text_tool_call_catalog_prompt(tools)
            if text_tool_catalog:
                system_prompts.append(text_tool_catalog)

        # If the last assistant message only contains tool results and no text,
        # force a direct answer to avoid repeated tool calls.
        last_assistant_msg = None
        for msg in reversed(messages):
            if msg.role == MessageRole.ASSISTANT:
                last_assistant_msg = msg
                break
        if last_assistant_msg:
            parts = await Message.parts(last_assistant_msg.id, self.session.id)
            has_text = any(getattr(p, "type", None) == "text" and getattr(p, "text", "").strip() for p in parts)
            has_tool_result = any(
                getattr(p, "type", None) == "tool" and
                getattr(getattr(p, "state", None), "status", None) in ("completed", "error", "running")
                for p in parts
            )
            if has_tool_result and not has_text:
                from flocks.session.prompt_strings import PROMPT_TOOL_RESULTS_AVAILABLE
                system_prompts.append(PROMPT_TOOL_RESULTS_AVAILABLE)
            
            # 检查最近几条消息中是否有重复的工具调用（轻量级警告）
            if has_tool_result and self._step > 2:
                # 收集最近的工具调用签名
                recent_tool_sigs = []
                for msg in reversed(messages[-3:]):  # 检查最近3条消息
                    if msg.role == MessageRole.ASSISTANT:
                        msg_parts = await Message.parts(msg.id, self.session.id)
                        for p in msg_parts:
                            if (getattr(p, "type", None) == "tool" and
                                hasattr(p, 'state') and 
                                hasattr(p.state, 'status') and
                                p.state.status == "completed"):
                                tool_name = getattr(p, 'tool', '')
                                tool_input = getattr(p.state, 'input', {})
                                sig = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)}"
                                recent_tool_sigs.append(sig)
                
                # 如果有重复的工具调用签名，添加提示（不禁用工具）
                if recent_tool_sigs:
                    sig_counts = {}
                    for sig in recent_tool_sigs:
                        sig_counts[sig] = sig_counts.get(sig, 0) + 1
                    
                    repeated_sigs = [sig for sig, count in sig_counts.items() if count >= 2]
                    if repeated_sigs:
                        log.warn("runner.repeated_tool_calls_detected", {
                            "repeated_sigs": repeated_sigs,
                            "step": self._step,
                        })
                        from flocks.session.prompt_strings import PROMPT_REPEATED_TOOL_CALLS
                        system_prompts.append(PROMPT_REPEATED_TOOL_CALLS)

        # Hook pipeline: chat.message stage
        try:
            from flocks.hooks.pipeline import HookPipeline
            user_text = await Message.get_text_content(last_user)
            hook_input = {
                "sessionID": self.session.id,
                "agent": agent.name,
                "model": {"providerID": self.provider_id, "modelID": self.model_id},
                "message": {
                    "id": last_user.id,
                    "role": "user",
                    "content": user_text,
                },
            }
            hook_output = {"message": {"variant": getattr(last_user, "variant", None)}}
            ctx = await HookPipeline.run_chat_message(hook_input, hook_output)
            variant = ctx.output.get("message", {}).get("variant") if ctx else None
            if variant:
                await Message.update(self.session.id, last_user.id, variant=variant)
        except Exception as e:
            log.debug("runner.hook.chat_message.error", {"error": str(e)})
        
        # Convert messages to chat format with error handling
        try:
            chat_messages = await self._to_chat_messages(messages, system_prompts)
        except Exception as e:
            log.error("runner.to_chat_messages.error", {
                "error": str(e),
                "error_type": type(e).__name__,
                "message_count": len(messages),
            })
            raise
        
        # CRITICAL FIX: Ensure messages don't end with assistant role when tools are present
        # This prevents "assistant role in the final position when tools are used" API error
        # This commonly happens when:
        # 1. Tool calls fail (e.g., missing required parameters)
        # 2. Assistant message contains tool calls but no user response follows
        if chat_messages and tools and not self._should_use_text_tool_call_mode():
            last_message = chat_messages[-1]
            if last_message.role == "assistant":
                # Check if we need to add a synthetic user message
                # This is required by Anthropic API when using tools
                from flocks.session.prompt_strings import PROMPT_SYNTHETIC_CONTINUE
                synthetic_msg = ChatMessage(
                    role="user",
                    content=PROMPT_SYNTHETIC_CONTINUE,
                )
                chat_messages.append(synthetic_msg)
                log.info("runner.synthetic_user_added", {
                    "reason": "assistant_at_end_with_tools",
                    "step": self._step,
                    "session_id": self.session.id,
                })
        
        # Add reminder wrapping for queued user messages (matching Flocks logic)
        # This reminds the AI to address new user messages while continuing tasks
        if self._step > 1:
            # Find last finished assistant message
            last_finished = None
            for msg in reversed(messages):
                if msg.role == MessageRole.ASSISTANT and hasattr(msg, 'finish') and msg.finish:
                    if msg.finish not in ("tool-calls", "unknown"):
                        last_finished = msg
                        break
            
            # Wrap queued user messages with reminder
            if last_finished:
                from flocks.session.prompt_strings import SYNTHETIC_MESSAGE_MARKERS
                for chat_msg in chat_messages:
                    if chat_msg.role == "user":
                        if not any(marker in chat_msg.content for marker in SYNTHETIC_MESSAGE_MARKERS):
                            # Wrap with reminder
                            chat_msg.content = f"""<system-reminder>
The user sent the following message:
{chat_msg.content}

Please address this message and continue with your tasks.
</system-reminder>"""
        
        # Add max steps warning if this is the last step (matching Flocks)
        if is_last_step:
            from flocks.session.prompt_strings import PROMPT_MAX_STEPS
            chat_messages.append(ChatMessage(
                role="assistant",
                content=PROMPT_MAX_STEPS,
            ))
            
            log.warn("runner.max_steps_reached", {
                "step": self._step,
                "max_steps": max_steps,
                "session_id": self.session.id,
            })
            
            # Disable tools when max steps reached
            tools = []
        
        # Create assistant message (will be reused across retries)
        assistant_msg = await Message.create(
            session_id=self.session.id,
            role=MessageRole.ASSISTANT,
            content="",
            agent=agent.name,
            model_id=self.model_id,
            provider_id=self.provider_id,
            parent_id=last_user.id,
        )
        
        # Publish assistant message SSE event so frontends can show the message card
        if self.callbacks.event_publish_callback:
            import time as _time
            await self.callbacks.event_publish_callback("message.updated", {
                "info": {
                    "id": assistant_msg.id,
                    "sessionID": self.session.id,
                    "role": "assistant",
                    "time": {"created": int(_time.time() * 1000)},
                    "parentID": last_user.id,
                    "modelID": self.model_id,
                    "providerID": self.provider_id,
                    "agent": agent.name,
                    "mode": agent.name,
                    "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                }
            })
        
        # Retry loop matching Flocks' SessionProcessor.process()
        # MAX_ERROR_RETRIES caps exception-based retries so a permanently-failing
        # model endpoint (e.g. repeated 500) cannot hold the session loop open
        # forever, which would block every subsequent user message via
        # loop.already_running.
        # The two counters are independent: empty-response retries (transient
        # model quirk) and exception retries (API errors) track separately so
        # that one kind of failure doesn't eat the other's budget.
        MAX_ERROR_RETRIES = 7
        MAX_EMPTY_RETRIES = 3
        error_attempt = 0
        empty_attempt = 0

        while not self.is_aborted:
            try:
                # Set status to busy
                SessionStatus.set(self.session.id, SessionStatusBusy())
                
                # Call LLM with tools
                result = await self._call_llm(
                    provider=provider,
                    messages=chat_messages,
                    tools=tools,
                    agent=agent,
                    assistant_msg=assistant_msg,
                )

                # Detect empty response: some models (e.g. MiniMax) occasionally
                # return 0 chunks with finish_reason=stop after tool execution,
                # producing no text and no tool calls. Treat this as a transient
                # failure and retry with exponential backoff instead of silently
                # terminating the agent.
                if (result.action == "stop" and not result.error
                        and not result.content and not result.tool_calls):
                    empty_attempt += 1
                    if empty_attempt <= MAX_EMPTY_RETRIES:
                        delay_ms = SessionRetry.delay(empty_attempt)
                        next_retry_time = int(asyncio.get_event_loop().time() * 1000) + delay_ms
                        log.warn("runner.step.empty_response_retry", {
                            "attempt": empty_attempt,
                            "delay_ms": delay_ms,
                            "session_id": self.session.id,
                            "model": self.model_id,
                        })
                        SessionStatus.set(
                            self.session.id,
                            SessionStatusRetry(
                                attempt=empty_attempt,
                                message="Model returned empty response, retrying...",
                                next=next_retry_time,
                            )
                        )
                        await SessionRetry.sleep(delay_ms, self._abort)
                        continue
                    else:
                        # All retries exhausted — surface a clear error so the
                        # user knows the model is incompatible, rather than
                        # silently hanging or showing a blank response.
                        empty_error_msg = (
                            f"Model '{self.model_id}' returned an empty response "
                            f"after {MAX_EMPTY_RETRIES} retries."
                        )
                        log.error("runner.step.empty_response_exhausted", {
                            "session_id": self.session.id,
                            "model": self.model_id,
                            "attempts": empty_attempt,
                        })
                        empty_error_dict = {
                            "name": "EmptyResponseError",
                            "message": empty_error_msg,
                            "data": {
                                "message": empty_error_msg,
                                "model": self.model_id,
                                "attempts": empty_attempt,
                            },
                        }
                        if self.callbacks.on_error:
                            await self.callbacks.on_error(empty_error_msg)
                        await Message.update(
                            self.session.id,
                            assistant_msg.id,
                            error=empty_error_dict,
                            finish="error",
                        )
                        return StepResult(action="stop", error=empty_error_msg)

                # Success! Update finish reason
                finish = "tool-calls" if result.tool_calls else "stop"
                await Message.update(self.session.id, assistant_msg.id, finish=finish)
                
                # Note: Compaction check is now done in the main loop (run()) before processing step
                # This matches Flocks's logic: check lastFinished.tokens at loop start

                return result
                
            except Exception as e:
                error_attempt += 1
                log.error("runner.step.error", {
                    "error": str(e),
                    "attempt": error_attempt,
                })
                
                # Convert exception to error dict for retry check
                error_dict = self._exception_to_error_dict(e)
                
                # Check if retryable
                retry_message = SessionRetry.retryable(error_dict)

                if retry_message is not None and error_attempt <= MAX_ERROR_RETRIES:
                    # Error is retryable and we have budget left
                    delay_ms = SessionRetry.delay(error_attempt, error_dict)
                    # Always cap the sleep to RETRY_MAX_DELAY_NO_HEADERS so a
                    # headers-present but retry-after-absent 500 response cannot
                    # cause multi-minute sleeps that block the loop.
                    from flocks.session.lifecycle.retry import RETRY_MAX_DELAY_NO_HEADERS
                    delay_ms = min(delay_ms, RETRY_MAX_DELAY_NO_HEADERS)
                    next_retry_time = int(asyncio.get_event_loop().time() * 1000) + delay_ms
                    
                    log.info("runner.step.retry", {
                        "attempt": error_attempt,
                        "delay_ms": delay_ms,
                        "reason": retry_message,
                    })
                    
                    # Set retry status
                    SessionStatus.set(
                        self.session.id,
                        SessionStatusRetry(
                            attempt=error_attempt,
                            message=retry_message,
                            next=next_retry_time,
                        )
                    )
                    
                    # Wait before retry
                    await SessionRetry.sleep(delay_ms, self._abort)
                    
                    # Continue to next retry attempt
                    continue
                else:
                    # Error is not retryable, or retry budget exhausted
                    if retry_message is not None:
                        log.error("runner.step.max_retries_exceeded", {
                            "error": str(e),
                            "attempt": error_attempt,
                            "max_retries": MAX_ERROR_RETRIES,
                        })
                    else:
                        log.error("runner.step.not_retryable", {"error": str(e)})

                    if self.callbacks.on_error:
                        await self.callbacks.on_error(str(e))
                    
                    # Update assistant message with error (must be dict, not string)
                    await Message.update(
                        self.session.id,
                        assistant_msg.id,
                        error=error_dict,
                        finish="error",
                    )
                    
                    return StepResult(action="stop", error=str(e))
        
        # Aborted
        return StepResult(action="stop", error="Aborted")
    
    async def _build_system_prompts(self, agent: AgentInfo) -> List[str]:
        """Build system prompts."""
        tool_revision = ToolRegistry.revision()
        cache_key = (
            f"system_prompts:{self.session.id}:{agent.name}:{self.provider_id}:{self.model_id}:{tool_revision}"
        )
        cached = self._static_cache.get(cache_key)
        if cached is not None:
            return list(cached)

        prompts = []
        
        # Provider-specific base prompt (from anthropic.txt, gemini.txt, etc.)
        provider_prompts = SystemPrompt.provider(self.model_id)
        prompts.extend(provider_prompts)
        
        # Memory bootstrap context (matching OpenClaw's injection)
        if self._memory_bootstrap_data:
            # Add memory instructions
            instructions = self._memory_bootstrap_data.get("instructions", "")
            if instructions:
                prompts.append(instructions)
            
            # Inject main MEMORY.md content
            main_memory = self._memory_bootstrap_data.get("main_memory")
            if main_memory and main_memory.get("inject"):
                memory_content = main_memory.get("content", "")
                if memory_content:
                    prompts.append(f"## {main_memory['path']}\n\n{memory_content}")
            
            # Note: daily files are NOT injected, agent reads them per instructions
            log.debug("runner.memory_injected", {
                "session_id": self.session.id,
                "has_main": main_memory is not None,
            })
        
        # Environment info
        env_prompts = await SystemPrompt.environment(
            directory=self.session.directory,
            vcs="git" if self.session.directory else None,
        )
        prompts.extend(env_prompts)
        
        # Custom instructions
        custom_prompts = await SystemPrompt.custom(directory=self.session.directory)
        prompts.extend(custom_prompts)
        
        # Agent-specific prompt (if any)
        if agent.prompt:
            prompts.append(agent.prompt)

        # Sandbox runtime context for better tool/path awareness
        sandbox_prompt = await self._build_sandbox_prompt(agent)
        if sandbox_prompt:
            prompts.append(sandbox_prompt)
        
        # Channel context: inject the IM channel and session info when this
        # session originates from an IM channel (Feishu / WeCom / DingTalk).
        channel_ctx_prompt = await self._build_channel_context_prompt()
        if channel_ctx_prompt:
            prompts.append(channel_ctx_prompt)

        # Tool instructions
        prompts.append(self._get_tool_instructions())

        tool_catalog_prompt = self._build_tool_catalog_prompt(agent)
        if tool_catalog_prompt:
            prompts.append(tool_catalog_prompt)

        # Debug: optionally print system prompt during execution
        if os.getenv("FLOCKS_PRINT_SYSTEM_PROMPT", "").lower() in ("1", "true", "yes"):
            header = (
                f"\n=== system_prompt session={self.session.id} "
                f"agent={agent.name} model={self.provider_id}/{self.model_id} ==="
            )
            print(header, file=sys.stderr)
            for idx, prompt in enumerate(prompts):
                print(f"\n--- prompt[{idx}] ---\n{prompt}\n", file=sys.stderr)
            print("=== end system_prompt ===\n", file=sys.stderr)
        
        self._static_cache[cache_key] = list(prompts)
        return list(prompts)

    async def _build_sandbox_prompt(self, agent: AgentInfo) -> Optional[str]:
        """Build sandbox context prompt when sandboxing is active."""
        try:
            from flocks.config import Config
            from flocks.session.core.session_state import get_main_session_id
            from flocks.sandbox.system_prompt import build_sandbox_system_prompt

            cfg = await Config.get()
            config_data = cfg.model_dump(by_alias=True, exclude_none=True)
            session_key = self.session.id
            main_session_key = get_main_session_id() or self.session.id
            return await build_sandbox_system_prompt(
                config_data=config_data,
                session_key=session_key,
                agent_id=agent.name,
                main_session_key=main_session_key,
                workspace_dir=self.session.directory,
            )
        except Exception as e:
            log.debug("runner.sandbox_prompt.error", {"error": str(e)})
            return None

    async def _build_channel_context_prompt(self) -> Optional[str]:
        """Build a brief system prompt snippet describing the IM channel context.

        When the current session was initiated from an IM channel (Feishu, WeCom,
        or DingTalk), this injects a one-liner so the agent knows:
          - which platform the user is on
          - the current Flocks session ID

        The lookup is best-effort: if the binding table is unavailable or the
        session has no binding the method returns None silently.
        """
        try:
            from flocks.channel.inbound.session_binding import SessionBindingService

            svc = SessionBindingService()
            bindings = await svc.get_bindings_by_session(self.session.id)
            if not bindings:
                return (
                    f"## Current Session Context\n\n"
                    f"This conversation originates from the **Flocks Web UI** (not an IM channel).\n"
                    f"Session ID: {self.session.id}\n\n"
                    f"When the user asks to send a message to an IM platform, you do NOT have a "
                    f"target IM session ID yet — you must discover and ask the user to pick one."
                )

            # Map channel_id prefix → human-readable platform name
            _CHANNEL_NAMES = {
                "feishu": "Feishu (飞书)",
                "wecom": "WeCom (企业微信)",
                "dingtalk": "DingTalk (钉钉)",
            }

            parts = []
            for b in bindings:
                cid = b.channel_id or ""
                platform = next(
                    (name for key, name in _CHANNEL_NAMES.items() if cid.startswith(key)),
                    cid,
                )
                chat_type = b.chat_type.value if b.chat_type else "unknown"
                parts.append(f"- Platform: {platform}, chat_type: {chat_type}, channel_id: {cid}")

            lines = "\n".join(parts)
            return (
                f"## Current IM Channel Context\n\n"
                f"This conversation originates from an IM channel. Details:\n"
                f"{lines}\n"
                f"Session ID: {self.session.id}\n\n"
                f"You can use this information when the user asks which platform they are "
                f"on, when sending messages back to the channel, or when the context of "
                f"the conversation depends on the IM source."
            )
        except Exception as e:
            log.debug("runner.channel_context_prompt.error", {"error": str(e)})
            return None

    def _get_tool_instructions(self) -> str:
        from flocks.session.prompt_strings import PROMPT_TOOL_INSTRUCTIONS
        if self._should_use_text_tool_call_mode():
            return (
                "You have access to tools, but for this model you MUST call them using "
                "MiniMax XML embedded in text instead of native API tool-calling.\n\n"
                "Required format:\n"
                "<minimax:tool_call>\n"
                "<invoke name=\"tool_name\">\n"
                "<parameter name=\"param_name\">json_or_string_value</parameter>\n"
                "</invoke>\n"
                "</minimax:tool_call>\n\n"
                "Rules:\n"
                "- Emit exactly one tool call block when you need a tool.\n"
                "- Use valid tool names only.\n"
                "- Parameter values must be valid JSON scalars/objects/arrays when appropriate.\n"
                "- After tool results are returned, continue the task instead of repeating the same call.\n"
                "- Do not use native API tool-calling for this model.\n"
            )
        return PROMPT_TOOL_INSTRUCTIONS

    def _list_catalog_tool_infos(self, agent: AgentInfo) -> List[Any]:
        tool_infos: List[Any] = []
        is_rex = getattr(agent, "name", "") == "rex"

        for tool_info in list_tool_catalog_infos():
            if is_rex:
                tool_infos.append(tool_info)
                continue

            if not isinstance(getattr(agent, "tools", None), (list, tuple, set)):
                tool_infos.append(tool_info)
                continue
            metadata = get_tool_catalog_metadata(tool_info.name, tool_info)
            if not agent_declares_tool(agent, tool_info.name) and not metadata.always_load:
                continue
            tool_infos.append(tool_info)

        return tool_infos

    def _build_tool_catalog_prompt(self, agent: AgentInfo) -> Optional[str]:
        from flocks.tool.system.slash_command import format_tools_catalog_summary

        catalog_tools = self._list_catalog_tool_infos(agent)
        if not catalog_tools:
            return None

        catalog_summary = format_tools_catalog_summary(
            tools=catalog_tools,
            max_description_chars=100,
            include_tip=False,
        )
        if not catalog_summary:
            return None

        is_rex = getattr(agent, "name", "") == "rex"
        if is_rex:
            rules = (
                "You can see the full tool catalog for awareness. "
                "This catalog is reference-only and does not define parameter names. "
                "Only tools exposed in the current callable schema may be called directly. "
                "If a tool appears in the catalog but is not exposed this turn, use `tool_search` first. "
                "Use the current callable schema as the sole source of truth for parameters. "
                "Do not invent parameters for tools that are not currently exposed."
            )
        else:
            rules = (
                "You can see a tool catalog derived from your configured callable tool set. "
                "This catalog is reference-only and does not define parameter names. "
                "Only tools exposed in the current callable schema may be called directly. "
                "Use the current callable schema as the sole source of truth for parameters. "
                "Do not infer argument names from the catalog. "
                "Do not invent parameters for tools that are not currently exposed."
            )

        return (
            "## Tool Catalog Awareness\n\n"
            f"{rules}\n\n"
            f"{catalog_summary}"
        )

    def _should_use_text_tool_call_mode(self) -> bool:
        model_lower = (self.model_id or "").lower()
        provider_lower = (self.provider_id or "").lower()
        minimax_text_tool_call_providers = {
            "custom-threatbook-internal",
            "custom-tb-inner",
        }
        return (
            "minimax" in model_lower
            and provider_lower in minimax_text_tool_call_providers
        )

    def _build_text_tool_call_catalog_prompt(self, tools: List[Dict[str, Any]]) -> Optional[str]:
        if not tools:
            return None

        lines = [
            "## Available Tools",
            "Use only the following tools when emitting MiniMax XML tool calls.",
            "This section is the authoritative callable schema for this turn.",
            "Parameter names must match exactly. Never infer or rename arguments from the awareness catalog.",
        ]
        for tool in tools:
            fn = tool.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            description = fn.get("description", "").strip()
            params = fn.get("parameters", {}) or {}
            properties = params.get("properties", {}) if isinstance(params, dict) else {}
            required = params.get("required", []) if isinstance(params, dict) else []

            lines.append(f"- `{name}`: {description or 'No description provided.'}")
            if properties:
                lines.append("  Parameters:")
                for param_name, spec in properties.items():
                    param_type = spec.get("type", "any") if isinstance(spec, dict) else "any"
                    param_desc = spec.get("description", "") if isinstance(spec, dict) else ""
                    required_suffix = "required" if param_name in required else "optional"
                    if param_desc:
                        lines.append(f"  - `{param_name}` ({param_type}, {required_suffix}): {param_desc}")
                    else:
                        lines.append(f"  - `{param_name}` ({param_type}, {required_suffix})")

        return "\n".join(lines)
    
    async def _build_callable_tool_schema(
        self,
        agent: AgentInfo,
        messages: Optional[List[MessageInfo]] = None,
    ) -> List[Dict[str, Any]]:
        """Build tool definitions for LLM."""
        selected_tool_infos, selection_metadata = await self._list_callable_tool_infos_for_turn(agent, messages or [])
        await self._publish_turn_tools_event(selection_metadata)

        tools = []
        for tool_info in selected_tool_infos:
            # Get dynamic description for skill tool
            description = tool_info.description
            if tool_info.name == "skill":
                # Import here to avoid circular dependency
                from flocks.tool.system.skill import build_description
                from flocks.skill.skill import Skill
                
                skills = await Skill.all()
                description = build_description(skills)
                log.info("runner.build_tools.skill_description", {
                    "skill_count": len(skills),
                    "description_preview": description[:100]
                })
            
            schema = tool_info.get_schema()
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool_info.name,
                    "description": description,
                    "parameters": schema.to_json_schema(),
                }
            }
            tools.append(tool_def)

        log.info("runner.tools_selected", {
            "session_id": self.session.id,
            "step": self._step,
            "selected": len(tools),
            "enabled": selection_metadata.get("enabledToolCount"),
        })
        return tools
    
    def _agent_declares_tool(self, agent: AgentInfo, tool_name: str) -> bool:
        """Check if agent statically declares a tool."""
        tool = ToolRegistry.get(tool_name)
        if tool is None:
            return False
        metadata = get_tool_catalog_metadata(tool_name, tool.info)
        return agent_declares_tool(agent, tool_name) or metadata.always_load
    
    def _exception_to_error_dict(self, exception: Exception) -> Dict[str, Any]:
        """
        Convert exception to error dict for retry checking.
        
        Ported from original MessageV2.fromError() structure.
        """
        error_dict = {
            "name": type(exception).__name__,
            "message": str(exception),
            "data": {
                "message": str(exception),
            }
        }
        
        # Check if it's an API error with specific attributes
        if hasattr(exception, 'status_code'):
            status_code = getattr(exception, 'status_code')
            error_dict["name"] = "APIError"
            error_dict["data"]["statusCode"] = status_code
            
            # Determine if retryable based on status code
            is_retryable = status_code in {429, 500, 502, 503, 504}
            error_dict["data"]["isRetryable"] = is_retryable
            
            # Extract response headers if available
            if hasattr(exception, 'response') and hasattr(exception.response, 'headers'):
                headers = dict(exception.response.headers)
                error_dict["data"]["responseHeaders"] = headers
        
        # Check for common retryable error patterns
        error_msg = str(exception).lower()
        if any(pattern in error_msg for pattern in [
            "rate limit", "too many requests", "429",
            "overloaded", "unavailable", "503", "502",
            "timeout", "timed out", "server error",
            "connection error", "connection reset", "connection refused",
        ]):
            error_dict["name"] = "APIError"
            error_dict["data"]["isRetryable"] = True
        
        return error_dict
    
    def _get_context_window_tokens(self) -> int:
        """Resolve the context window size for the current model."""
        try:
            ctx, _, _ = Provider.resolve_model_info(self.provider_id, self.model_id)
            if ctx and ctx > 0:
                return ctx
        except Exception:
            pass
        return 128_000

    async def _to_chat_messages(
        self,
        messages: List[MessageInfo],
        system_prompts: List[str],
    ) -> List[ChatMessage]:
        """
        Convert messages to chat format with tool calls.
        
        Ported from original MessageV2.toModelMessage() logic:
        - Include text parts
        - Include tool calls and results
        - Format tool results as user messages
        """
        chat_messages = []
        ctx_window_tokens = self._get_context_window_tokens()
        tool_result_refs: List[Dict[str, Any]] = []
        turn_index = 0
        
        # Add system prompts
        if system_prompts:
            chat_messages.append(ChatMessage(
                role="system",
                content="\n\n".join(system_prompts),
            ))
        
        # Convert each message with parts
        for msg in messages:
            if msg.role == MessageRole.USER or (isinstance(msg.role, str) and msg.role == "user"):
                turn_index += 1
            # Get message parts
            parts = await Message.parts(msg.id, self.session.id)
            
            if not parts:
                # Fallback: use text content only
                content = await Message.get_text_content(msg)
                if content.strip():
                    chat_messages.append(ChatMessage(
                        role=msg.role if isinstance(msg.role, str) else msg.role.value,
                        content=content,
                    ))
                continue
            
            # Build message content from parts
            if msg.role == MessageRole.USER or (isinstance(msg.role, str) and msg.role == "user"):
                user_content_parts = []
                user_content_blocks: list[dict[str, Any]] = []
                for part in parts:
                    if hasattr(part, 'type'):
                        if part.type == "text" and hasattr(part, 'text'):
                            if not getattr(part, 'ignored', False) and part.text.strip():
                                user_content_parts.append(part.text)
                                user_content_blocks.append({
                                    "type": "text",
                                    "text": part.text,
                                })
                        elif part.type == "file" and hasattr(part, 'mime'):
                            mime = getattr(part, 'mime', '')
                            if mime != 'application/x-directory':
                                filename = getattr(part, 'filename', 'file')
                                url = getattr(part, 'url', '')
                                self._append_file_content_block(
                                    user_content_blocks,
                                    user_content_parts,
                                    mime=mime,
                                    filename=filename,
                                    url=url,
                                )
                        elif part.type == "compaction":
                            user_content_parts.append("What did we do so far?")
                            user_content_blocks.append({
                                "type": "text",
                                "text": "What did we do so far?",
                            })
                        elif part.type == "subtask":
                            user_content_parts.append("The following tool was executed by the user")
                            user_content_blocks.append({
                                "type": "text",
                                "text": "The following tool was executed by the user",
                            })
                
                if user_content_blocks and any(
                    block.get("type") == "image"
                    for block in user_content_blocks
                ):
                    chat_messages.append(ChatMessage(
                        role="user",
                        content=user_content_blocks,
                    ))
                elif user_content_parts:
                    chat_messages.append(ChatMessage(
                        role="user",
                        content="\n\n".join(user_content_parts),
                    ))
            
            elif msg.role == MessageRole.ASSISTANT or (isinstance(msg.role, str) and msg.role == "assistant"):
                # Skip messages with errors (matching Flocks logic)
                # Flocks: skip if error exists, UNLESS it's AbortedError with useful content
                if hasattr(msg, 'error') and msg.error:
                    # Check if it's an AbortedError
                    is_aborted_error = False
                    if isinstance(msg.error, dict):
                        error_name = msg.error.get('name', '')
                        is_aborted_error = error_name in ('MessageAbortedError', 'AbortedError')
                    
                    # If AbortedError, check if message has useful content
                    if is_aborted_error:
                        has_content = any(
                            hasattr(p, 'type') and p.type not in ("step-start", "reasoning")
                            for p in parts
                        )
                        if not has_content:
                            # AbortedError with no content - skip
                            continue
                        # AbortedError with content - include it
                    else:
                        # Non-AbortedError - skip
                        continue
                
                assistant_content_parts = []
                # Structured tool calls for the assistant message (OpenAI format)
                structured_tool_calls: List[Dict[str, Any]] = []
                # Corresponding tool-result messages (role="tool")
                pending_tool_results: List[ChatMessage] = []
                
                for part in parts:
                    if not hasattr(part, 'type'):
                        continue
                    
                    # Text parts
                    if part.type == "text" and hasattr(part, 'text'):
                        assistant_content_parts.append(part.text)
                    
                    # Tool parts - use structured OpenAI function-calling format
                    elif part.type == "tool" and hasattr(part, 'state'):
                        tool_name = getattr(part, 'tool', 'unknown')
                        call_id = getattr(part, 'callID', None) or f"call_{id(part)}"
                        tool_input = getattr(part.state, 'input', {})
                        
                        if part.state.status == "completed":
                            persisted_placeholder = self._get_persisted_tool_placeholder(part, tool_name)
                            if persisted_placeholder:
                                tool_output_str = persisted_placeholder
                            else:
                                tool_output = getattr(part.state, 'output', '')
                                if hasattr(part.state, 'get_output_str'):
                                    tool_output_str = part.state.get_output_str()
                                elif isinstance(tool_output, str):
                                    tool_output_str = tool_output
                                else:
                                    try:
                                        tool_output_str = json.dumps(tool_output, ensure_ascii=False, indent=2)
                                    except (TypeError, ValueError):
                                        tool_output_str = str(tool_output)

                                from flocks.tool.truncation import truncate_tool_result_dynamic
                                tool_output_str, was_dyn_truncated = truncate_tool_result_dynamic(
                                    tool_output_str, ctx_window_tokens,
                                )
                                if was_dyn_truncated:
                                    log.info("runner.tool_result_dynamic_truncated", {
                                        "tool_name": tool_name,
                                        "call_id": call_id,
                                        "context_window": ctx_window_tokens,
                                        "truncated_len": len(tool_output_str),
                                    })
                            
                            # Build structured tool call for assistant message
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            # Build tool-result message
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content=tool_output_str,
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                            tool_result_refs.append({
                                "chat_message": pending_tool_results[-1],
                                "part": part,
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "turn_index": turn_index,
                                "char_count": len(tool_output_str),
                                "compacted": bool(persisted_placeholder),
                                "dirty": False,
                            })
                            
                            log.debug("runner.to_chat_messages.tool_result_added", {
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "call_id": call_id,
                                "output_length": len(tool_output_str),
                                "compacted": bool(persisted_placeholder),
                            })
                        
                        elif part.state.status == "error":
                            tool_error = getattr(part.state, 'error', 'Unknown error')
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content=f"Error: {tool_error}",
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                        
                        elif part.state.status == "running":
                            # Tool was interrupted (e.g., by user abort) before completing.
                            # Include it in chat context so the LLM knows this tool call was
                            # attempted and interrupted, allowing it to re-attempt if needed.
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content="Error: Tool execution was interrupted",
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                            log.debug("runner.to_chat_messages.running_tool_as_interrupted", {
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "call_id": call_id,
                            })
                
                # Add assistant message
                if assistant_content_parts or structured_tool_calls:
                    chat_messages.append(ChatMessage(
                        role="assistant",
                        content="\n\n".join(assistant_content_parts) if assistant_content_parts else "",
                        tool_calls=structured_tool_calls if structured_tool_calls else None,
                    ))
                    # Append tool-result messages immediately after the assistant message
                    chat_messages.extend(pending_tool_results)
                else:
                    # Log if assistant message was skipped due to no content
                    log.debug("runner.to_chat_messages.assistant_skipped", {
                        "message_id": msg.id,
                        "parts_count": len(parts),
                        "has_error": hasattr(msg, 'error') and bool(msg.error),
                    })
        
        budget_result = await self._apply_tool_result_budget(tool_result_refs, ctx_window_tokens)
        if budget_result.get("compacted"):
            log.info("runner.context_budget_enforced", {
                "session_id": self.session.id,
                "step": self._step,
                "context_window": ctx_window_tokens,
                "compacted_tool_results": budget_result.get("compacted", 0),
                "persisted_tool_results": budget_result.get("persisted", 0),
            })

        log.debug("runner.to_chat_messages.result", {
            "total_messages": len(chat_messages),
            "roles": [m.role for m in chat_messages],
        })
        
        return chat_messages
    
    async def _call_llm(
        self,
        provider: Any,
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
        agent: AgentInfo,
        assistant_msg: MessageInfo,
    ) -> StepResult:
        """
        Call LLM and process response with event-driven streaming.
        
        Uses StreamProcessor to handle events and execute tools synchronously.
        Ported from Flocks' SessionProcessor.process() behavior.
        """
        # Create stream processor
        main_session_key = self.session.id
        config_data: Dict[str, Any] = {}
        try:
            from flocks.config import Config
            from flocks.session.core.session_state import get_main_session_id

            cfg = await Config.get()
            config_data = cfg.model_dump(by_alias=True, exclude_none=True)
            main_session_key = get_main_session_id() or self.session.id
        except Exception as e:
            log.debug("runner.sandbox_context_init_failed", {"error": str(e)})

        processor = StreamProcessor(
            session_id=self.session.id,
            assistant_message=assistant_msg,
            agent=agent,
            permission_callback=self._handle_permission,
            text_delta_callback=self.callbacks.on_text_delta,
            reasoning_delta_callback=self.callbacks.on_reasoning_delta,
            tool_start_callback=self.callbacks.on_tool_start,
            tool_end_callback=self.callbacks.on_tool_end,
            event_publish_callback=self.callbacks.event_publish_callback,
            session_key=self.session.id,
            main_session_key=main_session_key,
            workspace_dir=self.session.directory,
            langfuse_generation=None,
            step_index=self._step,
        )
        
        # Build provider options (thinking / reasoning / max_tokens)
        from flocks.provider.options import build_provider_options
        provider_options = build_provider_options(self.provider_id, self.model_id)

        # Clean up any leftover reasoning state from a previous (failed) call
        if hasattr(self, '_current_reasoning_id'):
            delattr(self, '_current_reasoning_id')

        from flocks.session.streaming.tool_accumulator import ToolCallAccumulator
        tool_accumulator = ToolCallAccumulator(processor)

        text_started = False
        reasoning_id_counter = 0
        stream_finish_reason: Optional[str] = None

        # -- Observability: create trace & generation scopes (safe no-op when
        # Langfuse is unconfigured).  All observability calls are wrapped in
        # try/except so they never break the core session flow.
        trace_ctx = None
        generation_ctx = None
        try:
            input_preview = []
            for _msg in messages[-12:]:
                _mc = _msg.content or ""
                input_preview.append(
                    {"role": _msg.role, "chars": len(_mc), "preview": _mc[:240]}
                )

            trace_tags = [
                f"session:{self.session.id}",
                f"step:{self._step}",
                f"session_step:{self.session.id}:{self._step}",
                f"agent:{agent.name}",
                f"provider:{self.provider_id}",
            ]
            trace_ctx = trace_scope(
                name="SessionRunner.step",
                session_id=self.session.id,
                tags=trace_tags,
                input={
                    "step": self._step,
                    "message_count": len(messages),
                    "tool_count": len(tools),
                    "last_user_preview": next(
                        ((m.content or "")[:280] for m in reversed(messages) if m.role == "user"),
                        "",
                    ),
                },
                metadata={
                    "provider_id": self.provider_id,
                    "model_id": self.model_id,
                    "agent": agent.name,
                    "workspace": self.session.directory,
                },
            )
            generation_ctx = generation_scope(
                parent=trace_ctx.observation,
                name="LLM.generate",
                model=self.model_id,
                input=input_preview,
                metadata={
                    "provider_id": self.provider_id,
                    "session_id": self.session.id,
                    "step": self._step,
                    "tool_names": [t.get("function", {}).get("name", "") for t in tools][:50],
                },
            )
            processor._langfuse_generation = generation_ctx.observation
        except Exception as exc:
            log.debug("runner.observability.init_failed", {"error": str(exc)})
            trace_ctx = None
            generation_ctx = None
        
        # Validate messages - ensure we have at least one non-system message
        non_system_messages = [m for m in messages if m.role != "system"]
        if not non_system_messages:
            log.error("runner.call_llm.no_messages", {
                "total_messages": len(messages),
                "session_id": self.session.id,
            })
            self._end_observability(generation_ctx, trace_ctx, output="No valid messages", level="ERROR")
            return StepResult(action="stop", content="", error="No valid messages to send to LLM")
        
        log.debug("runner.call_llm.messages", {
            "total": len(messages),
            "non_system": len(non_system_messages),
            "roles": [m.role for m in messages],
        })
        
        # Emit start event
        await processor.process_event(StartEvent())
        
        # Lightweight counters instead of storing all chunks in memory
        chunk_counts = {"total": 0, "reasoning": 0, "text": 0, "tool": 0}
        stream_usage: Optional[Dict[str, int]] = None
        
        # Stream response and convert chunks to events
        provider_tools = None if self._should_use_text_tool_call_mode() else (tools if tools else None)
        if provider_tools is None and tools:
            log.info("runner.text_tool_call_mode.enabled", {
                "session_id": self.session.id,
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "tool_count": len(tools),
            })

        async for chunk in _iter_with_chunk_timeout(
            provider.chat_stream(
                model_id=self.model_id,
                messages=messages,
                tools=provider_tools,
                **provider_options,
            ),
            timeout_s=LLM_STREAM_CHUNK_TIMEOUT_S,
        ):
            chunk_counts["total"] += 1
            
            chunk_finish = getattr(chunk, 'finish_reason', None)
            if chunk_finish:
                stream_finish_reason = chunk_finish
            
            # Capture usage from chunk (providers may include it in the final chunk)
            if hasattr(chunk, 'usage') and chunk.usage:
                stream_usage = chunk.usage
            
            # Check for abort
            if self.is_aborted:
                break
            
            # Determine event type from chunk
            event_type = getattr(chunk, 'event_type', None)
            
            if event_type == 'reasoning' or (hasattr(chunk, 'reasoning') and chunk.reasoning):
                reasoning_text = chunk.reasoning if hasattr(chunk, 'reasoning') else chunk.delta
                if reasoning_text:
                    chunk_counts["reasoning"] += 1
                    log.debug("runner.reasoning.received", {
                        "length": len(reasoning_text),
                        "text_preview": reasoning_text[:50],
                    })
                    # Generate reasoning ID if needed
                    if not hasattr(self, '_current_reasoning_id'):
                        reasoning_id_counter += 1
                        self._current_reasoning_id = f"reasoning-{reasoning_id_counter}"
                        await processor.process_event(ReasoningStartEvent(
                            id=self._current_reasoning_id
                        ))
                    
                    # Send reasoning delta
                    await processor.process_event(ReasoningDeltaEvent(
                        id=self._current_reasoning_id,
                        text=reasoning_text,
                    ))
                continue
            elif hasattr(self, '_current_reasoning_id'):
                # End current reasoning block
                await processor.process_event(ReasoningEndEvent(
                    id=self._current_reasoning_id
                ))
                delattr(self, '_current_reasoning_id')
            
            if hasattr(chunk, 'delta') and chunk.delta:
                chunk_counts["text"] += 1
                if not text_started:
                    await processor.process_event(TextStartEvent())
                    text_started = True
                
                await processor.process_event(TextDeltaEvent(
                    text=chunk.delta,
                ))
            
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                chunk_counts["tool"] += 1
                for tc in chunk.tool_calls:
                    await tool_accumulator.feed_chunk(tc)
        
        log.info("runner.stream.summary", {
            "total_chunks": chunk_counts["total"],
            "reasoning_chunks": chunk_counts["reasoning"],
            "text_chunks": chunk_counts["text"],
            "tool_chunks": chunk_counts["tool"],
            "had_reasoning": chunk_counts["reasoning"] > 0,
            "finish_reason": stream_finish_reason,
            "agent": agent.name,
        })

        await tool_accumulator.flush_remaining(stream_finish_reason)
        
        # End text block if started
        if text_started:
            await processor.process_event(TextEndEvent())
        
        # End any remaining reasoning block
        if hasattr(self, '_current_reasoning_id'):
            await processor.process_event(ReasoningEndEvent(
                id=self._current_reasoning_id
            ))
            delattr(self, '_current_reasoning_id')
        
        # Emit finish event
        await processor.process_event(FinishEvent(
            finish_reason=processor.get_finish_reason()
        ))
        
        # Get processed content
        content = processor.get_text_content()
        reasoning = processor.get_reasoning_content()
        
        # Update message tokens if provider reported usage
        if stream_usage:
            tokens_update = {
                "input": stream_usage.get("prompt_tokens", 0),
                "output": stream_usage.get("completion_tokens", 0),
                "reasoning": 0,
                "cache": {
                    "read": stream_usage.get("cache_read_input_tokens", 0),
                    "write": stream_usage.get("cache_creation_input_tokens", 0),
                },
            }
            try:
                await Message.update(
                    self.session.id,
                    assistant_msg.id,
                    tokens=tokens_update,
                )
                log.info("runner.stream.usage_captured", {
                    "input": tokens_update["input"],
                    "output": tokens_update["output"],
                    "total": stream_usage.get("total_tokens", 0),
                })
            except Exception as e:
                log.warn("runner.stream.usage_update_failed", {"error": str(e)})
        
        # Log summary
        log.info("runner.stream.complete", {
            "text_length": len(content),
            "reasoning_length": len(reasoning),
            "tool_calls": len(processor.tool_calls),
            "usage": stream_usage,
        })
        
        # Update assistant message with content
        if content:
            await Message.update(
                self.session.id,
                assistant_msg.id,
                content=content,
            )
        
        # Note: Tools were already executed synchronously during streaming
        # Build tool call list for result
        tool_calls_for_result = [
            ToolCall(
                id=tc_state.id,
                name=tc_state.name,
                arguments=tc_state.input,
            )
            for tc_state in processor.tool_calls.values()
        ]
        
        if tool_calls_for_result:
            self._end_observability(
                generation_ctx, trace_ctx,
                output={
                    "content_preview": content[:600],
                    "content_chars": len(content),
                    "reasoning_chars": len(reasoning),
                    "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls_for_result[:30]],
                },
                usage=stream_usage,
                metadata={
                    "finish_reason": processor.get_finish_reason(),
                    "status": "continue_with_tools",
                    "tool_call_count": len(tool_calls_for_result),
                },
                trace_output={
                    "status": "ok",
                    "next_action": "continue",
                    "finish_reason": processor.get_finish_reason(),
                    "tool_call_count": len(tool_calls_for_result),
                },
            )
            return StepResult(
                action="continue",
                content=content,
                tool_calls=tool_calls_for_result,
            )
        
        self._end_observability(
            generation_ctx, trace_ctx,
            output={
                "content_preview": content[:600],
                "content_chars": len(content),
                "reasoning_chars": len(reasoning),
            },
            usage=stream_usage,
            metadata={
                "finish_reason": processor.get_finish_reason(),
                "status": "stop",
                "tool_call_count": 0,
            },
            trace_output={
                "status": "ok",
                "next_action": "stop",
                "finish_reason": processor.get_finish_reason(),
            },
        )
        return StepResult(action="stop", content=content)
    
    @staticmethod
    def _end_observability(
        generation_ctx: Any,
        trace_ctx: Any,
        *,
        output: Any = None,
        usage: Any = None,
        metadata: Any = None,
        level: Optional[str] = None,
        trace_output: Any = None,
    ) -> None:
        """Safely end observability scopes. Never raises."""
        try:
            if generation_ctx is not None:
                gen_kwargs: Dict[str, Any] = {}
                if output is not None:
                    gen_kwargs["output"] = output
                if usage is not None:
                    gen_kwargs["usage"] = usage
                if metadata is not None:
                    gen_kwargs["metadata"] = metadata
                if level is not None:
                    gen_kwargs["level"] = level
                generation_ctx.end(**gen_kwargs)
        except Exception as _gen_err:
            log.debug("runner.observability.generation_end_failed", {"error": str(_gen_err)})
        try:
            if trace_ctx is not None:
                tr_kwargs: Dict[str, Any] = {}
                if trace_output is not None:
                    tr_kwargs["output"] = trace_output
                elif output is not None:
                    tr_kwargs["output"] = output
                if level is not None:
                    tr_kwargs["level"] = level
                trace_ctx.end(**tr_kwargs)
        except Exception as _tr_err:
            log.debug("runner.observability.trace_end_failed", {"error": str(_tr_err)})

    async def _handle_permission(self, request) -> None:
        """Handle permission request."""
        if self.callbacks.on_permission_request:
            allowed = await self.callbacks.on_permission_request(request)
            if not allowed:
                raise PermissionError(f"Permission denied: {request.permission}")
            return

        tool_metadata = get_tool_catalog_metadata(str(getattr(request, "permission", "") or ""))
        if self.callbacks.event_publish_callback:
            await self.callbacks.event_publish_callback("runtime.permission_gate", {
                "sessionID": self.session.id,
                "step": self._step,
                "toolName": getattr(request, "permission", ""),
                "alwaysLoad": tool_metadata.always_load,
                "patterns": list(getattr(request, "patterns", None) or []),
            })

        from flocks.permission.next import PermissionNext
        from flocks.permission.rule import PermissionRule, PermissionLevel

        session_rules: List[PermissionRule] = []
        for rule in getattr(self.session, "permission", None) or []:
            raw_level = getattr(rule, "action", None) or getattr(rule, "level", None) or "ask"
            try:
                level = PermissionLevel(str(raw_level))
            except Exception:
                level = PermissionLevel.ASK
            session_rules.append(PermissionRule(
                permission=getattr(rule, "permission", "*"),
                level=level,
                pattern=getattr(rule, "pattern", "*"),
            ))

        metadata = dict(getattr(request, "metadata", None) or {})
        metadata.setdefault("messageID", getattr(request, "message_id", "") or "")
        metadata.setdefault("sessionID", self.session.id)

        await PermissionNext.ask(
            session_id=self.session.id,
            permission=request.permission,
            patterns=list(getattr(request, "patterns", None) or []),
            ruleset=session_rules,
            metadata=metadata,
            always=list(getattr(request, "always", None) or []),
            tool={"name": request.permission},
        )


async def run_session(
    session: SessionInfo,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    callbacks: Optional[RunnerCallbacks] = None,
) -> Optional[MessageInfo]:
    """
    Run a session to completion.

    Delegates to SessionLoop which is the single authoritative execution path.

    Args:
        session: Session to run
        provider_id: Provider ID
        model_id: Model ID
        agent_name: Agent name
        callbacks: RunnerCallbacks (wrapped into LoopCallbacks)

    Returns:
        Last assistant message
    """
    from flocks.session.session_loop import SessionLoop, LoopCallbacks

    loop_callbacks = LoopCallbacks(
        runner_callbacks=callbacks,
        event_publish_callback=callbacks.event_publish_callback if callbacks else None,
    )
    result = await SessionLoop.run(
        session_id=session.id,
        provider_id=provider_id,
        model_id=model_id,
        agent_name=agent_name,
        callbacks=loop_callbacks,
    )
    return result.last_message
