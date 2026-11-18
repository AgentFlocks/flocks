"""A session continued from the history goes to the top of the session lists.

INC-CB90DF32: lists sort by ``time.updated``, newest first, and the WebUI
history refreshes on ``session.*`` events. A turn only touched the session at
its end and said nothing, so a session continued from the history kept its
place in a list already on screen. The loop now touches the session as the
turn starts and as it ends, and announces ``session.updated`` each time.

Case list (编号 | 场景):
  SA1 a root session's turn: touched and announced as it starts (after busy, before
      the turn runs) and as it ends (after idle), each announcement after its touch
  SA2 a sub-agent session's turn: touched as well, never announced (not a row of
      the lists; its turns would only make them refetch)
  SA3 a touch that fails neither stops the turn nor is announced
  SA4 real storage: two sessions, the older one continued -- the project's list
      puts it first before the turn runs, and the events name it
  SA5 a session the lists leave out (category test, hideFromSessionManager: an
      agent's test run) is touched, never announced
  SA6 a touch that writes nothing (the session archived meanwhile) is not
      announced, and the turn still ends with its idle notice
  SA7 a Session whose touch returns nothing (the Pro edition's, which takes the
      open one's place): the loop goes by update, which says what it wrote
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import flocks.session.session_loop as loop_mod
from flocks.bus.events import SessionIdle
from flocks.session.session import Session, SessionInfo
from flocks.session.session_loop import LoopCallbacks, LoopResult, SessionLoop
from flocks.storage.storage import Storage


def _info(session_id: str, *, parent_id: str | None = None, category: str = "user", metadata: dict | None = None) -> SessionInfo:
    return SessionInfo.model_construct(
        id=session_id, slug="t", project_id="proj", directory="/tmp", title="t", parent_id=parent_id,
        category=category, metadata=metadata or {},
    )


def _recorder(written: SessionInfo | None):
    """What happened, in order: touches -- Session.update with nothing to change, answering
    *written* as it does -- and published events."""
    seen: list[tuple] = []

    async def touch(project_id: str, session_id: str, **updates):
        assert not updates, updates
        seen.append(("touch", session_id))
        return written

    async def publish(event_type: str, properties: dict) -> None:
        if event_type == "session.status":
            seen.append((event_type, properties["status"]["type"]))
        else:
            seen.append((event_type, properties.get("id")))

    return seen, touch, publish


async def _run(session_info: SessionInfo, *, touch, publish, get_by_id=None, run_loop=None, bus=None, session_touch=None) -> LoopResult:
    async def stop(*_args, **_kwargs):
        return LoopResult(action="stop")

    with patch.object(loop_mod.Session, "get_by_id", get_by_id or AsyncMock(return_value=session_info)), \
         patch.object(loop_mod.Message, "list", AsyncMock(return_value=[])), \
         patch("flocks.session.orphan_tools.abort_orphan_running_parts", AsyncMock(return_value=0)), \
         patch.object(SessionLoop, "_run_loop", AsyncMock(side_effect=run_loop or stop)), \
         patch.object(SessionLoop, "_settle_todos", AsyncMock()), \
         patch.object(loop_mod.Session, "update", touch), \
         patch.object(loop_mod.Session, "touch", session_touch or AsyncMock(return_value=None)), \
         patch("flocks.bus.bus.Bus.publish", bus or AsyncMock()):
        return await SessionLoop.run(
            session_id=session_info.id, provider_id="p", model_id="m", agent_name="rex",
            callbacks=LoopCallbacks(event_publish_callback=publish),
        )


@pytest.mark.asyncio
async def test_sa1_a_root_session_is_touched_and_announced_as_its_turn_starts_and_ends():
    session = _info("ses_sa1")
    seen, touch, publish = _recorder(session)
    at_turn: list[tuple] = []

    async def run_loop(*_args, **_kwargs):
        # The list is told before the turn runs, not after it.
        at_turn.extend(seen)
        return LoopResult(action="stop")

    result = await _run(session, touch=touch, publish=publish, run_loop=run_loop)

    assert result.action == "stop"
    assert at_turn == [("session.status", "busy"), ("touch", "ses_sa1"), ("session.updated", "ses_sa1")]
    assert seen == [
        ("session.status", "busy"),
        ("touch", "ses_sa1"),
        ("session.updated", "ses_sa1"),
        ("session.status", "idle"),
        ("touch", "ses_sa1"),
        ("session.updated", "ses_sa1"),
    ]


@pytest.mark.asyncio
async def test_sa2_a_sub_agent_session_is_touched_but_not_announced():
    child = _info("ses_sa2_child", parent_id="ses_sa2_parent")
    seen, touch, publish = _recorder(child)

    await _run(child, touch=touch, publish=publish)

    assert seen.count(("touch", "ses_sa2_child")) == 2
    assert not [entry for entry in seen if entry[0] == "session.updated"]


@pytest.mark.asyncio
async def test_sa3_a_touch_that_fails_neither_stops_the_turn_nor_is_announced():
    seen, _touch, publish = _recorder(None)

    async def failing_touch(project_id: str, session_id: str, **updates):
        raise RuntimeError("storage is busy")

    result = await _run(_info("ses_sa3"), touch=failing_touch, publish=publish)

    assert result.action == "stop"
    assert seen == [("session.status", "busy"), ("session.status", "idle")]


@pytest.mark.parametrize("hidden", [
    pytest.param({"category": "test"}, id="agent-test-category"),
    pytest.param({"metadata": {"hideFromSessionManager": True}}, id="hidden-from-session-manager"),
])
@pytest.mark.asyncio
async def test_sa5_a_session_the_lists_leave_out_is_touched_but_not_announced(hidden: dict):
    session = _info("ses_sa5", **hidden)
    seen, touch, publish = _recorder(session)

    await _run(session, touch=touch, publish=publish)

    assert seen.count(("touch", "ses_sa5")) == 2
    assert not [entry for entry in seen if entry[0] == "session.updated"]


@pytest.mark.asyncio
async def test_sa6_a_touch_that_writes_nothing_is_not_announced_and_the_turn_still_says_idle():
    seen, touch, publish = _recorder(None)  # Session.update wrote nothing: archived meanwhile
    bus = AsyncMock()

    result = await _run(_info("ses_sa6"), touch=touch, publish=publish, bus=bus)

    assert result.action == "stop"
    assert seen == [("session.status", "busy"), ("touch", "ses_sa6"), ("session.status", "idle"), ("touch", "ses_sa6")]
    assert any(call.args and call.args[0] is SessionIdle for call in bus.await_args_list)


@pytest.fixture
async def activity_storage(tmp_path: Path):
    await Storage.init(tmp_path / "activity.db")
    Session.invalidate_cache()
    yield
    for prefix in ("session:", "message:", "message_parts:"):
        await Storage.clear(prefix)
    Session.invalidate_cache()


@pytest.mark.asyncio
async def test_sa4_the_continued_session_goes_first_in_its_projects_list(activity_storage, tmp_path: Path):
    older = await Session.create(project_id="proj-sa4", directory=str(tmp_path), title="older")
    await asyncio.sleep(0.01)
    newer = await Session.create(project_id="proj-sa4", directory=str(tmp_path), title="newer")
    assert [s.id for s in await Session.list("proj-sa4")] == [newer.id, older.id]

    events: list[tuple[str, dict]] = []
    at_turn: dict = {}

    async def publish(event_type: str, properties: dict) -> None:
        events.append((event_type, properties))

    async def run_loop(*_args, **_kwargs):
        at_turn["order"] = [s.id for s in await Session.list("proj-sa4")]
        at_turn["updated"] = [props for kind, props in events if kind == "session.updated"]
        return LoopResult(action="stop")

    await asyncio.sleep(0.01)
    await _run(older, touch=Session.update, publish=publish, get_by_id=Session.get_by_id, run_loop=run_loop,
               session_touch=Session.touch)

    # Already first, and said so, as the turn starts; still first after it.
    assert at_turn == {"order": [older.id, newer.id], "updated": [{"id": older.id}]}
    assert [s.id for s in await Session.list("proj-sa4")] == [older.id, newer.id]
    assert [props for kind, props in events if kind == "session.updated"] == [{"id": older.id}, {"id": older.id}]


@pytest.mark.asyncio
async def test_sa7_the_loop_goes_by_update_not_by_touch_whose_return_says_nothing():
    """INC-CB90DF32 review: the Pro edition's Session takes this one's place, and its touch
    returns nothing even when it wrote; the loop must not take that for "nothing written"."""
    session = _info("ses_sa7")
    seen, update, publish = _recorder(session)
    pro_touch = AsyncMock(return_value=None)

    await _run(session, touch=update, publish=publish, session_touch=pro_touch)

    assert [entry for entry in seen if entry[0] == "session.updated"] == [("session.updated", "ses_sa7")] * 2
    pro_touch.assert_not_awaited()
