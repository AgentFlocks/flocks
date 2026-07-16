from dataclasses import FrozenInstanceError

import pytest

from flocks.agent.toolset import resolve_capability_pool


def test_capability_pool_only_removes_from_agent_enabled_set() -> None:
    pool = resolve_capability_pool(
        declared_tools=["read", "bash"],
        enabled_tools=["read"],
    )

    assert pool.tools == ("read",)


def test_capability_pool_applies_parent_ceiling_after_enabled_tools() -> None:
    pool = resolve_capability_pool(
        declared_tools=["read", "bash", "write"],
        enabled_tools=["read", "bash"],
        parent_ceiling_tools=["bash", "write"],
    )

    assert pool.tools == ("bash",)


def test_capability_pool_intersection_is_immutable() -> None:
    from flocks.security.capability_pool import CapabilityPool

    declared = CapabilityPool.from_tools(["read", "bash", "read"], context={})
    ceiling = CapabilityPool.from_tools(["bash"], context={})
    effective = declared.intersect(ceiling, source="parent_ceiling")

    assert effective.tools == ("bash",)
    with pytest.raises(FrozenInstanceError):
        effective.tools = ("read",)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_capability_filter_returns_base_pool_when_no_hook_is_registered() -> None:
    from flocks.security.capability_pool import filter_capability_pool

    base = resolve_capability_pool(
        declared_tools=["read"],
        enabled_tools=["read", "bash"],
    )

    filtered = await filter_capability_pool(base, context={})

    assert filtered == base


@pytest.mark.asyncio
async def test_capability_filter_applies_global_tool_policy_without_sandbox() -> None:
    """tool_policy is a B4 capability constraint, not only sandbox metadata."""
    from flocks.security.capability_pool import filter_capability_pool

    base = resolve_capability_pool(
        declared_tools=["read", "bash"],
        enabled_tools=["read", "bash"],
    )

    filtered = await filter_capability_pool(
        base,
        context={
            "agent": "rex",
            "_config_data": {
                "sandbox": {
                    "mode": "off",
                    "tools": {"allow": ["read"]},
                },
            },
        },
    )

    assert filtered.tools == ("read",)
    assert "sandbox.tool_policy" in filtered.filtered_by


@pytest.mark.asyncio
async def test_capability_filter_discovers_cold_hooks_before_returning_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookPipeline, HookStage
    from flocks.security.capability_pool import filter_capability_pool

    discovery = AsyncMock(return_value=False)
    monkeypatch.setattr(HookPipeline, "has_stage_handlers", discovery)
    base = resolve_capability_pool(
        declared_tools=["read"],
        enabled_tools=["read", "bash"],
    )

    filtered = await filter_capability_pool(
        base,
        context={
            "entry": "runner",
            "agent": "rex",
            "workspace": "/trusted/workspace",
            "sessionID": "ses-capability-context",
        },
    )

    assert filtered == base
    discovery.assert_awaited_once()
    stage, hook_input = discovery.await_args.args
    assert stage == HookStage.CAPABILITY_FILTER
    assert hook_input["capability_pool"] == base.as_dict()
    assert hook_input["entry"] == "runner"
    assert hook_input["agent"] == "rex"
    assert hook_input["workspace"] == "/trusted/workspace"
    assert hook_input["sessionID"] == "ses-capability-context"


@pytest.mark.asyncio
async def test_capability_filter_cannot_add_tool_outside_base_pool() -> None:
    from flocks.hooks.pipeline import HookBase, HookPipeline, HookStage
    from flocks.security.capability_pool import filter_capability_pool

    seen_input = {}

    class _AdversarialHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            seen_input.update(ctx.input)
            ctx.output["capability_pool"] = {"tools": ["read", "bash"]}

    HookPipeline.register("test-capability-filter-add", _AdversarialHook())
    try:
        base = resolve_capability_pool(
            declared_tools=["read"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(
            base,
            context={
                "subject": {"subject_id": "user-1"},
                "entry": "api",
                "permission_mode": "default_interactive",
                "execution_mode": "foreground",
                "agent": "rex",
                "secret": "must-not-be-forwarded",
            },
        )
    finally:
        HookPipeline.unregister("test-capability-filter-add")

    assert filtered.tools == ("read",)
    assert filtered.filtered_by[-1] == "capability.filter"
    assert filtered.removed_count == 0
    assert seen_input == {
        "capability_pool": base.as_dict(),
        "subject": {"subject_id": "user-1"},
        "entry": "api",
        "permission_mode": "default_interactive",
        "execution_mode": "foreground",
        "agent": "rex",
        "workspace": "",
        "sessionID": "",
    }
    assert HookStage.CAPABILITY_FILTER == "capability.filter"


@pytest.mark.asyncio
async def test_capability_filter_forwards_trusted_tenant_id_without_subject_or_context_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.security.capability_pool import filter_capability_pool

    seen_input = {}

    class _CaptureHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            seen_input.update(ctx.input)
            ctx.output["capability_pool"] = {"tools": ["read"]}

    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-capability-filter-tenant", _CaptureHook())
    try:
        base = resolve_capability_pool(
            declared_tools=["read", "bash"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(
            base,
            context={
                "subject": {
                    "subject_id": "user-1",
                    "tenant_id": "tenant-a",
                    "subject_secret": "do-not-forward",
                },
                "secret": "also-do-not-forward",
            },
        )
    finally:
        HookPipeline.unregister("test-capability-filter-tenant")

    assert filtered.tools == ("read",)
    assert seen_input["subject"] == {"subject_id": "user-1", "tenant_id": "tenant-a"}
    assert "do-not-forward" not in str(seen_input)


@pytest.mark.parametrize(
    ("subject", "expected_tenant_id"),
    [
        (
            {"tenant_id": " tenant-b ", "tenant_ids": ("tenant-a", "tenant-b", "tenant-a")},
            "tenant-b",
        ),
        ({"tenant_id": "tenant-z", "tenant_ids": ("tenant-a",)}, "tenant-a"),
        ({"tenant_id": "tenant-z", "tenant_ids": ("tenant-a", "tenant-b")}, None),
        ({"tenant_id": " ", "tenant_ids": ()}, None),
    ],
)
def test_safe_subject_derives_singular_tenant_only_from_a_valid_binding(
    subject,
    expected_tenant_id,
) -> None:
    from flocks.security.capability_pool import _safe_subject

    safe_subject = _safe_subject({"subject_id": "user-1", "secret": "do-not-forward", **subject})

    assert safe_subject.get("tenant_id") == expected_tenant_id
    assert "secret" not in safe_subject


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant_ids", "expected_tenant_id"),
    [(("tenant-a",), "tenant-a"), (("tenant-a", "tenant-b"), None)],
)
async def test_capability_filter_derives_current_subject_tenant_binding(
    monkeypatch: pytest.MonkeyPatch,
    tenant_ids: tuple[str, ...],
    expected_tenant_id: str | None,
) -> None:
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.identity.subject import Subject, reset_current_subject, set_current_subject
    from flocks.security.capability_pool import filter_capability_pool

    seen_input = {}

    class _CaptureHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            seen_input.update(ctx.input)
            ctx.output["capability_pool"] = {"tools": ["read"]}

    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-capability-filter-current-tenant", _CaptureHook())
    token = set_current_subject(Subject(subject_id="user-1", tenant_ids=tenant_ids))
    try:
        base = resolve_capability_pool(
            declared_tools=["read", "bash"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(base, context={})
    finally:
        reset_current_subject(token)
        HookPipeline.unregister("test-capability-filter-current-tenant")

    assert filtered.tools == ("read",)
    assert seen_input["subject"].get("tenant_id") == expected_tenant_id
    if expected_tenant_id is None:
        assert "tenant_id" not in seen_input["subject"]


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_output", [{}, {"tools": "read"}])
async def test_missing_or_malformed_capability_filter_output_leaves_base_pool_unchanged(
    hook_output,
) -> None:
    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.security.capability_pool import filter_capability_pool

    class _MalformedHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = hook_output

    HookPipeline.register("test-capability-filter-malformed", _MalformedHook())
    try:
        base = resolve_capability_pool(
            declared_tools=["read"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(base, context={})
    finally:
        HookPipeline.unregister("test-capability-filter-malformed")

    assert filtered == base


@pytest.mark.asyncio
async def test_capability_filter_removes_requested_base_tool_and_records_count() -> None:
    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.security.capability_pool import filter_capability_pool

    class _RestrictiveHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = {"tools": ["read"]}

    HookPipeline.register("test-capability-filter-restrict", _RestrictiveHook())
    try:
        base = resolve_capability_pool(
            declared_tools=["read", "bash"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(base, context={})
    finally:
        HookPipeline.unregister("test-capability-filter-restrict")

    assert filtered.tools == ("read",)
    assert filtered.filtered_by[-1] == "capability.filter"
    assert filtered.removed_count == 1


@pytest.mark.asyncio
async def test_capability_filters_are_monotonic_across_multiple_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.security.capability_pool import filter_capability_pool

    class _RemoveBash(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = {"tools": ["read"]}

    class _RestoreBash(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = {"tools": ["read", "bash"]}

    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-capability-filter-remove", _RemoveBash(), order=1)
    HookPipeline.register("test-capability-filter-restore", _RestoreBash(), order=2)
    try:
        base = resolve_capability_pool(
            declared_tools=["read", "bash"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(base, context={})
    finally:
        HookPipeline.unregister("test-capability-filter-remove")
        HookPipeline.unregister("test-capability-filter-restore")

    assert filtered.tools == ("read",)


@pytest.mark.asyncio
async def test_capability_stage_records_safe_candidates_in_handler_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookBase, HookPipeline

    class _FirstHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = {"tools": ["read"], "secret": "omit"}

    class _SecondHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = {"tools": ["read", "bash"]}

    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-capability-filter-first", _FirstHook(), order=1)
    HookPipeline.register("test-capability-filter-second", _SecondHook(), order=2)
    try:
        hook_context = await HookPipeline.run_capability_filter({})
    finally:
        HookPipeline.unregister("test-capability-filter-first")
        HookPipeline.unregister("test-capability-filter-second")

    assert hook_context.output["capability_filters"] == [
        {"tools": ["read"]},
        {"tools": ["read", "bash"]},
    ]


@pytest.mark.asyncio
async def test_capability_filter_accepts_mapping_hook_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import MappingProxyType
    from unittest.mock import AsyncMock

    from flocks.hooks.pipeline import HookBase, HookPipeline
    from flocks.security.capability_pool import filter_capability_pool

    class _MappingHook(HookBase):
        async def capability_filter(self, ctx) -> None:
            ctx.output["capability_pool"] = MappingProxyType({"tools": ["read"]})

    monkeypatch.setattr(HookPipeline, "ensure_initialized", AsyncMock())
    HookPipeline.register("test-capability-filter-mapping", _MappingHook())
    try:
        base = resolve_capability_pool(
            declared_tools=["read", "bash"],
            enabled_tools=["read", "bash"],
        )
        filtered = await filter_capability_pool(base, context={})
    finally:
        HookPipeline.unregister("test-capability-filter-mapping")

    assert filtered.tools == ("read",)
