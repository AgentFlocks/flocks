"""Preserve audit evidence at submission, recovery, and compaction boundaries.

Standard tools remain unchanged; the audit lifecycle owns receipt persistence.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, TYPE_CHECKING

from flocks.session.message import Message
from flocks.tool.registry import ToolContext, remap_schema_kwargs
from flocks.tool.path_utils import resolve_host_path


if TYPE_CHECKING:
    from flocks_code_security.runtime import PluginRuntime
    from flocks_code_security.store import SessionBinding


async def sync_source_receipts(ctx: ToolContext, runtime: PluginRuntime, binding: SessionBinding) -> None:
    await _sync_session_receipts(runtime, binding, ctx.agent, ctx.extra.get("workspace_dir"))


async def sync_worker_source_receipts(session_id: str, *, expected_attempt_id: str | None = None) -> None:
    """Refresh an active worker's evidence without invoking or modifying read."""
    from flocks_code_security.execution import ExecutionCapsuleError
    from flocks_code_security.runtime import get_runtime
    from flocks_code_security.source import SOURCE_ROLES
    from flocks_code_security.store import WORKER_ROLE_AGENTS

    runtime = get_runtime()
    binding = await asyncio.to_thread(runtime.store.resolve_binding, session_id)
    if binding is None or binding.role not in SOURCE_ROLES:
        return
    if expected_attempt_id is not None and binding.attempt_id != expected_attempt_id:
        raise ExecutionCapsuleError("Source receipt session belongs to another attempt")
    scan = await asyncio.to_thread(runtime.store.get_scan, binding.scan_id)
    unit = await asyncio.to_thread(runtime.store.get_work_unit, binding.work_unit_id)
    if scan is None or scan["status"] != "running" or unit is None or unit["status"] != "running":
        return
    attempt = await asyncio.to_thread(runtime.store.get_work_attempt, binding.attempt_id)
    if attempt is None or attempt["status"] != "running":
        return
    await _sync_session_receipts(runtime, binding, WORKER_ROLE_AGENTS[binding.role])


def register_source_receipt_preserver() -> None:
    from flocks.session.lifecycle.compaction.pruning import register_tool_output_preserver

    register_tool_output_preserver("flocks-code-security", preserve_worker_source_receipts)


async def preserve_worker_source_receipts(session_id: str) -> None:
    """Keep ordinary sessions independent of the audit runtime and database."""
    from flocks.session.session import Session
    from flocks_code_security.source import SOURCE_ROLES
    from flocks_code_security.store import WORKER_ROLE_AGENTS

    session = await Session.get_by_id_unfiltered(session_id)
    if session is None or session.agent not in {WORKER_ROLE_AGENTS[role] for role in SOURCE_ROLES}:
        return
    await sync_worker_source_receipts(session_id)


async def _sync_session_receipts(runtime: PluginRuntime, binding: SessionBinding, agent: str, base_dir: str | None = None) -> None:
    processed = await asyncio.to_thread(runtime.store.processed_source_parts, binding.attempt_id)
    messages = await Message.list_with_parts(binding.session_id, include_archived=True)
    parts = [
        part
        for message in messages
        if message.info.role == "assistant" and message.info.agent == agent
        for part in message.parts
        if part.type == "tool"
        and part.id not in processed
        and part.sessionID == binding.session_id
        and part.tool in {"read", "glob", "grep"}
        and part.state.status == "completed"
        and isinstance(part.state.output, str)
    ]
    if not parts:
        return
    if not base_dir:
        from flocks.session.session import Session

        session = await Session.get_by_id_unfiltered(binding.session_id)
        if session is None:
            raise ValueError("Source receipt session no longer exists")
        base_dir = session.directory
    await asyncio.to_thread(_record_transcript_receipts, runtime, binding, parts, base_dir)


def _record_transcript_receipts(runtime: PluginRuntime, binding: SessionBinding, parts: list[Any], base_dir: str) -> None:
    source = runtime.source
    snapshot = runtime.store.get_snapshot(binding.snapshot_id)
    if snapshot is None:
        raise ValueError("Bound snapshot no longer exists")
    root = Path(snapshot.root_path).resolve()
    assigned = source.assigned_paths(binding)
    accesses = []
    verified_lines: dict[str, list[tuple[str, int, int]]] = {}
    for part in parts:
        name, state = part.tool, part.state
        if name == "read":
            arguments, _ = remap_schema_kwargs(
                state.input, ["filePath", "offset", "limit"], tool_name="read",
            )
            paths = [arguments.get("filePath", "")]
        elif name == "glob":
            paths = state.output.splitlines()
        else:
            paths = [line[:-1] for line in state.output.splitlines() if line.endswith(":") and not line.startswith(" ")]
        for path in paths:
            if not isinstance(path, str) or not path:
                continue
            try:
                relative = Path(resolve_host_path(path, base_dir=base_dir)).relative_to(root).as_posix()
            except (ValueError, OSError):
                continue
            if not source.in_assigned_scope(relative, assigned):
                continue
            record = runtime.store.get_snapshot_file(binding.snapshot_id, relative)
            if record is None:
                continue
            base = {"relative_path": relative, "blob_digest": record.blob_digest, "source_part_id": part.id}
            if name != "read":
                accesses.append({**base, "operation": "inventory" if name == "glob" else "search"})
                continue
            if relative not in verified_lines:
                verified_lines[relative] = native_source_lines(source.verified_bytes(binding.snapshot_id, record))
            lines = verified_lines[relative]
            ranges: list[list[int]] = []
            for line in state.output.split("\n"):
                match = re.fullmatch(r"(\d+)\| (.*)", line)
                if match is None:
                    continue
                number = int(match[1])
                # Truncated or modified lines cannot establish complete coverage.
                if not 1 <= number <= len(lines):
                    continue
                text, start, end = lines[number - 1]
                if match[2] != text or end < start:
                    continue
                if ranges and start == ranges[-1][1] + 1:
                    ranges[-1][1] = end
                else:
                    ranges.append([start, end])
            accesses.extend({**base, "operation": "read", "start_line": start, "end_line": end} for start, end in ranges)

    def key(item: dict) -> tuple:
        return tuple(item.get(field) for field in ("operation", "relative_path", "blob_digest", "start_line", "end_line"))

    existing = {key(item) for item in runtime.store.list_source_accesses(binding.attempt_id)}
    pending = {key(item): item for item in accesses if key(item) not in existing}
    runtime.store.record_source_accesses(
        binding, list(pending.values()), processed_part_ids=[part.id for part in parts],
    )


def native_source_lines(data: bytes) -> list[tuple[str, int, int]]:
    """Map read's universal-newline numbering to the snapshot's splitlines spans.

    A displayed line may contain form feeds or Unicode separators. Those stay
    inside read's line, but each contributes to the audit evidence line range.
    The virtual empty line after a final newline has no snapshot range.
    """
    text = data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    native_lines = text.split("\n")
    result = []
    start = 1
    for index, line in enumerate(native_lines):
        terminated_line = line + "\n" if index < len(native_lines) - 1 else line
        count = len(terminated_line.splitlines())
        result.append((line, start, start + count - 1))
        start += count
    return result
