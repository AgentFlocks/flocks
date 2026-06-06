"""
Raptor MessageBridge

Converts between Flocks MessageInfo/Parts (SQLite storage) and the OpenAI-style
message list that hermes-agent / tui_gateway expects in ``session.create`` →
``messages`` and returns in ``session.history``.

Only TextPart and ToolPart are fully supported in this revision.
FilePart attachments are noted as TODO for a follow-up once the tui_gateway
image-attach API is better understood.
"""

from typing import Any, Dict, List, Optional

from flocks.utils.log import Log

log = Log.create(service="engine.raptor.message_bridge")


def flocks_messages_to_openai(messages_with_parts: List[Any]) -> List[Dict[str, Any]]:
    """
    Convert a list of Flocks MessageWithParts objects to OpenAI-style messages
    for seeding the hermes session history.

    Each Flocks message becomes one or more OpenAI messages:
    - user message  → {"role": "user",      "content": <text>}
    - assistant msg → {"role": "assistant",  "content": <text>,
                        "tool_calls": [...]}   (if it has ToolParts with pending state)
    - tool result   → {"role": "tool",       "tool_call_id": ..., "content": <output>}

    Notes
    -----
    - Synthetic/ignored parts are skipped.
    - Tool parts in completed state produce a follow-up "role=tool" message.
    - Very long text/outputs are left as-is; hermes manages its own compaction.
    """
    result: List[Dict[str, Any]] = []

    for msg_with_parts in messages_with_parts:
        msg = getattr(msg_with_parts, "message", msg_with_parts)
        parts = getattr(msg_with_parts, "parts", [])
        role = getattr(msg, "role", None)
        if hasattr(role, "value"):
            role = role.value

        if role not in ("user", "assistant"):
            continue

        # Gather text blocks and tool calls from the parts.
        text_blocks: List[str] = []
        tool_calls: List[Dict[str, Any]] = []
        tool_results: List[Dict[str, Any]] = []

        for part in parts:
            ptype = getattr(part, "type", None)

            if ptype == "text":
                if getattr(part, "synthetic", False) or getattr(part, "ignored", False):
                    continue
                text_blocks.append(getattr(part, "text", "") or "")

            elif ptype == "tool":
                state = getattr(part, "state", None)
                if state is None:
                    continue
                state_status = getattr(state, "status", "")
                call_id = getattr(part, "callID", "") or getattr(part, "call_id", "")
                tool_name = getattr(part, "tool", "unknown")
                tool_input = getattr(state, "input", {}) or {}

                if state_status in ("pending", "running"):
                    # Record tool call on assistant message.
                    import json
                    tool_calls.append({
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(tool_input, ensure_ascii=False),
                        },
                    })
                elif state_status == "completed":
                    output = getattr(state, "output", "")
                    if not isinstance(output, str):
                        import json
                        output = json.dumps(output, ensure_ascii=False)
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": output,
                    })
                elif state_status == "error":
                    error_msg = getattr(state, "error", "tool error")
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": f"[error] {error_msg}",
                    })

        content = "\n".join(t for t in text_blocks if t)

        if role == "user":
            if content:
                result.append({"role": "user", "content": content})
        elif role == "assistant":
            entry: Dict[str, Any] = {"role": "assistant", "content": content}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            if content or tool_calls:
                result.append(entry)
            # Append tool result messages immediately after assistant.
            result.extend(tool_results)

    return result


async def load_session_history_as_openai(session_id: str) -> List[Dict[str, Any]]:
    """
    Load Flocks session history and convert to OpenAI message format for hermes.
    """
    from flocks.session.message import Message
    try:
        messages_with_parts = await Message.list_with_parts(session_id)
        return flocks_messages_to_openai(messages_with_parts)
    except Exception as exc:
        log.warning("raptor.message_bridge.load_error", {
            "session_id": session_id,
            "error": str(exc),
        })
        return []


async def write_assistant_response(
    *,
    session_id: str,
    response_text: str,
    tool_events: Optional[List[Dict[str, Any]]] = None,
    engine_tag: str = "raptor",
) -> None:
    """
    Write the assistant's response back to Flocks storage (for WebUI rendering).

    Creates one assistant MessageInfo with a single TextPart containing the
    full response text.  Tool call records from tui_gateway events are included
    as ToolParts when provided.

    This is the MessageBridge "write back" path — hermes manages its own
    compressed history internally; here we only persist what the WebUI needs
    to render the conversation.
    """
    from flocks.session.message import Message, MessageRole
    from flocks.utils.id import Identifier
    import time

    if not response_text and not tool_events:
        return

    now_ms = int(time.time() * 1000)
    msg_id = Identifier.create("message")

    try:
        await Message.create(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content=response_text or "",
            id=msg_id,
            time={"created": now_ms},
            metadata={"engine": engine_tag},
        )
    except Exception as exc:
        log.warning("raptor.message_bridge.write_error", {
            "session_id": session_id,
            "error": str(exc),
        })
