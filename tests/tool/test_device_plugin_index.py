from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import yaml
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from flocks.tool.device.models import CustomDeviceTemplateCreate


def _write_provider(root: Path, data: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "_provider.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _write_tool(root: Path, name: str = "device_ping") -> None:
    (root / f"{name}.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "Ping device",
                "provider": "demo_api",
                "handler": {"type": "http", "path": "/ping"},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )


def _reset_env(monkeypatch, tmp_path):
    from flocks.config.config import Config
    from flocks.config import api_versioning
    from flocks.storage.storage import Storage

    home = tmp_path / "home"
    data = tmp_path / "data"
    project = tmp_path / "project"
    home.mkdir()
    data.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FLOCKS_DATA_DIR", str(data))
    monkeypatch.chdir(project)
    Config._global_config = None
    Config._cached_config = None
    Storage._db_path = None
    Storage._initialized = False
    api_versioning._reset_descriptor_cache()
    return home, data, project


def test_device_plugin_index_filters_and_shapes_templates(monkeypatch, tmp_path):
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    root = tmp_path / "bundled" / "demo_device"
    _write_provider(
        root,
        {
            "name": "Demo Device",
            "service_id": "demo_api",
            "version": "1.2.3",
            "integration_type": "device",
            "vendor": "demo",
            "docs_url": "https://docs.example.com/demo-device",
            "credential_fields": [
                {"key": "base_url", "label": "Base URL", "storage": "config"},
            ],
        },
    )
    _write_tool(root)

    api_root = tmp_path / "bundled" / "not_device"
    _write_provider(
        api_root,
        {
            "name": "API Only",
            "service_id": "api_only",
            "integration_type": "api",
        },
    )

    entries = [
        SimpleNamespace(
            id="demo_device",
            name="Demo Device",
            version="1.2.3",
            installedVersion=None,
            description="Demo",
            descriptionCn="演示",
            state="available",
            source="bundled",
            installPath=None,
        ),
        SimpleNamespace(
            id="not_device",
            name="API Only",
            version="1.0.0",
            installedVersion=None,
            description="Nope",
            descriptionCn=None,
            state="available",
            source="bundled",
            installPath=None,
        ),
    ]
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: entries)
    monkeypatch.setattr(
        plugin_index.hub_catalog,
        "system_plugin_root",
        lambda plugin_type, plugin_id: root if plugin_id == "demo_device" else api_root,
    )
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    templates = plugin_index.list_device_templates(refresh=True)

    assert [template.plugin_id for template in templates] == ["demo_device"]
    template = templates[0]
    assert template.storage_key == "demo_api_v1_2_3"
    assert template.service_id == "demo_api"
    assert template.vendor == "demo"
    assert template.docs_url == "https://docs.example.com/demo-device"
    assert template.installed is False
    assert template.state == "available"
    assert template.source == "bundled"
    assert template.tool_count == 1
    assert template.credential_schema[0]["key"] == "base_url"


def test_device_plugin_index_normalizes_plugin_id_name(monkeypatch, tmp_path):
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    root = tmp_path / "bundled" / "onesig_v2_5_3_D20250710"
    _write_provider(
        root,
        {
            "name": "onesig_v2_5_3_D20250710",
            "service_id": "onesig_v2_5_3_D20250710_api",
            "version": "2.5.3 D20250710",
            "integration_type": "device",
            "vendor": "threatbook",
            "credential_fields": [],
        },
    )
    _write_tool(root, "onesig_v2_5_3_D20250710_login")

    entries = [
        SimpleNamespace(
            id="onesig_v2_5_3_D20250710",
            name="onesig_v2_5_3_D20250710",
            version="2.5.3 D20250710",
            installedVersion=None,
            description="OneSIG legacy",
            descriptionCn="OneSIG 老版本",
            state="available",
            source="bundled",
            installPath=None,
        ),
    ]
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: entries)
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: root)
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    templates = plugin_index.list_device_templates(refresh=True)

    assert len(templates) == 1
    assert templates[0].name == "onesig"
    assert templates[0].storage_key == "onesig_v2_5_3_D20250710_api_v2_5_3_D20250710"
    assert templates[0].version == "2.5.3 D20250710"


def test_device_template_refresh_reloads_plugin_tools(monkeypatch, tmp_path):
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    calls: list[str] = []

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(
        plugin_index.ToolRegistry,
        "refresh_plugin_tools",
        classmethod(lambda cls: calls.append("refresh") or []),
    )

    assert plugin_index.list_device_templates(refresh=True) == []
    assert calls == ["refresh"]


def test_create_custom_device_template_writes_user_plugin(monkeypatch, tmp_path):
    from flocks.tool.device import plugin_index

    home, data, _project = _reset_env(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: None)
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    template = plugin_index.create_custom_device_template(
        CustomDeviceTemplateCreate(
            plugin_id="custom_demo",
            name="Custom Demo",
            vendor="demo",
            service_id="custom_demo_api",
            version="0.1.0",
            description="Custom device",
            credential_fields=[
                {"key": "base_url", "label": "Base URL", "storage": "config"},
            ],
            tools=[
                {
                    "name": "custom_demo_ping",
                    "description": "Ping",
                    "handler": {"type": "http", "path": "/ping"},
                },
            ],
        )
    )

    plugin_dir = home / ".flocks" / "plugins" / "tools" / "device" / "custom_demo"
    provider = yaml.safe_load((plugin_dir / "_provider.yaml").read_text(encoding="utf-8"))
    tool = yaml.safe_load((plugin_dir / "custom_demo_ping.yaml").read_text(encoding="utf-8"))
    records = json.loads((data / "hub" / "installed.json").read_text(encoding="utf-8"))

    assert template.plugin_id == "custom_demo"
    assert template.storage_key == "custom_demo_api_v0_1_0"
    assert provider["integration_type"] == "device"
    assert provider["service_id"] == "custom_demo_api"
    assert tool["provider"] == "custom_demo_api"
    assert records["plugins"]["device:custom_demo"]["type"] == "device"


def test_device_template_route_is_not_shadowed_by_device_id(monkeypatch):
    from flocks.server.routes import device as device_routes

    monkeypatch.setattr(
        device_routes,
        "list_device_templates",
        lambda refresh=False: [
            {
                "plugin_id": "demo",
                "storage_key": "demo_api_v1",
                "service_id": "demo_api",
                "name": "Demo",
                "version": "1",
                "credential_schema": [],
                "tool_count": 0,
                "installed": True,
                "state": "installed",
                "source": "project",
            }
        ],
    )

    app = FastAPI()
    app.include_router(device_routes.router, prefix="/api/devices")
    client = TestClient(app)

    response = client.get("/api/devices/templates")

    assert response.status_code == 200
    assert response.json()[0]["plugin_id"] == "demo"


def test_device_template_route_forwards_refresh(monkeypatch):
    from flocks.server.routes import device as device_routes

    calls: list[bool] = []

    def fake_list_device_templates(*, refresh: bool = False):
        calls.append(refresh)
        return [
            {
                "plugin_id": "demo",
                "storage_key": "demo_api_v1",
                "service_id": "demo_api",
                "name": "Demo",
                "version": "1",
                "credential_schema": [],
                "tool_count": 0,
                "installed": True,
                "state": "installed",
                "source": "project",
            }
        ]

    monkeypatch.setattr(device_routes, "list_device_templates", fake_list_device_templates)

    app = FastAPI()
    app.include_router(device_routes.router, prefix="/api/devices")
    client = TestClient(app)

    response = client.get("/api/devices/templates?refresh=true")

    assert response.status_code == 200
    assert calls == [True]


@pytest.mark.asyncio
async def test_device_list_auto_creates_user_device_plugin_instance(monkeypatch, tmp_path):
    from flocks.server.routes import device as device_routes
    from flocks.storage.storage import Storage
    from flocks.tool.device.store import list_devices
    from flocks.tool.device import plugin_index

    home, _data, _project = _reset_env(monkeypatch, tmp_path)
    await Storage.init()

    root = home / ".flocks" / "plugins" / "tools" / "device" / "custom_demo"
    _write_provider(
        root,
        {
            "name": "Custom Demo",
            "service_id": "custom_demo_api",
            "version": "0.1.0",
            "integration_type": "device",
            "vendor": "custom_vendor",
            "credential_fields": [],
        },
    )
    _write_tool(root, "custom_demo_ping")

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: None)
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    app = FastAPI()
    app.include_router(device_routes.router, prefix="/api/devices")
    client = TestClient(app)

    response = client.post("/api/devices/sync?refresh=true")
    repeated = client.post("/api/devices/sync?refresh=true")
    devices = await list_devices()

    assert response.status_code == 200
    assert repeated.status_code == 200
    assert len(devices) == 1
    assert devices[0].name == "Custom Demo"
    assert devices[0].storage_key == "custom_demo_api_v0_1_0"
    assert devices[0].service_id == "custom_demo_api"
    assert devices[0].enabled is True

    delete_response = client.delete(f"/api/devices/{devices[0].id}")
    sync_after_delete = client.post("/api/devices/sync?refresh=true")
    after_delete = client.get("/api/devices")
    devices_after_delete = await list_devices()

    assert delete_response.status_code == 204
    assert sync_after_delete.status_code == 200
    assert after_delete.status_code == 200
    assert after_delete.json() == []
    assert devices_after_delete == []

    manual_create = client.post(
        "/api/devices",
        json={
            "name": "Custom Demo Manual",
            "storage_key": "custom_demo_api_v0_1_0",
            "service_id": "custom_demo_api",
            "fields": {},
        },
    )
    after_manual_create = client.get("/api/devices")

    assert manual_create.status_code == 201
    assert len(after_manual_create.json()) == 1
    assert after_manual_create.json()[0]["name"] == "Custom Demo Manual"


@pytest.mark.asyncio
async def test_auto_creating_user_device_instances_is_serialized(monkeypatch, tmp_path):
    from flocks.storage.storage import Storage
    from flocks.tool.device import intake, plugin_index
    from flocks.tool.device.store import list_devices

    home, _data, _project = _reset_env(monkeypatch, tmp_path)
    await Storage.init()

    root = home / ".flocks" / "plugins" / "tools" / "device" / "custom_demo"
    _write_provider(
        root,
        {
            "name": "Custom Demo",
            "service_id": "custom_demo_api",
            "version": "0.1.0",
            "integration_type": "device",
            "vendor": "custom_vendor",
            "credential_fields": [],
        },
    )
    _write_tool(root, "custom_demo_ping")

    async def fake_sync_service_tool_state(service_id: str) -> None:
        return None

    original_insert_device = intake.insert_device
    insert_attempts = 0

    async def slow_insert_device(**kwargs):
        nonlocal insert_attempts
        insert_attempts += 1
        await asyncio.sleep(0)
        await original_insert_device(**kwargs)

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: None)
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(intake, "insert_device", slow_insert_device)
    monkeypatch.setattr(intake, "sync_service_tool_state", fake_sync_service_tool_state)

    created_counts = await asyncio.gather(
        intake.ensure_user_device_instances(refresh_templates=True),
        intake.ensure_user_device_instances(refresh_templates=True),
    )
    devices = await list_devices()

    assert sorted(created_counts) == [0, 1]
    assert insert_attempts == 1
    assert len(devices) == 1
    assert devices[0].storage_key == "custom_demo_api_v0_1_0"
