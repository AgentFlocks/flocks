"""What the retention pass deletes, and what it must leave alone."""

import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from flocks.retention import RetentionPolicy, apply_policy, collect_targets, expired_turn_prefix
from flocks.session.message import Message, MessageRole
from flocks.session.recorder import Recorder
from flocks.session.session import Session
from flocks.storage.storage import Storage
from flocks.utils.log import get_log_dir

DAY_MS = 86_400_000
NOW = time.time()
NOW_MS = int(NOW * 1000)


def _days_ago_ms(days: float) -> int:
    return NOW_MS - int(days * DAY_MS)


def _policy(*, logs: int = 0, messages: int = 0, sessions: int = 0) -> RetentionPolicy:
    return RetentionPolicy(logDays=logs, messageDays=messages, sessionDays=sessions)


async def _new_session(tmp_path: Path, *, parent_id=None, project_id: str = "proj"):
    return await Session.create(project_id=project_id, directory=str(tmp_path), parent_id=parent_id)


async def _set_session_time(session, *, created_ms: int, updated_ms: int) -> None:
    key = f"session:{session.project_id}:{session.id}"
    stored = await Storage.get(key)
    stored["time"]["created"] = created_ms
    stored["time"]["updated"] = updated_ms
    await Storage.set(key, stored, "session")
    Session.invalidate_cache()


async def _turn(session_id: str, user_ms: int, reply_ms: int, text: str) -> list[str]:
    user = await Message.create(session_id, MessageRole.USER, f"q {text}", time={"created": user_ms})
    reply = await Message.create(
        session_id,
        MessageRole.ASSISTANT,
        f"a {text}",
        parentID=user.id,
        time={"created": reply_ms, "completed": reply_ms},
    )
    return [user.id, reply.id]


async def _stored_message_ids(session_id: str) -> list[str]:
    raw = await Storage.get(f"message:{session_id}")
    return [item["id"] for item in raw or []]


# ---------------------------------------------------------------- turn planning


def test_turn_prefix_takes_only_whole_turns_older_than_the_cutoff():
    cutoff = _days_ago_ms(10)
    messages = [
        {"id": "u1", "role": "user", "time": {"created": _days_ago_ms(30)}},
        {"id": "a1", "role": "assistant", "time": {"created": _days_ago_ms(30)}},
        {"id": "u2", "role": "user", "time": {"created": _days_ago_ms(20)}},
        # The reply landed after the cutoff, so the whole second turn stays.
        {"id": "a2", "role": "assistant", "time": {"created": _days_ago_ms(20), "completed": _days_ago_ms(5)}},
        {"id": "u3", "role": "user", "time": {"created": _days_ago_ms(40)}},
    ]

    assert expired_turn_prefix(messages, cutoff) == ["u1", "a1"]


def test_turn_prefix_stops_at_revert_point_and_missing_timestamps():
    cutoff = _days_ago_ms(1)
    old = {"created": _days_ago_ms(30)}
    messages = [
        {"id": "a0", "role": "assistant", "time": old},
        {"id": "u1", "role": "user", "time": old},
        {"id": "u2", "role": "user", "time": old},
        {"id": "u3", "role": "user"},
        {"id": "u4", "role": "user", "time": old},
    ]

    assert expired_turn_prefix(messages, cutoff) == ["a0", "u1", "u2"]
    assert expired_turn_prefix(messages, cutoff, stop_at_message_id="u2") == ["a0", "u1"]


# ---------------------------------------------------------------- sessions


async def test_session_retention_deletes_idle_trees_and_keeps_recent_or_busy_ones(tmp_path, monkeypatch):
    old = await _new_session(tmp_path)
    old_child = await _new_session(tmp_path, parent_id=old.id)
    kept_by_child = await _new_session(tmp_path)
    recent_child = await _new_session(tmp_path, parent_id=kept_by_child.id)
    recent = await _new_session(tmp_path)
    busy = await _new_session(tmp_path)
    await _turn(old.id, _days_ago_ms(45), _days_ago_ms(45), "old")
    for session in (old, old_child, kept_by_child, busy):
        await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(45))
    await _set_session_time(recent_child, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(2))
    await _set_session_time(recent, created_ms=_days_ago_ms(3), updated_ms=_days_ago_ms(1))

    from flocks.session.session_loop import SessionLoop

    real_is_running = SessionLoop.is_running
    monkeypatch.setattr(
        SessionLoop,
        "is_running",
        classmethod(lambda cls, session_id: session_id == busy.id or real_is_running(session_id)),
    )

    policy = _policy(sessions=30)
    preview = (await collect_targets(policy, now=NOW)).summary()
    report = await apply_policy(policy, now=NOW)

    assert preview["sessions"] == 2 and report.sessions == 2
    assert preview["skippedBusy"] == 1 and report.skipped_busy == 1
    remaining = {session.id for session in await Session.list_all_unfiltered()}
    assert remaining == {kept_by_child.id, recent_child.id, recent.id, busy.id}
    assert await Storage.get(f"message:{old.id}") is None


async def test_keep_forever_deletes_nothing(tmp_path):
    session = await _new_session(tmp_path)
    await _turn(session.id, _days_ago_ms(900), _days_ago_ms(900), "ancient")
    await _set_session_time(session, created_ms=_days_ago_ms(900), updated_ms=_days_ago_ms(900))

    report = await apply_policy(_policy(), now=NOW)

    assert (report.sessions, report.messages, report.log_paths, report.record_files) == (0, 0, 0, 0)
    assert await Session.get_by_id_unfiltered(session.id) is not None
    assert len(await _stored_message_ids(session.id)) == 2


# ---------------------------------------------------------------- messages


async def test_message_retention_drops_old_turns_and_keeps_the_session(tmp_path):
    session = await _new_session(tmp_path)
    first = await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "first")
    second = await _turn(session.id, _days_ago_ms(40), _days_ago_ms(40), "second")
    third = await _turn(session.id, _days_ago_ms(2), _days_ago_ms(1), "third")
    await _set_session_time(session, created_ms=_days_ago_ms(90), updated_ms=_days_ago_ms(1))

    policy = _policy(messages=30)
    preview = (await collect_targets(policy, now=NOW)).summary()
    report = await apply_policy(policy, now=NOW)

    assert preview["messages"] == 4 and report.messages == 4
    assert await _stored_message_ids(session.id) == third
    for message_id in first + second:
        assert await Storage.get(f"message_parts:{session.id}:{message_id}") is None
    assert await Storage.get(f"message_parts:{session.id}:{third[0]}") is not None
    # The cache seen by the WebUI agrees with storage.
    assert [message.id for message in await Message.list(session.id, include_archived=True)] == third
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_message_retention_skips_running_sessions(tmp_path, monkeypatch):
    session = await _new_session(tmp_path)
    await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "old")
    await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "new")
    await _set_session_time(session, created_ms=_days_ago_ms(90), updated_ms=_days_ago_ms(1))

    from flocks.session.session_loop import SessionLoop

    monkeypatch.setattr(
        SessionLoop,
        "is_running",
        classmethod(lambda cls, session_id: session_id == session.id),
    )

    report = await apply_policy(_policy(messages=30), now=NOW)

    assert report.messages == 0 and report.skipped_busy == 1
    assert len(await _stored_message_ids(session.id)) == 4


async def test_delete_many_removes_messages_parts_and_search_rows_in_one_go(tmp_path):
    session = await _new_session(tmp_path)
    old = await _turn(session.id, _days_ago_ms(5), _days_ago_ms(5), "needle-old")
    new = await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "needle-new")

    deleted = await Message.delete_many(session.id, old + ["msg_missing"])

    assert deleted == 2
    assert await _stored_message_ids(session.id) == new
    for message_id in old:
        assert await Storage.get(f"message_parts:{session.id}:{message_id}") is None
    if Storage.session_search_available():
        async with Storage.transaction() as db:
            rows = await db.fetch_all("SELECT message_id FROM session_transcript_index_state")
        indexed = {row[0] for row in rows}
        assert indexed.isdisjoint(old)
        assert set(new) <= indexed
    Message.invalidate_cache(session.id)
    assert [message.id for message in await Message.list(session.id)] == new


# ---------------------------------------------------------------- logs and records


def _write_record(directory: Path, name: str, age_days: float) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.jsonl"
    path.write_text('{"type": "session.message"}\n')
    stamp = NOW - age_days * 86_400
    os.utime(path, (stamp, stamp))
    return path


async def test_log_retention_removes_old_logs_and_stale_records(tmp_path):
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.fromtimestamp(NOW)
    for days_ago in (0, 3, 20):
        day_dir = log_dir / (today - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        day_dir.mkdir(exist_ok=True)
        (day_dir / "flocks.log").write_text("x" * 100)
    paths = Recorder.paths()
    stale_session = _write_record(paths.session_dir, "ses_stale", 20)
    fresh_session = _write_record(paths.session_dir, "ses_fresh", 1)
    stale_workflow = _write_record(paths.workflow_dir, "exec_stale", 20)

    policy = _policy(logs=7)
    preview = (await collect_targets(policy, now=NOW)).summary()
    report = await apply_policy(policy, now=NOW)

    assert preview["logPaths"] == 1 and report.log_paths == 1
    assert preview["recordFiles"] == 2 and report.record_files == 2
    assert not stale_session.exists() and not stale_workflow.exists()
    assert fresh_session.exists()
    assert sorted(path.name for path in log_dir.iterdir()) == sorted(
        (today - timedelta(days=days_ago)).strftime("%Y-%m-%d") for days_ago in (0, 3)
    )


async def test_records_of_deleted_sessions_go_with_them(tmp_path):
    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    paths = Recorder.paths()
    # Written recently, so log retention alone would keep it.
    record = _write_record(paths.session_dir, session.id, 0)
    other = _write_record(paths.session_dir, "ses_unrelated", 0)

    report = await apply_policy(_policy(sessions=30), now=NOW)

    assert report.sessions == 1 and report.record_files == 1
    assert not record.exists() and other.exists()


async def test_session_used_again_after_planning_is_not_deleted(tmp_path, monkeypatch):
    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))

    import flocks.retention.cleanup as cleanup

    real_collect = cleanup.collect_targets

    async def collect_then_touch(policy, *, now=None):
        targets = await real_collect(policy, now=now)
        # The user opens the session between planning and deleting.
        await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=NOW_MS)
        return targets

    monkeypatch.setattr(cleanup, "collect_targets", collect_then_touch)

    report = await apply_policy(_policy(sessions=30), now=NOW)

    assert report.sessions == 0
    assert await Session.get_by_id_unfiltered(session.id) is not None


# ---------------------------------------------------------------- CR fixes


async def test_delete_many_keeps_messages_only_in_storage(tmp_path):
    # Regression: a message that reached storage but not this process's cache
    # (e.g. another worker) must not be dropped when deleting an older one.
    from flocks.session import message_pages

    session = await _new_session(tmp_path)
    old = await _turn(session.id, _days_ago_ms(5), _days_ago_ms(5), "old")
    await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "cached-new")
    # Commit a message the way another worker does, bypassing this process's cache.
    stored = await Storage.get(f"message:{session.id}")
    injected = dict(stored[-1], id="msg_only_in_storage")
    async with Storage.transaction() as db:
        await message_pages.lock_session(db, session.id)
        await message_pages.upsert(db, session.id, injected)
        await message_pages.sync_document(db, session.id)
        await message_pages.bump_revision(db, session.id)

    deleted = await Message.delete_many(session.id, old)

    assert deleted == 2
    remaining = await _stored_message_ids(session.id)
    assert "msg_only_in_storage" in remaining  # survived, not clobbered by a stale cache
    assert not any(mid in remaining for mid in old)
    page, *_ = await Message.list_page_with_parts(session.id, limit=50)
    assert "msg_only_in_storage" in [item.info.id for item in page]


async def test_bulk_delete_leaves_pages_and_later_writes_without_the_deleted_messages(tmp_path):
    # Regression: pages read the v2 message directory and every message write
    # rebuilds the export document from it, so a bulk delete that only rewrote
    # the document still showed the messages in pages and the next write
    # brought them back. Their turn diffs go with them.
    from flocks.session import message_pages

    session = await _new_session(tmp_path)
    old = await _turn(session.id, _days_ago_ms(5), _days_ago_ms(5), "old")
    new = await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "new")
    await Storage.set(f"message_diff:{session.id}:{old[0]}", [{"file": "a"}], "session_diff")
    await Storage.set(f"message_diff:{session.id}:{new[0]}", [{"file": "b"}], "session_diff")
    async with Storage.transaction() as db:
        revision = await message_pages.revision(db, session.id)

    assert await Message.delete_many(session.id, old) == 2

    page, *_ = await Message.list_page_with_parts(session.id, limit=50)
    assert [item.info.id for item in page] == new
    assert await Storage.get(f"message_diff:{session.id}:{old[0]}") is None
    assert await Storage.get(f"message_diff:{session.id}:{new[0]}") == [{"file": "b"}]
    async with Storage.transaction() as db:
        # Other workers reload their caches when the revision moves.
        assert await message_pages.revision(db, session.id) > revision

    later = await _turn(session.id, NOW_MS, NOW_MS, "later")

    assert await _stored_message_ids(session.id) == new + later
    page, *_ = await Message.list_page_with_parts(session.id, limit=50)
    assert [item.info.id for item in page] == new + later


async def test_bulk_delete_trims_a_legacy_parts_blob(tmp_path):
    session = await _new_session(tmp_path)
    old = await _turn(session.id, _days_ago_ms(5), _days_ago_ms(5), "old")
    new = await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "new")
    blob = {}
    for message_id in old + new:
        key = f"message_parts:{session.id}:{message_id}"
        blob[message_id] = await Storage.get(key)
        await Storage.delete(key)
    await Storage.set(f"message_parts:{session.id}", blob, "message_parts")
    Message.invalidate_cache(session.id)

    assert await Message.delete_many(session.id, old) == 2

    remaining = await Storage.get(f"message_parts:{session.id}")
    assert sorted(remaining) == sorted(new)
    page, *_ = await Message.list_page_with_parts(session.id, limit=50)
    assert [item.info.id for item in page] == new
    assert [part.text for item in page for part in item.parts] == ["q new", "a new"]


async def test_message_cleanup_of_a_session_deleted_meanwhile_deletes_nothing(tmp_path):
    session = await _new_session(tmp_path)
    old = await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "old")
    assert await Session.delete(session.project_id, session.id)

    assert await Message.delete_many(session.id, old) == 0
    assert await Message.delete_selected(session.id, lambda stored, s: old) == 0


async def test_delete_selected_skips_running_session(tmp_path, monkeypatch):
    session = await _new_session(tmp_path)
    ids = await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "old")

    from flocks.session.session_loop import SessionLoop

    monkeypatch.setattr(SessionLoop, "is_running", classmethod(lambda cls, sid: sid == session.id))

    result = await Message.delete_selected(session.id, lambda stored, s: [m["id"] for m in stored])

    assert result == -1  # skipped because running
    assert await _stored_message_ids(session.id) == ids  # nothing deleted


async def test_delete_selected_honors_revert_set_after_planning(tmp_path):
    from flocks.session.session import Session

    session = await _new_session(tmp_path)
    first = await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "first")
    second = await _turn(session.id, _days_ago_ms(80), _days_ago_ms(80), "second")
    # A revert is set pointing into the range the planner would have deleted.
    await Session.update(session.project_id, session.id, revert={"messageID": second[0]})

    cutoff = _days_ago_ms(10)

    def selector(stored, live_session):
        stop = live_session.revert.message_id if live_session and live_session.revert else None
        return expired_turn_prefix(stored, cutoff, stop_at_message_id=stop)

    deleted = await Message.delete_selected(session.id, selector)

    # Only the first turn goes; the second turn is protected by the live revert.
    assert deleted == len(first) == 2
    assert await _stored_message_ids(session.id) == second


async def test_session_running_in_another_worker_is_not_deleted(tmp_path):
    from flocks.session.ownership import SessionExecutionLease

    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    # Another worker runs it: its loop holds the shared execution lease, while
    # neither its SessionLoop nor its status is visible in this process.
    lease = await SessionExecutionLease.acquire(session.id)
    try:
        report = await apply_policy(_policy(sessions=30), now=NOW)
    finally:
        await lease.release()

    assert report.sessions == 0 and report.skipped_busy == 1
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_messages_of_a_session_running_in_another_worker_are_kept(tmp_path):
    from flocks.session.ownership import SessionExecutionLease

    session = await _new_session(tmp_path)
    await _turn(session.id, _days_ago_ms(90), _days_ago_ms(90), "old")
    await _turn(session.id, _days_ago_ms(1), _days_ago_ms(1), "new")
    await _set_session_time(session, created_ms=_days_ago_ms(90), updated_ms=_days_ago_ms(1))
    lease = await SessionExecutionLease.acquire(session.id)
    try:
        report = await apply_policy(_policy(messages=30), now=NOW)
    finally:
        await lease.release()

    assert report.messages == 0 and report.skipped_busy == 1
    assert len(await _stored_message_ids(session.id)) == 4


async def test_no_runner_can_take_a_session_while_retention_deletes_it(tmp_path, monkeypatch):
    from flocks.session.ownership import SessionExecutionLease

    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    real_delete = Session.delete
    attempts = []

    async def delete_while_a_runner_tries_to_start(project_id, session_id, **kwargs):
        attempts.append(await SessionExecutionLease.acquire(session_id))
        return await real_delete(project_id, session_id, **kwargs)

    monkeypatch.setattr(Session, "delete", delete_while_a_runner_tries_to_start)

    report = await apply_policy(_policy(sessions=30), now=NOW)

    assert attempts == [None]
    assert report.sessions == 1


async def test_a_synchronous_operation_on_a_session_keeps_retention_away(tmp_path):
    # /shell and revert run outside a runner; they hold the same execution lease,
    # so a cleanup in another process cannot delete the session meanwhile.
    from flocks.session.ownership import SessionExecutionLease, current_lease
    from flocks.session.session import SessionInactiveError

    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))

    async with Session.active_operation(session.id):
        assert current_lease(session.id) is not None
        report = await apply_policy(_policy(sessions=30), now=NOW)
    assert report.sessions == 0 and report.skipped_busy == 1
    assert await Session.get_by_id_unfiltered(session.id) is not None

    runner = await SessionExecutionLease.acquire(session.id)  # running in another process
    try:
        with pytest.raises(SessionInactiveError, match="is running"):
            async with Session.active_operation(session.id):
                pass
    finally:
        await runner.release()


async def test_unrevert_keeps_retention_away_while_it_restores_files(tmp_path, monkeypatch):
    # Unrevert restores the snapshot and clears the revert state only at the end, so
    # the session still looks 60 days old meanwhile: it holds the execution lease.
    from flocks.session.lifecycle.revert import SessionRevert
    from flocks.session.ownership import SessionExecutionLease, current_lease
    from flocks.session.session import SessionInactiveError
    from flocks.snapshot import Snapshot

    session = await _new_session(tmp_path)
    await Session.set_revert(session.project_id, session.id, "msg_reverted", snapshot="snap_before")
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    during = {}

    async def restore(project_id, worktree, snapshot):
        during["lease"] = current_lease(session.id) is not None
        during["report"] = await apply_policy(_policy(sessions=30), now=NOW)
        return True

    monkeypatch.setattr(Snapshot, "restore", staticmethod(restore))

    restored = await SessionRevert.unrevert(session.id)

    assert during["lease"] is True
    assert during["report"].sessions == 0 and during["report"].skipped_busy == 1
    assert restored is not None and restored.revert is None

    # Running in another process: refused, and the revert state stays.
    await Session.set_revert(session.project_id, session.id, "msg_reverted", snapshot="snap_before")
    runner = await SessionExecutionLease.acquire(session.id)
    try:
        with pytest.raises(SessionInactiveError, match="is running"):
            await SessionRevert.unrevert(session.id)
    finally:
        await runner.release()
    assert (await Session.get_by_id_unfiltered(session.id)).revert is not None


async def test_a_policy_saved_elsewhere_stops_deletes_planned_under_the_old_one(tmp_path, monkeypatch):
    import flocks.retention.cleanup as cleanup
    from flocks.retention.policy import bump_policy_version, policy_version

    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    version = await policy_version()
    real_collect = cleanup.collect_targets

    async def collect_then_saved_elsewhere(policy, *, now=None):
        targets = await real_collect(policy, now=now)
        await bump_policy_version()
        return targets

    monkeypatch.setattr(cleanup, "collect_targets", collect_then_saved_elsewhere)

    report = await apply_policy(_policy(sessions=30), now=NOW, policy_version=version)

    assert report.sessions == 0
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_record_delete_respects_mtime_touched_after_planning(tmp_path):
    from flocks.session.recorder import Recorder

    paths = Recorder.paths()
    path = _write_record(paths.session_dir, "ses_touched", 20)
    cutoff_s = NOW - 7 * 86_400

    # Touched (written to) after planning: now newer than the cutoff.
    os.utime(path, (NOW, NOW))
    outcome, freed = await Recorder.delete_file(path, older_than=cutoff_s)
    assert outcome == "skipped" and freed == 0 and path.exists()

    # Genuinely old: deleted.
    os.utime(path, (NOW - 20 * 86_400, NOW - 20 * 86_400))
    outcome, freed = await Recorder.delete_file(path, older_than=cutoff_s)
    assert outcome == "deleted" and freed > 0 and not path.exists()


def _retainable_before(cutoff_ms: int):
    """A retention-style precheck: keep the tree if any session is newer than the cutoff."""
    def ok_to_delete(tree) -> bool:
        return all(
            max(int(s.time.updated or 0), int(s.time.created or 0)) < cutoff_ms
            for s in tree
        )
    return ok_to_delete


async def test_session_delete_aborts_when_touched_since_planning(tmp_path):
    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=NOW_MS)  # just used

    # Retention planned under a 30-day cutoff; the precheck under the lock sees
    # the fresh activity and refuses.
    deleted = await Session.delete(
        session.project_id, session.id, precheck=_retainable_before(_days_ago_ms(30)),
    )

    assert deleted is False
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_apply_policy_stops_deleting_when_superseded(tmp_path):
    a = await _new_session(tmp_path)
    b = await _new_session(tmp_path)
    for s in (a, b):
        await _set_session_time(s, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))

    calls = {"n": 0}

    def should_continue() -> bool:
        calls["n"] += 1
        return calls["n"] <= 1  # true for the first session, false after

    report = await apply_policy(_policy(sessions=30), now=NOW, should_continue=should_continue)

    # The policy changed mid-run; only the first session was deleted.
    assert report.sessions == 1
    remaining = {s.id for s in await Session.list_all_unfiltered()}
    assert len(remaining & {a.id, b.id}) == 1


async def test_record_delete_failure_is_counted_as_error(tmp_path, monkeypatch):
    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(60))
    paths = Recorder.paths()
    _write_record(paths.session_dir, session.id, 0)

    from flocks.session.recorder import Recorder as Rec

    async def failing_delete(path, *, older_than=None):
        return ("failed", 0)

    monkeypatch.setattr(Rec, "delete_file", classmethod(lambda cls, path, **kw: failing_delete(path, **kw)))

    report = await apply_policy(_policy(sessions=30), now=NOW)

    # The session row went, but its record file could not be deleted -> an error.
    assert report.sessions == 1
    assert report.errors >= 1


async def test_delete_abort_does_not_stop_clear_or_bump_generation(tmp_path, monkeypatch):
    # Retention must not abort/clear a session it then decides to keep, and must
    # not even bump its lifecycle generation: the precheck runs under the lock
    # before the bump, so an in-flight write carrying the old generation survives.
    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=NOW_MS)  # touched

    stop_calls = {"n": 0}
    real_stop = Session._stop_session_tree_for_archive

    async def spy_stop(sessions, **kwargs):
        stop_calls["n"] += 1
        return await real_stop(sessions, **kwargs)

    monkeypatch.setattr(Session, "_stop_session_tree_for_archive", classmethod(lambda cls, s, **k: spy_stop(s, **k)))

    gen_before = Session.lifecycle_generation(session.id)
    deleted = await Session.delete(
        session.project_id, session.id, precheck=_retainable_before(_days_ago_ms(30)),
    )

    assert deleted is False
    assert stop_calls["n"] == 0  # bailed out before stopping the session
    assert Session.lifecycle_generation(session.id) == gen_before  # generation untouched
    assert await Session.get_by_id_unfiltered(session.id) is not None


def _status(target_id: str, type_when_match: str, type_otherwise: str = "idle"):
    """A SessionStatus.get_for_session stand-in: ``type_when_match`` for one session."""
    from types import SimpleNamespace

    def get_for_session(cls, sid, preferred_instance_id=None):
        return SimpleNamespace(type=type_when_match if sid == target_id else type_otherwise)

    return classmethod(get_for_session)


async def test_busy_check_covers_non_idle_status_without_a_running_loop(tmp_path, monkeypatch):
    # The retention "leave it alone" predicate must catch a session that is doing
    # work under a non-idle status (dreaming/compacting) even when no SessionLoop
    # is registered for it — looking only at the loop would miss these.
    from flocks.retention.cleanup import _session_is_busy
    from flocks.session.core.status import SessionStatus
    from flocks.session.session_loop import SessionLoop

    session = await _new_session(tmp_path)
    monkeypatch.setattr(SessionLoop, "is_running", classmethod(lambda cls, sid: False))

    monkeypatch.setattr(SessionStatus, "get_for_session", _status(session.id, "dreaming"))
    assert _session_is_busy(session) is True

    monkeypatch.setattr(SessionStatus, "get_for_session", _status(session.id, "idle"))
    assert _session_is_busy(session) is False


async def test_session_turning_busy_after_planning_is_left_alone(tmp_path, monkeypatch):
    # Idle when retention plans, but dreaming (a non-idle status with no live
    # loop) by the time the delete claims the tree. The precheck that runs under
    # the tree's lifecycle locks must see it and veto: nothing deleted, nothing
    # stopped, and the lifecycle generation left alone.
    from types import SimpleNamespace

    from flocks.session.core.status import SessionStatus
    from flocks.session.session_loop import SessionLoop

    session = await _new_session(tmp_path)
    await _set_session_time(session, created_ms=_days_ago_ms(60), updated_ms=_days_ago_ms(45))
    monkeypatch.setattr(SessionLoop, "is_running", classmethod(lambda cls, sid: False))

    claiming = {"on": False}
    real_begin = Session._begin_lifecycle_transition.__func__

    async def spy_begin(cls, project_id, session_id, **kwargs):
        claiming["on"] = True  # from the claim onwards the session reports "dreaming"
        return await real_begin(cls, project_id, session_id, **kwargs)

    monkeypatch.setattr(Session, "_begin_lifecycle_transition", classmethod(spy_begin))
    monkeypatch.setattr(
        SessionStatus,
        "get_for_session",
        classmethod(lambda cls, sid, preferred_instance_id=None: SimpleNamespace(
            type="dreaming" if claiming["on"] and sid == session.id else "idle"
        )),
    )
    gen_before = Session.lifecycle_generation(session.id)

    report = await apply_policy(_policy(sessions=30), now=NOW)

    assert claiming["on"]  # planning passed it; the delete really tried to claim the tree
    assert report.sessions == 0
    assert report.skipped_busy >= 1
    assert Session.lifecycle_generation(session.id) == gen_before
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_delete_selected_skips_non_idle_status_via_is_busy(tmp_path, monkeypatch):
    # Message trimming uses the same full busy check: a dreaming session with no
    # running loop is left alone (return -1), not trimmed.
    from flocks.retention.cleanup import _session_is_busy
    from flocks.session.core.status import SessionStatus
    from flocks.session.session_loop import SessionLoop

    session = await _new_session(tmp_path)
    await _turn(session.id, _days_ago_ms(45), _days_ago_ms(45), "old")
    monkeypatch.setattr(SessionLoop, "is_running", classmethod(lambda cls, sid: False))
    monkeypatch.setattr(SessionStatus, "get_for_session", _status(session.id, "dreaming"))

    result = await Message.delete_selected(
        session.id,
        lambda stored, s: [m["id"] for m in stored],
        is_busy=_session_is_busy,
    )

    assert result == -1  # busy: left alone
    assert len(await _stored_message_ids(session.id)) == 2  # nothing trimmed
