import json
from pathlib import Path

import pytest


@pytest.fixture()
def isolated_hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    project_dir = tmp_path / "project"
    home.mkdir()
    config_dir.mkdir()
    data_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FLOCKS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data_dir))
    (config_dir / "flocks.json").write_text(json.dumps({}), encoding="utf-8")

    from flocks.config.config import Config
    from flocks.skill.skill import Skill

    Config._global_config = None
    Config._cached_config = None
    Skill.clear_cache()
    yield {"home": home, "config_dir": config_dir, "data_dir": data_dir, "project_dir": project_dir}
    Skill.clear_cache()
