"""Tests for the built-in device_manage tool."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from flocks.tool.device.intake import DeviceNotFoundError
from flocks.tool.device.manage_tool import device_manage
from flocks.tool.device.models import DeviceIntegration, DeviceTemplate, DeviceTestResult
from flocks.tool.registry import ToolContext, ToolRegistry


def make_ctx() -> ToolContext:
    return ToolContext(
        session_id="session-device-test",
        message_id="message-device-test",
        agent="rex",
    )


def make_device(**overrides) -> DeviceIntegration:
    data = {
        "id": "dev-1",
        "group_id": "default-room",
        "name": "自定义设备",
        "storage_key": "custom_device_v1",
        "service_id": "custom_device",
        "enabled": True,
        "verify_ssl": False,
        "fields": {"base_url": "https://device.local"},
        "fields_set": {"base_url": True},
        "status": "unknown",
        "message": None,
        "latency_ms": None,
        "checked_at": None,
        "created_at": 1,
        "updated_at": 2,
    }
    data.update(overrides)
    return DeviceIntegration(**data)


def make_template(**overrides) -> DeviceTemplate:
    data = {
        "plugin_id": "360-waf",
        "storage_key": "360_waf_v5_5",
        "service_id": "360_waf",
        "name": "360 WAF",
        "credential_schema": [],
        "installed": True,
        "state": "installed",
        "source": "project",
    }
    data.update(overrides)
    return DeviceTemplate(**data)


def test_device_manage_is_registered():
    tools = {tool.name for tool in ToolRegistry.list_tools()}
    assert "device_manage" in tools


def test_device_manage_schema_includes_template_discovery_action():
    tool = ToolRegistry.get("device_manage")
    assert tool is not None

    action_param = next(param for param in tool.info.parameters if param.name == "action")
    assert action_param.enum == [
        "list",
        "list_templates",
        "create",
        "update",
        "connectivity_test",
    ]
    assert {param.name for param in tool.info.parameters} >= {
        "device_id",
        "device_name",
        "storage_key",
        "group_id",
        "fields",
        "verify_ssl",
    }


@pytest.mark.asyncio
async def test_device_manage_list_returns_device_inventory():
    with patch(
        "flocks.tool.device.prompt.build_device_manage_list_section",
        AsyncMock(return_value="### 已接入设备\n- device_id: `dev-1`"),
    ):
        result = await device_manage(make_ctx(), action="list")

    assert result.success is True
    assert "dev-1" in result.output


@pytest.mark.asyncio
async def test_device_manage_list_templates_returns_existing_template_metadata():
    templates = [
        make_template(
            plugin_id="tdp",
            storage_key="tdp_v3_3_10",
            service_id="tdp",
            name="TDP",
            version="3.3.10",
            vendor="threatbook",
            credential_schema=[
                {
                    "key": "base_url",
                    "label": "Base URL",
                    "required": True,
                    "secret": False,
                },
                {
                    "key": "api_key",
                    "label": "API Key",
                    "required": True,
                    "secret": True,
                },
            ],
            tool_count=2,
        )
    ]
    with patch(
        "flocks.tool.device.plugin_index.list_device_templates",
        return_value=templates,
    ) as mocked_list:
        result = await device_manage(make_ctx(), action="list_templates")

    mocked_list.assert_called_once_with(refresh=False)
    assert result.success is True
    assert result.output == [templates[0].model_dump(mode="json")]
    assert result.metadata == {
        "template_count": 1,
        "installed_count": 1,
    }


@pytest.mark.asyncio
async def test_device_manage_create_uses_installed_template_identity():
    template = make_template(
        credential_schema=[
            {
                "key": "base_url",
                "label": "Base URL",
                "storage": "config",
                "required": True,
            },
            {
                "key": "username",
                "label": "Username",
                "storage": "config",
                "required": False,
            },
            {
                "key": "auth_state",
                "label": "Auth State",
                "storage": "config",
                "required": False,
            },
            {
                "key": "password",
                "label": "Password",
                "storage": "secret",
                "required": True,
            },
        ],
    )
    created = make_device(
        id="dev-360",
        name="360 WAF",
        storage_key=template.storage_key,
        service_id=template.service_id,
        fields={"base_url": "https://192.168.1.100"},
        fields_set={"base_url": True},
    )
    with (
        patch(
            "flocks.tool.device.plugin_index.list_device_templates",
            return_value=[template],
        ),
        patch(
            "flocks.tool.device.manage_tool.create_device",
            AsyncMock(return_value=created),
        ) as mocked_create,
    ):
        result = await device_manage(
            make_ctx(),
            action="create",
            storage_key="360_waf_v5_5",
            device_name="360 WAF",
            fields={
                "base_url": "https://192.168.1.100",
                "auth_state": "ready",
            },
            verify_ssl=False,
        )

    mocked_create.assert_awaited_once()
    body = mocked_create.await_args.args[0]
    assert body.name == "360 WAF"
    assert body.storage_key == "360_waf_v5_5"
    assert body.service_id == "360_waf"
    assert body.fields == {
        "base_url": "https://192.168.1.100",
        "auth_state": "ready",
    }
    assert result.success is True
    assert result.output["device_id"] == "dev-360"
    assert result.metadata["sensitive_fields"] == ["password"]


@pytest.mark.asyncio
async def test_device_manage_create_rejects_uninstalled_template():
    template = make_template(
        installed=False,
        state="available",
        source="bundled",
    )
    with patch(
        "flocks.tool.device.plugin_index.list_device_templates",
        return_value=[template],
    ):
        result = await device_manage(
            make_ctx(),
            action="create",
            storage_key=template.storage_key,
            device_name="360 WAF",
        )

    assert result.success is False
    assert "尚未安装" in (result.error or "")
    assert "360-waf" in (result.error or "")


@pytest.mark.asyncio
async def test_device_manage_create_rejects_secret_and_unknown_fields():
    template = make_template(
        credential_schema=[
            {"key": "base_url", "storage": "config", "required": True},
            {"key": "password", "storage": "secret", "required": True},
        ],
    )
    with patch(
        "flocks.tool.device.plugin_index.list_device_templates",
        return_value=[template],
    ):
        secret_result = await device_manage(
            make_ctx(),
            action="create",
            storage_key=template.storage_key,
            device_name="360 WAF",
            fields={
                "base_url": "https://192.168.1.100",
                "password": "do-not-store",
            },
        )
        unknown_result = await device_manage(
            make_ctx(),
            action="create",
            storage_key=template.storage_key,
            device_name="360 WAF",
            fields={
                "base_url": "https://192.168.1.100",
                "account": "admin",
            },
        )

    assert secret_result.success is False
    assert "敏感字段" in (secret_result.error or "")
    assert "password" in (secret_result.error or "")
    assert unknown_result.success is False
    assert "模板未声明字段" in (unknown_result.error or "")
    assert "account" in (unknown_result.error or "")


@pytest.mark.asyncio
async def test_device_manage_create_leaves_missing_fields_for_page_completion():
    template = make_template(
        credential_schema=[
            {
                "key": "base_url",
                "storage": "config",
                "required": True,
                "default_value": "https://default.local",
            },
            {"key": "password", "storage": "secret", "required": True},
        ],
    )
    created = make_device(
        name="360 WAF",
        storage_key=template.storage_key,
        service_id=template.service_id,
        fields={},
        fields_set={},
    )
    with (
        patch(
            "flocks.tool.device.plugin_index.list_device_templates",
            return_value=[template],
        ),
        patch(
            "flocks.tool.device.manage_tool.create_device",
            AsyncMock(return_value=created),
        ) as mocked_create,
    ):
        result = await device_manage(
            make_ctx(),
            action="create",
            storage_key=template.storage_key,
            device_name="360 WAF",
        )

    assert result.success is True
    assert mocked_create.await_args.args[0].fields == {}
    assert result.output["sensitive_fields_to_complete"] == ["password"]


@pytest.mark.asyncio
async def test_device_manage_update_updates_existing_device_non_secret_config():
    updated_device = make_device(verify_ssl=True)
    with patch(
        "flocks.tool.device.manage_tool.update_device",
        AsyncMock(return_value=updated_device),
    ) as mocked_update:
        result = await device_manage(
            make_ctx(),
            action="update",
            device_id="dev-1",
            fields={
                "base_url": "https://device.local",
                "port": 443,
                "auth_state": "ready",
            },
            verify_ssl=True,
        )

    mocked_update.assert_awaited_once()
    called_device_id, update_body = mocked_update.await_args.args
    assert called_device_id == "dev-1"
    assert update_body.fields == {
        "base_url": "https://device.local",
        "port": "443",
        "auth_state": "ready",
    }
    assert update_body.verify_ssl is True
    assert result.success is True
    assert result.output["device_id"] == "dev-1"
    assert result.output["updated_fields"] == ["auth_state", "base_url", "port"]
    assert result.metadata["verify_ssl"] is True


@pytest.mark.asyncio
async def test_device_manage_update_rejects_sensitive_fields():
    with patch(
        "flocks.tool.device.manage_tool.update_device",
        AsyncMock(),
    ) as mocked_update:
        result = await device_manage(
            make_ctx(),
            action="update",
            device_id="dev-1",
            fields={"api_key": "secret-value"},
        )

    mocked_update.assert_not_awaited()
    assert result.success is False
    assert "敏感字段" in (result.error or "")
    assert "api_key" in (result.error or "")


@pytest.mark.asyncio
async def test_device_manage_update_requires_fields_or_verify_ssl():
    result = await device_manage(
        make_ctx(),
        action="update",
        device_id="dev-1",
    )

    assert result.success is False
    assert "至少需要提供 fields 或 verify_ssl" in (result.error or "")


@pytest.mark.asyncio
async def test_device_manage_update_reports_missing_device_as_tool_error():
    with patch(
        "flocks.tool.device.manage_tool.update_device",
        AsyncMock(side_effect=DeviceNotFoundError("missing")),
    ):
        result = await device_manage(
            make_ctx(),
            action="update",
            device_id="missing-id",
            fields={"base_url": "https://device.local"},
        )

    assert result.success is False
    assert "未找到" in (result.error or "")


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_writes_status_via_existing_test_path():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=True,
                message="HTTP 200，延迟 12ms",
                latency_ms=12,
            )
        ),
    ) as mocked_test:
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

    mocked_test.assert_awaited_once_with("dev-1")
    assert result.success is True
    assert result.output == {
        "device_id": "dev-1",
        "connected": True,
        "status": "ok",
        "message": "HTTP 200，延迟 12ms",
        "latency_ms": 12,
    }
    assert result.metadata["card_status_updated"] is True


@pytest.mark.asyncio
async def test_device_manage_keeps_device_id_when_executed_through_registry():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=True,
                message="HTTP 200，延迟 12ms",
                latency_ms=12,
            )
        ),
    ) as mocked_test:
        result = await ToolRegistry.execute(
            "device_manage",
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

    mocked_test.assert_awaited_once_with("dev-1")
    assert result.success is True
    assert result.metadata["device_id"] == "dev-1"


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_returns_successful_tool_result_for_failed_probe():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(
            return_value=DeviceTestResult(
                success=False,
                message="无法连接到 https://device.local",
                latency_ms=10000,
            )
        ),
    ):
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="dev-1",
        )

    assert result.success is True
    assert result.output["connected"] is False
    assert result.output["status"] == "error"


@pytest.mark.asyncio
async def test_device_manage_connectivity_test_reports_missing_device_as_tool_error():
    with patch(
        "flocks.tool.device.manage_tool.test_device",
        AsyncMock(side_effect=DeviceNotFoundError("missing")),
    ):
        result = await device_manage(
            make_ctx(),
            action="connectivity_test",
            device_id="missing-id",
        )

    assert result.success is False
    assert "未找到" in (result.error or "")
