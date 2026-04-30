"""Data models for the bundled Flocks Hub."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


PluginType = Literal["skill", "agent", "tool", "workflow"]
PluginState = Literal[
    "available",
    "installed",
    "updateAvailable",
    "localOnly",
    "broken",
    "incompatible",
]


class HubSource(BaseModel):
    kind: Literal["bundled", "github", "cloud"] = "bundled"
    path: Optional[str] = None
    repo: Optional[str] = None
    ref: Optional[str] = None


class HubCompatibility(BaseModel):
    flocks: Optional[str] = None
    os: list[str] = Field(default_factory=list)


class HubDependencies(BaseModel):
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    python: list[str] = Field(default_factory=list)
    external: list[str] = Field(default_factory=list)


class HubPermissions(BaseModel):
    tools: list[str] = Field(default_factory=list)
    network: bool = False
    shell: bool = False
    filesystem: str = "none"


class HubRisk(BaseModel):
    level: Literal["low", "medium", "high"] = "low"
    reasons: list[str] = Field(default_factory=list)


class HubPluginManifest(BaseModel):
    model_config = ConfigDict(extra="allow")

    schemaVersion: str
    id: str
    type: PluginType
    name: str
    description: str = ""
    version: str = "0.0.0"
    author: Optional[str] = None
    license: Optional[str] = None
    homepage: Optional[str] = None
    category: str = "default"
    tags: list[str] = Field(default_factory=list)
    useCases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    trust: Literal["official", "verified", "community", "experimental", "deprecated"] = "community"
    source: HubSource = Field(default_factory=HubSource)
    compatibility: HubCompatibility = Field(default_factory=HubCompatibility)
    dependencies: HubDependencies = Field(default_factory=HubDependencies)
    permissions: HubPermissions = Field(default_factory=HubPermissions)
    risk: HubRisk = Field(default_factory=HubRisk)
    entrypoints: list[str] = Field(default_factory=list)
    checksums: dict[str, str] = Field(default_factory=dict)


class HubIndexEntry(BaseModel):
    id: str
    type: PluginType
    name: str
    description: str = ""
    version: str = "0.0.0"
    category: str = "default"
    tags: list[str] = Field(default_factory=list)
    useCases: list[str] = Field(default_factory=list)
    trust: str = "community"
    riskLevel: str = "low"
    manifestPath: str


class HubIndex(BaseModel):
    schemaVersion: str
    generatedAt: Optional[str] = None
    source: HubSource = Field(default_factory=HubSource)
    plugins: list[HubIndexEntry] = Field(default_factory=list)


class InstalledPluginRecord(BaseModel):
    id: str
    type: PluginType
    version: str = "0.0.0"
    source: str = ""
    installedAt: int
    enabled: bool = True
    scope: Literal["global", "project"] = "global"
    checksum: Optional[str] = None
    installPath: Optional[str] = None


class HubCatalogEntry(BaseModel):
    id: str
    type: PluginType
    name: str
    description: str = ""
    version: str = "0.0.0"
    category: str = "default"
    tags: list[str] = Field(default_factory=list)
    useCases: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    trust: str = "community"
    riskLevel: str = "low"
    state: PluginState = "available"
    installedVersion: Optional[str] = None
    source: str = "bundled"
    manifestPath: str
    installPath: Optional[str] = None
    native: bool = False
    brokenReason: Optional[str] = None


class HubTaxonomy(BaseModel):
    schemaVersion: str
    categories: list[dict[str, Any]] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    tagLabels: dict[str, dict[str, str]] = Field(default_factory=dict)
    useCases: list[str] = Field(default_factory=list)
    useCaseLabels: dict[str, dict[str, str]] = Field(default_factory=dict)
    domains: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    trustLevels: list[str] = Field(default_factory=list)
    riskLevels: list[str] = Field(default_factory=list)


class HubFileNode(BaseModel):
    name: str
    path: str
    type: Literal["file", "directory"]
    size: int = 0
    checksum: Optional[str] = None
    previewable: bool = False
    children: list["HubFileNode"] = Field(default_factory=list)


class HubFileContent(BaseModel):
    path: str
    content: str
    size: int
    checksum: Optional[str] = None
    language: Optional[str] = None
