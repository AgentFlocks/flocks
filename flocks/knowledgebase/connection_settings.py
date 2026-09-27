"""Read and save the selected knowledge engine connection without touching the live client."""

from __future__ import annotations

import asyncio
import json
import secrets as secrets_lib
from pathlib import Path
from typing import Any

from flocks.config.config import Config
from flocks.config.config_writer import ConfigWriter
from flocks.security.secrets import get_secret_manager
from flocks.server.config_mutation import GLOBAL_CONFIG_MUTATION_LOCK

from .client import Connection
from .errors import KBError, KnowledgebaseError
from .ragflow import RagflowAdapter
from .service import KnowledgeAPI

_CREDENTIAL_ID = "knowledgebase_ragflow_api_key"
_PROBE_TIMEOUT_SECONDS = 12
_PROBE_DEADLINE_SECONDS = 26


def _error(status: int, code: str, message: str) -> KnowledgebaseError:
    return KnowledgebaseError(status, code, message)


def _has_knowledgebase_config(value: Any) -> bool:
    if not isinstance(value, dict) or value.get("api_services") is None:
        return False
    services = value["api_services"]
    return not isinstance(services, dict) or "knowledgebase" in services


def _check_overrides(writable: Path) -> None:
    global_config = Config.get_global()
    inline = global_config.config_content
    if inline:
        try:
            parsed = json.loads(inline)
        except (TypeError, ValueError):
            raise _error(409, "knowledgebase_config_overridden", "Knowledgebase configuration is externally managed.") from None
        if _has_knowledgebase_config(parsed):
            raise _error(409, "knowledgebase_config_overridden", "Knowledgebase configuration is externally managed.")

    sources = [global_config.config_dir / "config.json"]
    if global_config.config_path:
        sources.append(Path(global_config.config_path))
    for source in sources:
        if not source.exists() or source.resolve() == writable.resolve():
            continue
        try:
            value = ConfigWriter._read_path_raw(source, strict=True)
        except (OSError, ValueError):
            raise _error(409, "knowledgebase_config_overridden", "Knowledgebase configuration is externally managed.") from None
        if _has_knowledgebase_config(value):
            raise _error(409, "knowledgebase_config_overridden", "Knowledgebase configuration is externally managed.")


def _load_settings() -> tuple[Path, dict[str, Any], dict[str, Any]]:
    writable = ConfigWriter._get_config_path()
    _check_overrides(writable)
    try:
        data = ConfigWriter._read_path_raw(writable, strict=True)
    except (OSError, ValueError):
        raise _error(409, "knowledgebase_config_invalid", "Knowledgebase configuration cannot be read.") from None
    effective = data
    # Config.get() merges flocks.json before flocks.jsonc. Apply the same raw
    # merge before validating the effective service entry, without resolving secrets.
    if writable.name == "flocks.jsonc":
        lower = writable.parent / "flocks.json"
        if lower.exists():
            try:
                lower_data = ConfigWriter._read_path_raw(lower, strict=True)
            except (OSError, ValueError):
                raise _error(409, "knowledgebase_config_invalid", "Knowledgebase configuration cannot be read.") from None
            effective = Config.merge_deep(lower_data, data)
    services = effective.get("api_services", {})
    if not isinstance(services, dict):
        raise _error(409, "knowledgebase_config_invalid", "Knowledgebase configuration cannot be read.")
    settings = services.get("knowledgebase")
    if settings is not None and not isinstance(settings, dict):
        raise _error(409, "knowledgebase_config_invalid", "Knowledgebase configuration cannot be read.")
    return writable, data, settings or {}


def _stored_secrets() -> dict[str, str]:
    path = Config.get_secret_file()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or any(not isinstance(key, str) or not isinstance(value, str) for key, value in data.items()):
            raise ValueError("invalid credential store")
        return data
    except (OSError, ValueError):
        raise _error(409, "knowledgebase_config_invalid", "Knowledgebase credentials cannot be read.") from None


def _selected_key(settings: dict[str, Any], stored: dict[str, str]) -> str | None:
    credential_id = settings.get("credential_id")
    if not isinstance(credential_id, str) or not credential_id or "{" in credential_id or "}" in credential_id:
        return None
    return stored.get(credential_id)


def get_connection() -> dict[str, Any]:
    _, _, settings = _load_settings()
    stored = _stored_secrets() if settings.get("credential_id") else {}
    provider = settings.get("provider")
    base_url = settings.get("base_url")
    if isinstance(base_url, str):
        try:
            Connection(base_url, "x" * 32)
        except (TypeError, ValueError):
            base_url = ""
    return {
        "provider": provider if provider == "ragflow" else None,
        "base_url": base_url if isinstance(base_url, str) else "",
        "has_api_key": bool(_selected_key(settings, stored)),
    }


async def save_connection(provider: str, base_url: str, api_key: str) -> dict[str, Any]:
    writable, data, previous = _load_settings()
    if provider != "ragflow":
        raise _error(400, "invalid_request", "Unsupported knowledge engine.")
    url = base_url.strip()
    supplied = api_key if api_key.strip() else ""
    if not url or len(url) > 2048:
        raise _error(400, "invalid_request", "Invalid knowledge engine URL.")
    try:
        # Never send an existing key to another path, even on a shared origin.
        Connection(url, "x" * 32)
        existing_url = previous.get("base_url")
        if not supplied and (not isinstance(existing_url, str) or existing_url.rstrip("/") != url.rstrip("/")):
            raise _error(400, "api_key_required", "Provide an API key when changing the engine URL.")
    except KnowledgebaseError:
        raise
    except (TypeError, ValueError):
        raise _error(400, "invalid_request", "Invalid knowledge engine URL.") from None

    stored = _stored_secrets()
    selected = supplied or _selected_key(previous, stored)
    if not selected:
        raise _error(400, "api_key_required", "A valid knowledge engine API key is required.")
    try:
        Connection(url, selected)
    except (TypeError, ValueError):
        raise _error(400, "invalid_request", "Invalid knowledge engine API key.") from None
    timeout = previous.get("timeout_seconds", 30.0)
    upload_limit = previous.get("max_upload_bytes", 32 * 1024 * 1024)
    try:
        if type(timeout) not in {int, float}:
            raise ValueError("invalid timeout")
        Connection(url, selected, timeout_seconds=timeout, max_upload_bytes=upload_limit)
    except (TypeError, ValueError):
        raise _error(409, "knowledgebase_config_invalid", "Existing connection limits are invalid.") from None

    adapter: RagflowAdapter | None = None
    try:
        adapter = RagflowAdapter(url, selected, timeout=_PROBE_TIMEOUT_SECONDS, max_content_bytes=upload_limit)
        api = KnowledgeAPI(adapter)
        async with asyncio.timeout(_PROBE_DEADLINE_SECONDS):
            await api.list_datasets(page=1, page_size=1, keywords=None)
            await api.list_files(page=1, page_size=1, keywords=None)
    except (KBError, TimeoutError, OSError):
        raise _error(502, "connection_test_failed", "The knowledge engine connection test failed.") from None
    finally:
        if adapter is not None:
            try:
                await adapter.close()
            except KBError:
                raise _error(502, "connection_test_failed", "The knowledge engine connection test failed.") from None

    # Avoid holding the global configuration lock while waiting on RAGFlow.
    # Refuse stale saves if another configuration or credential change won
    # during the probe; only the short read-modify-write is serialized.
    async with GLOBAL_CONFIG_MUTATION_LOCK.hold():
        current_path, current_data, current_settings = _load_settings()
        if (
            current_path != writable or current_data != data
            or current_settings != previous or _stored_secrets() != stored
        ):
            raise _error(409, "knowledgebase_config_changed", "Knowledgebase connection changed; retry saving.")

        services = current_data.setdefault("api_services", {})
        changed_key = bool(supplied and supplied != _selected_key(previous, stored))
        if changed_key:
            # Write a new credential before atomically repointing the config.
            # A crash between writes must not send the new key to the old URL.
            if not previous.get("credential_id") and _CREDENTIAL_ID not in stored:
                credential_id = _CREDENTIAL_ID
            else:
                credential_id = f"{_CREDENTIAL_ID}_{secrets_lib.token_hex(12)}"
                while credential_id in stored:
                    credential_id = f"{_CREDENTIAL_ID}_{secrets_lib.token_hex(12)}"
        else:
            credential_id = previous["credential_id"]
        services["knowledgebase"] = {
            **previous,
            "enabled": True,
            "provider": "ragflow",
            "base_url": url,
            "credential_id": credential_id,
        }
        secrets = None
        try:
            if changed_key:
                secrets = get_secret_manager()
                secrets.set(credential_id, supplied)
            ConfigWriter._write_raw(current_data, path=writable)
        except Exception:
            if changed_key and secrets is not None:
                try:
                    secrets.delete(credential_id)
                except Exception:
                    pass
            raise _error(500, "knowledgebase_save_failed", "Knowledgebase connection could not be saved.") from None

    return {"provider": "ragflow", "base_url": url, "has_api_key": True, "applied": False, "restart_required": True}
