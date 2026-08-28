"""
Stream Event Types

Defines event types for streaming LLM responses, matching Flocks' event system.
Based on Flocks' ported src/session/processor.ts event handling.
"""

from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field


# Event type literals matching Flocks
StreamEventType = Literal[
    "start",
    "reasoning-start",
    "reasoning-delta",
    "reasoning-end",
    "tool-input-start",
    "tool-input-delta",
    "tool-input-end",
    "tool-input-error",
    "tool-call",
    "tool-result",
    "tool-error",
    "text-start",
    "text-delta",
    "text-end",
    "start-step",
    "finish-step",
    "finish",
]


class BaseStreamEvent(BaseModel):
    """Base stream event"""
    type: StreamEventType


class StartEvent(BaseStreamEvent):
    """Stream start event"""
    type: Literal["start"] = "start"


class ReasoningStartEvent(BaseStreamEvent):
    """Reasoning block start"""
    type: Literal["reasoning-start"] = "reasoning-start"
    id: str  # Reasoning block ID
    metadata: Optional[Dict[str, Any]] = None


class ReasoningDeltaEvent(BaseStreamEvent):
    """Incremental reasoning content"""
    type: Literal["reasoning-delta"] = "reasoning-delta"
    id: str
    text: str
    metadata: Optional[Dict[str, Any]] = None


class ReasoningEndEvent(BaseStreamEvent):
    """Reasoning block end"""
    type: Literal["reasoning-end"] = "reasoning-end"
    id: str
    metadata: Optional[Dict[str, Any]] = None


class ToolInputStartEvent(BaseStreamEvent):
    """Tool input start"""
    type: Literal["tool-input-start"] = "tool-input-start"
    id: str  # Tool call ID
    tool_name: str


class ToolInputDeltaEvent(BaseStreamEvent):
    """Incremental tool input"""
    type: Literal["tool-input-delta"] = "tool-input-delta"
    id: str
    delta: str  # JSON fragment


class ToolInputEndEvent(BaseStreamEvent):
    """Tool input end"""
    type: Literal["tool-input-end"] = "tool-input-end"
    id: str


class ToolInputErrorEvent(BaseStreamEvent):
    """Tool input failed before a runnable tool call was produced."""
    type: Literal["tool-input-error"] = "tool-input-error"
    id: str
    tool_name: str
    input: Dict[str, Any] = Field(default_factory=dict)
    error: str


class ToolCallEvent(BaseStreamEvent):
    """Tool call request (ready to execute)"""
    type: Literal["tool-call"] = "tool-call"
    tool_call_id: str
    tool_name: str
    input: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class ToolResultEvent(BaseStreamEvent):
    """Tool execution result"""
    type: Literal["tool-result"] = "tool-result"
    tool_call_id: str
    input: Dict[str, Any]
    output: Dict[str, Any]  # Contains: output, title, metadata, attachments


class ToolErrorEvent(BaseStreamEvent):
    """Tool execution error"""
    type: Literal["tool-error"] = "tool-error"
    tool_call_id: str
    error: str


class TextStartEvent(BaseStreamEvent):
    """Text block start"""
    type: Literal["text-start"] = "text-start"
    metadata: Optional[Dict[str, Any]] = None


class TextDeltaEvent(BaseStreamEvent):
    """Incremental text content"""
    type: Literal["text-delta"] = "text-delta"
    text: str
    metadata: Optional[Dict[str, Any]] = None


class TextEndEvent(BaseStreamEvent):
    """Text block end"""
    type: Literal["text-end"] = "text-end"
    metadata: Optional[Dict[str, Any]] = None


class StartStepEvent(BaseStreamEvent):
    """Step start"""
    type: Literal["start-step"] = "start-step"


class FinishStepEvent(BaseStreamEvent):
    """Step finish with usage"""
    type: Literal["finish-step"] = "finish-step"
    tokens: Dict[str, int]  # input, output, reasoning, cache
    cost: float


class FinishEvent(BaseStreamEvent):
    """Stream finish"""
    type: Literal["finish"] = "finish"
    finish_reason: str  # "stop", "tool-calls", "length", "error"


# Union type for all events
StreamEvent = (
    StartEvent |
    ReasoningStartEvent |
    ReasoningDeltaEvent |
    ReasoningEndEvent |
    ToolInputStartEvent |
    ToolInputDeltaEvent |
    ToolInputEndEvent |
    ToolInputErrorEvent |
    ToolCallEvent |
    ToolResultEvent |
    ToolErrorEvent |
    TextStartEvent |
    TextDeltaEvent |
    TextEndEvent |
    StartStepEvent |
    FinishStepEvent |
    FinishEvent
)


def event_from_dict(data: Dict[str, Any]) -> StreamEvent:
    """
    Create stream event from dictionary
    
    Args:
        data: Event data with 'type' field
        
    Returns:
        Appropriate StreamEvent subclass
    """
    event_type = data.get("type")
    
    event_map = {
        "start": StartEvent,
        "reasoning-start": ReasoningStartEvent,
        "reasoning-delta": ReasoningDeltaEvent,
        "reasoning-end": ReasoningEndEvent,
        "tool-input-start": ToolInputStartEvent,
        "tool-input-delta": ToolInputDeltaEvent,
        "tool-input-end": ToolInputEndEvent,
        "tool-input-error": ToolInputErrorEvent,
        "tool-call": ToolCallEvent,
        "tool-result": ToolResultEvent,
        "tool-error": ToolErrorEvent,
        "text-start": TextStartEvent,
        "text-delta": TextDeltaEvent,
        "text-end": TextEndEvent,
        "start-step": StartStepEvent,
        "finish-step": FinishStepEvent,
        "finish": FinishEvent,
    }
    
    event_class = event_map.get(event_type)
    if not event_class:
        # Default to base event
        return BaseStreamEvent(**data)
    
    return event_class(**data)
