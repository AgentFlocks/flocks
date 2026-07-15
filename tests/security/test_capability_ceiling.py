from __future__ import annotations

import pytest

from flocks.security.capability_pool import (
    build_capability_ceiling,
    derive_child_capability_ceiling,
)
from flocks.security.delegation_context import (
    resolve_session_security_context,
    store_delegation_security_context,
)


def test_capability_ceiling_is_monotonic_across_delegation_chain() -> None:
    parent = build_capability_ceiling(
        tools=["read", "write", "bash"],
        context={
            "permission_mode": "readonly",
            "execution_mode": "execution",
            "development_mode": "locked",
            "data_domains": ["tenant-a", "shared"],
            "secret_scopes": ["read-only"],
            "network_profile": "internal-only",
            "secret": "must-not-leak",
        },
    )
    child = derive_child_capability_ceiling(
        parent,
        child_tools=["read", "bash", "deploy"],
    )
    grandchild = derive_child_capability_ceiling(
        child,
        child_tools=["bash", "deploy"],
    )

    assert parent == {
        "tools": ["read", "write", "bash"],
        "permission_mode": "readonly",
        "execution_mode": "execution",
        "development_mode": "locked",
        "data_domains": ["tenant-a", "shared"],
        "secret_scopes": ["read-only"],
        "network_profile": "internal-only",
    }
    assert child["tools"] == ["read", "bash"]
    assert grandchild["tools"] == ["bash"]
    assert grandchild["permission_mode"] == "readonly"
    assert grandchild["data_domains"] == ["tenant-a", "shared"]
    assert "must-not-leak" not in str(grandchild)


def test_malformed_parent_ceiling_is_explicit_and_cannot_widen() -> None:
    child = derive_child_capability_ceiling(
        {"tools": "not-a-list", "secret": "must-not-leak"},
        child_tools=["read", "bash"],
    )

    assert child == {"invalid": True}


@pytest.mark.asyncio
async def test_child_callable_schema_intersects_persisted_parent_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    import flocks.session.callable_schema as callable_schema
    from flocks.tool.registry import ToolRegistry

    ToolRegistry.init()
    monkeypatch.setattr(
        callable_schema,
        "get_session_callable_tools",
        AsyncMock(return_value={"read", "bash"}),
    )
    monkeypatch.setattr(callable_schema, "get_always_load_tool_names", lambda: set())
    monkeypatch.setattr(
        callable_schema,
        "_resolve_dynamic_always_load_tool_names",
        AsyncMock(return_value=set()),
    )

    result = await callable_schema.list_session_callable_tool_infos(
        "child-session",
        capability_context={"parent_ceiling": {"tools": ["read"]}},
    )

    assert [tool.name for tool in result.tool_infos] == ["read"]
    assert result.capability_ceiling["tools"] == ["read"]


@pytest.mark.asyncio
async def test_marked_delegated_session_restores_original_ceiling_on_continuation(
    tmp_path,
) -> None:
    from flocks.storage.storage import Storage

    await Storage.init(tmp_path / "delegation-context.db")
    await store_delegation_security_context(
        "child-session",
        {
            "parent_ceiling": {"tools": ["read"], "secret": "must-not-leak"},
            "subject": {"subject_id": "user-1", "secret": "must-not-leak"},
        },
    )

    continued = await resolve_session_security_context(
        "child-session",
        delegation_context_required=True,
        supplied_context={"parent_ceiling": {"tools": ["bash"]}, "secret": "must-not-leak"},
    )
    root = await resolve_session_security_context(
        "root-session",
        delegation_context_required=False,
        supplied_context={"entry": "webui"},
    )

    assert continued["parent_ceiling"] == {"tools": ["read"]}
    assert continued["subject"] == {"subject_id": "user-1"}
    assert "must-not-leak" not in str(continued)
    assert root == {"entry": "webui"}
