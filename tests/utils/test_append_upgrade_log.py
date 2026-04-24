"""Tests for ``append_upgrade_text_log``."""

import re
from pathlib import Path

from flocks.utils.log import append_upgrade_text_log


def test_append_upgrade_text_log_writes_timestamped_lines(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_LOG_DIR", str(tmp_path))
    append_upgrade_text_log("first line")
    append_upgrade_text_log("a\nb")
    text = (tmp_path / "update.log").read_text(encoding="utf-8")
    lines = text.strip().splitlines()
    assert len(lines) == 3
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| first line$", lines[0])
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| a$", lines[1])
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \| b$", lines[2])
