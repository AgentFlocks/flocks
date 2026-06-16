"""
Session runner module.

Core session execution logic including:
- Session loop (message processing)
- Tool resolution and execution  
- LLM interaction with tool support

Implements session/prompt.ts SessionPrompt namespace pattern.
"""

import asyncio
import copy
import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Optional, Dict, Any, List, Callable, Awaitable, Tuple
from dataclasses import dataclass, field

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.session.session import Session, SessionInfo
from flocks.session.message import (
    Message,
    MessageInfo,
    MessageRole,
    PatchPart,
    ToolPart,
    ToolStateCompleted,
)
from flocks.session.prompt import SessionPrompt
from flocks.session.core.status import SessionStatus, SessionStatusRetry, SessionStatusBusy
from flocks.session.core.defaults import (
    DEFAULT_MAX_TOOL_STEPS,
    DOOM_LOOP_THRESHOLD,
    REPEATED_EXACT_TOOL_CALL_HALT_THRESHOLD,
)
from flocks.session.lifecycle.retry import CONNECTION_ERROR_DISPLAY_MESSAGE, SessionRetry
from flocks.session.lifecycle.compaction import SessionCompaction, CompactionPolicy
from flocks.session.streaming.stream_processor import StreamProcessor
from flocks.session.streaming.stream_events import (
    StartEvent,
    TextStartEvent,
    TextDeltaEvent,
    TextEndEvent,
    FinishEvent,
    ReasoningStartEvent,
    ReasoningDeltaEvent,
    ReasoningEndEvent,
)
from flocks.session.callable_schema import list_session_callable_tool_infos
from flocks.agent.registry import Agent
from flocks.agent.agent import AgentInfo
from flocks.agent.toolset import agent_declares_tool
from flocks.provider.provider import Provider, ChatMessage
from flocks.provider.reasoning_replay import prepare_reasoning_for_replay
from flocks.hooks.pipeline import HookPipeline, HookStage
from flocks.tool.catalog import (
    get_always_load_tool_names,
    get_tool_catalog_metadata,
    list_tool_catalog_infos,
)
from flocks.tool.registry import ToolRegistry, ToolResult
from flocks.utils.langfuse import generation_scope, is_active as langfuse_is_active, trace_scope
from flocks.session.utils.file_extractor import (
    read_file_part_bytes,
    is_text_extractable_mime,
    extract_file_text,
)


log = Log.create(service="session.runner")

TOOL_RESULT_CHAR_BUDGET_RATIO = 0.70
REWIND_MUTATING_TOOL_NAMES = frozenset({
    "write",
    "write_file",
    "edit",
    "edit_file",
    "apply_patch",
    "patch",
})
REWIND_DANGEROUS_SHELL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"(^|[;&|]\s*)(rm|mv|cp|mkdir|rmdir|touch|truncate|dd|chmod|chown|install)\b",
        r"(^|[;&|]\s*)git\s+(apply|checkout|restore|reset|clean|rm|mv)\b",
        r"(^|[;&|]\s*)sed\b[^\n;]*\s-i(\b|[.'\"])",
        r"(^|[;&|]\s*)perl\b[^\n;]*\s-pi(\b|[.'\"])",
        r"(^|[;&|]\s*)tee\b(?:\s+-a)?\s+[^|&;\n]+",
        r"(^|[^&])>>?\s*[^&\s]",
        r"<<\s*['\"]?[\w-]+",
    )
)
TOOL_RESULT_TURN_BUDGET_RATIO = 0.35
TOOL_RESULT_MIN_CHAR_BUDGET = 8_000


def _annotate_with_provider_version(tool_info: Any, description: Optional[str]) -> str:
    """Append a provider-version annotation so the LLM sees which service version backs the tool.

    Returns the original description (or empty string) unchanged when the tool
    has no ``provider_version``. Format::

        <original description>

        [Provider: <provider> | Version: <version>]

    The original ``ToolInfo`` is never mutated — a new string is returned.
    """
    base = description or ""
    provider_version = getattr(tool_info, "provider_version", None)
    if not provider_version:
        return base
    provider_label = getattr(tool_info, "provider", None) or "service"
    note = f"[Provider: {provider_label} | Version: {provider_version}]"
    if not base.strip():
        return note
    return f"{base.rstrip()}\n\n{note}"
TOOL_RESULT_MIN_TURN_BUDGET = 4_000
TOOL_RESULT_PREVIEW_CHARS = 160

# Maximum seconds to wait for the *first* chunk from the LLM stream.
# If the model never starts responding, the stream times out and the session
# surfaces a clear error rather than hanging forever.
LLM_STREAM_FIRST_CHUNK_TIMEOUT_S = 60

# Once the stream has started (at least one chunk received), allow a much
# longer gap between chunks.  Some models pause for extended periods between
# reasoning and content generation phases; a tight inter-chunk timeout causes
# spurious failures in those cases.
LLM_STREAM_ONGOING_CHUNK_TIMEOUT_S = 300

_WORKFLOW_NODE_REF_RE = re.compile(r"^@@node:([^|\n]+)\|([^\n]+)\n?([\s\S]*)$")


def _expand_workflow_node_ref(text: str) -> str:
    """Translate the web UI's node-ref marker into model-readable text.

    WorkflowDetail chat prefixes a user turn with ``@@node:<id>|<type>`` when
    the user picks a node from the canvas. Before this fix the marker was only
    rendered/decorated in the UI; the backend passed it through verbatim, so
    the model saw an opaque token instead of an explicit instruction to focus
    on that node.
    """
    if not text:
        return text
    match = _WORKFLOW_NODE_REF_RE.match(text)
    if not match:
        return text

    node_id = match.group(1).strip()
    node_type = match.group(2).strip()
    user_request = match.group(3).lstrip("\n")

    parts = [
        "Selected workflow node context:",
        f"- node_id: {node_id}",
        f"- node_type: {node_type}",
        "- Focus the requested workflow modification on this node unless the user explicitly asks for broader workflow changes.",
    ]
    if user_request.strip():
        parts.extend(["", "User request:", user_request])
    return "\n".join(parts)


async def _iter_with_chunk_timeout(
    aiter,
    first_chunk_timeout_s: float,
    ongoing_chunk_timeout_s: float,
):
    """Yield chunks from an async generator with adaptive timeouts.

    *first_chunk_timeout_s* applies while waiting for the very first chunk
    (guards against a completely unresponsive model).  After the first chunk
    arrives, *ongoing_chunk_timeout_s* is used for subsequent chunks so that
    models with long pauses mid-stream are not prematurely killed.
    """
    received_first = False
    try:
        while True:
            timeout = ongoing_chunk_timeout_s if received_first else first_chunk_timeout_s
            try:
                chunk = await asyncio.wait_for(aiter.__anext__(), timeout=timeout)
                received_first = True
                yield chunk
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError:
                phase = "mid-stream" if received_first else "waiting for first response"
                raise asyncio.TimeoutError(
                    f"LLM stream timed out after {timeout:.0f}s ({phase}). "
                    "The model may be overloaded or incompatible. Please try again or switch models."
                )
    finally:
        try:
            await aiter.aclose()
        except Exception:
            pass


@dataclass
class ToolCall:
    """Tool call from LLM response."""
    id: str
    name: str
    arguments: Dict[str, Any]

@dataclass
class StepResult:
    """Result of a single processing step."""
    action: str  # "stop", "continue", "compact"
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    error: Optional[str] = None
    usage: Optional[Dict[str, int]] = None


@dataclass
class RunnerCallbacks:
    """Callbacks for runner events."""
    on_step_start: Optional[Callable[[int], Awaitable[None]]] = None
    on_step_end: Optional[Callable[[int], Awaitable[None]]] = None
    on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_reasoning_delta: Optional[Callable[[str], Awaitable[None]]] = None
    on_tool_start: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None
    on_tool_end: Optional[Callable[[str, ToolResult], Awaitable[None]]] = None
    on_permission_request: Optional[Callable[[Any], Awaitable[bool]]] = None
    on_error: Optional[Callable[[str], Awaitable[None]]] = None
    # SSE event publishing callback (for TUI/WebUI real-time updates)
    event_publish_callback: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None


class SessionRunner:
    """
    Core session runner.
    
    Manages the session execution loop:
    1. Get messages from session
    2. Check if LLM response is needed
    3. Call LLM with tools
    4. Execute tool calls
    5. Loop until complete
    
    Implements SessionPrompt.loop()
    """
    
    # Class-level state for active sessions
    _active_sessions: Dict[str, 'SessionRunner'] = {}
    
    def __init__(
        self,
        session: SessionInfo,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        callbacks: Optional[RunnerCallbacks] = None,
        abort_event: Optional[asyncio.Event] = None,
        session_ctx: Optional[Any] = None,  # SessionContext interface
        memory_bootstrap_data: Optional[Dict[str, Any]] = None,
        static_cache: Optional[Dict[str, Any]] = None,
    ):
        self.session = session
        from flocks.session.core.defaults import fallback_provider_id, fallback_model_id
        self.provider_id = provider_id or fallback_provider_id()
        self.model_id = model_id or fallback_model_id()
        self.agent_name = agent_name or "rex"
        self.callbacks = callbacks or RunnerCallbacks()
        self._abort = asyncio.Event()
        self._external_abort = abort_event  # External abort event (e.g. from SessionLoop)
        self._step = 0
        self._recent_tool_calls: List[tuple[str, str]] = []  # Track recent (tool_name, args_json) for doom loop
        self.session_ctx = session_ctx  # SessionContext interface for decoupled access
        self._memory_bootstrap_data: Optional[Dict[str, Any]] = memory_bootstrap_data
        self._static_cache = static_cache if static_cache is not None else {}
        self._rewind_checkpoint_turn_id: Optional[str] = None
        self._rewind_checkpoint_by_dir: Dict[str, str] = {}
        self._rewind_checkpoint_locks: Dict[str, asyncio.Lock] = {}

    @staticmethod
    def _canonical_tool_signature(tool_name: str, arguments: Dict[str, Any]) -> str:
        args_json = json.dumps(
            arguments or {},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return f"{tool_name}:{args_json}"

    def _reset_tool_loop_guard(self, *, last_user_id: Optional[str] = None) -> Dict[str, Any]:
        state = {
            "last_user_id": last_user_id or "",
            "last_signature": "",
            "exact_count": 0,
        }
        self._static_cache["tool_loop_guard"] = state
        return state

    def _get_tool_loop_guard_state(self, *, last_user_id: Optional[str] = None) -> Dict[str, Any]:
        state = self._static_cache.get("tool_loop_guard")
        if not isinstance(state, dict):
            state = self._reset_tool_loop_guard(last_user_id=last_user_id)
        elif last_user_id is not None:
            cached_user_id = str(state.get("last_user_id") or "")
            if cached_user_id and cached_user_id != last_user_id:
                state = self._reset_tool_loop_guard(last_user_id=last_user_id)
            else:
                state["last_user_id"] = last_user_id
        return state

    def _reset_rewind_checkpoint_turn(self, turn_id: Optional[str]) -> None:
        """Clear checkpoint de-dupe state when a new user turn starts."""
        if not turn_id or self._rewind_checkpoint_turn_id == turn_id:
            return
        self._rewind_checkpoint_turn_id = turn_id
        self._rewind_checkpoint_by_dir = {}
        self._rewind_checkpoint_locks = {}

    @staticmethod
    def _is_dangerous_shell_command(command: str) -> bool:
        if not command or not command.strip():
            return False
        return any(pattern.search(command) for pattern in REWIND_DANGEROUS_SHELL_PATTERNS)

    def _should_checkpoint_tool(
        self,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]],
    ) -> bool:
        if tool_name in REWIND_MUTATING_TOOL_NAMES:
            return True
        if tool_name == "bash":
            command = (tool_input or {}).get("command")
            return isinstance(command, str) and self._is_dangerous_shell_command(command)
        return False

    def _checkpoint_worktree_for_tool(
        self,
        tool_name: str,
        tool_input: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        if not self._should_checkpoint_tool(tool_name, tool_input):
            return None
        data = tool_input or {}
        if tool_name == "bash":
            workdir = data.get("workdir")
            if isinstance(workdir, str) and workdir.strip():
                return workdir
        return self.session.directory

    async def _checkpoint_before_tool(
        self,
        tool_name: str,
        tool_input: Dict[str, Any],
        assistant_msg: MessageInfo,
    ) -> Optional[Dict[str, str]]:
        """Create one rewind checkpoint per user turn per worktree directory."""
        worktree = self._checkpoint_worktree_for_tool(tool_name, tool_input)
        if not worktree:
            return None

        try:
            normalized_worktree = os.path.abspath(os.path.expanduser(worktree))
            lock = self._rewind_checkpoint_locks.get(normalized_worktree)
            if lock is None:
                lock = asyncio.Lock()
                self._rewind_checkpoint_locks[normalized_worktree] = lock

            async with lock:
                existing = self._rewind_checkpoint_by_dir.get(normalized_worktree)
                if existing:
                    return {"snapshot": existing, "worktree": normalized_worktree}

                from flocks.snapshot import Snapshot

                snapshot = await Snapshot.track(self.session.project_id, normalized_worktree)
                if not snapshot:
                    return None
                self._rewind_checkpoint_by_dir[normalized_worktree] = snapshot
                return {"snapshot": snapshot, "worktree": normalized_worktree}
        except Exception as exc:
            log.debug("runner.rewind_checkpoint.start_failed", {
                "session_id": self.session.id,
                "message_id": assistant_msg.id,
                "tool": tool_name,
                "error": str(exc),
            })
            return None

    async def _record_tool_patch(
        self,
        assistant_msg: MessageInfo,
        checkpoint: Dict[str, str],
    ) -> None:
        """Persist changed files after a checkpointed mutating tool completes."""
        snapshot = checkpoint.get("snapshot")
        worktree = checkpoint.get("worktree")
        if not snapshot or not worktree:
            return

        try:
            from flocks.snapshot import Snapshot

            patch = await Snapshot.patch(self.session.project_id, worktree, snapshot)
            if not patch.files:
                return

            patch_part = PatchPart(
                id=Identifier.ascending("part"),
                sessionID=self.session.id,
                messageID=assistant_msg.id,
                hash=snapshot,
                files=patch.files,
            )
            await Message.store_part(self.session.id, assistant_msg.id, patch_part)
            if self.callbacks.event_publish_callback:
                await self.callbacks.event_publish_callback(
                    "message.part.updated",
                    {"part": patch_part.model_dump(by_alias=True, exclude_none=True)},
                )
        except Exception as exc:
            log.debug("runner.rewind_checkpoint.patch_failed", {
                "session_id": self.session.id,
                "message_id": assistant_msg.id,
                "error": str(exc),
            })

    def _should_warn_about_tool_loop(self, *, last_user_id: str) -> bool:
        state = self._get_tool_loop_guard_state(last_user_id=last_user_id)
        return int(state.get("exact_count", 0)) >= max(2, REPEATED_EXACT_TOOL_CALL_HALT_THRESHOLD - 1)

    def _build_tool_loop_halt_message(
        self,
        *,
        tool_name: str,
        count: int,
    ) -> str:
        return (
            f"Stopped the loop because `{tool_name}` was called {count} times in a row "
            "with the same arguments and kept producing a tool-only turn. Change strategy, "
            "summarize the blocker, or answer directly instead of repeating the exact same call."
        )

    def _update_tool_loop_guard(
        self,
        result: StepResult,
        *,
        last_user_id: str,
    ) -> Dict[str, Any]:
        visible_text = bool((result.content or "").strip())
        if visible_text or len(result.tool_calls) != 1:
            self._reset_tool_loop_guard(last_user_id=last_user_id)
            return {"action": "allow"}

        tool_call = result.tool_calls[0]
        state = self._get_tool_loop_guard_state(last_user_id=last_user_id)
        signature = self._canonical_tool_signature(tool_call.name, tool_call.arguments)

        exact_count = 1
        if state.get("last_signature") == signature:
            exact_count = int(state.get("exact_count", 0)) + 1

        state.update({
            "last_user_id": last_user_id,
            "last_signature": signature,
            "exact_count": exact_count,
        })

        if exact_count >= REPEATED_EXACT_TOOL_CALL_HALT_THRESHOLD:
            return {
                "action": "halt",
                "reason": "repeated_exact_tool_call",
                "tool_name": tool_call.name,
                "count": exact_count,
            }
        if exact_count >= max(2, REPEATED_EXACT_TOOL_CALL_HALT_THRESHOLD - 1):
            return {
                "action": "warn",
                "tool_name": tool_call.name,
                "exact_count": exact_count,
            }
        return {"action": "allow"}

    async def _list_callable_tool_infos_for_turn(
        self,
        agent: AgentInfo,
        messages: List[MessageInfo],
    ) -> Tuple[List[Any], Dict[str, Any]]:
        result = await list_session_callable_tool_infos(
            session_id=self.session.id,
            declared_tool_names=getattr(agent, "tools", None),
            step=self._step,
            event_publish_callback=self.callbacks.event_publish_callback,
        )
        return result.tool_infos, dict(result.metadata)

    @staticmethod
    def _get_prompt_tool_names_from_schema(tools: List[Dict[str, Any]]) -> Tuple[str, ...]:
        """Resolve prompt guidance tool names from the loaded tool schema."""
        names = {
            str(function.get("name", "")).strip()
            for tool in tools
            if isinstance(tool, dict)
            for function in [tool.get("function", {})]
            if isinstance(function, dict)
        }
        names.discard("")
        return tuple(sorted(names))

    async def _get_prompt_tool_names(
        self,
        agent: AgentInfo,
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        messages: Optional[List[MessageInfo]] = None,
    ) -> Tuple[str, ...]:
        """Compatibility shim for tests and older call sites."""
        tool_schema = tools
        if tool_schema is None:
            tool_schema = await self._build_callable_tool_schema(agent, messages)
        return self._get_prompt_tool_names_from_schema(tool_schema)

    async def _publish_turn_tools_event(self, selection_metadata: Dict[str, Any]) -> None:
        if not self.callbacks.event_publish_callback:
            return
        try:
            await self.callbacks.event_publish_callback("turn.tools_selected", {
                "sessionID": self.session.id,
                "step": self._step,
                **selection_metadata,
            })
        except Exception as exc:
            log.debug("runner.turn_tools_selected.publish_failed", {"error": str(exc)})

    def _log_perf(self, event: str, started_at: float, **extra: Any) -> None:
        payload = {
            "session_id": self.session.id,
            "step": self._step,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }
        payload.update(extra)
        log.debug(event, payload)

    def _provider_capability_key(self) -> str:
        interleaved = None
        try:
            active_model = Provider.resolve_model(self.provider_id, self.model_id)
            if active_model and getattr(active_model, "capabilities", None):
                interleaved = getattr(active_model.capabilities, "interleaved", None)
        except Exception:
            interleaved = None
        return (
            f"{self.provider_id}:{self.model_id}:"
            f"vision={int(self._model_supports_vision())}:"
            f"interleaved={json.dumps(interleaved, ensure_ascii=False, sort_keys=True, default=str)}"
        )

    def _tool_schema_cache_key(
        self,
        agent: AgentInfo,
        selected_tool_infos: List[Any],
        *,
        text_tool_call_mode: bool,
    ) -> Tuple[Any, ...]:
        return (
            ToolRegistry.revision(),
            getattr(agent, "name", ""),
            tuple(sorted(getattr(agent, "tools", None) or ())),
            tuple(tool_info.name for tool_info in selected_tool_infos),
            text_tool_call_mode,
            self.provider_id,
            self.model_id,
        )

    def _tool_compact_placeholder(self, tool_name: str, text: str) -> Tuple[str, str]:
        normalized = " ".join(text.split())
        preview = normalized[:TOOL_RESULT_PREVIEW_CHARS]
        suffix = "..." if len(normalized) > TOOL_RESULT_PREVIEW_CHARS else ""
        if preview:
            placeholder = (
                f"[Context compacted: `{tool_name}` output omitted to save space. "
                f"Preview: {preview}{suffix}]"
            )
        else:
            placeholder = f"[Context compacted: `{tool_name}` output omitted to save space.]"
        return placeholder, preview

    def _get_persisted_tool_placeholder(self, part: Any, fallback_tool_name: str) -> Optional[str]:
        state = getattr(part, "state", None)
        metadata = getattr(state, "metadata", None) or {}
        placeholder = metadata.get("context_compact_placeholder")
        if placeholder:
            return str(placeholder)
        time_info = getattr(state, "time", None) or {}
        if isinstance(time_info, dict) and time_info.get("compacted"):
            return f"[Context compacted: `{fallback_tool_name}` output omitted to save space.]"
        return None

    async def _persist_tool_compaction(self, refs: List[Dict[str, Any]]) -> int:
        dirty_refs = [ref for ref in refs if ref.get("dirty")]
        if not dirty_refs:
            return 0

        updated_count = 0
        for ref in dirty_refs:
            part = ref["part"]
            state = getattr(part, "state", None)
            if state is None:
                continue
            await Message.update_part(
                self.session.id,
                ref["message_id"],
                part.id,
                state=state.model_dump() if hasattr(state, "model_dump") else state,
            )
            ref["dirty"] = False
            updated_count += 1
        return updated_count

    async def _compact_tool_ref(self, ref: Dict[str, Any], reason: str) -> bool:
        current_content = ref["chat_message"].content or ""
        if ref.get("compacted") or len(current_content) <= TOOL_RESULT_PREVIEW_CHARS:
            return False

        placeholder, preview = self._tool_compact_placeholder(ref["tool_name"], current_content)
        ref["chat_message"].content = placeholder
        ref["char_count"] = len(placeholder)
        ref["compacted"] = True

        part = ref["part"]
        state = getattr(part, "state", None)
        if state is not None:
            metadata = dict(getattr(state, "metadata", None) or {})
            metadata.update({
                "context_compacted": True,
                "context_compact_reason": reason,
                "context_compact_preview": preview,
                "context_compact_placeholder": placeholder,
                "context_compacted_step": self._step,
            })
            state.metadata = metadata
            time_info = dict(getattr(state, "time", None) or {})
            time_info["compacted"] = int(datetime.now().timestamp() * 1000)
            state.time = time_info
            ref["dirty"] = True

        return True

    async def _apply_tool_result_budget(
        self,
        tool_result_refs: List[Dict[str, Any]],
        ctx_window_tokens: int,
    ) -> Dict[str, int]:
        if not tool_result_refs:
            return {"compacted": 0, "persisted": 0}

        total_budget = max(
            TOOL_RESULT_MIN_CHAR_BUDGET,
            int(ctx_window_tokens * 4 * TOOL_RESULT_CHAR_BUDGET_RATIO),
        )
        per_turn_budget = max(
            TOOL_RESULT_MIN_TURN_BUDGET,
            int(total_budget * TOOL_RESULT_TURN_BUDGET_RATIO),
        )
        compacted = 0

        latest_turn = max(ref["turn_index"] for ref in tool_result_refs)
        latest_turn_refs = [ref for ref in tool_result_refs if ref["turn_index"] == latest_turn]
        latest_turn_chars = sum(ref["char_count"] for ref in latest_turn_refs)
        for ref in latest_turn_refs[:-1]:
            if latest_turn_chars <= per_turn_budget:
                break
            if await self._compact_tool_ref(ref, "tool_result_budget"):
                latest_turn_chars = sum(item["char_count"] for item in latest_turn_refs)
                compacted += 1

        total_chars = sum(ref["char_count"] for ref in tool_result_refs)
        for ref in tool_result_refs:
            if total_chars <= total_budget:
                break
            if await self._compact_tool_ref(ref, "context_budget"):
                total_chars = sum(item["char_count"] for item in tool_result_refs)
                compacted += 1

        persisted = await self._persist_tool_compaction(tool_result_refs)
        if compacted and self.callbacks.event_publish_callback:
            try:
                await self.callbacks.event_publish_callback("context.compacted", {
                    "sessionID": self.session.id,
                    "step": self._step,
                    "reason": "tool_result_budget",
                    "compactedToolResults": compacted,
                    "persistedToolResults": persisted,
                })
            except Exception as exc:
                log.debug("runner.context_compacted.publish_failed", {"error": str(exc)})

        return {"compacted": compacted, "persisted": persisted}

    # Provider IDs whose adapters are known to translate Flocks' internal
    # ``{"type": "image", "mimeType": ..., "data": <base64>}`` block into
    # the provider-native multimodal format (e.g. OpenAI ``image_url`` or
    # Anthropic vision blocks).
    #
    # Matched with exact equality (no substring matching) to prevent false
    # positives such as a user-configured "not-openai" or an internal
    # "xxx-llm-gateway" id being mistakenly classified as multimodal-capable.
    #
    # ``custom-`` providers (created via ``POST /api/custom/providers``) are
    # checked separately via ``startswith`` because their ids follow the
    # pattern ``custom-<user-chosen-name>`` and they always use the
    # ``@ai-sdk/openai-compatible`` adapter that handles vision blocks.
    _MULTIMODAL_PROVIDER_NAMES = frozenset({
        "anthropic", "openai", "azure",
        "vertex", "bedrock", "openrouter",
    })

    def _model_supports_vision(self) -> bool:
        """Best-effort vision capability lookup from the model definition.

        Returns ``True`` only when explicitly declared on the model entry via
        ``capabilities.supports_vision``. Defaults to a safe ``False`` for
        unknown configurations.
        """
        try:
            from flocks.provider.provider import Provider as _Provider

            provider = _Provider.get(self.provider_id)
            if provider is not None:
                for model in getattr(provider, "_config_models", []) or []:
                    if model.id == self.model_id:
                        caps = getattr(model, "capabilities", None)
                        if caps and getattr(caps, "supports_vision", False):
                            return True
                        break
        except Exception as exc:
            log.debug("runner.vision_lookup.failed", {"error": str(exc)})
        return False

    def _supports_multimodal_user_content(self) -> bool:
        """Whether the active provider/model can accept image content blocks.

        Decision order:
          1. The model definition explicitly advertises vision support
             (``capabilities.supports_vision`` on the model entry).
          2. The provider id is an exact match against the known multimodal
             provider name set, or starts with ``"custom-"`` (user-registered
             OpenAI-compatible providers that inherit vision capability from
             their underlying model).
        """
        if self._model_supports_vision():
            return True
        provider_id = (self.provider_id or "").lower()
        return (
            provider_id in self._MULTIMODAL_PROVIDER_NAMES
            or provider_id.startswith("custom-")
        )

    def _append_file_content_block(
        self,
        blocks: list[dict[str, Any]],
        text_fallbacks: list[str],
        *,
        mime: str,
        filename: str,
        url: str,
    ) -> None:
        """Append an appropriate content block for *url* into *blocks*.

        Images are embedded as base64 for multimodal-capable providers; other
        file types are extracted as text. We *never* spill a raw ``data:`` URL
        into the text fallback — doing so previously caused OpenAI to tokenize
        the entire base64 payload (~250k tokens for a single screenshot,
        blowing past the model's context window).
        """
        is_image = mime.startswith("image/")
        multimodal_ok = self._supports_multimodal_user_content()

        log.info("runner.file_part.dispatch", {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "mime": mime,
            "filename": filename,
            "is_image": is_image,
            "multimodal_supported": multimodal_ok,
            "url_scheme": url.split(":", 1)[0] if url else None,
            "url_size": len(url),
        })

        # For images we ALWAYS try the multimodal path first, regardless of
        # provider whitelist. The whitelist guards against silently degrading
        # to a text fallback that would tokenize the entire base64 payload —
        # but since fallback now never embeds the URL, "force-try multimodal"
        # is strictly safer: providers that genuinely cannot handle ``image``
        # blocks will surface a precise error, which is far better UX than a
        # 250k-token context_length_exceeded error.
        if is_image:
            import base64 as _b64
            data = read_file_part_bytes(url)
            if data:
                blocks.append({
                    "type": "image",
                    "mimeType": mime,
                    "data": _b64.b64encode(data).decode("utf-8"),
                })
                return
            log.warn("runner.file_part.image_decode_failed", {
                "provider_id": self.provider_id,
                "filename": filename,
            })
            # Image bytes could not be read — fall through to placeholder
            # (which is intentionally tiny, never the raw URL).

        extracted_text = extract_file_text(mime=mime, filename=filename, url=url) if not is_image else None
        if extracted_text:
            text_fallbacks.append(extracted_text)
            blocks.append({"type": "text", "text": extracted_text})
            return

        # Final fallback — for images we either lack vision support or could
        # not decode the bytes. Either way, refuse to embed the raw URL when
        # it is a data URI; the base64 payload would otherwise be sent to the
        # LLM as plain text and explode the prompt token count. Also clamp the
        # placeholder to a hard byte cap as a belt-and-braces measure.
        MAX_PLACEHOLDER_CHARS = 200
        if is_image:
            placeholder = (
                f"[Image: {filename} — model does not support image input; "
                f"the image was omitted from the prompt]"
            )
            log.info("runner.file_part.image_skipped", {
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "reason": "multimodal_unsupported" if not multimodal_ok else "decode_failed",
                "filename": filename,
            })
        else:
            safe_url = url if url and not url.startswith("data:") else ""
            placeholder = (
                f"[File: {filename}]({safe_url})" if safe_url else f"[File: {filename}]"
            )
        if len(placeholder) > MAX_PLACEHOLDER_CHARS:
            placeholder = placeholder[:MAX_PLACEHOLDER_CHARS] + "…"
        text_fallbacks.append(placeholder)

    @classmethod
    async def loop(cls, session_id: str) -> Optional['MessageInfo']:
        """
        Start or continue session processing loop.
        
        This is the main entry point for session execution,
        matching Flocks' SessionPrompt.loop() behavior.
        
        Now delegates to SessionLoop for better separation of concerns.
        
        Args:
            session_id: Session ID to process
            
        Returns:
            Last assistant message with parts
        """
        # Delegate to SessionLoop (new architecture)
        from flocks.session.session_loop import SessionLoop
        
        result = await SessionLoop.run(session_id)
        return result.last_message
    
    @classmethod
    def cancel(cls, session_id: str) -> bool:
        """
        Cancel a running session.
        
        Args:
            session_id: Session ID to cancel
            
        Returns:
            True if session was cancelled
        """
        from flocks.session.core.status import SessionStatus
        
        runner = cls._active_sessions.get(session_id)
        if runner:
            runner.abort()
            del cls._active_sessions[session_id]
            log.info("runner.cancelled", {"session_id": session_id})
        
        # Set status to idle (Flocks compatibility)
        from flocks.session.core.status import SessionStatusIdle
        SessionStatus.set(session_id, SessionStatusIdle())
        return True
    
    @classmethod
    def cancel_children(cls, parent_session_id: str) -> int:
        """Cancel all runners whose session.parent_id matches, recursively."""
        from flocks.session.core.status import SessionStatus, SessionStatusIdle
        
        cancelled = 0
        child_ids = [
            sid for sid, runner in cls._active_sessions.items()
            if getattr(runner.session, 'parent_id', None) == parent_session_id
        ]
        for sid in child_ids:
            runner = cls._active_sessions.pop(sid, None)
            if runner:
                runner.abort()
                SessionStatus.set(sid, SessionStatusIdle())
                cancelled += 1
                log.info("runner.child_cancelled", {
                    "session_id": sid,
                    "parent_session_id": parent_session_id,
                })
            cancelled += cls.cancel_children(sid)
        return cancelled
    
    @classmethod
    async def command(
        cls,
        session_id: str,
        command: str,
        arguments: str = "",
        message_id: Optional[str] = None,
        agent: Optional[str] = None,
        model: Optional[str] = None,
        variant: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Execute a slash command in a session.
        
        Args:
            session_id: Session ID
            command: Command name (e.g., "init", "help")
            arguments: Command arguments
            message_id: Optional message ID
            agent: Optional agent name
            model: Optional model string (provider/model)
            variant: Optional model variant
            
        Returns:
            Command execution result
        """
        from flocks.command.command import Command
        
        # Get command definition
        cmd = Command.get(command)
        if not cmd:
            raise ValueError(f"Command '{command}' not found")
        
        # Parse model if provided
        provider_id, model_id = None, None
        if model:
            parts = model.split("/", 1)
            if len(parts) == 2:
                provider_id, model_id = parts
        
        # Execute command template
        template = cmd.template
        
        # Replace placeholders
        template = template.replace("$ARGUMENTS", arguments)
        
        # Create prompt request
        parts = [{"type": "text", "text": template}]
        
        log.info("runner.command", {
            "session_id": session_id,
            "command": command,
            "arguments": arguments[:50] if arguments else "",
        })
        
        return {
            "command": command,
            "arguments": arguments,
            "template": template,
        }
    
    @classmethod
    async def shell(
        cls,
        session_id: str,
        agent: str,
        command: str,
        model: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Execute a shell command in session context.
        
        Args:
            session_id: Session ID
            agent: Agent name
            command: Shell command to execute
            model: Optional model info
            
        Returns:
            Shell execution result
        """
        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")
        
        cwd = session.directory or os.getcwd()
        
        user_msg = await Message.create(
            session_id=session_id,
            role=MessageRole.USER,
            content="The following tool was executed by the user",
            agent=agent,
        )
        
        assistant_msg = await Message.create(
            session_id=session_id,
            role=MessageRole.ASSISTANT,
            content="",
            agent=agent,
            parent_id=user_msg.id,
        )
        
        start_time = asyncio.get_event_loop().time()
        start_time_ms = int(time.time() * 1000)
        start_snapshot: Optional[str] = None
        if cls._is_dangerous_shell_command(command):
            try:
                from flocks.snapshot import Snapshot

                start_snapshot = await Snapshot.track(session.project_id, cwd)
            except Exception as exc:
                log.debug("runner.shell.snapshot_start_failed", {
                    "session_id": session_id,
                    "message_id": assistant_msg.id,
                    "error": str(exc),
                })

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=300,
            )
            output = (stdout_bytes or b"").decode("utf-8", errors="replace") + \
                     (stderr_bytes or b"").decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0
        except asyncio.TimeoutError:
            output = "Command timed out after 300 seconds"
            exit_code = -1
            try:
                proc.kill()
            except Exception as _kill_err:
                log.debug("runner.shell.kill_failed", {"error": str(_kill_err)})
        except Exception as e:
            output = f"Error executing command: {str(e)}"
            exit_code = -1
        
        end_time = asyncio.get_event_loop().time()
        end_time_ms = int(time.time() * 1000)

        tool_part = ToolPart(
            sessionID=session_id,
            messageID=assistant_msg.id,
            callID=Identifier.create("call"),
            tool="bash",
            state=ToolStateCompleted(
                status="completed",
                input={"command": command},
                output=output,
                title="",
                metadata={
                    "description": "",
                    "exitCode": exit_code,
                },
                time={"start": start_time_ms, "end": end_time_ms},
            ),
        )
        await Message.store_part(session_id, assistant_msg.id, tool_part)

        if start_snapshot:
            try:
                from flocks.snapshot import Snapshot

                patch = await Snapshot.patch(session.project_id, cwd, start_snapshot)
                if patch.files:
                    await Message.store_part(
                        session_id,
                        assistant_msg.id,
                        PatchPart(
                            sessionID=session_id,
                            messageID=assistant_msg.id,
                            hash=start_snapshot,
                            files=patch.files,
                        ),
                    )
            except Exception as exc:
                log.debug("runner.shell.snapshot_finish_failed", {
                    "session_id": session_id,
                    "message_id": assistant_msg.id,
                    "error": str(exc),
                })
        
        log.info("runner.shell", {
            "session_id": session_id,
            "command": command[:50],
            "exit_code": exit_code,
            "duration_ms": int((end_time - start_time) * 1000),
        })
        
        return {
            "info": {
                "id": assistant_msg.id,
                "sessionID": session_id,
                "role": "assistant",
                "agent": agent,
            },
            "parts": [{
                **tool_part.model_dump(by_alias=True, exclude_none=True),
            }],
        }
    
    def abort(self) -> None:
        """Signal abort to stop the loop."""
        self._abort.set()
    
    @property
    def is_aborted(self) -> bool:
        """Check if abort was signaled (internal or external)."""
        if self._abort.is_set():
            return True
        if self._external_abort is not None and self._external_abort.is_set():
            return True
        return False

    async def _process_step(
        self,
        messages: List[MessageInfo],
        last_user: MessageInfo,
    ) -> StepResult:
        """Process a single step in the loop with retry logic."""
        # Check for CLI callbacks (if running in CLI mode)
        # Only use CLI fallback if no callbacks were explicitly provided via constructor
        has_explicit_callbacks = any([
            self.callbacks.on_text_delta,
            self.callbacks.on_tool_start,
            self.callbacks.on_tool_end,
            self.callbacks.on_error,
            self.callbacks.event_publish_callback,
        ])
        if not has_explicit_callbacks:
            try:
                from flocks.cli.session_runner import _get_cli_callbacks
                cli_callbacks = _get_cli_callbacks()
                if cli_callbacks:
                    self.callbacks = cli_callbacks
            except ImportError:
                pass
        
        # Resolve agent
        agent_name = last_user.agent or self.agent_name
        agent = await Agent.get(agent_name) or await Agent.get("rex")

        # Track session agent (Flocks compatibility)
        try:
            from flocks.session.core.session_state import set_session_agent
            set_session_agent(self.session.id, agent.name)
        except Exception as e:
            log.debug("runner.session_agent.error", {"error": str(e)})
        
        # Check if we've reached max steps (matching Flocks logic)
        max_steps = agent.steps if hasattr(agent, 'steps') and agent.steps is not None else DEFAULT_MAX_TOOL_STEPS
        is_last_step = self._step >= max_steps
        
        # Get provider
        provider = Provider.get(self.provider_id)
        if not provider:
            error = f"Provider {self.provider_id} not found"
            if self.callbacks.on_error:
                await self.callbacks.on_error(error)
            return StepResult(action="stop", error=error)

        # Apply config-based provider options (api_key/base_url)
        try:
            await Provider.apply_config(provider_id=self.provider_id)
        except Exception as e:
            log.debug("runner.provider.apply_config.error", {
                "provider": self.provider_id,
                "error": str(e),
            })
        
        if not provider.is_configured():
            error = f"Provider {self.provider_id} not configured"
            if self.callbacks.on_error:
                await self.callbacks.on_error(error)
            return StepResult(action="stop", error=error)
        
        # Build prompts and tools
        tools_started_at = time.perf_counter()
        tools = await self._build_callable_tool_schema(agent, messages)
        self._log_perf("runner.process_step.tools_ready", tools_started_at, tool_count=len(tools))
        prompt_tool_names = self._get_prompt_tool_names_from_schema(tools)

        async def sandbox_prompt_factory() -> Optional[str]:
            return await self._build_sandbox_prompt(agent)

        async def channel_context_prompt_factory() -> Optional[str]:
            return await self._build_channel_context_prompt()

        async def device_asset_prompt_factory() -> Optional[str]:
            return await self._build_device_asset_hint()

        try:
            from flocks.tool.device.store import device_revision as get_device_revision

            current_device_revision = get_device_revision()
        except Exception:
            current_device_revision = None

        prompts_started_at = time.perf_counter()
        system_prompts = await SessionPrompt.build_system_prompts(
            session_id=self.session.id,
            session_directory=self.session.directory,
            agent_name=agent.name,
            agent_prompt=getattr(agent, "prompt", None),
            provider_id=self.provider_id,
            model_id=self.model_id,
            prompt_tool_names=prompt_tool_names,
            tool_revision=ToolRegistry.revision(),
            memory_bootstrap_data=self._memory_bootstrap_data,
            static_cache=self._static_cache,
            sandbox_prompt_factory=sandbox_prompt_factory,
            channel_context_prompt_factory=channel_context_prompt_factory,
            tool_catalog_prompt_factory=lambda: self._build_tool_catalog_prompt(agent),
            device_asset_prompt_factory=device_asset_prompt_factory,
            device_revision=current_device_revision,
            use_text_tool_call_mode=self._should_use_text_tool_call_mode(),
        )
        self._log_perf("runner.process_step.system_prompts_ready", prompts_started_at, prompt_count=len(system_prompts))

        if self._should_use_text_tool_call_mode() and tools:
            text_tool_catalog = self._build_text_tool_call_catalog_prompt(tools)
            if text_tool_catalog:
                system_prompts.append(text_tool_catalog)

        # If the last assistant message only contains tool results and no text,
        # force a direct answer to avoid repeated tool calls.
        last_assistant_msg = None
        for msg in reversed(messages):
            if msg.role == MessageRole.ASSISTANT:
                last_assistant_msg = msg
                break
        if last_assistant_msg:
            parts = await Message.parts(last_assistant_msg.id, self.session.id)
            has_text = any(getattr(p, "type", None) == "text" and getattr(p, "text", "").strip() for p in parts)
            has_tool_result = any(
                getattr(p, "type", None) == "tool" and
                getattr(getattr(p, "state", None), "status", None) in ("completed", "error", "running")
                for p in parts
            )
            if has_tool_result and not has_text:
                from flocks.session.prompt_strings import PROMPT_TOOL_RESULTS_AVAILABLE
                system_prompts.append(PROMPT_TOOL_RESULTS_AVAILABLE)
            
            if has_tool_result and self._should_warn_about_tool_loop(last_user_id=last_user.id):
                state = self._get_tool_loop_guard_state(last_user_id=last_user.id)
                log.warn("runner.repeated_tool_calls_detected", {
                    "tool_name": state.get("last_signature", "").split(":", 1)[0],
                    "exact_count": state.get("exact_count", 0),
                    "step": self._step,
                })
                from flocks.session.prompt_strings import PROMPT_REPEATED_TOOL_CALLS
                system_prompts.append(PROMPT_REPEATED_TOOL_CALLS)

        # Hook pipeline: chat.message stage
        try:
            from flocks.hooks.pipeline import HookPipeline
            user_text = await Message.get_text_content(last_user)
            hook_input = {
                "sessionID": self.session.id,
                "workspace": self.session.directory,
                "agent": agent.name,
                "model": {"providerID": self.provider_id, "modelID": self.model_id},
                "message": {
                    "id": last_user.id,
                    "role": "user",
                    "content": user_text,
                },
            }
            hook_output = {"message": {"variant": getattr(last_user, "variant", None)}}
            ctx = await HookPipeline.run_chat_message(hook_input, hook_output)
            variant = ctx.output.get("message", {}).get("variant") if ctx else None
            if variant:
                await Message.update(self.session.id, last_user.id, variant=variant)
        except Exception as e:
            log.debug("runner.hook.chat_message.error", {"error": str(e)})
        
        # Convert messages to chat format with error handling
        try:
            queued_user_message_ids = self._get_queued_user_message_ids(messages)
            if queued_user_message_ids:
                self._invalidate_chat_context_cache()
            previous_queued_user_ids = getattr(self, "_queued_user_message_ids", None)
            self._queued_user_message_ids = queued_user_message_ids
            chat_messages_started_at = time.perf_counter()
            try:
                chat_messages = await self._to_chat_messages(messages, system_prompts)
            finally:
                if previous_queued_user_ids is None:
                    if hasattr(self, "_queued_user_message_ids"):
                        delattr(self, "_queued_user_message_ids")
                else:
                    self._queued_user_message_ids = previous_queued_user_ids
            self._log_perf(
                "runner.process_step.chat_messages_ready",
                chat_messages_started_at,
                source_message_count=len(messages),
                chat_message_count=len(chat_messages),
            )
        except Exception as e:
            log.error("runner.to_chat_messages.error", {
                "error": str(e),
                "error_type": type(e).__name__,
                "message_count": len(messages),
            })
            raise
        
        # CRITICAL FIX: Ensure messages don't end with assistant role when tools are present
        # This prevents "assistant role in the final position when tools are used" API error
        # This commonly happens when:
        # 1. Tool calls fail (e.g., missing required parameters)
        # 2. Assistant message contains tool calls but no user response follows
        if chat_messages and tools and not self._should_use_text_tool_call_mode():
            last_message = chat_messages[-1]
            if last_message.role == "assistant":
                # Check if we need to add a synthetic user message
                # This is required by Anthropic API when using tools
                from flocks.session.prompt_strings import PROMPT_SYNTHETIC_CONTINUE
                synthetic_msg = ChatMessage(
                    role="user",
                    content=PROMPT_SYNTHETIC_CONTINUE,
                )
                chat_messages.append(synthetic_msg)
                log.info("runner.synthetic_user_added", {
                    "reason": "assistant_at_end_with_tools",
                    "step": self._step,
                    "session_id": self.session.id,
                })
        
        # Add max steps warning if this is the last step (matching Flocks)
        if is_last_step:
            from flocks.session.prompt_strings import PROMPT_MAX_STEPS
            chat_messages.append(ChatMessage(
                role="assistant",
                content=PROMPT_MAX_STEPS,
            ))
            
            log.warn("runner.max_steps_reached", {
                "step": self._step,
                "max_steps": max_steps,
                "session_id": self.session.id,
            })
            
            # Disable tools when max steps reached
            tools = []
        
        # Create assistant message (will be reused across retries)
        assistant_msg = await Message.create(
            session_id=self.session.id,
            role=MessageRole.ASSISTANT,
            content="",
            agent=agent.name,
            model_id=self.model_id,
            provider_id=self.provider_id,
            parent_id=last_user.id,
        )
        
        # Publish assistant message SSE event so frontends can show the message card
        if self.callbacks.event_publish_callback:
            import time as _time
            await self.callbacks.event_publish_callback("message.updated", {
                "info": {
                    "id": assistant_msg.id,
                    "sessionID": self.session.id,
                    "role": "assistant",
                    "time": {"created": int(_time.time() * 1000)},
                    "parentID": last_user.id,
                    "modelID": self.model_id,
                    "providerID": self.provider_id,
                    "agent": agent.name,
                    "mode": agent.name,
                    "tokens": {"input": 0, "output": 0, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                }
            })
        
        # Retry loop matching Flocks' SessionProcessor.process()
        # MAX_ERROR_RETRIES caps exception-based retries so a permanently-failing
        # model endpoint (e.g. repeated 500) cannot hold the session loop open
        # forever, which would block every subsequent user message via
        # loop.already_running.
        # The two counters are independent: empty-response retries (transient
        # model quirk) and exception retries (API errors) track separately so
        # that one kind of failure doesn't eat the other's budget.
        MAX_ERROR_RETRIES = 3
        MAX_EMPTY_RETRIES = 3
        error_attempt = 0
        empty_attempt = 0
        self._reset_rewind_checkpoint_turn(last_user.id)

        while not self.is_aborted:
            try:
                # Set status to busy
                SessionStatus.set(self.session.id, SessionStatusBusy())

                # Call LLM with tools
                result = await self._call_llm(
                    provider=provider,
                    messages=chat_messages,
                    tools=tools,
                    agent=agent,
                    assistant_msg=assistant_msg,
                )

                if getattr(self, "_llm_call_aborted", False):
                    await Message.update(
                        self.session.id,
                        assistant_msg.id,
                        error=self._build_message_aborted_error(),
                        finish="error",
                    )
                    await self._record_usage_if_available(result.usage, message_id=assistant_msg.id)
                    return StepResult(
                        action="stop",
                        content=result.content,
                        usage=result.usage,
                    )

                # Detect empty response: some models (e.g. MiniMax) occasionally
                # return 0 chunks with finish_reason=stop after tool execution,
                # producing no text and no tool calls. Treat this as a transient
                # failure and retry with exponential backoff instead of silently
                # terminating the agent.
                if (result.action == "stop" and not result.error
                        and not result.content and not result.tool_calls):
                    empty_attempt += 1
                    if empty_attempt <= MAX_EMPTY_RETRIES:
                        # Record usage for this empty attempt even though we are
                        # about to retry – the provider may have already charged
                        # for the tokens returned in this response.
                        await self._record_usage_if_available(result.usage, message_id=assistant_msg.id)
                        delay_ms = SessionRetry.delay(empty_attempt)
                        next_retry_time = int(asyncio.get_event_loop().time() * 1000) + delay_ms
                        log.warn("runner.step.empty_response_retry", {
                            "attempt": empty_attempt,
                            "delay_ms": delay_ms,
                            "session_id": self.session.id,
                            "model": self.model_id,
                        })
                        SessionStatus.set(
                            self.session.id,
                            SessionStatusRetry(
                                attempt=empty_attempt,
                                message="Model returned empty response, retrying...",
                                next=next_retry_time,
                            )
                        )
                        await SessionRetry.sleep(delay_ms, self._abort)
                        continue
                    else:
                        # All retries exhausted — surface a clear error so the
                        # user knows the model is incompatible, rather than
                        # silently hanging or showing a blank response.
                        empty_error_msg = (
                            f"Model '{self.model_id}' returned an empty response "
                            f"after {MAX_EMPTY_RETRIES} retries."
                        )
                        log.error("runner.step.empty_response_exhausted", {
                            "session_id": self.session.id,
                            "model": self.model_id,
                            "attempts": empty_attempt,
                        })
                        empty_error_dict = {
                            "name": "EmptyResponseError",
                            "message": empty_error_msg,
                            "data": {
                                "message": empty_error_msg,
                                "model": self.model_id,
                                "attempts": empty_attempt,
                            },
                        }
                        if self.callbacks.on_error:
                            await self.callbacks.on_error(empty_error_msg)
                        await Message.update(
                            self.session.id,
                            assistant_msg.id,
                            error=empty_error_dict,
                            finish="error",
                        )
                        return StepResult(action="stop", error=empty_error_msg)

                tool_loop_guard = self._update_tool_loop_guard(
                    result,
                    last_user_id=last_user.id,
                )
                if tool_loop_guard.get("action") == "halt":
                    halt_message = self._build_tool_loop_halt_message(
                        tool_name=str(tool_loop_guard.get("tool_name") or "tool"),
                        count=int(tool_loop_guard.get("count", 0) or 0),
                    )
                    log.warn("runner.tool_loop_guard_halt", {
                        "tool_name": tool_loop_guard.get("tool_name"),
                        "reason": tool_loop_guard.get("reason"),
                        "count": tool_loop_guard.get("count"),
                        "step": self._step,
                    })
                    await Message.update(
                        self.session.id,
                        assistant_msg.id,
                        content=halt_message,
                    )
                    result = StepResult(
                        action="stop",
                        content=halt_message,
                        usage=result.usage,
                    )

                # Success! Update finish reason
                finish = "tool-calls" if result.tool_calls else "stop"
                await Message.update(self.session.id, assistant_msg.id, finish=finish)
                await self._record_usage_if_available(result.usage, message_id=assistant_msg.id)

                # Note: Compaction check is now done in the main loop (run()) before processing step
                # This matches Flocks's logic: check lastFinished.tokens at loop start

                return result
                    
            except Exception as e:
                error_attempt += 1
                log.error("runner.step.error", {
                    "error": str(e),
                    "attempt": error_attempt,
                })

                # Convert exception to error dict for retry check
                error_dict = self._exception_to_error_dict(e)

                # Check if retryable
                retry_message = SessionRetry.retryable(error_dict)

                if retry_message is not None and error_attempt <= MAX_ERROR_RETRIES:
                    # Error is retryable and we have budget left
                    delay_ms = SessionRetry.delay(error_attempt, error_dict)
                    # Always cap the sleep to RETRY_MAX_DELAY_NO_HEADERS so a
                    # headers-present but retry-after-absent 500 response cannot
                    # cause multi-minute sleeps that block the loop.
                    from flocks.session.lifecycle.retry import RETRY_MAX_DELAY_NO_HEADERS
                    delay_ms = min(delay_ms, RETRY_MAX_DELAY_NO_HEADERS)
                    next_retry_time = int(asyncio.get_event_loop().time() * 1000) + delay_ms

                    log.info("runner.step.retry", {
                        "attempt": error_attempt,
                        "delay_ms": delay_ms,
                        "reason": retry_message,
                        "max_retries": MAX_ERROR_RETRIES,
                    })

                    # Set retry status
                    SessionStatus.set(
                        self.session.id,
                        SessionStatusRetry(
                            attempt=error_attempt,
                            message=retry_message,
                            next=next_retry_time,
                        )
                    )

                    # Wait before retry
                    await SessionRetry.sleep(delay_ms, self._abort)

                    # Continue to next retry attempt
                    continue
                else:
                    # Error is not retryable, or retry budget exhausted
                    if retry_message is not None:
                        log.error("runner.step.max_retries_exceeded", {
                            "error": str(e),
                            "attempt": error_attempt,
                            "max_retries": MAX_ERROR_RETRIES,
                        })
                    else:
                        log.error("runner.step.not_retryable", {"error": str(e)})

                    final_error_message = str(e)
                    if SessionRetry.is_connection_error(error_dict):
                        final_error_message = CONNECTION_ERROR_DISPLAY_MESSAGE
                        error_dict["data"]["displayMessage"] = CONNECTION_ERROR_DISPLAY_MESSAGE

                    if self.callbacks.on_error:
                        await self.callbacks.on_error(final_error_message)

                    # Update assistant message with error (must be dict, not string)
                    await Message.update(
                        self.session.id,
                        assistant_msg.id,
                        error=error_dict,
                        finish="error",
                    )

                    return StepResult(action="stop", error=final_error_message)

        return StepResult(action="stop", error="Aborted")

    @staticmethod
    def _build_tokens_update(stream_usage: Optional[Dict[str, int]]) -> Optional[Dict[str, Any]]:
        """Normalize runner token updates from provider usage."""
        if not stream_usage:
            return None
        return {
            "input": stream_usage.get("prompt_tokens", 0),
            "output": stream_usage.get("completion_tokens", 0),
            "reasoning": stream_usage.get("reasoning_tokens", 0),
            "cache": {
                "read": stream_usage.get("cache_read_input_tokens", 0),
                "write": stream_usage.get("cache_creation_input_tokens", 0),
            },
        }

    def _resolve_usage_pricing(self) -> Optional[Any]:
        """Resolve pricing config for the current provider/model pair."""
        from flocks.provider.usage_service import resolve_usage_pricing

        return resolve_usage_pricing(self.provider_id, self.model_id)

    async def _record_usage_if_available(
        self,
        usage: Optional[Dict[str, int]],
        *,
        message_id: Optional[str] = None,
    ) -> None:
        """Persist usage records without blocking successful session steps.

        All exceptions – including ImportError when server routes are absent
        in CLI-only environments – are caught here so that a usage-recording
        failure can never corrupt an already-successful step result.

        Uses the shared provider-layer usage service so that CLI and HTTP
        callers rely on the same persistence and aggregation path.
        """
        if not usage:
            return

        try:
            from flocks.provider.usage_service import RecordUsageRequest, record_usage

            await record_usage(
                RecordUsageRequest(
                    provider_id=self.provider_id,
                    model_id=self.model_id,
                    session_id=self.session.id,
                    message_id=message_id,
                    input_tokens=usage.get("prompt_tokens", 0),
                    output_tokens=usage.get("completion_tokens", 0),
                    cached_tokens=usage.get("cache_read_input_tokens", 0),
                    cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
                    reasoning_tokens=usage.get("reasoning_tokens", 0),
                    pricing=self._resolve_usage_pricing(),
                )
            )
        except Exception as exc:
            log.warn("runner.usage_record_failed", {
                "session_id": self.session.id,
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "error": str(exc),
            })
    
    async def _build_device_asset_hint(self) -> Optional[str]:
        """Return concise device-aware tool guidance plus enabled device summary."""
        try:
            from flocks.tool.device.store import list_devices

            devices = await list_devices()
        except Exception as exc:
            log.warn("runner.device_hint_failed", {"error": str(exc)})
            devices = []

        vendor_by_storage_key: Dict[str, str] = {}
        try:
            for tool_info in ToolRegistry.list_tools():
                if getattr(tool_info, "source", None) != "device":
                    continue
                storage_key = str(getattr(tool_info, "provider", "") or "").strip()
                vendor = str(getattr(tool_info, "vendor", "") or "").strip()
                if storage_key and vendor and storage_key not in vendor_by_storage_key:
                    vendor_by_storage_key[storage_key] = vendor
        except Exception as exc:
            log.warn("runner.device_hint_vendor_map_failed", {"error": str(exc)})

        enabled_devices = [device for device in devices if getattr(device, "enabled", False)]
        device_lines = []
        for device in enabled_devices:
            vendor = vendor_by_storage_key.get(device.storage_key) or "unknown"
            device_lines.append(f"- `{device.name}` -> `{vendor}`")

        summary = (
            "当前已启用设备（名称 -> 厂商）:\n"
            + ("\n".join(device_lines) if device_lines else "- 当前无已启用设备")
        )

        return (
            "## 安全设备使用\n\n"
            f"{summary}\n\n"
            "当用户要操作特定机房、设备或产品时，先调用 `device_context` 获取 `device_id` 等相关信息。"
            "使用 `tool_search` 搜索工具名称查看用法；执行设备工具时必须传入目标 `device_id`。"
            "如果同类设备有多个候选，不要猜测，先询问用户选择。"
        )

    async def _build_sandbox_prompt(self, agent: AgentInfo) -> Optional[str]:
        """Build sandbox context prompt when sandboxing is active."""
        try:
            from flocks.config import Config
            from flocks.session.core.session_state import get_main_session_id
            from flocks.sandbox.system_prompt import build_sandbox_system_prompt

            cfg = await Config.get()
            config_data = cfg.model_dump(by_alias=True, exclude_none=True)
            session_key = self.session.id
            main_session_key = get_main_session_id() or self.session.id
            return await build_sandbox_system_prompt(
                config_data=config_data,
                session_key=session_key,
                agent_id=agent.name,
                main_session_key=main_session_key,
                workspace_dir=self.session.directory,
            )
        except Exception as e:
            log.debug("runner.sandbox_prompt.error", {"error": str(e)})
            return None

    async def _build_channel_context_prompt(self) -> Optional[str]:
        """Build a brief system prompt snippet describing the IM channel context.

        When the current session was initiated from an IM channel (Feishu, WeCom,
        or DingTalk), this injects a one-liner so the agent knows:
          - which platform the user is on
          - the current Flocks session ID

        The lookup is best-effort: if the binding table is unavailable or the
        session has no binding the method returns None silently.
        """
        try:
            from flocks.channel.inbound.session_binding import SessionBindingService

            svc = SessionBindingService()
            bindings = await svc.get_bindings_by_session(self.session.id)
            if not bindings:
                return (
                    f"## Current Session Context\n\n"
                    f"This conversation originates from the **Flocks Web UI** (not an IM channel).\n"
                    f"Session ID: {self.session.id}\n\n"
                    f"When the user asks to send a message to an IM platform, you do NOT have a "
                    f"target IM session ID yet — you must discover and ask the user to pick one."
                )

            # Map channel_id prefix → human-readable platform name
            _CHANNEL_NAMES = {
                "feishu": "Feishu (飞书)",
                "wecom": "WeCom (企业微信)",
                "dingtalk": "DingTalk (钉钉)",
            }

            parts = []
            for b in bindings:
                cid = b.channel_id or ""
                platform = next(
                    (name for key, name in _CHANNEL_NAMES.items() if cid.startswith(key)),
                    cid,
                )
                chat_type = b.chat_type.value if b.chat_type else "unknown"
                parts.append(f"- Platform: {platform}, chat_type: {chat_type}, channel_id: {cid}")

            lines = "\n".join(parts)
            return (
                f"## Current IM Channel Context\n\n"
                f"This conversation originates from an IM channel. Details:\n"
                f"{lines}\n"
                f"Session ID: {self.session.id}\n\n"
                f"You can use this information when the user asks which platform they are "
                f"on, when sending messages back to the channel, or when the context of "
                f"the conversation depends on the IM source."
            )
        except Exception as e:
            log.debug("runner.channel_context_prompt.error", {"error": str(e)})
            return None

    def _list_catalog_tool_infos(self, agent: AgentInfo) -> List[Any]:
        tool_infos: List[Any] = []
        is_rex = getattr(agent, "name", "") == "rex"

        for tool_info in list_tool_catalog_infos():
            if is_rex:
                tool_infos.append(tool_info)
                continue

            metadata = get_tool_catalog_metadata(tool_info.name, tool_info)
            if not agent_declares_tool(agent, tool_info.name) and not metadata.always_load:
                continue
            tool_infos.append(tool_info)

        return tool_infos

    def _build_tool_catalog_prompt(self, agent: AgentInfo) -> Optional[str]:
        from flocks.command.direct import format_tools_catalog_summary
        from flocks.agent.toolset import get_all_enabled_builtin_tool_names

        is_rex = getattr(agent, "name", "") == "rex"
        if not is_rex:
            return None

        catalog_tools = self._list_catalog_tool_infos(agent)
        excluded_tool_names = (
            set(get_all_enabled_builtin_tool_names())
            | get_always_load_tool_names()
        )
        catalog_tools = [
            tool_info
            for tool_info in catalog_tools
            if tool_info.name not in excluded_tool_names
            and getattr(tool_info, "source", None) != "device"
        ]
        if not catalog_tools:
            return None

        catalog_summary = format_tools_catalog_summary(
            tools=catalog_tools,
            max_description_chars=100,
            include_tip=False,
        )
        if not catalog_summary:
            return None

        rules = (
            "The following deferred tools are available via `tool_search`. "
            "Their schemas are NOT loaded - calling them directly will fail "
            "with `InputValidationError`. Use `tool_search` with query "
            "`select:<name>[,<name>...]` to load tool schemas before calling them:"
        )

        return (
            "## Tool Catalog Awareness\n\n"
            f"{rules}\n\n"
            f"{catalog_summary}"
        )

    def _should_use_text_tool_call_mode(self) -> bool:
        # MiniMax models exposed through ThreatBook-managed gateways do not
        # forward the OpenAI ``tool_calls`` field on their streaming chunks
        # (observed on ``threatbook-cn-llm`` 2026-04: first ``ChoiceDelta``
        # only contains ``content`` / ``role``). Without this opt-in the
        # model can never request a tool and ends every turn with
        # ``finish_reason=stop`` and zero tool calls. Force the MiniMax XML
        # text-call protocol for these provider/model pairs so tools work.

        # model_lower = (self.model_id or "").lower()
        # provider_lower = (self.provider_id or "").lower()
        # minimax_text_tool_call_providers = {
        #     "custom-threatbook-internal",
        #     "custom-tb-inner",
        #     "threatbook-cn-llm",
        # }
        # return (
        #     "minimax" in model_lower
        #     and provider_lower in minimax_text_tool_call_providers
        # )
        return False

    def _build_text_tool_call_catalog_prompt(self, tools: List[Dict[str, Any]]) -> Optional[str]:
        if not tools:
            return None

        lines = [
            "## Available Tools",
            "Use only the following tools when emitting MiniMax XML tool calls.",
            "This section is the authoritative callable schema for this turn.",
            "Parameter names must match exactly. Never infer or rename arguments from the awareness catalog.",
        ]
        for tool in tools:
            fn = tool.get("function", {})
            name = fn.get("name", "")
            if not name:
                continue
            description = fn.get("description", "").strip()
            params = fn.get("parameters", {}) or {}
            properties = params.get("properties", {}) if isinstance(params, dict) else {}
            required = params.get("required", []) if isinstance(params, dict) else []

            lines.append(f"- `{name}`: {description or 'No description provided.'}")
            if properties:
                lines.append("  Parameters:")
                for param_name, spec in properties.items():
                    param_type = spec.get("type", "any") if isinstance(spec, dict) else "any"
                    param_desc = spec.get("description", "") if isinstance(spec, dict) else ""
                    required_suffix = "required" if param_name in required else "optional"
                    if param_desc:
                        lines.append(f"  - `{param_name}` ({param_type}, {required_suffix}): {param_desc}")
                    else:
                        lines.append(f"  - `{param_name}` ({param_type}, {required_suffix})")

        return "\n".join(lines)
    
    async def _build_callable_tool_schema(
        self,
        agent: AgentInfo,
        messages: Optional[List[MessageInfo]] = None,
    ) -> List[Dict[str, Any]]:
        """Build tool definitions for LLM."""
        started_at = time.perf_counter()
        selected_tool_infos, selection_metadata = await self._list_callable_tool_infos_for_turn(agent, messages or [])
        await self._publish_turn_tools_event(selection_metadata)

        text_tool_call_mode = self._should_use_text_tool_call_mode()
        cache_key = self._tool_schema_cache_key(
            agent,
            selected_tool_infos,
            text_tool_call_mode=text_tool_call_mode,
        )
        schema_cache = self._static_cache.setdefault("tool_schema_cache", {})
        cached_tools = schema_cache.get(cache_key)
        if cached_tools is not None:
            self._log_perf(
                "runner.tools_schema_cached",
                started_at,
                selected=len(cached_tools),
                enabled=selection_metadata.get("enabledToolCount"),
            )
            return copy.deepcopy(cached_tools)

        tools = []
        for tool_info in selected_tool_infos:
            description = tool_info.description

            # Surface provider/service version to the model so it can pick
            # version-appropriate parameters (e.g. SIP v9.2 vs older spec).
            description = _annotate_with_provider_version(tool_info, description)

            schema = tool_info.get_schema()
            tool_def = {
                "type": "function",
                "function": {
                    "name": tool_info.name,
                    "description": description,
                    "parameters": schema.to_json_schema(),
                }
            }
            tools.append(tool_def)

        schema_cache[cache_key] = copy.deepcopy(tools)

        log.info("runner.tools_selected", {
            "session_id": self.session.id,
            "step": self._step,
            "selected": len(tools),
            "enabled": selection_metadata.get("enabledToolCount"),
        })
        self._log_perf(
            "runner.tools_schema_built",
            started_at,
            selected=len(tools),
            enabled=selection_metadata.get("enabledToolCount"),
        )
        return tools
    
    def _agent_declares_tool(self, agent: AgentInfo, tool_name: str) -> bool:
        """Check if agent statically declares a tool."""
        tool = ToolRegistry.get(tool_name)
        if tool is None:
            return False
        metadata = get_tool_catalog_metadata(tool_name, tool.info)
        return agent_declares_tool(agent, tool_name) or metadata.always_load
    
    def _exception_to_error_dict(self, exception: Exception) -> Dict[str, Any]:
        """
        Convert exception to error dict for retry checking.
        
        Ported from original MessageV2.fromError() structure.
        """
        error_dict = {
            "name": type(exception).__name__,
            "message": str(exception),
            "data": {
                "message": str(exception),
            }
        }
        
        # Check if it's an API error with specific attributes
        if hasattr(exception, 'status_code'):
            status_code = getattr(exception, 'status_code')
            error_dict["name"] = "APIError"
            error_dict["data"]["statusCode"] = status_code
            
            # Determine if retryable based on status code
            is_retryable = status_code in {429, 500, 502, 503, 504}
            error_dict["data"]["isRetryable"] = is_retryable
            
            # Extract response headers if available
            if hasattr(exception, 'response') and hasattr(exception.response, 'headers'):
                headers = dict(exception.response.headers)
                error_dict["data"]["responseHeaders"] = headers
        
        # Check for common retryable error patterns
        error_msg = str(exception).lower()
        if any(pattern in error_msg for pattern in [
            "rate limit", "too many requests", "429",
            "overloaded", "unavailable", "503", "502",
            "timeout", "timed out", "server error",
        ]):
            error_dict["name"] = "APIError"
            error_dict["data"]["isRetryable"] = True

        if SessionRetry.is_connection_error(error_dict):
            error_dict["name"] = "APIError"
            error_dict["data"]["isRetryable"] = True
            error_dict["data"]["displayMessage"] = CONNECTION_ERROR_DISPLAY_MESSAGE
        
        return error_dict
    
    def _get_context_window_tokens(self) -> int:
        """Resolve the context window size for the current model."""
        try:
            ctx, _, _ = Provider.resolve_model_info(self.provider_id, self.model_id)
            if ctx and ctx > 0:
                return ctx
        except Exception:
            pass
        return 128_000

    def _message_conversion_cache_key(
        self,
        msg: MessageInfo,
        parts: List[Any],
        *,
        is_latest_user_turn: bool,
    ) -> Tuple[Any, ...]:
        role = msg.role if isinstance(msg.role, str) else getattr(msg.role, "value", None)
        has_file_part = any(getattr(part, "type", None) == "file" for part in parts)
        latest_user_marker = msg.id if (role == "user" and has_file_part and is_latest_user_turn) else (
            "stale-file-user" if role == "user" and has_file_part else None
        )
        return (
            msg.id,
            role,
            bool(getattr(msg, "compacted", None)),
            getattr(msg, "finish", None),
            json.dumps(getattr(msg, "error", None), ensure_ascii=False, sort_keys=True, default=str),
            Message.get_parts_revision(self.session.id, msg.id),
            latest_user_marker,
            self._provider_capability_key(),
        )

    @staticmethod
    def _build_message_aborted_error() -> Dict[str, Any]:
        message = "Message generation was interrupted before completion."
        return {
            "name": "MessageAbortedError",
            "message": message,
            "data": {
                "message": message,
            },
        }

    @staticmethod
    def _message_role_value(msg: MessageInfo) -> Optional[str]:
        return msg.role if isinstance(msg.role, str) else getattr(msg.role, "value", None)

    def _get_queued_user_message_ids(
        self,
        messages: List[MessageInfo],
    ) -> set[str]:
        if self._step <= 1:
            return set()

        last_finished_index: Optional[int] = None
        for idx, msg in enumerate(messages):
            role = self._message_role_value(msg)
            finish = getattr(msg, "finish", None)
            if role == "assistant" and finish and finish not in ("tool-calls", "unknown"):
                last_finished_index = idx

        if last_finished_index is None:
            return set()

        queued_user_ids: set[str] = set()
        seen_turn_root_user = False
        for msg in messages[last_finished_index + 1:]:
            if self._message_role_value(msg) != "user":
                continue
            if not seen_turn_root_user:
                seen_turn_root_user = True
                continue
            queued_user_ids.add(msg.id)
        return queued_user_ids

    def _invalidate_chat_context_cache(self) -> None:
        self._static_cache.pop("chat_context_cache", None)

    @staticmethod
    def _wrap_queued_user_text(content: str) -> str:
        from flocks.session.prompt_strings import SYNTHETIC_MESSAGE_MARKERS

        if not content or any(marker in content for marker in SYNTHETIC_MESSAGE_MARKERS):
            return content
        return (
            "<system-reminder>\n"
            "The user sent the following message:\n"
            f"{content}\n\n"
            "Please address this message and continue with your tasks.\n"
            "</system-reminder>"
        )

    def _wrap_queued_user_blocks(
        self,
        blocks: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        first_text_idx: Optional[int] = None
        for idx, block in enumerate(blocks):
            if isinstance(block, dict) and block.get("type") == "text":
                first_text_idx = idx
                break
        if first_text_idx is None:
            return blocks

        text_val = blocks[first_text_idx].get("text") or ""
        wrapped_text = self._wrap_queued_user_text(text_val)
        if wrapped_text == text_val:
            return blocks

        wrapped_blocks = copy.deepcopy(blocks)
        wrapped_blocks[first_text_idx] = {
            "type": "text",
            "text": wrapped_text,
        }
        return wrapped_blocks

    @staticmethod
    def _clone_cached_chat_messages(payloads: List[Dict[str, Any]]) -> List[ChatMessage]:
        return [ChatMessage.model_validate(copy.deepcopy(payload)) for payload in payloads]

    def _build_tool_output_text(self, part: Any, tool_name: str, ctx_window_tokens: int) -> Tuple[str, bool, bool]:
        state = getattr(part, "state", None)
        metadata = dict(getattr(state, "metadata", None) or {}) if state is not None else {}
        persisted_placeholder = self._get_persisted_tool_placeholder(part, tool_name)
        if persisted_placeholder:
            return persisted_placeholder, False, True

        cached_output_text = metadata.get("llm_output_text")
        if isinstance(cached_output_text, str):
            tool_output_str = cached_output_text
        else:
            tool_output = getattr(state, "output", "") if state is not None else ""
            if hasattr(state, "get_output_str"):
                tool_output_str = state.get_output_str()
            elif isinstance(tool_output, str):
                tool_output_str = tool_output
            else:
                try:
                    tool_output_str = json.dumps(tool_output, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    tool_output_str = str(tool_output)

        from flocks.tool.truncation import truncate_tool_result_dynamic, HARD_MAX_TOOL_RESULT_CHARS
        already_truncated = (
            metadata.get("truncated")
            and len(tool_output_str) <= HARD_MAX_TOOL_RESULT_CHARS * 2
        )
        if already_truncated:
            return tool_output_str, False, False

        tool_output_str, was_dyn_truncated = truncate_tool_result_dynamic(
            tool_output_str,
            ctx_window_tokens,
        )
        return tool_output_str, was_dyn_truncated, False

    def _build_system_message_content(
        self,
        system_prompts: List[str],
    ) -> str | list[dict[str, Any]]:
        """Format system prompts for the active provider.

        Anthropic supports structured system blocks, which lets us place a
        conservative cache breakpoint before the dynamic runtime tail.
        """
        prompt_parts = [prompt for prompt in system_prompts if prompt and prompt.strip()]
        if not prompt_parts:
            return ""

        provider_lower = (self.provider_id or "").lower()
        if "anthropic" not in provider_lower:
            return "\n\n".join(prompt_parts)

        cache_break_index = max(0, len(prompt_parts) - 3)
        blocks: list[dict[str, Any]] = []
        for index, prompt in enumerate(prompt_parts):
            block: dict[str, Any] = {
                "type": "text",
                "text": prompt,
            }
            if index == cache_break_index:
                block["cache_control"] = {"type": "ephemeral"}
            blocks.append(block)
        return blocks

    async def _to_chat_messages(
        self,
        messages: List[MessageInfo],
        system_prompts: List[str],
    ) -> List[ChatMessage]:
        """
        Convert messages to chat format with tool calls.
        
        Ported from original MessageV2.toModelMessage() logic:
        - Include text parts
        - Include tool calls and results
        - Format tool results as user messages
        """
        started_at = time.perf_counter()
        chat_messages: List[ChatMessage] = []
        ctx_window_tokens = self._get_context_window_tokens()
        tool_result_refs: List[Dict[str, Any]] = []
        turn_index = 0
        queued_user_message_ids: set[str] = set(getattr(self, "_queued_user_message_ids", set()) or set())
        active_model = Provider.resolve_model(self.provider_id, self.model_id)
        active_interleaved = (
            getattr(active_model.capabilities, "interleaved", None)
            if active_model and getattr(active_model, "capabilities", None)
            else None
        )

        # Identify the last USER message — only that one keeps real image
        # bytes in its content blocks. Earlier turns get a short text
        # placeholder so we don't ship hundreds of KB of base64 back to the
        # model on every follow-up. Even providers that count vision tokens
        # natively (OpenAI proper) charge per resent image, and gateways that
        # tokenize the data URL as plain text (e.g. some Azure proxies) will
        # blow past the context window after the second turn otherwise.
        last_user_msg_id: Optional[str] = None
        for _msg in messages:
            _role = _msg.role if isinstance(_msg.role, str) else getattr(_msg.role, "value", None)
            if _role == "user":
                last_user_msg_id = _msg.id

        preloaded_parts: List[List[Any]] = []
        message_signatures: List[Tuple[Any, ...]] = []
        for msg in messages:
            parts = await Message.parts(msg.id, self.session.id)
            preloaded_parts.append(parts)
            message_signatures.append(
                self._message_conversion_cache_key(
                    msg,
                    parts,
                    is_latest_user_turn=(msg.id == last_user_msg_id),
                )
            )

        system_content = self._build_system_message_content(system_prompts) if system_prompts else None
        system_cache_key = json.dumps(system_content, ensure_ascii=False, sort_keys=True, default=str)
        context_cache = self._static_cache.setdefault("chat_context_cache", {})
        cached_context = context_cache.get("latest")
        resume_message_index = 0
        if cached_context and cached_context.get("system_cache_key") == system_cache_key:
            cached_signatures = list(cached_context.get("message_signatures") or [])
            if len(cached_signatures) <= len(message_signatures):
                prefix_matches = True
                for idx, cached_signature in enumerate(cached_signatures):
                    if cached_signature != message_signatures[idx]:
                        prefix_matches = False
                        break
                if prefix_matches:
                    chat_messages = self._clone_cached_chat_messages(cached_context.get("chat_messages") or [])
                    resume_message_index = len(cached_signatures)
                    turn_index = sum(
                        1
                        for msg in messages[:resume_message_index]
                        if msg.role == MessageRole.USER or (isinstance(msg.role, str) and msg.role == "user")
                    )
                    self._log_perf(
                        "runner.to_chat_messages.cache_hit",
                        started_at,
                        reused_messages=resume_message_index,
                        total_messages=len(messages),
                    )

        # Add system prompts
        if system_prompts and not chat_messages:
            chat_messages.append(ChatMessage(
                role="system",
                content=system_content,
            ))
        
        # Convert each message with parts
        for idx, msg in enumerate(messages):
            if idx < resume_message_index:
                continue
            role = msg.role if isinstance(msg.role, str) else msg.role.value
            if role == "user":
                turn_index += 1
            is_latest_user_turn = msg.id == last_user_msg_id
            # Get message parts
            parts = preloaded_parts[idx]
            
            if not parts:
                # Fallback: use text content only
                content = await Message.get_text_content(msg)
                if content.strip():
                    normalized_content = _expand_workflow_node_ref(content)
                    if role == "user" and msg.id in queued_user_message_ids:
                        normalized_content = self._wrap_queued_user_text(normalized_content)
                    chat_messages.append(ChatMessage(
                        role=role,
                        content=normalized_content,
                    ))
                continue
            
            # Build message content from parts
            if msg.role == MessageRole.USER or (isinstance(msg.role, str) and msg.role == "user"):
                is_queued_user_turn = msg.id in queued_user_message_ids
                user_content_parts = []
                user_content_blocks: list[dict[str, Any]] = []
                for part in parts:
                    if hasattr(part, 'type'):
                        if part.type == "text" and hasattr(part, 'text'):
                            if not getattr(part, 'ignored', False) and part.text.strip():
                                normalized_text = _expand_workflow_node_ref(part.text)
                                user_content_parts.append(normalized_text)
                                user_content_blocks.append({
                                    "type": "text",
                                    "text": normalized_text,
                                })
                        elif part.type == "file" and hasattr(part, 'mime'):
                            mime = getattr(part, 'mime', '')
                            if mime != 'application/x-directory':
                                filename = getattr(part, 'filename', 'file')
                                url = getattr(part, 'url', '')
                                # Image bytes only ride on the latest user
                                # turn — older turns are reduced to a short,
                                # opaque placeholder. Crucially the placeholder
                                # does NOT include the filename: leaking
                                # earlier filenames was making the model
                                # confidently misidentify the *current* image
                                # as one of the older ones (it would echo back
                                # the older filename instead of describing the
                                # newly-attached picture).
                                if mime.startswith("image/") and not is_latest_user_turn:
                                    stub = "[earlier image omitted]"
                                    user_content_parts.append(stub)
                                    user_content_blocks.append({
                                        "type": "text",
                                        "text": stub,
                                    })
                                else:
                                    self._append_file_content_block(
                                        user_content_blocks,
                                        user_content_parts,
                                        mime=mime,
                                        filename=filename,
                                        url=url,
                                    )
                        elif part.type == "compaction":
                            user_content_parts.append("What did we do so far?")
                            user_content_blocks.append({
                                "type": "text",
                                "text": "What did we do so far?",
                            })
                        elif part.type == "subtask":
                            user_content_parts.append("The following tool was executed by the user")
                            user_content_blocks.append({
                                "type": "text",
                                "text": "The following tool was executed by the user",
                            })
                
                if user_content_blocks and any(
                    block.get("type") == "image"
                    for block in user_content_blocks
                ):
                    if is_queued_user_turn:
                        user_content_blocks = self._wrap_queued_user_blocks(user_content_blocks)
                    chat_messages.append(ChatMessage(
                        role="user",
                        content=user_content_blocks,
                    ))
                elif user_content_parts:
                    user_text = "\n\n".join(user_content_parts)
                    if is_queued_user_turn:
                        user_text = self._wrap_queued_user_text(user_text)
                    chat_messages.append(ChatMessage(
                        role="user",
                        content=user_text,
                    ))
            
            elif msg.role == MessageRole.ASSISTANT or (isinstance(msg.role, str) and msg.role == "assistant"):
                # Skip messages with errors (matching Flocks logic)
                # Flocks: skip if error exists, UNLESS it's AbortedError with useful content
                if hasattr(msg, 'error') and msg.error:
                    # Check if it's an AbortedError
                    is_aborted_error = False
                    if isinstance(msg.error, dict):
                        error_name = msg.error.get('name', '')
                        is_aborted_error = error_name in ('MessageAbortedError', 'AbortedError')
                    
                    # If AbortedError, check if message has useful content
                    if is_aborted_error:
                        has_content = any(
                            hasattr(p, 'type') and p.type not in ("step-start", "reasoning")
                            for p in parts
                        )
                        if not has_content:
                            # AbortedError with no content - skip
                            continue
                        # AbortedError with content - include it
                    else:
                        # Non-AbortedError - skip
                        continue
                
                assistant_content_parts = []
                assistant_reasoning_parts = []
                assistant_reasoning_content_parts = []
                assistant_reasoning_details: List[Dict[str, Any]] = []
                assistant_reasoning_sources: set[str] = set()
                assistant_custom_settings: Dict[str, Any] = {}
                # Structured tool calls for the assistant message (OpenAI format)
                structured_tool_calls: List[Dict[str, Any]] = []
                # Corresponding tool-result messages (role="tool")
                pending_tool_results: List[ChatMessage] = []
                
                for part in parts:
                    if not hasattr(part, 'type'):
                        continue
                    
                    # Text parts
                    if part.type == "text" and hasattr(part, 'text'):
                        assistant_content_parts.append(part.text)
                    elif part.type == "reasoning" and hasattr(part, 'text'):
                        assistant_reasoning_parts.append(part.text)
                        part_metadata = getattr(part, "metadata", None) or {}
                        reasoning_meta = part_metadata.get("reasoning") if isinstance(part_metadata, dict) else None
                        reasoning_content = None
                        reasoning_source = None
                        reasoning_details = None
                        if isinstance(reasoning_meta, dict):
                            reasoning_content = reasoning_meta.get("content")
                            reasoning_source = reasoning_meta.get("source")
                            reasoning_details = reasoning_meta.get("details")
                        if reasoning_content is None and isinstance(part_metadata, dict):
                            reasoning_content = part_metadata.get("reasoningContent")
                        if not reasoning_source and isinstance(part_metadata, dict):
                            reasoning_source = part_metadata.get("reasoningSource")
                        if reasoning_details is None and isinstance(part_metadata, dict):
                            reasoning_details = part_metadata.get("reasoningDetails")

                        if reasoning_content is not None:
                            assistant_reasoning_content_parts.append(reasoning_content)
                        if reasoning_source:
                            assistant_reasoning_sources.add(reasoning_source)
                        if isinstance(reasoning_details, list):
                            for item in reasoning_details:
                                if isinstance(item, dict):
                                    assistant_reasoning_details.append(item)
                        reasoning_field = part_metadata.get("reasoningField") if isinstance(part_metadata, dict) else None
                        thinking_signature = part_metadata.get("thinkingSignature") if isinstance(part_metadata, dict) else None
                        redacted_thinking = part_metadata.get("redactedThinkingData") if isinstance(part_metadata, dict) else None
                        if redacted_thinking:
                            assistant_custom_settings.setdefault("anthropic_thinking_blocks", []).append({
                                "type": "redacted_thinking",
                                "data": redacted_thinking,
                            })
                        elif thinking_signature:
                            assistant_custom_settings.setdefault("anthropic_thinking_blocks", []).append({
                                "type": "thinking",
                                "thinking": part.text,
                                "signature": thinking_signature,
                            })
                        elif reasoning_field == "thinking" and getattr(part, "text", None):
                            assistant_custom_settings.setdefault("anthropic_thinking_blocks", []).append({
                                "type": "thinking",
                                "thinking": part.text,
                            })
                    
                    # Tool parts - use structured OpenAI function-calling format
                    elif part.type == "tool" and hasattr(part, 'state'):
                        tool_name = getattr(part, 'tool', 'unknown')
                        call_id = getattr(part, 'callID', None) or f"call_{id(part)}"
                        tool_input = getattr(part.state, 'input', {})
                        
                        if part.state.status == "completed":
                            tool_output_str, was_dyn_truncated, persisted_placeholder = self._build_tool_output_text(
                                part,
                                tool_name,
                                ctx_window_tokens,
                            )
                            if was_dyn_truncated:
                                log.info("runner.tool_result_dynamic_truncated", {
                                    "tool_name": tool_name,
                                    "call_id": call_id,
                                    "context_window": ctx_window_tokens,
                                    "truncated_len": len(tool_output_str),
                                })
                            
                            # Build structured tool call for assistant message
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            # Build tool-result message
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content=tool_output_str,
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                            tool_result_refs.append({
                                "chat_message": pending_tool_results[-1],
                                "part": part,
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "turn_index": turn_index,
                                "char_count": len(tool_output_str),
                                "compacted": bool(persisted_placeholder),
                                "dirty": False,
                            })
                            
                            log.debug("runner.to_chat_messages.tool_result_added", {
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "call_id": call_id,
                                "output_length": len(tool_output_str),
                                "compacted": bool(persisted_placeholder),
                            })
                        
                        elif part.state.status == "error":
                            tool_error = getattr(part.state, 'error', 'Unknown error')
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content=f"Error: {tool_error}",
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                        
                        elif part.state.status == "running":
                            # Tool was interrupted (e.g., by user abort) before completing.
                            # Include it in chat context so the LLM knows this tool call was
                            # attempted and interrupted, allowing it to re-attempt if needed.
                            args_str = json.dumps(tool_input, ensure_ascii=False) if not isinstance(tool_input, str) else tool_input
                            structured_tool_calls.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": args_str,
                                },
                            })
                            pending_tool_results.append(ChatMessage(
                                role="tool",
                                content="Error: Tool execution was interrupted",
                                tool_call_id=call_id,
                                name=tool_name,
                            ))
                            log.debug("runner.to_chat_messages.running_tool_as_interrupted", {
                                "message_id": msg.id,
                                "tool_name": tool_name,
                                "call_id": call_id,
                            })
                
                has_assistant_reasoning = bool(
                    assistant_reasoning_parts
                    or assistant_reasoning_content_parts
                    or assistant_reasoning_details
                    or assistant_custom_settings
                )
                # Add assistant message
                if assistant_content_parts or structured_tool_calls or has_assistant_reasoning:
                    assistant_message = ChatMessage(
                        role="assistant",
                        content="\n\n".join(assistant_content_parts) if assistant_content_parts else "",
                        reasoning="".join(assistant_reasoning_parts) if assistant_reasoning_parts else None,
                        reasoning_content="".join(assistant_reasoning_content_parts) if assistant_reasoning_content_parts else None,
                        reasoning_details=assistant_reasoning_details if assistant_reasoning_details else None,
                        reasoning_source=sorted(assistant_reasoning_sources)[0] if assistant_reasoning_sources else None,
                        tool_calls=structured_tool_calls if structured_tool_calls else None,
                        custom_settings=assistant_custom_settings,
                    )
                    assistant_message = prepare_reasoning_for_replay(
                        provider_id=self.provider_id,
                        model_id=self.model_id,
                        message=assistant_message,
                        interleaved=active_interleaved,
                    )
                    chat_messages.append(assistant_message)
                    # Append tool-result messages immediately after the assistant message
                    chat_messages.extend(pending_tool_results)
                else:
                    # Log if assistant message was skipped due to no content
                    log.debug("runner.to_chat_messages.assistant_skipped", {
                        "message_id": msg.id,
                        "parts_count": len(parts),
                        "has_error": hasattr(msg, 'error') and bool(msg.error),
                    })
        
        budget_result = await self._apply_tool_result_budget(tool_result_refs, ctx_window_tokens)
        if budget_result.get("compacted"):
            log.info("runner.context_budget_enforced", {
                "session_id": self.session.id,
                "step": self._step,
                "context_window": ctx_window_tokens,
                "compacted_tool_results": budget_result.get("compacted", 0),
                "persisted_tool_results": budget_result.get("persisted", 0),
            })

        log.debug("runner.to_chat_messages.result", {
            "total_messages": len(chat_messages),
            "roles": [m.role for m in chat_messages],
        })
        context_cache["latest"] = {
            "system_cache_key": system_cache_key,
            "message_signatures": list(message_signatures),
            "chat_messages": [
                message.model_dump(exclude_none=True)
                for message in chat_messages
            ],
        }
        self._log_perf(
            "runner.to_chat_messages.complete",
            started_at,
            source_message_count=len(messages),
            chat_message_count=len(chat_messages),
        )
        
        return chat_messages
    
    async def _call_llm(
        self,
        provider: Any,
        messages: List[ChatMessage],
        tools: List[Dict[str, Any]],
        agent: AgentInfo,
        assistant_msg: MessageInfo,
    ) -> StepResult:
        """
        Call LLM and process response with event-driven streaming.
        
        Uses StreamProcessor to handle events and execute tools synchronously.
        Ported from Flocks' SessionProcessor.process() behavior.
        """
        def _serialize_message(message: ChatMessage) -> Dict[str, Any]:
            payload = message.model_dump(exclude_none=True)
            if not payload.get("custom_settings"):
                payload.pop("custom_settings", None)
            return payload

        def _build_llm_response_payload(
            *,
            content: str,
            reasoning: str,
            tool_calls: List["ToolCall"],
        ) -> Dict[str, Any]:
            return {
                "role": "assistant",
                "content": content,
                "reasoning": reasoning,
                "toolCalls": [
                    {
                        "id": tool_call.id,
                        "name": tool_call.name,
                        "arguments": tool_call.arguments,
                    }
                    for tool_call in tool_calls
                ],
            }

        # Create stream processor
        main_session_key = self.session.id
        try:
            from flocks.session.core.session_state import get_main_session_id

            main_session_key = get_main_session_id() or self.session.id
        except Exception as e:
            log.debug("runner.sandbox_context_init_failed", {"error": str(e)})

        processor = StreamProcessor(
            session_id=self.session.id,
            assistant_message=assistant_msg,
            agent=agent,
            abort_event=self._external_abort or self._abort,
            permission_callback=self._handle_permission,
            text_delta_callback=self.callbacks.on_text_delta,
            reasoning_delta_callback=self.callbacks.on_reasoning_delta,
            tool_start_callback=self.callbacks.on_tool_start,
            tool_end_callback=self.callbacks.on_tool_end,
            tool_checkpoint_callback=self._checkpoint_before_tool,
            tool_patch_callback=self._record_tool_patch,
            event_publish_callback=self.callbacks.event_publish_callback,
            session_key=self.session.id,
            main_session_key=main_session_key,
            workspace_dir=self.session.directory,
            langfuse_generation=None,
            step_index=self._step,
        )
        
        # Build provider options (thinking / reasoning / max_tokens)
        from flocks.provider.options import build_provider_options
        provider_options = build_provider_options(self.provider_id, self.model_id)

        # Clean up any leftover reasoning state from a previous (failed) call
        if hasattr(self, '_current_reasoning_id'):
            delattr(self, '_current_reasoning_id')
        if hasattr(self, '_current_reasoning_metadata'):
            delattr(self, '_current_reasoning_metadata')

        from flocks.session.streaming.tool_accumulator import ToolCallAccumulator
        tool_accumulator = ToolCallAccumulator(processor)

        text_started = False
        reasoning_id_counter = 0
        stream_finish_reason: Optional[str] = None

        # -- Observability: create trace & generation scopes (safe no-op when
        # Langfuse is unconfigured).  All observability calls are wrapped in
        # try/except so they never break the core session flow.
        trace_ctx = None
        generation_ctx = None
        if langfuse_is_active():
            try:
                input_preview = []
                for _msg in messages[-12:]:
                    _mc = _msg.content or ""
                    input_preview.append(
                        {"role": _msg.role, "chars": len(_mc), "preview": _mc[:240]}
                    )

                trace_tags = [
                    f"session:{self.session.id}",
                    f"step:{self._step}",
                    f"session_step:{self.session.id}:{self._step}",
                    f"agent:{agent.name}",
                    f"provider:{self.provider_id}",
                ]
                trace_ctx = trace_scope(
                    name="SessionRunner.step",
                    session_id=self.session.id,
                    tags=trace_tags,
                    input={
                        "step": self._step,
                        "message_count": len(messages),
                        "tool_count": len(tools),
                        "last_user_preview": next(
                            ((m.content or "")[:280] for m in reversed(messages) if m.role == "user"),
                            "",
                        ),
                    },
                    metadata={
                        "provider_id": self.provider_id,
                        "model_id": self.model_id,
                        "agent": agent.name,
                        "workspace": self.session.directory,
                    },
                )
                generation_ctx = generation_scope(
                    parent=trace_ctx.observation,
                    name="LLM.generate",
                    model=self.model_id,
                    input=input_preview,
                    metadata={
                        "provider_id": self.provider_id,
                        "session_id": self.session.id,
                        "step": self._step,
                        "tool_names": [t.get("function", {}).get("name", "") for t in tools][:50],
                    },
                )
                processor._langfuse_generation = generation_ctx.observation
            except Exception as exc:
                log.debug("runner.observability.init_failed", {"error": str(exc)})
                trace_ctx = None
                generation_ctx = None
        
        # Validate messages - ensure we have at least one non-system message
        non_system_messages = [m for m in messages if m.role != "system"]
        if not non_system_messages:
            log.error("runner.call_llm.no_messages", {
                "total_messages": len(messages),
                "session_id": self.session.id,
            })
            self._end_observability(generation_ctx, trace_ctx, output="No valid messages", level="ERROR")
            return StepResult(action="stop", content="", error="No valid messages to send to LLM")
        
        log.debug("runner.call_llm.messages", {
            "total": len(messages),
            "non_system": len(non_system_messages),
            "roles": [m.role for m in messages],
        })
        
        # Emit start event
        await processor.process_event(StartEvent())
        
        # Lightweight counters instead of storing all chunks in memory
        chunk_counts = {"total": 0, "reasoning": 0, "text": 0, "tool": 0}
        stream_usage: Optional[Dict[str, int]] = None
        
        # Stream response and convert chunks to events
        provider_tools = None if self._should_use_text_tool_call_mode() else (tools if tools else None)
        if provider_tools is None and tools:
            log.info("runner.text_tool_call_mode.enabled", {
                "session_id": self.session.id,
                "provider_id": self.provider_id,
                "model_id": self.model_id,
                "tool_count": len(tools),
            })

        llm_hook_metadata = {
            "sessionID": self.session.id,
            "messageID": assistant_msg.id,
            "workspace": self.session.directory,
            "agent": agent.name,
            "step": self._step,
            "model": {
                "providerID": self.provider_id,
                "modelID": self.model_id,
            },
        }
        llm_before_enabled = False
        llm_after_enabled = False
        self._llm_call_aborted = False
        try:
            llm_before_enabled = await HookPipeline.has_stage_handlers(
                HookStage.LLM_BEFORE,
                llm_hook_metadata,
            )
            llm_after_enabled = await HookPipeline.has_stage_handlers(
                HookStage.LLM_AFTER,
                llm_hook_metadata,
            )
        except Exception as exc:
            log.debug("runner.hook.stage_probe.error", {"error": str(exc)})

        if llm_before_enabled:
            llm_before_hook_input = {
                **llm_hook_metadata,
                "request": {
                    "messageCount": len(messages),
                    "messages": [_serialize_message(message) for message in messages],
                    "toolCount": len(tools),
                    "tools": copy.deepcopy(tools),
                    "providerOptions": dict(provider_options),
                    "providerToolsEnabled": provider_tools is not None,
                },
            }
            try:
                hook_started_at = time.perf_counter()
                await HookPipeline.run_llm_before(llm_before_hook_input)
                self._log_perf(
                    "runner.hook.llm_before.complete",
                    hook_started_at,
                    message_count=len(messages),
                    tool_count=len(tools),
                )
            except Exception as exc:
                log.debug("runner.hook.llm_before.error", {"error": str(exc)})

        llm_call_started_at = time.perf_counter()
        first_chunk_logged = False
        aborted_during_stream = False
        try:
            async for chunk in _iter_with_chunk_timeout(
                provider.chat_stream(
                    model_id=self.model_id,
                    messages=messages,
                    tools=provider_tools,
                    # session_id is forwarded via kwargs so providers that need
                    # to look up persisted session data (e.g. Gemini's DB-backed
                    # reasoning replay) can do so.  Providers that don't care
                    # simply ignore unknown kwargs.
                    session_id=self.session.id,
                    **provider_options,
                ),
                first_chunk_timeout_s=LLM_STREAM_FIRST_CHUNK_TIMEOUT_S,
                ongoing_chunk_timeout_s=LLM_STREAM_ONGOING_CHUNK_TIMEOUT_S,
            ):
                chunk_counts["total"] += 1
                if not first_chunk_logged:
                    first_chunk_logged = True
                    self._log_perf(
                        "runner.llm.first_chunk",
                        llm_call_started_at,
                        provider_id=self.provider_id,
                        model_id=self.model_id,
                    )
                
                chunk_finish = getattr(chunk, 'finish_reason', None)
                if chunk_finish:
                    stream_finish_reason = chunk_finish
                
                # Capture usage from chunk (providers may include it in the final chunk)
                if hasattr(chunk, 'usage') and chunk.usage:
                    stream_usage = chunk.usage
                
                # Check for abort
                if self.is_aborted:
                    aborted_during_stream = True
                    break
                
                # Determine event type from chunk.  A single chunk may carry any
                # combination of reasoning / text / tool_calls (e.g. Gemini bundles
                # them).  We must not drop non-reasoning content when reasoning is
                # present, and we must not double-emit `delta` as text when the
                # provider used `event_type == 'reasoning'` to overload `delta` for
                # reasoning text.
                event_type = getattr(chunk, 'event_type', None)
                chunk_metadata = getattr(chunk, 'metadata', None) or {}
                reasoning_event_types = {"reasoning", "reasoning-start", "reasoning-end"}

                if hasattr(self, '_current_reasoning_id') and chunk_metadata:
                    current_metadata = getattr(self, '_current_reasoning_metadata', {}) or {}
                    current_metadata.update(chunk_metadata)
                    self._current_reasoning_metadata = current_metadata

                if event_type == "reasoning-start" and not hasattr(self, '_current_reasoning_id'):
                    reasoning_id_counter += 1
                    self._current_reasoning_id = f"reasoning-{reasoning_id_counter}"
                    self._current_reasoning_metadata = dict(chunk_metadata)
                    await processor.process_event(ReasoningStartEvent(
                        id=self._current_reasoning_id,
                        metadata=chunk_metadata,
                    ))

                if event_type == "reasoning-end" and hasattr(self, '_current_reasoning_id'):
                    reasoning_end_metadata = getattr(self, '_current_reasoning_metadata', {}) or {}
                    await processor.process_event(ReasoningEndEvent(
                        id=self._current_reasoning_id,
                        metadata=reasoning_end_metadata,
                    ))
                    delattr(self, '_current_reasoning_id')
                    if hasattr(self, '_current_reasoning_metadata'):
                        delattr(self, '_current_reasoning_metadata')

                chunk_reasoning = getattr(chunk, 'reasoning', None) or None
                if not chunk_reasoning and event_type == 'reasoning':
                    # Older providers signal reasoning via event_type and put the
                    # reasoning text in `delta` (no separate `reasoning` field).
                    chunk_reasoning = getattr(chunk, 'delta', '') or None
                has_reasoning_metadata = bool(
                    chunk_metadata.get("reasoningDetails")
                    or chunk_metadata.get("reasoningContent") is not None
                    or chunk_metadata.get("reasoningField")
                )

                # Treat `delta` as text only when it isn't already consumed as
                # reasoning above.  This preserves backward compatibility with
                # providers that emit reasoning-only chunks via `event_type`.
                chunk_text = ''
                if event_type not in reasoning_event_types or getattr(chunk, 'reasoning', None):
                    chunk_text = getattr(chunk, 'delta', '') or ''

                chunk_tool_calls = getattr(chunk, 'tool_calls', None)

                # 1) Process reasoning delta (start reasoning block on first sight).
                if chunk_reasoning or (event_type == 'reasoning' and has_reasoning_metadata):
                    reasoning_text = chunk_reasoning or ""
                    chunk_counts["reasoning"] += 1
                    log.debug("runner.reasoning.received", {
                        "length": len(reasoning_text),
                        "text_preview": reasoning_text[:50],
                    })
                    if not hasattr(self, '_current_reasoning_id'):
                        reasoning_id_counter += 1
                        self._current_reasoning_id = f"reasoning-{reasoning_id_counter}"
                        self._current_reasoning_metadata = dict(chunk_metadata)
                        await processor.process_event(ReasoningStartEvent(
                            id=self._current_reasoning_id,
                            metadata=chunk_metadata,
                        ))

                    if chunk_reasoning:
                        await processor.process_event(ReasoningDeltaEvent(
                            id=self._current_reasoning_id,
                            text=chunk_reasoning,
                            metadata=chunk_metadata,
                        ))

                # 2) End reasoning block when this chunk also carries non-reasoning
                #    content (or once the stream moves away from reasoning).
                if (chunk_text or chunk_tool_calls) and hasattr(self, '_current_reasoning_id'):
                    reasoning_end_metadata = getattr(self, '_current_reasoning_metadata', {}) or {}
                    await processor.process_event(ReasoningEndEvent(
                        id=self._current_reasoning_id,
                        metadata=reasoning_end_metadata,
                    ))
                    delattr(self, '_current_reasoning_id')
                    if hasattr(self, '_current_reasoning_metadata'):
                        delattr(self, '_current_reasoning_metadata')

                # 3) Process text delta.
                if chunk_text:
                    chunk_counts["text"] += 1
                    if not text_started:
                        await processor.process_event(TextStartEvent())
                        text_started = True

                    await processor.process_event(TextDeltaEvent(
                        text=chunk_text,
                    ))

                # 4) Process tool calls.
                if chunk_tool_calls:
                    chunk_counts["tool"] += 1
                    for tc in chunk_tool_calls:
                        await tool_accumulator.feed_chunk(tc)
        except Exception as exc:
            partial_response = _build_llm_response_payload(
                content=processor.get_text_content(),
                reasoning=processor.get_reasoning_content(),
                tool_calls=[],
            )
            if llm_after_enabled:
                try:
                    await HookPipeline.run_llm_after(
                        llm_hook_metadata,
                        {
                            "durationMs": int((time.perf_counter() - llm_call_started_at) * 1000),
                            "error": {
                                "type": type(exc).__name__,
                                "message": str(exc),
                            },
                            "response": partial_response,
                            "usage": stream_usage,
                            "chunkCounts": dict(chunk_counts),
                        },
                    )
                except Exception as hook_exc:
                    log.debug("runner.hook.llm_after.error", {"error": str(hook_exc)})
            raise
        
        log.debug("runner.stream.summary", {
            "total_chunks": chunk_counts["total"],
            "reasoning_chunks": chunk_counts["reasoning"],
            "text_chunks": chunk_counts["text"],
            "tool_chunks": chunk_counts["tool"],
            "had_reasoning": chunk_counts["reasoning"] > 0,
            "finish_reason": stream_finish_reason,
            "agent": agent.name,
        })

        await tool_accumulator.flush_remaining(stream_finish_reason)
        
        # End text block if started
        if text_started:
            await processor.process_event(TextEndEvent())
        
        # End any remaining reasoning block
        if hasattr(self, '_current_reasoning_id'):
            reasoning_end_metadata = getattr(self, '_current_reasoning_metadata', {}) or {}
            await processor.process_event(ReasoningEndEvent(
                id=self._current_reasoning_id,
                metadata=reasoning_end_metadata,
            ))
            delattr(self, '_current_reasoning_id')
            if hasattr(self, '_current_reasoning_metadata'):
                delattr(self, '_current_reasoning_metadata')
        
        # Emit finish event
        await processor.process_event(FinishEvent(
            finish_reason=processor.get_finish_reason()
        ))

        # Foreground subagent tool-calls are launched concurrently during
        # streaming so sibling subagents can start in the same assistant turn.
        # Drain them here before exposing tool results to the next loop step.
        await processor.drain_parallel_tool_calls()
        
        # Get processed content
        content = processor.get_text_content()
        reasoning = processor.get_reasoning_content()
        
        # Update message tokens if provider reported usage
        tokens_update = self._build_tokens_update(stream_usage)
        if tokens_update:
            try:
                await Message.update(
                    self.session.id,
                    assistant_msg.id,
                    tokens=tokens_update,
                )
                log.info("runner.stream.usage_captured", {
                    "input": tokens_update["input"],
                    "output": tokens_update["output"],
                    "total": stream_usage.get("total_tokens", 0),
                })
            except Exception as e:
                log.warn("runner.stream.usage_update_failed", {"error": str(e)})
        
        # Log summary
        log.debug("runner.stream.complete", {
            "text_length": len(content),
            "reasoning_length": len(reasoning),
            "tool_calls": len(processor.tool_calls),
            "usage": stream_usage,
        })
        
        # Update assistant message with content
        if content:
            await Message.update(
                self.session.id,
                assistant_msg.id,
                content=content,
            )
        self._llm_call_aborted = aborted_during_stream
        
        # Note: Tools were already executed synchronously during streaming
        # Build tool call list for result
        tool_calls_for_result = [
            ToolCall(
                id=tc_state.id,
                name=tc_state.name,
                arguments=tc_state.input,
            )
            for tc_state in processor.tool_calls.values()
        ]
        result_action = "continue" if tool_calls_for_result else "stop"
        response_payload = _build_llm_response_payload(
            content=content,
            reasoning=reasoning,
            tool_calls=tool_calls_for_result,
        )
        if llm_after_enabled:
            try:
                hook_started_at = time.perf_counter()
                await HookPipeline.run_llm_after(
                    llm_hook_metadata,
                    {
                        "durationMs": int((time.perf_counter() - llm_call_started_at) * 1000),
                        "finishReason": processor.get_finish_reason(),
                        "contentLength": len(content),
                        "reasoningLength": len(reasoning),
                        "toolCallCount": len(tool_calls_for_result),
                        "toolCalls": [
                            {"id": tool_call.id, "name": tool_call.name}
                            for tool_call in tool_calls_for_result[:30]
                        ],
                        "response": response_payload,
                        "usage": stream_usage,
                        "chunkCounts": dict(chunk_counts),
                        "action": result_action,
                    },
                )
                self._log_perf(
                    "runner.hook.llm_after.complete",
                    hook_started_at,
                    action=result_action,
                    tool_call_count=len(tool_calls_for_result),
                )
            except Exception as exc:
                log.debug("runner.hook.llm_after.error", {"error": str(exc)})
        
        if tool_calls_for_result:
            self._end_observability(
                generation_ctx, trace_ctx,
                output={
                    "content_preview": content[:600],
                    "content_chars": len(content),
                    "reasoning_chars": len(reasoning),
                    "tool_calls": [{"id": tc.id, "name": tc.name} for tc in tool_calls_for_result[:30]],
                },
                usage=stream_usage,
                metadata={
                    "finish_reason": processor.get_finish_reason(),
                    "status": "continue_with_tools",
                    "tool_call_count": len(tool_calls_for_result),
                },
                trace_output={
                    "status": "ok",
                    "next_action": "continue",
                    "finish_reason": processor.get_finish_reason(),
                    "tool_call_count": len(tool_calls_for_result),
                },
            )
            return StepResult(
                action=result_action,
                content=content,
                tool_calls=tool_calls_for_result,
                usage=stream_usage,
            )
        
        self._end_observability(
            generation_ctx, trace_ctx,
            output={
                "content_preview": content[:600],
                "content_chars": len(content),
                "reasoning_chars": len(reasoning),
            },
            usage=stream_usage,
            metadata={
                "finish_reason": processor.get_finish_reason(),
                "status": "stop",
                "tool_call_count": 0,
            },
            trace_output={
                "status": "ok",
                "next_action": "stop",
                "finish_reason": processor.get_finish_reason(),
            },
        )
        return StepResult(action=result_action, content=content, usage=stream_usage)
    
    @staticmethod
    def _end_observability(
        generation_ctx: Any,
        trace_ctx: Any,
        *,
        output: Any = None,
        usage: Any = None,
        metadata: Any = None,
        level: Optional[str] = None,
        trace_output: Any = None,
    ) -> None:
        """Safely end observability scopes. Never raises."""
        try:
            if generation_ctx is not None:
                gen_kwargs: Dict[str, Any] = {}
                if output is not None:
                    gen_kwargs["output"] = output
                if usage is not None:
                    gen_kwargs["usage"] = usage
                if metadata is not None:
                    gen_kwargs["metadata"] = metadata
                if level is not None:
                    gen_kwargs["level"] = level
                generation_ctx.end(**gen_kwargs)
        except Exception as _gen_err:
            log.debug("runner.observability.generation_end_failed", {"error": str(_gen_err)})
        try:
            if trace_ctx is not None:
                tr_kwargs: Dict[str, Any] = {}
                if trace_output is not None:
                    tr_kwargs["output"] = trace_output
                elif output is not None:
                    tr_kwargs["output"] = output
                if level is not None:
                    tr_kwargs["level"] = level
                trace_ctx.end(**tr_kwargs)
        except Exception as _tr_err:
            log.debug("runner.observability.trace_end_failed", {"error": str(_tr_err)})

    async def _handle_permission(self, request) -> None:
        """Handle permission request."""
        if self.callbacks.on_permission_request:
            allowed = await self.callbacks.on_permission_request(request)
            if not allowed:
                raise PermissionError(f"Permission denied: {request.permission}")
            return

        tool_metadata = get_tool_catalog_metadata(str(getattr(request, "permission", "") or ""))
        if self.callbacks.event_publish_callback:
            await self.callbacks.event_publish_callback("runtime.permission_gate", {
                "sessionID": self.session.id,
                "step": self._step,
                "toolName": getattr(request, "permission", ""),
                "alwaysLoad": tool_metadata.always_load,
                "patterns": list(getattr(request, "patterns", None) or []),
            })

        from flocks.permission.next import PermissionNext
        from flocks.permission.rule import PermissionRule, PermissionLevel

        session_rules: List[PermissionRule] = []
        for rule in getattr(self.session, "permission", None) or []:
            raw_level = getattr(rule, "action", None) or getattr(rule, "level", None) or "ask"
            try:
                level = PermissionLevel(str(raw_level))
            except Exception:
                level = PermissionLevel.ASK
            session_rules.append(PermissionRule(
                permission=getattr(rule, "permission", "*"),
                level=level,
                pattern=getattr(rule, "pattern", "*"),
            ))

        metadata = dict(getattr(request, "metadata", None) or {})
        metadata.setdefault("messageID", getattr(request, "message_id", "") or "")
        metadata.setdefault("sessionID", self.session.id)

        await PermissionNext.ask(
            session_id=self.session.id,
            permission=request.permission,
            patterns=list(getattr(request, "patterns", None) or []),
            ruleset=session_rules,
            metadata=metadata,
            always=list(getattr(request, "always", None) or []),
            tool={"name": request.permission},
        )


async def run_session(
    session: SessionInfo,
    provider_id: Optional[str] = None,
    model_id: Optional[str] = None,
    agent_name: Optional[str] = None,
    callbacks: Optional[RunnerCallbacks] = None,
) -> Optional[MessageInfo]:
    """
    Run a session to completion.

    Delegates to SessionLoop which is the single authoritative execution path.

    Args:
        session: Session to run
        provider_id: Provider ID
        model_id: Model ID
        agent_name: Agent name
        callbacks: RunnerCallbacks (wrapped into LoopCallbacks)

    Returns:
        Last assistant message
    """
    from flocks.session.session_loop import SessionLoop, LoopCallbacks

    loop_callbacks = LoopCallbacks(
        runner_callbacks=callbacks,
        event_publish_callback=callbacks.event_publish_callback if callbacks else None,
    )
    result = await SessionLoop.run(
        session_id=session.id,
        provider_id=provider_id,
        model_id=model_id,
        agent_name=agent_name,
        callbacks=loop_callbacks,
    )
    return result.last_message
