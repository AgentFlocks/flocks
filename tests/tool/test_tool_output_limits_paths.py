"""tool_output_limits must read the same config file Config does (G3a, 2026-09-14)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.config.config import Config
from flocks.tool.tool_output_limits import get_tool_output_limits


def test_fallback_reads_flocks_root_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "root"
    (root / "config").mkdir(parents=True)
    (root / "config" / "flocks.json").write_text(json.dumps({"toolOutput": {"readMaxLines": 100}}))
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.delenv("FLOCKS_CONFIG_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(Config, "_cached_config", None)

    assert get_tool_output_limits()["read_max_lines"] == 100
