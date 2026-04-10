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
    ToolInputDeltaEvent,
    ToolCallEvent,
)

log = Log.create(service="tool_accumulator")

# Tools whose input should be streamed to the UI via ToolInputDeltaEvent
# so users see progress while the model generates large arguments.
_STREAM_INPUT_TOOLS = frozenset({"write", "edit"})


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
        tc_id = tc.get("id")

        if tc_id:
            self._index_to_id[tc_index] = tc_id
        elif tc_index in self._index_to_id:
            tc_id = self._index_to_id[tc_index]
        else:
            tc_id = Identifier.create("call")
            self._index_to_id[tc_index] = tc_id

        tool_name = tc.get("function", {}).get("name") or ""
        args_str = tc.get("function", {}).get("arguments") or ""

        if tc_id in self._accumulator and self._accumulator[tc_id].get("completed"):
            return

        is_new_entry = tc_id not in self._accumulator
        if is_new_entry:
            self._accumulator[tc_id] = {
                "id": tc_id,
                "name": "",
                "arguments_str": "",
                "completed": False,
            }

        if tool_name:
            prev_name = self._accumulator[tc_id]["name"]
            self._accumulator[tc_id]["name"] = tool_name
            # Emit ToolInputStartEvent eagerly as soon as tool name is known,
            # so the UI shows a pending tool card immediately.
            if not prev_name and not self._accumulator[tc_id].get("input_started"):
                await self._processor.process_event(
                    ToolInputStartEvent(id=tc_id, tool_name=tool_name)
                )
                self._accumulator[tc_id]["input_started"] = True

        if args_str and not self._accumulator[tc_id].get("completed"):
            current_args = self._accumulator[tc_id]["arguments_str"]
            if current_args:
                if not self._should_accumulate(tc_id, current_args):
                    return
            self._accumulator[tc_id]["arguments_str"] += args_str

            # Stream input deltas for tools with large arguments (e.g. write)
            final_name = self._accumulator[tc_id]["name"]
            if final_name in _STREAM_INPUT_TOOLS:
                if not self._accumulator[tc_id].get("input_started"):
                    await self._processor.process_event(
                        ToolInputStartEvent(id=tc_id, tool_name=final_name)
                    )
                    self._accumulator[tc_id]["input_started"] = True
                await self._processor.process_event(
                    ToolInputDeltaEvent(id=tc_id, delta=args_str)
                )

        accumulated = self._accumulator[tc_id]["arguments_str"]
        final_name = self._accumulator[tc_id]["name"]
        if accumulated and final_name:
            arguments, ok = _parse_json_robust(accumulated)
            if ok:
                schema = ToolRegistry.get_schema(final_name)
                if schema:
                    missing = [p for p in schema.required if p not in arguments]
                    if missing:
                        self._accumulator[tc_id]["awaiting_required"] = True
                        return

                if not self._accumulator[tc_id].get("input_started") and final_name:
                    await self._processor.process_event(
                        ToolInputStartEvent(id=tc_id, tool_name=final_name)
                    )
                    self._accumulator[tc_id]["input_started"] = True

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

        for tc_id, tc_data in list(self._accumulator.items()):
            if tc_data.get("completed"):
                continue
            accumulated_args = tc_data.get("arguments_str", "")
            tool_name = tc_data.get("name", "")
            if not (accumulated_args and tool_name):
                continue

            arguments, ok = _parse_json_robust(accumulated_args)
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
            if is_truncated:
                error_msg = (
                    f"Output was truncated (finish_reason='{stream_finish_reason}'). "
                    f"Tool arguments for '{tool_name}' cut off at {len(accumulated_args)} chars. "
                    f"Please reduce the content size or split the operation."
                )
            else:
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
        """Attempt multiple repair strategies. Return True if repaired."""
        repaired_json = repair_truncated_json(accumulated_args)
        jsons_to_try = [accumulated_args]
        if repaired_json != accumulated_args:
            jsons_to_try.append(repaired_json)

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

        # Strategy 0: truncated JSON repair with original name
        if repaired_json != accumulated_args:
            args, ok = _parse_json_robust(repaired_json)
            if ok:
                schema = ToolRegistry.get_schema(tool_name)
                missing = [p for p in schema.required if p not in args] if schema else []
                if not missing:
                    if not tc_data.get("input_started"):
                        await self._processor.process_event(
                            ToolInputStartEvent(id=tc_id, tool_name=tool_name)
                        )
                    await self._processor.process_event(
                        ToolCallEvent(
                            tool_call_id=tc_id, tool_name=tool_name, input=args,
                        )
                    )
                    return True

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
