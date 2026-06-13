"""Conversation rewind helpers."""

from dataclasses import dataclass
from typing import Optional

from flocks.session.lifecycle.revert import SessionRevert
from flocks.session.message import Message, MessageInfo, MessageRole
from flocks.session.session import Session, SessionInfo
from flocks.session.session_loop import SessionLoop


@dataclass(frozen=True)
class RewindResult:
    """Result of selecting and applying a conversation rewind target."""

    session: SessionInfo
    target_message: MessageInfo


@dataclass(frozen=True)
class RewindCandidate:
    """A user turn that can be selected as a rewind target."""

    index: int
    message: MessageInfo
    preview: str


class SessionRewind:
    """Pick a user-turn rewind target and apply the existing revert flow."""

    @classmethod
    async def rewind(
        cls,
        session_id: str,
        *,
        count: int = 1,
        message_id: Optional[str] = None,
    ) -> RewindResult:
        if count < 1:
            raise ValueError("Rewind count must be at least 1")
        if SessionLoop.is_running(session_id):
            raise RuntimeError("Cannot rewind while the session is running")

        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        messages = await Message.list(session_id)
        target = cls._select_target(messages, session, count=count, message_id=message_id)
        if not target:
            raise ValueError("No user message is available to rewind to")

        updated = await SessionRevert.revert(
            session_id=session_id,
            message_id=target.id,
        )
        if not updated:
            raise ValueError("Failed to apply rewind")
        return RewindResult(session=updated, target_message=target)

    @classmethod
    async def candidates(cls, session_id: str) -> list[RewindCandidate]:
        session = await Session.get_by_id(session_id)
        if not session:
            raise ValueError(f"Session {session_id} not found")

        messages = await Message.list(session_id)
        candidates = cls._candidate_messages(messages, session)
        result: list[RewindCandidate] = []
        for index, message in enumerate(reversed(candidates), start=1):
            result.append(
                RewindCandidate(
                    index=index,
                    message=message,
                    preview=await cls._message_preview(session_id, message),
                )
            )
        return result

    @staticmethod
    def _select_target(
        messages: list[MessageInfo],
        session: SessionInfo,
        *,
        count: int,
        message_id: Optional[str],
    ) -> Optional[MessageInfo]:
        candidates = SessionRewind._candidate_messages(messages, session)
        if not candidates:
            return None

        if message_id:
            return next((message for message in candidates if message.id == message_id), None)

        return candidates[-min(count, len(candidates))]

    @staticmethod
    def _candidate_messages(messages: list[MessageInfo], session: SessionInfo) -> list[MessageInfo]:
        message_indexes = {message.id: index for index, message in enumerate(messages)}
        user_messages = [message for message in messages if message.role == MessageRole.USER]
        if not user_messages:
            return []

        boundary = len(messages)
        if session.revert and session.revert.message_id:
            for index, message in enumerate(messages):
                if message.id == session.revert.message_id:
                    boundary = index
                    break

        candidates = [
            message
            for message in user_messages
            if message_indexes.get(message.id, len(messages)) < boundary
        ]
        if not candidates:
            return user_messages[:1]

        return candidates

    @staticmethod
    async def _message_preview(session_id: str, message: MessageInfo, max_chars: int = 120) -> str:
        parts = await Message.parts(message.id, session_id)
        text = " ".join(
            str(getattr(part, "text", "")).strip()
            for part in parts
            if getattr(part, "type", None) == "text" and not getattr(part, "synthetic", False)
        )
        text = " ".join(text.split())
        if not text:
            text = message.id
        if len(text) > max_chars:
            return text[: max_chars - 1].rstrip() + "…"
        return text
