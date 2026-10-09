"""Managed package replacement retains each editable definition's own group."""

import json
from pathlib import Path

import pytest
import yaml

from flocks.hub import installer
from flocks.skill.skill import Skill
from flocks.workflow.fs_store import read_workflow_dir


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.mark.parametrize("group", ["本地分类", "", None])
def test_user_skill_upgrade_preserves_group_and_new_body(tmp_path, group):
    old, new = tmp_path / "installed", tmp_path / "package"
    write(old / "SKILL.md", "---\n" + yaml.safe_dump({"name": "demo", "group": group}, allow_unicode=True) + "---\nOld body\n")
    write(new / "SKILL.md", "---\nname: demo\ngroup: Package default\ndescription: New description\ncustom: retained\n---\n\nNew body\n")
    installer._copy_package(new, old, plugin_type="skill", scope="global")
    text = (old / "SKILL.md").read_text()
    metadata = Skill._parse_frontmatter(text)
    assert metadata["group"] == (group or "")
    assert metadata["description"] == "New description" and metadata["custom"] == "retained"
    assert text.endswith("\n\nNew body\n")


def test_readonly_project_skill_cannot_be_replaced_by_hub(tmp_path):
    old, new = tmp_path / "installed", tmp_path / "package"
    original = "---\nname: demo\ngroup: Old shipped group\n---\nOld body\n"
    write(old / "SKILL.md", original)
    write(new / "SKILL.md", "---\nname: demo\ngroup: New shipped group\n---\nNew body\n")
    with pytest.raises(ValueError, match="read-only"):
        installer._copy_package(new, old, plugin_type="skill", scope="project")
    assert (old / "SKILL.md").read_text() == original
    assert not list(tmp_path.glob(".installed.*"))


@pytest.mark.parametrize("group", ["Local", "", None])
def test_agent_explicit_group_survives_without_retaining_old_other_fields(tmp_path, group):
    old, new = tmp_path / "installed", tmp_path / "package"
    write(old / "agent.yaml", yaml.safe_dump({"name": "demo", "group": group, "description": "Old"}))
    write(new / "agent.yaml", "name: demo\ngroup: Default\ndescription: New\nprompt: New prompt\n")
    installer._copy_package(new, old, plugin_type="agent")
    data = yaml.safe_load((old / "agent.yaml").read_text())
    assert data == {"name": "demo", "group": group or "", "description": "New", "prompt": "New prompt"}


@pytest.mark.parametrize("group", ["Intel", "", None])
def test_yaml_tool_metadata_preserved_in_its_own_file(tmp_path, group):
    old, new = tmp_path / "installed", tmp_path / "package"
    write(old / "queries" / "lookup.yaml", yaml.safe_dump({"name": "lookup", "group": group, "handler": {"type": "http"}}))
    write(new / "queries" / "lookup.yaml", "name: lookup\ndescription: Updated lookup\nhandler: {type: http}\n")
    write(new / "_provider.yaml", "name: Provider\ngroup: New provider default\n")
    write(old / "_provider.yaml", "name: Provider\ngroup: Old provider default\n")
    installer._copy_package(new, old, plugin_type="tool")
    assert yaml.safe_load((old / "queries" / "lookup.yaml").read_text())["group"] == group
    assert yaml.safe_load((old / "_provider.yaml").read_text())["group"] == "New provider default"


@pytest.mark.parametrize("group", ["Ops", "", None])
def test_workflow_upgrade_preserves_meta_group_without_changing_graph(tmp_path, group):
    old, new = tmp_path / "demo-workflow", tmp_path / "package"
    write(old / "meta.json", json.dumps({"name": "Old", "group": group}))
    graph = json.dumps({"name": "New flow", "start": "", "nodes": [], "edges": [], "metadata": {"group": "Package default"}})
    write(new / "workflow.json", graph)
    installer._copy_package(new, old, plugin_type="workflow")
    assert (old / "workflow.json").read_text() == graph
    meta = json.loads((old / "meta.json").read_text())
    assert meta["group"] == (group or "") and meta["name"] == "New flow"
    assert read_workflow_dir(old, "demo-workflow", "global")["group"] == (group or "")


def test_workflow_package_group_is_imported_into_native_metadata(tmp_path):
    target, source = tmp_path / "demo-workflow", tmp_path / "package"
    write(source / "workflow.json", json.dumps({"name": "Flow", "start": "", "nodes": [], "edges": [], "metadata": {"group": "Intel"}}))
    installer._copy_package(source, target, plugin_type="workflow")
    assert json.loads((target / "meta.json").read_text())["group"] == "Intel"
    loaded = read_workflow_dir(target, "demo-workflow", "global")
    assert loaded["group"] == "Intel"
    assert "group" not in loaded["workflowJson"]["metadata"]


def test_markdown_draft_metadata_never_creates_execution_json_or_staging_name(tmp_path):
    old, new = tmp_path / "demo-workflow", tmp_path / "package"
    write(old / "meta.json", json.dumps({"group": "Drafts"}))
    write(new / "workflow.md", "A titleless draft.\n")
    installer._copy_package(new, old, plugin_type="workflow")
    assert not (old / "workflow.json").exists()
    assert (old / "workflow.md").read_text() == "A titleless draft.\n"
    meta = json.loads((old / "meta.json").read_text())
    assert meta["name"] == "demo-workflow" and meta["group"] == "Drafts"


def test_invalid_saved_metadata_aborts_before_replacing_original(tmp_path):
    old, new = tmp_path / "installed", tmp_path / "package"
    original = "name: demo\ngroup: [invalid, value]\ndescription: Original\n"
    write(old / "agent.yaml", original)
    write(new / "agent.yaml", "name: demo\ndescription: New\n")
    with pytest.raises(ValueError, match="group metadata"):
        installer._copy_package(new, old, plugin_type="agent")
    assert (old / "agent.yaml").read_text() == original
    assert not list(tmp_path.glob(".installed.*"))


def test_first_install_keeps_packaged_default_without_an_extra_store(tmp_path):
    old, new = tmp_path / "installed", tmp_path / "package"
    incoming = "name: demo\ngroup: Default\ndescription: New\n"
    write(new / "agent.yaml", incoming)
    installer._copy_package(new, old, plugin_type="agent")
    assert (old / "agent.yaml").read_text() == incoming
    assert [path.name for path in old.iterdir()] == ["agent.yaml"]


def test_external_symlink_metadata_is_not_copied(tmp_path):
    old, new, external = tmp_path / "installed", tmp_path / "package", tmp_path / "external.yaml"
    write(external, "name: demo\ngroup: Outside\n")
    old.mkdir()
    (old / "agent.yaml").symlink_to(external)
    write(new / "agent.yaml", "name: demo\ngroup: Package\n")
    installer._copy_package(new, old, plugin_type="agent")
    assert yaml.safe_load((old / "agent.yaml").read_text())["group"] == "Package"
    assert external.read_text() == "name: demo\ngroup: Outside\n"


def tool_yaml(name, group):
    return yaml.safe_dump({"name": name, "group": group, "handler": {"type": "http", "url": "https://offline.invalid"}})


def test_tool_group_matches_declared_identity_not_filename(tmp_path):
    old, new = tmp_path / "installed", tmp_path / "package"
    write(old / "lookup.yaml", tool_yaml("same-tool", "Personal"))
    write(new / "moved.yml", tool_yaml("same-tool", "Package"))
    write(new / "lookup.yaml", tool_yaml("different-tool", "Different default"))
    installer._copy_package(new, old, plugin_type="tool")
    assert yaml.safe_load((old / "moved.yml").read_text())["group"] == "Personal"
    assert yaml.safe_load((old / "lookup.yaml").read_text())["group"] == "Different default"


@pytest.mark.parametrize("relative", ["_fixtures/tool.yaml", "_fixtures/deep/tool.yaml", "queries/deeper/tool.yaml", "config.yaml"])
def test_tool_replacement_ignores_undiscoverable_and_unrelated_yaml(tmp_path, relative):
    old, new = tmp_path / "installed", tmp_path / "package"
    before = tool_yaml("ignored", ["invalid group"])
    after = tool_yaml("ignored", "Package")
    if relative == "config.yaml":
        before = "name: config\ngroup: [unrelated, data]\nsettings: true\n"
        after = "name: config\ngroup: untouched\nsettings: false\n"
    write(old / relative, before)
    write(new / relative, after)
    installer._copy_package(new, old, plugin_type="tool")
    assert (old / relative).read_text() == after


@pytest.mark.parametrize("subsystem", ["api", "python", "device", "mcp", "generated"])
def test_tool_scan_uses_destination_depth_and_subsystem_exclusions(tmp_path, monkeypatch, subsystem):
    root = tmp_path / "tools"
    old, new = root / subsystem / "provider", tmp_path / "package"
    monkeypatch.setattr(installer.local, "install_root", lambda *_args: root)
    write(old / "lookup.yaml", tool_yaml("lookup", "Personal"))
    write(new / "lookup.yaml", tool_yaml("lookup", "Package"))
    write(old / "nested/too-deep.yaml", tool_yaml("deep", ["invalid"]))
    write(new / "nested/too-deep.yaml", tool_yaml("deep", "Unchanged"))
    installer._copy_package(new, old, plugin_type="tool")
    expected = "Package" if subsystem in {"mcp", "generated"} else "Personal"
    assert yaml.safe_load((old / "lookup.yaml").read_text())["group"] == expected
    assert yaml.safe_load((old / "nested/too-deep.yaml").read_text())["group"] == "Unchanged"


def test_tool_metadata_preservation_does_not_import_python_handlers(tmp_path):
    old, new = tmp_path / "installed", tmp_path / "package"
    for package, group in ((old, "Personal"), (new, "Default")):
        write(package / "tool.yaml", yaml.safe_dump({"name": "lookup", "group": group, "handler": {"type": "script", "path": "crash.py"}}))
        write(package / "crash.py", "raise RuntimeError('must not import during metadata preservation')\n")
    installer._copy_package(new, old, plugin_type="tool")
    assert yaml.safe_load((old / "tool.yaml").read_text())["group"] == "Personal"


def test_external_symlink_tool_tree_is_not_read_for_group_preservation(tmp_path):
    old, new, outside = tmp_path / "installed", tmp_path / "package", tmp_path / "outside"
    old.mkdir()
    write(outside / "lookup.yaml", tool_yaml("lookup", ["invalid"]))
    (old / "queries").symlink_to(outside, target_is_directory=True)
    write(new / "queries/lookup.yaml", tool_yaml("lookup", "Package"))
    installer._copy_package(new, old, plugin_type="tool")
    assert yaml.safe_load((old / "queries/lookup.yaml").read_text())["group"] == "Package"
    assert yaml.safe_load((outside / "lookup.yaml").read_text())["group"] == ["invalid"]


def test_hub_skill_cannot_shadow_actual_bundled_definition(tmp_path, monkeypatch):
    from flocks.project.instance import Instance

    source = tmp_path / "source"
    monkeypatch.setattr(Skill, "_source_root", lambda: source)
    monkeypatch.setattr(Instance, "get_directory", lambda: str(tmp_path))
    monkeypatch.setattr(Instance, "get_worktree", lambda: str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    protected = source / ".flocks/plugins/skills/hub-demo/SKILL.md"
    original = "---\nname: hub-demo\ndescription: Bundled\ngroup: Fixed\n---\nOriginal\n"
    write(protected, original)
    package, target = tmp_path / "package", tmp_path / "home/.flocks/plugins/skills/hub-demo"
    write(package / "SKILL.md", "---\nname: hub-demo\ndescription: Replacement\ngroup: New\n---\nNew\n")
    with pytest.raises(ValueError, match="read-only"):
        installer._copy_package(package, target, plugin_type="skill")
    assert protected.read_text() == original
    assert not target.exists()


@pytest.mark.parametrize("plugin_type", ["agent", "tool", "workflow"])
@pytest.mark.parametrize("group", ["Changed", "", None])
def test_hub_cannot_change_actual_shipped_target_group(tmp_path, monkeypatch, plugin_type, group):
    from flocks.agent import agent_factory
    from flocks.tool import registry
    from flocks.workflow import fs_store

    source = tmp_path / "source"
    roots = {kind: source / ".flocks/plugins" / (kind + "s") for kind in ("agent", "tool", "workflow")}
    monkeypatch.setattr(agent_factory, "_SYSTEM_AGENT_ROOTS", (roots["agent"],))
    monkeypatch.setattr(registry, "__file__", str(source / "flocks/tool/registry.py"))
    monkeypatch.setattr(fs_store, "_SYSTEM_WORKFLOW_ROOT", roots["workflow"])
    monkeypatch.setattr(installer.local, "install_root", lambda *_args: roots["tool"])
    old, new = roots[plugin_type] / "demo", tmp_path / "package"
    if plugin_type == "agent":
        write(old / "agent.yaml", "name: demo\ngroup: Fixed\n")
        write(new / "agent.yaml", yaml.safe_dump({"name": "demo", "group": group}))
    elif plugin_type == "tool":
        write(old / "tool.yaml", tool_yaml("demo", "Fixed"))
        write(new / "tool.yaml", tool_yaml("demo", group))
    else:
        write(old / "workflow.md", "# Original\n")
        write(old / "meta.json", json.dumps({"group": "Fixed"}))
        write(new / "workflow.json", json.dumps({"name": "Changed", "metadata": {"group": group}}))
    before = {path.relative_to(old): path.read_bytes() for path in old.rglob("*") if path.is_file()}
    with pytest.raises(ValueError, match="read-only"):
        installer._copy_package(new, old, plugin_type=plugin_type, scope="project")
    assert {path.relative_to(old): path.read_bytes() for path in old.rglob("*") if path.is_file()} == before
    assert not list(old.parent.glob(".demo.*"))


@pytest.mark.parametrize("plugin_type", ["agent", "tool", "workflow"])
def test_arbitrary_user_project_copies_keep_group_preservation(tmp_path, plugin_type):
    old, new = tmp_path / "project/.flocks/plugins" / (plugin_type + "s") / "demo", tmp_path / "package"
    if plugin_type == "agent":
        filename = "agent.yaml"
        write(old / filename, "name: demo\ngroup: Personal\n")
        write(new / filename, "name: demo\ngroup: Package\n")
    elif plugin_type == "tool":
        filename = "lookup.yaml"
        write(old / filename, tool_yaml("lookup", "Personal"))
        write(new / filename, tool_yaml("lookup", "Package"))
    else:
        filename = "meta.json"
        write(old / filename, json.dumps({"group": "Personal"}))
        write(old / "workflow.md", "# Old\n")
        write(new / "workflow.md", "# New\n")
        write(new / filename, json.dumps({"group": "Package"}))
    installer._copy_package(new, old, plugin_type=plugin_type, scope="project")
    assert yaml.safe_load((old / filename).read_text())["group"] == "Personal"
