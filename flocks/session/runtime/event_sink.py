"""Best-effort delivery of observable session runtime events."""

from __future__ import annotations

from typing import Any, Optional

from flocks.session.core.turn_state import set_turn_state
from flocks.utils.log import Log


log = Log.create(service="session.events")


class SessionEventSink:
    """Forward runtime events without coupling control flow to observers."""

    @staticmethod
    async def emit(
        callbacks: Any,
        event_name: str,
        payload: dict[str, Any],
    ) -> None:
        """Publish one event; observer failures never fail the agent run."""
        publish = getattr(callbacks, "event_publish_callback", None)
        if publish is None:
            return
        try:
            await publish(event_name, payload)
        except Exception as exc:
            log.debug(
                "session.event.publish_failed",
                {"event": event_name, "error": str(exc)},
            )

    @classmethod
    async def turn_stopped(
        cls,
        callbacks: Any,
        session_id: str,
        *,
        step: int,
        stop_reason: str,
    ) -> None:
        """Publish the terminal state of one logical turn."""
        turn_state = set_turn_state(
            session_id,
            step=step,
            status="stopped",
            stop_reason=stop_reason,
            queued_message_detected=False,
        )
        await cls.emit(
            callbacks,
            "turn.stopped",
            turn_state.model_dump(by_alias=True),
        )

    @classmethod
    async def session_status(
        cls,
        callbacks: Any,
        session_id: str,
        status: str,
    ) -> None:
        """Publish the current process-local session execution status."""
        await cls.emit(
            callbacks,
            "session.status",
            {"sessionID": session_id, "status": {"type": status}},
        )

    @classmethod
    async def notice(
        cls,
        callbacks: Any,
        session_id: str,
        *,
        level: str,
        message: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """Publish a user-visible session notice."""
        await cls.emit(
            callbacks,
            "session.notice",
            {
                "sessionID": session_id,
                "level": level,
                "message": message,
                "details": details or {},
            },
        )
