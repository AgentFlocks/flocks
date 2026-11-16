"""Routes that reload plugin tools keep the rest of the server answering.

A reload re-reads every plugin source and takes seconds. Run on the event
loop, it held up every other request on the server until it ended.
"""

from __future__ import annotations

import asyncio
import threading

import httpx
import pytest
from fastapi import FastAPI

from flocks.auth.context import AuthUser
from flocks.server.auth import require_admin
from flocks.tool.registry import ToolRegistry

_ADMIN = AuthUser(
    id="admin-test",
    username="admin-test",
    role="admin",
    status="active",
    must_reset_password=False,
)


def _app() -> FastAPI:
    from flocks.server.routes.device import router as device_router
    from flocks.server.routes.tool import router as tool_router

    app = FastAPI()
    app.include_router(tool_router, prefix="/api/tools")
    app.include_router(device_router, prefix="/api/devices")
    app.dependency_overrides[require_admin] = lambda: _ADMIN

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


class _HeldReload:
    """A stand-in reload that blocks its thread until released."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, *_args, **_kwargs):
        self.calls += 1
        self.entered.set()
        assert self.release.wait(timeout=5)
        return []


async def _ping_while_held(
    app: FastAPI,
    method: str,
    url: str,
    held: _HeldReload,
    json: dict | None = None,
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        slow = asyncio.create_task(client.request(method, url, json=json))
        try:
            # The reload starts on a worker thread; the loop stays free meanwhile.
            for _ in range(200):
                if held.entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert held.entered.is_set(), "the reload never started"
            ping = await asyncio.wait_for(client.get("/ping"), timeout=1)
            assert ping.status_code == 200
            assert not slow.done(), "the reload finished before the ping was answered"
        finally:
            held.release.set()
        return await asyncio.wait_for(slow, timeout=5)


@pytest.mark.asyncio
async def test_tools_refresh_runs_reloads_off_the_event_loop(monkeypatch: pytest.MonkeyPatch):
    held = _HeldReload()
    monkeypatch.setattr(ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(ToolRegistry, "refresh_dynamic_tools", classmethod(lambda cls: held()))
    monkeypatch.setattr(ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls, changed_path=None: []))
    monkeypatch.setattr(ToolRegistry, "all_tool_ids", classmethod(lambda cls: []))

    response = await _ping_while_held(_app(), "POST", "/api/tools/refresh", held)

    assert response.status_code == 200, response.text
    assert response.json()["stages"] == {"dynamic": "success", "plugin": "success"}


@pytest.mark.asyncio
async def test_tool_delete_reloads_off_the_event_loop(monkeypatch: pytest.MonkeyPatch):
    from flocks.hub import local as hub_local
    from flocks.tool import tool_loader

    held = _HeldReload()
    monkeypatch.setattr(ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls, changed_path=None: held()))
    monkeypatch.setattr(tool_loader, "find_yaml_tool", lambda name: True)
    monkeypatch.setattr(tool_loader, "delete_yaml_tool", lambda name: True)
    monkeypatch.setattr(hub_local, "remove_installed_record", lambda *_args, **_kwargs: None)

    response = await _ping_while_held(_app(), "DELETE", "/api/tools/held_plugin_tool", held)

    assert response.status_code == 200, response.text
    assert held.calls == 1


@pytest.mark.asyncio
async def test_custom_device_template_is_created_off_the_event_loop(monkeypatch: pytest.MonkeyPatch):
    from flocks.server.routes import device as device_routes
    from flocks.tool.device.models import DeviceTemplate

    held = _HeldReload()
    template = DeviceTemplate(
        plugin_id="held_device",
        storage_key="held_device",
        service_id="held_device",
        name="Held device",
        installed=True,
        state="installed",
        source="global",
    )
    monkeypatch.setattr(device_routes, "require_admin", lambda request: _ADMIN)

    def _create(body):
        held()
        return template

    monkeypatch.setattr(device_routes, "create_custom_device_template", _create)

    response = await _ping_while_held(
        _app(),
        "POST",
        "/api/devices/templates/custom",
        held,
        json={"plugin_id": "held_device", "name": "Held device", "service_id": "held_device"},
    )

    assert response.status_code == 201, response.text
    assert response.json()["plugin_id"] == "held_device"
    assert held.calls == 1
