"""
Session Reminders data models.

The runtime reminder injection path has been removed because it was never
wired into session execution. These exports remain as legacy import
compatibility until that public surface is retired separately.
"""

from dataclasses import dataclass
from typing import Optional

# Reminder trigger threshold defaults (kept for reference if re-enabled)
REMINDER_STEP_INTERVAL = 10
REMINDER_MESSAGE_INTERVAL = 20
REMINDER_TIME_INTERVAL = 300000  # 5 minutes in ms


@dataclass
class ReminderConfig:
    """Configuration for reminder triggers"""
    step_interval: int = REMINDER_STEP_INTERVAL
    message_interval: int = REMINDER_MESSAGE_INTERVAL
    time_interval: int = REMINDER_TIME_INTERVAL
    enabled: bool = True


@dataclass
class ReminderContext:
    """Context for reminder generation"""
    session_id: str
    step_count: int
    message_count: int
    elapsed_ms: int
    original_task: Optional[str] = None
    current_focus: Optional[str] = None


class SessionReminders:
    """Compatibility stub for the removed reminder runtime."""

    _last_reminder: dict = {}
    _last_step: dict = {}

    @classmethod
    def should_remind(cls, session_id: str, ctx: ReminderContext, config=None) -> bool:
        return False

    @classmethod
    async def create_reminder(cls, session_id: str, ctx: ReminderContext, config=None):
        return None

    @classmethod
    async def extract_original_task(cls, messages) -> Optional[str]:
        for msg in messages:
            from flocks.session.message import Message, MessageRole
            if msg.role == MessageRole.USER:
                content = await Message.get_text_content(msg)
                if content:
                    return content[:200] + ("..." if len(content) > 200 else "")
        return None

    @classmethod
    def clear_session(cls, session_id: str) -> None:
        cls._last_reminder.pop(session_id, None)
        cls._last_step.pop(session_id, None)


__all__ = [
    "SessionReminders",
    "ReminderConfig",
    "ReminderContext",
]
