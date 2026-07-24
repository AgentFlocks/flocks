"""
Tests for the runtime HookPipeline plugin loader behavior.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from flocks.hooks.pipeline import HookBase, HookPipeline, HookStage
from flocks.plugin.loader import PluginLoader


def _write_project_hook(project_dir: Path, module_name: str, hook_name: str) -> None:
    hooks_dir = project_dir / ".flocks" / "plugins" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / f"{module_name}.py").write_text(
        (
            "from flocks.hooks.pipeline import HookBase\n\n"
            "class _Hook(HookBase):\n"
            f"    name = {hook_name!r}\n\n"
            "HOOKS = [_Hook()]\n"
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _reset_pipeline_state() -> None:
    HookPipeline.reset()
    PluginLoader.clear_extension_points()
    yield
    HookPipeline.reset()
    PluginLoader.clear_extension_points()


@pytest.mark.asyncio
async def test_pipeline_reloads_project_hooks_when_workspace_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_plugin_root = tmp_path / "user_plugins"
    project_a = tmp_path / "project_a"
    project_b = tmp_path / "project_b"
    _write_project_hook(project_a, "alpha_hook", "hook-alpha")
    _write_project_hook(project_b, "beta_hook", "hook-beta")

    monkeypatch.setattr(PluginLoader, "_plugin_root", user_plugin_root)
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=SimpleNamespace(plugin=[])),
    )

    class _ManualHook(HookBase):
        pass

    HookPipeline.register("manual-hook", _ManualHook())

    await HookPipeline.run_tool_before(
        {
            "workspace": str(project_a),
            "sessionID": "ses_a",
            "tool": {"name": "read", "input": {}},
        }
    )
    hooks_after_a = set(HookPipeline.list_hooks())
    assert "manual-hook" in hooks_after_a
    assert "hook-alpha" in hooks_after_a
    assert "hook-beta" not in hooks_after_a

    await HookPipeline.run_tool_before(
        {
            "workspace": str(project_b),
            "sessionID": "ses_b",
            "tool": {"name": "read", "input": {}},
        }
    )
    hooks_after_b = set(HookPipeline.list_hooks())
    assert "manual-hook" in hooks_after_b
    assert "hook-beta" in hooks_after_b
    assert "hook-alpha" not in hooks_after_b


@pytest.mark.asyncio
async def test_pipeline_resolves_project_dir_from_session_when_workspace_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_plugin_root = tmp_path / "user_plugins"
    project_dir = tmp_path / "project_session"
    _write_project_hook(project_dir, "session_hook", "hook-session")

    monkeypatch.setattr(PluginLoader, "_plugin_root", user_plugin_root)
    monkeypatch.setattr(
        "flocks.config.config.Config.get",
        AsyncMock(return_value=SimpleNamespace(plugin=[])),
    )
    monkeypatch.setattr(
        "flocks.session.session.Session.get_by_id",
        AsyncMock(return_value=SimpleNamespace(directory=str(project_dir))),
    )

    await HookPipeline.run_tool_before(
        {
            "sessionID": "ses_lookup",
            "tool": {"name": "read", "input": {}},
        }
    )

    assert "hook-session" in set(HookPipeline.list_hooks())


def test_hook_base_exposes_only_canonical_lifecycle_names() -> None:
    assert not hasattr(HookBase, "chat_message")
    assert hasattr(HookBase, "tool_before")
    assert hasattr(HookBase, "tool_after")
    assert not hasattr(HookBase, "pre_tool_use")
    assert not hasattr(HookBase, "post_tool_use")
    assert not hasattr(HookPipeline, "run_chat_message")
    assert hasattr(HookPipeline, "run_tool_before")
    assert hasattr(HookPipeline, "run_tool_after")
    assert not hasattr(HookPipeline, "run_pre_tool_use")
    assert not hasattr(HookPipeline, "run_post_tool_use")


def test_default_hook_methods_are_not_stage_handlers() -> None:
    hook = HookBase()

    assert HookPipeline._resolve_handler(
        hook,
        HookStage.USER_PROMPT_SUBMIT,
    ) is None
    assert HookPipeline._resolve_handler(hook, HookStage.LLM_BEFORE) is None
    assert HookPipeline._resolve_handler(hook, HookStage.TOOL_BEFORE) is None


@pytest.mark.asyncio
async def test_pipeline_runs_canonical_lifecycle_handlers() -> None:
    seen: list[str] = []

    class _CanonicalHook(HookBase):
        async def user_prompt_submit(self, ctx) -> None:
            seen.append(ctx.stage)

        async def tool_before(self, ctx) -> None:
            seen.append(ctx.stage)

        async def tool_after(self, ctx) -> None:
            seen.append(ctx.stage)

        async def turn_finish(self, ctx) -> None:
            seen.append(ctx.stage)

    HookPipeline.register("canonical-hook", _CanonicalHook())
    HookPipeline._initialized = True

    await HookPipeline.run_user_prompt_submit({"sessionID": "ses_test"})
    await HookPipeline.run_tool_before({"sessionID": "ses_test"})
    await HookPipeline.run_tool_after({"sessionID": "ses_test"})
    await HookPipeline.run_turn_finish({"sessionID": "ses_test"})

    assert seen == [
        HookStage.USER_PROMPT_SUBMIT,
        HookStage.TOOL_BEFORE,
        HookStage.TOOL_AFTER,
        HookStage.TURN_FINISH,
    ]
