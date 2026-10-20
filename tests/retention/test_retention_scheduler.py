"""The scheduler reads the latest on-disk policy each run and records the result."""

import time

from flocks.config.config import Config, ConfigInfo
from flocks.config.config_writer import ConfigWriter
from flocks.retention.policy import bump_policy_version
from flocks.retention.scheduler import DataRetentionScheduler
from flocks.session.session import Session
from flocks.storage.storage import Storage

DAY_MS = 86_400_000


async def _aged_idle_session(tmp_path, days: int) -> Session:
    session = await Session.create(project_id="proj", directory=str(tmp_path))
    key = f"session:{session.project_id}:{session.id}"
    stored = await Storage.get(key)
    old = int(time.time() * 1000) - days * DAY_MS
    stored["time"]["created"] = old
    stored["time"]["updated"] = old
    await Storage.set(key, stored, "session")
    Session.invalidate_cache()
    return session


async def test_run_once_uses_latest_on_disk_policy_despite_stale_cache(tmp_path):
    session = await _aged_idle_session(tmp_path, days=60)

    # Disk now says: delete sessions idle for 30 days.
    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 30}})
    # Simulate another worker whose cache still holds the old "keep forever".
    Config._cached_config = ConfigInfo.model_validate(
        {"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}}
    )

    report = await DataRetentionScheduler.run_once()

    assert report is not None and report.sessions == 1
    assert await Session.get_by_id_unfiltered(session.id) is None
    # The result is persisted for the settings page to read back.
    last = await DataRetentionScheduler.last_run()
    assert last and last.get("sessions") == 1


async def test_run_once_keep_forever_deletes_nothing(tmp_path):
    session = await _aged_idle_session(tmp_path, days=900)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})

    report = await DataRetentionScheduler.run_once()

    assert report is not None and report.sessions == 0
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_a_policy_saved_by_another_worker_after_planning_stops_the_round(tmp_path, monkeypatch):
    import flocks.retention.cleanup as cleanup

    session = await _aged_idle_session(tmp_path, days=60)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 30}})
    real_collect = cleanup.collect_targets

    async def collect_then_saved_elsewhere(policy, *, now=None):
        targets = await real_collect(policy, now=now)
        # An admin on another worker switches to "keep forever": the file and
        # the shared version move, this worker's in-process generation does not.
        ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})
        await bump_policy_version()
        return targets

    monkeypatch.setattr(cleanup, "collect_targets", collect_then_saved_elsewhere)

    report = await DataRetentionScheduler.run_once()

    assert report is not None and report.sessions == 0
    assert await Session.get_by_id_unfiltered(session.id) is not None


async def test_the_round_reads_the_policy_past_a_cache_refilled_before_a_save(tmp_path, monkeypatch):
    # A request refills the configuration cache with the old policy right after the
    # round reads the shared version an admin's save already moved: the round must
    # still use the saved policy, not that cache.
    import flocks.retention.scheduler as scheduler

    session = await _aged_idle_session(tmp_path, days=60)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})
    stale = ConfigInfo.model_validate({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 30}})
    real_version = scheduler.policy_version

    async def version_then_stale_cache():
        version = await real_version()
        Config._cached_config = stale
        return version

    monkeypatch.setattr(scheduler, "policy_version", version_then_stale_cache)

    report = await DataRetentionScheduler.run_once()

    assert report is not None and report.sessions == 0
    assert await Session.get_by_id_unfiltered(session.id) is not None

