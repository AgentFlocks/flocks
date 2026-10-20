"""Policy defaults, flocks.json tolerance and the logger's retention override."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from flocks.config.config import ConfigInfo
from flocks.retention import RetentionPolicy, apply_log_retention, policy_from_config
from flocks.utils.log import (
    Log,
    get_log_dir,
    get_log_retention_days,
    list_expired_log_paths,
    set_log_retention_days_override,
)


def _config(section=None) -> ConfigInfo:
    return ConfigInfo.model_validate({} if section is None else {"dataRetention": section})


def test_unset_policy_keeps_previous_behaviour():
    policy = policy_from_config(_config())

    assert (policy.log_days, policy.message_days, policy.session_days) == (30, 0, 0)


def test_unset_log_days_follow_the_environment(monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "7")
    assert policy_from_config(_config()).log_days == 7

    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "0")
    assert policy_from_config(_config()).log_days == 0

    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "soon")
    assert policy_from_config(_config()).log_days == 30


def test_configured_days_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "7")

    policy = policy_from_config(_config({"logDays": 90, "messageDays": 14, "sessionDays": 0}))

    assert (policy.log_days, policy.message_days, policy.session_days) == (90, 14, 0)


def test_hand_edited_values_do_not_break_config_loading():
    config = _config({"logDays": "14", "messageDays": "soon", "sessionDays": -5})

    policy = policy_from_config(config)

    # "14" reads as 14; unreadable and negative values fall back to the defaults.
    assert (policy.log_days, policy.message_days, policy.session_days) == (14, 0, 0)


@pytest.mark.parametrize(
    "section",
    [
        {"logDays": "--1", "messageDays": "²", "sessionDays": 7},  # double sign, superscript
        {"logDays": "", "messageDays": "   ", "sessionDays": "+"},  # empty / whitespace / sign-only
        {"logDays": " 90 ", "messageDays": 14.0, "sessionDays": "+30"},
        "soon",   # whole section is a string
        [],       # whole section is a list
        {"logDays": {"nested": 1}},
    ],
)
def test_bad_config_values_never_break_loading(section):
    # The whole config must still load; policy_from_config normalises the rest.
    config = _config(section)
    policy = policy_from_config(config)
    assert 0 <= policy.log_days <= 3650
    assert 0 <= policy.message_days <= 3650
    assert 0 <= policy.session_days <= 3650


async def test_preload_log_retention_applies_saved_policy_before_cleanup(monkeypatch, tmp_path):
    from flocks.config.config_writer import ConfigWriter
    from flocks.retention import preload_log_retention

    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "30")
    set_log_retention_days_override(None)
    assert get_log_retention_days() == 30

    ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})
    await preload_log_retention()
    # 0 = keep forever, applied before any first cleanup would run.
    assert get_log_retention_days() == 0


async def test_after_preload_only_the_scheduler_deletes_logs(monkeypatch):
    # Every Flocks process preloads; from then on its logger deletes nothing on its
    # own (startup or rollover), and expired logs go in the scheduler's fenced round.
    import flocks.utils.log as log_module
    from flocks.config.config_writer import ConfigWriter
    from flocks.retention import preload_log_retention
    from flocks.retention.scheduler import DataRetentionScheduler
    from flocks.utils.log import LogLevel

    log_dir = get_log_dir()
    old_dir = log_dir / (datetime.now() - timedelta(days=20)).date().isoformat()
    old_dir.mkdir(parents=True)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 7, "messageDays": 0, "sessionDays": 0}})

    class FakeDate:
        current = (datetime.now() - timedelta(days=1)).date()

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(log_module, "date", FakeDate)
    try:
        await preload_log_retention()
        await Log.init(print=False, dev=False, level=LogLevel.INFO)  # a CLI process starting
        assert old_dir.exists()
        FakeDate.current = datetime.now().date()
        Log.Default.info("next day")  # its day rollover
        assert old_dir.exists()

        report = await DataRetentionScheduler.run_once()
        assert report is not None and report.log_paths == 1 and not old_dir.exists()
    finally:
        if Log._writer:
            Log._writer.close()
            Log._writer = None
        if Log._error_writer:
            Log._error_writer.close()
            Log._error_writer = None
        Log._log_file = None
        Log._log_dir_path = None
        Log._log_date = None


async def test_stopping_the_scheduler_keeps_a_preloaded_logger_out_of_deleting(monkeypatch):
    # Shutdown can cross midnight. The logger only knows the period it read last,
    # older than what another process saved since, so stop() must not hand
    # deleting back to it.
    import flocks.utils.log as log_module
    from flocks.config.config_writer import ConfigWriter
    from flocks.retention import bump_policy_version, preload_log_retention
    from flocks.retention.scheduler import DataRetentionScheduler
    from flocks.utils.log import LogLevel, set_log_cleanup_managed

    log_dir = get_log_dir()
    old_dir = log_dir / (datetime.now() - timedelta(days=20)).date().isoformat()
    old_dir.mkdir(parents=True)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 7, "messageDays": 0, "sessionDays": 0}})

    class FakeDate:
        current = (datetime.now() - timedelta(days=1)).date()

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(log_module, "date", FakeDate)
    try:
        await preload_log_retention()
        await Log.init(print=False, dev=False, level=LogLevel.INFO)
        await DataRetentionScheduler.start()
        # Another process saves "keep forever"; this one runs no round before it stops.
        ConfigWriter._write_raw({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})
        await bump_policy_version()
        await DataRetentionScheduler.stop()

        FakeDate.current = datetime.now().date()
        Log.Default.info("next day, after shutdown")  # its day rollover
        assert old_dir.exists()

        from flocks.utils.log import log_cleanup_managed

        assert log_cleanup_managed() is True
        # Without a preload (library use, tests) stop() gives deleting back as before.
        set_log_cleanup_managed(False)
        await DataRetentionScheduler.start()
        assert log_cleanup_managed() is True
        await DataRetentionScheduler.stop()
        assert log_cleanup_managed() is False
    finally:
        await DataRetentionScheduler.stop()
        if Log._writer:
            Log._writer.close()
            Log._writer = None
        if Log._error_writer:
            Log._error_writer.close()
            Log._error_writer = None
        Log._log_file = None
        Log._log_dir_path = None
        Log._log_date = None


def test_oversized_values_are_capped():
    policy = policy_from_config(_config({"logDays": 99999}))

    assert policy.log_days == 3650


_ONES = {"log_days": 1, "message_days": 1, "session_days": 1}


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"dataRetention": None, "data_retention": _ONES}, (1, 1, 1)),
        ({"dataRetention": [], "data_retention": _ONES}, (1, 1, 1)),
        ({"dataRetention": "soon", "data_retention": _ONES}, (1, 1, 1)),
        ({"dataRetention": {"logDays": 9, "messageDays": 9, "sessionDays": 9}, "data_retention": _ONES}, (9, 9, 9)),
        ({"data_retention": _ONES}, (1, 1, 1)),
    ],
)
def test_both_spellings_collapse_to_one_value_that_survives_the_save_round_trip(payload, expected):
    # Config.update writes validate -> dump -> re-validate. A camelCase/snake_case
    # mix must resolve to the same single value at every step, and must never
    # leave the second spelling behind as an extra that gets written to disk —
    # otherwise one validation (the admin guard) and the saved value disagree.
    config = ConfigInfo.model_validate(payload)
    section = config.data_retention
    assert (section.log_days, section.message_days, section.session_days) == expected
    assert "data_retention" not in (config.model_extra or {})

    dumped = config.model_dump(by_alias=True, exclude_none=True)
    assert "data_retention" not in dumped
    again = ConfigInfo.model_validate(dumped).data_retention
    assert (again.log_days, again.message_days, again.session_days) == expected


@pytest.mark.parametrize(
    "values",
    [
        {"logDays": -1, "messageDays": 0, "sessionDays": 0},
        {"logDays": 30, "messageDays": 3651, "sessionDays": 0},
        {"logDays": 30, "messageDays": 0},
    ],
)
def test_policy_model_rejects_out_of_range_or_missing_days(values):
    with pytest.raises(ValidationError):
        RetentionPolicy(**values)


def test_policy_override_wins_over_environment_for_the_logger(monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "7")
    assert get_log_retention_days() == 7

    apply_log_retention(RetentionPolicy(logDays=60, messageDays=0, sessionDays=0))
    assert get_log_retention_days() == 60

    set_log_retention_days_override(None)
    assert get_log_retention_days() == 7


def _make_log_tree(log_dir, now):
    log_dir.mkdir(parents=True, exist_ok=True)
    for days_ago in (0, 1, 6, 7, 8, 40):
        day_dir = log_dir / (now - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        day_dir.mkdir()
        (day_dir / "flocks.log").write_text("x\n")
    old_stamp = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H%M%S")
    (log_dir / f"{old_stamp}.log").write_text("legacy\n")
    (log_dir / f"{old_stamp}.log.1").write_text("rotated\n")
    (log_dir / "backend.log").write_text("service\n")


def test_expired_log_paths_match_what_cleanup_removes(tmp_path):
    now = datetime.now()
    log_dir = tmp_path / "logs"
    _make_log_tree(log_dir, now)

    expired = list_expired_log_paths(log_dir, 7, now=now)
    expired_names = sorted(path.name for path in expired)
    old_stamp = (now - timedelta(days=40)).strftime("%Y-%m-%dT%H%M%S")
    assert expired_names == sorted([
        (now - timedelta(days=8)).strftime("%Y-%m-%d"),
        (now - timedelta(days=40)).strftime("%Y-%m-%d"),
        f"{old_stamp}.log",
        f"{old_stamp}.log.1",
    ])

    Log._cleanup_sync(log_dir, retention_days=7)
    remaining = sorted(path.name for path in log_dir.iterdir())
    assert remaining == sorted([
        (now - timedelta(days=days_ago)).strftime("%Y-%m-%d") for days_ago in (0, 1, 6, 7)
    ] + ["backend.log"])


def test_zero_days_keeps_every_log(tmp_path):
    now = datetime.now()
    log_dir = tmp_path / "logs"
    _make_log_tree(log_dir, now)
    before = sorted(path.name for path in log_dir.iterdir())

    assert list_expired_log_paths(log_dir, 0, now=now) == []
    Log._cleanup_sync(log_dir, retention_days=0)

    assert sorted(path.name for path in log_dir.iterdir()) == before


async def test_a_policy_save_waits_for_a_delete_in_flight():
    import asyncio

    from flocks.retention.policy import bump_policy_version, policy_unchanged, policy_version

    version = await policy_version()
    async with policy_unchanged(version) as unchanged:
        assert unchanged
        save = asyncio.create_task(bump_policy_version())
        await asyncio.sleep(0.3)
        assert not save.done()  # the save cannot overtake a delete holding the version
    assert await asyncio.wait_for(save, 10) == version + 1
    async with policy_unchanged(version) as unchanged:
        assert not unchanged  # deletes planned under the old policy stop from now on


async def test_day_rollover_leaves_log_cleanup_to_the_scheduler(monkeypatch):
    # flocks.json says 7 days, but the effective policy is "keep forever" from a
    # higher-priority layer (FLOCKS_CONFIG_CONTENT). The logger's own period is 7, so
    # while the scheduler runs, rollover deletes nothing; the scheduler's round reads
    # every layer and keeps the logs too.
    import flocks.utils.log as log_module
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter
    from flocks.retention.scheduler import DataRetentionScheduler
    from flocks.utils.log import LogLevel, set_log_cleanup_managed

    log_dir = get_log_dir()
    first_day = (datetime.now() - timedelta(days=1)).date()
    old_dir = log_dir / (datetime.now() - timedelta(days=20)).date().isoformat()
    old_dir.mkdir(parents=True)
    (old_dir / "flocks.log").write_text("old", encoding="utf-8")

    class FakeDate:
        current = first_day

        @classmethod
        def today(cls):
            return cls.current

    monkeypatch.setattr(log_module, "date", FakeDate)
    try:
        await Log.init(print=False, dev=False, level=LogLevel.INFO)
        ConfigWriter._write_raw({"dataRetention": {"logDays": 7, "messageDays": 0, "sessionDays": 0}})
        monkeypatch.setenv("FLOCKS_CONFIG_CONTENT", '{"dataRetention": {"logDays": 0}}')
        Config._global_config = None
        Config.clear_cache()
        apply_log_retention(RetentionPolicy(logDays=7, messageDays=0, sessionDays=0))
        set_log_cleanup_managed(True)  # what the running scheduler sets

        FakeDate.current = datetime.now().date()
        Log.Default.info("next day")
        assert old_dir.exists()

        report = await DataRetentionScheduler.run_once()
        assert report is not None and report.log_paths == 0
        assert old_dir.exists()
    finally:
        set_log_cleanup_managed(False)
        if Log._writer:
            Log._writer.close()
            Log._writer = None
        if Log._error_writer:
            Log._error_writer.close()
            Log._error_writer = None
        Log._log_file = None
        Log._log_dir_path = None
        Log._log_date = None


async def test_the_scheduler_follows_the_highest_priority_configuration(monkeypatch):
    # flocks.json says 7 days, the inline configuration (FLOCKS_CONFIG_CONTENT) says keep forever.
    from flocks.config.config import Config
    from flocks.config.config_writer import ConfigWriter
    from flocks.retention.scheduler import DataRetentionScheduler

    log_dir = get_log_dir()
    old_dir = log_dir / (datetime.now() - timedelta(days=20)).date().isoformat()
    old_dir.mkdir(parents=True)
    ConfigWriter._write_raw({"dataRetention": {"logDays": 7, "messageDays": 0, "sessionDays": 0}})
    monkeypatch.setenv("FLOCKS_CONFIG_CONTENT", '{"dataRetention": {"logDays": 0}}')
    Config._global_config = None
    Config.clear_cache()

    report = await DataRetentionScheduler.run_once()

    assert report is not None and report.log_paths == 0 and old_dir.exists()

