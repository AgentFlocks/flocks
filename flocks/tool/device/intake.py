"""Device lifecycle orchestration.

Routes should stay thin: this module owns persistence, secret handling, tool
state sync, and connectivity probing for device integrations.
"""
from __future__ import annotations

import json
import time
import uuid
from typing import Optional

import httpx

from flocks.tool.device.models import (
    DEFAULT_GROUP_ID,
    MULTI_GROUP_ENABLED,
    DeviceIntegration,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
    DeviceTestRequest,
    DeviceTestResult,
)
from flocks.tool.device.secrets import delete_secrets, persist_fields, resolve_for_runtime
from flocks.tool.device.store import (
    delete_device_row,
    fetch_device,
    group_exists,
    insert_device,
    record_test_result,
    row_to_device,
    storage_key_to_service_id,
    update_device_row,
)
from flocks.tool.device.sync import sync_service_tool_state


class DeviceIntakeError(Exception):
    status_code = 400


class DeviceNotFoundError(DeviceIntakeError):
    status_code = 404


async def create_device(body: DeviceIntegrationCreate) -> DeviceIntegration:
    name = body.name.strip()
    storage_key = body.storage_key.strip()
    if not name:
        raise ValueError("name is required")
    if not storage_key:
        raise ValueError("storage_key is required")

    group_id = DEFAULT_GROUP_ID if not MULTI_GROUP_ENABLED else (body.group_id or DEFAULT_GROUP_ID)
    if not await group_exists(group_id):
        raise ValueError(f"Group '{group_id}' does not exist")

    service_id = (body.service_id or "").strip() or storage_key_to_service_id(storage_key)
    device_id = str(uuid.uuid4())
    db_fields = persist_fields(device_id, storage_key, body.fields)

    await insert_device(
        device_id=device_id,
        group_id=group_id,
        name=name,
        storage_key=storage_key,
        service_id=service_id,
        enabled=body.enabled,
        verify_ssl=body.verify_ssl,
        db_fields=db_fields,
    )
    await sync_service_tool_state(service_id)

    row = await fetch_device(device_id)
    if row is None:
        raise RuntimeError(f"created device '{device_id}' was not persisted")
    return row_to_device(row)


async def update_device(device_id: str, body: DeviceIntegrationUpdate) -> DeviceIntegration:
    row = await fetch_device(device_id)
    if row is None:
        raise DeviceNotFoundError("Device not found")

    prior_fields: dict = json.loads(row["fields"] or "{}")

    stripped_name = body.name.strip() if body.name else ""
    new_name = stripped_name or row["name"]
    new_enabled = body.enabled if body.enabled is not None else bool(row["enabled"])
    new_ssl = body.verify_ssl if body.verify_ssl is not None else bool(row["verify_ssl"])

    if body.group_id and MULTI_GROUP_ENABLED and body.group_id != row["group_id"]:
        if not await group_exists(body.group_id):
            raise ValueError(f"Group '{body.group_id}' does not exist")
        new_group_id = body.group_id
    else:
        new_group_id = row["group_id"] or DEFAULT_GROUP_ID

    new_fields = (
        persist_fields(device_id, row["storage_key"], body.fields, prior_db_fields=prior_fields)
        if body.fields is not None
        else prior_fields
    )

    await update_device_row(
        device_id,
        name=new_name,
        group_id=new_group_id,
        enabled=new_enabled,
        verify_ssl=new_ssl,
        db_fields=new_fields,
    )
    await sync_service_tool_state(storage_key_to_service_id(row["storage_key"]))

    updated = await fetch_device(device_id)
    if updated is None:
        raise DeviceNotFoundError("Device not found")
    return row_to_device(updated)


async def delete_device(device_id: str) -> None:
    row = await fetch_device(device_id)
    if row is None:
        raise DeviceNotFoundError("Device not found")

    storage_key: str = row["storage_key"]
    service_id: str = storage_key_to_service_id(storage_key)
    db_fields: dict = json.loads(row["fields"] or "{}")

    delete_secrets(device_id, db_fields)
    await delete_device_row(device_id)
    await sync_service_tool_state(service_id, deleted_storage_keys=[storage_key])


async def test_device(
    device_id: str,
    body: Optional[DeviceTestRequest] = None,
) -> DeviceTestResult:
    row = await fetch_device(device_id)
    if row is None:
        raise DeviceNotFoundError("Device not found")

    db_fields: dict = json.loads(row["fields"] or "{}")
    resolved = resolve_for_runtime(db_fields)
    persisted_base_url = (resolved.get("base_url") or "").strip()

    override_base_url = (body.base_url.strip() if body and body.base_url else "")
    base_url = override_base_url or persisted_base_url

    if not base_url:
        host = (resolved.get("host") or "").strip()
        port = (resolved.get("port") or "").strip()
        if host:
            has_scheme = "://" in host
            if has_scheme:
                base_url = f"{host}:{port}" if port else host
            else:
                base_url = f"https://{host}:{port}" if port else f"https://{host}"

    if not base_url:
        return DeviceTestResult(
            success=False,
            message="未配置设备地址（base_url 或 host），请先填写",
        )

    verify_ssl = bool(body.verify_ssl) if body is not None and body.verify_ssl is not None else bool(row["verify_ssl"])

    result = await _probe(base_url, verify_ssl=verify_ssl)
    await record_test_result(
        device_id,
        success=result.success,
        message=result.message,
        latency_ms=result.latency_ms,
    )
    return result


async def _probe(base_url: str, *, verify_ssl: bool) -> DeviceTestResult:
    start = time.monotonic()

    def elapsed() -> int:
        return int((time.monotonic() - start) * 1000)

    try:
        async with httpx.AsyncClient(verify=verify_ssl, timeout=10.0) as client:
            resp = await client.get(base_url)
        ms = elapsed()
        return DeviceTestResult(
            success=resp.status_code < 500,
            message=f"HTTP {resp.status_code}，延迟 {ms}ms",
            latency_ms=ms,
        )
    except httpx.ConnectError:
        return DeviceTestResult(
            success=False,
            message=f"无法连接到 {base_url}，请检查地址是否正确",
            latency_ms=elapsed(),
        )
    except httpx.TimeoutException:
        return DeviceTestResult(
            success=False,
            message="连接超时（10s），请检查网络或设备地址",
            latency_ms=elapsed(),
        )
    except Exception as exc:
        return DeviceTestResult(success=False, message=f"测试失败：{exc}")
