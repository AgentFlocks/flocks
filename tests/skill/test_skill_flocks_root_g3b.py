"""G3b 回归：技能发现必须跟随 ``FLOCKS_ROOT``（与 routes/installer/hub 写入的用户级插件根一致）。

缺陷：``POST /api/skills`` / 技能安装 / Hub 安装都写到 ``<FLOCKS_ROOT>/plugins/skills``，
而 ``Skill._discover`` 与 ``SkillFileWatcher._collect_watch_dirs`` 仍写死 ``~/.flocks``，
用 ``FLOCKS_ROOT`` 启动的实例刚创建的技能在列表里永远不出现。
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from flocks.skill.skill import Skill, SkillFileWatcher


def _write_skill_md(path: Path, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\ndescription: test\n---\n# Body\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _clear_cache():
    Skill.clear_cache()
    yield
    Skill.clear_cache()


@pytest.mark.asyncio
async def test_discover_reads_user_skills_under_flocks_root(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "instance-root"
    _write_skill_md(root / "plugins" / "skills" / "root-skill" / "SKILL.md", "root-skill")
    # A skill under the *default* ~/.flocks must not leak into an instance that overrides the root.
    _write_skill_md(home / ".flocks" / "plugins" / "skills" / "home-skill" / "SKILL.md", "home-skill")
    monkeypatch.setenv("FLOCKS_ROOT", str(root))

    with (
        patch("os.path.expanduser", return_value=str(home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(tmp_path)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(tmp_path)),
    ):
        skills = {s.name: s for s in await Skill.all()}

    assert "root-skill" in skills, sorted(skills)
    assert skills["root-skill"].source == "user"
    assert Path(skills["root-skill"].location).is_relative_to(root)
    assert "home-skill" not in skills


@pytest.mark.asyncio
async def test_discover_defaults_to_home_flocks_without_override(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _write_skill_md(home / ".flocks" / "plugins" / "skills" / "home-skill" / "SKILL.md", "home-skill")
    monkeypatch.delenv("FLOCKS_ROOT", raising=False)

    with (
        patch("os.path.expanduser", return_value=str(home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(tmp_path)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(tmp_path)),
    ):
        skills = {s.name: s for s in await Skill.all()}

    assert skills["home-skill"].source == "user"


def test_watcher_watches_flocks_root_plugin_skills(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "instance-root"
    (root / "plugins" / "skills").mkdir(parents=True)
    (home / ".flocks" / "plugins" / "skills").mkdir(parents=True)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))

    with (
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(project_dir)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(project_dir)),
        patch("os.path.expanduser", return_value=str(home)),
    ):
        watch_dirs = SkillFileWatcher(Skill)._collect_watch_dirs()

    assert str(root / "plugins" / "skills") in watch_dirs
    assert str(home / ".flocks" / "plugins" / "skills") not in watch_dirs
