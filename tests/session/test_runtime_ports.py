"""Tests for prompt, tool, model, and hook runtime ports."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flocks.agent.runtime import ExternalRuntimePorts
from flocks.session.runner import SessionRunner
from flocks.session.runtime_adapters import (
    FlocksHookPort,
    FlocksModelPort,
    FlocksPromptPort,
    FlocksToolPort,
)


@pytest.mark.asyncio
async def test_runner_uses_injected_ports_at_external_boundaries() -> None:
    prompt_port = SimpleNamespace(build_system_prompts=AsyncMock(return_value=[]))
    tool_port = SimpleNamespace(
        revision=MagicMock(return_value=7),
        list_tools=MagicMock(return_value=[]),
        get=MagicMock(return_value=None),
    )
    model_port = SimpleNamespace(
        get_provider=MagicMock(
            return_value=SimpleNamespace(_config_models=[]),
        ),
        apply_config=AsyncMock(),
        resolve_model=MagicMock(
            return_value=SimpleNamespace(
                capabilities=SimpleNamespace(interleaved=True),
            )
        ),
        resolve_model_info=MagicMock(return_value=(100_000, 8_192, None)),
    )
    hook_port = SimpleNamespace(
        run_session_start=AsyncMock(),
        has_stage_handlers=AsyncMock(return_value=False),
        run_llm_before=AsyncMock(),
        run_llm_after=AsyncMock(),
    )
    ports = ExternalRuntimePorts(
        prompts=prompt_port,
        tools=tool_port,
        models=model_port,
        hooks=hook_port,
    )
    runner = SessionRunner(
        session=SimpleNamespace(id="session-1", directory="/tmp"),
        provider_id="provider-a",
        model_id="model-a",
        runtime_ports=ports,
        session_start_pending=True,
    )

    await runner._run_session_start_hook(SimpleNamespace(name="rex"))
    capability_key = runner._provider_capability_key()
    cache_key = runner._tool_schema_cache_key(
        SimpleNamespace(name="rex", tools=[]),
        [],
        text_tool_call_mode=False,
    )

    hook_port.run_session_start.assert_awaited_once()
    model_port.resolve_model.assert_called_once_with("provider-a", "model-a")
    assert "interleaved=true" in capability_key
    assert cache_key[0] == 7


@pytest.mark.asyncio
async def test_default_adapters_preserve_existing_flocks_interfaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_prompts = AsyncMock(return_value=["system"])
    apply_config = AsyncMock()
    session_start = AsyncMock(return_value="hook-result")
    monkeypatch.setattr(
        "flocks.session.runtime_adapters.SessionPrompt.build_system_prompts",
        build_prompts,
    )
    monkeypatch.setattr(
        "flocks.session.runtime_adapters.ToolRegistry.revision",
        MagicMock(return_value=11),
    )
    monkeypatch.setattr(
        "flocks.session.runtime_adapters.Provider.get",
        MagicMock(return_value="provider"),
    )
    monkeypatch.setattr(
        "flocks.session.runtime_adapters.Provider.apply_config",
        apply_config,
    )
    monkeypatch.setattr(
        "flocks.session.runtime_adapters.HookPipeline.run_session_start",
        session_start,
    )

    assert await FlocksPromptPort().build_system_prompts(session_id="session-1") == ["system"]
    assert FlocksToolPort().revision() == 11
    assert FlocksModelPort().get_provider("provider-a") == "provider"
    await FlocksModelPort().apply_config("provider-a")
    assert await FlocksHookPort().run_session_start({"sessionID": "session-1"}) == ("hook-result")

    build_prompts.assert_awaited_once_with(session_id="session-1")
    apply_config.assert_awaited_once_with(provider_id="provider-a")
    session_start.assert_awaited_once_with({"sessionID": "session-1"})
