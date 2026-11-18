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


@pytest.fixture
def home_project_root(tmp_path, monkeypatch):
    """The default layout with the server started from the home directory.

    The instance directory is ``~`` and the flocks root ``~/.flocks``: the
    project plugin root ``~/.flocks/plugins`` is the user plugin root itself.
    """
    home = tmp_path / "home"
    root = home / ".flocks"
    (root / "config").mkdir(parents=True)
    (root / "data").mkdir()
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(local.Instance, "get_directory", staticmethod(lambda: str(home)))
    monkeypatch.setattr(local.Config, "get_data_path", staticmethod(lambda: root / "data"))
    assert local.install_root("component", "project").resolve() == local.install_root("component", "global").resolve()
    return root


async def test_uninstall_from_home_directory_instance_is_not_a_project_builtin(home_project_root, monkeypatch):
    """v0.21 #6: 场景管理卸载告警运营套件报 "Built-in project Hub plugins cannot be removed"."""
    path = _install_skill_under(home_project_root, "home-skill")

    async def _no_refresh(*_a, **_k):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", _no_refresh)
    monkeypatch.setattr(installer, "_remove_attached_access_contracts", lambda *_a, **_k: None)

    assert installer._is_project_install_path("skill", path) is False
    assert await installer.uninstall_plugin("skill", "home-skill") is True
    assert not path.exists()


async def test_project_scope_record_in_shared_root_is_still_confined(home_project_root, tmp_path, monkeypatch):
    elsewhere = tmp_path / "elsewhere" / "skills" / "foreign"
    elsewhere.mkdir(parents=True)
    (elsewhere / "SKILL.md").write_text("---\nname: foreign\ndescription: d\n---\n", encoding="utf-8")
    local.save_installed_record(InstalledPluginRecord(
        id="foreign", type="skill", version="1.0.0", source="x", installedAt=1, enabled=True,
        scope="project", installPath=str(elsewhere),
    ))

    with pytest.raises(ValueError, match="user-managed install location"):
        await installer.uninstall_plugin("skill", "foreign")
    assert elsewhere.exists()


def test_distinct_project_root_still_protects_shipped_plugins(isolated_root, tmp_path):
    shipped = tmp_path / "proj" / ".flocks" / "plugins" / "skills" / "shipped"
    shipped.mkdir(parents=True)
    assert installer._is_project_install_path("skill", shipped) is True
    with pytest.raises(ValueError, match="Built-in project Hub plugins cannot be removed"):
        installer._validate_uninstall_target("skill", "shipped", shipped, "global")


def _install_webui_under_shared_root(plugin_id: str, *, scope: str) -> tuple[Path, Path]:
    page = local.install_dir("webui", plugin_id, "global")
    (page / "pages" / "home").mkdir(parents=True)
    (page / "pages" / "home" / "page.json").write_text("{}", encoding="utf-8")
    access = local.install_root("webui", "global").resolve().parent / "access" / plugin_id
    access.mkdir(parents=True)
    (access / "contract.py").write_text("# access contract\n", encoding="utf-8")
    local.save_installed_record(InstalledPluginRecord(
        id=plugin_id, type="webui", version="1.0.0", source="bundled:plugins/webuis/" + plugin_id,
        installedAt=1, enabled=True, scope=scope, installPath=str(page),
    ))
    return page, access


@pytest.mark.parametrize("scope", ["project", "global"])
async def test_shared_root_webui_record_is_removed_with_its_access_contracts(home_project_root, monkeypatch, scope):
    # Codex review 20261112-212545-2f33: a "project" record in the shared root used to pass the
    # path check, delete the pages and then be refused at the access contracts.
    page, access = _install_webui_under_shared_root("shared_ui", scope=scope)

    async def _no_refresh(*_a, **_k):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", _no_refresh)
    assert await installer.uninstall_plugin("webui", "shared_ui") is True
    assert not page.exists() and not access.exists()
    assert local.get_record("webui", "shared_ui") is None


async def test_shared_root_webui_is_untouched_when_its_access_contracts_cannot_be_removed(home_project_root, tmp_path, monkeypatch):
    page, access = _install_webui_under_shared_root("linked_ui", scope="project")
    # A link where the access contracts should be: refused before anything is deleted.
    elsewhere = tmp_path / "elsewhere-access"
    access.rename(elsewhere)
    access.symlink_to(elsewhere, target_is_directory=True)

    async def _no_refresh(*_a, **_k):
        return None

    monkeypatch.setattr(installer, "_refresh_runtime", _no_refresh)
    with pytest.raises(ValueError, match="escapes"):
        await installer.uninstall_plugin("webui", "linked_ui")
    assert (page / "pages" / "home" / "page.json").is_file()
    assert (elsewhere / "contract.py").is_file()
    assert local.get_record("webui", "linked_ui") is not None
