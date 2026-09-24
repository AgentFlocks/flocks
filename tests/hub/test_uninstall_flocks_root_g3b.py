"""G3b 回归：Hub 卸载的"用户管理安装"判定必须跟随 FLOCKS_ROOT 的用户级插件根。

缺陷：``uninstall_plugin`` 用 ``".flocks/plugins" in path`` 判断是否用户安装；
用 ``FLOCKS_ROOT=<root>`` 启动的实例把技能装在 ``<root>/plugins/skills``，
字面量不匹配，所有 Hub 卸载都被拒为 "Only user-managed Hub plugin installs can be removed"。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from flocks.hub import installer, local
from flocks.hub.models import InstalledPluginRecord


@pytest.fixture
def isolated_root(tmp_path, monkeypatch):
    root = tmp_path / "instance-root"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    # 项目目录放在 root 之外，避免被判成 project 内置插件
    project = tmp_path / "proj"
    (project / ".flocks").mkdir(parents=True)
    monkeypatch.setattr(local.Instance, "get_directory", staticmethod(lambda: str(project)))
    monkeypatch.setattr(local.Config, "get_data_path", staticmethod(lambda: root / "data"))
    return root


def _install_skill_under(root: Path, plugin_id: str) -> Path:
    path = local.install_dir("skill", plugin_id, "global")
    assert path == root / "plugins" / "skills" / plugin_id
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text(f"---\nname: {plugin_id}\ndescription: d\n---\nbody\n", encoding="utf-8")
    local.save_installed_record(InstalledPluginRecord(
        id=plugin_id, type="skill", version="1.0.0", source="bundled:plugins/skills/" + plugin_id,
        installedAt=1, enabled=True, scope="global", installPath=str(path),
    ))
    return path


async def test_uninstall_accepts_install_under_flocks_root_plugins(isolated_root, monkeypatch):
    path = _install_skill_under(isolated_root, "g3b-root-skill")

    async def _no_refresh(*_a, **_k):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", _no_refresh)
    monkeypatch.setattr(installer, "_remove_attached_access_contracts", lambda *_a, **_k: None)

    assert await installer.uninstall_plugin("skill", "g3b-root-skill") is True
    assert not path.exists()
    records = json.loads((isolated_root / "data" / "hub" / "installed.json").read_text())
    assert "skill:g3b-root-skill" not in records.get("plugins", {})


async def test_uninstall_still_refuses_paths_outside_user_root(isolated_root, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "skills" / "foreign"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("---\nname: foreign\ndescription: d\n---\n", encoding="utf-8")
    local.save_installed_record(InstalledPluginRecord(
        id="foreign", type="skill", version="1.0.0", source="x", installedAt=1, enabled=True,
        scope="global", installPath=str(elsewhere),
    ))

    with pytest.raises(ValueError, match="user-managed install location"):
        await installer.uninstall_plugin("skill", "foreign")
    assert elsewhere.exists()
