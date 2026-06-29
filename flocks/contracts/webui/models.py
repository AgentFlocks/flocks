"""Pydantic models for WebUI page plugins."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class WebUIPageManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Stable page identifier")
    title: str = Field(..., description="Navigation label")
    route: str = Field(..., description="WebUI route path")
    icon: str = Field("LayoutDashboard", description="Lucide icon name")
    order: int = Field(100, description="Sort order in navigation")
    enabled: bool = Field(True, description="Whether page appears in navigation")
    placement: Literal["home.after"] = Field(
        "home.after",
        description="Where to insert the nav item",
    )
    entry: str = Field("src/index.tsx", description="Source entry relative to page dir")
    updatedAt: int = Field(0, description="Last manifest update timestamp (ms)")


class WebUIWorkspaceManifest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(..., description="Stable workspace identifier")
    title: str = Field(..., description="Navigation label")
    icon: str = Field("LayoutDashboard", description="Lucide icon name")
    order: int = Field(100, description="Sort order in navigation")
    enabled: bool = Field(True, description="Whether workspace appears in navigation")
    placement: Literal["sceneWorkspace", "aiWorkbench"] = Field(
        "sceneWorkspace",
        description="Where to insert the nav item",
    )
    defaultPageId: Optional[str] = Field(None, description="Preferred default page id", alias="defaultPageId")


class WebUIPageBuildMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    hash: str = Field("", description="Content hash for cache busting")
    builtAt: int = Field(0, description="Build timestamp (ms)")
    status: Literal["idle", "building", "ready", "failed"] = Field("idle")
    error: Optional[str] = Field(None, description="Last build error message")
    runtime: str = Field("webui_page", description="Builder runtime marker")
    runtimeVersion: int = Field(1, description="Builder runtime version")
    sdkImport: str = Field("@flocks/webui-contract-sdk", description="SDK import marker")


class WebUIPageApiMeta(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    status: Literal["idle", "ready", "failed"] = Field("idle")
    loadedAt: int = Field(0, description="Runtime load timestamp (ms)")
    error: Optional[str] = Field(None, description="Last API runtime error")
    routes: list[dict[str, str]] = Field(default_factory=list, description="Loaded route descriptors")


class WebUIPageListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    id: str
    title: str
    route: str
    icon: str
    order: int
    enabled: bool
    placement: str
    buildHash: str = Field("", alias="buildHash")
    buildStatus: str = Field("idle", alias="buildStatus")
    workspaceId: Optional[str] = Field(None, alias="workspaceId")
    workspaceTitle: Optional[str] = Field(None, alias="workspaceTitle")
    workspaceRoute: Optional[str] = Field(None, alias="workspaceRoute")


class WebUIWorkspaceListItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    id: str
    title: str
    route: str
    icon: str
    order: int
    enabled: bool
    placement: str
    defaultPageId: Optional[str] = Field(None, alias="defaultPageId")
    pages: list[WebUIPageListItem] = Field(default_factory=list)


class WebUIPageDetail(BaseModel):
    model_config = ConfigDict(populate_by_name=True, by_alias=True)

    manifest: WebUIPageManifest
    build: WebUIPageBuildMeta
    sourceFiles: list[str] = Field(default_factory=list, alias="sourceFiles")
