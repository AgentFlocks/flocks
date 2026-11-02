"""Resend / regenerate rewind the session goal together with the history (INC-80002C0A).

The goal lives beside the messages and SessionLoop judges it after every
turn. Edited and resent, a goal message used to replay its new text while
the old objective stayed in place, so the next continuation went back to
the old goal: the edit seemed ignored and the old run carried on.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient

from flocks.session.goal import GoalManager
from flocks.session.lifecycle.revert import SessionRevert


@pytest.fixture
def replay(monkeypatch: pytest.MonkeyPatch) -> list:
    """Run the replay up to the history cleanup, without a model; returns the events published."""
    from flocks.server.routes import session as session_routes

    async def _fake_instance_provide(*, directory, init, fn):
        return await fn()

    async def _fake_prepare_replay_runtime(session_id: str, user_message):
        return {"agent_name": "rex", "provider_id": "openai", "model_id": "gpt-4-turbo-preview"}

    async def _fake_run_existing_user_message(session_id, session, user_message, working_directory, runtime=None):
        await SessionRevert.cleanup(session)
        return {"status": "completed", "sessionID": session_id, "messageID": user_message.id}

    published: list = []

    async def _record_event(event_type, properties, *args, **kwargs):
        published.append((event_type, properties))

    monkeypatch.setattr("flocks.project.instance.Instance.provide", _fake_instance_provide)
    monkeypatch.setattr(session_routes, "_prepare_replay_runtime", _fake_prepare_replay_runtime)
    monkeypatch.setattr(session_routes, "_run_existing_user_message", _fake_run_existing_user_message)
    monkeypatch.setattr("flocks.server.routes.event.publish_event", _record_event)
    scheduled: list = []
    monkeypatch.setattr(session_routes, "_schedule_background_coro", lambda coro, **kwargs: scheduled.append(coro))
    published.append(("scheduled", scheduled))
    return published


async def _run_scheduled(published: list) -> None:
    scheduled = published[0][1]
    assert len(scheduled) == 1
    await scheduled.pop(0)


async def _say(client: AsyncClient, session_id: str, text: str, *, goal: bool = False, **extra) -> None:
    body = {"parts": [{"type": "text", "text": text}], "noReply": True, "mockReply": f"reply to {text}", **extra}
    if goal:
        body["executionMode"] = "goal"
    resp = await client.post(f"/api/session/{session_id}/message", json=body)
    assert resp.status_code == status.HTTP_200_OK, resp.text


async def _messages(client: AsyncClient, session_id: str) -> list:
    resp = await client.get(f"/api/session/{session_id}/message")
    assert resp.status_code == status.HTTP_200_OK
    return resp.json()


def _users(messages: list) -> list:
    return [message for message in messages if message["info"]["role"] == "user"]


def _text_part(message: dict) -> dict:
    return next(part for part in message["parts"] if part["type"] == "text")


async def _resend(client: AsyncClient, session_id: str, message: dict, text: str) -> None:
    resp = await client.post(
        f"/api/session/{session_id}/message/{message['info']['id']}/resend",
        json={"text": text, "partID": _text_part(message)["id"]},
    )
    assert resp.status_code == status.HTTP_202_ACCEPTED, resp.text


async def _advance_goal(session_id: str, turns: int) -> None:
    state = await GoalManager.get(session_id)
    state.turns_used = turns
    state.last_reason = "still working"
    await GoalManager.save(session_id, state)


@pytest.mark.asyncio
async def test_resend_of_the_goal_message_restarts_the_goal_toward_the_edited_objective(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    await _advance_goal(session_id, 3)
    goal_message = _users(await _messages(client, session_id))[0]
    assert _text_part(goal_message)["text"] == GoalManager.goal_prompt("OBJ-A build the alpha report")

    # The edit box holds the goal prompt as stored; the user rewrites the objective in it.
    edited = GoalManager.goal_prompt("OBJ-A build the alpha report").replace(
        "OBJ-A build the alpha report", "OBJ-B build the beta report",
    )
    await _resend(client, session_id, goal_message, edited)
    await _run_scheduled(replay)

    state = await GoalManager.get(session_id)
    assert state.objective == "OBJ-B build the beta report"
    assert state.status == "active" and state.turns_used == 0
    [message] = await _messages(client, session_id)
    part = _text_part(message)
    assert part["text"] == GoalManager.goal_prompt("OBJ-B build the beta report")
    assert part["metadata"]["displayText"] == "OBJ-B build the beta report"
    assert ("session.goal.updated", {
        "sessionID": session_id, "status": "active", "objective": "OBJ-B build the beta report", "reason": None,
    }) in replay


@pytest.mark.asyncio
async def test_goal_message_rewritten_as_plain_text_stays_a_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    goal_message = _users(await _messages(client, session_id))[0]

    await _resend(client, session_id, goal_message, "OBJ-C just list the tools")
    await _run_scheduled(replay)

    assert (await GoalManager.get(session_id)).objective == "OBJ-C just list the tools"
    [message] = await _messages(client, session_id)
    assert _text_part(message)["text"] == GoalManager.goal_prompt("OBJ-C just list the tools")


@pytest.mark.asyncio
async def test_resend_of_a_message_before_the_goal_clears_the_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "first question")
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    first = _users(await _messages(client, session_id))[0]

    await _resend(client, session_id, first, "first question edited")
    await _run_scheduled(replay)

    assert await GoalManager.get(session_id) is None
    assert [_text_part(message)["text"] for message in await _messages(client, session_id)] == [
        "first question edited",
    ]
    assert ("session.goal.updated", {
        "sessionID": session_id, "status": "cleared", "objective": "OBJ-A build the alpha report", "reason": None,
    }) in replay


@pytest.mark.asyncio
async def test_resend_of_a_message_after_the_goal_keeps_the_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    await _say(client, session_id, "also mention the date")
    await _advance_goal(session_id, 2)
    later = _users(await _messages(client, session_id))[1]
    del replay[1:]  # the goal message announced its goal when it was sent

    await _resend(client, session_id, later, "also mention the time")
    await _run_scheduled(replay)

    state = await GoalManager.get(session_id)
    assert state.objective == "OBJ-A build the alpha report" and state.turns_used == 2
    assert not [event for event in replay if event[0] == "session.goal.updated"]


@pytest.mark.asyncio
async def test_resend_drops_the_display_text_written_for_the_old_text(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "expanded command prompt", displayText="/report weekly")
    message = _users(await _messages(client, session_id))[0]
    assert _text_part(message)["metadata"]["displayText"] == "/report weekly"

    await _resend(client, session_id, message, "write the monthly report")
    await _run_scheduled(replay)

    [message] = await _messages(client, session_id)
    part = _text_part(message)
    assert part["text"] == "write the monthly report"
    assert "displayText" not in (part.get("metadata") or {})


@pytest.mark.asyncio
async def test_regenerating_the_goal_reply_starts_the_goal_over(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    await _advance_goal(session_id, 3)
    reply = next(m for m in await _messages(client, session_id) if m["info"]["role"] == "assistant")

    resp = await client.post(f"/api/session/{session_id}/message/{reply['info']['id']}/regenerate")
    assert resp.status_code == status.HTTP_202_ACCEPTED, resp.text
    await _run_scheduled(replay)

    state = await GoalManager.get(session_id)
    assert state.objective == "OBJ-A build the alpha report"
    assert state.status == "active" and state.turns_used == 0


@pytest.mark.asyncio
async def test_regenerating_a_reply_before_the_goal_clears_the_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "first question")
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    first_reply = next(m for m in await _messages(client, session_id) if m["info"]["role"] == "assistant")

    resp = await client.post(f"/api/session/{session_id}/message/{first_reply['info']['id']}/regenerate")
    assert resp.status_code == status.HTTP_202_ACCEPTED, resp.text
    await _run_scheduled(replay)

    assert await GoalManager.get(session_id) is None


@pytest.mark.asyncio
async def test_a_multi_paragraph_objective_survives_a_resend(
    client: AsyncClient, session_id: str, replay: list,
):
    objective = "write the report\n\nit must list the acceptance results"
    await _say(client, session_id, objective, goal=True)
    goal_message = _users(await _messages(client, session_id))[0]

    await _resend(client, session_id, goal_message, _text_part(goal_message)["text"])
    await _run_scheduled(replay)

    assert (await GoalManager.get(session_id)).objective == objective


@pytest.mark.asyncio
async def test_replaying_an_earlier_goal_message_restarts_that_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    await _say(client, session_id, "OBJ-B build the beta report", goal=True)
    first_goal = _users(await _messages(client, session_id))[0]
    assert (await GoalManager.get(session_id)).objective == "OBJ-B build the beta report"

    await _resend(client, session_id, first_goal, "OBJ-A2 build the alpha report again")
    await _run_scheduled(replay)

    state = await GoalManager.get(session_id)
    assert state.objective == "OBJ-A2 build the alpha report again" and state.status == "active"


@pytest.mark.asyncio
async def test_a_goal_message_saved_then_resent_restarts_the_goal(
    client: AsyncClient, session_id: str, replay: list,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    goal_message = _users(await _messages(client, session_id))[0]
    part = _text_part(goal_message)
    saved = part["text"].replace("OBJ-A build the alpha report", "OBJ-B build the beta report")
    resp = await client.patch(
        f"/api/session/{session_id}/message/{goal_message['info']['id']}/part/{part['id']}",
        json={**part, "text": saved},
    )
    assert resp.status_code == status.HTTP_200_OK, resp.text
    goal_message = _users(await _messages(client, session_id))[0]

    await _resend(client, session_id, goal_message, saved.replace("OBJ-B", "OBJ-C"))
    await _run_scheduled(replay)

    assert (await GoalManager.get(session_id)).objective == "OBJ-C build the beta report"


@pytest.mark.asyncio
async def test_a_goal_that_cannot_be_cleared_stops_the_replay_before_history_changes(
    client: AsyncClient, session_id: str, replay: list, monkeypatch: pytest.MonkeyPatch,
):
    await _say(client, session_id, "first question")
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    before = await _messages(client, session_id)
    del replay[1:]

    async def _locked(session_id: str) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(GoalManager, "remove", _locked)
    await _resend(client, session_id, _users(before)[0], "first question edited")
    with pytest.raises(RuntimeError, match="database is locked"):
        await _run_scheduled(replay)

    assert (await GoalManager.get(session_id)).objective == "OBJ-A build the alpha report"
    assert await _messages(client, session_id) == before
    assert not [event for event in replay[1:] if event[0] == "session.goal.updated"]


@pytest.mark.asyncio
async def test_a_goal_that_cannot_be_read_stops_the_replay_before_history_changes(
    client: AsyncClient, session_id: str, replay: list, monkeypatch: pytest.MonkeyPatch,
):
    from flocks.session import goal as goal_module

    await _say(client, session_id, "first question")
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    before = await _messages(client, session_id)
    real_get = goal_module.Storage.get
    failing = {"on": True}

    async def _get(key, *args, **kwargs):
        if failing["on"] and str(key).startswith("goal:"):
            raise RuntimeError("database is locked")
        return await real_get(key, *args, **kwargs)

    monkeypatch.setattr(goal_module.Storage, "get", _get)
    await _resend(client, session_id, _users(before)[0], "first question edited")
    with pytest.raises(RuntimeError, match="database is locked"):
        await _run_scheduled(replay)
    failing["on"] = False

    assert (await GoalManager.get(session_id)).objective == "OBJ-A build the alpha report"
    assert await _messages(client, session_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["resend", "regenerate"])
async def test_a_replay_that_fails_to_rewind_the_history_puts_the_goal_back(
    client: AsyncClient, session_id: str, replay: list, monkeypatch: pytest.MonkeyPatch, action: str,
):
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    await _advance_goal(session_id, 3)
    before = await _messages(client, session_id)
    del replay[1:]

    async def _revert_fails(*args, **kwargs):
        raise RuntimeError("revert failed")

    monkeypatch.setattr(SessionRevert, "revert", _revert_fails)
    if action == "resend":
        await _resend(client, session_id, _users(before)[0], "OBJ-B build the beta report")
    else:
        reply = next(m for m in before if m["info"]["role"] == "assistant")
        resp = await client.post(f"/api/session/{session_id}/message/{reply['info']['id']}/regenerate")
        assert resp.status_code == status.HTTP_202_ACCEPTED, resp.text
    with pytest.raises(RuntimeError, match="revert failed"):
        await _run_scheduled(replay)

    state = await GoalManager.get(session_id)
    assert state.objective == "OBJ-A build the alpha report" and state.turns_used == 3
    assert await _messages(client, session_id) == before
    assert not [event for event in replay[1:] if event[0] == "session.goal.updated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("replayed", ["goal message", "message before the goal"])
async def test_a_failed_replay_leaves_a_goal_set_meanwhile_alone(
    client: AsyncClient, session_id: str, replay: list, monkeypatch: pytest.MonkeyPatch, replayed: str,
):
    if replayed == "message before the goal":
        await _say(client, session_id, "first question")
    await _say(client, session_id, "OBJ-A build the alpha report", goal=True)
    target = _users(await _messages(client, session_id))[0]

    async def _goal_set_meanwhile_then_revert_fails(*args, **kwargs):
        await GoalManager.set_goal(session_id, "OBJ-D set by another message")
        raise RuntimeError("revert failed")

    monkeypatch.setattr(SessionRevert, "revert", _goal_set_meanwhile_then_revert_fails)
    await _resend(client, session_id, target, "OBJ-B build the beta report")
    with pytest.raises(RuntimeError, match="revert failed"):
        await _run_scheduled(replay)

    assert (await GoalManager.get(session_id)).objective == "OBJ-D set by another message"


def test_objective_from_prompt_reads_an_edited_goal_prompt():
    prompt = GoalManager.goal_prompt("ship the beta")
    assert GoalManager.objective_from_prompt(prompt) == "ship the beta"
    assert GoalManager.objective_from_prompt(prompt.replace("\n", "\r\n")) == "ship the beta"
    assert GoalManager.objective_from_prompt(GoalManager.goal_prompt("a\n\nb")) == "a\n\nb"
    assert GoalManager.objective_from_prompt("  just a question  ") == "just a question"
    assert GoalManager.is_goal_prompt(prompt + "\n")
    assert not GoalManager.is_goal_prompt("ship the beta")
