"""
Message management for sessions

Handles message creation, storage, and retrieval.
Based on Flocks' ported src/session/message-v2.ts

Key features:
- Persistent storage using Storage module (SQLite)
- Thread-safe operations with asyncio locks
- Flocks compatible API
"""

import asyncio
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Literal, Union
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict
from enum import Enum

from flocks.utils.log import Log
from flocks.utils.id import Identifier
from flocks.storage.storage import Storage
from flocks.session.recorder import Recorder
from flocks.session.execution_mode import SessionExecutionMode

log = Log.create(service="message")


class MessageCacheInvalidatedError(RuntimeError):
    """Raised when a full cache load cannot stabilize after invalidation."""


class _SessionLockManager:
    """Per-session asyncio.Lock manager with LRU eviction.

    Replaces the two global locks (``_message_lock`` / ``_parts_lock``) so that
    operations on different sessions never block each other.
    """

    _MAX_LOCKS = 200

    def __init__(self) -> None:
        self._locks: OrderedDict[str, asyncio.Lock] = OrderedDict()

    def get(self, session_id: str) -> asyncio.Lock:
        if session_id in self._locks:
            self._locks.move_to_end(session_id)
            return self._locks[session_id]
        while len(self._locks) >= self._MAX_LOCKS:
            self._locks.popitem(last=False)
        lock = asyncio.Lock()
        self._locks[session_id] = lock
        return lock

    def discard(self, session_id: str) -> None:
        self._locks.pop(session_id, None)

    def clear(self) -> None:
        self._locks.clear()


_session_locks = _SessionLockManager()


class MessageRole(str, Enum):
    """Message role"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


# Part types matching TypeScript MessageV2.Part
class PartTime(BaseModel):
    """Part timing information"""
    start: int = Field(..., description="Start timestamp (ms)")
    end: Optional[int] = Field(None, description="End timestamp (ms)")
    compacted: Optional[int] = Field(None, description="Compaction timestamp (ms)")


class TokenCache(BaseModel):
    """Token cache information"""
    read: int = Field(0, description="Cache read tokens")
    write: int = Field(0, description="Cache write tokens")


class TokenUsage(BaseModel):
    """Token usage information - Flocks compatible"""
    input: int = Field(0, description="Input tokens")
    output: int = Field(0, description="Output tokens")
    reasoning: int = Field(0, description="Reasoning tokens")
    cache: TokenCache = Field(default_factory=TokenCache, description="Cache tokens")
    
    @property
    def total(self) -> int:
        return self.input + self.output + self.reasoning


class TextPart(BaseModel):
    """Text message part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["text"] = "text"
    text: str = Field("", description="Text content")
    synthetic: Optional[bool] = Field(None, description="System-generated content")
    ignored: Optional[bool] = Field(None, description="Ignored by processing")
    time: Optional[PartTime] = Field(None, description="Timing information")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class FilePart(BaseModel):
    """File attachment part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["file"] = "file"
    mime: str = Field(..., description="MIME type")
    filename: Optional[str] = Field(None, description="Original filename")
    url: str = Field(..., description="File URL (file:// or data:)")
    source: Optional[Dict[str, Any]] = Field(None, description="Source info for MCP resources")


class ToolStatePending(BaseModel):
    """Tool pending state"""
    status: Literal["pending"] = "pending"
    input: Dict[str, Any] = Field(..., description="Tool input")
    raw: str = Field(..., description="Raw tool call")


class ToolStateRunning(BaseModel):
    """Tool running state"""
    status: Literal["running"] = "running"
    input: Dict[str, Any] = Field(..., description="Tool input")
    title: Optional[str] = Field(None, description="Execution title")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata")
    time: Dict[str, int] = Field(..., description="Timing info")


class ToolStateCompleted(BaseModel):
    """Tool completed state"""
    status: Literal["completed"] = "completed"
    input: Dict[str, Any] = Field(..., description="Tool input")
    output: Union[str, Dict[str, Any], List[Any]] = Field(..., description="Tool output (str, dict, or list)")
    title: str = Field(..., description="Execution title")
    metadata: Dict[str, Any] = Field(..., description="Metadata")
    time: Dict[str, int] = Field(..., description="Timing info")
    attachments: Optional[List[Dict[str, Any]]] = Field(None, description="File attachments")
    
    def get_output_str(self) -> str:
        """Get output as string, converting if necessary"""
        if isinstance(self.output, str):
            return self.output
        import json
        return json.dumps(self.output, ensure_ascii=False, indent=2)


class ToolStateError(BaseModel):
    """Tool error state"""
    status: Literal["error"] = "error"
    input: Dict[str, Any] = Field(..., description="Tool input")
    error: str = Field(..., description="Error message")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Metadata")
    time: Dict[str, int] = Field(..., description="Timing info")


# Union type for tool state
ToolState = Union[ToolStatePending, ToolStateRunning, ToolStateCompleted, ToolStateError]


class ToolPart(BaseModel):
    """Tool call/result part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["tool"] = "tool"
    callID: str = Field(..., description="Tool call ID")
    tool: str = Field(..., description="Tool name")
    state: ToolState = Field(..., description="Tool execution state")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")


class ReasoningPart(BaseModel):
    """Reasoning/thinking part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["reasoning"] = "reasoning"
    text: str = Field(..., description="Reasoning text")
    metadata: Optional[Dict[str, Any]] = Field(None, description="Additional metadata")
    time: PartTime = Field(..., description="Timing information")


class SnapshotPart(BaseModel):
    """Snapshot part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["snapshot"] = "snapshot"
    snapshot: str = Field(..., description="Snapshot ID")


class PatchPart(BaseModel):
    """File patch/edit part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["patch"] = "patch"
    hash: str = Field(..., description="Patch hash")
    files: List[str] = Field(..., description="Affected files")


class StepStartPart(BaseModel):
    """Step start marker - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["step-start"] = "step-start"
    snapshot: Optional[str] = Field(None, description="Snapshot ID")


class StepFinishPart(BaseModel):
    """Step finish marker - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["step-finish"] = "step-finish"
    reason: str = Field(..., description="Finish reason")
    snapshot: Optional[str] = Field(None, description="Snapshot ID")
    cost: float = Field(..., description="Step cost")
    tokens: TokenUsage = Field(..., description="Token usage")


class AgentPart(BaseModel):
    """Agent invocation part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["agent"] = "agent"
    name: str = Field(..., description="Agent name")
    source: Optional[Dict[str, Any]] = Field(None, description="Source information")


class SubtaskPart(BaseModel):
    """Subtask/subagent part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["subtask"] = "subtask"
    prompt: str = Field(..., description="Task prompt")
    description: str = Field(..., description="Task description")
    agent: str = Field(..., description="Agent name")
    model: Optional[Dict[str, str]] = Field(None, description="Model configuration")
    command: Optional[str] = Field(None, description="Command to execute")


class RetryPart(BaseModel):
    """Retry part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["retry"] = "retry"
    attempt: int = Field(..., description="Retry attempt number")
    error: Dict[str, Any] = Field(..., description="Error information")
    time: Dict[str, int] = Field(..., description="Timing information")


class CompactionPart(BaseModel):
    """Compaction marker part - Flocks compatible"""
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    sessionID: str = Field(..., description="Session ID")
    messageID: str = Field(..., description="Message ID")
    type: Literal["compaction"] = "compaction"
    auto: bool = Field(..., description="Auto compaction")


# Union type for all parts - matches Flocks MessageV2.Part
PartType = Union[
    TextPart,
    SubtaskPart,
    ReasoningPart,
    FilePart,
    ToolPart,
    StepStartPart,
    StepFinishPart,
    SnapshotPart,
    PatchPart,
    AgentPart,
    RetryPart,
    CompactionPart,
]


class MessagePart(BaseModel):
    """Legacy message part — DEPRECATED, use typed PartType union instead."""
    id: str = Field(default_factory=lambda: Identifier.ascending("part"))
    type: str  # "text", "file", "tool", etc.
    content: str = ""
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    @classmethod
    def from_typed_part(cls, part: PartType) -> "MessagePart":
        """Convert typed part to legacy format"""
        if isinstance(part, TextPart):
            return cls(id=part.id, type="text", content=part.text, metadata={"synthetic": part.synthetic})
        elif isinstance(part, FilePart):
            return cls(id=part.id, type="file", content=part.url, metadata={"filename": part.filename, "mime": part.mime})
        elif isinstance(part, ToolPart):
            return cls(id=part.id, type="tool", content="", metadata={"tool": part.tool, "state": "pending"})
        elif isinstance(part, ReasoningPart):
            return cls(id=part.id, type="reasoning", content=part.text)
        elif isinstance(part, PatchPart):
            return cls(id=part.id, type="patch", content="", metadata={"files": part.files})
        else:
            return cls(id=part.id, type=part.type, content="")


class MessageTime(BaseModel):
    """Message timestamps"""
    created: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1000))
    updated: Optional[int] = None
    completed: Optional[int] = None


class MessagePath(BaseModel):
    """Working path information"""
    cwd: str = Field("", description="Current working directory")
    root: str = Field("", description="Worktree root")


class MessageSummary(BaseModel):
    """Message summary information"""
    title: Optional[str] = None
    diffs: Optional[List[Dict[str, Any]]] = None


class UserMessageInfo(BaseModel):
    """
    User message information - Flocks compatible
    
    Matches TypeScript MessageV2.User structure
    """
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("message"))
    sessionID: str = Field(..., description="Session ID")
    role: Literal["user"] = "user"
    time: Dict[str, int] = Field(..., description="Timestamps")
    summary: Optional[MessageSummary] = Field(None, description="User message summary")
    agent: str = Field(..., description="Agent name")
    model: Dict[str, str] = Field(..., description="Model configuration (providerID, modelID)")
    system: Optional[str] = Field(None, description="System prompt")
    tools: Optional[Dict[str, bool]] = Field(None, description="Tool availability")
    variant: Optional[str] = Field(None, description="Prompt variant")
    compacted: Optional[bool] = Field(None, description="Archived by compaction (soft-deleted)")
    executionMode: SessionExecutionMode = Field(
        SessionExecutionMode.BUILD,
        description="Execution mode used for this user turn",
    )


class AssistantMessageInfo(BaseModel):
    """
    Assistant message information - Flocks compatible
    
    Matches TypeScript MessageV2.Assistant structure
    """
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    id: str = Field(default_factory=lambda: Identifier.ascending("message"))
    sessionID: str = Field(..., description="Session ID")
    role: Literal["assistant"] = "assistant"
    time: Dict[str, int] = Field(..., description="Timestamps (created, completed)")
    error: Optional[Dict[str, Any]] = Field(None, description="Error information")
    parentID: str = Field(..., description="Parent message ID")
    modelID: str = Field(..., description="Model ID")
    providerID: str = Field(..., description="Provider ID")
    mode: str = Field(..., description="Execution mode (deprecated)")
    agent: str = Field(..., description="Agent name")
    path: MessagePath = Field(..., description="Working path")
    summary: Optional[bool] = Field(None, description="Is compaction summary")
    cost: float = Field(0.0, description="Cost in dollars")
    tokens: TokenUsage = Field(..., description="Token usage")
    finish: Optional[str] = Field(None, description="Finish reason")
    compacted: Optional[bool] = Field(None, description="Archived by compaction (soft-deleted)")


# Union type for message info - discriminated by role
MessageInfo = Union[UserMessageInfo, AssistantMessageInfo]


class MessageWithParts(BaseModel):
    """
    Message with parts - Flocks compatible
    
    Matches TypeScript MessageV2.WithParts structure
    """
    model_config = ConfigDict(populate_by_name=True, by_alias=True)
    
    info: MessageInfo = Field(..., description="Message information")
    parts: List[PartType] = Field(default_factory=list, description="Message parts")


# Backwards compatible alias
MessageV2 = MessageInfo


class Message:
    """
    Message management namespace
    
    Features:
    - Persistent storage using Storage module (SQLite)
    - Thread-safe operations with asyncio locks
    - In-memory cache for performance
    - Flocks compatible API
    """
    
    _messages_cache: Dict[str, List[MessageInfo]] = {}
    _msg_id_index: Dict[str, Dict[str, int]] = {}  # session_id -> {message_id -> list index}
    _parts_cache: Dict[str, Dict[str, List[PartType]]] = {}  # session_id -> message_id -> parts
    _parts_revision_cache: Dict[str, Dict[str, int]] = {}
    _parts_serialized_cache: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    _parts_storage_format: Dict[str, Literal["legacy", "per_message"]] = {}
    _parts_persisted_mids: Dict[str, set[str]] = {}
    _parts_flush_tasks: Dict[str, asyncio.Task] = {}
    _parts_fully_loaded: set[str] = set()
    _lru: OrderedDict[str, bool] = OrderedDict()  # LRU tracker: move_to_end() is O(1)
    _cache_epoch: int = 0
    _session_cache_generations: OrderedDict[str, int] = OrderedDict()
    _cache_loading_sessions: set[str] = set()
    
    # Maximum number of sessions to keep in cache before evicting oldest
    _MAX_CACHED_SESSIONS = 50
    _MAX_CACHE_GENERATIONS = _MAX_CACHED_SESSIONS * 4
    
    # Storage key prefixes
    _MESSAGE_PREFIX = "message"
    _PARTS_PREFIX = "message_parts"
    _PARTS_ITEM_PREFIX = "message_parts"
    _PARTS_PERSIST_DEBOUNCE_MS = 75
    _CACHE_RELOAD_RETRY_LIMIT = 3

    @classmethod
    def _cancel_parts_flush_task(cls, session_id: str) -> None:
        task = cls._parts_flush_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    @classmethod
    def _cache_token(cls, session_id: str) -> tuple[int, int]:
        return cls._cache_epoch, cls._session_cache_generations.get(session_id, 0)

    @classmethod
    def _bump_cache_epoch(cls) -> None:
        cls._cache_epoch += 1
        cls._session_cache_generations.clear()

    @classmethod
    def _bump_session_cache_generation(cls, session_id: str) -> None:
        generation = cls._session_cache_generations.get(session_id, 0) + 1
        cls._session_cache_generations[session_id] = generation
        cls._session_cache_generations.move_to_end(session_id)
        cls._prune_session_cache_generations()

    @classmethod
    def _prune_session_cache_generations(cls) -> None:
        while len(cls._session_cache_generations) > cls._MAX_CACHE_GENERATIONS:
            for stale_id in list(cls._session_cache_generations):
                if stale_id in cls._cache_loading_sessions:
                    continue
                cls._session_cache_generations.pop(stale_id, None)
                break
            else:
                break

    @classmethod
    def _has_message_cache(cls, session_id: str) -> bool:
        return session_id in cls._lru and session_id in cls._messages_cache

    @classmethod
    def _drop_cached_session(
        cls,
        session_id: str,
        *,
        discard_lock: bool = True,
    ) -> None:
        cls._bump_session_cache_generation(session_id)
        cls._cancel_parts_flush_task(session_id)
        cls._lru.pop(session_id, None)
        cls._messages_cache.pop(session_id, None)
        cls._msg_id_index.pop(session_id, None)
        cls._parts_cache.pop(session_id, None)
        cls._parts_revision_cache.pop(session_id, None)
        cls._parts_serialized_cache.pop(session_id, None)
        cls._parts_storage_format.pop(session_id, None)
        cls._parts_persisted_mids.pop(session_id, None)
        cls._parts_fully_loaded.discard(session_id)
        if discard_lock:
            _session_locks.discard(session_id)

    @classmethod
    def _touch_lru(cls, session_id: str) -> None:
        if session_id in cls._lru:
            cls._lru.move_to_end(session_id)
            return

        while len(cls._lru) >= cls._MAX_CACHED_SESSIONS:
            evict_id, _ = cls._lru.popitem(last=False)
            cls._drop_cached_session(evict_id)
            log.debug("message.cache.evicted", {"session_id": evict_id})
        cls._lru[session_id] = True

    @classmethod
    def _serialize_message_parts(cls, parts: List[PartType]) -> List[Dict[str, Any]]:
        return [p.model_dump() for p in parts]

    @classmethod
    def _parts_blob_key(cls, session_id: str) -> str:
        return f"{cls._PARTS_PREFIX}:{session_id}"

    @classmethod
    def _parts_item_prefix(cls, session_id: str) -> str:
        return f"{cls._PARTS_ITEM_PREFIX}:{session_id}:"

    @classmethod
    def _parts_item_key(cls, session_id: str, message_id: str) -> str:
        return f"{cls._PARTS_ITEM_PREFIX}:{session_id}:{message_id}"

    @classmethod
    def _is_parts_item_key(cls, key: str, session_id: str) -> bool:
        return key.startswith(cls._parts_item_prefix(session_id)) and key.count(":") == 2

    @classmethod
    def _sync_serialized_parts_for_message(cls, session_id: str, message_id: str) -> Dict[str, List[Dict[str, Any]]]:
        serialized = cls._parts_serialized_cache.setdefault(session_id, {})
        serialized[message_id] = cls._serialize_message_parts(
            cls._parts_cache.get(session_id, {}).get(message_id, [])
        )
        return serialized

    @classmethod
    def _touch_parts_revision(cls, session_id: str, message_id: str) -> int:
        revisions = cls._parts_revision_cache.setdefault(session_id, {})
        next_revision = int(revisions.get(message_id, 0)) + 1
        revisions[message_id] = next_revision
        return next_revision

    @classmethod
    def get_parts_revision(cls, session_id: str, message_id: str) -> int:
        """Return the current in-memory parts revision for a message."""
        return int(cls._parts_revision_cache.get(session_id, {}).get(message_id, 0))

    @classmethod
    def _prepare_tool_output_cache(cls, part: PartType) -> PartType:
        """Cache a stable prompt-facing string for completed tool results."""
        if not isinstance(part, ToolPart):
            return part
        state = part.state
        if not hasattr(state, "status") or state.status != "completed":
            return part

        metadata = dict(getattr(state, "metadata", None) or {})
        placeholder = metadata.get("context_compact_placeholder")
        if placeholder:
            output_text = str(placeholder)
            source = "context_compact_placeholder"
        else:
            output = getattr(state, "output", None)
            if output is None:
                output_text = ""
                source = "empty"
            elif isinstance(output, str):
                output_text = output
                source = "state.output"
            else:
                import json as _json
                try:
                    output_text = _json.dumps(output, ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    output_text = str(output)
                state.output = output_text
                source = "state.output"

        metadata["llm_output_text"] = output_text
        metadata["llm_output_len"] = len(output_text)
        metadata["llm_output_source"] = source
        state.metadata = metadata
        return part

    @classmethod
    def _should_debounce_part_persist(cls, part: PartType) -> bool:
        if not isinstance(part, ToolPart):
            return False
        status = getattr(part.state, "status", None)
        return status in {"pending", "running"}

    @classmethod
    def _schedule_parts_flush(cls, session_id: str, *, message_id: Optional[str] = None) -> None:
        if session_id in cls._parts_flush_tasks:
            return

        async def _flush_later() -> None:
            try:
                await asyncio.sleep(cls._PARTS_PERSIST_DEBOUNCE_MS / 1000)
                async with _session_locks.get(session_id):
                    await cls._persist_parts(session_id, message_id=message_id)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.debug("message.parts_flush_task.failed", {
                    "session_id": session_id,
                    "message_id": message_id,
                    "error": str(exc),
                })
            finally:
                cls._parts_flush_tasks.pop(session_id, None)

        cls._parts_flush_tasks[session_id] = asyncio.create_task(_flush_later())
    
    @classmethod
    async def _ensure_cache(cls, session_id: str) -> None:
        """Ensure messages and all parts for a session are loaded into cache.

        Uses a per-session lock so operations on different sessions are
        fully concurrent.
        """
        reload_attempts = 0
        while True:
            await cls._ensure_message_cache(session_id)
            if session_id in cls._parts_fully_loaded:
                if cls._has_message_cache(session_id):
                    cls._touch_lru(session_id)
                    return
                cls._parts_fully_loaded.discard(session_id)

            lock = _session_locks.get(session_id)
            async with lock:
                if session_id in cls._parts_fully_loaded:
                    if cls._has_message_cache(session_id):
                        cls._touch_lru(session_id)
                        return
                    cls._parts_fully_loaded.discard(session_id)
                    continue

                cls._cache_loading_sessions.add(session_id)
                try:
                    token = cls._cache_token(session_id)
                    message_times = {
                        message.id: message.time
                        for message in cls._messages_cache.get(session_id, [])
                    }
                    await cls._load_all_parts_locked(session_id, message_times=message_times)
                    cache_invalidated = (
                        token != cls._cache_token(session_id)
                        or not cls._has_message_cache(session_id)
                    )
                finally:
                    cls._cache_loading_sessions.discard(session_id)
                    cls._prune_session_cache_generations()

                if cache_invalidated:
                    reload_attempts += 1
                    cls._drop_cached_session(session_id, discard_lock=False)
                    if reload_attempts >= cls._CACHE_RELOAD_RETRY_LIMIT:
                        log.warn("message.cache.reload_aborted_after_invalidation", {
                            "session_id": session_id,
                            "attempts": reload_attempts,
                        })
                        raise MessageCacheInvalidatedError(
                            f"Message cache for {session_id} was invalidated during load"
                        )
                    log.debug("message.cache.reload_after_invalidation", {"session_id": session_id})
                    continue

                cls._parts_fully_loaded.add(session_id)
                cls._touch_lru(session_id)
                log.debug("message.cache.loaded", {"session_id": session_id, "parts": "all"})
                return

    @classmethod
    async def _ensure_message_cache(cls, session_id: str) -> None:
        """Ensure message metadata is cached without loading every part.

        The session page only needs recent messages initially. Loading all
        parts here would deserialize large tool outputs even when only the
        newest page is requested.
        """
        if cls._has_message_cache(session_id):
            cls._touch_lru(session_id)
            return
        if session_id in cls._lru:
            cls._drop_cached_session(session_id, discard_lock=False)

        lock = _session_locks.get(session_id)
        async with lock:
            if cls._has_message_cache(session_id):
                cls._touch_lru(session_id)
                return
            if session_id in cls._lru:
                cls._drop_cached_session(session_id, discard_lock=False)

            while len(cls._lru) >= cls._MAX_CACHED_SESSIONS:
                evict_id, _ = cls._lru.popitem(last=False)
                cls._drop_cached_session(evict_id)
                log.debug("message.cache.evicted", {"session_id": evict_id})

            storage_key = f"{cls._MESSAGE_PREFIX}:{session_id}"
            stored_data = await Storage.get(storage_key)

            if isinstance(stored_data, list):
                messages = []
                for msg_data in stored_data:
                    if not isinstance(msg_data, dict):
                        log.warn("message.cache.skipped_non_dict", {
                            "session_id": session_id,
                            "raw_type": type(msg_data).__name__,
                        })
                        continue

                    normalized = cls._normalize_stored_message(msg_data, session_id)
                    role = normalized.get("role", "assistant")
                    try:
                        if role == "user":
                            message = UserMessageInfo.model_validate(normalized)
                        else:
                            message = AssistantMessageInfo.model_validate(normalized)
                    except Exception as exc:
                        log.warn("message.cache.skipped_invalid", {
                            "session_id": session_id,
                            "message_id": normalized.get("id"),
                            "role": role,
                            "error": str(exc),
                        })
                        continue

                    messages.append(message)
                cls._messages_cache[session_id] = messages
            else:
                cls._messages_cache[session_id] = []

            cls._parts_cache.setdefault(session_id, {})
            cls._parts_revision_cache.setdefault(session_id, {})
            cls._parts_serialized_cache.setdefault(session_id, {})
            cls._parts_storage_format.setdefault(session_id, "per_message")
            cls._parts_persisted_mids.setdefault(session_id, set())
            cls._parts_fully_loaded.discard(session_id)
            cls._rebuild_id_index(session_id)
            cls._lru[session_id] = True
            log.debug("message.cache.loaded", {"session_id": session_id, "parts": "lazy"})

    @classmethod
    async def _load_all_parts_locked(
        cls,
        session_id: str,
        *,
        message_times: Dict[str, Dict[str, int]],
    ) -> None:
        """Load all stored parts for a session. Caller must hold session lock."""
        item_entries = await Storage.list_entries(prefix=cls._parts_item_prefix(session_id))
        stored_parts: Dict[str, List[Dict[str, Any]]] = {}
        persisted_mids: set[str] = set()

        for key, value in item_entries:
            if not cls._is_parts_item_key(key, session_id):
                continue
            if not isinstance(value, list):
                continue
            msg_id = key.rsplit(":", 1)[1]
            stored_parts[msg_id] = value
            persisted_mids.add(msg_id)

        if stored_parts:
            cls._parts_storage_format[session_id] = "per_message"
        else:
            legacy_parts = await Storage.get(cls._parts_blob_key(session_id))
            if isinstance(legacy_parts, dict):
                stored_parts = legacy_parts
                persisted_mids = set(legacy_parts.keys())
                cls._parts_storage_format[session_id] = "legacy"
            else:
                cls._parts_storage_format[session_id] = "per_message"

        cls._parts_cache[session_id] = {}
        cls._parts_revision_cache[session_id] = {}
        cls._parts_serialized_cache[session_id] = {}
        for msg_id, parts_data in stored_parts.items():
            if not isinstance(parts_data, list):
                continue
            cls._parts_cache[session_id][msg_id] = cls._deserialize_parts_list(
                session_id,
                msg_id,
                parts_data,
                message_time=message_times.get(msg_id),
            )
            cls._parts_revision_cache[session_id][msg_id] = 0
            cls._parts_serialized_cache[session_id][msg_id] = list(parts_data)
        cls._parts_persisted_mids[session_id] = persisted_mids

    @classmethod
    async def _load_parts_for_message(
        cls,
        session_id: str,
        message_id: str,
        *,
        legacy_parts_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    ) -> List[PartType]:
        """Load one message's parts without hydrating every part in the session."""
        await cls._ensure_message_cache(session_id)
        cached = cls._parts_cache.setdefault(session_id, {}).get(message_id)
        if cached is not None:
            return list(cached)

        message_times = {
            message.id: message.time
            for message in cls._messages_cache.get(session_id, [])
        }
        parts_data = await Storage.get(cls._parts_item_key(session_id, message_id))
        if not isinstance(parts_data, list):
            legacy_parts = legacy_parts_cache
            if legacy_parts is None:
                stored_legacy_parts = await Storage.get(cls._parts_blob_key(session_id))
                legacy_parts = stored_legacy_parts if isinstance(stored_legacy_parts, dict) else None
            if isinstance(legacy_parts, dict):
                cls._parts_storage_format[session_id] = "legacy"
                parts_data = legacy_parts.get(message_id, [])
                cls._parts_persisted_mids.setdefault(session_id, set()).update(legacy_parts.keys())
            else:
                cls._parts_storage_format.setdefault(session_id, "per_message")
                parts_data = []
        else:
            cls._parts_storage_format[session_id] = "per_message"
            cls._parts_persisted_mids.setdefault(session_id, set()).add(message_id)

        parts = cls._deserialize_parts_list(
            session_id,
            message_id,
            parts_data,
            message_time=message_times.get(message_id),
        )
        cls._parts_cache.setdefault(session_id, {})[message_id] = parts
        cls._parts_revision_cache.setdefault(session_id, {})[message_id] = 0
        cls._parts_serialized_cache.setdefault(session_id, {})[message_id] = list(parts_data)
        return list(parts)
    
    @classmethod
    def _rebuild_id_index(cls, session_id: str) -> None:
        """Rebuild the message ID → list-index map for a session."""
        messages = cls._messages_cache.get(session_id, [])
        cls._msg_id_index[session_id] = {m.id: i for i, m in enumerate(messages)}

    @classmethod
    def _first_non_none(cls, *values: Any) -> Any:
        """Return the first value that is not None, preserving 0-like sentinels."""
        for value in values:
            if value is not None:
                return value
        return None

    @classmethod
    def _default_message_time(cls, raw_time: Any) -> Dict[str, int]:
        """Normalize stored message timestamps to the current schema."""
        now_ms = int(datetime.now().timestamp() * 1000)
        if isinstance(raw_time, dict):
            normalized = dict(raw_time)
            created = cls._first_non_none(
                normalized.get("created"),
                normalized.get("start"),
            )
            normalized["created"] = int(created) if created is not None else now_ms
            if "completed" not in normalized and normalized.get("end") is not None:
                normalized["completed"] = int(normalized["end"])
            return normalized
        return {"created": now_ms}

    @classmethod
    def _default_message_path(cls, raw_path: Any) -> Dict[str, str]:
        """Return a safe message path payload for assistant messages."""
        if isinstance(raw_path, MessagePath):
            return raw_path.model_dump()
        if isinstance(raw_path, dict):
            return {
                "cwd": str(raw_path.get("cwd") or "./"),
                "root": str(raw_path.get("root") or ""),
            }
        return MessagePath(cwd="./").model_dump()

    @classmethod
    def _default_token_usage(cls, raw_tokens: Any = None) -> Dict[str, Any]:
        """Return a safe token payload for assistant messages."""
        defaults = TokenUsage().model_dump()
        if isinstance(raw_tokens, TokenUsage):
            return raw_tokens.model_dump()
        if isinstance(raw_tokens, dict):
            normalized = dict(defaults)
            for key in ("input", "output", "reasoning"):
                value = raw_tokens.get(key)
                if value is not None:
                    normalized[key] = value
            cache_raw = raw_tokens.get("cache")
            if isinstance(cache_raw, dict):
                normalized["cache"] = {
                    "read": cache_raw.get("read", 0),
                    "write": cache_raw.get("write", 0),
                }
            return normalized
        return defaults

    @classmethod
    def _normalize_stored_message(
        cls,
        msg_data: Dict[str, Any],
        session_id: str,
    ) -> Dict[str, Any]:
        """Backfill missing fields for legacy or partially-written messages."""
        normalized = dict(msg_data)
        role = normalized.get("role", "assistant")
        normalized["sessionID"] = normalized.get("sessionID") or session_id
        normalized["time"] = cls._default_message_time(normalized.get("time"))

        if role == "user":
            model_raw = normalized.get("model")
            if not isinstance(model_raw, dict):
                model_raw = {}
            normalized["agent"] = normalized.get("agent") or "rex"
            normalized["executionMode"] = (
                normalized.get("executionMode")
                or normalized.get("execution_mode")
                or SessionExecutionMode.BUILD.value
            )
            normalized["model"] = {
                "providerID": model_raw.get("providerID")
                or normalized.get("providerID")
                or "",
                "modelID": model_raw.get("modelID")
                or normalized.get("modelID")
                or "",
            }
            return normalized

        model_raw = normalized.get("model")
        if not isinstance(model_raw, dict):
            model_raw = {}
        normalized["parentID"] = (
            normalized.get("parentID")
            or normalized.get("parent_id")
            or ""
        )
        normalized["modelID"] = (
            normalized.get("modelID")
            or normalized.get("model_id")
            or model_raw.get("modelID")
            or ""
        )
        normalized["providerID"] = (
            normalized.get("providerID")
            or normalized.get("provider_id")
            or model_raw.get("providerID")
            or ""
        )
        normalized["agent"] = normalized.get("agent") or "rex"
        normalized["mode"] = normalized.get("mode") or normalized["agent"] or "standard"
        normalized["path"] = cls._default_message_path(normalized.get("path"))
        normalized["tokens"] = cls._default_token_usage(normalized.get("tokens"))
        return normalized

    @classmethod
    def _default_part_time(
        cls,
        raw_time: Any = None,
        *,
        message_time: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Optional[int]]:
        """Provide best-effort timestamps for legacy stored parts."""
        time_info = raw_time if isinstance(raw_time, dict) else {}
        fallback_time = message_time if isinstance(message_time, dict) else {}
        start = cls._first_non_none(
            time_info.get("start"),
            time_info.get("created"),
            fallback_time.get("start"),
            fallback_time.get("created"),
            0,
        )
        end = cls._first_non_none(
            time_info.get("end"),
            time_info.get("updated"),
            time_info.get("completed"),
            fallback_time.get("end"),
            fallback_time.get("updated"),
            fallback_time.get("completed"),
        )
        if end is None and start is not None:
            end = start
        return {"start": int(start), "end": int(end) if end is not None else None}

    @classmethod
    def _normalize_tool_state(
        cls,
        raw_state: Any,
        *,
        message_time: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Convert legacy tool state payloads to the current shape."""
        fallback_time = cls._default_part_time(message_time=message_time)
        if isinstance(raw_state, dict):
            normalized = dict(raw_state)
        else:
            normalized = {}
            if isinstance(raw_state, str):
                normalized["status"] = raw_state

        status = normalized.get("status")
        if status not in {"pending", "running", "completed", "error"}:
            if "output" in normalized:
                status = "completed"
            elif "error" in normalized:
                status = "error"
            elif "time" in normalized:
                status = "running"
            else:
                status = "pending"
        normalized["status"] = status

        if status == "pending":
            normalized.setdefault("input", {})
            normalized.setdefault("raw", "")
        elif status == "running":
            normalized.setdefault("input", {})
            raw_time = normalized.get("time")
            normalized["time"] = (
                cls._default_part_time(raw_time, message_time=message_time)
                if isinstance(raw_time, dict)
                else fallback_time
            )
        elif status == "completed":
            normalized.setdefault("input", {})
            normalized.setdefault("output", "")
            normalized.setdefault("title", "")
            normalized.setdefault("metadata", {})
            raw_time = normalized.get("time")
            normalized["time"] = (
                cls._default_part_time(raw_time, message_time=message_time)
                if isinstance(raw_time, dict)
                else fallback_time
            )
        elif status == "error":
            normalized.setdefault("input", {})
            normalized.setdefault("error", "")
            raw_time = normalized.get("time")
            normalized["time"] = (
                cls._default_part_time(raw_time, message_time=message_time)
                if isinstance(raw_time, dict)
                else fallback_time
            )

        return normalized

    @classmethod
    def _normalize_part_data(
        cls,
        part_data: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        message_time: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Normalize legacy/exported part payloads before validation."""
        normalized = dict(part_data)
        if session_id and not normalized.get("sessionID"):
            normalized["sessionID"] = session_id
        if message_id and not normalized.get("messageID"):
            normalized["messageID"] = message_id

        part_type = normalized.get("type", "text")
        metadata = normalized.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}

        if "content" in normalized and "text" not in normalized:
            normalized["text"] = normalized.get("content", "")

        raw_time = normalized.get("time")
        if part_type == "text":
            normalized.setdefault("text", "")
            if isinstance(raw_time, dict):
                if "start" not in raw_time and any(
                    key in raw_time for key in ("created", "updated", "completed", "end")
                ):
                    normalized["time"] = cls._default_part_time(
                        raw_time,
                        message_time=message_time,
                    )
            elif raw_time is not None:
                normalized.pop("time", None)
            if metadata is not None and not isinstance(metadata, dict):
                normalized["metadata"] = None
        elif part_type == "reasoning":
            normalized.setdefault("text", normalized.get("content", ""))
            normalized.setdefault("metadata", metadata_dict or None)
            normalized["time"] = cls._default_part_time(
                raw_time,
                message_time=message_time,
            )
        elif part_type == "tool":
            normalized.setdefault("callID", metadata_dict.get("callID") or normalized.get("id", ""))
            normalized.setdefault("tool", metadata_dict.get("tool") or normalized.get("tool", "unknown"))
            raw_state = normalized.get("state")
            if raw_state is None and metadata_dict:
                raw_state = metadata_dict.get("state")
            normalized["state"] = cls._normalize_tool_state(
                raw_state,
                message_time=message_time,
            )
            normalized.setdefault("metadata", metadata_dict or None)
        elif part_type == "file":
            normalized.setdefault("mime", metadata_dict.get("mime") or "application/octet-stream")
            normalized.setdefault("filename", metadata_dict.get("filename"))
            normalized.setdefault("url", metadata_dict.get("url") or normalized.get("content", ""))
        elif part_type == "snapshot":
            normalized.setdefault("snapshot", metadata_dict.get("snapshot") or normalized.get("content", ""))
        elif part_type == "patch":
            normalized.setdefault("hash", metadata_dict.get("hash") or "")
            normalized.setdefault("files", metadata_dict.get("files") or [])
        elif part_type == "step-finish":
            normalized.setdefault("reason", metadata_dict.get("reason") or "completed")
            normalized.setdefault("snapshot", metadata_dict.get("snapshot"))
            normalized.setdefault("cost", metadata_dict.get("cost") or 0.0)
            normalized.setdefault("tokens", metadata_dict.get("tokens") or cls._default_token_usage())
        elif part_type == "agent":
            normalized.setdefault("name", metadata_dict.get("name") or normalized.get("content") or "agent")
        elif part_type == "subtask":
            normalized.setdefault("prompt", metadata_dict.get("prompt") or normalized.get("content", ""))
            normalized.setdefault("description", metadata_dict.get("description") or "")
            normalized.setdefault("agent", metadata_dict.get("agent") or "agent")
        elif part_type == "retry":
            normalized.setdefault("attempt", metadata_dict.get("attempt") or 1)
            normalized.setdefault("error", metadata_dict.get("error") or {})
            normalized["time"] = cls._default_part_time(
                metadata_dict.get("time") or raw_time,
                message_time=message_time,
            )
        elif part_type == "compaction":
            normalized.setdefault("auto", bool(metadata_dict.get("auto", False)))

        return normalized

    @classmethod
    def _deserialize_parts_list(
        cls,
        session_id: str,
        message_id: str,
        parts_data: List[Dict[str, Any]],
        *,
        message_time: Optional[Dict[str, Any]] = None,
    ) -> List[PartType]:
        """Best-effort deserialize a stored parts list without dropping the session."""
        parts: List[PartType] = []
        for raw_part in parts_data:
            if not isinstance(raw_part, dict):
                log.warn("message.part.skipped_non_dict", {
                    "session_id": session_id,
                    "message_id": message_id,
                    "raw_type": type(raw_part).__name__,
                })
                continue
            try:
                parts.append(
                    cls.deserialize_part(
                        raw_part,
                        session_id=session_id,
                        message_id=message_id,
                        message_time=message_time,
                    )
                )
            except Exception as exc:
                log.warn("message.part.skipped_invalid", {
                    "session_id": session_id,
                    "message_id": message_id,
                    "part_id": raw_part.get("id"),
                    "error": str(exc),
                })
        return parts

    @classmethod
    def deserialize_part(
        cls,
        part_data: Dict[str, Any],
        *,
        session_id: Optional[str] = None,
        message_id: Optional[str] = None,
        message_time: Optional[Dict[str, Any]] = None,
    ) -> PartType:
        """Deserialize a part from storage format"""
        part_data = cls._normalize_part_data(
            part_data,
            session_id=session_id,
            message_id=message_id,
            message_time=message_time,
        )
        part_type = part_data.get('type', 'text')
        
        type_map = {
            'text': TextPart,
            'file': FilePart,
            'tool': ToolPart,
            'reasoning': ReasoningPart,
            'snapshot': SnapshotPart,
            'patch': PatchPart,
            'step-start': StepStartPart,
            'step-finish': StepFinishPart,
            'agent': AgentPart,
            'subtask': SubtaskPart,
            'retry': RetryPart,
            'compaction': CompactionPart,
        }
        
        model_class = type_map.get(part_type, TextPart)
        return model_class.model_validate(part_data)

    @staticmethod
    def _normalize_assistant_message(message: MessageInfo) -> MessageInfo:
        """Coerce assistant fields back to typed models before serialization."""
        if not isinstance(message, AssistantMessageInfo):
            return message

        updates: Dict[str, Any] = {}
        if not isinstance(message.tokens, TokenUsage):
            updates["tokens"] = TokenUsage.model_validate(
                Message._default_token_usage(message.tokens)
            )
        if not isinstance(message.path, MessagePath):
            updates["path"] = MessagePath.model_validate(
                Message._default_message_path(message.path)
            )

        if not updates:
            return message
        return message.model_copy(update=updates)

    @classmethod
    def _normalize_message_patch(
        cls,
        message: MessageInfo,
        patch: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Coerce typed assistant fields when update callers pass plain dicts."""
        if not isinstance(message, AssistantMessageInfo):
            return patch

        normalized = dict(patch)
        if isinstance(normalized.get("tokens"), dict):
            normalized["tokens"] = TokenUsage.model_validate(normalized["tokens"])
        if isinstance(normalized.get("path"), dict):
            normalized["path"] = MessagePath.model_validate(normalized["path"])
        return normalized
    
    @classmethod
    async def _persist_messages(cls, session_id: str) -> None:
        """
        Persist messages to storage
        
        Args:
            session_id: Session ID
        """
        storage_key = f"{cls._MESSAGE_PREFIX}:{session_id}"
        messages = cls._messages_cache.get(session_id, [])
        serialized_messages = []
        for index, message in enumerate(messages):
            normalized = cls._normalize_assistant_message(message)
            if normalized is not message:
                messages[index] = normalized
            serialized_messages.append(normalized.model_dump())
        await Storage.set(storage_key, serialized_messages)
    
    @classmethod
    async def _persist_parts(cls, session_id: str, *, message_id: Optional[str] = None) -> None:
        """Persist parts to storage.

        Existing legacy sessions keep writing the aggregated
        ``message_parts:<session_id>`` blob.  New sessions write each message's
        parts to ``message_parts:<session_id>:<message_id>`` so hot-path part
        updates no longer rewrite the full session blob.
        """
        all_parts = cls._parts_cache.get(session_id, {})
        serialized = cls._parts_serialized_cache.setdefault(session_id, {})
        storage_format = cls._parts_storage_format.setdefault(session_id, "per_message")

        if storage_format == "legacy":
            parts_key = cls._parts_blob_key(session_id)
            if message_id is not None:
                serialized[message_id] = cls._serialize_message_parts(all_parts.get(message_id, []))
            else:
                serialized = {
                    mid: cls._serialize_message_parts(mparts)
                    for mid, mparts in list(all_parts.items())
                }
                cls._parts_serialized_cache[session_id] = serialized
            cls._parts_persisted_mids[session_id] = set(serialized.keys())
            await Storage.set(parts_key, serialized, "message_parts")
            return

        if message_id is not None:
            serialized_one = cls._serialize_message_parts(all_parts.get(message_id, []))
            serialized[message_id] = serialized_one
            await Storage.set(
                cls._parts_item_key(session_id, message_id),
                serialized_one,
                "message_part",
            )
            cls._parts_persisted_mids.setdefault(session_id, set()).add(message_id)
            return

        persisted_mids = cls._parts_persisted_mids.setdefault(session_id, set())
        current_mids = set(all_parts.keys())
        for mid, mparts in list(all_parts.items()):
            serialized_one = cls._serialize_message_parts(mparts)
            if serialized.get(mid) != serialized_one or mid not in persisted_mids:
                await Storage.set(
                    cls._parts_item_key(session_id, mid),
                    serialized_one,
                    "message_part",
                )
            serialized[mid] = serialized_one
            persisted_mids.add(mid)

        for stale_mid in list(persisted_mids - current_mids):
            await Storage.delete(cls._parts_item_key(session_id, stale_mid))
            serialized.pop(stale_mid, None)
            persisted_mids.discard(stale_mid)
    
    @classmethod
    async def create(
        cls,
        session_id: str,
        role: MessageRole,
        content: str,
        **kwargs
    ) -> MessageInfo:
        """
        Create a new message
        
        Args:
            session_id: Session ID
            role: Message role
            content: Message content
            **kwargs: Additional fields
            
        Returns:
            Message info
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            message_id = kwargs.pop("id", None) or Identifier.ascending("message")
            
            # Create timestamps if not provided
            if "time" not in kwargs:
                now_ms = int(datetime.now().timestamp() * 1000)
                kwargs["time"] = {"created": now_ms}
            
            # Pop TextPart-specific fields before message creation — they
            # belong on TextPart, not on UserMessageInfo/AssistantMessageInfo.
            _synthetic = kwargs.pop("synthetic", None)
            _part_metadata = kwargs.pop("part_metadata", None)
            
            # Create appropriate message type based on role
            if role == MessageRole.USER:
                # Get model with fallback (avoid hardcoded defaults)
                model = kwargs.pop("model", None)
                if not model:
                    from flocks.session.core.defaults import fallback_provider_id, fallback_model_id
                    model = {"providerID": fallback_provider_id(), "modelID": fallback_model_id()}
                
                # Provide required fields for UserMessageInfo
                message = UserMessageInfo(
                    id=message_id,
                    sessionID=session_id,
                    role=role,
                    agent=kwargs.pop("agent", "rex"),
                    model=model,
                    time=kwargs.pop("time"),
                    **kwargs
                )
            else:  # ASSISTANT or SYSTEM
                # Provide required fields for AssistantMessageInfo
                message = AssistantMessageInfo(
                    id=message_id,
                    sessionID=session_id,
                    role=role,
                    parentID=kwargs.pop("parentID", kwargs.pop("parent_id", "")),
                    modelID=kwargs.pop("modelID", kwargs.pop("model_id", "")),
                    providerID=kwargs.pop("providerID", kwargs.pop("provider_id", "")),
                    mode=kwargs.pop("mode", "standard"),
                    agent=kwargs.pop("agent", "rex"),
                    path=kwargs.pop("path", MessagePath(cwd="./")),
                    tokens=kwargs.pop("tokens", TokenUsage()),
                    time=kwargs.pop("time"),
                    **kwargs
                )
            
            if session_id not in cls._messages_cache:
                cls._messages_cache[session_id] = []
            cls._messages_cache[session_id].append(message)
            if session_id not in cls._msg_id_index:
                cls._msg_id_index[session_id] = {}
            cls._msg_id_index[session_id][message.id] = len(cls._messages_cache[session_id]) - 1
            
            # Create and store text part.
            # Propagate ``synthetic`` so callers can mark system-generated
            # content (e.g. compaction continuation messages).
            _part_id = kwargs.pop("part_id", None)
            _part_extras = {}
            if _synthetic is not None:
                _part_extras["synthetic"] = _synthetic
            if _part_metadata is not None:
                _part_extras["metadata"] = _part_metadata
            part = TextPart(
                id=_part_id or Identifier.ascending("part"),
                sessionID=session_id,
                messageID=message.id,
                type="text",
                text=content,
                **_part_extras,
            )
            
            # Store part in cache
            if session_id not in cls._parts_cache:
                cls._parts_cache[session_id] = {}
            if message.id not in cls._parts_cache[session_id]:
                cls._parts_cache[session_id][message.id] = []
            cls._parts_cache[session_id][message.id].append(part)
            
            # Persist to storage
            await cls._persist_messages(session_id)
            await cls._persist_parts(session_id)
            
            log.info("message.created", {
                "id": message.id,
                "session_id": session_id,
                "role": role.value,
                "parts_count": 1,
            })

            # Append-only record (human/audit friendly)
            await Recorder.record_session_message(
                session_id=session_id,
                message_id=message.id,
                role=role.value,
                text=content,
            )
            
            return message
    
    @classmethod
    async def list(cls, session_id: str, include_archived: bool = False) -> List[MessageInfo]:
        """
        List messages for a session
        
        Args:
            session_id: Session ID
            include_archived: If True, include messages archived by compaction.
                Defaults to False so LLM prompt building skips compacted messages.
            
        Returns:
            List of messages
        """
        await cls._ensure_message_cache(session_id)
        messages = cls._messages_cache.get(session_id, [])
        if not include_archived:
            messages = [m for m in messages if not getattr(m, 'compacted', None)]
        return messages
    
    @classmethod
    async def get(cls, session_id: str, message_id: str) -> Optional[MessageInfo]:
        """Get a specific message by ID (O(1) via index)."""
        await cls._ensure_message_cache(session_id)
        idx = cls._msg_id_index.get(session_id, {}).get(message_id)
        if idx is not None:
            messages = cls._messages_cache.get(session_id, [])
            if idx < len(messages) and messages[idx].id == message_id:
                return messages[idx]
        return None
    
    @classmethod
    async def parts(cls, message_id: str, session_id: Optional[str] = None) -> List[PartType]:
        """
        Get parts for a message
        
        Ported from original MessageV2.parts() function.
        
        Args:
            message_id: Message ID
            session_id: Optional session ID (will search all sessions if not provided)
            
        Returns:
            List of parts sorted by ID
        """
        if session_id:
            await cls._ensure_cache(session_id)
            parts = cls._parts_cache.get(session_id, {}).get(message_id, [])
        else:
            # Search all sessions for the message
            parts = []
            for sid in list(cls._parts_cache):
                await cls._ensure_cache(sid)
                if message_id in cls._parts_cache.get(sid, {}):
                    parts = cls._parts_cache[sid][message_id]
                    break
        
        # Parts are stored in ascending insertion order. Return a shallow copy so
        # hot paths can skip an extra sort without exposing the cache directly.
        return list(parts)
    
    @classmethod
    def _cap_tool_part_output(cls, part: PartType) -> PartType:
        """Apply hard size cap to ToolPart output before persistence.

        Prevents a single oversized tool result from consuming the
        entire context window on subsequent LLM calls.
        """
        if not isinstance(part, ToolPart):
            return part
        state = part.state
        if not hasattr(state, 'status') or state.status != "completed":
            return part
        output = getattr(state, 'output', None)
        if output is None:
            return part
        if not isinstance(output, str):
            import json as _json
            try:
                output = _json.dumps(output, ensure_ascii=False)
                state.output = output
            except (TypeError, ValueError):
                output = str(output)
                state.output = output
        from flocks.tool.truncation import HARD_MAX_TOOL_RESULT_CHARS, truncate_tool_result_text
        if len(output) > HARD_MAX_TOOL_RESULT_CHARS:
            state.output = truncate_tool_result_text(
                output,
                HARD_MAX_TOOL_RESULT_CHARS,
                suffix=(
                    "\n\n[Content truncated during persistence - exceeded size limit. "
                    "Use offset/limit parameters or request specific sections.]"
                ),
            )
            log.info("message.tool_part_capped", {
                "part_id": part.id,
                "original_len": len(output),
                "capped_len": len(state.output),
            })
        return part

    @classmethod
    async def store_part(cls, session_id: str, message_id: str, part: PartType) -> PartType:
        """
        Store or update a part for a message
        
        Ported from original Session.updatePart() behavior.
        If a part with the same ID exists, it will be updated.
        Otherwise, the part will be added.
        
        Args:
            session_id: Session ID
            message_id: Message ID
            part: Part to store
            
        Returns:
            The stored part
        """
        # Cap oversized tool results before they reach storage and cache a
        # stable prompt-facing string for subsequent continuation steps.
        part = cls._cap_tool_part_output(part)
        part = cls._prepare_tool_output_cache(part)

        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            if session_id not in cls._parts_cache:
                cls._parts_cache[session_id] = {}
            if message_id not in cls._parts_cache[session_id]:
                cls._parts_cache[session_id][message_id] = []
            
            parts_list = cls._parts_cache[session_id][message_id]
            
            updated = False
            for i, existing in enumerate(parts_list):
                if existing.id == part.id:
                    existing_status = getattr(getattr(existing, "state", None), "status", None)
                    incoming_status = getattr(getattr(part, "state", None), "status", None)
                    if (
                        isinstance(existing, ToolPart)
                        and isinstance(part, ToolPart)
                        and existing_status in {"completed", "error"}
                        and incoming_status in {"pending", "running"}
                    ):
                        log.debug("message.part.skip_terminal_tool_downgrade", {
                            "session_id": session_id,
                            "message_id": message_id,
                            "part_id": part.id,
                            "existing_status": existing_status,
                            "incoming_status": incoming_status,
                        })
                        return existing
                    parts_list[i] = part
                    updated = True
                    break
            
            if not updated:
                parts_list.append(part)

            cls._touch_parts_revision(session_id, message_id)

            if cls._should_debounce_part_persist(part):
                cls._sync_serialized_parts_for_message(session_id, message_id)
                cls._schedule_parts_flush(session_id, message_id=message_id)
            else:
                cls._cancel_parts_flush_task(session_id)
                await cls._persist_parts(session_id, message_id=message_id)
            
            log.debug("message.part.stored" if not updated else "message.part.updated", {
                "session_id": session_id,
                "message_id": message_id,
                "part_id": part.id,
                "type": part.type,
            })

            try:
                if isinstance(part, ToolPart):
                    await Recorder.record_tool_state(
                        session_id=session_id,
                        message_id=message_id,
                        part_id=part.id,
                        call_id=part.callID,
                        tool=part.tool,
                        state=part.state.model_dump() if hasattr(part.state, "model_dump") else dict(part.state),
                    )
            except Exception as _rec_err:
                log.debug("message.recorder.failed", {"part_id": part.id, "error": str(_rec_err)})
            return part
    
    @classmethod
    async def upsert_message_info(cls, session_id: str, message_info: MessageInfo) -> MessageInfo:
        """
        Insert or update a message info in cache (without creating a TextPart).

        Use this when StreamProcessor has already stored parts and you only need
        to persist the message metadata.  Duplicate-safe: if a message with the
        same ID already exists it will be replaced.

        Args:
            session_id: Session ID
            message_info: The message info to upsert

        Returns:
            The upserted message info
        """
        await cls._ensure_cache(session_id)

        async with _session_locks.get(session_id):
            if session_id not in cls._messages_cache:
                cls._messages_cache[session_id] = []

            messages = cls._messages_cache[session_id]
            idx = cls._msg_id_index.get(session_id, {}).get(message_info.id)
            if idx is not None and idx < len(messages) and messages[idx].id == message_info.id:
                messages[idx] = message_info
            else:
                messages.append(message_info)
                cls._rebuild_id_index(session_id)

            await cls._persist_messages(session_id)

        log.debug("message.upserted", {
            "id": message_info.id,
            "session_id": session_id,
            "role": getattr(message_info, "role", "unknown"),
        })
        return message_info

    @classmethod
    async def ensure_text_part(
        cls,
        session_id: str,
        message_id: str,
        text: str,
    ) -> bool:
        """
        Ensure the message has at least one non-empty TextPart.

        If StreamProcessor already stored text parts this is a no-op.
        Otherwise a fallback TextPart is created.

        Args:
            session_id: Session ID
            message_id: Message ID
            text: Fallback text content

        Returns:
            True if a fallback part was created, False if one already existed
        """
        await cls._ensure_cache(session_id)

        async with _session_locks.get(session_id):
            existing_parts = cls._parts_cache.get(session_id, {}).get(message_id, [])
            has_text = any(
                getattr(p, "type", None) == "text" and getattr(p, "text", "")
                for p in existing_parts
            )
            if has_text or not text:
                return False

        # Outside lock – store_part acquires its own lock
        fallback = TextPart(
            id=Identifier.ascending("part"),
            sessionID=session_id,
            messageID=message_id,
            type="text",
            text=text,
        )
        await cls.store_part(session_id, message_id, fallback)
        log.debug("message.fallback_text_part_created", {
            "session_id": session_id,
            "message_id": message_id,
        })
        return True

    @classmethod
    async def get_with_parts(cls, session_id: str, message_id: str) -> Optional[MessageWithParts]:
        """
        Get a message with its parts
        
        Args:
            session_id: Session ID
            message_id: Message ID
            
        Returns:
            Message with parts or None
        """
        message = await cls.get(session_id, message_id)
        if not message:
            return None
        
        parts = await cls.parts(message_id, session_id)
        return MessageWithParts(info=message, parts=parts)
    
    @classmethod
    async def list_with_parts(cls, session_id: str, include_archived: bool = False) -> List[MessageWithParts]:
        """
        List all messages with parts for a session
        
        Args:
            session_id: Session ID
            include_archived: If True, include messages archived by compaction.
            
        Returns:
            List of messages with parts
        """
        await cls._ensure_cache(session_id)
        messages = await cls.list(session_id, include_archived=include_archived)
        result = []
        for message in messages:
            parts = cls._parts_cache.get(session_id, {}).get(message.id, [])
            result.append(MessageWithParts(info=message, parts=parts))
        return result

    @classmethod
    async def list_recent_with_parts(
        cls,
        session_id: str,
        *,
        limit: int = 50,
        before: Optional[str] = None,
        include_archived: bool = True,
    ) -> tuple[List[MessageWithParts], bool, Optional[str]]:
        """List one recent page of messages and lazily load only their parts.

        ``before`` is a message id cursor. The returned page preserves
        chronological order and ``next_before`` is the first message id in the
        page, suitable for loading older messages.
        """
        safe_limit = max(int(limit), 1)
        messages = await cls.list(session_id, include_archived=include_archived)
        if before:
            before_index = next(
                (idx for idx, message in enumerate(messages) if message.id == before),
                len(messages),
            )
            candidate_messages = messages[:before_index]
        else:
            candidate_messages = messages

        has_more = len(candidate_messages) > safe_limit
        page_messages = candidate_messages[-safe_limit:]
        legacy_parts_cache: Optional[Dict[str, List[Dict[str, Any]]]] = None
        missing_message_ids = [
            message.id
            for message in page_messages
            if message.id not in cls._parts_cache.setdefault(session_id, {})
        ]
        if missing_message_ids:
            has_per_message_parts = False
            for message_id in missing_message_ids:
                parts_data = await Storage.get(cls._parts_item_key(session_id, message_id))
                if isinstance(parts_data, list):
                    has_per_message_parts = True
                    break
            if not has_per_message_parts:
                stored_legacy_parts = await Storage.get(cls._parts_blob_key(session_id))
                if isinstance(stored_legacy_parts, dict):
                    legacy_parts_cache = stored_legacy_parts

        result = []
        for message in page_messages:
            parts = await cls._load_parts_for_message(
                session_id,
                message.id,
                legacy_parts_cache=legacy_parts_cache,
            )
            result.append(MessageWithParts(info=message, parts=parts))
        next_before = page_messages[0].id if has_more and page_messages else None
        return result, has_more, next_before

    @classmethod
    async def get_with_parts_lazy(cls, session_id: str, message_id: str) -> Optional[MessageWithParts]:
        """Get one message with parts without hydrating all session parts."""
        message = await cls.get(session_id, message_id)
        if not message:
            return None
        parts = await cls._load_parts_for_message(session_id, message_id)
        return MessageWithParts(info=message, parts=parts)
    
    @classmethod
    async def delete(cls, session_id: str, message_id: str) -> bool:
        """
        Delete a message
        
        Args:
            session_id: Session ID
            message_id: Message ID
            
        Returns:
            True if deleted
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            idx = cls._msg_id_index.get(session_id, {}).get(message_id)
            if idx is None:
                return False
            messages = cls._messages_cache.get(session_id, [])
            if idx < len(messages) and messages[idx].id == message_id:
                missing = object()
                removed_message = messages.pop(idx)
                cls._rebuild_id_index(session_id)
                parts_cache = cls._parts_cache.get(session_id)
                removed_parts = (
                    parts_cache.pop(message_id, missing)
                    if parts_cache is not None
                    else missing
                )
                parts_revision_cache = cls._parts_revision_cache.get(session_id)
                removed_revision = (
                    parts_revision_cache.pop(message_id, missing)
                    if parts_revision_cache is not None
                    else missing
                )
                parts_serialized_cache = cls._parts_serialized_cache.get(session_id)
                removed_serialized = (
                    parts_serialized_cache.pop(message_id, missing)
                    if parts_serialized_cache is not None
                    else missing
                )
                had_pending_parts_flush = session_id in cls._parts_flush_tasks
                cls._cancel_parts_flush_task(session_id)
                try:
                    await cls._persist_messages(session_id)
                except BaseException:
                    # Message metadata is the deletion commit point. Restore
                    # every in-memory index/cache if it was not persisted so a
                    # later retry or process restart sees the same message.
                    messages.insert(idx, removed_message)
                    cls._rebuild_id_index(session_id)
                    if parts_cache is not None and removed_parts is not missing:
                        parts_cache[message_id] = removed_parts
                    if (
                        parts_revision_cache is not None
                        and removed_revision is not missing
                    ):
                        parts_revision_cache[message_id] = removed_revision
                    if (
                        parts_serialized_cache is not None
                        and removed_serialized is not missing
                    ):
                        parts_serialized_cache[message_id] = removed_serialized
                    if had_pending_parts_flush:
                        cls._schedule_parts_flush(
                            session_id,
                            message_id=message_id,
                        )
                    raise

                try:
                    if cls._parts_storage_format.get(session_id) == "legacy":
                        await cls._persist_parts(session_id)
                    else:
                        await Storage.delete(cls._parts_item_key(session_id, message_id))
                        cls._parts_persisted_mids.setdefault(session_id, set()).discard(
                            message_id
                        )
                except Exception as exc:
                    # Metadata deletion has committed. Orphaned parts are not
                    # user-visible and can be cleaned later; restoring the
                    # message here would make cache and durable metadata diverge.
                    log.warn("message.delete.parts_cleanup_failed", {
                        "session_id": session_id,
                        "message_id": message_id,
                        "error": str(exc),
                    })
                log.info("message.deleted", {"id": message_id, "session_id": session_id})
                return True
            return False
    
    @classmethod
    async def archive(cls, session_id: str, message_id: str) -> bool:
        """
        Archive a message (soft delete for compaction).

        Marks the message as compacted=True instead of physically deleting it,
        so the frontend can still display it in a collapsed section while the
        LLM prompt builder skips it.

        Args:
            session_id: Session ID
            message_id: Message ID

        Returns:
            True if archived
        """
        await cls._ensure_cache(session_id)

        async with _session_locks.get(session_id):
            messages = cls._messages_cache.get(session_id, [])
            for i, message in enumerate(messages):
                if message.id == message_id:
                    messages[i] = message.model_copy(update={"compacted": True})
                    await cls._persist_messages(session_id)

                    log.info("message.archived", {
                        "id": message_id,
                        "session_id": session_id,
                    })
                    return True
            return False

    @classmethod
    async def clear(cls, session_id: str) -> int:
        """
        Clear all messages for a session
        
        Args:
            session_id: Session ID
            
        Returns:
            Number of messages cleared
        """
        await cls._ensure_message_cache(session_id)
        
        async with _session_locks.get(session_id):
            count = len(cls._messages_cache.get(session_id, []))
            cls._messages_cache[session_id] = []
            cls._parts_cache[session_id] = {}
            cls._parts_revision_cache[session_id] = {}
            cls._parts_serialized_cache[session_id] = {}
            cls._parts_persisted_mids[session_id] = set()
            cls._parts_storage_format[session_id] = "per_message"
            cls._parts_fully_loaded.add(session_id)
            cls._cancel_parts_flush_task(session_id)
            
            # Persist changes
            await cls._persist_messages(session_id)
            await Storage.clear(prefix=cls._parts_item_prefix(session_id))
            await Storage.delete(cls._parts_blob_key(session_id))
            
            log.info("messages.cleared", {
                "session_id": session_id,
                "count": count,
            })
            
            return count
    
    @classmethod
    def invalidate_cache(cls, session_id: Optional[str] = None) -> None:
        """
        Invalidate cache for a session or all sessions
        
        Args:
            session_id: Optional session ID, if None invalidates all
        """
        if session_id:
            cls._drop_cached_session(session_id)
        else:
            for sid in list(cls._parts_flush_tasks):
                cls._cancel_parts_flush_task(sid)
            cls._bump_cache_epoch()
            cls._lru.clear()
            cls._messages_cache.clear()
            cls._msg_id_index.clear()
            cls._parts_cache.clear()
            cls._parts_revision_cache.clear()
            cls._parts_serialized_cache.clear()
            cls._parts_storage_format.clear()
            cls._parts_persisted_mids.clear()
            cls._parts_fully_loaded.clear()
            _session_locks.clear()
        log.debug("message.cache.invalidated", {"session_id": session_id})
    
    @classmethod
    async def get_text_content(cls, message: MessageInfo) -> str:
        """
        Extract text content from message
        
        Args:
            message: Message info
            
        Returns:
            Combined text content
        """
        # Get parts from cache
        session_id = message.sessionID
        message_id = message.id
        await cls._ensure_cache(session_id)
        parts = cls._parts_cache.get(session_id, {}).get(message_id, [])
        
        texts = []
        for part in parts:
            if part.type == "text":
                texts.append(part.text)
        return "\n".join(texts)
    
    @classmethod
    async def to_llm_format(cls, messages: List[MessageInfo]) -> List[Dict[str, Any]]:
        """
        Convert messages to LLM API format
        
        Args:
            messages: List of messages
            
        Returns:
            List of messages in LLM format
        """
        result = []
        
        for message in messages:
            # Simple text format for now
            content = await cls.get_text_content(message)
            
            # role is already a string in Flocks format
            role = message.role.value if hasattr(message.role, 'value') else message.role
            
            result.append({
                "role": role,
                "content": content,
            })
        
        return result
    
    @classmethod
    def to_model_message(cls, messages_with_parts: List[MessageWithParts]) -> List[Dict[str, Any]]:
        """
        Convert messages to model message format (with parts)
        
        Mirrors TypeScript MessageV2.toModelMessage
        
        Args:
            messages_with_parts: List of MessageWithParts
            
        Returns:
            List of model messages with content parts
        """
        result = []
        
        for mwp in messages_with_parts:
            message = mwp.info
            content_parts = []
            
            for part in mwp.parts:
                if part.type == "text":
                    if getattr(part, "ignored", None):
                        continue
                    content_parts.append({
                        "type": "text",
                        "text": getattr(part, "text", ""),
                    })
                elif part.type == "file":
                    mime = getattr(part, "mime", "")
                    if mime.startswith("image/"):
                        content_parts.append({
                            "type": "image",
                            "image": getattr(part, "url", ""),
                            "mimeType": mime,
                        })
                    else:
                        filename = getattr(part, "filename", "file")
                        content_parts.append({
                            "type": "text",
                            "text": f"[File: {filename}]",
                        })
                elif part.type == "tool":
                    state = getattr(part, "state", None)
                    if not state:
                        continue
                    status = getattr(state, "status", None)
                    
                    if status == "completed":
                        output = getattr(state, "output", "")
                        metadata = getattr(state, "metadata", {}) or {}
                        time_info = getattr(state, "time", {})
                        if metadata.get("context_compact_placeholder"):
                            output = str(metadata["context_compact_placeholder"])
                        elif isinstance(time_info, dict) and time_info.get("compacted"):
                            output = "[Tool output compacted]"
                        content_parts.append({
                            "type": "tool-result",
                            "toolCallId": getattr(part, "callID", part.id),
                            "result": output,
                        })
                    elif status == "error":
                        error_msg = getattr(state, "error", "Tool execution failed")
                        content_parts.append({
                            "type": "tool-result",
                            "toolCallId": getattr(part, "callID", part.id),
                            "result": f"Error: {error_msg}",
                            "isError": True,
                        })
                elif part.type == "reasoning":
                    content_parts.append({
                        "type": "reasoning",
                        "reasoning": getattr(part, "text", ""),
                    })
            
            if content_parts:
                role = message.role.value if hasattr(message.role, 'value') else message.role
                result.append({
                    "role": role,
                    "content": content_parts,
                })
        
        return result
    
    @classmethod
    def filter_compacted(cls, messages: List[MessageInfo]) -> List[MessageInfo]:
        """
        Filter out messages archived by compaction.
        
        Args:
            messages: List of messages
            
        Returns:
            Messages that have not been compacted
        """
        return [m for m in messages if not getattr(m, "compacted", None)]
    
    @classmethod
    def message_to_api(cls, message: MessageInfo) -> Dict[str, Any]:
        """
        Convert MessageInfo to LLM API format.
        
        Ported from original MessageV2.toApi() conversion.
        """
        from flocks.session.message import MessageSync
        
        # Get text content from parts (sync access via cache)
        text_content = MessageSync.get_text_content(message)
        
        # Build base message
        api_message: Dict[str, Any] = {
            "role": message.role if isinstance(message.role, str) else message.role.value,
            "content": text_content,
        }
        
        # Add tool results if present (sync access via cache)
        parts = MessageSync.parts(message.id, message.sessionID)
        tool_results = []
        
        for part in parts:
            if part.type == "tool" and hasattr(part, 'state'):
                if part.state.status == "completed":
                    time_info = getattr(part.state, 'time', None)
                    metadata = getattr(part.state, 'metadata', None) or {}
                    if metadata.get("context_compact_placeholder"):
                        output = str(metadata["context_compact_placeholder"])
                    elif isinstance(time_info, dict) and time_info.get("compacted"):
                        output = "[Tool output compacted]"
                    else:
                        output = part.state.output
                        if output is None:
                            output = ""
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": part.callID,
                        "content": output,
                    })
        
        if tool_results:
            if isinstance(api_message["content"], str):
                api_message["content"] = [
                    {"type": "text", "text": api_message["content"]}
                ] + tool_results
            else:
                api_message["content"].extend(tool_results)
        
        return api_message
    
    @classmethod
    def api_to_message(cls, api_message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert LLM API format to MessageInfo format.
        
        Ported from original MessageV2.fromApi() conversion.
        """
        role = api_message.get("role", "user")
        content = api_message.get("content", "")
        
        # Handle string content
        if isinstance(content, str):
            return {
                "role": role,
                "content": content,
            }
        
        # Handle structured content (list of parts)
        text_parts = []
        tool_parts = []
        
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    item_type = item.get("type", "text")
                    
                    if item_type == "text":
                        text_parts.append(item.get("text", ""))
                    elif item_type == "tool_result":
                        tool_parts.append({
                            "type": "tool",
                            "callID": item.get("tool_use_id", ""),
                            "state": {
                                "status": "completed",
                                "output": item.get("content", ""),
                            },
                        })
        
        return {
            "role": role,
            "content": "\n".join(text_parts),
            "parts": tool_parts,
        }
    
    @classmethod
    async def update(cls, session_id: str, message_id: str, **updates) -> Optional[MessageInfo]:
        """
        Update a message
        
        Args:
            session_id: Session ID
            message_id: Message ID
            **updates: Fields to update
            
        Returns:
            Updated message or None
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            messages = cls._messages_cache.get(session_id, [])
            message = None
            msg_index = -1
            for i, m in enumerate(messages):
                if m.id == message_id:
                    message = m
                    msg_index = i
                    break
            if not message:
                return None
            
            # Build update dict (skip None values)
            patch = {k: v for k, v in updates.items() if v is not None}
            patch = cls._normalize_message_patch(message, patch)
            
            # Update timestamp
            time_data = message.time if hasattr(message, 'time') else message.model_dump().get("time", {})
            if isinstance(time_data, dict):
                patch["time"] = {**time_data, "updated": int(datetime.now().timestamp() * 1000)}
            
            updated = message.model_copy(update=patch)
            messages[msg_index] = updated
            
            await cls._persist_messages(session_id)
            
            log.info("message.updated", {
                "id": message_id,
                "session_id": session_id,
            })
            
            return updated
    
    @classmethod
    async def add_part(cls, session_id: str, message_id: str, part: PartType) -> Optional[MessageInfo]:
        """
        Add a part to a message
        
        Args:
            session_id: Session ID
            message_id: Message ID
            part: Part to add
            
        Returns:
            Updated message or None
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            message = next(
                (m for m in cls._messages_cache.get(session_id, []) if m.id == message_id),
                None,
            )
            if not message:
                return None
            
            # Add to parts cache
            if session_id not in cls._parts_cache:
                cls._parts_cache[session_id] = {}
            if message_id not in cls._parts_cache[session_id]:
                cls._parts_cache[session_id][message_id] = []
            
            cls._parts_cache[session_id][message_id].append(part)
            cls._touch_parts_revision(session_id, message_id)
            
            cls._cancel_parts_flush_task(session_id)
            await cls._persist_parts(session_id, message_id=message_id)
            
            log.info("message.part_added", {
                "message_id": message_id,
                "part_id": part.id,
                "type": part.type,
            })
            
            return message
    
    @classmethod
    async def update_part(cls, session_id: str, message_id: str, part_id: str, **updates) -> Optional[PartType]:
        """
        Update a message part
        
        Args:
            session_id: Session ID
            message_id: Message ID
            part_id: Part ID
            **updates: Fields to update
            
        Returns:
            Updated part or None
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            parts = cls._parts_cache.get(session_id, {}).get(message_id, [])
            
            for i, part in enumerate(parts):
                if part.id == part_id:
                    part_data = part.model_dump()
                    for key, value in updates.items():
                        if key in part_data:
                            part_data[key] = value
                    
                    updated_part = cls.deserialize_part(part_data)
                    updated_part = cls._cap_tool_part_output(updated_part)
                    updated_part = cls._prepare_tool_output_cache(updated_part)
                    parts[i] = updated_part
                    cls._touch_parts_revision(session_id, message_id)
                    
                    cls._cancel_parts_flush_task(session_id)
                    await cls._persist_parts(session_id, message_id=message_id)
                    
                    log.info("message.part_updated", {
                        "message_id": message_id,
                        "part_id": part_id,
                    })
                    
                    return updated_part
            
            return None
    
    @classmethod
    async def remove_part(cls, session_id: str, message_id: str, part_id: str) -> bool:
        """
        Remove a part from a message
        
        Args:
            session_id: Session ID
            message_id: Message ID
            part_id: Part ID
            
        Returns:
            True if removed
        """
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            parts = cls._parts_cache.get(session_id, {}).get(message_id, [])
            
            for i, part in enumerate(parts):
                if part.id == part_id:
                    parts.pop(i)
                    cls._touch_parts_revision(session_id, message_id)
                    
                    cls._cancel_parts_flush_task(session_id)
                    await cls._persist_parts(session_id, message_id=message_id)
                    
                    log.info("message.part_removed", {
                        "message_id": message_id,
                        "part_id": part_id,
                    })
                    
                    return True
            
            return False


class MessageSync:
    """
    Synchronous wrapper for Message class
    
    Provides synchronous methods for non-async contexts.
    Uses the in-memory cache directly for sync operations.
    
    Note: These methods should only be used in non-async contexts.
    For FastAPI routes, use the async Message methods directly.
    """
    
    @classmethod
    def _run_async(cls, coro):
        """Run async coroutine in sync context"""
        import asyncio
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running event loop — safe to use asyncio.run
            return asyncio.run(coro)
        raise RuntimeError(
            "MessageSync methods cannot be called from an async context. "
            "Use the async Message methods instead."
        )
    
    @classmethod
    def create(cls, session_id: str, role: MessageRole, content: str, **kwargs) -> MessageInfo:
        """Sync version of Message.create - uses cache directly"""
        return cls._run_async(Message.create(session_id, role, content, **kwargs))
    
    @classmethod
    def list(cls, session_id: str, include_archived: bool = False) -> List[MessageInfo]:
        """Sync version - returns from cache directly"""
        messages = Message._messages_cache.get(session_id, [])
        if not include_archived:
            messages = [m for m in messages if not getattr(m, 'compacted', None)]
        return messages
    
    @classmethod
    def get(cls, session_id: str, message_id: str) -> Optional[MessageInfo]:
        """Sync version - O(1) via index"""
        idx = Message._msg_id_index.get(session_id, {}).get(message_id)
        if idx is not None:
            messages = Message._messages_cache.get(session_id, [])
            if idx < len(messages) and messages[idx].id == message_id:
                return messages[idx]
        return None
    
    @classmethod
    def parts(cls, message_id: str, session_id: Optional[str] = None) -> List[PartType]:
        """Sync version - returns from cache directly"""
        if session_id:
            parts = Message._parts_cache.get(session_id, {}).get(message_id, [])
        else:
            parts = []
            for sid in list(Message._parts_cache):
                if message_id in Message._parts_cache.get(sid, {}):
                    parts = Message._parts_cache[sid][message_id]
                    break
        return sorted(parts, key=lambda p: p.id)
    
    @classmethod
    def store_part(cls, session_id: str, message_id: str, part: PartType) -> PartType:
        """Sync version - stores in cache directly"""
        return cls._run_async(Message.store_part(session_id, message_id, part))
    
    @classmethod
    def get_with_parts(cls, session_id: str, message_id: str) -> Optional[MessageWithParts]:
        """Sync version"""
        message = cls.get(session_id, message_id)
        if not message:
            return None
        parts = cls.parts(message_id, session_id)
        return MessageWithParts(info=message, parts=parts)
    
    @classmethod
    def list_with_parts(cls, session_id: str, include_archived: bool = False) -> List[MessageWithParts]:
        """Sync version"""
        messages = cls.list(session_id, include_archived=include_archived)
        result = []
        for message in messages:
            parts = Message._parts_cache.get(session_id, {}).get(message.id, [])
            result.append(MessageWithParts(info=message, parts=parts))
        return result
    
    @classmethod
    def delete(cls, session_id: str, message_id: str) -> bool:
        """Sync version"""
        return cls._run_async(Message.delete(session_id, message_id))
    
    @classmethod
    def clear(cls, session_id: str) -> int:
        """Sync version"""
        return cls._run_async(Message.clear(session_id))
    
    @classmethod
    def get_text_content(cls, message: MessageInfo) -> str:
        """Sync version - returns from cache directly"""
        session_id = message.sessionID
        message_id = message.id
        parts = Message._parts_cache.get(session_id, {}).get(message_id, [])
        texts = []
        for part in parts:
            if part.type == "text":
                texts.append(part.text)
        return "\n".join(texts)
    
    @classmethod
    def update(cls, session_id: str, message_id: str, **updates) -> Optional[MessageInfo]:
        """Sync version"""
        return cls._run_async(Message.update(session_id, message_id, **updates))
    
    @classmethod
    def add_part(cls, session_id: str, message_id: str, part: PartType) -> Optional[MessageInfo]:
        """Sync version"""
        return cls._run_async(Message.add_part(session_id, message_id, part))
    
    @classmethod
    def update_part(cls, session_id: str, message_id: str, part_id: str, **updates) -> Optional[PartType]:
        """Sync version"""
        return cls._run_async(Message.update_part(session_id, message_id, part_id, **updates))
    
    @classmethod
    def remove_part(cls, session_id: str, message_id: str, part_id: str) -> bool:
        """Sync version"""
        return cls._run_async(Message.remove_part(session_id, message_id, part_id))
