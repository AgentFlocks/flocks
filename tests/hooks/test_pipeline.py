"""
Tests for the runtime HookPipeline plugin loader behavior.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path
import sys
import threading
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
def _reset_pipeline_state(monkeypatch: pytest.MonkeyPatch) -> None:
    # Clearing the shared loader leaves an already initialized ToolRegistry
    # without its TOOLS point. Isolate this suite and restore the original
    # objects so other initialized registries keep their matching state.
    monkeypatch.setattr(HookPipeline, "_hooks", [])
    monkeypatch.setattr(HookPipeline, "_initialized", False)
    monkeypatch.setattr(HookPipeline, "_loaded_project_dir", None)
    monkeypatch.setattr(HookPipeline, "_plugin_hook_names", set())
    monkeypatch.setattr(PluginLoader, "_extension_points", {})
    monkeypatch.setattr(PluginLoader, "_last_load_errors", [])
    monkeypatch.setattr(PluginLoader, "_runtime_critical_entrypoint_failure", False)


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
        async def user_prompt_before(self, ctx) -> None:
            seen.append(ctx.stage)

        async def tool_before(self, ctx) -> None:
            seen.append(ctx.stage)

        async def tool_after(self, ctx) -> None:
            seen.append(ctx.stage)

        async def turn_after(self, ctx) -> None:
            seen.append(ctx.stage)

    HookPipeline.register("canonical-hook", _CanonicalHook())
    HookPipeline._initialized = True

    await HookPipeline.run_user_prompt_before({"sessionID": "ses_test"})
    await HookPipeline.run_tool_before({"sessionID": "ses_test"})
    await HookPipeline.run_tool_after({"sessionID": "ses_test"})
    await HookPipeline.run_turn_after({"sessionID": "ses_test"})

    assert seen == [
        HookStage.USER_PROMPT_BEFORE,
        HookStage.TOOL_BEFORE,
        HookStage.TOOL_AFTER,
        HookStage.TURN_AFTER,
    ]


@pytest.mark.parametrize("payload", [{}, {"sessionID": "missing-session"}])
async def test_context_free_ingress_does_not_inherit_last_project_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict,
) -> None:
    host = tmp_path / "host"
    host.mkdir()
    monkeypatch.chdir(host)
    project = tmp_path / "private-project"
    hooks_dir = project / ".flocks" / "plugins" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "private_hook.py").write_text(
        "from flocks.hooks.pipeline import HookBase\n"
        "class PrivateHook(HookBase):\n"
        "    name = 'private-project-hook'\n"
        "    async def ingress_before(self, ctx):\n"
        "        ctx.output['private_project_hook_ran'] = True\n"
        "HOOKS = [PrivateHook()]\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user-plugins")
    monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace(plugin=[])))
    monkeypatch.setattr("flocks.session.session.Session.get_by_id", AsyncMock(return_value=None))

    class GlobalGuard(HookBase):
        async def ingress_before(self, ctx):
            ctx.output["global_guard_ran"] = True

    HookPipeline.register("global-security-guard", GlobalGuard(), critical=True)
    first = await HookPipeline.run_ingress_before({"workspace": str(project)})
    assert first.output == {"private_project_hook_ran": True, "global_guard_ran": True}

    other_request = await HookPipeline.run_ingress_before(payload)
    assert other_request.output == {"global_guard_ran": True}
    assert "private-project-hook" not in HookPipeline.list_hooks()
    assert "global-security-guard" in HookPipeline.list_hooks()


def _write_recording_project_hook(project_dir: Path, name: str) -> None:
    hooks_dir = project_dir / ".flocks" / "plugins" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    (hooks_dir / f"{name}_hook.py").write_text(
        "from flocks.hooks.pipeline import HookBase\n"
        "class ProjectHook(HookBase):\n"
        f"    name = {name!r}\n"
        "    async def tool_before(self, ctx):\n"
        f"        ctx.output.setdefault('hooks', []).append({name!r})\n"
        f"        if ctx.input.get('pause_hook') == {name!r}:\n"
        "            ctx.input['entered'].set()\n"
        "            await ctx.input['release'].wait()\n"
        "HOOKS = [ProjectHook()]\n",
        encoding="utf-8",
    )


async def test_overlapping_initial_loads_do_not_mix_project_hooks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_recording_project_hook(project_a, "a")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def get_config():
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        return SimpleNamespace(plugin=[])

    monkeypatch.setattr("flocks.config.config.Config.get", get_config)
    task_a = asyncio.create_task(HookPipeline.run_tool_before({"workspace": str(project_a)}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result_b = await HookPipeline.run_tool_before({"workspace": str(project_b)})
        release.set()
        result_a = await asyncio.wait_for(task_a, 1)
        assert result_b.output == {"hooks": ["b"]}
        assert result_a.output == {"hooks": ["a"]}
    finally:
        release.set()
        await task_a


@pytest.mark.parametrize("cancel_switch", [False, True])
async def test_loading_other_project_keeps_initialized_project_available(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cancel_switch: bool,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_recording_project_hook(project_a, "a")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def get_config():
        nonlocal calls
        calls += 1
        if calls == 2:
            entered.set()
            await release.wait()
        return SimpleNamespace(plugin=[])

    monkeypatch.setattr("flocks.config.config.Config.get", get_config)
    first = await HookPipeline.run_tool_before({"workspace": str(project_a)})
    assert first.output == {"hooks": ["a"]}
    task_b = asyncio.create_task(HookPipeline.run_tool_before({"workspace": str(project_b)}))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        if cancel_switch:
            task_b.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task_b
        result_a = await HookPipeline.run_tool_before({"workspace": str(project_a)})
        assert result_a.output == {"hooks": ["a"]}
        if cancel_switch:
            assert HookPipeline._loaded_project_dir == str(project_a.resolve())
        else:
            release.set()
            result_b = await asyncio.wait_for(task_b, 1)
            assert result_b.output == {"hooks": ["b"]}
    finally:
        release.set()
        if not task_b.cancelled():
            await task_b


@pytest.mark.parametrize("failure", ["config", "partial_load"])
async def test_failed_project_switch_drops_partial_hooks_and_can_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_recording_project_hook(project_a, "a")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    config = AsyncMock(return_value=SimpleNamespace(plugin=[]))
    monkeypatch.setattr("flocks.config.config.Config.get", config)

    class ManualHook(HookBase):
        async def tool_before(self, ctx):
            ctx.output["manual"] = True

    HookPipeline.register("manual", ManualHook())
    await HookPipeline.run_tool_before({"workspace": str(project_a)})
    original_load = PluginLoader.load_extension

    def partial_load(*args, **kwargs):
        original_load(*args, **kwargs)
        raise RuntimeError("temporary plugin load failure")

    if failure == "config":
        config.side_effect = RuntimeError("temporary config failure")
    else:
        monkeypatch.setattr(PluginLoader, "load_extension", partial_load)
    failed = await HookPipeline.run_tool_before({"workspace": str(project_b)})
    assert failed.output == {"manual": True}
    assert not HookPipeline._initialized
    assert HookPipeline._loaded_project_dir is None

    config.side_effect = None
    monkeypatch.setattr(PluginLoader, "load_extension", original_load)
    retried = await HookPipeline.run_tool_before({"workspace": str(project_b)})
    assert retried.output == {"hooks": ["b"], "manual": True}
    assert HookPipeline._loaded_project_dir == str(project_b.resolve())


async def test_in_flight_stage_keeps_project_hooks_while_other_project_loads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_recording_project_hook(project_a, "a1")
    _write_recording_project_hook(project_a, "a2")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace(plugin=[])))
    entered, release = asyncio.Event(), asyncio.Event()
    task_a = asyncio.create_task(HookPipeline.run_tool_before({
        "workspace": str(project_a), "pause_hook": "a1", "entered": entered, "release": release,
    }))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        result_b = await HookPipeline.run_tool_before({"workspace": str(project_b)})
        release.set()
        result_a = await asyncio.wait_for(task_a, 1)
        assert result_b.output == {"hooks": ["b"]}
        assert result_a.output == {"hooks": ["a1", "a2"]}
    finally:
        release.set()
        await task_a


def test_two_event_loops_cannot_interleave_project_imports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    _write_recording_project_hook(project_a, "a")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    started, release, other_started = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setitem(sys.modules, "_flocks_hook_test_barrier", SimpleNamespace(started=started, release=release))
    path = project_a / ".flocks/plugins/hooks/a_hook.py"
    path.write_text(
        "from _flocks_hook_test_barrier import started, release\n"
        "started.set()\n"
        "assert release.wait(2), 'test release timed out'\n" + path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace(plugin=[])))

    async def run_other_project():
        other_started.set()
        return await HookPipeline.run_tool_before({"workspace": str(project_b)})

    with ThreadPoolExecutor(max_workers=2) as pool:
        task_a = pool.submit(asyncio.run, HookPipeline.run_tool_before({"workspace": str(project_a)}))
        try:
            assert started.wait(1)
            task_b = pool.submit(asyncio.run, run_other_project())
            assert other_started.wait(1)
            try:
                task_b.result(timeout=0.1)
            except FutureTimeout:
                pass
        finally:
            release.set()
        result_a, result_b = task_a.result(timeout=2), task_b.result(timeout=2)
    assert result_b.output == {"hooks": ["b"]}
    assert result_a.output == {"hooks": ["a"]}


@pytest.mark.parametrize("probe_only", [False, True])
def test_stage_uses_its_project_snapshot_across_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, probe_only: bool,
) -> None:
    project_a, project_b = tmp_path / "a", tmp_path / "b"
    if probe_only:
        _write_project_hook(project_a, "a_hook", "a")
    else:
        _write_recording_project_hook(project_a, "a")
    _write_recording_project_hook(project_b, "b")
    monkeypatch.setattr(PluginLoader, "_plugin_root", tmp_path / "user_plugins")
    monkeypatch.setattr("flocks.config.config.Config.get", AsyncMock(return_value=SimpleNamespace(plugin=[])))
    started, release = threading.Event(), threading.Event()
    original_ensure = HookPipeline.ensure_initialized

    async def pause_after_snapshot(project_dir=None):
        snapshot = await original_ensure(project_dir)
        if project_dir == project_a:
            started.set()
            assert await asyncio.to_thread(release.wait, 2), "test release timed out"
        return snapshot

    async def invoke(project_dir):
        if probe_only:
            return await HookPipeline.has_stage_handlers(HookStage.TOOL_BEFORE, {"workspace": str(project_dir)})
        return await HookPipeline.run_tool_before({"workspace": str(project_dir)})

    monkeypatch.setattr(HookPipeline, "ensure_initialized", pause_after_snapshot)
    with ThreadPoolExecutor(max_workers=2) as pool:
        task_a = pool.submit(asyncio.run, invoke(project_a))
        try:
            assert started.wait(1)
            task_b = pool.submit(asyncio.run, invoke(project_b))
            result_b = task_b.result(timeout=2)
        finally:
            release.set()
        result_a = task_a.result(timeout=2)
    if probe_only:
        assert result_a is False
        assert result_b is True
    else:
        assert result_b.output == {"hooks": ["b"]}
        assert result_a.output == {"hooks": ["a"]}


@pytest.mark.asyncio
async def test_tool_input_rewrite_stage_only_dispatches_to_rewrite_handlers() -> None:
    """tool.input.rewrite must not re-fire tool_before hooks (that was the double fire)."""
    HookPipeline._initialized = True
    seen: list[str] = []

    class _Rewriter(HookBase):
        async def tool_input_rewrite(self, ctx) -> None:  # noqa: ANN001
            seen.append("rewrite")
            ctx.input["tool"]["input"] = {"ip": "10.1.2.3"}

    class _Policy(HookBase):
        async def tool_before(self, ctx) -> None:  # noqa: ANN001
            seen.append("before")

    HookPipeline.register("rewriter", _Rewriter())
    HookPipeline.register("policy", _Policy())

    ctx = await HookPipeline.run_tool_input_rewrite(
        {"sessionID": "ses_rw", "tool": {"name": "ip_query", "input": {"ip": "[IP_1]"}}}
    )

    assert ctx.stage == HookStage.TOOL_INPUT_REWRITE
    assert seen == ["rewrite"]
    assert ctx.input["tool"]["input"] == {"ip": "10.1.2.3"}
    assert hasattr(HookBase, "tool_input_rewrite")
    assert hasattr(HookPipeline, "run_tool_input_rewrite")
