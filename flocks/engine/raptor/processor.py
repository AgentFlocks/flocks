"""
RaptorStreamProcessor - deferred and parallel tool execution.

Execution model:
  - During LLM streaming: tool calls are registered (pending ToolPart created)
    but NOT executed immediately.
  - After streaming: the whole batch is evaluated with _should_parallelize_batch().
    If the batch is safe (all tools are known-safe and paths don't conflict),
    all calls run concurrently via asyncio.gather.  Otherwise the batch falls
    back to serial execution.
  - Before write / destructive tool calls a Git snapshot is taken (checkpoint),
    enabling rollback of changes.

Parallelisation rules (conservative by default):
  - _NEVER_PARALLEL_TOOLS forces the entire batch to run serially.
  - _PATH_SCOPED_TOOLS may parallelize only when file paths don't overlap.
  - _PARALLEL_SAFE_TOOLS are always safe to run concurrently.
  - Everything else defaults to serial (unknown tools are treated as unsafe).
"""

from __future__ import annotations

import asyncio
import re
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.message import (
    Message,
    ToolPart,
    ToolStatePending,
    ToolStateRunning,
    ToolStateCompleted,
    ToolStateError,
)
from flocks.session.session import Session
from flocks.session.streaming.stream_processor import StreamProcessor, ToolCallState
from flocks.session.streaming.stream_events import ToolCallEvent
from flocks.tool.registry import ToolRegistry, ToolContext, ToolResult
from flocks.tool.delegate_task_constants import (
    DELEGATE_DEPTH_KEY,
    DELEGATE_ROLE_KEY,
    DELEGATE_ROLE_LEAF,
)

log = Log.create(service="engine.raptor.processor")

# ---------------------------------------------------------------------------
# Tool classification sets
# ---------------------------------------------------------------------------

# These tools must NEVER run concurrently with any other tool call.
# They carry session-level or global side-effects that make interleaving unsafe.
_NEVER_PARALLEL_TOOLS: frozenset = frozenset()

# Sub-agent delegation tools. Each delegation runs in an isolated Flocks
# sub-session (independent session_id and state), so multiple delegations are
# safe to run concurrently with each other and with read-only tools. This
# realises the design's "Subagent batch concurrency" requirement (§5.4).
# Guard: delegation is NOT parallelised alongside file-mutating tools in the
# same batch (see _MUTATING_PATH_TOOLS below).
_DELEGATION_TOOLS: frozenset = frozenset({
    "delegate_task",
})

# File-operation tools whose path argument is used for conflict detection.
# Two calls to these tools are safe to run concurrently only when their
# resolved file paths do not overlap.
_PATH_SCOPED_TOOLS: frozenset = frozenset({
    "read",        # filePath arg
    "write",       # filePath arg
    "edit",        # filePath arg
})

# Path-scoped tools that mutate the file-system. Used as a safety guard:
# a delegation must not run in parallel with any of these in the same batch,
# because a sub-agent may touch files that the write/edit targets.
_MUTATING_PATH_TOOLS: frozenset = frozenset({
    "write",
    "edit",
})

# Purely read-only tools with no shared mutable state: always safe to
# parallelize regardless of arguments.
_PARALLEL_SAFE_TOOLS: frozenset = frozenset({
    "glob",
    "grep",
    "websearch",
    "webfetch",
    "lsp",
    "get_time",
    "doc_parser",
    "skill_load",
    "flocks_skills",
})

# Tools that mutate the file-system: a Git snapshot is taken before they run.
_CHECKPOINT_BEFORE_TOOLS: frozenset = frozenset({
    "write",
    "edit",
    "apply_patch",
    "bash",
})

# ---------------------------------------------------------------------------
# Destructive-bash detection
# ---------------------------------------------------------------------------

_DESTRUCTIVE_BASH_RE = re.compile(
    r'(?:^|[;&|`\s])'
    r'(?:rm\s|rmdir\s|mv\s|cp\s|dd\s|truncate\s'
    r'|git\s+(?:reset|clean|checkout)\s'
    r'|sed\s+-i)',
    re.MULTILINE,
)
# Matches single > but not >> (overwrite redirect, not append)
_REDIRECT_OVERWRITE_RE = re.compile(r'(?<![>])>(?!>)')


def _is_destructive_bash(args: Dict[str, Any]) -> bool:
    """Return True when a bash call looks like it will overwrite or delete files."""
    cmd = args.get("command", "")
    if not cmd:
        return False
    if _DESTRUCTIVE_BASH_RE.search(cmd):
        return True
    if _REDIRECT_OVERWRITE_RE.search(cmd):
        return True
    return False


# ---------------------------------------------------------------------------
# Path-conflict helpers
# ---------------------------------------------------------------------------

def _extract_tool_path(tool_name: str, args: Dict[str, Any]) -> Optional[str]:
    """
    Return the primary file path for a path-scoped tool, or None if not found.

    Only `_PATH_SCOPED_TOOLS` use this; all of them accept `filePath`.
    """
    return args.get("filePath") or args.get("file_path") or args.get("path")


def _paths_overlap(a: str, b: str) -> bool:
    """
    Return True when path *a* and path *b* refer to the same or nested location.

    Normalises trailing slashes before comparison.
    """
    a = a.rstrip("/")
    b = b.rstrip("/")
    if a == b:
        return True
    # One is a directory that contains the other
    return a.startswith(b + "/") or b.startswith(a + "/")


# ---------------------------------------------------------------------------
# Batch-level parallelism decision
# ---------------------------------------------------------------------------

def _should_parallelize_batch(
    calls: List[Tuple[str, str, Dict[str, Any]]],
) -> bool:
    """
    Return True only when every call in *calls* is safe to run concurrently.

    Conservative policy:
      1. Single call: always serial (no benefit from gather).
      2. Any call in _NEVER_PARALLEL_TOOLS: entire batch is serial.
      3. Delegation tools (_DELEGATION_TOOLS) run in isolated sub-sessions and
         are parallel-safe, except they must not be mixed with file-mutating
         tools in the same batch (a sub-agent may touch those files).
      4. Path-scoped tools are OK to parallelize as long as their file paths
         do not overlap with any previously seen path in this batch.
      5. Explicitly safe tools (_PARALLEL_SAFE_TOOLS): always OK.
      6. Unknown tools: default serial (safe over fast).
    """
    if len(calls) <= 1:
        return False

    names = [name for _, name, _ in calls]
    has_delegation = any(n in _DELEGATION_TOOLS for n in names)
    has_mutation = any(n in _MUTATING_PATH_TOOLS for n in names)

    # Never parallelise a sub-agent delegation alongside a file write/edit.
    if has_delegation and has_mutation:
        return False

    reserved_paths: List[str] = []

    for _, tool_name, args in calls:
        if tool_name in _NEVER_PARALLEL_TOOLS:
            return False

        if tool_name in _DELEGATION_TOOLS:
            # Isolated sub-session: parallel-safe (guarded above).
            continue

        if tool_name in _PATH_SCOPED_TOOLS:
            scoped_path = _extract_tool_path(tool_name, args)
            if scoped_path is None:
                # Cannot determine path: conservative serial
                return False
            if any(_paths_overlap(scoped_path, existing) for existing in reserved_paths):
                return False
            reserved_paths.append(scoped_path)
            continue

        if tool_name in _PARALLEL_SAFE_TOOLS:
            continue

        # Unknown tool: default serial
        return False

    return True


def _resolve_tool_error(result: ToolResult) -> str:
    if result.error:
        return result.error
    if isinstance(result.metadata, dict):
        out = str(result.metadata.get("output") or "").strip()
        if out:
            return out
    if isinstance(result.output, str) and result.output.strip():
        return result.output.strip()
    return "Unknown error"


class RaptorStreamProcessor(StreamProcessor):
    """
    StreamProcessor subclass that defers tool execution for parallel batching.

    During LLM streaming:
      - _handle_tool_call() registers each tool call and creates a ToolPart in
        "pending" state (so the WebUI shows the card immediately) without running
        the tool.

    After the stream is fully flushed:
      - execute_deferred_parallel() is called by RaptorSessionRunner.
      - Tool calls are grouped by file-path conflicts and executed concurrently
        with asyncio.gather within each parallel-safe batch.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        # Ordered list of (call_id, tool_name, tool_input) to execute
        self._deferred: List[Tuple[str, str, Dict[str, Any]]] = []

    async def _handle_tool_call(self, event: ToolCallEvent) -> None:
        """
        Override: register the tool call as pending without executing it.

        The base StreamProcessor executes the tool inline (serial).  This
        override creates the ToolPart in pending state and enqueues the call
        for later parallel execution by RaptorSessionRunner._after_tools_collected().
        """
        tool_call_id = event.tool_call_id
        tool_name = event.tool_name
        tool_input = event.input

        # Respect doom-loop stop flag from base class
        if self._stop_tool_processing:
            log.debug("raptor.processor.tool_call.skipped", {
                "tool_call_id": tool_call_id,
                "reason": "doom_loop_prevention_active",
            })
            return

        # Skip already-registered calls (guard for duplicate events)
        existing = self.tool_calls.get(tool_call_id)
        if existing and existing.status in ("completed", "error"):
            return

        # Create or retrieve ToolCallState
        if tool_call_id not in self.tool_calls:
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
        tool_state.status = "pending"

        # Persist a pending ToolPart so the WebUI shows the tool card immediately
        try:
            pending_part = ToolPart(
                id=tool_state.part_id,
                sessionID=self.session_id,
                messageID=self.assistant_message.id,
                type="tool",
                callID=tool_call_id,
                tool=tool_name,
                state=ToolStatePending(
                    status="pending",
                    input=tool_input,
                    raw=json.dumps(tool_input, ensure_ascii=False),
                ),
            )
            await Message.store_part(
                self.session_id, self.assistant_message.id, pending_part
            )
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
                            "status": "pending",
                            "input": tool_input,
                        },
                    }
                })
        except Exception as exc:
            log.debug("raptor.processor.pending_part.failed", {"error": str(exc)})

        # Enqueue for parallel execution
        self._deferred.append((tool_call_id, tool_name, tool_input))
        log.info("raptor.processor.tool_call.deferred", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "deferred_count": len(self._deferred),
        })

    async def execute_deferred_parallel(self) -> None:
        """
        Execute all deferred tool calls, choosing parallel or serial execution.

        Called by RaptorSessionRunner._after_tools_collected() once the full
        LLM response stream has been flushed.

        Decision:
          _should_parallelize_batch() true: asyncio.gather (all concurrent)
          _should_parallelize_batch() false: sequential loop
        """
        if not self._deferred:
            return

        tools = [name for _, name, _ in self._deferred]
        parallel = _should_parallelize_batch(self._deferred)

        log.info("raptor.processor.parallel_exec.start", {
            "session_id": self.session_id,
            "tool_count": len(self._deferred),
            "tools": tools,
            "mode": "parallel" if parallel else "serial",
        })

        if parallel:
            await asyncio.gather(
                *[self._execute_one_tool(cid, nm, inp)
                  for cid, nm, inp in self._deferred],
                return_exceptions=True,
            )
        else:
            for call_id, name, inp in self._deferred:
                await self._execute_one_tool(call_id, name, inp)

    async def _take_checkpoint_if_needed(
        self,
        tool_name: str,
        args: Dict[str, Any],
    ) -> None:
        """
        Take a Git snapshot before any tool that may overwrite or delete files.

        Best-effort: a failure here never blocks tool execution.  The snapshot
        hash is logged so operators can identify which commit to roll back to.
        """
        needs_checkpoint = (
            tool_name in _CHECKPOINT_BEFORE_TOOLS
            and (tool_name != "bash" or _is_destructive_bash(args))
        )
        if not needs_checkpoint or not self._workspace_dir:
            return

        try:
            from flocks.snapshot.snapshot import Snapshot
            from flocks.session.session import Session

            session = await Session.get_by_id(self.session_id)
            if not session or not getattr(session, "project_id", None):
                return

            snapshot_hash = await Snapshot.track(
                session.project_id, self._workspace_dir
            )
            if snapshot_hash:
                log.info("raptor.checkpoint.taken", {
                    "session_id": self.session_id,
                    "tool": tool_name,
                    "hash": snapshot_hash[:12],
                })
        except Exception as exc:
            log.debug("raptor.checkpoint.skipped", {
                "tool": tool_name,
                "error": str(exc),
            })

    async def _blocked_delegate_result(self, tool_name: str) -> Optional[ToolResult]:
        """Return a blocking result when a leaf delegate tries to spawn children."""
        if tool_name != "delegate_task":
            return None

        try:
            session = await Session.get_by_id(self.session_id)
        except Exception as exc:
            log.debug("raptor.delegate_guard.session_load_failed", {
                "session_id": self.session_id,
                "error": str(exc),
            })
            return None

        metadata = session.metadata if (session and isinstance(session.metadata, dict)) else {}
        depth = metadata.get(DELEGATE_DEPTH_KEY)
        role = metadata.get(DELEGATE_ROLE_KEY, DELEGATE_ROLE_LEAF)
        if role != DELEGATE_ROLE_LEAF or depth is None:
            return None

        return ToolResult(
            success=False,
            error=(
                "This agent was created as a leaf delegate and cannot further "
                "delegate to sub-agents. Use role='orchestrator' on the parent "
                "delegate_task call to enable nested delegation."
            ),
            metadata={
                "blocked_by": "delegate_role",
                "delegate_role": role,
                "delegate_depth": depth,
            },
        )

    async def _persist_tool_result(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
        tool_state: ToolCallState,
        result: ToolResult,
        tool_start_time: int,
    ) -> None:
        """Persist the final ToolPart state and publish callbacks/events."""
        tool_end_time = int(datetime.now().timestamp() * 1000)

        try:
            if result.success:
                tool_state.status = "completed"
                tool_state.output = result.output
                completed_state = ToolStateCompleted(
                    status="completed",
                    input=tool_input,
                    output=result.output if result.output is not None else "",
                    title=result.title or tool_name,
                    metadata=result.metadata or {},
                    time={"start": tool_start_time, "end": tool_end_time},
                )
                state_dict: Dict[str, Any] = {
                    "status": "completed",
                    "input": tool_input,
                    "output": result.output if result.output is not None else "",
                    "title": result.title or tool_name,
                    "metadata": result.metadata or {},
                    "time": {"start": tool_start_time, "end": tool_end_time},
                }
            else:
                tool_state.status = "error"
                tool_state.error = _resolve_tool_error(result)
                resolved_error = _resolve_tool_error(result)
                completed_state = ToolStateError(
                    status="error",
                    input=tool_input,
                    error=resolved_error,
                    metadata=result.metadata or {},
                    time={"start": tool_start_time, "end": tool_end_time},
                )
                state_dict = {
                    "status": "error",
                    "input": tool_input,
                    "error": resolved_error,
                    "metadata": result.metadata or {},
                    "time": {"start": tool_start_time, "end": tool_end_time},
                }

            done_part = ToolPart(
                id=tool_state.part_id,
                sessionID=self.session_id,
                messageID=self.assistant_message.id,
                type="tool",
                callID=tool_call_id,
                tool=tool_name,
                state=completed_state,
            )
            await Message.store_part(
                self.session_id, self.assistant_message.id, done_part
            )
            if self.event_publish_callback:
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
        except Exception as exc:
            log.error("raptor.processor.result_persist.failed", {"error": str(exc)})

        if self.tool_end_callback:
            try:
                await self.tool_end_callback(tool_name, result)
            except Exception as exc:
                log.debug("raptor.processor.tool_end_cb.error", {"error": str(exc)})

        log.info("raptor.processor.tool_call.completed", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "success": result.success,
            "duration_ms": tool_end_time - tool_start_time,
        })

    async def _execute_one_tool(
        self,
        tool_call_id: str,
        tool_name: str,
        tool_input: Dict[str, Any],
    ) -> None:
        """Execute a single tool call and persist the result."""
        tool_state = self.tool_calls.get(tool_call_id)
        if tool_state is None:
            log.warn("raptor.processor.tool_state_missing", {
                "tool_call_id": tool_call_id,
            })
            return

        tool_start_time = int(datetime.now().timestamp() * 1000)
        blocked_result = await self._blocked_delegate_result(tool_name)
        if blocked_result is not None:
            await self._persist_tool_result(
                tool_call_id,
                tool_name,
                tool_input,
                tool_state,
                blocked_result,
                tool_start_time,
            )
            return

        # Take a Git snapshot before destructive writes so changes can be reverted
        await self._take_checkpoint_if_needed(tool_name, tool_input)

        tool_state.status = "running"

        # Transition to running state in UI
        try:
            running_part = ToolPart(
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
            await Message.store_part(
                self.session_id, self.assistant_message.id, running_part
            )
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
                        },
                    }
                })
        except Exception as exc:
            log.debug("raptor.processor.running_part.failed", {"error": str(exc)})

        # Notify tool-start callback (CLI display)
        if self.tool_start_callback:
            try:
                await self.tool_start_callback(tool_name, tool_input)
            except Exception as exc:
                log.debug("raptor.processor.tool_start_cb.error", {"error": str(exc)})

        # Run hooks and execute via ToolRegistry
        result: Optional[ToolResult] = None
        try:
            # tool.execute.before hook
            hook_skip = False
            try:
                from flocks.hooks.pipeline import HookPipeline
                hook_ctx = await HookPipeline.run_tool_before({
                    "sessionID": self.session_id,
                    "workspace": self._workspace_dir,
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
                hook_skip = bool(hook_ctx.output.get("skip")) if hook_ctx else False
            except Exception as exc:
                log.debug("raptor.processor.tool_before_hook.error", {"error": str(exc)})

            if hook_skip:
                result = ToolResult(success=False, error="Tool execution blocked by hook")
            else:
                # Sandbox policy check
                sandbox_meta = await self._resolve_sandbox_meta(tool_name)
                if sandbox_meta.get("blocked"):
                    result = ToolResult(
                        success=False,
                        error=sandbox_meta.get("error", "Sandbox blocked"),
                        metadata={"sandbox": True, "blocked_by_policy": True},
                    )
                else:
                    ctx = ToolContext(
                        session_id=self.session_id,
                        message_id=self.assistant_message.id,
                        agent=self.agent.name,
                        call_id=tool_call_id,
                        permission_callback=self.permission_callback,
                        extra=sandbox_meta.get("extra", {}),
                        event_publish_callback=self.event_publish_callback,
                    )
                    result = await ToolRegistry.execute(
                        tool_name=tool_name,
                        ctx=ctx,
                        **tool_input,
                    )

            # tool.execute.after hook
            try:
                from flocks.hooks.pipeline import HookPipeline
                hook_ctx = await HookPipeline.run_tool_after({
                    "sessionID": self.session_id,
                    "workspace": self._workspace_dir,
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
            except Exception as exc:
                log.debug("raptor.processor.tool_after_hook.error", {"error": str(exc)})

        except Exception as exc:
            log.error("raptor.processor.tool_exec.error", {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "error": str(exc),
            })
            result = ToolResult(success=False, error=str(exc))

        # Persist completed/error state
        tool_end_time = int(datetime.now().timestamp() * 1000)
        assert result is not None

        try:
            if result.success:
                tool_state.status = "completed"
                tool_state.output = result.output
                completed_state = ToolStateCompleted(
                    status="completed",
                    input=tool_input,
                    output=result.output if result.output is not None else "",
                    title=result.title or tool_name,
                    metadata=result.metadata or {},
                    time={"start": tool_start_time, "end": tool_end_time},
                )
                state_dict: Dict[str, Any] = {
                    "status": "completed",
                    "input": tool_input,
                    "output": result.output if result.output is not None else "",
                    "title": result.title or tool_name,
                    "metadata": result.metadata or {},
                    "time": {"start": tool_start_time, "end": tool_end_time},
                }
            else:
                tool_state.status = "error"
                tool_state.error = _resolve_tool_error(result)
                resolved_error = _resolve_tool_error(result)
                completed_state = ToolStateError(
                    status="error",
                    input=tool_input,
                    error=resolved_error,
                    metadata=result.metadata or {},
                    time={"start": tool_start_time, "end": tool_end_time},
                )
                state_dict = {
                    "status": "error",
                    "input": tool_input,
                    "error": resolved_error,
                    "metadata": result.metadata or {},
                    "time": {"start": tool_start_time, "end": tool_end_time},
                }

            done_part = ToolPart(
                id=tool_state.part_id,
                sessionID=self.session_id,
                messageID=self.assistant_message.id,
                type="tool",
                callID=tool_call_id,
                tool=tool_name,
                state=completed_state,
            )
            await Message.store_part(
                self.session_id, self.assistant_message.id, done_part
            )
            if self.event_publish_callback:
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
        except Exception as exc:
            log.error("raptor.processor.result_persist.failed", {"error": str(exc)})

        # Notify tool-end callback (CLI display)
        if self.tool_end_callback:
            try:
                await self.tool_end_callback(tool_name, result)
            except Exception as exc:
                log.debug("raptor.processor.tool_end_cb.error", {"error": str(exc)})

        log.info("raptor.processor.tool_call.completed", {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "success": result.success,
            "duration_ms": tool_end_time - tool_start_time,
        })
