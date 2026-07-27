from __future__ import annotations

import json

from flocks.cli.install_profile import read_install_language


def test_install_profile_round_trips_language(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))

    (tmp_path / "install_profile.json").write_text(
        json.dumps({"Language": "zh-CN"}),
        encoding="utf-8",
    )

    assert read_install_language() == "zh-CN"


def test_install_profile_falls_back_to_environment(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("FLOCKS_INSTALL_LANGUAGE", "zh_CN")

    assert read_install_language() == "zh-CN"
