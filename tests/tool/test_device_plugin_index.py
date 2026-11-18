from __future__ import annotations

import asyncio
import json
import threading
import time
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


@pytest.fixture(autouse=True)
def _no_recorded_clean_plugin_load(monkeypatch):
    """A clean plugin load recorded by an earlier test would let forced
    refreshes here skip the (stubbed) reload they are about."""
    from flocks.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "_plugin_sources_stamp", None)


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


def test_template_group_follows_selected_definition_not_catalog_native_label(monkeypatch, tmp_path):
    from flocks.config.api_versioning import ApiServiceDescriptor
    from flocks.tool import registry
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    installation = tmp_path / "installation"
    monkeypatch.setattr(registry, "__file__", str(installation / "flocks/tool/registry.py"))
    shipped = installation / ".flocks/plugins/tools/device/device_v1"
    user = tmp_path / "project/.flocks/plugins/tools/device/device_v1"
    provider = {"name": "Demo", "service_id": "demo_api", "version": "1", "integration_type": "device", "group": "Canonical"}
    _write_provider(shipped, provider)
    _write_provider(user, {**provider, "group": "Editable project"})
    entry = SimpleNamespace(
        id="device_v1", name="Demo", version="1", installedVersion=None,
        description=None, descriptionCn=None, state="installed", source="system", installPath=str(shipped),
    )
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda **kw: [entry])
    monkeypatch.setattr(plugin_index, "_device_tool_counts", lambda: {})
    descriptor = ApiServiceDescriptor(service_id="demo_api", version="1", storage_key="demo_api_v1", provider_yaml=shipped / "_provider.yaml")
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [descriptor])
    plugin_index.clear_device_template_cache()
    template = plugin_index.list_device_templates()[0]
    assert template.group == "Canonical" and template.group_readonly is True

    selected = ApiServiceDescriptor(service_id="demo_api", version="1", storage_key="demo_api_v1", provider_yaml=user / "_provider.yaml")
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [selected])
    plugin_index.clear_device_template_cache()
    template = plugin_index.list_device_templates()[0]
    assert template.group == "Editable project" and template.group_readonly is False
    assert yaml.safe_load((shipped / "_provider.yaml").read_text())["group"] == "Canonical"
    plugin_index.clear_device_template_cache()


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


def test_onesig_strategy_api_template_uses_unversioned_service_id(monkeypatch, tmp_path):
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    root = Path.cwd() / ".flocks" / "plugins" / "tools" / "device" / "onesig_v2_5_3"
    _write_provider(
        root,
        {
            "name": "onesig",
            "service_id": "onesig_api",
            "version": "2.5.3",
            "integration_type": "device",
            "vendor": "threatbook",
            "credential_fields": [
                {"key": "base_url", "label": "Base URL", "storage": "config"},
            ],
        },
    )
    _write_tool(root, "onesig_strategy_api_query")

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    templates = plugin_index.list_device_templates(refresh=True)

    assert len(templates) == 1
    assert templates[0].service_id == "onesig_api"
    assert templates[0].storage_key == "onesig_api_v2_5_3"


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


def test_forced_template_refresh_skips_the_reload_when_no_plugin_file_changed(monkeypatch, tmp_path):
    """The page forces a refresh on every return to the window. With no plugin
    file changed since the last clean load, the tool registry is not reloaded
    (seconds of CPU each time), but the template index is still rebuilt."""
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    calls: list[str] = []
    fingerprint = ["same"]

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(
        plugin_index.ToolRegistry,
        "refresh_plugin_tools",
        classmethod(lambda cls: calls.append("refresh") or []),
    )
    monkeypatch.setattr(
        plugin_index.ToolRegistry,
        "_plugin_sources_fingerprint",
        classmethod(lambda cls: (fingerprint[0],)),
    )
    monkeypatch.setattr(plugin_index.ToolRegistry, "_plugin_sources_stamp", ("same",))
    monkeypatch.setattr(plugin_index, "_template_cache", ["stale index"])

    assert plugin_index.list_device_templates(refresh=True) == []
    assert calls == []
    assert plugin_index._template_cache == []

    fingerprint[0] = "a plugin file changed"
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


def test_concurrent_custom_templates_with_one_id_are_created_once(monkeypatch, tmp_path):
    """The route creates templates on worker threads: the existence check and
    the writes are one step, or both requests pass the check and both 'win'."""
    from flocks.tool.device import plugin_index

    _reset_env(monkeypatch, tmp_path)
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: None)
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))

    both_checked = threading.Barrier(2, timeout=5)
    write_yaml = plugin_index._write_yaml

    def slow_first_write(path, data):
        # Both requests reach the writes before either has written anything.
        if path.name == "_provider.yaml":
            try:
                both_checked.wait()
            except threading.BrokenBarrierError:
                pass
        write_yaml(path, data)

    monkeypatch.setattr(plugin_index, "_write_yaml", slow_first_write)
    body = CustomDeviceTemplateCreate(
        plugin_id="custom_race",
        name="Custom Race",
        service_id="custom_race_api",
        tools=[{"name": "custom_race_ping", "description": "Ping", "handler": {"type": "http", "path": "/ping"}}],
    )
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def create() -> None:
        try:
            plugin_index.create_custom_device_template(body)
            outcome = "created"
        except FileExistsError:
            outcome = "exists"
        with outcomes_lock:
            outcomes.append(outcome)

    threads = [threading.Thread(target=create) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["created", "exists"]


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


def _patch_empty_template_sources(monkeypatch, plugin_index) -> None:
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda **kw: [])
    monkeypatch.setattr(plugin_index, "_device_tool_counts", lambda: {})


class _CountingCondition(threading.Condition):
    """Condition that counts its waiters, so a test can wait for them deterministically."""

    waiting = 0

    def wait(self, timeout=None):
        self.waiting += 1
        try:
            return super().wait(timeout)
        finally:
            self.waiting -= 1


def _counting_refresh_cond(monkeypatch, plugin_index) -> _CountingCondition:
    cond = _CountingCondition()
    # raising=False keeps the test runnable (and red) against the pre-fix module.
    monkeypatch.setattr(plugin_index, "_runtime_refresh_cond", cond, raising=False)
    return cond


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_overlapping_forced_template_refreshes_share_reloads(monkeypatch):
    """INC-5959A657: queued page syncs each reloaded every plugin tool."""
    from flocks.tool.device import plugin_index

    _patch_empty_template_sources(monkeypatch, plugin_index)
    cond = _counting_refresh_cond(monkeypatch, plugin_index)
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])
    events: list[str] = []
    events_lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()

    def slow_refresh_plugin_tools(cls):
        with events_lock:
            run = sum(event.startswith("start-") for event in events) + 1
            events.append(f"start-{run}")
        if run == 1:
            first_started.set()
            assert release_first.wait(5)
        with events_lock:
            events.append(f"end-{run}")
        return []

    monkeypatch.setattr(
        plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(slow_refresh_plugin_tools),
    )

    def call(name: str) -> None:
        assert plugin_index.list_device_templates(refresh=True) == []
        with events_lock:
            events.append(f"return-{name}")

    first = threading.Thread(target=call, args=("first",))
    first.start()
    assert first_started.wait(5)
    late = [threading.Thread(target=call, args=(f"late{index}",)) for index in range(5)]
    for thread in late:
        thread.start()
    # Every late caller asks while the first reload still runs.
    assert _wait_until(lambda: cond.waiting == 5), "late callers did not wait for the running reload"
    release_first.set()
    for thread in [first, *late]:
        thread.join(5)
        assert not thread.is_alive()

    assert [event for event in events if event.startswith("start-")] == ["start-1", "start-2"]
    assert events.index("return-first") > events.index("end-1")
    for index in range(5):
        # A caller that asked during reload 1 must see a reload that began after it asked.
        assert events.index(f"return-late{index}") > events.index("end-2")


def test_caller_of_a_finished_reload_does_not_wait_for_the_next_one(monkeypatch):
    """The first of two overlapping forced refreshes returns after its own reload.

    Building the index reads the tool registry, whose lock a running reload
    holds, so a caller that rebuilt only after giving up the reload would sit
    through the reload queued behind it (17 s -> 34 s, past the WebUI timeout).
    """
    from flocks.tool.device import plugin_index

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda **kw: [])
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])
    cond = _counting_refresh_cond(monkeypatch, plugin_index)
    registry_lock = threading.RLock()  # stands in for ToolRegistry._refresh_lock
    first_returned = threading.Event()
    events: list[str] = []
    events_lock = threading.Lock()

    def device_tool_counts():
        with registry_lock:  # ToolRegistry.list_tools() takes the refresh lock
            return {}

    def refresh_plugin_tools(cls):
        with registry_lock:
            with events_lock:
                run = sum(event.startswith("start-") for event in events) + 1
                events.append(f"start-{run}")
            if run == 1:
                assert _wait_until(lambda: cond.waiting == 1), "second caller never waited"
            else:
                first_returned.wait(5)
            with events_lock:
                events.append(f"end-{run}")
        return []

    monkeypatch.setattr(plugin_index, "_device_tool_counts", device_tool_counts)
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(refresh_plugin_tools))

    def first_caller() -> None:
        plugin_index.list_device_templates(refresh=True)
        # Like a device sync reading the index again under its instance lock:
        # by then the queued reload has started and holds the registry lock.
        assert _wait_until(lambda: "start-2" in events), "second reload never started"
        plugin_index.list_device_templates()
        with events_lock:
            events.append("return-first")
        first_returned.set()

    def second_caller() -> None:
        plugin_index.list_device_templates(refresh=True)
        with events_lock:
            events.append("return-second")

    first = threading.Thread(target=first_caller)
    first.start()
    assert _wait_until(lambda: "start-1" in events)
    second = threading.Thread(target=second_caller)
    second.start()
    for thread in (first, second):
        thread.join(15)
        assert not thread.is_alive()

    assert events.index("return-first") < events.index("end-2")
    assert events.index("return-second") > events.index("end-2")


def test_failed_forced_template_refresh_is_not_shared(monkeypatch):
    from flocks.tool.device import plugin_index

    _patch_empty_template_sources(monkeypatch, plugin_index)
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls: []))
    scans = 0

    def flaky_discover(*, refresh: bool = False):
        nonlocal scans
        if refresh:
            scans += 1
            if scans == 1:
                raise RuntimeError("descriptor scan failed")
        return []

    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", flaky_discover)

    with pytest.raises(RuntimeError, match="descriptor scan failed"):
        plugin_index.list_device_templates(refresh=True)
    assert plugin_index.list_device_templates(refresh=True) == []
    assert scans == 2


def test_failed_tool_reload_is_not_shared_with_waiters(monkeypatch):
    """A tool reload that failed (logged, not raised) must not satisfy waiting callers."""
    from flocks.tool.device import plugin_index

    _patch_empty_template_sources(monkeypatch, plugin_index)
    cond = _counting_refresh_cond(monkeypatch, plugin_index)
    failed_runner: list[str] = []
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])
    events: list[str] = []
    events_lock = threading.Lock()
    first_started = threading.Event()
    release_first = threading.Event()

    def flaky_refresh_plugin_tools(cls):
        with events_lock:
            run = sum(event.startswith("start-") for event in events) + 1
            events.append(f"start-{run}")
        if run == 1:
            first_started.set()
            assert release_first.wait(5)
        if run == 2:
            with events_lock:
                events.append("fail-2")
                failed_runner.append(threading.current_thread().name)
            raise RuntimeError("plugin load failed")
        with events_lock:
            events.append(f"end-{run}")
        return []

    monkeypatch.setattr(
        plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(flaky_refresh_plugin_tools),
    )

    def call(name: str) -> None:
        plugin_index.list_device_templates(refresh=True)
        with events_lock:
            events.append(f"return-{name}")

    first = threading.Thread(target=call, args=("first",), name="first")
    first.start()
    assert first_started.wait(5)
    late_names = [f"late{index}" for index in range(3)]
    late = [threading.Thread(target=call, args=(name,), name=name) for name in late_names]
    for thread in late:
        thread.start()
    assert _wait_until(lambda: cond.waiting == 3), "late callers did not wait for the running reload"
    release_first.set()
    for thread in [first, *late]:
        thread.join(5)
        assert not thread.is_alive()

    assert [event for event in events if event.startswith("start-")] == ["start-1", "start-2", "start-3"]
    # The caller that ran the failed reload 2 has its failure logged; the other
    # two must not take that reload as theirs and only return after reload 3.
    waiters = [name for name in late_names if name not in failed_runner]
    assert len(waiters) == 2
    for name in waiters:
        assert events.index(f"return-{name}") > events.index("end-3")


def test_index_build_failure_after_a_reload_drops_the_old_index(monkeypatch):
    from flocks.tool.device import plugin_index

    monkeypatch.setattr(plugin_index, "_device_tool_counts", lambda: {})
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])
    monkeypatch.setattr(plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda **kw: [])
    plugin_index.clear_device_template_cache()
    assert plugin_index.list_device_templates() == []
    assert plugin_index._template_cache == []

    def broken_catalog(**kwargs):
        raise RuntimeError("catalog unreadable")

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", broken_catalog)
    with pytest.raises(RuntimeError, match="catalog unreadable"):
        plugin_index.list_device_templates(refresh=True)
    assert plugin_index._template_cache is None


def test_forced_template_refresh_from_inside_a_reload_does_not_deadlock(monkeypatch):
    from flocks.tool.device import plugin_index

    _patch_empty_template_sources(monkeypatch, plugin_index)
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])
    reloads: list[bool] = []

    def reentrant_refresh_plugin_tools(cls):
        reloads.append(True)
        if len(reloads) == 1:
            plugin_index.list_device_templates(refresh=True)
        return []

    monkeypatch.setattr(
        plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(reentrant_refresh_plugin_tools),
    )

    worker = threading.Thread(target=plugin_index.list_device_templates, kwargs={"refresh": True})
    worker.start()
    worker.join(5)

    assert not worker.is_alive()
    assert len(reloads) == 2


def test_template_rebuild_overtaken_by_a_cache_clear_is_not_stored(monkeypatch):
    from flocks.tool.device import plugin_index

    monkeypatch.setattr(plugin_index, "_device_tool_counts", lambda: {})
    monkeypatch.setattr(plugin_index, "discover_api_service_descriptors", lambda **kw: [])

    def list_catalog_racing_a_plugin_change(**kwargs):
        plugin_index.clear_device_template_cache()  # a Hub install lands mid-rebuild
        return []

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", list_catalog_racing_a_plugin_change)
    plugin_index.clear_device_template_cache()

    assert plugin_index.list_device_templates() == []
    assert plugin_index._template_cache is None


@pytest.mark.asyncio
async def test_overlapping_forced_device_syncs_share_the_plugin_reload(monkeypatch, tmp_path):
    """INC-5959A657: each queued /api/devices/sync?refresh=true reloaded all plugins."""
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
            "credential_fields": [],
        },
    )
    _write_tool(root, "custom_demo_ping")
    reloads = 0
    reloads_lock = threading.Lock()
    cond = _counting_refresh_cond(monkeypatch, plugin_index)

    def slow_refresh_plugin_tools(cls):
        nonlocal reloads
        with reloads_lock:
            reloads += 1
            run = reloads
        if run == 1:
            # Hold the first reload until the other four syncs are waiting on it.
            _wait_until(lambda: cond.waiting >= 4)
        return []

    async def fake_sync_service_tool_state(service_id: str) -> None:
        return None

    monkeypatch.setattr(plugin_index.hub_catalog, "list_catalog", lambda plugin_type=None: [])
    monkeypatch.setattr(plugin_index.hub_catalog, "system_plugin_root", lambda plugin_type, plugin_id: None)
    monkeypatch.setattr(
        plugin_index.ToolRegistry, "refresh_plugin_tools", classmethod(slow_refresh_plugin_tools),
    )
    monkeypatch.setattr(plugin_index.ToolRegistry, "init", classmethod(lambda cls: None))
    monkeypatch.setattr(plugin_index.ToolRegistry, "list_tools", classmethod(lambda cls: []))
    monkeypatch.setattr(intake, "sync_service_tool_state", fake_sync_service_tool_state)

    created_counts = await asyncio.gather(
        *(intake.ensure_user_device_instances(refresh_templates=True) for _ in range(5)),
    )
    devices = await list_devices()

    assert sorted(created_counts) == [0, 0, 0, 0, 1]
    assert [device.storage_key for device in devices] == ["custom_demo_api_v0_1_0"]
    assert reloads == 2
    plugin_index.clear_device_template_cache()
