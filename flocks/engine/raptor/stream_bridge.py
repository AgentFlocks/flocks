"""
Raptor StreamBridge

Maps tui_gateway event notifications to Flocks SSE (publish_event) calls
and emits the turn_state SSE events that the WebUI depends on.

tui_gateway events consumed
-----------------------------
message.start    → turn.started SSE + internal state reset
message.delta    → message.part.updated (streaming text delta)
message.complete → turn.stopped SSE + response accumulation
tool.start       → message.part.updated (tool running)
tool.complete    → message.part.updated (tool completed)
tool.generating  → message.part.updated (tool name hint)
approval.request → session.permission SSE
error            → session.error SSE + turn.stopped
status.update    → (logged; no direct SSE equivalent yet)
"""

import time
from typing import Any, Callable, Dict, Optional

from flocks.utils.log import Log
from flocks.session.core.turn_state import set_turn_state
from flocks.utils.id import Identifier

log = Log.create(service="engine.raptor.stream_bridge")

# Type alias
PublishFn = Callable[[str, Dict[str, Any]], Any]


class TurnAccumulator:
    """
    Accumulates per-turn state needed to write back to Flocks storage
    and emit correct SSE events.
    """

    def __init__(self, session_id: str, step: int) -> None:
        self.session_id = session_id
        self.step = step
        self.response_text = ""
        self.tool_events: list = []
        self.message_id: str = Identifier.create("message")
        self.part_id: str = Identifier.create("part")
        self.started_at_ms: int = int(time.time() * 1000)
        self.is_complete = False
        self.error: Optional[str] = None


class StreamBridge:
    """
    Maps tui_gateway events to Flocks SSE events and turn-state updates.

    Instantiated once per RaptorEngine.run() call (one turn or queued-turn
    continuation).  The ``event_callback`` is wired directly into the daemon's
    event dispatch path (called from the daemon reader thread), so all methods
    must be thread-safe.  Heavy work is offloaded to asyncio via
    ``loop.call_soon_threadsafe(asyncio.ensure_future, ...)``.
    """

    def __init__(
        self,
        *,
        session_id: str,
        step: int,
        publish: PublishFn,
        loop: Any,  # asyncio.AbstractEventLoop
    ) -> None:
        self.accumulator = TurnAccumulator(session_id, step)
        self._publish = publish
        self._session_id = session_id
        self._loop = loop

    # ── Public entry point (called from daemon reader thread) ─────────────

    def on_event(self, params: Dict[str, Any]) -> None:
        """Called for every tui_gateway event matching this session."""
        evt_session = params.get("session_id", "")
        if evt_session and evt_session != self.accumulator.session_id:
            # Wrong hermes session id — shouldn't happen with per-session daemon
            # but guard anyway.
            # Note: the hermes session_id (uuid hex) differs from the Flocks
            # session_id; the RaptorEngine stores the mapping so this check
            # uses the hermes sid stored in the accumulator by the caller.
            pass  # checked at Engine level; bridge accepts all from this daemon

        event_type = params.get("type", "")
        payload = params.get("payload") or {}
        self._dispatch(event_type, payload)

    # ── Internal dispatcher ────────────────────────────────────────────────

    def _dispatch(self, event_type: str, payload: Dict[str, Any]) -> None:
        from .protocol import (
            EVT_MESSAGE_START, EVT_MESSAGE_DELTA, EVT_MESSAGE_COMPLETE,
            EVT_TOOL_START, EVT_TOOL_COMPLETE, EVT_TOOL_GENERATING,
            EVT_APPROVAL_REQUEST, EVT_ERROR, EVT_STATUS_UPDATE,
        )
        if event_type == EVT_MESSAGE_START:
            self._on_message_start()
        elif event_type == EVT_MESSAGE_DELTA:
            self._on_message_delta(payload)
        elif event_type == EVT_MESSAGE_COMPLETE:
            self._on_message_complete(payload)
        elif event_type == EVT_TOOL_START:
            self._on_tool_start(payload)
        elif event_type == EVT_TOOL_COMPLETE:
            self._on_tool_complete(payload)
        elif event_type == EVT_TOOL_GENERATING:
            self._on_tool_generating(payload)
        elif event_type == EVT_APPROVAL_REQUEST:
            self._on_approval_request(payload)
        elif event_type == EVT_ERROR:
            self._on_error(payload)
        elif event_type == EVT_STATUS_UPDATE:
            log.debug("raptor.stream.status", {"text": payload.get("text", "")})

    # ── Event handlers ─────────────────────────────────────────────────────

    def _on_message_start(self) -> None:
        acc = self.accumulator
        # Update in-memory turn state (mirrors _run_loop behaviour).
        set_turn_state(
            acc.session_id,
            step=acc.step,
            status="started",
            queued_message_detected=False,
        )
        self._async(self._publish("turn.state", {
            "sessionID": acc.session_id,
            "step": acc.step,
            "status": "started",
        }))

    def _on_message_delta(self, payload: Dict[str, Any]) -> None:
        text = payload.get("text", "")
        if not text:
            return
        acc = self.accumulator
        acc.response_text += text
        self._async(self._publish("message.part.updated", {
            "part": {
                "id": acc.part_id,
                "messageID": acc.message_id,
                "sessionID": acc.session_id,
                "type": "text",
                "text": text,
                "time": {"start": acc.started_at_ms},
            }
        }))

    def _on_message_complete(self, payload: Dict[str, Any]) -> None:
        acc = self.accumulator
        acc.is_complete = True
        response = payload.get("response", "")
        if response and not acc.response_text:
            # Some turns deliver full response only on complete (non-streaming).
            acc.response_text = response
            self._async(self._publish("message.part.updated", {
                "part": {
                    "id": acc.part_id,
                    "messageID": acc.message_id,
                    "sessionID": acc.session_id,
                    "type": "text",
                    "text": response,
                    "time": {"start": acc.started_at_ms,
                             "end": int(time.time() * 1000)},
                }
            }))
        # Emit turn stopped.
        set_turn_state(acc.session_id, step=acc.step, status="stopped")
        self._async(self._publish("turn.state", {
            "sessionID": acc.session_id,
            "step": acc.step,
            "status": "stopped",
        }))

    def _on_tool_start(self, payload: Dict[str, Any]) -> None:
        acc = self.accumulator
        tool_name = payload.get("name", "unknown")
        call_id = payload.get("call_id") or payload.get("id") or Identifier.create("tc")
        now_ms = int(time.time() * 1000)
        part_id = Identifier.create("part")
        acc.tool_events.append({"type": "start", "name": tool_name,
                                 "call_id": call_id, "part_id": part_id})
        self._async(self._publish("message.part.updated", {
            "part": {
                "id": part_id,
                "messageID": acc.message_id,
                "sessionID": acc.session_id,
                "type": "tool",
                "callID": call_id,
                "tool": tool_name,
                "state": {
                    "status": "running",
                    "input": payload.get("params") or payload.get("input") or {},
                    "time": {"start": now_ms},
                },
            }
        }))

    def _on_tool_complete(self, payload: Dict[str, Any]) -> None:
        acc = self.accumulator
        tool_name = payload.get("name", "unknown")
        call_id = payload.get("call_id") or payload.get("id") or ""
        output = payload.get("result") or payload.get("output") or ""
        now_ms = int(time.time() * 1000)
        # Find matching part_id from tool_events
        part_id = Identifier.create("part")
        for evt in reversed(acc.tool_events):
            if evt.get("name") == tool_name and evt.get("type") == "start":
                part_id = evt["part_id"]
                break
        acc.tool_events.append({"type": "complete", "name": tool_name,
                                  "call_id": call_id})
        if not isinstance(output, str):
            import json
            output = json.dumps(output, ensure_ascii=False)
        self._async(self._publish("message.part.updated", {
            "part": {
                "id": part_id,
                "messageID": acc.message_id,
                "sessionID": acc.session_id,
                "type": "tool",
                "callID": call_id,
                "tool": tool_name,
                "state": {
                    "status": "completed",
                    "input": payload.get("params") or payload.get("input") or {},
                    "output": output,
                    "title": tool_name,
                    "metadata": {},
                    "time": {"start": acc.started_at_ms, "end": now_ms},
                },
            }
        }))

    def _on_tool_generating(self, payload: Dict[str, Any]) -> None:
        # LLM is writing out a tool call — low-noise status, no SSE needed.
        log.debug("raptor.stream.tool_generating",
                  {"name": payload.get("name", "")})

    def _on_approval_request(self, payload: Dict[str, Any]) -> None:
        self._async(self._publish("session.permission", {
            "sessionID": self._session_id,
            "tool": payload.get("tool", ""),
            "description": payload.get("description", ""),
            "details": payload,
        }))

    def _on_error(self, payload: Dict[str, Any]) -> None:
        acc = self.accumulator
        acc.error = payload.get("message", "unknown error")
        acc.is_complete = True
        set_turn_state(acc.session_id, step=acc.step, status="stopped",
                       stop_reason="error")
        self._async(self._publish("session.error", {
            "sessionID": acc.session_id,
            "error": {"name": "RaptorError", "message": acc.error},
        }))

    # ── Asyncio helper ─────────────────────────────────────────────────────

    def _async(self, coro: Any) -> None:
        """Schedule a coroutine on the asyncio event loop from any thread."""
        import asyncio
        if self._loop and not self._loop.is_closed():
            asyncio.run_coroutine_threadsafe(coro, self._loop)
