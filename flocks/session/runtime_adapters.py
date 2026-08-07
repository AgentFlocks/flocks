"""Default adapters from runtime ports to existing Flocks services."""

from __future__ import annotations

from typing import Any, Optional

from flocks.agent.runtime.ports import ExternalRuntimePorts
from flocks.hooks.pipeline import HookPipeline
from flocks.provider.provider import Provider
from flocks.session.prompt import SessionPrompt
from flocks.tool.registry import ToolRegistry


class FlocksPromptPort:
    """Adapt the existing SessionPrompt builder."""

    async def build_system_prompts(self, **kwargs: Any) -> list[str]:
        return await SessionPrompt.build_system_prompts(**kwargs)


class FlocksToolPort:
    """Adapt the process-wide ToolRegistry."""

    def revision(self) -> int:
        return ToolRegistry.revision()

    def list_tools(self) -> list[Any]:
        return ToolRegistry.list_tools()

    def get(self, name: str) -> Optional[Any]:
        return ToolRegistry.get(name)


class FlocksModelPort:
    """Adapt provider configuration and model metadata lookup."""

    def get_provider(self, provider_id: str) -> Optional[Any]:
        return Provider.get(provider_id)

    async def apply_config(self, provider_id: str) -> None:
        await Provider.apply_config(provider_id=provider_id)

    def resolve_model(self, provider_id: str, model_id: str) -> Optional[Any]:
        return Provider.resolve_model(provider_id, model_id)

    def resolve_model_info(
        self,
        provider_id: str,
        model_id: str,
    ) -> tuple[int, int, Optional[int]]:
        return Provider.resolve_model_info(provider_id, model_id)


class FlocksHookPort:
    """Adapt HookPipeline while preserving hook names and payloads."""

    async def run_session_start(self, data: dict[str, Any]) -> Any:
        return await HookPipeline.run_session_start(data)

    async def has_stage_handlers(
        self,
        stage: Any,
        metadata: dict[str, Any],
    ) -> bool:
        return await HookPipeline.has_stage_handlers(stage, metadata)

    async def run_llm_before(self, data: dict[str, Any]) -> Any:
        return await HookPipeline.run_llm_before(data)

    async def run_llm_after(
        self,
        metadata: dict[str, Any],
        result: dict[str, Any],
    ) -> Any:
        return await HookPipeline.run_llm_after(metadata, result)


def create_default_runtime_ports() -> ExternalRuntimePorts:
    """Create adapters for one runner without changing public APIs."""
    return ExternalRuntimePorts(
        prompts=FlocksPromptPort(),
        tools=FlocksToolPort(),
        models=FlocksModelPort(),
        hooks=FlocksHookPort(),
    )
