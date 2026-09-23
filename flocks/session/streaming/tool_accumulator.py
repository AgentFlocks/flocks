"""
Tool call accumulator for streaming LLM responses.

Manages the stateful process of accumulating JSON fragments from
streamed tool call chunks, parsing them, and dispatching execution
events through the StreamProcessor.

Extracted from runner._call_llm to improve readability and testability.
"""

import json
from typing import Any, Optional

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.utils.json_repair import (
    parse_json_robust as _parse_json_robust,
    repair_truncated_json,
)
from flocks.tool.registry import ToolRegistry
from flocks.session.streaming.stream_events import (
    ToolInputStartEvent,
    ToolInputErrorEvent,
    ToolCallEvent,
)

log = Log.create(service="tool_accumulator")


class StreamToolArgumentsTruncatedError(RuntimeError):
    """Raised when the stream ends before tool arguments form valid JSON."""

    def __init__(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        finish_reason: str,
        arguments_len: int,
        arguments_preview: str,
    ) -> None:
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.finish_reason = finish_reason
        self.arguments_len = arguments_len
        self.arguments_preview = arguments_preview
        super().__init__(
            "Model output was truncated while generating tool arguments "
            f"for '{tool_name}' (finish_reason='{finish_reason}', "
            f"{arguments_len} chars). The tool was not executed."
        )


def _looks_truncated(accumulated_args: str) -> bool:
    """True when the arguments are valid JSON that simply stops early.

    Closing the open strings / brackets makes it parse, which is the signature
    of a stream that was cut mid-argument. Text that stays unparseable after
    that is malformed for some other reason and keeps going down the existing
    invalid-tool path.
    """
    repaired = repair_truncated_json(accumulated_args)
    if repaired == accumulated_args:
        return False
    _, repaired_ok = _parse_json_robust(repaired)
    return repaired_ok


class ToolCallAccumulator:
    """Accumulates streamed tool-call JSON fragments and dispatches execution.

    Typical lifecycle per tool call:
      1. ``feed_chunk()``  — called for each streaming chunk
      2. When JSON is complete and required params present → emits events
      3. ``flush_remaining()`` — after stream ends, processes leftovers
    """

    def __init__(self, processor: Any) -> None:
        self._processor = processor
        self._accumulator: dict[str, dict[str, Any]] = {}
        self._index_to_id: dict[int, str] = {}

    async def feed_chunk(self, tc: dict[str, Any]) -> None:
        """Process a single tool-call chunk from the provider stream."""
        tc_index = tc.get("index", 0)
        provider_tc_id = tc.get("id")

        # Keep one stable internal id for the lifetime of a streamed tool call.
        # Some providers reveal the tool name before their call id; replacing a
        # generated id later would disconnect the already-published pending UI
        # part from the eventual ToolCallEvent.
        if tc_index in self._index_to_id:
            tc_id = self._index_to_id[tc_index]
        elif provider_tc_id:
            tc_id = provider_tc_id
            self._index_to_id[tc_index] = tc_id
        else:
            tc_id = Identifier.create("call")
            self._index_to_id[tc_index] = tc_id

        tool_name = tc.get("function", {}).get("name") or ""
        args_str = tc.get("function", {}).get("arguments") or ""

        if tc_id in self._accumulator and self._accumulator[tc_id].get("completed"):
            return

        if tc_id not in self._accumulator:
            self._accumulator[tc_id] = {
                "id": tc_id,
                "name": tool_name,
                "arguments_str": "",
                "completed": False,
            }

        if tool_name:
            self._accumulator[tc_id]["name"] = tool_name

        if args_str and not self._accumulator[tc_id].get("completed"):
            current_args = self._accumulator[tc_id]["arguments_str"]
            if current_args:
                if not self._should_accumulate(tc_id, current_args):
                    return
            self._accumulator[tc_id]["arguments_str"] += args_str

        accumulated = self._accumulator[tc_id]["arguments_str"]
        final_name = self._accumulator[tc_id]["name"]

        # Publish the tool step as soon as its name is known. Parameters can be
        # large (especially write/edit content), so waiting for valid JSON here
        # would hide the tool until input generation — and often execution —
        # was nearly complete.
        if final_name and not self._accumulator[tc_id].get("input_started"):
            await self._processor.process_event(
                ToolInputStartEvent(id=tc_id, tool_name=final_name)
            )
            self._accumulator[tc_id]["input_started"] = True

        if accumulated and final_name:
            arguments, ok = _parse_json_robust(accumulated)
            if ok:
                schema = ToolRegistry.get_schema(final_name)
                if schema:
                    missing = [p for p in schema.required if p not in arguments]
                    if missing:
                        self._accumulator[tc_id]["awaiting_required"] = True
                        return

                if final_name:
                    await self._processor.process_event(
                        ToolCallEvent(
                            tool_call_id=tc_id,
                            tool_name=final_name,
                            input=arguments,
                        )
                    )
                    self._accumulator[tc_id]["completed"] = True

    async def flush_remaining(
        self,
        stream_finish_reason: Optional[str] = None,
    ) -> None:
        """Process any tool calls still in the accumulator after the stream ends."""
        is_truncated = stream_finish_reason in ("length", "max_tokens")
        truncation_error: StreamToolArgumentsTruncatedError | None = None

        for tc_id, tc_data in list(self._accumulator.items()):
            if tc_data.get("completed") or tc_data.get("failed"):
                continue
            accumulated_args = tc_data.get("arguments_str", "")
            tool_name = tc_data.get("name", "")
            if not tool_name:
                continue

            if is_truncated:
                if accumulated_args:
                    detail = (
                        f"Tool arguments for '{tool_name}' cut off at "
                        f"{len(accumulated_args)} chars."
                    )
                else:
                    detail = f"Tool arguments for '{tool_name}' were not completed."
                error_msg = (
                    f"Output was truncated (finish_reason='{stream_finish_reason}'). "
                    f"{detail} The tool was not executed."
                )
                await self._processor.process_event(
                    ToolInputErrorEvent(
                        id=tc_id,
                        tool_name=tool_name,
                        input={
                            "tool": tool_name,
                            "arguments_preview": accumulated_args[:500],
                            "finish_reason": stream_finish_reason,
                        },
                        error=error_msg,
                    )
                )
                tc_data["failed"] = True
                if truncation_error is None:
                    truncation_error = StreamToolArgumentsTruncatedError(
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                        finish_reason=str(stream_finish_reason),
                        arguments_len=len(accumulated_args),
                        arguments_preview=accumulated_args[:500],
                    )
                continue

            if not accumulated_args:
                continue

            arguments, ok = _parse_json_robust(accumulated_args)
            if not ok and _looks_truncated(accumulated_args):
                # The JSON has an unterminated string or unclosed bracket, i.e.
                # the stream stopped mid-argument. Providers only sometimes say
                # so via finish_reason ("stop" or None is common when a gateway
                # drops the connection, and the Anthropic SDK path never reports
                # "length" at all), so the shape of the JSON is the reliable
                # signal. Repairing it and executing anyway silently wrote half
                # a file and reported success.
                detail = (
                    f"Tool arguments for '{tool_name}' stopped mid-value at "
                    f"{len(accumulated_args)} chars (unterminated JSON)."
                )
                error_msg = (
                    f"Output was truncated (finish_reason={stream_finish_reason!r}). "
                    f"{detail} The tool was not executed."
                )
                await self._processor.process_event(
                    ToolInputErrorEvent(
                        id=tc_id,
                        tool_name=tool_name,
                        input={
                            "tool": tool_name,
                            "arguments_preview": accumulated_args[:500],
                            "finish_reason": stream_finish_reason,
                        },
                        error=error_msg,
                    )
                )
                tc_data["failed"] = True
                if truncation_error is None:
                    truncation_error = StreamToolArgumentsTruncatedError(
                        tool_call_id=tc_id,
                        tool_name=tool_name,
                        finish_reason=str(stream_finish_reason),
                        arguments_len=len(accumulated_args),
                        arguments_preview=accumulated_args[:500],
                    )
                continue

            if ok:
                if not tc_data.get("input_started"):
                    await self._processor.process_event(
                        ToolInputStartEvent(id=tc_id, tool_name=tool_name)
                    )
                await self._processor.process_event(
                    ToolCallEvent(
                        tool_call_id=tc_id, tool_name=tool_name, input=arguments,
                    )
                )
                continue

            # --- Repair strategies ---
            repaired = await self._try_repair(
                tc_id, tool_name, accumulated_args, tc_data,
            )
            if repaired:
                continue

            # All strategies failed — redirect to invalid tool
            error_msg = (
                f"Failed to parse tool arguments ({len(accumulated_args)} chars). "
                f"Please ensure valid JSON with balanced braces/brackets."
            )

            await self._processor.process_event(
                ToolCallEvent(
                    tool_call_id=tc_id,
                    tool_name="invalid",
                    input={
                        "tool": tool_name,
                        "error": error_msg,
                        "arguments_preview": accumulated_args[:500],
                    },
                )
            )

        if truncation_error is not None:
            raise truncation_error

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _should_accumulate(self, tc_id: str, current_args: str) -> bool:
        """Return True if we should keep accumulating JSON fragments."""
        try:
            parsed = json.loads(current_args)
            if self._accumulator[tc_id].get("awaiting_required"):
                return True
            tc_tool = self._accumulator[tc_id].get("name", "")
            if tc_tool and isinstance(parsed, dict):
                schema = ToolRegistry.get_schema(tc_tool)
                if schema:
                    missing = [p for p in schema.required if p not in parsed]
                    if missing:
                        self._accumulator[tc_id]["awaiting_required"] = True
                        return True
            return False
        except json.JSONDecodeError:
            return True

    async def _try_repair(
        self,
        tc_id: str,
        tool_name: str,
        accumulated_args: str,
        tc_data: dict[str, Any],
    ) -> bool:
        """Attempt multiple repair strategies. Return True if repaired.

        Only the tool *name* is repaired here. Padding truncated JSON back into
        shape and executing it is what let a cut-off ``write`` land on disk as a
        half file; ``flush_remaining`` now routes that case to the truncation
        path before this method is reached.
        """
        jsons_to_try = [accumulated_args]

        async def _try_exec(candidate: str, variants: list[str]) -> bool:
            for v in variants:
                args, ok = _parse_json_robust(v)
                if ok:
                    schema = ToolRegistry.get_schema(candidate)
                    if schema and [p for p in schema.required if p not in args]:
                        continue
                    if not tc_data.get("input_started"):
                        await self._processor.process_event(
                            ToolInputStartEvent(id=tc_id, tool_name=candidate)
                        )
                    await self._processor.process_event(
                        ToolCallEvent(
                            tool_call_id=tc_id, tool_name=candidate, input=args,
                        )
                    )
                    log.info("tool_accumulator.repair.success", {
                        "original": tool_name, "repaired": candidate,
                    })
                    return True
            return False

        # Strategy 1: case sensitivity
        lower = tool_name.lower()
        if lower != tool_name and ToolRegistry.get(lower) is not None:
            if await _try_exec(lower, jsons_to_try):
                return True

        # Strategy 2: fuzzy match (edit distance ≤ 1)
        similar = _find_similar_tool(tool_name)
        if similar:
            if await _try_exec(similar, jsons_to_try):
                return True

        # Strategy 3: original name with JSON variants
        if ToolRegistry.get(tool_name) is not None:
            if await _try_exec(tool_name, jsons_to_try):
                return True

        return False


def _find_similar_tool(tool_name: str) -> Optional[str]:
    """Find a registered tool name within edit distance 1."""
    all_tools = [t.name for t in ToolRegistry.list_tools()]

    for t in all_tools:
        if t.lower() == tool_name.lower():
            return t

    def _edit_dist(s1: str, s2: str) -> int:
        if abs(len(s1) - len(s2)) > 1:
            return 999
        if len(s1) < len(s2):
            s1, s2 = s2, s1
        prev = list(range(len(s2) + 1))
        for i, c1 in enumerate(s1):
            curr = [i + 1]
            for j, c2 in enumerate(s2):
                curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
            prev = curr
        return prev[-1]

    for t in all_tools:
        if _edit_dist(tool_name.lower(), t.lower()) <= 1:
            return t
    return None
