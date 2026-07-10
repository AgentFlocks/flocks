from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import flocks.tool.registry as registry_mod
from flocks.hooks.pipeline import ToolDecision
from flocks.tool.registry import (
    ParameterType,
    Tool,
    ToolCategory,
    ToolContext,
    ToolInfo,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)


def _tool(
    name: str,
    handler,
    *,
    parameters: list[ToolParameter] | None = None,
    source: str | None = None,
    provider: str | None = None,
) -> Tool:
    return Tool(
        info=ToolInfo(
            name=name,
            description="B3 final input test tool",
            category=ToolCategory.CUSTOM,
            parameters=parameters or [],
            enabled=True,
            source=source,
            provider=provider,
        ),
        handler=handler,
    )


def test_prepare_input_remaps_aliases_and_coerces_before_validation() -> None:
    async def _handler(_ctx: ToolContext, itemCount: int) -> ToolResult:
        return ToolResult(success=True, output=itemCount)

    tool = _tool(
        "b3_prepare_input",
        _handler,
        parameters=[
            ToolParameter(
                name="itemCount",
                type=ParameterType.INTEGER,
                required=True,
            )
        ],
    )

    prepared = tool.prepare_input({"item_count": "7"})

    assert prepared.kwargs == {"itemCount": 7}
    assert prepared.aliases == {"item_count": "itemCount"}
    assert prepared.validation_error is None


@pytest.mark.asyncio
async def test_policy_and_handler_receive_same_prepared_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def _handler(_ctx: ToolContext, itemCount: int) -> ToolResult:
        observed["handler"] = itemCount
        return ToolResult(success=True, output=itemCount)

    tool = _tool(
        "b3_prepared_gateway",
        _handler,
        parameters=[
            ToolParameter(
                name="itemCount",
                type=ParameterType.INTEGER,
                required=True,
            )
        ],
    )

    async def _before(payload):
        observed["policy"] = payload["tool"]["input"]
        return SimpleNamespace(output={"decision": {"action": "allow"}})

    monkeypatch.setattr(ToolRegistry, "get", lambda _name: tool)
    monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", _before)
    monkeypatch.setattr(
        "flocks.hooks.pipeline.HookPipeline.run_tool_after",
        AsyncMock(return_value=SimpleNamespace(output={})),
    )
    monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

    result = await ToolRegistry.execute(
        tool.info.name,
        ctx=ToolContext(session_id="s", message_id="m"),
        item_count="7",
    )

    assert result.success is True
    assert observed["policy"] == {"itemCount": 7}
    assert observed["handler"] == 7


@pytest.mark.asyncio
async def test_hook_cannot_mutate_prepared_nested_input_in_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def _handler(_ctx: ToolContext, payload: dict) -> ToolResult:
        observed["handler"] = payload
        return ToolResult(success=True, output="ok")

    tool = _tool(
        "b3_nested_input_isolation",
        _handler,
        parameters=[
            ToolParameter(
                name="payload",
                type=ParameterType.OBJECT,
                required=True,
            )
        ],
    )

    async def _before(hook_payload):
        hook_payload["tool"]["input"]["payload"]["value"] = "mutated"
        return SimpleNamespace(output={"decision": {"action": "allow"}})

    monkeypatch.setattr(ToolRegistry, "get", lambda _name: tool)
    monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", _before)
    monkeypatch.setattr(
        "flocks.hooks.pipeline.HookPipeline.run_tool_after",
        AsyncMock(return_value=SimpleNamespace(output={})),
    )
    monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

    result = await ToolRegistry.execute(
        tool.info.name,
        ctx=ToolContext(session_id="s", message_id="m"),
        payload={"value": "original"},
    )

    assert result.success is True
    assert observed["handler"] == {"value": "original"}


@pytest.mark.asyncio
async def test_resolved_device_identity_is_bound_before_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    async def _handler(_ctx: ToolContext) -> ToolResult:
        return ToolResult(success=True, output="ok")

    tool = _tool(
        "b3_device_gateway",
        _handler,
        source="device",
        provider="device-provider",
    )
    monkeypatch.setattr(ToolRegistry, "get", lambda _name: tool)
    monkeypatch.setattr(
        "flocks.tool.device.store.list_devices",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    id="device-1",
                    storage_key="device-provider",
                    enabled=True,
                )
            ]
        ),
    )

    async def _before(payload):
        observed["payload"] = payload
        return SimpleNamespace(output={"decision": {"action": "allow"}})

    @asynccontextmanager
    async def _activate(_device_id: str):
        yield True

    monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", _before)
    monkeypatch.setattr(
        "flocks.hooks.pipeline.HookPipeline.run_tool_after",
        AsyncMock(return_value=SimpleNamespace(output={})),
    )
    monkeypatch.setattr("flocks.tool.credential_context.activate_device_credentials", _activate)
    monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

    result = await ToolRegistry.execute(
        tool.info.name,
        ctx=ToolContext(
            session_id="s",
            message_id="m",
            extra={"execution_domain": "production"},
        ),
    )

    payload = observed["payload"]
    assert result.success is True
    assert payload["resource"] == {
        "type": "device",
        "id": "device-1",
        "provider": "device-provider",
    }
    assert payload["canonical"]["resource"] == payload["resource"]
    assert payload["canonical"]["execution_domain"] == "production"
    assert payload["canonical"]["hash"] is not None


@pytest.mark.asyncio
async def test_constraint_triggers_only_one_reprepare_recanonicalize_and_policy_recheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_inputs: list[dict[str, object]] = []
    observed_hashes: list[str | None] = []
    handled: dict[str, object] = {}

    async def _handler(_ctx: ToolContext, itemCount: int) -> ToolResult:
        handled["itemCount"] = itemCount
        return ToolResult(success=True, output=itemCount)

    tool = _tool(
        "b3_constraint_recheck",
        _handler,
        parameters=[
            ToolParameter(
                name="itemCount",
                type=ParameterType.INTEGER,
                required=True,
            )
        ],
    )

    async def _before(payload):
        observed_inputs.append(dict(payload["tool"]["input"]))
        observed_hashes.append(payload["canonical_hash"])
        if len(observed_inputs) == 1:
            return SimpleNamespace(
                output={
                    "policy_engine_present": True,
                    "decision": {
                        "action": "constrain",
                        "updated_input": {"item_count": "2"},
                    },
                }
            )
        return SimpleNamespace(
            output={
                "policy_engine_present": True,
                "decision": {"action": "allow"},
            }
        )

    monkeypatch.setattr(ToolRegistry, "get", lambda _name: tool)
    monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", _before)
    monkeypatch.setattr(
        "flocks.hooks.pipeline.HookPipeline.run_tool_after",
        AsyncMock(return_value=SimpleNamespace(output={})),
    )
    monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

    result = await ToolRegistry.execute(
        tool.info.name,
        ctx=ToolContext(session_id="s", message_id="m"),
        item_count="1",
    )

    assert result.success is True
    assert observed_inputs == [{"itemCount": 1}, {"itemCount": 2}]
    assert observed_hashes[0] != observed_hashes[1]
    assert handled == {"itemCount": 2}


@pytest.mark.asyncio
async def test_second_constraint_fails_closed_without_executing_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled = False
    policy_calls = 0

    async def _handler(_ctx: ToolContext, itemCount: int) -> ToolResult:
        nonlocal handled
        handled = True
        return ToolResult(success=True, output=itemCount)

    tool = _tool(
        "b3_constraint_recheck_limit",
        _handler,
        parameters=[
            ToolParameter(
                name="itemCount",
                type=ParameterType.INTEGER,
                required=True,
            )
        ],
    )

    async def _before(_payload):
        nonlocal policy_calls
        policy_calls += 1
        updated = 2 if policy_calls == 1 else 3
        return SimpleNamespace(
            output={
                "policy_engine_present": True,
                "decision": {
                    "action": "constrain",
                    "updated_input": {"itemCount": updated},
                    "matched_rule": f"constraint-{policy_calls}",
                    "policy_version": "b3-v1",
                },
            }
        )

    monkeypatch.setattr(ToolRegistry, "get", lambda _name: tool)
    monkeypatch.setattr("flocks.hooks.pipeline.HookPipeline.run_tool_before", _before)
    monkeypatch.setattr(
        "flocks.hooks.pipeline.HookPipeline.run_tool_after",
        AsyncMock(return_value=SimpleNamespace(output={})),
    )
    monkeypatch.setattr(registry_mod, "_emit_tool_audit", AsyncMock())

    result = await ToolRegistry.execute(
        tool.info.name,
        ctx=ToolContext(session_id="s", message_id="m"),
        itemCount=1,
    )

    assert result.success is False
    assert result.error == "constraint_recheck_limit"
    assert result.metadata["decision"]["action"] == "deny"
    assert result.metadata["decision"]["policy_version"] == "b3-v1"
    assert result.metadata["decision"]["updated_input"] == {"itemCount": 2}
    assert policy_calls == 2
    assert handled is False


@pytest.mark.parametrize("tool_name", ["read", "write", "edit"])
def test_prepare_input_rejects_empty_required_file_path(tool_name: str) -> None:
    async def _handler(_ctx: ToolContext, filePath: str) -> ToolResult:
        return ToolResult(success=True, output=filePath)

    tool = _tool(
        tool_name,
        _handler,
        parameters=[
            ToolParameter(
                name="filePath",
                type=ParameterType.STRING,
                required=True,
            )
        ],
    )

    prepared = tool.prepare_input({"filePath": ""})

    assert prepared.validation_error is not None


def test_prepare_input_resolves_glob_default_path(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)

    async def _handler(_ctx: ToolContext, pattern: str, path: str | None = None) -> ToolResult:
        return ToolResult(success=True, output=path or pattern)

    tool = _tool(
        "glob",
        _handler,
        parameters=[
            ToolParameter(name="pattern", type=ParameterType.STRING, required=True),
            ToolParameter(name="path", type=ParameterType.STRING, required=False),
        ],
    )

    prepared = tool.prepare_input({"pattern": "*.py"})

    assert prepared.validation_error is None
    assert prepared.kwargs == {"pattern": "*.py"}
    assert prepared.resource == {"type": "file", "id": str(tmp_path.resolve())}


def test_second_policy_decision_cannot_weaken_first_decision() -> None:
    from flocks.hooks.pipeline import merge_tool_decisions

    merged = merge_tool_decisions(
        ToolDecision(
            action="constrain",
            updated_input={"itemCount": 2},
            policy_version="b3-v1",
        ),
        ToolDecision(action="allow"),
    )

    assert merged.action == "constrain"
    assert merged.updated_input == {"itemCount": 2}
    assert merged.policy_version == "b3-v1"
