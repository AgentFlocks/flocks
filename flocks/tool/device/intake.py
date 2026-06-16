"""Device lifecycle orchestration.

Routes should stay thin: this module owns persistence, secret handling, tool
state sync, and connectivity probing for device integrations.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Optional

import httpx

from flocks.storage.storage import Storage
from flocks.tool.device.models import (
    DEFAULT_GROUP_ID,
    MULTI_GROUP_ENABLED,
    DeviceIntegration,
    DeviceIntegrationCreate,
    DeviceIntegrationUpdate,
    DeviceTestRequest,
    DeviceTestResult,
)
from flocks.tool.device.secrets import (
    delete_secrets,
    mask_for_display,
    persist_fields,
    resolve_for_runtime,
)
from flocks.tool.device.store import (
    delete_device_row,
    ensure_default_group,
    fetch_device,
    group_exists,
    insert_device,
    list_devices,
    record_test_result,
    row_to_device,
    storage_key_to_service_id,
    update_device_row,
)
from flocks.tool.device.sync import sync_service_tool_state

_AUTO_INSTANCE_IGNORED_KEY = "device.auto_instance_ignored_storage_keys"
_AUTO_INSTANCE_LOCKS: dict[int, asyncio.Lock] = {}


class DeviceIntakeError(Exception):
    status_code = 400


class DeviceNotFoundError(DeviceIntakeError):
    status_code = 404


async def _load_auto_instance_ignored_storage_keys() -> set[str]:
    raw = await Storage.get(_AUTO_INSTANCE_IGNORED_KEY)
    if isinstance(raw, list):
        return {str(item) for item in raw if item}
    if isinstance(raw, dict):
        values = raw.get("storage_keys")
        if isinstance(values, list):
            return {str(item) for item in values if item}
    return set()


async def _save_auto_instance_ignored_storage_keys(storage_keys: set[str]) -> None:
    await Storage.set(_AUTO_INSTANCE_IGNORED_KEY, sorted(storage_keys))


async def _remember_auto_instance_ignore(storage_key: str) -> None:
    if not storage_key:
        return
    ignored = await _load_auto_instance_ignored_storage_keys()
    if storage_key in ignored:
        return
    ignored.add(storage_key)
    await _save_auto_instance_ignored_storage_keys(ignored)


async def _forget_auto_instance_ignore(storage_key: str) -> None:
    if not storage_key:
        return
    ignored = await _load_auto_instance_ignored_storage_keys()
    if storage_key not in ignored:
        return
    ignored.remove(storage_key)
    await _save_auto_instance_ignored_storage_keys(ignored)


def _user_device_template_storage_keys(*, refresh_templates: bool = False) -> set[str]:
    from flocks.tool.device.plugin_index import list_device_templates

    return {
        template.storage_key
        for template in list_device_templates(refresh=refresh_templates)
        if template.source == "global" and template.installed
    }


def _auto_instance_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    loop_id = id(loop)
    lock = _AUTO_INSTANCE_LOCKS.get(loop_id)
    if lock is None:
        lock = asyncio.Lock()
        _AUTO_INSTANCE_LOCKS[loop_id] = lock
    return lock


async def ensure_user_device_instances(*, refresh_templates: bool = False) -> int:
    """Create default device rows for user-level device plugin templates.

    Device templates discovered under ``~/.flocks/plugins/tools/device`` are
    installable product definitions. The device access page's left pane shows
    concrete ``device_integrations`` rows, so a user-created local template
    would otherwise appear only in the right-side picker until the user manually
    added an instance. Auto-provision one editable instance per user-level
    template when no instance for the same storage_key exists yet.
    """
    async with _auto_instance_lock():
        return await _ensure_user_device_instances_unlocked(
            refresh_templates=refresh_templates,
        )


async def _ensure_user_device_instances_unlocked(*, refresh_templates: bool = False) -> int:
    await ensure_default_group()
    existing_storage_keys = {device.storage_key for device in await list_devices()}
    ignored_storage_keys = await _load_auto_instance_ignored_storage_keys()
    user_template_storage_keys = _user_device_template_storage_keys(
        refresh_templates=refresh_templates,
    )
    created = 0

    from flocks.tool.device.plugin_index import list_device_templates

    for template in list_device_templates(refresh=False):
        if template.source != "global" or not template.installed:
            continue
        if template.storage_key in existing_storage_keys:
            continue
        if template.storage_key in ignored_storage_keys:
            continue
        if template.storage_key not in user_template_storage_keys:
            continue

        device_id = str(uuid.uuid4())
        await insert_device(
            device_id=device_id,
            group_id=DEFAULT_GROUP_ID,
            name=template.name,
            storage_key=template.storage_key,
            service_id=template.service_id,
            enabled=True,
            verify_ssl=False,
            db_fields={},
        )
        existing_storage_keys.add(template.storage_key)
        created += 1
        await sync_service_tool_state(template.service_id)

    return created


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
    await _forget_auto_instance_ignore(storage_key)
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

    if storage_key in _user_device_template_storage_keys(refresh_templates=True):
        await _remember_auto_instance_ignore(storage_key)
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
    resolved = _resolve_test_fields(db_fields, body)
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


def _resolve_test_fields(
    db_fields: dict,
    body: Optional[DeviceTestRequest],
) -> dict[str, str]:
    """Resolve persisted fields and apply unsaved form values for one probe."""
    resolved = resolve_for_runtime(db_fields)
    draft_fields = body.fields if body and body.fields else None
    if not draft_fields:
        return resolved

    display_fields, _ = mask_for_display(db_fields)
    merged = dict(resolved)
    for key, value in draft_fields.items():
        draft_value = value if isinstance(value, str) else ""
        persisted_value = resolved.get(key, "")
        display_value = display_fields.get(key, "")
        is_masked_secret = bool(persisted_value) and display_value != persisted_value
        if is_masked_secret and draft_value in {"", display_value}:
            continue
        merged[key] = draft_value
    return merged


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
