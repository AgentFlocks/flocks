"""Regression tests for atomic-save handling in plugin/agent/skill watchers.

Atomic-save editors persist edits by writing a sibling temp file and then
``rename``-ing it onto the real target.  watchdog surfaces this as a
``FileMovedEvent`` whose ``src_path`` is the temp filename and whose
``dest_path`` is the actual ``tool.yaml`` / ``agent.yaml`` / ``SKILL.md``.

These tests pin down the contract enforced by the three module-level
predicates that the watcher event handlers delegate to.
"""

from __future__ import annotations

from types import SimpleNamespace

from flocks.tool.registry import ToolFileWatcher, _tool_event_should_reload
from flocks.agent.registry import _agent_event_should_reload
from flocks.skill.skill import _skill_event_should_reload


def _move_event(src: str, dest: str) -> SimpleNamespace:
    return SimpleNamespace(event_type="moved", src_path=src, dest_path=dest, is_directory=False)


def _modify_event(path: str) -> SimpleNamespace:
    return SimpleNamespace(event_type="modified", src_path=path, dest_path="", is_directory=False)


# ---------------------------------------------------------------------------
# Tool watcher predicate
# ---------------------------------------------------------------------------


def test_tool_watcher_accepts_dest_path_on_atomic_save() -> None:
    """A rename of ``<tmp>`` -> ``tool.yaml`` must trigger a reload."""
    evt = _move_event(
        src="/repo/.flocks/plugins/tools/api/foo/.tool.yaml.swp",
        dest="/repo/.flocks/plugins/tools/api/foo/tool.yaml",
    )
    assert _tool_event_should_reload(evt) is True


def test_tool_watcher_accepts_python_atomic_save() -> None:
    evt = _move_event(
        src="/repo/.flocks/plugins/tools/python/foo/.tool.py.4321~",
        dest="/repo/.flocks/plugins/tools/python/foo/tool.py",
    )
    assert _tool_event_should_reload(evt) is True


def test_tool_watcher_rejects_irrelevant_paths() -> None:
    assert _tool_event_should_reload(_modify_event("/repo/.flocks/plugins/tools/api/foo/README")) is False
    assert _tool_event_should_reload(_modify_event("/repo/.flocks/plugins/tools/api/foo/__pycache__/x.py")) is False
    assert _tool_event_should_reload(_modify_event("/repo/.flocks/plugins/tools/api/foo/.hidden.yaml")) is False
    assert _tool_event_should_reload(_modify_event("/repo/.flocks/plugins/tools/api/foo/_tmp.yaml")) is False


def test_tool_watcher_accepts_direct_modify_on_yaml() -> None:
    evt = _modify_event("/repo/.flocks/plugins/tools/api/foo/tool.yaml")
    assert _tool_event_should_reload(evt) is True


def test_tool_watcher_includes_device_plugin_directory() -> None:
    assert "device" in ToolFileWatcher._WATCH_SUBDIRS


# ---------------------------------------------------------------------------
# Agent watcher predicate
# ---------------------------------------------------------------------------


def test_agent_watcher_accepts_dest_path_on_atomic_save() -> None:
    evt = _move_event(
        src="/repo/.flocks/plugins/agents/foo/.agent.yaml.swp",
        dest="/repo/.flocks/plugins/agents/foo/agent.yaml",
    )
    assert _agent_event_should_reload(evt) is True


def test_agent_watcher_accepts_md_via_dest_path() -> None:
    evt = _move_event(
        src="/repo/.flocks/plugins/agents/foo/.AGENT.md.tmp",
        dest="/repo/.flocks/plugins/agents/foo/AGENT.md",
    )
    assert _agent_event_should_reload(evt) is True


def test_agent_watcher_rejects_unrelated_paths() -> None:
    evt = _modify_event("/repo/.flocks/plugins/agents/foo/README.txt")
    assert _agent_event_should_reload(evt) is False


# ---------------------------------------------------------------------------
# Skill watcher predicate
# ---------------------------------------------------------------------------


def test_skill_watcher_accepts_skill_md_via_dest_path() -> None:
    evt = _move_event(
        src="/repo/.flocks/plugins/skills/foo/.SKILL.md.swap",
        dest="/repo/.flocks/plugins/skills/foo/SKILL.md",
    )
    assert _skill_event_should_reload(evt) is True


def test_skill_watcher_accepts_direct_modify() -> None:
    assert _skill_event_should_reload(_modify_event("/repo/.flocks/plugins/skills/foo/SKILL.md")) is True


def test_skill_watcher_rejects_non_skill_files() -> None:
    assert _skill_event_should_reload(_modify_event("/repo/.flocks/plugins/skills/foo/notes.md")) is False
