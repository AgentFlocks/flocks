"""Credentials never reach a log line, and log upkeep problems raise alerts (G18).

* records written through the standard library -- the root logger, uvicorn's
  access and error logs, any third-party library -- are scrubbed like the
  ones written through Log, exception text included;
* uvicorn's access formatter still gets the request line it unpacks;
* dropping logs to stay within the disk budget raises an alert.
"""

from __future__ import annotations

import logging

import pytest

from flocks.observability import alerts
from flocks.utils import log as log_module
from flocks.utils.log import install_log_record_scrubber, scrub_log_record

SECRET = "sk-live1234567890abcdefXYZ"  # secret-guard: allow 误报：测试用假密钥，验证脱敏


@pytest.fixture(autouse=True)
def fresh_alerts():
    alerts.reset_alerts()
    yield
    alerts.reset_alerts()


def _emit(logger_name: str, msg, *args, exc_info=None) -> logging.LogRecord:
    install_log_record_scrubber()
    captured: list[logging.LogRecord] = []

    class _Keep(logging.Handler):
        def emit(self, record):
            captured.append(record)

    logger = logging.getLogger(logger_name)
    handler = _Keep()
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        logger.info(msg, *args, exc_info=exc_info)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous)
    return captured[0]


def test_the_root_logger_and_third_parties_are_scrubbed():
    record = _emit("some.library", "calling with api_key=%s and Authorization: Bearer %s", SECRET, "abcdefghijklmnop")  # secret-guard: allow 误报：测试用假凭证，验证脱敏
    text = record.getMessage()
    assert SECRET not in text and "abcdefghijklmnop" not in text and "[REDACTED]" in text

    # A credential split over the format and its argument is still caught.
    record = _emit("another.library", "token=%s", "plainvalue123")
    assert "plainvalue123" not in record.getMessage()


def test_exception_text_is_scrubbed():
    try:
        raise RuntimeError(f"connect failed: postgresql://app:{SECRET}@db:5432/flocks")
    except RuntimeError:
        import sys

        record = _emit("uvicorn.error", "Exception in ASGI application", exc_info=sys.exc_info())
    formatted = logging.Formatter().format(record)
    assert SECRET not in formatted and "Traceback" in formatted


def test_uvicorn_access_lines_keep_their_shape():
    pytest.importorskip("uvicorn")
    from uvicorn.logging import AccessFormatter

    record = _emit(
        "uvicorn.access", '%s - "%s %s HTTP/%s" %d',
        "127.0.0.1:5555", "GET", f"/api/thing?token={SECRET}", "1.1", 200,
    )
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert SECRET not in line and "GET /api/thing?token=[REDACTED]" in line and "200" in line  # secret-guard: allow 误报：断言脱敏结果


def test_a_message_that_is_not_a_string_is_scrubbed_too():
    record = _emit("some.library", ValueError(f"bad credentials password={SECRET}"))
    assert SECRET not in record.getMessage()


def test_a_record_that_needs_nothing_is_left_as_it_was():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%d tokens used, max_tokens=%d", (12, 4096), None)
    scrub_log_record(record)
    assert record.args == (12, 4096) and record.getMessage() == "12 tokens used, max_tokens=4096"


def test_dropping_logs_for_the_disk_budget_raises_an_alert(tmp_path, monkeypatch):
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        (tmp_path / day).mkdir()
        (tmp_path / day / "flocks.log").write_bytes(b"x" * 700_000)
    monkeypatch.setenv("FLOCKS_LOG_MAX_TOTAL_MB", "1")

    log_module.Log._cap_total_size(tmp_path)

    (alert,) = alerts.active_alerts()
    assert alert["name"] == "log_budget_exhausted" and alert["dropped_days"] == ["2026-10-01", "2026-10-02"]
    assert not (tmp_path / "2026-10-01").exists() and (tmp_path / "2026-10-03").exists()


def test_within_budget_nothing_is_raised(tmp_path, monkeypatch):
    (tmp_path / "2026-10-03").mkdir()
    (tmp_path / "2026-10-03" / "flocks.log").write_bytes(b"x" * 10)
    monkeypatch.setenv("FLOCKS_LOG_MAX_TOTAL_MB", "1")
    log_module.Log._cap_total_size(tmp_path)
    assert alerts.active_alerts() == []


@pytest.mark.parametrize("module", ["flocks.workflow.service_runtime"])
def test_processes_started_on_their_own_scrub_before_they_log(module, monkeypatch):
    """They never run Log.init: their main() installs the scrubber itself."""
    import importlib

    target = importlib.import_module(module)
    installed = []
    monkeypatch.setattr(log_module, "install_log_record_scrubber", lambda: installed.append(module))

    class _Stop(Exception):
        pass

    def stop(*_args, **_kwargs):
        raise _Stop

    if module.endswith("service_runtime"):
        monkeypatch.setattr(target, "_parse_args", stop)
    else:
        monkeypatch.setattr(target.argparse.ArgumentParser, "parse_args", stop)
    with pytest.raises(_Stop):
        target.main()
    assert installed == [module], "installed before the first thing that can log"


def test_an_alert_reaches_journald_even_when_logs_go_to_a_file(monkeypatch, capfd):
    """Raised and cleared alerts go to the process's own stderr too (what journald takes)."""
    import sys

    monkeypatch.setattr(log_module.Log, "_writer", type("_File", (), {"write": lambda self, m: None, "flush": lambda self: None})())
    alerts.raise_alert("log_shipping_stalled", f"sends failing; token={SECRET}")
    alerts.clear_alert("log_shipping_stalled")
    err = capfd.readouterr().err
    assert "flocks alert raised: log_shipping_stalled" in err and "flocks alert cleared: log_shipping_stalled" in err
    assert SECRET not in err
    assert sys.__stderr__ is not None
