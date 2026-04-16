"""
Cherry Provider — client-side tool calling for OpenAI-compatible endpoints.

Works with backends that do NOT support server-side tool calling (e.g. vLLM
without ``--enable-auto-tool-choice``).  Instead of passing ``tools`` in the
API request, the provider injects tool definitions into the system prompt and
instructs the model to emit ``<tool_use>`` XML blocks in its text output.

The downstream ``StreamProcessor`` already understands ``<tool_use>`` blocks
and will parse / execute them transparently.

Multi-turn handling
-------------------
After the first tool call, the runner's ``_to_chat_messages`` converts stored
tool-call parts to structured ``tool_calls`` (on assistant messages) and
``role="tool"`` result messages.  Since we never send ``tools`` in the API
request, the backend would reject those.  ``_convert_tool_messages_to_text``
rewrites them to plain text (``<tool_use>`` / ``<tool_result>``) so the
backend sees a clean, text-only conversation at every turn.
"""

import json
from typing import Any, AsyncIterator, Dict, List

from flocks.provider.provider import (
    ChatMessage,
    ChatResponse,
    StreamChunk,
)
from flocks.provider.sdk.openai_compatible import OpenAICompatibleProvider
from flocks.utils.log import Log

log = Log.create(service="provider.cherry")

# ------------------------------------------------------------------ #
#  Tool-prompt builder                                                 #
# ------------------------------------------------------------------ #

_TOOL_USE_INSTRUCTIONS = """\
You have access to a set of tools.  When you decide to call a tool, output \
exactly one XML block in the following format (do NOT use native API tool-calling):

<tool_use>
{"name": "<tool_name>", "input": {<arguments as JSON object>}}
</tool_use>

Rules:
- Output ONE tool call per <tool_use> block.  You may emit multiple blocks if \
you need to call several tools.
- Always wait for the tool result before continuing your response.
- Parameter values must be valid JSON.
- Only call tools listed below — never invent tool names.
- Tool results will be returned inside <tool_result> blocks."""


def _build_tools_system_prompt(tools: List[Dict[str, Any]]) -> str:
    """Convert an OpenAI-style tool schema list to a human-readable prompt."""
    parts: list[str] = [_TOOL_USE_INSTRUCTIONS, "", "## Available Tools", ""]

    for tool in tools:
        fn = tool.get("function", {})
        name = fn.get("name", "")
        if not name:
            continue
        description = (fn.get("description") or "").strip()
        params = fn.get("parameters") or {}
        properties = params.get("properties", {}) if isinstance(params, dict) else {}
        required_set = set(params.get("required", [])) if isinstance(params, dict) else set()

        parts.append(f"### {name}")
        if description:
            parts.append(description)

        if properties:
            parts.append("Parameters:")
            for pname, spec in properties.items():
                ptype = spec.get("type", "any") if isinstance(spec, dict) else "any"
                pdesc = spec.get("description", "") if isinstance(spec, dict) else ""
                req_tag = "required" if pname in required_set else "optional"
                line = f"- `{pname}` ({ptype}, {req_tag})"
                if pdesc:
                    line += f": {pdesc}"
                parts.append(line)

            example_args: Dict[str, Any] = {}
            for pname, spec in properties.items():
                if not isinstance(spec, dict):
                    continue
                ptype = spec.get("type", "string")
                enum_vals = spec.get("enum")
                if enum_vals:
                    example_args[pname] = enum_vals[0]
                elif ptype == "string":
                    example_args[pname] = f"<{pname}>"
                elif ptype == "integer":
                    example_args[pname] = 0
                elif ptype == "number":
                    example_args[pname] = 0.0
                elif ptype == "boolean":
                    example_args[pname] = True
                elif ptype == "array":
                    example_args[pname] = []
                elif ptype == "object":
                    example_args[pname] = {}
                else:
                    example_args[pname] = f"<{pname}>"

            parts.append("Example:")
            parts.append("<tool_use>")
            parts.append(json.dumps({"name": name, "input": example_args}, ensure_ascii=False))
            parts.append("</tool_use>")
        parts.append("")

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  Multi-turn message converter                                        #
# ------------------------------------------------------------------ #


def _convert_tool_messages_to_text(
    messages: List[ChatMessage],
) -> List[ChatMessage]:
    """Rewrite structured tool_calls / role="tool" messages to plain text.

    The runner's ``_to_chat_messages`` always produces OpenAI-structured
    tool interactions (assistant ``tool_calls`` + ``role="tool"`` results).
    This function converts them back so the backend never sees fields that
    require server-side tool-call support.

    Consecutive ``role="tool"`` results are merged into a single ``user``
    message to avoid issues with backends that reject consecutive same-role
    messages.
    """
    converted: list[ChatMessage] = []
    pending_results: list[str] = []

    def _flush_results() -> None:
        nonlocal pending_results
        if pending_results:
            converted.append(ChatMessage(
                role="user",
                content="\n\n".join(pending_results),
            ))
            pending_results = []

    for m in messages:
        if m.role == "assistant" and m.tool_calls:
            _flush_results()
            text_parts: list[str] = []
            content = m.content
            if content:
                text_parts.append(content if isinstance(content, str) else str(content))

            for tc in m.tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                args_raw = fn.get("arguments", "{}")
                try:
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                except (json.JSONDecodeError, ValueError):
                    args = args_raw
                tc_json = json.dumps(
                    {"name": name, "input": args},
                    ensure_ascii=False,
                )
                text_parts.append(f"<tool_use>\n{tc_json}\n</tool_use>")

            converted.append(ChatMessage(
                role="assistant",
                content="\n\n".join(text_parts),
            ))

        elif m.role == "tool":
            tool_name = m.name or "unknown"
            result = m.content if isinstance(m.content, str) else str(m.content)
            pending_results.append(
                f"<tool_result>\n"
                f"Tool `{tool_name}` returned:\n"
                f"{result}\n"
                f"</tool_result>"
            )

        else:
            _flush_results()
            converted.append(m)

    _flush_results()
    return converted


# ------------------------------------------------------------------ #
#  CherryProvider                                                      #
# ------------------------------------------------------------------ #


class CherryProvider(OpenAICompatibleProvider):
    """OpenAI-compatible provider with client-side tool calling.

    Inherits connection / streaming logic from ``OpenAICompatibleProvider``
    but never sends ``tools`` in the API request.  Tool definitions are
    injected into the conversation as a system message so the model can
    emit ``<tool_use>`` blocks in plain text.
    """

    def __init__(self):
        super().__init__()
        self.id = "cherry"
        self.name = "Cherry"

    # ---- helpers ---- #

    @staticmethod
    def _inject_tools_into_messages(
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
    ) -> List[ChatMessage]:
        """Return a *new* message list with tool definitions appended to the
        first system message.  If no system message exists, a new one is
        prepended.  The original list is not mutated."""
        tool_prompt = _build_tools_system_prompt(tools)

        new_messages: list[ChatMessage] = []
        appended = False
        for m in messages:
            if not appended and m.role == "system":
                existing = m.content if isinstance(m.content, str) else str(m.content)
                new_messages.append(ChatMessage(
                    role="system",
                    content=f"{existing}\n\n{tool_prompt}",
                ))
                appended = True
            else:
                new_messages.append(m)

        if not appended:
            new_messages.insert(0, ChatMessage(role="system", content=tool_prompt))
        return new_messages

    @staticmethod
    def _prepare_messages(
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
    ) -> List[ChatMessage]:
        """Full message preparation pipeline: convert structured tool
        history to text, then inject tool definitions into the system prompt."""
        messages = _convert_tool_messages_to_text(messages)
        messages = CherryProvider._inject_tools_into_messages(messages, tools)
        return messages

    # ---- overrides ---- #

    async def chat(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs,
    ) -> ChatResponse:
        tools = kwargs.pop("tools", None)
        if tools:
            messages = self._prepare_messages(messages, tools)
            log.info("cherry.chat.tools_injected", {
                "model": model_id,
                "tool_count": len(tools),
            })
        else:
            messages = _convert_tool_messages_to_text(messages)
        return await super().chat(model_id, messages, **kwargs)

    async def chat_stream(
        self,
        model_id: str,
        messages: List[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        tools = kwargs.pop("tools", None)
        if tools:
            messages = self._prepare_messages(messages, tools)
            log.info("cherry.stream.tools_injected", {
                "model": model_id,
                "tool_count": len(tools),
            })
        else:
            messages = _convert_tool_messages_to_text(messages)
        async for chunk in super().chat_stream(model_id, messages, **kwargs):
            yield chunk
