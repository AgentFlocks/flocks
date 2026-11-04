"""Agent registry cases from the 2026-09-14 execution (G2a): SES-0638 ~ SES-0644."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import flocks.agent.agent_factory as factory_mod
from flocks.agent.agent import AgentInfo
from flocks.agent.agent_factory import _BUILTIN_AGENTS_DIR, scan_and_load, yaml_to_agent_info
from flocks.agent.registry import Agent
from flocks.config.config import ConfigInfo
from flocks.project.instance import Instance


def _agents(**kwargs: AgentInfo) -> dict[str, AgentInfo]:
    return dict(kwargs)


def _cfg(default_agent: str | None) -> ConfigInfo:
    return ConfigInfo.model_construct(default_agent=default_agent)


@pytest.fixture
def stubbed_agents():
    agents = _agents(
        rex=AgentInfo(name="rex", mode="primary"),
        explore=AgentInfo(name="explore", mode="subagent"),
        ghostly=AgentInfo(name="ghostly", mode="primary", hidden=True),
    )
    with patch.object(Agent, "state", AsyncMock(return_value=agents)):
        yield agents


# ---------------------------------------------------------------------------
# default_agent  (SES-0638 / 0639 / 0640)
# ---------------------------------------------------------------------------

async def test_default_agent_is_rex(stubbed_agents):
    """SES-0638."""
    with patch("flocks.agent.registry.Config.get", AsyncMock(return_value=_cfg(None))):
        assert await Agent.default_agent() == "rex"


async def test_default_agent_unknown_name_raises(stubbed_agents):
    """SES-0639."""
    with patch("flocks.agent.registry.Config.get", AsyncMock(return_value=_cfg("ghost"))):
        with pytest.raises(ValueError, match='default agent "ghost" not found'):
            await Agent.default_agent()


async def test_default_agent_rejects_subagent_and_hidden(stubbed_agents):
    """SES-0640."""
    with patch("flocks.agent.registry.Config.get", AsyncMock(return_value=_cfg("explore"))):
        with pytest.raises(ValueError, match="is a subagent"):
            await Agent.default_agent()
    with patch("flocks.agent.registry.Config.get", AsyncMock(return_value=_cfg("ghostly"))):
        with pytest.raises(ValueError, match="is hidden"):
            await Agent.default_agent()


# ---------------------------------------------------------------------------
# AgentInfo.delegatable derivation  (SES-0641)
# ---------------------------------------------------------------------------

def test_delegatable_derived_from_mode():
    """SES-0641."""
    assert AgentInfo(name="x", mode="primary").delegatable is False
    assert AgentInfo(name="y", mode="subagent").delegatable is True
    assert AgentInfo(name="z", mode="primary", delegatable=True).delegatable is True


# ---------------------------------------------------------------------------
# built-in agents  (SES-0642)
# ---------------------------------------------------------------------------

def test_builtin_agents_load_and_self_improve_is_hidden(monkeypatch, tmp_path: Path):
    """SES-0642."""
    monkeypatch.setattr(factory_mod, "_PLUGIN_AGENTS_DIR", tmp_path / "no-user-agents")
    monkeypatch.chdir(tmp_path)  # no project-level agents either
    agents = scan_and_load()
    expected = {
        "rex", "explore", "librarian", "oracle", "hephaestus", "prometheus",
        "rex-junior", "multimodal-looker", "self-improve",
    }
    assert expected <= set(agents)
    assert len([d for d in _BUILTIN_AGENTS_DIR.iterdir() if d.is_dir() and not d.name.startswith("_")]) == 9
    assert all(agents[name].native for name in expected)
    assert agents["self-improve"].hidden is True
    assert "evolution" in (agents["self-improve"].tags or [])


# ---------------------------------------------------------------------------
# yaml_to_agent_info prompt_file  (SES-0643)
# ---------------------------------------------------------------------------

def test_prompt_file_is_resolved_relative_to_yaml(tmp_path: Path):
    """SES-0643: the prompt comes from the sibling file; a missing file is logged."""
    yaml_path = tmp_path / "agent.yaml"
    yaml_path.write_text("name: p\n")
    (tmp_path / "prompt.md").write_text("  hello from file \n")
    info = yaml_to_agent_info({"name": "p", "prompt_file": "prompt.md"}, yaml_path)
    assert info.prompt == "hello from file"

    errors: list = []
    with patch.object(factory_mod.log, "error", lambda event, payload=None: errors.append((event, payload))):
        missing = yaml_to_agent_info({"name": "q", "prompt_file": "nope.md"}, yaml_path)
    assert missing.prompt is None
    assert errors and errors[0][0] == "agent.factory.prompt_file_missing"
    assert errors[0][1]["prompt_file"].endswith("nope.md")


# ---------------------------------------------------------------------------
# watcher invalidation reaches every instance scope  (SES-0644)
# ---------------------------------------------------------------------------

async def test_invalidate_cache_from_thread_without_instance_context(monkeypatch):
    """SES-0644: the watcher timer thread has no instance context, yet the
    request-scoped cache (keyed by project directory) must be rebuilt."""
    loads = 0

    async def fake_load():
        nonlocal loads
        loads += 1
        return {"rex": AgentInfo(name="rex", mode="primary", prompt=f"v{loads}")}

    monkeypatch.setattr(Agent, "_load_agents", staticmethod(fake_load))
    monkeypatch.setattr(Agent, "_sync_skill_settings_cache", classmethod(lambda cls: None))
    monkeypatch.setattr(Agent, "_sync_delegatable_settings_cache", classmethod(lambda cls: None))
    monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: "/proj/a"))
    Agent.invalidate_cache()
    first = await Agent.state()
    assert first["rex"].prompt == "v1"
    assert (await Agent.state())["rex"].prompt == "v1"  # cached

    # simulate the AgentFileWatcher debounce timer: a plain thread, no instance context
    done = threading.Event()

    def _from_timer_thread():
        real_get = Instance.get_directory
        monkeypatch.setattr(Instance, "get_directory", classmethod(lambda cls: None))
        try:
            Agent.invalidate_cache()
        finally:
            monkeypatch.setattr(Instance, "get_directory", real_get)
            done.set()

    await asyncio.to_thread(_from_timer_thread)
    assert done.wait(1)
    assert (await Agent.state())["rex"].prompt == "v2"
    Agent.invalidate_cache()
