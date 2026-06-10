"""Runtime index for device plugin templates.

The device access page consumes this module instead of hand-maintaining a
frontend catalog. The only source of device identity is plugin metadata in
``_provider.yaml`` with ``integration_type: device``.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml

from flocks.config.api_versioning import (
    ApiServiceDescriptor,
    derive_storage_key,
    discover_api_service_descriptors,
)
from flocks.hub import catalog as hub_catalog
from flocks.hub import local as hub_local
from flocks.tool.device.models import CustomDeviceTemplateCreate, DeviceTemplate
from flocks.tool.registry import ToolRegistry
from flocks.tool.schema.api_service_schema import _build_api_service_credential_schema
from flocks.tool.tool_loader import TOOL_TYPE_DEVICE, extract_provider_version
from flocks.utils.log import Log

log = Log.create(service="device.plugin-index")

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
_SAFE_SERVICE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def list_device_templates(*, refresh: bool = False) -> list[DeviceTemplate]:
    """Return device templates from Hub catalog plus local descriptor discovery."""
    if refresh:
        _refresh_device_plugin_runtime()

    by_key: dict[str, DeviceTemplate] = {}
    for entry in hub_catalog.list_catalog(plugin_type="device"):
        root = _catalog_entry_root(entry)
        if root is None:
            by_key[f"broken:{entry.id}"] = DeviceTemplate(
                plugin_id=entry.id,
                storage_key=entry.id,
                service_id=entry.id,
                name=entry.name or entry.id,
                version=entry.version,
                description=entry.description,
                description_cn=entry.descriptionCn,
                credential_schema=[],
                tool_count=0,
                installed=False,
                state="broken",
                source=_source_from_entry(entry),
            )
            continue

        template = _template_from_plugin_root(
            plugin_id=entry.id,
            root=root,
            state=_normalize_state(entry.state),
            installed=_entry_installed(entry),
            source=_source_from_entry(entry),
            fallback_name=entry.name,
            fallback_description=entry.description,
            fallback_description_cn=entry.descriptionCn,
            fallback_version=entry.installedVersion or entry.version,
        )
        if template is not None:
            by_key[template.storage_key] = template

    # Defensive pass for project/user device plugins that may not have Hub
    # records yet. This also explicitly exercises the descriptor API so version
    # and storage_key stay aligned with the runtime credential namespace.
    for descriptor in discover_api_service_descriptors(refresh=False):
        root = descriptor.provider_yaml.parent
        provider = _read_provider_yaml(root)
        if _integration_type(provider) != "device":
            continue
        if descriptor.storage_key in by_key:
            continue
        plugin_id = root.name
        source = _source_from_path(root)
        by_key[descriptor.storage_key] = _template_from_descriptor(
            plugin_id=plugin_id,
            descriptor=descriptor,
            provider=provider,
            state="localOnly",
            installed=True,
            source=source,
        )

    return sorted(by_key.values(), key=_sort_key)


def create_custom_device_template(body: CustomDeviceTemplateCreate) -> DeviceTemplate:
    """Create a user-level device plugin package and return its new template."""
    plugin_id = body.plugin_id.strip()
    service_id = body.service_id.strip()
    if not _SAFE_ID.match(plugin_id):
        raise ValueError("plugin_id must contain only letters, numbers, '_' or '-'")
    if not _SAFE_SERVICE_ID.match(service_id):
        raise ValueError("service_id must be a valid identifier")
    if not body.name.strip():
        raise ValueError("name is required")
    if not body.tools:
        raise ValueError("at least one tool is required")

    target = hub_local.install_dir("device", plugin_id, "global")
    if target.exists() and hub_local.has_install_payload("device", target):
        raise FileExistsError(f"device plugin '{plugin_id}' already exists")

    provider_yaml = _custom_provider_yaml(body)
    target.mkdir(parents=True, exist_ok=True)
    _write_yaml(target / "_provider.yaml", provider_yaml)

    seen_tools: set[str] = set()
    for tool in body.tools:
        tool_name = tool.name.strip()
        if not _SAFE_SERVICE_ID.match(tool_name):
            raise ValueError(f"tool name '{tool.name}' must be a valid identifier")
        if tool_name in seen_tools:
            raise ValueError(f"duplicate tool name '{tool_name}'")
        seen_tools.add(tool_name)
        _write_yaml(target / f"{tool_name}.yaml", _custom_tool_yaml(tool.model_dump(exclude_none=True), service_id))

    record = hub_local.make_record(
        plugin_type="device",
        plugin_id=plugin_id,
        version=body.version or "0.0.0",
        source="custom",
        install_path=target,
        scope="global",
    )
    hub_local.save_installed_record(record)

    _refresh_device_plugin_runtime()

    for template in list_device_templates(refresh=False):
        if template.plugin_id == plugin_id:
            return template
    raise RuntimeError(f"created device plugin '{plugin_id}' was not indexed")


def _refresh_device_plugin_runtime() -> None:
    """Refresh both device descriptors and runtime tool registrations."""
    discover_api_service_descriptors(refresh=True)
    try:
        ToolRegistry.refresh_plugin_tools()
    except Exception as exc:  # pragma: no cover - defensive runtime isolation
        log.warn("device.templates.tool_refresh_failed", {"error": str(exc)})


def _template_from_plugin_root(
    *,
    plugin_id: str,
    root: Path,
    state: str,
    installed: bool,
    source: str,
    fallback_name: Optional[str] = None,
    fallback_description: Optional[str] = None,
    fallback_description_cn: Optional[str] = None,
    fallback_version: Optional[str] = None,
) -> Optional[DeviceTemplate]:
    provider = _read_provider_yaml(root)
    if _integration_type(provider) != "device":
        return None
    service_id = str(provider.get("service_id") or provider.get("name") or "").strip()
    if not service_id:
        return None
    version = extract_provider_version(provider) or fallback_version
    descriptor = ApiServiceDescriptor(
        service_id=service_id,
        version=version,
        storage_key=derive_storage_key(service_id, version),
        provider_yaml=root / "_provider.yaml",
    )
    return _template_from_descriptor(
        plugin_id=plugin_id,
        descriptor=descriptor,
        provider=provider,
        state=state,
        installed=installed,
        source=source,
        fallback_name=fallback_name,
        fallback_description=fallback_description,
        fallback_description_cn=fallback_description_cn,
    )


def _template_from_descriptor(
    *,
    plugin_id: str,
    descriptor: ApiServiceDescriptor,
    provider: Dict[str, Any],
    state: str,
    installed: bool,
    source: str,
    fallback_name: Optional[str] = None,
    fallback_description: Optional[str] = None,
    fallback_description_cn: Optional[str] = None,
) -> DeviceTemplate:
    name = _template_name(provider, descriptor, fallback_name, plugin_id)
    description = _optional_str(provider.get("description")) or fallback_description
    description_cn = _optional_str(provider.get("description_cn")) or fallback_description_cn
    return DeviceTemplate(
        plugin_id=plugin_id,
        storage_key=descriptor.storage_key,
        service_id=descriptor.service_id,
        name=name,
        version=descriptor.version,
        vendor=_optional_str(provider.get("vendor")),
        description=description,
        description_cn=description_cn,
        credential_schema=[
            field.model_dump(mode="json")
            for field in _build_api_service_credential_schema(descriptor.storage_key, provider)
        ],
        tool_count=_tool_count(descriptor.storage_key, descriptor.provider_yaml.parent),
        installed=installed,
        state=state,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
    )


def _template_name(
    provider: Dict[str, Any],
    descriptor: ApiServiceDescriptor,
    fallback_name: Optional[str],
    plugin_id: str,
) -> str:
    display_name = _optional_str(provider.get("display_name"))
    if display_name:
        return display_name

    raw_name = str(provider.get("name") or fallback_name or plugin_id).strip() or plugin_id
    raw_identity = raw_name.lower()
    identity_values = {
        plugin_id.lower(),
        descriptor.service_id.lower(),
        descriptor.storage_key.lower(),
    }
    if raw_identity not in identity_values:
        return raw_name

    return _product_name_from_identifier(descriptor.service_id) or _product_name_from_identifier(plugin_id) or raw_name


def _product_name_from_identifier(value: str) -> str:
    candidate = value.strip()
    if candidate.endswith("_api"):
        candidate = candidate[:-4]
    candidate = re.sub(r"_v\d+(?:_\d+)*(?:_[A-Za-z]\d+)?$", "", candidate)
    return candidate or value


def _catalog_entry_root(entry: Any) -> Optional[Path]:
    if entry.installPath:
        path = Path(entry.installPath)
        if path.exists():
            return path
    return hub_catalog.system_plugin_root("device", entry.id)


def _entry_installed(entry: Any) -> bool:
    return entry.state in {"installed", "updateAvailable", "localOnly"} or bool(entry.installPath)


def _normalize_state(state: str) -> str:
    if state in {"available", "installed", "updateAvailable", "localOnly", "broken"}:
        return state
    return "broken"


def _source_from_entry(entry: Any) -> str:
    if entry.installPath:
        return _source_from_path(Path(entry.installPath))
    if entry.source == "system":
        root = hub_catalog.system_plugin_root("device", entry.id)
        return _source_from_path(root) if root else "project"
    return "bundled"


def _source_from_path(path: Optional[Path]) -> str:
    if path is None:
        return "bundled"
    try:
        resolved = path.resolve()
    except Exception:
        resolved = path
    try:
        resolved.relative_to((Path.cwd() / ".flocks" / "plugins").resolve())
        return "project"
    except Exception:
        pass
    try:
        resolved.relative_to((Path.home() / ".flocks" / "plugins").resolve())
        return "global"
    except Exception:
        pass
    return "bundled"


def _tool_count(storage_key: str, root: Path) -> int:
    try:
        ToolRegistry.init()
        count = len([
            tool for tool in ToolRegistry.list_tools()
            if tool.source == "device" and tool.provider == storage_key
        ])
        if count:
            return count
    except Exception as exc:
        log.debug("device.templates.tool_registry_unavailable", {"error": str(exc)})

    return len([
        path for path in _tool_yaml_files(root)
        if not path.name.startswith("_")
    ])


def _tool_yaml_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    return [
        path for path in root.iterdir()
        if path.is_file() and path.suffix in {".yaml", ".yml"}
    ]


def _read_provider_yaml(root: Path) -> Dict[str, Any]:
    path = root / "_provider.yaml"
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("device.templates.provider_yaml_read_failed", {
            "path": str(path),
            "error": str(exc),
        })
        return {}
    return data if isinstance(data, dict) else {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


def _custom_provider_yaml(body: CustomDeviceTemplateCreate) -> Dict[str, Any]:
    data: Dict[str, Any] = {
        "name": body.name.strip(),
        "service_id": body.service_id.strip(),
        "integration_type": "device",
        "credential_fields": body.credential_fields,
    }
    for key in ("vendor", "version", "description", "description_cn"):
        value = getattr(body, key)
        if value:
            data[key] = value
    return data


def _custom_tool_yaml(tool: Dict[str, Any], service_id: str) -> Dict[str, Any]:
    tool["provider"] = service_id
    tool.setdefault("enabled", True)
    if not tool.get("category"):
        tool["category"] = TOOL_TYPE_DEVICE
    return tool


def _integration_type(provider: Dict[str, Any]) -> Optional[str]:
    value = provider.get("integration_type")
    return value.strip().lower() if isinstance(value, str) and value.strip() else None


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sort_key(template: DeviceTemplate) -> tuple[int, str, str, str, str]:
    return (
        0 if template.installed else 1,
        (template.vendor or "").lower(),
        template.name.lower(),
        template.service_id.lower(),
        template.version or "",
    )
