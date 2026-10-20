"""Runtime logs: no credentials in a line, bounded files and directory, and
the node that wrote a line when the deployment names its nodes."""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest

import flocks.utils.log as log_module
from flocks.utils.log import Log, scrub_credentials


@pytest.mark.parametrize(
    "line, secret",
    [
        ("api_key=sk-abcdefghijklmnopqrstu", "sk-abcdefghijklmnopqrstu"),  # secret-guard: allow 测脱敏的假数据
        ('{"Authorization": "Bearer abcdefghijklmno"}', "abcdefghijklmno"),
        ("dsn=postgresql://flocks:hunter2@db:5432/x", "hunter2"),  # secret-guard: allow 测脱敏的假数据
        ("connecting to postgres://app:s3cr3t@db/flocks", "s3cr3t"),  # secret-guard: allow 测脱敏的假数据
        ("auth_token=abc123", "abc123"),
        ("x-api-key: zzz-key", "zzz-key"),
        ("leaked AKIAABCDEFGHIJKLMNOP in text", "AKIAABCDEFGHIJKLMNOP"),  # secret-guard: allow 假的，测脱敏用
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEsecret\n-----END RSA PRIVATE KEY-----", "MIIEsecret"),  # secret-guard: allow 假的，测脱敏用
        # A quoted value goes as a whole, not just its first word.
        ('password="correct horse battery staple"', "battery staple"),
        ("secret='two words'", "words"),
        ('{"api_key": "sk live key with spaces"}', "with spaces"),
        # Every cookie in the header, not just the first one.
        ("Cookie: theme=dark; session=abcd1234efgh", "abcd1234efgh"),
        ("set-cookie: sid=zz9plural; Path=/; HttpOnly", "zz9plural"),
        # Quoted, with a blank after the colon: JSON, a dict repr, a quoted header.
        ('{"Cookie": "csrftoken=csrf-secret; sessionid=session-secret"}', "session-secret"),  # secret-guard: allow 测脱敏的假数据
        ("headers={'Cookie': 'csrftoken=csrf-secret; sessionid=session-secret'}", "session-secret"),  # secret-guard: allow 测脱敏的假数据
        ('Set-Cookie: "sessionid=session-secret; Path=/; HttpOnly"', "session-secret"),
        ('{"Set-Cookie": "sid=abc123secret; Path=/"}', "abc123secret"),
        ("Cookie:   spaced=cookie-secret", "cookie-secret"),
        # Quotes inside a header value belong to a cookie value, not a container.
        ('Cookie: theme="dark"; sessionid=session-secret', "session-secret"),
        ('Set-Cookie: sid="session-secret"; Path=/; HttpOnly', "session-secret"),
    ],
)
def test_credentials_never_reach_a_line(line, secret):
    assert secret not in scrub_credentials(line)


def test_structured_values_are_masked_by_their_key_before_serialisation(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path / "logs"))
    saved = (Log._writer, Log._error_writer, Log._log_dir_path, Log._log_date, Log._log_file)
    Log._log_dir_path = tmp_path / "logs"
    Log._open_daily_writers()
    try:
        Log.create(service="hygiene").info("call", {
            "credentials": {"user": "u", "password": "nested secret words"},
            "headers": {"Authorization": "Token plain-value-without-scheme"},
        })
        text = (tmp_path / "logs" / date.today().isoformat() / "flocks.log").read_text(encoding="utf-8")
    finally:
        for writer in (Log._writer, Log._error_writer):
            if writer is not None:
                writer.close()
        Log._writer, Log._error_writer, Log._log_dir_path, Log._log_date, Log._log_file = saved
    assert "nested secret words" not in text and "plain-value-without-scheme" not in text


def test_upgrade_text_log_is_scrubbed_too(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path / "logs"))
    log_module.append_upgrade_text_log("pip failed: https://user:hunter2@mirror.example/simple token=abc123")  # secret-guard: allow 测脱敏的假数据

    text = (tmp_path / "logs" / date.today().isoformat() / "errors.log").read_text(encoding="utf-8")
    assert "hunter2" not in text and "abc123" not in text


@pytest.mark.parametrize("line", ["max_tokens=4096 input_tokens=12", "tokens=1234 used", "session_id=ses_1 token_count=3"])
def test_ordinary_fields_stay_readable(line):
    assert scrub_credentials(line) == line


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    directory = tmp_path / "logs"
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(directory))
    saved = (Log._writer, Log._error_writer, Log._log_dir_path, Log._log_date, Log._log_file)
    Log._log_dir_path = directory
    Log._open_daily_writers()
    yield directory
    for writer in (Log._writer, Log._error_writer):
        if writer is not None:
            writer.close()
    Log._writer, Log._error_writer, Log._log_dir_path, Log._log_date, Log._log_file = saved


def test_written_lines_are_scrubbed_and_name_the_node(log_dir, monkeypatch):
    monkeypatch.setenv("FLOCKS_NODE_ID", "node-a")
    Log.create(service="hygiene").info("provider.call", {"api_key": "sk-abcdefghijklmnopqrstu", "model": "m1"})

    text = (log_dir / date.today().isoformat() / "flocks.log").read_text(encoding="utf-8")
    assert "sk-abcdefghijklmnopqrstu" not in text and "api_key=[REDACTED]" in text  # secret-guard: allow 测脱敏的假数据
    assert "node=node-a" in text and "model=m1" in text


def test_a_full_log_file_rotates(log_dir, monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_MAX_FILE_MB", "1")
    monkeypatch.setenv("FLOCKS_LOG_KEEP_ROTATIONS", "2")
    logger = Log.create(service="rotation")
    chunk = "x" * 1024
    for _ in range(3 * 1024 + 10):
        logger.info(chunk)

    day = log_dir / date.today().isoformat()
    assert (day / "flocks.log.1").exists() and (day / "flocks.log.2").exists()
    assert not (day / "flocks.log.3").exists()
    assert (day / "flocks.log").stat().st_size <= 1024 * 1024 + 4096


def test_the_total_size_cap_holds_with_age_based_cleanup_off(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_MAX_TOTAL_MB", "1")
    root = tmp_path / "logs"
    old_day = root / (date.today() - timedelta(days=5)).isoformat()
    old_day.mkdir(parents=True)
    (old_day / "flocks.log").write_bytes(b"x" * (2 * 1024 * 1024))
    (root / date.today().isoformat()).mkdir()

    Log._cleanup_sync(root, retention_days=0)

    assert not old_day.exists()


def test_the_log_directory_is_held_under_its_total_size(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_LOG_MAX_TOTAL_MB", "1")
    root = tmp_path / "logs"
    today = date.today()
    for offset in (3, 2, 1, 0):
        day = root / (today - timedelta(days=offset)).isoformat()
        day.mkdir(parents=True)
        (day / "flocks.log").write_bytes(b"x" * 600 * 1024)

    Log._cap_total_size(root)

    remaining = sorted(path.name for path in root.iterdir())
    assert remaining[-1] == today.isoformat(), "today's directory is never dropped"
    total = sum(item.stat().st_size for item in root.rglob("*") if item.is_file())
    assert total <= 1024 * 1024 or remaining == [today.isoformat()]
