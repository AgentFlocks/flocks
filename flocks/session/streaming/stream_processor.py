"""
Stream Processor

Processes streaming LLM responses with synchronous tool execution.
Ported from Flocks' SessionProcessor pattern.
"""

import json
import re
import asyncio
import time as _time
from datetime import datetime
from typing import Dict, Any, Optional, List, AsyncIterator, Callable, Awaitable
from dataclasses import dataclass

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.message import (
    Message,
    MessageInfo,
    MessageRole,
    PartTime,
    TextPart,
    ReasoningPart,
    ToolPart,
    ToolStatePending,
    ToolStateRunning,
    ToolStateCompleted,
    ToolStateError,
)
from flocks.session.streaming.stream_events import (
    StreamEvent,
    ToolCallEvent,
    ReasoningStartEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    ToolInputStartEvent,
)
from flocks.tool.registry import ToolRegistry, ToolContext, ToolResult
from flocks.permission import PermissionNext
from flocks.agent.agent import AgentInfo
from flocks.utils.langfuse import span_scope
from flocks.session.core.defaults import DOOM_LOOP_THRESHOLD


log = Log.create(service="session.stream_processor")


# Note: Using message part types from message.py instead of custom class


def _resolve_tool_error(result: ToolResult) -> str:
    """Prefer an explicit tool error, then fall back to captured output."""
    if result.error:
        return result.error

    metadata_output = ""
    if isinstance(result.metadata, dict):
        metadata_output = str(result.metadata.get("output") or "").strip()
    if metadata_output:
        return metadata_output

    if isinstance(result.output, str):
        output_text = result.output.strip()
        if output_text:
            return output_text

    return "Unknown error"


@dataclass
class ToolCallState:
    """State for tracking tool calls"""
    id: str
    name: str
    input: Dict[str, Any]
    part_id: str
    status: str = "pending"  # "pending", "running", "completed", "error"
    output: Optional[str] = None
    error: Optional[str] = None


class StreamProcessor:
    """
    Stream processor for LLM responses
    
    Handles streaming events and executes tools synchronously.
    Ported from Flocks' SessionProcessor behavior.
    """
    
    def __init__(
        self,
        session_id: str,
        assistant_message: MessageInfo,
        agent: AgentInfo,
        permission_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        text_delta_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        reasoning_delta_callback: Optional[Callable[[str], Awaitable[None]]] = None,
        tool_start_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        tool_end_callback: Optional[Callable[[str, ToolResult], Awaitable[None]]] = None,
        event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
        config_data: Optional[Dict[str, Any]] = None,
        session_key: Optional[str] = None,
        main_session_key: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        langfuse_generation: Optional[Any] = None,
        step_index: Optional[int] = None,
    ):
        self.session_id = session_id
        self.assistant_message = assistant_message
        self.agent = agent
        self.permission_callback = permission_callback
        self.text_delta_callback = text_delta_callback
        self.reasoning_delta_callback = reasoning_delta_callback
        self.tool_start_callback = tool_start_callback
        self.tool_end_callback = tool_end_callback
        self.event_publish_callback = event_publish_callback
        self._config_data = config_data
        self._session_key = session_key or session_id
        self._main_session_key = main_session_key or session_id
        self._workspace_dir = workspace_dir
        self._langfuse_generation = langfuse_generation
        self._step_index = step_index
        self._sandbox_runtime_cache = None
        self._sandbox_config_cache = None
        self._sandbox_context_cache = None
        self._sandbox_context_resolved = False
        
        # Track message parts
        self.parts: List[Any] = []  # TextPart, ReasoningPart, ToolPart
        self.current_text_part: Optional[TextPart] = None
        self.current_reasoning_part: Optional[ReasoningPart] = None
        self.reasoning_parts: Dict[str, ReasoningPart] = {}
        
        # Track tool calls
        self.tool_calls: Dict[str, ToolCallState] = {}
        self._last_visible_text: str = ""
        
        # Throttle event publishing for text/reasoning deltas to reduce network overhead
        # Send updates at most every 50ms during streaming
        self._last_text_event_time: float = 0
        self._last_reasoning_event_time: Dict[str, float] = {}  # Track per reasoning ID
        self._text_event_throttle_ms: float = 50
        self.recent_tool_signatures: List[tuple[str, str]] = []
        
        # Finish state
        self.finish_reason: Optional[str] = None
        
        # Flag to stop processing more tool calls (for doom loop prevention)
        self._stop_tool_processing = False
        
        # Flag: model emitted text-embedded tool calls (<tool_use> XML) that were extracted and executed
        self._text_tool_calls_executed = False
    
    async def process_event(self, event: StreamEvent) -> None:
        """
        Process a single stream event
        
        Args:
            event: Stream event to process
        """
        event_type = event.type
        
        # Debug log for reasoning events
        if event_type.startswith("reasoning"):
            log.debug("stream.event.processing", {
                "event_type": event_type,
                "has_text": hasattr(event, 'text') and bool(getattr(event, 'text', None)),
            })
        
        if event_type == "start":
            log.debug("stream.start", {"session_id": self.session_id})
        
        elif event_type == "reasoning-start":
            await self._handle_reasoning_start(event)
        
        elif event_type == "reasoning-delta":
            await self._handle_reasoning_delta(event)
        
        elif event_type == "reasoning-end":
            await self._handle_reasoning_end(event)
        
        elif event_type == "tool-input-start":
            await self._handle_tool_input_start(event)
        
        elif event_type == "tool-input-delta":
            pass  # Just track incremental input
        
        elif event_type == "tool-input-end":
            pass  # Input is complete
        
        elif event_type == "tool-call":
            await self._handle_tool_call(event)
        
        elif event_type == "text-start":
            await self._handle_text_start(event)
        
        elif event_type == "text-delta":
            await self._handle_text_delta(event)
        
        elif event_type == "text-end":
            await self._handle_text_end(event)
        
        elif event_type == "finish":
            incoming_reason = event.finish_reason
            # If this step extracted and executed tool calls from inline XML text,
            # override "stop" so the session loop knows to continue with tool results.
            if self._text_tool_calls_executed and incoming_reason == "stop":
                self.finish_reason = "tool-calls"
                log.info("stream.finish_reason_overridden", {
                    "original": incoming_reason,
                    "overridden_to": "tool-calls",
                    "session_id": self.session_id,
                })
            else:
                self.finish_reason = incoming_reason
            log.info("stream.finish", {
                "session_id": self.session_id,
                "finish_reason": self.finish_reason,
            })
    
    async def _handle_reasoning_start(self, event: ReasoningStartEvent) -> None:
        """Handle reasoning block start"""
        part = ReasoningPart(
            id=Identifier.create("part"),
            sessionID=self.session_id,
            messageID=self.assistant_message.id,
            text="",
            time=PartTime(start=int(datetime.now().timestamp() * 1000)),
            metadata=event.metadata or {},
        )
        self.reasoning_parts[event.id] = part
        self.parts.append(part)
        
        # Publish part created event (matches Flocks Session.updatePart)
        if self.event_publish_callback:
            await self.event_publish_callback("message.part.updated", {
                "part": {
                    "id": part.id,
                    "messageID": part.messageID,
                    "sessionID": part.sessionID,
                    "type": "reasoning",
                    "text": part.text,
                    "time": {"start": part.time.start},
                }
            })
        
        log.info("stream.reasoning.start", {"reasoning_id": event.id})
    
    async def _handle_reasoning_delta(self, event: ReasoningDeltaEvent) -> None:
        """Handle reasoning content delta with throttling"""
        log.debug("stream.reasoning.delta.received", {
            "reasoning_id": event.id,
            "text_len": len(event.text),
        })
        if event.id in self.reasoning_parts:
            part = self.reasoning_parts[event.id]
            part.text += event.text
            
            # Update metadata if provided
            if event.metadata:
                if part.metadata is None:
                    part.metadata = {}
                part.metadata.update(event.metadata)
            
            # Call reasoning delta callback for CLI display
            if self.reasoning_delta_callback and event.text:
                try:
                    await self.reasoning_delta_callback(event.text)
                except Exception as e:
                    log.error("stream.reasoning_delta_callback.error", {"error": str(e)})
            
            # Throttle event publishing similar to text deltas
            if self.event_publish_callback and event.text:
                current_time = _time.time() * 1000
                last_time = self._last_reasoning_event_time.get(event.id, 0)
                time_since_last = current_time - last_time
                
                # Always publish first few characters, then throttle
                should_publish = (
                    len(part.text) <= 50 or  # First ~50 chars always publish
                    time_since_last >= self._text_event_throttle_ms  # Or throttle interval passed
                )
                
                if should_publish:
                    self._last_reasoning_event_time[event.id] = current_time
                    log.debug("stream.reasoning.publishing_delta", {
                        "delta_len": len(event.text),
                        "total_len": len(part.text),
                        "reasoning_id": event.id,
                        "throttled": time_since_last < self._text_event_throttle_ms,
                    })
                    await self.event_publish_callback("message.part.updated", {
                        "part": {
                            "id": part.id,
                            "messageID": part.messageID,
                            "sessionID": part.sessionID,
                            "type": "reasoning",
                            "text": part.text,
                            "time": {"start": part.time.start},
                        },
                        "delta": event.text,
                    })
                    log.debug("stream.reasoning.delta.published", {
                        "part_id": part.id,
                        "delta": event.text[:20],
                    })
    
    async def _handle_reasoning_end(self, event: ReasoningEndEvent) -> None:
        """Handle reasoning block end"""
        if event.id in self.reasoning_parts:
            part = self.reasoning_parts[event.id]
            part.text = part.text.rstrip()
            
            if event.metadata:
                if part.metadata is None:
                    part.metadata = {}
                part.metadata.update(event.metadata)
            
            # Update time
            if part.time:
                part.time.end = int(datetime.now().timestamp() * 1000)
            
            # Store the completed reasoning part to database
            await Message.store_part(self.session_id, self.assistant_message.id, part)
            
            # Publish final reasoning part (matches Flocks Session.updatePart)
            if self.event_publish_callback:
                await self.event_publish_callback("message.part.updated", {
                    "part": {
                        "id": part.id,
                        "messageID": part.messageID,
                        "sessionID": part.sessionID,
                        "type": "reasoning",
                        "text": part.text,
                        "time": {"start": part.time.start, "end": part.time.end},
                    }
                })
            
            log.info("stream.reasoning.end", {
                "reasoning_id": event.id,
                "length": len(part.text),
            })
            
            # Remove from active tracking
            del self.reasoning_parts[event.id]
    
    async def _handle_tool_input_start(self, event: ToolInputStartEvent) -> None:
        """
        Handle tool input start - create and store pending part
        
        Ported from original exact logic:
        - Create pending ToolPart immediately when tool-input-start is received
        - Store it to Message._parts so it's available for doom loop detection
        """
        # Reuse existing part_id if already tracked, otherwise create new
        if event.id in self.tool_calls:
            part_id = self.tool_calls[event.id].part_id
        else:
            part_id = Identifier.create("part")
        
        # Create pending tool call state in memory
        self.tool_calls[event.id] = ToolCallState(
            id=event.id,
            name=event.tool_name,
            input={},
            part_id=part_id,
            status="pending",
        )
        
        # Create and store pending ToolPart (like Flocks's Session.updatePart)
        try:
            tool_part = ToolPart(
                id=part_id,
                sessionID=self.session_id,
                messageID=self.assistant_message.id,
                type="tool",
                callID=event.id,
                tool=event.tool_name,
                state=ToolStatePending(
                    status="pending",
                    input={},
                    raw="",
                ),
            )
            await Message.store_part(self.session_id, self.assistant_message.id, tool_part)
            
            # Publish pending tool event (matches Flocks Session.updatePart)
            if self.event_publish_callback:
                await self.event_publish_callback("message.part.updated", {
                    "part": {
                        "id": part_id,
                        "messageID": self.assistant_message.id,
                        "sessionID": self.session_id,
                        "type": "tool",
                        "callID": event.id,
                        "tool": event.tool_name,
                        "state": {
                            "status": "pending",
                            "input": {},
                            "raw": "",
                        }
                    }
                })
            
            log.debug("stream.tool_input.start", {
                "tool_call_id": event.id,
                "tool_name": event.tool_name,
                "part_id": part_id,
            })
        except Exception as e:
            log.error("stream.tool_input_start.store_part_failed", {"error": str(e)})
    
    async def _handle_tool_call(self, event: ToolCallEvent) -> None:
        """
        Handle tool call - execute tool synchronously
        
        This is the key difference from the old implementation:
        tools are executed immediately upon receiving the tool-call event,
        not batched after streaming completes.
        """
        tool_call_id = event.tool_call_id
        tool_name = event.tool_name
        tool_input = event.input
        
        # Check if we should stop processing tool calls (doom loop prevention)
        if self._stop_tool_processing:
            log.warn("stream.tool_call.skipped", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "reason": "doom_loop_prevention_active"
            })
            return
        
        # Guard: skip if this exact tool_call_id was already executed
        existing = self.tool_calls.get(tool_call_id)
        if existing and existing.status in ("completed", "error"):
            log.warn("stream.tool_call.skipped", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "reason": "already_executed",
                "previous_status": existing.status,
            })
            return
        
        log.info("stream.tool_call", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
        })
        
        # Get existing tool call state (should exist from tool-input-start)
        # If not, create it (fallback for safety)
        if tool_call_id not in self.tool_calls:
            log.warn("stream.tool_call.no_input_start", {
                "tool_call_id": tool_call_id,
                "message": "tool-call received without tool-input-start, creating state"
            })
            part_id = Identifier.create("part")
            self.tool_calls[tool_call_id] = ToolCallState(
                id=tool_call_id,
                name=tool_name,
                input=tool_input,
                part_id=part_id,
                status="pending",
            )
        
        tool_state = self.tool_calls[tool_call_id]
        tool_state.input = tool_input
        tool_state.status = "running"
        
        # Update ToolPart to running state (like Flocks's Session.updatePart)
        # This matches Flocks's logic: update existing part from pending to running
        tool_start_time = int(datetime.now().timestamp() * 1000)
        try:
            # Update the existing ToolPart with running state and actual input
            tool_part = ToolPart(
                id=tool_state.part_id,
                sessionID=self.session_id,
                messageID=self.assistant_message.id,
                type="tool",
                callID=tool_call_id,
                tool=tool_name,
                state=ToolStateRunning(
                    status="running",
                    input=tool_input,
                    time={"start": tool_start_time},
                ),
            )
            await Message.store_part(self.session_id, self.assistant_message.id, tool_part)
            
            # Publish running tool event (matches Flocks Session.updatePart)
            if self.event_publish_callback:
                await self.event_publish_callback("message.part.updated", {
                    "part": {
                        "id": tool_state.part_id,
                        "messageID": self.assistant_message.id,
                        "sessionID": self.session_id,
                        "type": "tool",
                        "callID": tool_call_id,
                        "tool": tool_name,
                        "state": {
                            "status": "running",
                            "input": tool_input,
                            "time": {"start": tool_start_time},
                        }
                    }
                })
            
        except Exception as e:
            log.error("stream.tool_call.update_part_failed", {"error": str(e)})
        
        # Doom loop detection - check recent tool calls in assistant message
        # Ported from original exact logic: check parts of current assistant message
        try:
            # Get all parts of current assistant message from storage
            parts = await Message.parts(self.assistant_message.id, self.session_id)
            
            recent_parts = parts[-DOOM_LOOP_THRESHOLD:]
            
            if (
                len(recent_parts) == DOOM_LOOP_THRESHOLD and
                all(
                    p.type == "tool" and
                    p.tool == tool_name and
                    p.state.status != "pending" and
                    json.dumps(p.state.input, sort_keys=True) == json.dumps(tool_input, sort_keys=True)
                    for p in recent_parts
                )
            ):
                log.warn("stream.doom_loop_detected", {
                    "tool": tool_name,
                    "count": DOOM_LOOP_THRESHOLD,
                    "input": tool_input,
                })
                
                # Stop processing further tool calls to prevent infinite loop
                self._stop_tool_processing = True
                
                log.warn("stream.doom_loop.prevented", {
                    "tool": tool_name,
                    "threshold": DOOM_LOOP_THRESHOLD,
                    "message": "Skipping redundant tool call - will be handled in next step with full context"
                })
                
                # Skip this tool call (don't execute, don't mark as error)
                # The tool result from previous call will be available in next step
                return
        except Exception as e:
            log.debug("stream.doom_loop_check_failed", {"error": str(e)})
        
        # Notify tool start (for CLI display)
        if self.tool_start_callback:
            try:
                await self.tool_start_callback(tool_name, tool_input)
            except Exception as e:
                log.error("stream.tool_start_callback.error", {"error": str(e)})
        
        # Hook pipeline: tool.execute.before
        try:
            from flocks.hooks.pipeline import HookPipeline
            hook_ctx = await HookPipeline.run_tool_before({
                "sessionID": self.session_id,
                "agent": self.agent.name,
                "tool": {
                    "name": tool_name,
                    "input": tool_input,
                    "callID": tool_call_id,
                },
            })
            if hook_ctx and isinstance(hook_ctx.input, dict):
                updated = hook_ctx.input.get("tool", {}).get("input")
                if isinstance(updated, dict):
                    tool_input = updated
            hook_skip = hook_ctx.output.get("skip") if hook_ctx else False
        except Exception as e:
            log.error("stream.tool_before_hook.error", {"error": str(e)})
            hook_skip = False

        # Execute tool synchronously
        tool_span_ctx = None
        try:
            tool_span_ctx = span_scope(
                parent=self._langfuse_generation,
                name=f"Tool.execute.{tool_name}",
                input=tool_input,
                metadata={
                    "session_id": self.session_id,
                    "message_id": self.assistant_message.id,
                    "call_id": tool_call_id,
                    "agent": self.agent.name,
                    "step": self._step_index,
                    "session_step": f"{self.session_id}:{self._step_index}" if self._step_index is not None else None,
                },
            )
        except Exception as exc:
            log.debug("stream.tool_span.init_failed", {"error": str(exc)})
        try:
            if hook_skip:
                result = ToolResult(
                    success=False,
                    error="Tool execution blocked by hook",
                )
            else:
                sandbox_meta = await self._resolve_sandbox_meta(tool_name)
                if sandbox_meta["blocked"]:
                    result = ToolResult(
                        success=False,
                        error=sandbox_meta["error"],
                        metadata={"sandbox": True, "blocked_by_policy": True},
                    )
                else:
                    def _make_metadata_cb(
                        _part_id=tool_state.part_id,
                        _call_id=tool_call_id,
                        _tool=tool_name,
                        _input=tool_input,
                        _start=tool_start_time,
                    ):
                        import copy

                        _finished = [False]

                        def _cb(metadata: Dict[str, Any]):
                            if _finished[0]:
                                return
                            snapshot = copy.deepcopy(metadata)
                            state_dict = {
                                "status": "running",
                                "input": _input,
                                "time": {"start": _start},
                                "metadata": snapshot,
                            }
                            if snapshot.get("title"):
                                state_dict["title"] = snapshot["title"]
                            if self.event_publish_callback:
                                async def _safe_publish():
                                    try:
                                        await self.event_publish_callback(
                                            "message.part.updated",
                                            {
                                                "part": {
                                                    "id": _part_id,
                                                    "messageID": self.assistant_message.id,
                                                    "sessionID": self.session_id,
                                                    "type": "tool",
                                                    "callID": _call_id,
                                                    "tool": _tool,
                                                    "state": state_dict,
                                                }
                                            },
                                        )
                                    except Exception as exc:
                                        log.debug("stream.metadata_publish.error", {"error": str(exc)})
                                asyncio.create_task(_safe_publish())

                            # Persist updated running state so metadata (e.g. sessionId)
                            # survives page reload / session switch
                            async def _persist_running_metadata():
                                if _finished[0]:
                                    return
                                try:
                                    running_state = ToolStateRunning(
                                        status="running",
                                        input=_input,
                                        title=snapshot.get("title"),
                                        metadata=snapshot,
                                        time={"start": _start},
                                    )
                                    part = ToolPart(
                                        id=_part_id,
                                        sessionID=self.session_id,
                                        messageID=self.assistant_message.id,
                                        type="tool",
                                        callID=_call_id,
                                        tool=_tool,
                                        state=running_state,
                                    )
                                    await Message.store_part(
                                        self.session_id,
                                        self.assistant_message.id,
                                        part,
                                    )
                                except Exception as exc:
                                    log.debug("stream.metadata_persist.error", {"error": str(exc)})
                            asyncio.create_task(_persist_running_metadata())

                        _cb.mark_finished = lambda: _finished.__setitem__(0, True)
                        return _cb

                    ctx = ToolContext(
                        session_id=self.session_id,
                        message_id=self.assistant_message.id,
                        agent=self.agent.name,
                        call_id=tool_call_id,
                        permission_callback=self.permission_callback,
                        extra=sandbox_meta["extra"],
                        metadata_callback=_make_metadata_cb(),
                        event_publish_callback=self.event_publish_callback,
                    )
                    
                    result = await ToolRegistry.execute(
                        tool_name=tool_name,
                        ctx=ctx,
                        **tool_input
                    )

                    # Mark metadata callback as finished so pending async persist
                    # tasks won't overwrite the upcoming completed/error state
                    cb = ctx._metadata_callback
                    if cb and hasattr(cb, 'mark_finished'):
                        cb.mark_finished()

            # Hook pipeline: tool.execute.after
            try:
                from flocks.hooks.pipeline import HookPipeline
                hook_ctx = await HookPipeline.run_tool_after({
                    "sessionID": self.session_id,
                    "agent": self.agent.name,
                    "tool": {
                        "name": tool_name,
                        "input": tool_input,
                        "callID": tool_call_id,
                    },
                    "result": result.model_dump(),
                })
                if hook_ctx and isinstance(hook_ctx.output, dict):
                    override = hook_ctx.output.get("result")
                    if isinstance(override, dict):
                        result = ToolResult(**override)
            except Exception as e:
                log.error("stream.tool_after_hook.error", {"error": str(e)})
            
            # Update tool state
            tool_state.status = "completed" if result.success else "error"
            tool_state.output = result.output if result.success else None
            tool_state.error = result.error if not result.success else None
            
            log.info("stream.tool_call.completed", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "success": result.success,
            })
            output_preview = result.output if result.success else result.error
            if isinstance(output_preview, str):
                output_preview = output_preview[:600]

            try:
                if tool_span_ctx is not None:
                    end_kwargs: Dict[str, Any] = {
                        "output": output_preview,
                        "metadata": {
                            "success": result.success,
                            "title": result.title,
                            "status": tool_state.status,
                        },
                    }
                    if not result.success:
                        end_kwargs["level"] = "ERROR"
                    tool_span_ctx.end(**end_kwargs)
            except Exception as _span_err:
                log.debug("stream.tool_span.end_failed", {"error": str(_span_err)})
            
            # Update ToolPart in storage with completed state (for doom loop detection)
            try:
                tool_end_time = int(datetime.now().timestamp() * 1000)
                
                if result.success:
                    # output can be str, dict, or list - ToolStateCompleted handles all
                    completed_state = ToolStateCompleted(
                        status="completed",
                        input=tool_input,
                        output=result.output if result.output is not None else "",
                        title=result.title or tool_name,
                        metadata=result.metadata or {},
                        time={"start": tool_start_time, "end": tool_end_time},
                    )
                else:
                    resolved_error = _resolve_tool_error(result)
                    completed_state = ToolStateError(
                        status="error",
                        input=tool_input,
                        error=resolved_error,
                        metadata=result.metadata or {},
                        time={"start": tool_start_time, "end": tool_end_time},
                    )
                
                completed_part = ToolPart(
                    id=tool_state.part_id,
                    sessionID=self.session_id,
                    messageID=self.assistant_message.id,
                    type="tool",
                    callID=tool_call_id,
                    tool=tool_name,
                    state=completed_state,
                )
                
                await Message.store_part(self.session_id, self.assistant_message.id, completed_part)
                
                # Publish completed/error tool event (matches Flocks Session.updatePart)
                if self.event_publish_callback:
                    state_dict = {
                        "status": completed_state.status,
                        "input": tool_input,
                        "time": {"start": tool_start_time, "end": tool_end_time},
                    }
                    if result.success:
                        state_dict["output"] = result.output if result.output is not None else ""
                        state_dict["title"] = result.title or tool_name
                        state_dict["metadata"] = result.metadata or {}
                    else:
                        state_dict["error"] = resolved_error
                        state_dict["metadata"] = result.metadata or {}
                    
                    await self.event_publish_callback("message.part.updated", {
                        "part": {
                            "id": tool_state.part_id,
                            "messageID": self.assistant_message.id,
                            "sessionID": self.session_id,
                            "type": "tool",
                            "callID": tool_call_id,
                            "tool": tool_name,
                            "state": state_dict,
                        }
                    })
                
            except Exception as e:
                log.error("stream.tool_call.update_part_failed", {"error": str(e)})
            
            # Notify tool end (for CLI display)
            if self.tool_end_callback:
                try:
                    await self.tool_end_callback(tool_name, result)
                except Exception as e:
                    log.error("stream.tool_end_callback.error", {"error": str(e)})
            
        except Exception as e:
            log.error("stream.tool_call.error", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "error": str(e),
            })
            try:
                if tool_span_ctx is not None:
                    tool_span_ctx.end(
                        output=str(e),
                        metadata={"success": False},
                        level="ERROR",
                        status_message="tool_exception",
                    )
            except Exception as _span_err:
                log.debug("stream.tool_span.error_end_failed", {"error": str(_span_err)})
            
            tool_state.status = "error"
            tool_state.error = str(e)
            
            # Update ToolPart in storage with error state
            try:
                tool_error_time = int(datetime.now().timestamp() * 1000)
                error_state = ToolStateError(
                    status="error",
                    input=tool_input,
                    error=str(e),
                    time={"start": tool_start_time if 'tool_start_time' in locals() else tool_error_time, "end": tool_error_time},
                )
                
                error_part = ToolPart(
                    id=tool_state.part_id,
                    sessionID=self.session_id,
                    messageID=self.assistant_message.id,
                    type="tool",
                    callID=tool_call_id,
                    tool=tool_name,
                    state=error_state,
                )
                await Message.store_part(self.session_id, self.assistant_message.id, error_part)
                
                # Publish error tool event (matches Flocks Session.updatePart)
                if self.event_publish_callback:
                    await self.event_publish_callback("message.part.updated", {
                        "part": {
                            "id": tool_state.part_id,
                            "messageID": self.assistant_message.id,
                            "sessionID": self.session_id,
                            "type": "tool",
                            "callID": tool_call_id,
                            "tool": tool_name,
                            "state": {
                                "status": "error",
                                "input": tool_input,
                                "error": str(e),
                                "time": {"start": tool_start_time if 'tool_start_time' in locals() else tool_error_time, "end": tool_error_time},
                            }
                        }
                    })
                
            except Exception as store_e:
                log.error("stream.tool_call.update_part_failed", {"error": str(store_e)})
            
            # Create error result for callback
            error_result = ToolResult(
                success=False,
                output="",
                error=str(e),
                title="",
                metadata={},
            )
            
            # Notify tool end with error (for CLI display)
            if self.tool_end_callback:
                try:
                    await self.tool_end_callback(tool_name, error_result)
                except Exception as e2:
                    log.error("stream.tool_end_callback.error", {"error": str(e2)})

    async def _load_config_data(self) -> Dict[str, Any]:
        """Load and cache config as plain dict."""
        if isinstance(self._config_data, dict):
            return self._config_data
        try:
            from flocks.config import Config

            cfg = await Config.get()
            self._config_data = cfg.model_dump(by_alias=True, exclude_none=True)
        except Exception as e:
            log.warn("stream.sandbox.config_load_failed", {"error": str(e)})
            self._config_data = {}
        return self._config_data or {}

    async def _resolve_sandbox_meta(self, tool_name: str) -> Dict[str, Any]:
        """
        Resolve sandbox metadata for tool execution.

        Returns:
            Dict with keys:
            - blocked: bool
            - error: Optional[str]
            - extra: Dict[str, Any]
        """
        result: Dict[str, Any] = {"blocked": False, "error": None, "extra": {}}
        try:
            from flocks.sandbox.context import resolve_sandbox_context
            from flocks.sandbox.config import resolve_sandbox_config_for_agent
            from flocks.sandbox.runtime_status import resolve_sandbox_runtime_status
            from flocks.sandbox.tool_policy import is_tool_allowed
            from flocks.sandbox.types import BashSandboxConfig

            if self._sandbox_runtime_cache is None:
                config_data = await self._load_config_data()
                self._sandbox_runtime_cache = resolve_sandbox_runtime_status(
                    config_data=config_data,
                    session_key=self._session_key,
                    agent_id=self.agent.name,
                    main_session_key=self._main_session_key,
                )
            runtime = self._sandbox_runtime_cache

            if not runtime.sandboxed:
                return result

            if self._sandbox_config_cache is None:
                config_data = await self._load_config_data()
                self._sandbox_config_cache = resolve_sandbox_config_for_agent(
                    config_data=config_data,
                    agent_id=self.agent.name,
                )

            if not is_tool_allowed(runtime.tool_policy, tool_name):
                result["blocked"] = True
                result["error"] = (
                    f"Tool '{tool_name}' is blocked by sandbox tool policy. "
                    "Update sandbox.tools.allow/deny in ~/.flocks/config/flocks.json if needed."
                )
                return result

            # Sandbox metadata is needed for sandbox-aware tools, including workflow
            # entrypoint so workflow runtime can execute python nodes in sandbox.
            if tool_name not in {"bash", "read", "write", "edit", "run_workflow"}:
                return result

            if not self._sandbox_context_resolved:
                config_data = await self._load_config_data()
                self._sandbox_context_cache = await resolve_sandbox_context(
                    config_data=config_data,
                    session_key=self._session_key,
                    agent_id=self.agent.name,
                    main_session_key=self._main_session_key,
                    workspace_dir=self._workspace_dir,
                )
                self._sandbox_context_resolved = True
            sandbox_ctx = self._sandbox_context_cache
            if not sandbox_ctx:
                return result

            sandbox = BashSandboxConfig(
                container_name=sandbox_ctx.container_name,
                workspace_dir=sandbox_ctx.workspace_dir,
                container_workdir=sandbox_ctx.container_workdir,
                env=sandbox_ctx.docker.env,
            )
            result["extra"] = {
                "sandbox": {
                    **sandbox.model_dump(exclude_none=True),
                    "workspace_access": sandbox_ctx.workspace_access,
                    "agent_workspace_dir": sandbox_ctx.agent_workspace_dir,
                }
            }
            elevated_cfg = getattr(self._sandbox_config_cache, "elevated", None)
            if elevated_cfg and elevated_cfg.enabled:
                elevated_tools = elevated_cfg.tools or ["bash"]
                result["extra"]["sandbox_elevated"] = {
                    "enabled": True,
                    "tools": elevated_tools,
                }
            return result
        except Exception as e:
            log.warn("stream.sandbox.resolve_failed", {"tool": tool_name, "error": str(e)})
            return result
    
    async def _handle_text_start(self, event: TextStartEvent) -> None:
        """Handle text block start"""
        log.info("stream.text.start", {"session_id": self.session_id})
        self._last_visible_text = ""
        
        self.current_text_part = TextPart(
            id=Identifier.create("part"),
            sessionID=self.session_id,
            messageID=self.assistant_message.id,
            text="",
            time=PartTime(start=int(datetime.now().timestamp() * 1000)),
            metadata=event.metadata or {},
        )
        self.parts.append(self.current_text_part)
        
        # Publish part created event (matches Flocks Session.updatePart)
        if self.event_publish_callback:
            await self.event_publish_callback("message.part.updated", {
                "part": {
                    "id": self.current_text_part.id,
                    "messageID": self.current_text_part.messageID,
                    "sessionID": self.current_text_part.sessionID,
                    "type": "text",
                    "text": self.current_text_part.text,
                    "time": {"start": self.current_text_part.time.start},
                }
            })
    
    async def _handle_text_delta(self, event: TextDeltaEvent) -> None:
        """Handle text content delta with throttling to reduce network/render overhead"""
        if self.current_text_part:
            self.current_text_part.text += event.text
            visible_text = self._sanitize_streaming_text_for_display(self.current_text_part.text)
            visible_delta = self._compute_visible_delta(self._last_visible_text, visible_text)
            
            if len(self.current_text_part.text) <= 100 or len(self.current_text_part.text) % 100 < len(event.text):
                log.debug("stream.text.delta", {
                    "delta_length": len(event.text),
                    "total_length": len(self.current_text_part.text),
                })
            
            # Call text delta callback if provided (for CLI display)
            if self.text_delta_callback and visible_delta:
                await self.text_delta_callback(visible_delta)
            
            # Update metadata if provided
            if event.metadata:
                if self.current_text_part.metadata is None:
                    self.current_text_part.metadata = {}
                self.current_text_part.metadata.update(event.metadata)
            
            # Throttle event publishing to reduce network overhead and TUI render thrashing
            # Only publish if enough time has passed since last event (50ms throttle)
            # This dramatically improves TUI responsiveness during streaming
            if self.event_publish_callback:
                current_time = _time.time() * 1000
                time_since_last = current_time - self._last_text_event_time
                
                # Always publish first few characters to show immediate feedback
                should_publish = (
                    len(self.current_text_part.text) <= 50 or  # First ~50 chars always publish
                    time_since_last >= self._text_event_throttle_ms  # Or throttle interval passed
                )
                
                if should_publish:
                    self._last_text_event_time = current_time
                    if len(self.current_text_part.text) <= 100 or len(self.current_text_part.text) % 100 < len(event.text):
                        log.debug("stream.text.publishing_delta", {
                            "delta_len": len(visible_delta),
                            "part_id": self.current_text_part.id,
                            "throttled": time_since_last < self._text_event_throttle_ms,
                        })
                    self._last_visible_text = visible_text
                    await self.event_publish_callback("message.part.updated", {
                        "part": {
                            "id": self.current_text_part.id,
                            "messageID": self.current_text_part.messageID,
                            "sessionID": self.current_text_part.sessionID,
                            "type": "text",
                            "text": visible_text,
                            "time": {"start": self.current_text_part.time.start},
                        },
                        "delta": visible_delta,
                    })
            else:
                log.warn("stream.text.no_publish_callback", {})
    
    async def _handle_text_end(self, event: TextEndEvent) -> None:
        """Handle text block end"""
        if self.current_text_part:
            self.current_text_part.text = self.current_text_part.text.rstrip()
            
            if event.metadata:
                if self.current_text_part.metadata is None:
                    self.current_text_part.metadata = {}
                self.current_text_part.metadata.update(event.metadata)
            
            # Intercept text-embedded tool calls produced by models that hallucinate
            # tool invocations as text rather than using the native API tool-calling
            # mechanism.  Two formats are supported:
            #   1. XML:  <tool_use>{"name":"…","input":{…}}</tool_use>
            #   2. JSON: [{"tool_name":"…","parameters":{…}}]
            # Parse, execute, and strip them so the conversation loop continues
            # correctly and no raw markup/JSON appears in the UI.
            raw_text = self.current_text_part.text
            all_text_tool_calls: list[dict] = []
            cleaned = raw_text

            if "<tool_use>" in cleaned or "<minimax:tool_call>" in cleaned:
                cleaned, xml_calls = self._parse_xml_text_tool_calls(cleaned)
                all_text_tool_calls.extend(xml_calls)

            json_cleaned, json_calls = self._parse_json_text_tool_calls(cleaned)
            if json_calls:
                cleaned = json_cleaned
                all_text_tool_calls.extend(json_calls)

            if cleaned != raw_text:
                self.current_text_part.text = cleaned

            if all_text_tool_calls:
                log.warn("stream.text_tool_calls_detected", {
                    "count": len(all_text_tool_calls),
                    "names": [tc["name"] for tc in all_text_tool_calls],
                    "session_id": self.session_id,
                })
                for tc in all_text_tool_calls:
                    if not self._stop_tool_processing:
                        await self._handle_tool_call(ToolCallEvent(
                            tool_call_id=tc["id"],
                            tool_name=tc["name"],
                            input=tc["input"],
                        ))
                self._text_tool_calls_executed = True
            elif "<tool_use>" in raw_text or "<minimax:tool_call>" in raw_text:
                log.warn("stream.text_tool_use_xml_stripped", {
                    "session_id": self.session_id,
                    "reason": "found XML tool-call markup but could not parse any valid tool calls",
                })
            
            # Update time
            if self.current_text_part.time:
                self.current_text_part.time.end = int(datetime.now().timestamp() * 1000)
            
            # Store the completed text part to database
            await Message.store_part(self.session_id, self.assistant_message.id, self.current_text_part)
            
            # Publish final text part (matches Flocks Session.updatePart)
            if self.event_publish_callback:
                await self.event_publish_callback("message.part.updated", {
                    "part": {
                        "id": self.current_text_part.id,
                        "messageID": self.current_text_part.messageID,
                        "sessionID": self.current_text_part.sessionID,
                        "type": "text",
                        "text": self.current_text_part.text,
                        "time": {"start": self.current_text_part.time.start, "end": self.current_text_part.time.end},
                    }
                })
            
            log.debug("stream.text.end", {
                "length": len(self.current_text_part.text),
            })
            
            self.current_text_part = None

    @staticmethod
    def _compute_visible_delta(previous: str, current: str) -> str:
        if current.startswith(previous):
            return current[len(previous):]
        return current

    @staticmethod
    def _sanitize_streaming_text_for_display(text: str) -> str:
        visible = text

        block_patterns = [
            re.compile(r"<tool_use>.*?</tool_use>", re.DOTALL),
            re.compile(r"<tool_result>.*?</tool_result>", re.DOTALL),
            re.compile(r"<minimax:tool_call>.*?</minimax:tool_call>", re.DOTALL),
        ]
        for pattern in block_patterns:
            visible = pattern.sub("", visible)

        open_blocks = [
            ("<tool_use>", "</tool_use>"),
            ("<tool_result>", "</tool_result>"),
            ("<minimax:tool_call>", "</minimax:tool_call>"),
        ]
        truncate_at: Optional[int] = None
        for start_tag, end_tag in open_blocks:
            start = visible.find(start_tag)
            while start != -1:
                end = visible.find(end_tag, start + len(start_tag))
                if end == -1:
                    truncate_at = start if truncate_at is None else min(truncate_at, start)
                    break
                start = visible.find(start_tag, end + len(end_tag))

        if truncate_at is not None:
            visible = visible[:truncate_at]

        visible = re.sub(r"\n{3,}", "\n\n", visible)
        return visible.rstrip()
    
    def _parse_xml_text_tool_calls(self, text: str) -> tuple[str, list[dict]]:
        """
        Detect and extract <tool_use> XML blocks from text content.

        Supports two body formats inside <tool_use>...</tool_use>:
          - JSON:  {"name": "...", "input": {...}}  (various key aliases)
          - XML:   <tool_name>...</tool_name> <parameters>...</parameters>

        Returns:
            (cleaned_text, tool_calls)
        """
        tool_calls: list[dict] = []

        tool_use_re = re.compile(r'<tool_use>(.*?)</tool_use>', re.DOTALL)
        minimax_tool_call_re = re.compile(
            r'<minimax:tool_call>(.*?)</minimax:tool_call>',
            re.DOTALL,
        )
        tool_result_re = re.compile(r'<tool_result>.*?</tool_result>', re.DOTALL)

        for match in tool_use_re.finditer(text):
            body = match.group(1).strip()
            parsed: dict | None = None

            # Try JSON body first
            try:
                data = json.loads(body)
                name = next(
                    (data[k] for k in ("name", "tool_name", "tool") if k in data and data[k]),
                    None,
                )
                raw_input = next(
                    (data[k] for k in ("input", "parameters", "arguments") if k in data),
                    {},
                )
                if isinstance(raw_input, str):
                    try:
                        raw_input = json.loads(raw_input)
                    except Exception:
                        raw_input = {}
                if not isinstance(raw_input, dict):
                    raw_input = {}
                if name:
                    parsed = {"name": str(name), "input": raw_input}
            except (json.JSONDecodeError, ValueError):
                pass

            # Fall back to XML sub-tags: <tool_name>...</tool_name>
            if not parsed:
                name_match = re.search(r'<tool_name>(.*?)</tool_name>', body, re.DOTALL)
                params_match = re.search(r'<parameters>(.*?)</parameters>', body, re.DOTALL)
                if name_match:
                    name = name_match.group(1).strip()
                    params_raw = params_match.group(1).strip() if params_match else "{}"
                    try:
                        raw_input = json.loads(params_raw)
                    except (json.JSONDecodeError, ValueError):
                        raw_input = {}
                    parsed = {"name": name, "input": raw_input}

            if parsed:
                tool_calls.append({
                    "id": Identifier.create("call"),
                    "name": parsed["name"],
                    "input": parsed["input"],
                })

        for match in minimax_tool_call_re.finditer(text):
            body = match.group(1).strip()
            invoke_match = re.search(r'<invoke\s+name="([^"]+)">(.*?)</invoke>', body, re.DOTALL)
            if not invoke_match:
                continue

            name = invoke_match.group(1).strip()
            invoke_body = invoke_match.group(2)
            raw_input: dict[str, object] = {}

            for param_match in re.finditer(
                r'<parameter\s+name="([^"]+)">(.*?)</parameter>',
                invoke_body,
                re.DOTALL,
            ):
                param_name = param_match.group(1).strip()
                param_value = param_match.group(2).strip()

                parsed_value: object = param_value
                if param_value:
                    try:
                        parsed_value = json.loads(param_value)
                    except (json.JSONDecodeError, ValueError):
                        parsed_value = param_value

                raw_input[param_name] = parsed_value

            if name:
                tool_calls.append({
                    "id": Identifier.create("call"),
                    "name": name,
                    "input": raw_input,
                })

        # Strip <tool_use> and <tool_result> blocks from visible text
        cleaned = tool_use_re.sub("", text)
        cleaned = minimax_tool_call_re.sub("", cleaned)
        cleaned = tool_result_re.sub("", cleaned)
        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()

        return cleaned, tool_calls

    def _parse_json_text_tool_calls(self, text: str) -> tuple[str, list[dict]]:
        """
        Detect and extract JSON-array tool calls from text content.

        Models sometimes hallucinate tool calls as inline JSON arrays:
          [{"tool_name": "__mcp_web_search", "parameters": {"keyword": "..."}}]

        Uses bracket-matching to correctly handle nested braces inside
        parameter objects, then validates each candidate with json.loads.

        Returns:
            (cleaned_text, tool_calls)
        """
        tool_calls: list[dict] = []
        spans_to_remove: list[tuple[int, int]] = []

        i = 0
        while i < len(text):
            start = text.find("[", i)
            if start == -1:
                break

            peek_end = min(start + 500, len(text))
            peek = text[start:peek_end]
            if not re.search(r'"(?:tool_name|name|tool)"\s*:', peek):
                i = start + 1
                continue

            end = self._find_matching_bracket(text, start)
            if end == -1:
                i = start + 1
                continue

            candidate = text[start:end + 1]
            try:
                arr = json.loads(candidate)
            except (json.JSONDecodeError, ValueError):
                i = end + 1
                continue

            if not isinstance(arr, list) or not arr:
                i = end + 1
                continue

            parsed_calls: list[dict] = []
            for item in arr:
                if not isinstance(item, dict):
                    continue
                name = next(
                    (item[k] for k in ("tool_name", "name", "tool") if k in item and item[k]),
                    None,
                )
                if not name or not isinstance(name, str):
                    continue
                raw_input = next(
                    (item[k] for k in ("parameters", "input", "arguments") if k in item),
                    {},
                )
                if isinstance(raw_input, str):
                    try:
                        raw_input = json.loads(raw_input)
                    except Exception:
                        raw_input = {}
                if not isinstance(raw_input, dict):
                    raw_input = {}
                parsed_calls.append({
                    "id": Identifier.create("call"),
                    "name": name,
                    "input": raw_input,
                })

            if parsed_calls:
                tool_calls.extend(parsed_calls)
                spans_to_remove.append((start, end + 1))

            i = end + 1

        if not spans_to_remove:
            return text, tool_calls

        result = text
        for s, e in reversed(spans_to_remove):
            result = result[:s] + result[e:]
        result = re.sub(r'\n{3,}', '\n\n', result).strip()
        return result, tool_calls

    @staticmethod
    def _find_matching_bracket(text: str, start: int) -> int:
        """Find the position of ] that closes [ at *start*, respecting strings."""
        depth = 0
        in_string = False
        i = start
        while i < len(text):
            ch = text[i]
            if in_string:
                if ch == '\\':
                    i += 2
                    continue
                if ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == '[':
                    depth += 1
                elif ch == ']':
                    depth -= 1
                    if depth == 0:
                        return i
            i += 1
        return -1

    def get_text_content(self) -> str:
        """Get all text content from parts"""
        text_parts = [p for p in self.parts if isinstance(p, TextPart)]
        return "".join(p.text for p in text_parts)
    
    def get_reasoning_content(self) -> str:
        """Get all reasoning content from parts"""
        reasoning_parts = [p for p in self.parts if isinstance(p, ReasoningPart)]
        return "".join(p.text for p in reasoning_parts)
    
    def has_tool_calls(self) -> bool:
        """Check if there are any tool calls"""
        return len(self.tool_calls) > 0
    
    def get_finish_reason(self) -> str:
        """Get finish reason"""
        if self.finish_reason:
            return self.finish_reason
        
        # Infer from state
        if self.has_tool_calls():
            return "tool-calls"
        
        return "stop"
