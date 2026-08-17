"""
Activity Forwarder

Bridges child sub-agent session activity back to the parent ToolPart metadata,
enabling real-time progress display in TUI/WebUI without requiring the frontend
to subscribe to child session events directly.

Usage:
    forwarder = ActivityForwarder(parent_ctx, child_session_id, description)
    callbacks = forwarder.build_callbacks()
    result = await SessionLoop.run(child_session_id, callbacks=callbacks)
"""

import copy
import time
import asyncio
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional

from flocks.tool.registry import ToolContext, ToolResult
from flocks.utils.log import Log


log = Log.create(service="session.activity_forwarder")

# Max steps to include in metadata payload
_MAX_STEPS = 8
# Max chars for the streaming text preview
_MAX_TEXT_PREVIEW = 120
# Minimum interval (seconds) between text-delta metadata publishes
_TEXT_THROTTLE_SEC = 0.3


def _summarize_input(tool_name: str, tool_input: Dict[str, Any]) -> str:
    """Extract a short human-readable label from tool input."""
    for key in ("filePath", "file_path", "path", "pattern", "query", "command", "url", "glob_pattern"):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            if len(val) > 60:
                return val[:57] + "..."
            return val
    for key in ("description", "prompt"):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            if len(val) > 40:
                return val[:37] + "..."
            return val
    return ""


@dataclass
class ActivityForwarder:
    """Forwards child session activity as parent ToolPart metadata updates."""

    parent_ctx: ToolContext
    child_session_id: str
    description: str

    _steps: List[Dict[str, Any]] = field(default_factory=list)
    _current_text: str = ""
    _start_time: float = field(default_factory=time.time)
    _last_publish: float = 0

    def build_callbacks(self, event_publish_callback=None):
        """Build LoopCallbacks for child SessionLoop.run().
        
        Args:
            event_publish_callback: Optional SSE event publisher injected by the
                server layer. Avoids session → server reverse dependency.
        """
        from flocks.session.session_loop import LoopCallbacks

        return LoopCallbacks(
            event_publish_callback=event_publish_callback,
            on_tool_start=self._on_tool_start,
            on_tool_end=self._on_tool_end,
            on_text_delta=self._on_text_delta,
        )

    # ------------------------------------------------------------------
    # Callbacks
    # ------------------------------------------------------------------

    async def _on_tool_start(self, tool_name: str, tool_input: Dict[str, Any]) -> None:
        self._current_text = ""
        self._steps.append({
            "tool": tool_name,
            "title": _summarize_input(tool_name, tool_input),
            "status": "running",
        })
        self._publish()

    async def _on_tool_end(self, tool_name: str, result: ToolResult) -> None:
        for step in reversed(self._steps):
            if step["status"] == "running":
                step["status"] = "completed" if result.success else "error"
                break
        self._publish()

    async def _on_text_delta(self, delta: str) -> None:
        self._current_text += delta
        if len(self._current_text) > _MAX_TEXT_PREVIEW * 2:
            self._current_text = self._current_text[-_MAX_TEXT_PREVIEW:]
        now = time.time()
        if now - self._last_publish >= _TEXT_THROTTLE_SEC:
            self._publish()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _publish(self) -> None:
        self._last_publish = time.time()
        elapsed = round(time.time() - self._start_time, 1)
        steps_snapshot = copy.deepcopy(self._steps[-_MAX_STEPS:])
        try:
            self.parent_ctx.metadata({
                "title": self.description,
                "metadata": {
                    "sessionId": self.child_session_id,
                    "status": "running",
                    "steps": steps_snapshot,
                    "stepCount": len(self._steps),
                    "currentText": self._current_text[-_MAX_TEXT_PREVIEW:] if self._current_text else "",
                    "elapsed": elapsed,
                },
            })
        except Exception:
            log.debug("activity_forwarder.publish_failed", {
                "child_session_id": self.child_session_id,
            })

    @property
    def final_metadata(self) -> Dict[str, Any]:
        """Snapshot of accumulated stats for inclusion in final ToolResult metadata."""
        return {
            "sessionId": self.child_session_id,
            "stepCount": len(self._steps),
            "elapsed": round(time.time() - self._start_time, 1),
        }
