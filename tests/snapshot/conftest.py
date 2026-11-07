"""Snapshot tests opt in explicitly and never use the user's snapshot store."""

from unittest.mock import AsyncMock

import pytest

from flocks.config.config import Config, ConfigInfo


@pytest.fixture(autouse=True)
def isolated_snapshots(tmp_path, monkeypatch):
    data = tmp_path / "data"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "runtime"))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))
    monkeypatch.setattr(Config, "get_data_path", lambda: data)
    monkeypatch.setattr(Config, "get", AsyncMock(return_value=ConfigInfo(snapshot=True)))
