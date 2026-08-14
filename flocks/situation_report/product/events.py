"""Durable-sequenced status events for managed report Sessions."""

from __future__ import annotations

from typing import Any

from flocks.server.routes.event import publish_event

from .files import async_file_lock, atomic_write_json, read_json, session_root, utc_now


REPORT_STATUS_EVENT = "situation.report.status"


async def publish_report_status(
    *,
    session_id: str,
    generation_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Persist the next sequence and publish one Session-addressed Event."""

    workspace_dir = session_root(session_id)
    state_path = workspace_dir / "runs" / generation_id / "event_state.json"
    async with async_file_lock(workspace_dir / ".locks" / "status-event.lock"):
        state = read_json(state_path) if state_path.exists() else {"lastSequence": 0}
        sequence = int(state.get("lastSequence") or 0) + 1
        properties = {
            "schemaVersion": 1,
            "eventID": f"{generation_id}:{sequence}",
            "eventSequence": sequence,
            "sessionID": session_id,
            "generationID": generation_id,
            **payload,
        }
        atomic_write_json(
            state_path,
            {
                "lastSequence": sequence,
                "lastEventID": properties["eventID"],
                "lastStatus": properties.get("status"),
                "updatedAt": utc_now(),
            },
        )
        await publish_event(REPORT_STATUS_EVENT, properties)
        return properties
