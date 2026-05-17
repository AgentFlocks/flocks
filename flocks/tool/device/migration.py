"""One-time, idempotent migration: flocks.json api_services → device_integrations.

For each ``api_services`` entry that:
  * declares ``integration_type: device`` in its ``_provider.yaml``;
  * has at least one non-empty credential value;
  * has NOT been migrated before (tracked per storage_key in the
    ``device.migration.done`` Storage key);

…insert a corresponding row in ``device_integrations``, re-scoping its
secrets to the new ``device_<uuid>_<field>`` namespace via :func:`persist_fields`.

The per-storage_key marker ensures the migration is a strict no-op on
subsequent restarts, even if the user later deletes the migrated device.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict

from flocks.storage.storage import Storage
from flocks.utils.log import Log

from .models import DEFAULT_GROUP_ID
from .secrets import persist_fields
from .store import insert_device, storage_key_to_service_id

log = Log.create(service="tool.device.migration")


async def migrate_from_config() -> None:
    """Migrate legacy device API configurations from ``flocks.json`` to SQL."""
    try:
        from flocks.config.config_writer import ConfigWriter
        from flocks.security import get_secret_manager
        from flocks.tool.schema.api_service_schema import (
            _build_api_service_credential_schema,
            _get_api_service_secret_candidates,
            _load_api_service_metadata_data,
        )
    except Exception as exc:
        log.warn("tool.device.migrate.import_error", {"error": str(exc)})
        return

    raw_services: Dict[str, Any] = ConfigWriter.list_api_services_raw()
    if not raw_services:
        return

    marker: Dict[str, Any] = await Storage.get("device.migration.done") or {}
    if not isinstance(marker, dict):
        marker = {}

    secrets = get_secret_manager()
    new_marker = dict(marker)

    for storage_key, raw_cfg in raw_services.items():
        if not isinstance(raw_cfg, dict):
            continue

        try:
            meta = _load_api_service_metadata_data(storage_key) or {}
        except Exception:
            continue

        if meta.get("integration_type") != "device":
            continue

        if marker.get(storage_key):
            log.info("tool.device.migrate.skip", {"storage_key": storage_key, "reason": "already done"})
            continue

        plain_values = _extract_plain_values(
            storage_key, raw_cfg, meta, secrets,
            build_schema=_build_api_service_credential_schema,
            secret_candidates=_get_api_service_secret_candidates,
        )

        now = int(time.time() * 1000)
        if not any(plain_values.values()):
            new_marker[storage_key] = now
            log.info("tool.device.migrate.skip", {"storage_key": storage_key, "reason": "no credentials"})
            continue

        try:
            device_id = await _insert_migrated_device(storage_key, raw_cfg, meta, plain_values)
            new_marker[storage_key] = now
            log.info("tool.device.migrate.created", {"storage_key": storage_key, "id": device_id})
        except Exception as exc:
            log.warn("tool.device.migrate.insert_error", {"storage_key": storage_key, "error": str(exc)})

    if new_marker != marker:
        try:
            await Storage.set("device.migration.done", new_marker)
        except Exception as exc:
            log.warn("tool.device.migrate.marker_error", {"error": str(exc)})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_plain_values(
    storage_key: str,
    raw_cfg: Dict[str, Any],
    meta: Dict[str, Any],
    secrets,
    *,
    build_schema,
    secret_candidates,
) -> Dict[str, str]:
    """Reconstruct plaintext credential values from flocks.json + secret store."""
    schema = build_schema(storage_key, meta)
    plain: Dict[str, str] = {}

    for field in schema:
        if field.storage == "config":
            candidates = (field.config_key, field.key, "baseUrl" if field.key == "base_url" else None)
            for cand in candidates:
                if cand and isinstance(raw_cfg.get(cand), str) and raw_cfg[cand]:
                    plain[field.key] = raw_cfg[cand]
                    break
        else:
            for sid in secret_candidates(storage_key, raw_cfg, field_name=field.key):
                val = secrets.get(sid)
                if val:
                    plain[field.key] = val
                    break

    # Legacy fallbacks for plugins without a formal credential schema
    for legacy_key, field_name in (("base_url", "base_url"), ("baseUrl", "base_url"), ("username", "username")):
        if field_name not in plain and isinstance(raw_cfg.get(legacy_key), str) and raw_cfg[legacy_key]:
            plain[field_name] = raw_cfg[legacy_key]

    return plain


async def _insert_migrated_device(
    storage_key: str,
    raw_cfg: Dict[str, Any],
    meta: Dict[str, Any],
    plain_values: Dict[str, str],
) -> str:
    """Persist secrets, insert the device row, return the new device id."""
    service_name = meta.get("name") or storage_key.replace("_", " ").title()
    device_id = str(uuid.uuid4())
    db_fields = persist_fields(device_id, storage_key, plain_values)

    await insert_device(
        device_id=device_id,
        group_id=DEFAULT_GROUP_ID,
        name=f"{service_name}（迁移）",
        storage_key=storage_key,
        service_id=storage_key_to_service_id(storage_key),
        enabled=bool(raw_cfg.get("enabled", False)),
        verify_ssl=bool(raw_cfg.get("verify_ssl") or raw_cfg.get("verifySsl") or False),
        db_fields=db_fields,
        status="unknown",
        message="从 flocks.json 自动迁移",
    )
    return device_id
