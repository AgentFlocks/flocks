"""Unified user-input events for command and prompt dispatch."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from flocks.command.command import CommandInfo
from flocks.input.types import InputSourceType, surface_for_source


class UserInputEvent(BaseModel):
    """Normalized input event shared by WebUI, TUI, CLI, ACP, and channels."""

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    source_type: InputSourceType
    session_id: Optional[str] = Field(None, alias="sessionID")
    text: str
    parts: List[Dict[str, Any]] = Field(default_factory=list)
    agent: Optional[str] = None
    model: Optional[Any] = None
    variant: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    delivery_context: Dict[str, Any] = Field(default_factory=dict)
    display_text: Optional[str] = None
    message_id: Optional[str] = Field(None, alias="messageID")
    working_directory: Optional[str] = None
    no_reply: Optional[bool] = Field(None, alias="noReply")
    mock_reply: Optional[str] = Field(None, alias="mockReply")
    system: Optional[str] = None
    tools: Optional[Dict[str, bool]] = None

    @property
    def surface(self) -> str:
        return surface_for_source(self.source_type)

    @property
    def user_visible_text(self) -> str:
        return self.display_text or self.text


class ParsedCommand(BaseModel):
    """Parsed slash command enriched with registry metadata."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    raw_text: str
    command_name: str
    canonical_name: str
    args: str = ""
    command_def: Optional[CommandInfo] = None
