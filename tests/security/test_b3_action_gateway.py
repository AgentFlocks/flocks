from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from flocks.hooks.pipeline import HookBase, HookPipeline, ToolDecision
import flocks.security.action_gateway as action_gateway
from flocks.security.action_gateway import (
    ActionDeniedError,
    ActionPendingError,
    SecurityAction,
    enforce_action_decision,
    run_before_action,
)
from flocks.server.routes import config as config_routes
from flocks.server.routes import mcp as mcp_routes
from flocks.server.routes import workflow as workflow_routes

def _deny_route(monkeypatch: pytest.MonkeyPatch, route_module):
    calls = []

    async def _deny(action, *, subject=None):
        calls.append((action, subject))
        return ToolDecision(action="deny", reason="test_policy_deny")

    monkeypatch.setattr(route_module, "run_before_action", _deny, raising=False)

    # Newer route families delegate the entire effect to the neutral gateway
    # instead of spelling out before/enforce in each endpoint.  Keep this test
    # helper at that same public boundary so both migration shapes prove the
    # side effect is unreachable after a deny.
    async def _deny_execute(action, effect, *, subject=None):
        calls.append((action, subject))
        raise ActionDeniedError(ToolDecision(action="deny", reason="test_policy_deny"))

    monkeypatch.setattr(route_module, "execute_action", _deny_execute, raising=False)
    return calls


async def _assert_typed_deny(call, side_effect: Mock, gateway_calls: list) -> None:
    # Direct route calls retain the historical typed gateway exception for
    # hand-wired endpoints; the generic workflow Router correctly adapts the
    # same deny to HTTP 403 for FastAPI.  Both must stop the side effect.
    with pytest.raises((ActionDeniedError, HTTPException)) as exc_info:
        await call()

    if isinstance(exc_info.value, HTTPException):
        assert exc_info.value.status_code == 403

    side_effect.assert_not_called()
    assert len(gateway_calls) == 1


@pytest.fixture(autouse=True)
def _clean_manual_hooks(monkeypatch: pytest.MonkeyPatch):
    HookPipeline.reset()
    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    yield
    HookPipeline.reset()


@pytest.mark.asyncio
async def test_action_gateway_is_passthrough_without_active_policy() -> None:
    decision = await run_before_action(
        SecurityAction(
            action="configure",
            resource={"type": "mcp_server", "id": "demo"},
            canonical_input={"b": 2, "a": 1},
            execution_domain="control_plane",
            metadata={"entry": "api"},
        )
    )

    assert decision == ToolDecision(action="allow")


@pytest.mark.asyncio
async def test_action_gateway_executes_effect_and_emits_outcome_without_policy() -> None:
    observed = []
    effect = AsyncMock(return_value={"ok": True, "secret": "must-not-be-forwarded"})

    class _OutcomeHook(HookBase):
        async def action_after(self, ctx) -> None:
            observed.append(deepcopy(ctx.input))

    HookPipeline.register("capture-action-outcome", _OutcomeHook())
    try:
        execute_action = getattr(action_gateway, "execute_action", None)
        assert callable(execute_action)
        result = await execute_action(
            SecurityAction(
                action="configure",
                resource={"type": "mcp_server", "id": "demo"},
                canonical_input={"name": "demo"},
                execution_domain="control_plane",
                metadata={"entry": "api"},
            ),
            effect,
        )
    finally:
        HookPipeline.unregister("capture-action-outcome")

    assert result == {"ok": True, "secret": "must-not-be-forwarded"}
    effect.assert_awaited_once()
    assert observed[0]["phase"] == "after_action"
    assert observed[0]["outcome"] == {"success": True}
    assert "secret" not in observed[0]


@pytest.mark.asyncio
async def test_action_gateway_after_hook_allowlists_outcome_and_decision_fields() -> None:
    observed = []

    class _OutcomeHook(HookBase):
        async def action_after(self, ctx) -> None:
            observed.append(deepcopy(ctx.input))

    HookPipeline.register("capture-action-after-schema", _OutcomeHook())
    try:
        await action_gateway.run_after_action(
            SecurityAction(
                action="configure",
                resource={"type": "mcp_server", "id": "demo"},
                canonical_input={"token": "must-not-leak"},
                execution_domain="control_plane",
            ),
            decision=ToolDecision(action="allow", updated_input={"token": "must-not-leak"}),
            outcome={"success": True, "token": "must-not-leak", "nested": {"secret": "no"}},
        )
    finally:
        HookPipeline.unregister("capture-action-after-schema")

    assert observed[0]["outcome"] == {"success": True}
    assert "updated_input" not in observed[0]["decision"]


@pytest.mark.asyncio
async def test_action_gateway_is_canonical_and_deterministic() -> None:
    inputs = []

    class _CaptureHook(HookBase):
        async def action_before(self, ctx) -> None:
            inputs.append(deepcopy(ctx.input))

    HookPipeline.register("capture-actions", _CaptureHook())
    try:
        first = SecurityAction(
            action="configure",
            resource={"id": "demo", "type": "mcp_server"},
            canonical_input={"b": 2, "a": 1},
            execution_domain="control_plane",
            metadata={},
        )
        second = SecurityAction(
            action="configure",
            resource={"type": "mcp_server", "id": "demo"},
            canonical_input={"a": 1, "b": 2},
            execution_domain="control_plane",
            metadata={},
        )
        await run_before_action(first)
        await run_before_action(second)
    finally:
        HookPipeline.unregister("capture-actions")

    assert inputs[0]["canonical"]["status"] == "ok"
    assert inputs[0]["canonical_hash"] == inputs[1]["canonical_hash"]


@pytest.mark.asyncio
async def test_action_gateway_fails_closed_for_malformed_active_policy() -> None:
    class _MalformedPolicy(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True

    HookPipeline.register("malformed-action-policy", _MalformedPolicy())
    try:
        decision = await run_before_action(
            SecurityAction(
                action="publish",
                resource={"type": "workflow", "id": "wf-1"},
                canonical_input={},
                execution_domain="control_plane",
                metadata={},
            )
        )
    finally:
        HookPipeline.unregister("malformed-action-policy")

    assert decision.action == "deny"
    assert decision.reason == "invalid_policy_decision"


@pytest.mark.asyncio
async def test_action_gateway_decisions_are_monotonic() -> None:
    class _Approval(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["policy_engine_present"] = True
            ctx.output["decision"] = {"action": "ask", "mode": "approval"}

    class _LaterAllow(HookBase):
        async def action_before(self, ctx) -> None:
            ctx.output["decision"] = {"action": "allow"}

    HookPipeline.register("action-approval", _Approval(), order=1)
    HookPipeline.register("action-later-allow", _LaterAllow(), order=2)
    try:
        decision = await run_before_action(
            SecurityAction(
                action="delete",
                resource={"type": "mcp_server", "id": "demo"},
                canonical_input={},
                execution_domain="control_plane",
                metadata={},
            )
        )
    finally:
        HookPipeline.unregister("action-approval")
        HookPipeline.unregister("action-later-allow")

    assert decision.action == "ask"
    assert decision.mode == "approval"


def test_enforce_action_decision_raises_typed_deny_and_pending() -> None:
    with pytest.raises(ActionDeniedError):
        enforce_action_decision(ToolDecision(action="deny", reason="blocked"))
    with pytest.raises(ActionPendingError):
        enforce_action_decision(
            ToolDecision(action="ask", mode="approval", reason="approval_required")
        )


@pytest.mark.asyncio
async def test_publish_workflow_denied_before_registry_write(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, workflow_routes)
    first_side_effect = AsyncMock(side_effect=AssertionError("registry write reached"))
    monkeypatch.setattr(workflow_routes, "_prepare_workflow_api_registry", first_side_effect)

    await _assert_typed_deny(
        lambda: workflow_routes.publish_workflow_as_api("wf-1"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_unpublish_workflow_denied_before_runtime_stop(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, workflow_routes)
    monkeypatch.setattr(
        workflow_routes.WorkflowStore,
        "kv_get",
        AsyncMock(return_value={"workflowId": "wf-1", "status": "running"}),
    )
    first_side_effect = AsyncMock(side_effect=AssertionError("runtime stop reached"))
    monkeypatch.setattr(workflow_routes, "stop_workflow_service", first_side_effect)

    await _assert_typed_deny(
        lambda: workflow_routes.unpublish_workflow_api("wf-1"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_workflow_center_publish_denied_before_publish(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, workflow_routes)
    first_side_effect = AsyncMock(side_effect=AssertionError("workflow center publish reached"))
    monkeypatch.setattr(workflow_routes, "publish_workflow", first_side_effect)

    await _assert_typed_deny(
        lambda: workflow_routes.workflow_center_publish("wf-center-1"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_workflow_center_stop_denied_before_runtime_stop(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, workflow_routes)
    first_side_effect = AsyncMock(side_effect=AssertionError("workflow center stop reached"))
    monkeypatch.setattr(workflow_routes, "stop_workflow_service", first_side_effect)

    await _assert_typed_deny(
        lambda: workflow_routes.workflow_center_stop("wf-center-1"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_add_mcp_server_denied_before_config_preparation(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, mcp_routes)
    first_side_effect = Mock(side_effect=AssertionError("secret/config write reached"))
    monkeypatch.setattr(mcp_routes, "_prepare_mcp_config_for_save", first_side_effect)

    await _assert_typed_deny(
        lambda: mcp_routes.add_mcp_server(
            mcp_routes.McpAddRequest(name="demo", config={"type": "remote", "url": "https://example.test"})
        ),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_remove_mcp_server_denied_before_config_delete(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, mcp_routes)
    first_side_effect = Mock(side_effect=AssertionError("config delete reached"))
    monkeypatch.setattr(mcp_routes.ConfigWriter, "remove_mcp_server", first_side_effect)

    await _assert_typed_deny(
        lambda: mcp_routes.remove_mcp_server("demo"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_update_mcp_server_denied_before_config_write(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, mcp_routes)
    monkeypatch.setattr(
        mcp_routes,
        "_load_raw_mcp_server_config",
        lambda _name: {"type": "local", "command": ["old"]},
    )
    first_side_effect = Mock(side_effect=AssertionError("config write reached"))
    monkeypatch.setattr(mcp_routes, "_persist_mcp_server_config", first_side_effect)

    await _assert_typed_deny(
        lambda: mcp_routes.update_mcp_server(
            "demo", mcp_routes.McpUpdateRequest(config={"command": ["new"]})
        ),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_set_mcp_credentials_denied_before_secret_write(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, mcp_routes)
    first_side_effect = Mock(side_effect=AssertionError("secret store reached"))
    monkeypatch.setattr("flocks.security.get_secret_manager", first_side_effect)

    await _assert_typed_deny(
        lambda: mcp_routes.set_mcp_credentials(
            "demo", mcp_routes.McpCredentialRequest(api_key="secret")
        ),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_delete_mcp_credentials_denied_before_secret_delete(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, mcp_routes)
    first_side_effect = Mock(side_effect=AssertionError("secret store reached"))
    monkeypatch.setattr("flocks.security.get_secret_manager", first_side_effect)

    await _assert_typed_deny(
        lambda: mcp_routes.delete_mcp_credentials("demo"),
        first_side_effect,
        gateway_calls,
    )


@pytest.mark.asyncio
async def test_update_config_denied_before_persistence(monkeypatch) -> None:
    gateway_calls = _deny_route(monkeypatch, config_routes)
    monkeypatch.setattr(
        config_routes.ConfigInfoModel,
        "model_validate",
        Mock(return_value=SimpleNamespace()),
    )
    first_side_effect = AsyncMock(side_effect=AssertionError("config update reached"))
    monkeypatch.setattr(config_routes.Config, "update", first_side_effect)

    await _assert_typed_deny(
        lambda: config_routes.update_config({"theme": "dark"}),
        first_side_effect,
        gateway_calls,
    )
