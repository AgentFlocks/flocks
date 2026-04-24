"""
Session management module.

Provides session lifecycle, message management, LLM interaction,
prompt generation, context compaction, and summarization.
"""

from flocks.session.session import (
    Session,
    SessionInfo,
    SessionChangeStats,
    SessionRevert,
    SessionTime,
    PermissionRule,
)
from flocks.session.features.memory import SessionMemory
from flocks.session.message import (
    Message,
    MessageInfo,
    MessageRole,
    MessagePart,
    MessageV2,
    TokenUsage,
    MessageTime,
    MessagePath,
    # Part types
    TextPart,
    FilePart,
    ToolPart,
    ReasoningPart,
    PatchPart,
    AgentPart,
    SubtaskPart,
)
from flocks.session.prompt import SessionPrompt, SystemPrompt, ContextInfo
from flocks.session.lifecycle.compaction import SessionCompaction, CompactionResult, CompactionPolicy, ContextTier
from flocks.session.lifecycle.summary import SessionSummary, FileDiff
from flocks.session.runner import (
    SessionRunner,
    RunnerCallbacks,
    ToolCall,
    StepResult,
    run_session,
)
from flocks.session.session_loop import (
    SessionLoop,
    LoopContext,
    LoopCallbacks,
    LoopResult,
)
from flocks.session.features.reminders import (
    SessionReminders,
    ReminderConfig,
    ReminderContext,
)
from flocks.session.features.subtask import (
    SessionSubtask,
    SubtaskInfo,
    SubtaskResult,
)
from flocks.session.lifecycle.revert import (
    SessionRevertManager,
    RevertInput,
)
from flocks.session.features.todo import (
    Todo,
    TodoInfo,
    TodoStatus,
    TodoPriority,
)

__all__ = [
    # Session
    "Session",
    "SessionInfo",
    "SessionChangeStats",
    "SessionRevert",
    "SessionTime",
    "PermissionRule",
    "SessionMemory",
    # Message
    "Message",
    "MessageInfo",
    "MessageRole",
    "MessagePart",
    "MessageV2",
    "TokenUsage",
    "MessageTime",
    "MessagePath",
    "TextPart",
    "FilePart",
    "ToolPart",
    "ReasoningPart",
    "PatchPart",
    "AgentPart",
    "SubtaskPart",
    # Prompt
    "SessionPrompt",
    "SystemPrompt",
    "ContextInfo",
    # Compaction
    "SessionCompaction",
    "CompactionResult",
    # Summary
    "SessionSummary",
    "FileDiff",
    # Runner
    "SessionRunner",
    "RunnerCallbacks",
    "ToolCall",
    "StepResult",
    "run_session",
    # Session Loop
    "SessionLoop",
    "LoopContext",
    "LoopCallbacks",
    "LoopResult",
    # Reminders
    "SessionReminders",
    "ReminderConfig",
    "ReminderContext",
    # Subtask
    "SessionSubtask",
    "SubtaskInfo",
    "SubtaskResult",
    # Revert
    "SessionRevertManager",
    "RevertInput",
    # Todo
    "Todo",
    "TodoInfo",
    "TodoStatus",
    "TodoPriority",
]
