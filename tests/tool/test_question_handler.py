from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_api_question_handler_publishes_rejected_event_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from flocks.server.routes import event as event_routes
    from flocks.server.routes.question import list_question_requests
    from flocks.tool import question_handler
    from flocks.utils.id import Identifier

    events: list[tuple[str, dict]] = []

    async def fake_publish_event(event_type: str, payload: dict) -> None:
        events.append((event_type, payload))

    monkeypatch.setattr(Identifier, "ascending", lambda _prefix: "question_timeout_req")
    monkeypatch.setattr(question_handler.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(event_routes, "publish_event", fake_publish_event)

    with pytest.raises(TimeoutError):
        await question_handler.api_question_handler(
            "ses_question_timeout",
            [{"question": "Continue?", "type": "confirm"}],
        )

    assert ("question.asked", {
        "id": "question_timeout_req",
        "sessionID": "ses_question_timeout",
        "questions": [{
            "question": "Continue?",
            "header": "",
            "type": "confirm",
            "options": [],
            "multiple": False,
            "placeholder": "",
            "multiline": False,
            "custom": True,
        }],
    }) in events
    assert ("question.rejected", {
        "sessionID": "ses_question_timeout",
        "requestID": "question_timeout_req",
        "reason": "timeout",
    }) in events
    assert list_question_requests("ses_question_timeout") == []
