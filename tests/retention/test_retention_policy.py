"""Policy defaults, flocks.json tolerance and the logger's retention override."""

from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from flocks.config.config import ConfigInfo
from flocks.retention import RetentionPolicy, apply_log_retention, policy_from_config
from flocks.utils.log import (
    Log,
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
    from flocks.config.config import Config, ConfigInfo
    from flocks.retention import preload_log_retention

    monkeypatch.setenv("FLOCKS_LOG_RETENTION_DAYS", "30")
    set_log_retention_days_override(None)
    assert get_log_retention_days() == 30

    async def _fake_get():
        return ConfigInfo.model_validate({"dataRetention": {"logDays": 0, "messageDays": 0, "sessionDays": 0}})

    monkeypatch.setattr(Config, "get", classmethod(lambda cls: _fake_get()))
    await preload_log_retention()
    # 0 = keep forever, applied before any first cleanup would run.
    assert get_log_retention_days() == 0


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
