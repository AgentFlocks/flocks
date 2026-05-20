"""Append-only execution recorder (JSONL).

This complements the SQLite storage:
- SQLite (Storage) remains the canonical state store for sessions/messages/parts.
- Recorder writes an append-only event stream that is easy to inspect and replay.

Default directory: ~/.flocks/data/records
Overrides:
- FLOCKS_RECORD_DIR: explicit record directory
- FLOCKS_ROOT: used indirectly via Config paths (if set)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from flocks.config.config import Config
from flocks.utils.log import Log


_log = Log.create(service="recording")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _record_dir() -> Path:
    raw = os.getenv("FLOCKS_RECORD_DIR")
    if raw:
        return Path(raw)
    return Config.get_data_path() / "records"


def _safe_truncate(value: Any, *, max_chars: int = 20_000) -> Any:
    """Best-effort truncate long strings/JSON for log safety."""
    if value is None:
        return None
    if isinstance(value, str):
        if len(value) <= max_chars:
            return value
        return value[:max_chars] + f"...[truncated:{len(value) - max_chars}]"
    return value


@dataclass(frozen=True)
class RecordPaths:
    session_dir: Path
    workflow_dir: Path


_MAX_FILE_LOCKS = 512  # cap to prevent unbounded growth across long-lived processes


class Recorder:
    """Append-only JSONL recorder."""

    # OrderedDict used as a simple LRU: recently-used keys move to the end;
    # oldest entries are evicted when the cap is exceeded.
    _locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    @classmethod
    def paths(cls) -> RecordPaths:
        base = _record_dir()
        return RecordPaths(
            session_dir=base / "session",
            workflow_dir=base / "workflow",
        )

    @classmethod
    def _get_lock(cls, key: str) -> asyncio.Lock:
        """Return the per-file lock, creating and caching it with LRU eviction."""
        if key in cls._locks:
            cls._locks.move_to_end(key)
            return cls._locks[key]
        lock = asyncio.Lock()
        cls._locks[key] = lock
        if len(cls._locks) > _MAX_FILE_LOCKS:
            cls._locks.popitem(last=False)  # evict oldest
        return lock

    @classmethod
    async def append_jsonl(cls, path: Path, obj: Dict[str, Any]) -> None:
        """Append one JSONL line. Never raises."""
        key = str(path)
        lock = cls._get_lock(key)

        async with lock:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(obj, ensure_ascii=False, default=str) + "\n"
                def _write(p: Path, data: str) -> None:
                    with p.open("a", encoding="utf-8") as f:
                        f.write(data)

                await asyncio.to_thread(_write, path, line)
            except Exception as e:
                _log.warn("record.append.failed", {"path": str(path), "error": str(e)})

    @classmethod
    async def record_session_message(
        cls,
        *,
        session_id: str,
        message_id: str,
        role: str,
        text: str,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        paths = cls.paths()
        path = paths.session_dir / f"{session_id}.jsonl"
        await cls.append_jsonl(
            path,
            {
                "ts": _now_ms(),
                "type": "session.message",
                "session_id": session_id,
                "message_id": message_id,
                "role": role,
                "text": _safe_truncate(text),
                "extra": extra or {},
            },
        )

    @classmethod
    async def record_tool_state(
        cls,
        *,
        session_id: str,
        message_id: str,
        part_id: str,
        call_id: str,
        tool: str,
        state: Dict[str, Any],
    ) -> None:
        paths = cls.paths()
        path = paths.session_dir / f"{session_id}.jsonl"

        # Keep payload readable and bounded.
        safe_state = dict(state)
        if "raw" in safe_state:
            safe_state["raw"] = _safe_truncate(safe_state.get("raw"))
        if "output" in safe_state:
            safe_state["output"] = _safe_truncate(safe_state.get("output"))
        if "error" in safe_state:
            safe_state["error"] = _safe_truncate(safe_state.get("error"))

        await cls.append_jsonl(
            path,
            {
                "ts": _now_ms(),
                "type": "session.tool",
                "session_id": session_id,
                "message_id": message_id,
                "part_id": part_id,
                "call_id": call_id,
                "tool": tool,
                "state": safe_state,
            },
        )

    @classmethod
    async def record_workflow_execution(
        cls,
        *,
        exec_id: str,
        workflow_id: Optional[str],
        run_result: Dict[str, Any],
    ) -> None:
        # Import lazily here to avoid an import cycle:
        # execution_store -> Recorder, while the recorder only needs the
        # compaction helpers when serializing workflow audit records.
        from flocks.workflow.execution_store import compact_outputs_for_storage

        paths = cls.paths()
        path = paths.workflow_dir / f"{exec_id}.jsonl"
        history = run_result.get("executionLog") or run_result.get("history") or []
        summary_outputs = run_result.get("outputResults") or run_result.get("outputs")
        if isinstance(summary_outputs, dict):
            summary_outputs = compact_outputs_for_storage(summary_outputs)
        await cls.append_jsonl(
            path,
            {
                "ts": _now_ms(),
                "type": "workflow.summary",
                "exec_id": exec_id,
                "workflow_id": workflow_id,
                "status": run_result.get("status"),
                "error": _safe_truncate(run_result.get("errorMessage") or run_result.get("error")),
                "steps": len(history) if isinstance(history, list) else None,
                "outputs": _safe_truncate(summary_outputs),
            },
        )

        if isinstance(history, list):
            for idx, step in enumerate(history, 1):
                step_outputs = step.get("outputs") if isinstance(step, dict) else None
                if isinstance(step_outputs, dict):
                    step_outputs = compact_outputs_for_storage(step_outputs)
                await cls.append_jsonl(
                    path,
                    {
                        "ts": _now_ms(),
                        "type": "workflow.step",
                        "exec_id": exec_id,
                        "workflow_id": workflow_id,
                        "step": idx,
                        "node_id": step.get("node_id") or step.get("nodeId") or step.get("node"),
                        "inputs": _safe_truncate(step.get("inputs")),
                        "outputs": _safe_truncate(step_outputs if step_outputs is not None else step.get("outputs")),
                        "stdout": _safe_truncate(step.get("stdout")),
                        "error": _safe_truncate(step.get("error")),
                        "traceback": _safe_truncate(step.get("traceback")),
                        "duration_ms": step.get("duration_ms"),
                    },
                )

