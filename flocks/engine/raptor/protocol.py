"""
Raptor / tui_gateway JSON-RPC Protocol

Helpers to build request payloads and define the event types emitted
by the hermes-agent tui_gateway daemon.

tui_gateway wire format (newline-delimited JSON, no framing)
------------------------------------------------------------
Client → daemon (stdin)::

    {"jsonrpc": "2.0", "id": "req-1", "method": "session.create", "params": {...}}

Daemon → client (stdout)::

    {"jsonrpc": "2.0", "id": "req-1", "result": {"session_id": "abc123", ...}}
    {"jsonrpc": "2.0", "method": "event", "params": {"type": "message.delta",
     "session_id": "abc123", "payload": {"text": "Hello"}}}

Key RPC methods used by RaptorEngine
--------------------------------------
session.create   → params: messages, cwd, system_message, title
prompt.submit    → params: session_id, text
session.interrupt→ params: session_id
session.history  → params: session_id
session.close    → params: session_id

Events emitted by tui_gateway
------------------------------
message.start    — new assistant turn started
message.delta    — streaming text delta      payload: {text: str}
message.complete — turn complete             payload: {response: str, ...}
tool.start       — tool execution started    payload: {name, params, ...}
tool.complete    — tool execution finished   payload: {name, result, ...}
tool.generating  — LLM writing tool call     payload: {name}
approval.request — destructive op needs OK  payload: {description, tool, ...}
error            — session-level error       payload: {message: str}
status.update    — informational status      payload: {kind, text}
"""

import uuid
from typing import Any, Dict, List, Optional


# ── Event type constants ─────────────────────────────────────────────────────

EVT_MESSAGE_START = "message.start"
EVT_MESSAGE_DELTA = "message.delta"
EVT_MESSAGE_COMPLETE = "message.complete"
EVT_TOOL_START = "tool.start"
EVT_TOOL_COMPLETE = "tool.complete"
EVT_TOOL_GENERATING = "tool.generating"
EVT_APPROVAL_REQUEST = "approval.request"
EVT_ERROR = "error"
EVT_STATUS_UPDATE = "status.update"

# Events that signal the end of a prompt.submit turn (normal or error)
TERMINAL_EVENTS = frozenset({EVT_MESSAGE_COMPLETE, EVT_ERROR})


# ── Request builders ─────────────────────────────────────────────────────────

def new_id() -> str:
    return uuid.uuid4().hex[:12]


def req_session_create(
    *,
    messages: List[Dict[str, Any]],
    cwd: str,
    title: str = "",
    system_message: Optional[str] = None,
) -> Dict[str, Any]:
    params: Dict[str, Any] = {
        "messages": messages,
        "cwd": cwd,
        "title": title,
    }
    if system_message:
        params["system_message"] = system_message
    return {"jsonrpc": "2.0", "id": new_id(), "method": "session.create", "params": params}


def req_prompt_submit(*, session_id: str, text: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": new_id(),
        "method": "prompt.submit",
        "params": {"session_id": session_id, "text": text},
    }


def req_session_interrupt(*, session_id: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": new_id(),
        "method": "session.interrupt",
        "params": {"session_id": session_id},
    }


def req_session_close(*, session_id: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": new_id(),
        "method": "session.close",
        "params": {"session_id": session_id},
    }


def req_session_history(*, session_id: str) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": new_id(),
        "method": "session.history",
        "params": {"session_id": session_id},
    }


def req_approval_respond(*, session_id: str, approved: bool) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": new_id(),
        "method": "approval.respond",
        "params": {"session_id": session_id, "approved": approved},
    }
