"""Durable permission rules API (/api/permission/rules).

The WebUI Permission page used to POST /api/permission and DELETE
/api/permission/{id}, neither of which existed (405), so rules could not be
managed at all. The rules now live in PermissionNext's permanent-rule store
(the same one an "always" reply writes to) behind admin-only mutations.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.auth.context import AuthUser
from flocks.permission.next import PermissionNext
from flocks.server.routes import permission as permission_routes
from flocks.storage.storage import Storage


@pytest.fixture
async def permission_storage():
    with tempfile.TemporaryDirectory() as tmpdir:
        await Storage.init(Path(tmpdir) / "permission.db")
        PermissionNext._pending = {}
        PermissionNext._permanent_rules = {}
        PermissionNext._session_permissions = {}
        PermissionNext._state_loaded = False
        yield
        PermissionNext._pending = {}
        PermissionNext._permanent_rules = {}
        PermissionNext._state_loaded = False
        await Storage.shutdown()


def _client(role: str) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def user_auth(request, call_next):
        request.state.auth_user = AuthUser(id=f"{role}-1", username=role, role=role, status="active", must_reset_password=False)
        return await call_next(request)

    app.include_router(permission_routes.router, prefix="/api/permission")
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.asyncio
async def test_rules_crud_round_trip(permission_storage):
    admin = _client("admin")
    assert admin.get("/api/permission/rules").json() == []

    created = admin.post("/api/permission/rules", json={"permission": "bash", "action": "deny", "pattern": "*"})
    assert created.status_code == 201, created.text
    assert created.json() == {"id": "bash", "permission": "bash", "action": "deny", "pattern": "*", "createdAt": None}

    listed = admin.get("/api/permission/rules").json()
    assert [(r["permission"], r["action"]) for r in listed] == [("bash", "deny")]
    # the rule is the same durable store an "always" reply writes to
    assert await PermissionNext.list_permanent_rules() == {"bash": "deny"}
    assert await Storage.get("permission_rule:bash") == "deny"

    assert admin.delete("/api/permission/rules/bash").status_code == 204
    assert admin.get("/api/permission/rules").json() == []
    assert admin.delete("/api/permission/rules/bash").status_code == 404


@pytest.mark.asyncio
async def test_rules_validation(permission_storage):
    admin = _client("admin")
    assert admin.post("/api/permission/rules", json={"permission": "  ", "action": "deny"}).status_code == 422
    assert admin.post("/api/permission/rules", json={"permission": "bash", "action": "maybe"}).status_code == 422
    scoped = admin.post("/api/permission/rules", json={"permission": "bash", "action": "deny", "pattern": "rm *"})
    assert scoped.status_code == 422 and "pattern must be '*'" in scoped.text
    assert admin.get("/api/permission/rules").json() == []


@pytest.mark.asyncio
async def test_rule_mutations_require_admin(permission_storage):
    member = _client("member")
    assert member.get("/api/permission/rules").status_code == 200
    assert member.post("/api/permission/rules", json={"permission": "bash", "action": "deny"}).status_code == 403
    assert member.delete("/api/permission/rules/bash").status_code == 403
    assert await PermissionNext.list_permanent_rules() == {}
