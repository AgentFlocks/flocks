"""Bundled Hub catalog reader and state merger."""

from __future__ import annotations

import json
import platform
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from flocks.hub import local
from flocks.hub.models import HubCatalogEntry, HubIndex, HubIndexEntry, HubPluginManifest, HubTaxonomy, PluginType
from flocks.hub.paths import get_bundled_hub_root


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _frontmatter(path: Path) -> dict[str, Any]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            try:
                import yaml

                data = yaml.safe_load("\n".join(lines[1:index])) or {}
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
    return {}


@lru_cache(maxsize=1)
def load_index() -> HubIndex:
    root = get_bundled_hub_root()
    path = root / "index.json"
    if not path.is_file():
        return HubIndex(schemaVersion="hub.index.v1", plugins=[])
    return HubIndex.model_validate(_read_json(path))


@lru_cache(maxsize=1)
def load_taxonomy() -> HubTaxonomy:
    root = get_bundled_hub_root()
    path = root / "taxonomy.json"
    if not path.is_file():
        return HubTaxonomy(schemaVersion="hub.taxonomy.v1")
    return HubTaxonomy.model_validate(_read_json(path))


@lru_cache(maxsize=1)
def _manifest_path_lookup() -> dict[tuple[str, str], str]:
    return {(item.type, item.id): item.manifestPath for item in load_index().plugins}


def manifest_path(plugin_type: PluginType, plugin_id: str) -> Path:
    root = get_bundled_hub_root()
    direct = root / "plugins" / f"{plugin_type}s" / plugin_id / "manifest.json"
    if direct.is_file():
        return direct
    manifest_rel = _manifest_path_lookup().get((plugin_type, plugin_id))
    if manifest_rel:
        path = (root / manifest_rel).resolve()
        bundled = root.resolve()
        if bundled not in path.parents and path != bundled:
            raise ValueError("manifest path escapes hub root")
        return path
    return direct


def load_manifest(plugin_type: PluginType, plugin_id: str) -> HubPluginManifest:
    path = manifest_path(plugin_type, plugin_id)
    if path.is_file():
        return HubPluginManifest.model_validate(_read_json(path))
    manifest = system_plugin_manifest(plugin_type, plugin_id)
    if manifest:
        return manifest
    return HubPluginManifest.model_validate(_read_json(path))


def _safe_tags(values: Iterable[str]) -> list[str]:
    allowed = set(load_taxonomy().tags)
    return [value for value in values if value in allowed]


def _system_manifest_path(plugin_type: PluginType, plugin_id: str) -> str:
    return f"system/{plugin_type}s/{plugin_id}/manifest.json"


def _system_source_path(root: Path) -> str:
    project_root = local._project_plugins_root().parent.parent
    try:
        return root.relative_to(project_root).as_posix()
    except Exception:
        return str(root)


def _base_manifest(
    *,
    plugin_type: PluginType,
    plugin_id: str,
    name: str,
    description: str,
    category: str,
    tags: Optional[list[str]] = None,
    use_cases: Optional[list[str]] = None,
    capabilities: Optional[list[str]] = None,
    entrypoints: Optional[list[str]] = None,
    root: Path,
    network: bool = False,
    shell: bool = False,
    filesystem: str = "none",
    tools: Optional[list[str]] = None,
    risk_level: str = "low",
) -> HubPluginManifest:
    return HubPluginManifest(
        schemaVersion="hub.plugin.v1",
        id=plugin_id,
        type=plugin_type,
        name=name or plugin_id,
        description=description or "",
        version="1.0.0",
        author="Flocks Team",
        license="MIT",
        homepage="",
        category=category,
        tags=_safe_tags(tags or []),
        useCases=use_cases or ["other"],
        domains=["security-ops"],
        capabilities=capabilities or [],
        trust="official",
        source={"kind": "bundled", "path": _system_source_path(root)},
        compatibility={"flocks": ">=0.8.0", "os": ["darwin", "linux", "windows"]},
        dependencies={"skills": [], "tools": [], "python": [], "external": []},
        permissions={
            "tools": tools or [],
            "network": network,
            "shell": shell,
            "filesystem": filesystem,
        },
        risk={"level": risk_level, "reasons": []},
        entrypoints=entrypoints or [],
        checksums={},
    )


def _skill_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    skill_file = root / "SKILL.md"
    if not skill_file.is_file():
        return None
    data = _frontmatter(skill_file)
    return _base_manifest(
        plugin_type="skill",
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        description=str(data.get("description") or ""),
        category="devtools" if plugin_id == "agent-browser" else "threat-intel",
        tags=_safe_tags(["api-security"] if plugin_id == "agent-browser" else ["ndr"]),
        use_cases=["integration"] if plugin_id == "agent-browser" else ["threat-intelligence"],
        capabilities=["file-analysis"],
        entrypoints=["SKILL.md"],
        root=root,
        filesystem="read",
    )


def _agent_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    agent_file = root / "agent.yaml"
    if not agent_file.is_file():
        return None
    data = _read_yaml(agent_file)
    tools = [str(item) for item in data.get("tools", []) if isinstance(item, str)]
    network = any(item in {"websearch", "webfetch"} or item.endswith("_query") for item in tools)
    shell = "bash" in tools
    filesystem = "write" if any(item in {"edit", "write"} for item in tools) else "read"
    return _base_manifest(
        plugin_type="agent",
        plugin_id=plugin_id,
        name=str(data.get("name") or plugin_id),
        description=str(data.get("description_cn") or data.get("description") or ""),
        category="detection",
        tags=_safe_tags([str(item) for item in data.get("tags", []) if isinstance(item, str)]),
        use_cases=["alert-triage"],
        capabilities=["llm-agent"],
        entrypoints=[item for item in ("agent.yaml", str(data.get("prompt_file") or "prompt.md")) if (root / item).exists()],
        root=root,
        network=network,
        shell=shell,
        filesystem=filesystem,
        tools=tools,
        risk_level="medium" if network or shell else "low",
    )


def _workflow_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    workflow_file = root / "workflow.json"
    if not workflow_file.is_file() and not (root / "workflow.md").is_file():
        return None
    data = _read_json(workflow_file) if workflow_file.is_file() else {}
    entrypoints = [name for name in ("workflow.json", "workflow.md") if (root / name).exists()]
    return _base_manifest(
        plugin_type="workflow",
        plugin_id=plugin_id,
        name=str(data.get("name") or data.get("id") or plugin_id),
        description=str(data.get("description") or ""),
        category="workflow-automation",
        tags=_safe_tags(["siem", "ndr"] if "tdp" in plugin_id else ["hids", "linux"]),
        use_cases=["alert-triage"] if "tdp" in plugin_id else ["endpoint-forensics"],
        capabilities=["workflow"],
        entrypoints=entrypoints,
        root=root,
        filesystem="write",
    )


def _has_direct_tool_payload(root: Path) -> bool:
    return any(
        path.is_file()
        and (
            (path.suffix in {".yaml", ".yml"} and not path.name.startswith("_"))
            or (path.suffix == ".py" and path.name != "__init__.py")
        )
        for path in root.iterdir()
    )


def _tool_manifest(plugin_id: str, root: Path) -> Optional[HubPluginManifest]:
    if not _has_direct_tool_payload(root):
        return None
    provider = _read_yaml(root / "_provider.yaml") if (root / "_provider.yaml").is_file() else {}
    yaml_files = sorted(path for path in root.glob("*.y*ml") if not path.name.startswith("_"))
    first_tool = _read_yaml(yaml_files[0]) if yaml_files else {}
    entrypoints = [
        path.name
        for path in sorted(root.iterdir(), key=lambda item: item.name)
        if path.is_file() and path.suffix in {".yaml", ".yml", ".py"}
    ]
    return _base_manifest(
        plugin_type="tool",
        plugin_id=plugin_id,
        name=str(provider.get("name") or first_tool.get("name") or plugin_id),
        description=str(provider.get("description_cn") or provider.get("description") or first_tool.get("description") or ""),
        category="integration",
        tags=_safe_tags(["ioc", "vulnerability"]),
        use_cases=["integration", "threat-intelligence"],
        capabilities=["external-api"],
        entrypoints=entrypoints,
        root=root,
        network=True,
        filesystem="none",
        risk_level="medium",
    )


def _system_plugin_roots() -> dict[tuple[PluginType, str], Path]:
    roots: dict[tuple[PluginType, str], Path] = {}

    for plugin_type, detector in (
        ("skill", _skill_manifest),
        ("agent", _agent_manifest),
        ("workflow", _workflow_manifest),
    ):
        base = local.install_root(plugin_type, "project")
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir(), key=lambda item: item.name):
            if child.is_dir() and detector(child.name, child):
                roots[(plugin_type, child.name)] = child

    tools_root = local.install_root("tool", "project")
    if tools_root.is_dir():
        for directory in sorted((path for path in tools_root.rglob("*") if path.is_dir()), key=lambda item: item.as_posix()):
            if _tool_manifest(directory.name, directory):
                roots[("tool", directory.name)] = directory

    return roots


def system_plugin_root(plugin_type: PluginType, plugin_id: str) -> Optional[Path]:
    return _system_plugin_roots().get((plugin_type, plugin_id))


def system_plugin_manifest(plugin_type: PluginType, plugin_id: str) -> Optional[HubPluginManifest]:
    root = system_plugin_root(plugin_type, plugin_id)
    if root is None:
        return None
    if plugin_type == "skill":
        return _skill_manifest(plugin_id, root)
    if plugin_type == "agent":
        return _agent_manifest(plugin_id, root)
    if plugin_type == "workflow":
        return _workflow_manifest(plugin_id, root)
    if plugin_type == "tool":
        return _tool_manifest(plugin_id, root)
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", value or "")
    return tuple(int(part) for part in parts) if parts else (0,)


def _os_compatible(manifest: HubPluginManifest) -> bool:
    allowed = {item.lower() for item in manifest.compatibility.os}
    if not allowed:
        return True
    current = platform.system().lower()
    aliases = {"darwin": "darwin", "linux": "linux", "windows": "windows"}
    return aliases.get(current, current) in allowed or current in allowed


def _entry_from_manifest(manifest: HubPluginManifest) -> HubCatalogEntry:
    record = local.get_record(manifest.type, manifest.id)
    install_path = _resolve_install_path(manifest.type, manifest.id, record)

    state = "available"
    installed_version: Optional[str] = None
    if install_path:
        installed_version = record.version if record else manifest.version
        state = "installed"
        if _version_tuple(installed_version) < _version_tuple(manifest.version):
            state = "updateAvailable"
    if not _os_compatible(manifest):
        state = "incompatible"

    return HubCatalogEntry(
        id=manifest.id,
        type=manifest.type,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        category=manifest.category,
        tags=manifest.tags,
        useCases=manifest.useCases,
        domains=manifest.domains,
        capabilities=manifest.capabilities,
        trust=manifest.trust,
        riskLevel=manifest.risk.level,
        state=state,
        installedVersion=installed_version,
        source=manifest.source.kind,
        manifestPath=str(manifest_path(manifest.type, manifest.id).relative_to(get_bundled_hub_root())),
        installPath=str(install_path) if install_path else None,
        native=False,
    )


def _resolve_install_path(
    plugin_type: PluginType,
    plugin_id: str,
    record: Optional[local.InstalledPluginRecord],
) -> Optional[Path]:
    if record and record.installPath:
        path = Path(record.installPath)
        if local.has_install_payload(plugin_type, path):
            return path
        local.remove_installed_record(plugin_type, plugin_id)
    return local.infer_local_install(plugin_type, plugin_id)


def _entry_from_index(
    item: HubIndexEntry,
    records: dict[str, local.InstalledPluginRecord],
) -> HubCatalogEntry:
    record = records.get(f"{item.type}:{item.id}")
    install_path = _resolve_install_path(item.type, item.id, record)

    state = "available"
    installed_version: Optional[str] = None
    if install_path:
        installed_version = record.version if record else item.version
        state = "installed"
        if _version_tuple(installed_version) < _version_tuple(item.version):
            state = "updateAvailable"

    return HubCatalogEntry(
        id=item.id,
        type=item.type,
        name=item.name,
        description=item.description,
        version=item.version,
        category=item.category,
        tags=item.tags,
        useCases=item.useCases,
        trust=item.trust,
        riskLevel=item.riskLevel,
        state=state,
        installedVersion=installed_version,
        source="bundled",
        manifestPath=item.manifestPath,
        installPath=str(install_path) if install_path else None,
        native=False,
    )


def _entry_from_system_manifest(manifest: HubPluginManifest, root: Path) -> HubCatalogEntry:
    return HubCatalogEntry(
        id=manifest.id,
        type=manifest.type,
        name=manifest.name,
        description=manifest.description,
        version=manifest.version,
        category=manifest.category,
        tags=manifest.tags,
        useCases=manifest.useCases,
        domains=manifest.domains,
        capabilities=manifest.capabilities,
        trust=manifest.trust,
        riskLevel=manifest.risk.level,
        state="installed",
        installedVersion=manifest.version,
        source="system",
        manifestPath=_system_manifest_path(manifest.type, manifest.id),
        installPath=str(root),
        native=True,
    )


def list_manifests() -> list[HubPluginManifest]:
    root = get_bundled_hub_root()
    index = load_index()
    result: list[HubPluginManifest] = []
    for item in index.plugins:
        path = root / item.manifestPath
        try:
            result.append(HubPluginManifest.model_validate(_read_json(path)))
        except Exception:
            continue
    return result


def _contains_any(values: Iterable[str], selected: Optional[list[str]]) -> bool:
    if not selected:
        return True
    value_set = {value.lower() for value in values}
    return any(item.lower() in value_set for item in selected)


def list_catalog(
    *,
    plugin_type: Optional[PluginType] = None,
    category: Optional[list[str]] = None,
    tags: Optional[list[str]] = None,
    use_cases: Optional[list[str]] = None,
    state: Optional[list[str]] = None,
    trust: Optional[list[str]] = None,
    risk: Optional[list[str]] = None,
    q: Optional[str] = None,
) -> list[HubCatalogEntry]:
    records = local.load_installed_records()
    entries = [
        _entry_from_index(item, records)
        for item in load_index().plugins
    ]
    indexed = {(entry.type, entry.id) for entry in entries}
    for (system_type, system_id), root in _system_plugin_roots().items():
        if (system_type, system_id) in indexed:
            continue
        manifest = system_plugin_manifest(system_type, system_id)
        if manifest:
            entries.append(_entry_from_system_manifest(manifest, root))
    query = (q or "").strip().lower()

    def keep(entry: HubCatalogEntry) -> bool:
        if plugin_type and entry.type != plugin_type:
            return False
        if category and entry.category not in category:
            return False
        if not _contains_any(entry.tags, tags):
            return False
        if not _contains_any(entry.useCases, use_cases):
            return False
        if state and entry.state not in state:
            return False
        if trust and entry.trust not in trust:
            return False
        if risk and entry.riskLevel not in risk:
            return False
        if query:
            haystack = " ".join([entry.id, entry.name, entry.description, entry.category, *entry.tags, *entry.useCases]).lower()
            if query not in haystack:
                return False
        return True

    return [entry for entry in entries if keep(entry)]


def category_counts() -> dict:
    taxonomy = load_taxonomy().model_dump(mode="json")
    entries = list_catalog()

    def count_by(attr: str) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in entries:
            values = getattr(entry, attr)
            if isinstance(values, str):
                values = [values]
            for value in values:
                counts[value] = counts.get(value, 0) + 1
        return counts

    taxonomy["counts"] = {
        "type": count_by("type"),
        "category": count_by("category"),
        "tags": count_by("tags"),
        "useCases": count_by("useCases"),
        "state": count_by("state"),
        "trust": count_by("trust"),
        "riskLevel": count_by("riskLevel"),
    }
    return taxonomy
