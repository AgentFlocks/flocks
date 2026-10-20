"""
Message management for sessions

Handles message creation, storage, and retrieval.
Based on Flocks' ported src/session/message-v2.ts

Key features:
- Persistent storage using the PostgreSQL document store
- Thread-safe operations with asyncio locks
- Flocks compatible API
"""

import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import quote
from collections import OrderedDict
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Sequence, Union
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
    reported: Optional[bool] = Field(
        None,
        description=(
            "Whether the provider reported cache usage. False: read and write "
            "are unknown, not zero. None: recorded before this was tracked."
        ),
    )


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
    # Optional model-stream activity. Legacy step markers have no timing.
    time: Optional[PartTime] = None


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
    - Persistent storage using the PostgreSQL-backed Storage module
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
    _storage_revisions: Dict[str, int] = {}
    _parts_dirty: Dict[str, set[str]] = {}
    _parts_dirty_owners: Dict[str, Dict[str, Any]] = {}
    
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
    async def quiesce_parts(cls, session_id: str, *, persist: bool) -> None:
        """Stop a delayed parts flush, optionally persisting its latest cache."""
        task = cls._parts_flush_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        async with _session_locks.get(session_id):
            if persist and session_id in cls._parts_cache:
                await cls._persist_parts(session_id)

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
        cls._storage_revisions.pop(session_id, None)
        cls._parts_dirty.pop(session_id, None)
        cls._parts_dirty_owners.pop(session_id, None)
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
    def get_parts_cache_version(cls, session_id: str, message_id: str) -> tuple[int, int, int, int]:
        """Also invalidate derived prompts when a peer causes a cache reload."""
        return (
            *cls._cache_token(session_id),
            cls._storage_revisions.get(session_id, -1),
            cls.get_parts_revision(session_id, message_id),
        )

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
        from flocks.session.ownership import current_lease
        if message_id is not None:
            cls._parts_dirty.setdefault(session_id, set()).add(message_id)
            cls._parts_dirty_owners.setdefault(session_id, {})[message_id] = current_lease(session_id)
        if not cls._parts_dirty.get(session_id):
            return
        if session_id in cls._parts_flush_tasks:
            return

        async def _flush_later() -> None:
            try:
                await asyncio.sleep(cls._PARTS_PERSIST_DEBOUNCE_MS / 1000)
                async with _session_locks.get(session_id):
                    await cls._persist_parts(session_id)
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
                    from flocks.session import message_pages
                    async with Storage.transaction() as db:
                        stored_revision = await message_pages.revision(db, session_id)
                    cache_invalidated = (
                        token != cls._cache_token(session_id)
                        or not cls._has_message_cache(session_id)
                        or stored_revision != cls._storage_revisions.get(session_id)
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
        from flocks.session import message_pages

        if cls._has_message_cache(session_id):
            async with Storage.transaction() as db:
                revision = await message_pages.revision(db, session_id)
            if cls._storage_revisions.get(session_id) == revision:
                cls._touch_lru(session_id)
                return
        if session_id in cls._lru and not cls._has_message_cache(session_id):
            cls._drop_cached_session(session_id, discard_lock=False)

        lock = _session_locks.get(session_id)
        async with lock:
            if cls._has_message_cache(session_id):
                async with Storage.transaction() as db:
                    revision = await message_pages.revision(db, session_id)
                if cls._storage_revisions.get(session_id) == revision:
                    cls._touch_lru(session_id)
                    return
                # A peer may commit during a local stream. Flush only our dirty
                # deltas before discarding the old snapshot, never the full cache.
                await cls._persist_parts(session_id, dirty_only=True)
                cls._drop_cached_session(session_id, discard_lock=False)
            if session_id in cls._lru:
                cls._drop_cached_session(session_id, discard_lock=False)

            while len(cls._lru) >= cls._MAX_CACHED_SESSIONS:
                evict_id, _ = cls._lru.popitem(last=False)
                cls._drop_cached_session(evict_id)
                log.debug("message.cache.evicted", {"session_id": evict_id})

            async with Storage.transaction() as db:
                await message_pages.ensure_session(db, session_id)
                row = await db.fetch_one(
                    "SELECT state.revision, document.value FROM session_message_page_state state "
                    "LEFT JOIN runtime_state document ON document.key = ? WHERE state.session_id = ?",
                    (f"{cls._MESSAGE_PREFIX}:{session_id}", session_id),
                )
            revision, stored_data = row
            stored_data = json.loads(stored_data) if isinstance(stored_data, str) else stored_data

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
            cls._storage_revisions[session_id] = int(revision)
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
            cls._parts_serialized_cache[session_id][msg_id] = cls._serialize_message_parts(
                cls._parts_cache[session_id][msg_id]
            )
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
        cls._parts_serialized_cache.setdefault(session_id, {})[message_id] = cls._serialize_message_parts(parts)
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
                    "reported": cache_raw.get("reported"),
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
        normalized: Dict[str, Optional[int]] = {
            "start": int(start),
            "end": int(end) if end is not None else None,
        }
        # ``compacted`` is the only marker prune() leaves on a tool result (the
        # output text stays in storage), so dropping it here would send the
        # full output back to the LLM after the next cache load.  Only keep a
        # real timestamp: tool-state ``time`` is ``Dict[str, int]``.
        compacted = time_info.get("compacted")
        if isinstance(compacted, int) and not isinstance(compacted, bool) and compacted > 0:
            normalized["compacted"] = compacted
        return normalized

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
            # A modern running interval is intentionally open. Synthesizing
            # end=start on reload freezes its live process-duration clock.
            if isinstance(raw_time, dict) and isinstance(raw_time.get("start"), int) and raw_time.get("end") is None:
                normalized["time"].pop("end", None)
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

        if part_type == "subtask":
            normalized["type"] = "text"
            normalized["text"] = ""
            normalized["ignored"] = True
            normalized["metadata"] = {
                **metadata_dict,
                "legacyPartType": "subtask",
            }
            part_type = "text"
            metadata = normalized["metadata"]
            metadata_dict = metadata

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
        from flocks.session.message_pages import with_part_ids
        parts: List[PartType] = []
        for raw_part in with_part_ids(parts_data, session_id, message_id):
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
    def _record_storage_revision(cls, session_id: str, revision: int) -> None:
        # Skipped versions leave the cache stale until the next read.
        if cls._storage_revisions.get(session_id) == revision - 1:
            cls._storage_revisions[session_id] = revision

    @classmethod
    async def _persist_parts(cls, session_id: str, *, message_id: Optional[str] = None,
                             dirty_only: bool = True) -> None:
        """Flush pending deltas; clean history is never rewritten."""
        pending = ([message_id] if message_id is not None
                   else list(cls._parts_dirty.get(session_id, set())))
        for mid in pending:
            from flocks.session.ownership import SessionOwnershipLost
            try:
                await cls._persist_indexed_state(session_id, mid, include_parts=True)
            except SessionOwnershipLost:
                # Never replay a dead runner's queued deltas from another
                # request context. A fresh reader reloads the committed state.
                cls._parts_dirty.get(session_id, set()).discard(mid)
                cls._parts_dirty_owners.get(session_id, {}).pop(mid, None)

    @classmethod
    def _restore_parts(cls, session_id: str, message_id: str, before: List[PartType]) -> None:
        """Put a message's cached parts back as they were before a change whose write failed.

        Left in the cache, the change would ride along with the next write or
        flush of this message after its caller was told it failed; deltas the
        message had pending from before stay pending.
        """
        parts = cls._parts_cache.get(session_id, {}).get(message_id)
        if parts is not None:
            parts[:] = before
            cls._touch_parts_revision(session_id, message_id)

    @classmethod
    def _forget_refused_message(cls, session_id: str, message_id: str) -> None:
        """Drop a message the database refused from this process's cache."""
        messages = cls._messages_cache.get(session_id)
        if messages is not None:
            for index, cached in enumerate(messages):
                if cached.id == message_id:
                    del messages[index]
                    break
            cls._rebuild_id_index(session_id)
        cls._parts_cache.get(session_id, {}).pop(message_id, None)

    @staticmethod
    async def _require_claimed_agent(db: Any, session_id: str, message: MessageInfo) -> None:
        """Refuse another agent's new user message in a dedicated session.

        The caller holds the session's message lock, which the claim
        transaction takes first too, so claiming a session and writing its
        first message never interleave, even across processes. That relies on
        READ COMMITTED reading the claim committed while this transaction
        waited; the share lock makes a stricter isolation level fail instead
        of reading a stale snapshot.
        """
        from flocks.session.session import (
            DEDICATED_AGENT_POLICY_METADATA_KEY,
            SessionAgentMismatchError,
        )

        row = await db.fetch_one(
            "SELECT value -> 'metadata' -> ?::text FROM runtime_state "
            "WHERE key LIKE 'session:%' AND value ->> 'id' = ? LIMIT 1 FOR SHARE",
            (DEDICATED_AGENT_POLICY_METADATA_KEY, session_id),
        )
        policy = row[0] if row else None
        policy = json.loads(policy) if isinstance(policy, str) else policy
        if (
            isinstance(policy, dict)
            and policy.get("version") == 1
            and policy.get("agent") != getattr(message, "agent", None)
        ):
            raise SessionAgentMismatchError(
                f"Session {session_id} is dedicated to agent {policy.get('agent')!r}"
            )

    @classmethod
    async def _persist_indexed_state(
        cls, session_id: str, message_id: str, *, include_messages: bool = False,
        include_parts: bool = False, delete_message: bool = False,
        metadata_patch: Optional[Dict[str, Any]] = None,
    ) -> Optional[MessageInfo]:
        """Merge one message into the latest PostgreSQL state and FTS atomically."""
        from flocks.session import message_pages
        from flocks.session.session import Session
        from flocks.storage.session_search import delete_message_document, upsert_session_document
        messages = cls._messages_cache.get(session_id, [])
        index = cls._msg_id_index.get(session_id, {}).get(message_id)
        message = messages[index] if index is not None and index < len(messages) else None
        if message is not None:
            message = cls._normalize_assistant_message(message)
        parts = list(cls._parts_cache.get(session_id, {}).get(message_id, []))
        previous_parts = cls._parts_serialized_cache.get(session_id, {}).get(message_id, [])
        serialized_parts = cls._serialize_message_parts(parts)
        legacy = cls._parts_storage_format.get(session_id) == "legacy"
        session = await Session.get_by_id_unfiltered(session_id)
        try:
            if include_parts and _named_uploads(serialized_parts):
                from flocks.storage.file_mirror import FileMirror

                # The tombstone table, created outside the transaction below
                # (a failure here is handled like one in it: nothing is kept).
                await FileMirror._pool()
            async with Storage.transaction() as db:
                await message_pages.lock_session(db, session_id)
                from flocks.session.ownership import require_current_ownership
                await require_current_ownership(db, session_id)
                pending_owner = cls._parts_dirty_owners.get(session_id, {}).get(message_id)
                if include_parts and pending_owner is not None:
                    await pending_owner.verify(db)
                current = await message_pages.read_info(db, session_id, message_id)
                if delete_message:
                    await message_pages.remove(db, session_id, message_id)
                    message = None
                elif include_messages and message is not None:
                    if metadata_patch is not None:
                        if current is None:
                            return None
                        patch = dict(metadata_patch)
                        if isinstance(patch.get("time"), dict):
                            patch["time"] = {**current.get("time", {}), **patch["time"]}
                        current = {**current, **patch}
                        model = UserMessageInfo if current.get("role") == "user" else AssistantMessageInfo
                        message = model.model_validate(cls._normalize_stored_message(current, session_id))
                    elif current is None and message.role == MessageRole.USER:
                        await cls._require_claimed_agent(db, session_id, message)
                    await message_pages.upsert(db, session_id, message)
                elif current is not None:
                    model = UserMessageInfo if current.get("role") == "user" else AssistantMessageInfo
                    message = model.model_validate(cls._normalize_stored_message(current, session_id))
                else:
                    cls._parts_dirty.get(session_id, set()).discard(message_id)
                    return None
                if include_messages:
                    await message_pages.sync_document(db, session_id)
                if include_parts:
                    # An upload the merged parts newly name -- against what is
                    # stored, not this worker's cache -- may be on its way out
                    # (UploadSweep): the same lock decides, and a removed one is refused.
                    serialized_parts = await message_pages.merge_parts(
                        db, session_id, message_id, previous=previous_parts,
                        incoming=serialized_parts, legacy=legacy, delete=delete_message,
                        guard=guard_new_upload_references,
                    )
                    parts = cls._deserialize_parts_list(
                        session_id, message_id, serialized_parts,
                        message_time=message.time if message is not None else None,
                    )
                else:
                    row = await db.fetch_one(
                        "SELECT COALESCE(item.value, legacy.value -> ?) FROM (SELECT 1) seed "
                        "LEFT JOIN runtime_state item ON item.key = ? "
                        "LEFT JOIN runtime_state legacy ON legacy.key = ?",
                        (message_id, cls._parts_item_key(session_id, message_id), cls._parts_blob_key(session_id)),
                    )
                    stored = row[0] if row and row[0] is not None else []
                    stored = json.loads(stored) if isinstance(stored, str) else stored
                    parts = cls._deserialize_parts_list(
                        session_id, message_id, stored,
                        message_time=message.time if message is not None else None,
                    )
                if Storage.session_search_available():
                    if delete_message or message is None:
                        await delete_message_document(db, message_id)
                    elif session is not None:
                        await upsert_session_document(db, project_id=session.project_id, message=message, parts=parts)
                revision = await message_pages.bump_revision(db, session_id)
        except BaseException:
            # Nothing was written: the next read or write sees an unknown
            # revision and reloads.  The change that failed is taken back out
            # of the cache by the caller that made it (_restore_parts).
            cls._storage_revisions.pop(session_id, None)
            raise
        if message is not None and index is not None and index < len(messages):
            messages[index] = message
            if include_messages:
                order = lambda item: (int(item.time.get("created", 0)), item.id)
                if ((index > 0 and order(messages[index - 1]) > order(message))
                        or (index + 1 < len(messages) and order(message) > order(messages[index + 1]))):
                    messages.sort(key=order)
                    cls._rebuild_id_index(session_id)
        if include_parts:
            if not delete_message:
                cls._parts_cache.setdefault(session_id, {})[message_id] = parts
                cls._parts_serialized_cache.setdefault(session_id, {})[message_id] = cls._serialize_message_parts(parts)
                cls._parts_persisted_mids.setdefault(session_id, set()).add(message_id)
            else:
                cls._parts_persisted_mids.setdefault(session_id, set()).discard(message_id)
            cls._parts_dirty.get(session_id, set()).discard(message_id)
            cls._parts_dirty_owners.get(session_id, {}).pop(message_id, None)
        cls._record_storage_revision(session_id, revision)
        return message

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
            _ignored = kwargs.pop("ignored", None)
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
                # Refuse before the cache sees it when the session is already
                # dedicated to another agent; the check under the message lock
                # in _persist_indexed_state still has the final word.
                async with Storage.transaction() as db:
                    await cls._require_claimed_agent(db, session_id, message)
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
            if _ignored is not None:
                _part_extras["ignored"] = _ignored
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
            from flocks.session.session import SessionAgentMismatchError

            try:
                await cls._persist_indexed_state(
                    session_id,
                    message.id,
                    include_messages=True,
                    include_parts=True,
                )
            except SessionAgentMismatchError:
                cls._forget_refused_message(session_id, message.id)
                raise
            
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
        """Use the live cache if loaded, otherwise read one indexed row."""
        if session_id in cls._messages_cache:
            await cls._ensure_message_cache(session_id)
        if session_id not in cls._messages_cache:
            from flocks.session import message_pages

            async with Storage.transaction() as db:
                await message_pages.ensure_session(db, session_id)
                row = await db.fetch_one(
                    "SELECT info FROM session_message_pages WHERE session_id = ? AND message_id = ?",
                    (session_id, message_id),
                )
            if row is None:
                return None
            raw = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            info = cls._normalize_stored_message(raw, session_id)
            model = UserMessageInfo if info.get("role") == "user" else AssistantMessageInfo
            return model.model_validate(info)
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
    async def parts_many(cls, session_id: str, message_ids: List[str]) -> Dict[str, List[PartType]]:
        """Read one coherent cache snapshot for prompt construction."""
        await cls._ensure_cache(session_id)
        cached = cls._parts_cache.get(session_id, {})
        return {message_id: list(cached.get(message_id, [])) for message_id in message_ids}
    
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
            before = list(parts_list)
            
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
                cls._schedule_parts_flush(session_id, message_id=message_id)
            else:
                try:
                    await cls._persist_indexed_state(
                        session_id,
                        message_id,
                        include_parts=True,
                    )
                except BaseException:
                    cls._restore_parts(session_id, message_id, before)
                    raise
            
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

            await cls._persist_indexed_state(
                session_id,
                message_info.id,
                include_messages=True,
            )

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
        messages = cls._messages_cache.get(session_id, [])
        if not include_archived:
            messages = [m for m in messages if not getattr(m, "compacted", None)]
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
        result, has_more, next_before, _, _ = await cls.list_page_with_parts(
            session_id, limit=limit, before=before, include_archived=include_archived,
        )
        return result, has_more, next_before

    @classmethod
    async def list_page_with_parts(
        cls, session_id: str, *, limit: int = 50, before: Optional[str] = None,
        after: Optional[str] = None, around: Optional[str] = None, include_archived: bool = True,
    ) -> tuple[List[MessageWithParts], bool, Optional[str], bool, Optional[str]]:
        """Read an indexed page without hydrating the whole session cache."""
        from flocks.session import message_pages

        async with Storage.transaction() as db:
            rows, older, before_id, newer, after_id = await message_pages.read_page(
                db, session_id, limit=limit, before=before, after=after,
                around=around, include_archived=include_archived,
            )
        # Pending/running tool parts are deliberately written after a short
        # debounce. Keep the live writer's view for selected messages during
        # that window; a clean cache must never override newer PG documents.
        flush_task = cls._parts_flush_tasks.get(session_id)
        live_parts = (
            cls._parts_cache.get(session_id, {})
            if flush_task is not None and not flush_task.done()
            else {}
        )
        result = []
        for row in rows:
            info = cls._normalize_stored_message(row["info"], session_id)
            model = UserMessageInfo if info.get("role") == "user" else AssistantMessageInfo
            message = model.model_validate(info)
            cached_parts = live_parts.get(message.id)
            parts = (
                list(cached_parts) if cached_parts is not None
                else cls._deserialize_parts_list(
                    session_id, message.id, row["parts"],
                    message_time=message.time,
                )
            )
            result.append(MessageWithParts(info=message, parts=parts))
        return result, older, before_id, newer, after_id

    @classmethod
    async def get_with_parts_lazy(cls, session_id: str, message_id: str) -> Optional[MessageWithParts]:
        """Get one message with parts without hydrating all session parts."""
        if session_id not in cls._messages_cache and session_id not in cls._parts_cache:
            try:
                rows, *_ = await cls.list_page_with_parts(session_id, limit=1, around=message_id)
            except LookupError:
                return None
            return rows[0] if rows else None
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
                    await cls._persist_indexed_state(
                        session_id,
                        message_id,
                        include_messages=True,
                        include_parts=True,
                        delete_message=True,
                    )
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
                        cls._schedule_parts_flush(session_id)
                    raise

                cls._schedule_parts_flush(session_id)
                log.info("message.deleted", {"id": message_id, "session_id": session_id})
                return True
            return False

    @classmethod
    async def delete_many(cls, session_id: str, message_ids: Sequence[str]) -> int:
        """Delete several messages of one session in a single storage transaction.

        Storage-authoritative: the kept list is computed from what is *stored*,
        not from the in-memory cache, so a message that reached storage but not
        this process's cache (e.g. another worker) is never dropped. Returns how
        many messages were deleted.
        """
        targets = {message_id for message_id in message_ids if message_id}
        if not targets:
            return 0
        async with _session_locks.get(session_id):
            return await cls._delete_ids_locked(session_id, lambda stored: targets)

    @classmethod
    async def delete_selected(
        cls,
        session_id: str,
        selector: "Callable[[List[Dict[str, Any]], Optional[Any]], Iterable[str]]",
        *,
        is_busy: "Optional[Callable[[Any], bool]]" = None,
        sweep_uploads: bool = True,
    ) -> int:
        """Delete the messages ``selector`` picks, decided under the session lock.

        ``selector(stored_messages, session)`` runs inside the lock on the
        freshly-read stored message list and the live session, so a retention
        decision (which turns are expired, the current revert point) can't be
        stale by the time it deletes. Returns the count, or -1 if the session is
        busy (left alone).

        ``is_busy(session)`` is the authoritative "leave it alone" check (a
        running loop, or a non-idle status such as compacting/dreaming that has
        no live loop). Without it, only a running loop is treated as busy.

        The uploads the deleted messages brought go once nothing names them any
        more (UploadSweep): they are recorded as candidates with the delete,
        and swept here -- or, ``sweep_uploads=False``, by the caller once it
        deleted from all its sessions.
        """
        from flocks.session.session import Session
        from flocks.session.session_loop import SessionLoop

        # Hold the session lifecycle lock so a revert being set (Session.update)
        # can't slip in between reading it and deleting. message.py never
        # acquires this lock elsewhere, and the lifecycle lock is always taken
        # before the message lock, so this order cannot deadlock.
        async with Session.lifecycle_lock(session_id):
            async with _session_locks.get(session_id):
                session = await Session.get_by_id_unfiltered(session_id)
                if is_busy is not None:
                    if session is not None and is_busy(session):
                        return -1
                elif SessionLoop.is_running(session_id):
                    return -1
                return await cls._delete_ids_locked(
                    session_id, lambda stored: selector(stored, session), sweep_uploads=sweep_uploads,
                )

    @classmethod
    async def _delete_ids_locked(
        cls,
        session_id: str,
        choose: "Callable[[List[Dict[str, Any]]], Iterable[str]]",
        *,
        sweep_uploads: bool = True,
    ) -> int:
        """Delete the messages ``choose`` picks from the committed history. Lock held by caller.

        ``choose`` sees the session's messages as the PostgreSQL message
        directory holds them, read under ``lock_session`` in the transaction
        that deletes, so a message another worker committed is never dropped
        and the choice cannot go stale before it is applied. The directory, the
        export document rebuilt from it, parts, turn diffs and search rows go
        together, and the revision bump makes every worker's cache reload.
        """
        from flocks.session import message_pages
        from flocks.session.ownership import require_current_ownership
        from flocks.storage.session_search import delete_message_documents

        removed_ids: List[str] = []
        removed_uploads: List[str] = []
        try:
            async with Storage.transaction() as db:
                await message_pages.lock_session(db, session_id)
                await require_current_ownership(db, session_id)
                rows = await db.fetch_all(
                    "SELECT info FROM session_message_pages WHERE session_id = ? "
                    "ORDER BY created_at, message_id",
                    (session_id,),
                )
                stored = [json.loads(row[0]) if isinstance(row[0], str) else row[0] for row in rows]
                targets = {message_id for message_id in choose(stored) if message_id} if stored else set()
                removed_ids = [item["id"] for item in stored if item.get("id") in targets]
                if not removed_ids:
                    return 0
                # The uploads those messages carried go with them, once nothing
                # names them any more: kept, they would outlive the retention
                # that removed the messages, on disk and in the shared store.
                # Recorded with the delete, so none is lost to a later failure.
                removed_uploads = await cls._uploads_of(db, session_id, removed_ids)
                if removed_uploads:
                    await register_upload_candidates(db, session_id, removed_uploads)
                await db.execute(
                    "DELETE FROM session_message_pages WHERE session_id = ? AND message_id = ANY(?::text[])",
                    (session_id, removed_ids),
                )
                await message_pages.sync_document(db, session_id)
                # Parts live per message, or in a legacy blob keyed by message id.
                await db.execute(
                    "DELETE FROM runtime_state WHERE key = ANY(?::text[])",
                    ([cls._parts_item_key(session_id, message_id) for message_id in removed_ids]
                     + [f"message_diff:{session_id}:{message_id}" for message_id in removed_ids],),
                )
                await db.execute(
                    "UPDATE runtime_state SET value = value - ?::text[], updated_at = clock_timestamp() "
                    "WHERE key = ? AND jsonb_typeof(value) = 'object'",
                    (removed_ids, cls._parts_blob_key(session_id)),
                )
                if Storage.session_search_available():
                    await delete_message_documents(db, session_id, removed_ids)
                await message_pages.bump_revision(db, session_id)
        except message_pages.SessionMessagesDeletedError:
            return 0  # the session itself was deleted meanwhile
        finally:
            if removed_ids:
                # Reload from storage on next access, whether or not the delete
                # committed; a surgical prune could disagree with storage.
                # (Retention only deletes from idle sessions, which have no
                # pending part flush.)
                cls._drop_cached_session(session_id, discard_lock=False)

        if removed_uploads and sweep_uploads:
            await UploadSweep(sessions=[session_id]).run()
        log.info("message.deleted_many", {"session_id": session_id, "count": len(removed_ids)})
        return len(removed_ids)

    @classmethod
    async def _uploads_of(cls, db: Any, session_id: str, message_ids: List[str]) -> List[str]:
        """The session's own uploads these messages' file parts point at (stored paths)."""
        from flocks.session.files import session_upload_part_path, uploads_mirror

        rows = await db.fetch_all(
            "SELECT value FROM runtime_state WHERE key = ANY(?::text[])",
            ([cls._parts_item_key(session_id, message_id) for message_id in message_ids],),
        )
        # A legacy session keeps every message's parts in one blob: only these messages' are read.
        rows += await db.fetch_all(
            "SELECT value -> wanted.id FROM runtime_state, unnest(?::text[]) AS wanted(id) "
            "WHERE key = ? AND jsonb_typeof(value) = 'object' AND value -> wanted.id IS NOT NULL",
            (message_ids, cls._parts_blob_key(session_id)),
        )
        uploads: List[str] = []
        mirror = uploads_mirror()
        for row in rows:
            parts = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            for part in parts if isinstance(parts, list) else []:
                if not isinstance(part, dict) or part.get("type") != "file":
                    continue
                path = session_upload_part_path(session_id, SimpleNamespace(url=part.get("url")))
                if path is None:
                    continue
                try:
                    uploads.append(mirror.relative(path))
                except ValueError:
                    continue
        return list(dict.fromkeys(uploads))

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
                    await cls._persist_indexed_state(
                        session_id, message_id, include_messages=True,
                        metadata_patch={"compacted": True},
                    )

                    log.info("message.archived", {
                        "id": message_id,
                        "session_id": session_id,
                    })
                    return True
            return False

    @classmethod
    async def clear(cls, session_id: str) -> int:
        """Clear the latest committed history under the shared session lock."""
        from flocks.session import message_pages
        from flocks.session.ownership import require_current_ownership
        from flocks.runtime_state.postgres import _like_prefix
        from flocks.storage.session_search import delete_session_documents

        async with _session_locks.get(session_id):
            async with Storage.transaction() as db:
                await message_pages.lock_session(db, session_id)
                await require_current_ownership(db, session_id)
                row = await db.fetch_one(
                    "SELECT count(*) FROM session_message_pages WHERE session_id = ?", (session_id,),
                )
                count = int(row[0])
                await message_pages.remove(db, session_id)
                await message_pages.sync_document(db, session_id)
                await db.execute(
                    "DELETE FROM runtime_state WHERE key = ? OR key LIKE ?",
                    (cls._parts_blob_key(session_id), _like_prefix(cls._parts_item_prefix(session_id))),
                )
                if Storage.session_search_available():
                    await delete_session_documents(db, [session_id])
                revision = await message_pages.bump_revision(db, session_id)
            cls._drop_cached_session(session_id, discard_lock=False)
            cls._messages_cache[session_id] = []
            cls._msg_id_index[session_id] = {}
            cls._parts_cache[session_id] = {}
            cls._parts_revision_cache[session_id] = {}
            cls._parts_serialized_cache[session_id] = {}
            cls._parts_persisted_mids[session_id] = set()
            cls._parts_storage_format[session_id] = "per_message"
            cls._parts_fully_loaded.add(session_id)
            cls._storage_revisions[session_id] = revision
            cls._touch_lru(session_id)
            log.info("messages.cleared", {"session_id": session_id, "count": count})
            return count

    @classmethod
    async def clear_active(
        cls,
        session_id: str,
        *,
        expected_generation: Optional[int] = None,
    ) -> int:
        """Clear history only while the owning session remains active."""

        from flocks.session.session import Session

        return await Session.run_active_write(
            session_id,
            lambda: cls.clear(session_id),
            expected_generation=expected_generation,
        )
    
    @classmethod
    def invalidate_cache(
        cls,
        session_id: Optional[str] = None,
        *,
        discard_lock: bool = True,
    ) -> None:
        """
        Invalidate cache for a session or all sessions
        
        Args:
            session_id: Optional session ID, if None invalidates all
            discard_lock: Also forget the session's lock (per-session only).
                Pass ``False`` when a caller may still be holding it, so a
                concurrent writer cannot obtain a second lock for the session.
        """
        if session_id:
            cls._drop_cached_session(session_id, discard_lock=discard_lock)
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
            cls._storage_revisions.clear()
            cls._parts_dirty.clear()
            cls._parts_dirty_owners.clear()
            _session_locks.clear()
        log.debug("message.cache.invalidated", {"session_id": session_id})
    
    @classmethod
    async def get_text_content(cls, message: MessageInfo, *, parts: Optional[List[PartType]] = None) -> str:
        """
        Extract text content from message
        
        Args:
            message: Message info
            
        Returns:
            Combined text content
        """
        # Get parts from cache
        if parts is None:
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
        by_session: Dict[str, List[str]] = {}
        for message in messages:
            by_session.setdefault(message.sessionID, []).append(message.id)
        snapshots = {}
        for sid, ids in by_session.items():
            snapshots[sid] = await cls.parts_many(sid, ids)

        for message in messages:
            # Simple text format for now
            content = await cls.get_text_content(message, parts=snapshots[message.sessionID][message.id])
            
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
                requested_time = patch.get("time")
                patch["time"] = {
                    **(requested_time if isinstance(requested_time, dict) else {}),
                    "updated": int(datetime.now().timestamp() * 1000),
                }
            
            messages[msg_index] = message.model_copy(update=patch)
            
            updated = await cls._persist_indexed_state(
                session_id,
                message_id,
                include_messages=True,
                metadata_patch=patch,
            )
            
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
            
            before = list(cls._parts_cache[session_id][message_id])
            cls._parts_cache[session_id][message_id].append(part)
            cls._touch_parts_revision(session_id, message_id)
            
            cls._cancel_parts_flush_task(session_id)
            try:
                await cls._persist_indexed_state(
                    session_id,
                    message_id,
                    include_parts=True,
                )
            except BaseException:
                cls._restore_parts(session_id, message_id, before)
                raise
            
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
                    before = list(parts)
                    parts[i] = updated_part
                    cls._touch_parts_revision(session_id, message_id)
                    
                    cls._cancel_parts_flush_task(session_id)
                    try:
                        await cls._persist_indexed_state(
                            session_id,
                            message_id,
                            include_parts=True,
                        )
                    except BaseException:
                        cls._restore_parts(session_id, message_id, before)
                        raise
                    
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
                    before = list(parts)
                    parts.pop(i)
                    cls._touch_parts_revision(session_id, message_id)
                    
                    cls._cancel_parts_flush_task(session_id)
                    try:
                        await cls._persist_indexed_state(
                            session_id,
                            message_id,
                            include_parts=True,
                        )
                    except BaseException:
                        cls._restore_parts(session_id, message_id, before)
                        raise
                    
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
        return cls._run_async(Message.clear_active(session_id))
    
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


_CANDIDATE_PREFIX = "upload_candidate:"
#: Advisory lock per upload: every transaction that newly names an upload and
#: every removal of one take it, so they never pass each other.
_UPLOAD_LOCK = "flocks.upload:"

# Which candidate uploads a message part anywhere still names: one pass over
# every session's parts. Each row is cast to text once (OFFSET 0 keeps the
# cast from being repeated per comparison); only rows that mention one of the
# sessions are kept for the per-file comparison.
_STILL_NAMED = (
    "WITH mentions AS MATERIALIZED ("
    " SELECT body FROM runtime_state, LATERAL (SELECT value::text AS body OFFSET 0) AS cast_once"
    " WHERE key LIKE ? ESCAPE '\\' AND EXISTS ("
    "  SELECT 1 FROM unnest(?::text[]) AS mentioned(fragment) WHERE strpos(body, mentioned.fragment) > 0))"
    " SELECT wanted.token FROM unnest(?::text[]) AS wanted(token)"
    " WHERE EXISTS (SELECT 1 FROM mentions WHERE strpos(mentions.body, wanted.token) > 0)"
)


class UploadRemoved(ValueError):
    """A message names an upload that was removed (its stored copy is a tombstone)."""


def _like_prefix(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _tokens(relative: str) -> set:
    # The URL holds the path as written, or percent-encoded (``Path.as_uri``).
    return {f"/{relative}", f"/{quote(relative)}"}


def _named_uploads(parts: Any) -> set:
    """The session uploads (stored paths) file parts point at, whichever session they belong to."""
    from flocks.session.files import upload_relative_of

    found = set()
    for part in parts or ():
        if isinstance(part, dict) and part.get("type") == "file":
            relative = upload_relative_of(part.get("url"))
            if relative:
                found.add(relative)
    return found


async def _lock_uploads(db: Any, relatives: Iterable[str]) -> None:
    keys = [_UPLOAD_LOCK + relative for relative in sorted(set(relatives))]
    if keys:
        # One statement for all of them (a sweep may hold many), taken in the
        # array's sorted order: one order everywhere, no deadlock between two lockers.
        await db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(item.key, 0)) "
            "FROM unnest(?::text[]) WITH ORDINALITY AS item(key, position) ORDER BY item.position",
            (keys,),
        )


async def guard_new_upload_references(db: Any, stored: Any, merged: Any) -> None:
    """In the transaction that stores parts: an upload they newly name must still be there.

    "Newly" against the parts stored now (read in this transaction), so a
    worker whose cache is behind -- another one removed the part meanwhile --
    cannot write it back past the check.  Its lock is the one a removal takes
    (UploadSweep): either this write commits first and the removal sees the
    new reference, or the removal commits first and this write finds the
    tombstone and is refused.
    """
    new = _named_uploads(merged) - _named_uploads(stored)
    if not new:
        return
    row = await db.fetch_one("SELECT to_regclass('file_mirror') IS NOT NULL", ())
    if not (row and row[0]):
        return  # nothing was ever stored, so nothing was ever removed
    await _lock_uploads(db, new)
    rows = await db.fetch_all(
        "SELECT path FROM file_mirror WHERE area = 'uploads' AND path = ANY(?::text[]) AND deleted",
        (sorted(new),),
    )
    if rows:
        raise UploadRemoved(f"the attachment {rows[0][0]} was removed; upload it again")


async def register_upload_candidates(db: Any, session_id: str, relatives: Sequence[str]) -> None:
    """In the caller's transaction: these uploads of *session_id* lost a message that named them."""
    await db.execute(
        "INSERT INTO runtime_state (key, value, value_type, updated_at) "
        "SELECT ? || relative, jsonb_build_object('sessionID', ?::text, 'since', ?::bigint), 'json', now() "
        "FROM unnest(?::text[]) AS relative ON CONFLICT (key) DO NOTHING",
        (_CANDIDATE_PREFIX, session_id, int(time.time() * 1000), list(dict.fromkeys(relatives))),
    )


class UploadSweep:
    """Candidate uploads go once no message anywhere names them.

    A retry sends the same upload again in a later message, and a fork or a
    copy imported before they had uploads of their own still points at the
    original's file -- from any session, related to it or not.  So whether a
    candidate is still named is looked up across every session's message
    parts, in one pass for all of them.  The ones that are not are removed in
    one transaction that first takes their upload locks (the ones a write that
    newly names an upload takes, guard_new_upload_references), then looks
    again, then gives them their tombstone; their local files go after it.  A
    write that waited for those locks finds the tombstone and is refused; one
    that got there first is seen by the second look.  A candidate still named
    stays one, for a later sweep.  Anything that names the file -- a part's
    URL or a tool's output -- keeps it.
    """

    def __init__(self, sessions: Optional[Iterable[str]] = None) -> None:
        self._sessions = None if sessions is None else sorted(set(sessions))

    async def run(self) -> int:
        """Remove the candidates nothing names any more; returns how many."""
        candidates = await self._candidates()
        if not candidates:
            return 0
        named = await self._still_named(candidates)
        unnamed = {relative: session_id for relative, session_id in candidates.items() if relative not in named}
        if not unnamed:
            return 0
        return await self._remove(unnamed)

    async def _candidates(self) -> Dict[str, str]:
        query = (
            "SELECT key, value ->> 'sessionID' FROM runtime_state WHERE key LIKE ? ESCAPE '\\'"
        )
        params: tuple = (_like_prefix(_CANDIDATE_PREFIX) + "%",)
        if self._sessions is not None:
            query += " AND value ->> 'sessionID' = ANY(?::text[])"
            params += (self._sessions,)
        async with Storage.transaction() as db:
            rows = await db.fetch_all(query, params)
        return {row[0][len(_CANDIDATE_PREFIX):]: row[1] for row in rows if row[1]}

    @staticmethod
    async def _named_in(db: Any, candidates: Dict[str, str]) -> set:
        tokens = {relative: _tokens(relative) for relative in candidates}
        wanted = sorted({token for forms in tokens.values() for token in forms})
        fragments = sorted({f"/{session_id}/" for session_id in candidates.values()})
        rows = await db.fetch_all(
            _STILL_NAMED, (_like_prefix(Message._PARTS_PREFIX) + ":%", fragments, wanted),
        )
        named_tokens = {row[0] for row in rows}
        return {relative for relative, forms in tokens.items() if forms & named_tokens}

    async def _still_named(self, candidates: Dict[str, str]) -> set:
        async with Storage.transaction() as db:
            return await self._named_in(db, candidates)

    async def _remove(self, unnamed: Dict[str, str]) -> int:
        from flocks.session.files import uploads_mirror
        from flocks.storage.file_mirror import FileMirror

        await FileMirror._pool()  # the table exists before it is written
        async with Storage.transaction() as db:
            await _lock_uploads(db, unnamed)
            # Looked at again under the locks: a write that named one meanwhile is seen here.
            named_now = await self._named_in(db, unnamed)
            gone = sorted(relative for relative in unnamed if relative not in named_now)
            if gone:
                # A tombstone also for a file that never got a stored copy (one
                # over the size kept), so a later write naming it is refused.
                await db.execute(
                    "INSERT INTO file_mirror (area, path, content, sha256, size, deleted, updated_at) "
                    "SELECT 'uploads', path, ''::bytea, '', 0, TRUE, clock_timestamp() FROM unnest(?::text[]) AS path "
                    "ON CONFLICT (area, path) DO UPDATE SET deleted = TRUE, content = ''::bytea, sha256 = '', "
                    "size = 0, updated_at = clock_timestamp()",
                    (gone,),
                )
                await db.execute(
                    "DELETE FROM runtime_state WHERE key = ANY(?::text[])",
                    ([_CANDIDATE_PREFIX + relative for relative in gone],),
                )
        mirror = uploads_mirror()
        for relative in gone:
            try:
                local = mirror.local(relative)
                if not mirror.is_linked(relative) and local.is_file():
                    await asyncio.to_thread(local.unlink)
            except Exception as exc:
                # Its stored copy is gone and no write can name it again; a
                # file left behind here is reported, not a reason to fail.
                log.warning("message.upload_forget_failed", {
                    "session_id": unnamed.get(relative), "error_type": type(exc).__name__,
                })
        return len(gone)
