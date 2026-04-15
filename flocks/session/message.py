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

log = Log.create(service="message")


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
    _parts_serialized_cache: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    _parts_flush_tasks: Dict[str, asyncio.Task] = {}
    _lru: OrderedDict[str, bool] = OrderedDict()  # LRU tracker: move_to_end() is O(1)
    
    # Maximum number of sessions to keep in cache before evicting oldest
    _MAX_CACHED_SESSIONS = 50
    
    # Storage key prefixes
    _MESSAGE_PREFIX = "message"
    _PARTS_PREFIX = "message_parts"
    _PARTS_PERSIST_DEBOUNCE_MS = 75

    @classmethod
    def _cancel_parts_flush_task(cls, session_id: str) -> None:
        task = cls._parts_flush_tasks.pop(session_id, None)
        if task and not task.done():
            task.cancel()

    @classmethod
    def _serialize_message_parts(cls, parts: List[PartType]) -> List[Dict[str, Any]]:
        return [p.model_dump() for p in parts]

    @classmethod
    def _sync_serialized_parts_for_message(cls, session_id: str, message_id: str) -> Dict[str, List[Dict[str, Any]]]:
        serialized = cls._parts_serialized_cache.setdefault(session_id, {})
        serialized[message_id] = cls._serialize_message_parts(
            cls._parts_cache.get(session_id, {}).get(message_id, [])
        )
        return serialized

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
        """Ensure messages for a session are loaded into cache from storage.

        Uses a per-session lock so operations on different sessions are
        fully concurrent.
        """
        if session_id in cls._lru:
            cls._lru.move_to_end(session_id)
            return
        
        lock = _session_locks.get(session_id)
        async with lock:
            if session_id in cls._lru:
                cls._lru.move_to_end(session_id)
                return
            
            while len(cls._lru) >= cls._MAX_CACHED_SESSIONS:
                evict_id, _ = cls._lru.popitem(last=False)
                cls._cancel_parts_flush_task(evict_id)
                cls._messages_cache.pop(evict_id, None)
                cls._msg_id_index.pop(evict_id, None)
                cls._parts_cache.pop(evict_id, None)
                cls._parts_serialized_cache.pop(evict_id, None)
                _session_locks.discard(evict_id)
                log.debug("message.cache.evicted", {"session_id": evict_id})
            
            storage_key = f"{cls._MESSAGE_PREFIX}:{session_id}"
            stored_data = await Storage.get(storage_key)
            
            if stored_data:
                messages = []
                for msg_data in stored_data:
                    role = msg_data.get('role', 'assistant')
                    if role == 'user':
                        messages.append(UserMessageInfo.model_validate(msg_data))
                    else:
                        messages.append(AssistantMessageInfo.model_validate(msg_data))
                cls._messages_cache[session_id] = messages
            else:
                cls._messages_cache[session_id] = []
            
            parts_key = f"{cls._PARTS_PREFIX}:{session_id}"
            stored_parts = await Storage.get(parts_key)
            
            if stored_parts:
                cls._parts_cache[session_id] = {}
                for msg_id, parts_data in stored_parts.items():
                    cls._parts_cache[session_id][msg_id] = [
                        cls.deserialize_part(p) for p in parts_data
                    ]
                cls._parts_serialized_cache[session_id] = {
                    msg_id: list(parts_data)
                    for msg_id, parts_data in stored_parts.items()
                }
            else:
                cls._parts_cache[session_id] = {}
                cls._parts_serialized_cache[session_id] = {}
            
            cls._rebuild_id_index(session_id)
            cls._lru[session_id] = True
            log.debug("message.cache.loaded", {"session_id": session_id})
    
    @classmethod
    def _rebuild_id_index(cls, session_id: str) -> None:
        """Rebuild the message ID → list-index map for a session."""
        messages = cls._messages_cache.get(session_id, [])
        cls._msg_id_index[session_id] = {m.id: i for i, m in enumerate(messages)}

    @classmethod
    def deserialize_part(cls, part_data: Dict[str, Any]) -> PartType:
        """Deserialize a part from storage format"""
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
        if isinstance(message.tokens, dict):
            updates["tokens"] = TokenUsage.model_validate(message.tokens)
        if isinstance(message.path, dict):
            updates["path"] = MessagePath.model_validate(message.path)

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

        When *message_id* is given, only the parts for that single message are
        serialised and merged into the stored blob.  This avoids re-serialising
        every message on every tool-call update (the hot path).

        Falls back to a full write when *message_id* is ``None``.
        """
        parts_key = f"{cls._PARTS_PREFIX}:{session_id}"
        all_parts = cls._parts_cache.get(session_id, {})
        serialized = cls._parts_serialized_cache.setdefault(session_id, {})

        if message_id is not None:
            serialized[message_id] = cls._serialize_message_parts(all_parts.get(message_id, []))
            await Storage.set(parts_key, serialized)
        else:
            serialized = {
                mid: cls._serialize_message_parts(mparts)
                for mid, mparts in all_parts.items()
            }
            cls._parts_serialized_cache[session_id] = serialized
            await Storage.set(parts_key, serialized)
    
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
        await cls._ensure_cache(session_id)
        messages = cls._messages_cache.get(session_id, [])
        if not include_archived:
            messages = [m for m in messages if not getattr(m, 'compacted', None)]
        return messages
    
    @classmethod
    async def get(cls, session_id: str, message_id: str) -> Optional[MessageInfo]:
        """Get a specific message by ID (O(1) via index)."""
        await cls._ensure_cache(session_id)
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
            for sid in cls._parts_cache:
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
        # Cap oversized tool results before they reach storage
        part = cls._cap_tool_part_output(part)

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
                    parts_list[i] = part
                    updated = True
                    break
            
            if not updated:
                parts_list.append(part)

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
                messages.pop(idx)
                cls._rebuild_id_index(session_id)
                if session_id in cls._parts_cache:
                    cls._parts_cache[session_id].pop(message_id, None)
                cls._cancel_parts_flush_task(session_id)
                await cls._persist_messages(session_id)
                await cls._persist_parts(session_id)
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
        await cls._ensure_cache(session_id)
        
        async with _session_locks.get(session_id):
            count = len(cls._messages_cache.get(session_id, []))
            cls._messages_cache[session_id] = []
            cls._parts_cache[session_id] = {}
            cls._cancel_parts_flush_task(session_id)
            
            # Persist changes
            await cls._persist_messages(session_id)
            await cls._persist_parts(session_id)
            
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
            cls._cancel_parts_flush_task(session_id)
            cls._lru.pop(session_id, None)
            cls._messages_cache.pop(session_id, None)
            cls._msg_id_index.pop(session_id, None)
            cls._parts_cache.pop(session_id, None)
            cls._parts_serialized_cache.pop(session_id, None)
            _session_locks.discard(session_id)
        else:
            for sid in list(cls._parts_flush_tasks):
                cls._cancel_parts_flush_task(sid)
            cls._lru.clear()
            cls._messages_cache.clear()
            cls._msg_id_index.clear()
            cls._parts_cache.clear()
            cls._parts_serialized_cache.clear()
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
                    parts[i] = updated_part
                    
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
            for sid in Message._parts_cache:
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


