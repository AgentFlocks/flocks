"""Filesystem store for user-defined pages."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from flocks.user_defined_pages.models import (
    UserDefinedPageApiMeta,
    UserDefinedPageBuildMeta,
    UserDefinedPageDetail,
    UserDefinedPageListItem,
    UserDefinedPageManifest,
)
from flocks.utils.log import Log

log = Log.create(service="user-defined-pages-store")

PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_SOURCE_FILE_BYTES = 512_000
ALLOWED_WRITE_PREFIXES = ("src/", "assets/", "api/")
ALLOWED_WRITE_FILES = frozenset({"manifest.json"})
_SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".css", ".json"}
_API_SUFFIXES = {".py", ".yaml", ".yml"}

def _default_page_tsx(title: str) -> str:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    return f"""import {{ useEffect, useState }} from 'react';
import {{ Card }} from '@flocks/user-defined-page-sdk';

export default function Page() {{
  const [ready, setReady] = useState(false);

  useEffect(() => {{
    setReady(true);
  }}, []);

  return (
    <Card title="{safe_title}">
      {{ready ? 'Ready' : 'Loading...'}}
    </Card>
  );
}}
"""

_DEFAULT_INDEX_TSX = """import Page from './Page';

export default Page;
"""


def get_user_defined_pages_root() -> Path:
    """Return canonical user-space root for user-defined pages."""
    override = os.environ.get("FLOCKS_USER_DEFINED_PAGES_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".flocks" / "plugins" / "user_defined_pages").resolve()


class UserDefinedPagesStore:
    """CRUD and scan helpers for ~/.flocks/plugins/user_defined_pages."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = (root or get_user_defined_pages_root()).resolve()

    @property
    def root(self) -> Path:
        return self._root

    def ensure_root(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        return self._root

    @staticmethod
    def validate_page_id(page_id: str) -> str:
        normalized = (page_id or "").strip().lower()
        if not PAGE_ID_RE.fullmatch(normalized):
            raise ValueError("invalid page id: use lowercase letters, numbers, and hyphens")
        return normalized

    def page_dir(self, page_id: str) -> Path:
        page_id = self.validate_page_id(page_id)
        page_path = (self._root / page_id).resolve()
        try:
            page_path.relative_to(self._root)
        except ValueError:
            raise ValueError("invalid page path")
        return page_path

    def _assert_writable_relative(self, relative_path: str) -> Path:
        if not relative_path or Path(relative_path).is_absolute():
            raise ValueError("absolute path is not allowed")
        rel = relative_path.replace("\\", "/").lstrip("/")
        if rel in ALLOWED_WRITE_FILES:
            return Path(rel)
        if any(rel.startswith(prefix) for prefix in ALLOWED_WRITE_PREFIXES):
            parts = rel.split("/")
            if ".." in parts:
                raise ValueError("path traversal is not allowed")
            if any(part.startswith(".") for part in parts if part):
                raise ValueError("hidden path is not allowed")
            return Path(rel)
        raise ValueError(f"writes are not allowed for path: {relative_path}")

    def list_pages(self, *, enabled_only: bool = False) -> list[UserDefinedPageListItem]:
        self.ensure_root()
        items: list[UserDefinedPageListItem] = []
        for child in sorted(self._root.iterdir()):
            if not child.is_dir():
                continue
            manifest = self._read_manifest(child.name)
            if manifest is None:
                continue
            if enabled_only and not manifest.enabled:
                continue
            build = self._read_build_meta(child.name)
            items.append(
                UserDefinedPageListItem(
                    id=manifest.id,
                    title=manifest.title,
                    route=manifest.route,
                    icon=manifest.icon,
                    order=manifest.order,
                    enabled=manifest.enabled,
                    placement=manifest.placement,
                    buildHash=build.hash,
                    buildStatus=build.status,
                )
            )
        items.sort(key=lambda item: (item.order, item.title))
        return items

    def get_page(self, page_id: str) -> UserDefinedPageDetail:
        page_dir = self.page_dir(page_id)
        if not page_dir.is_dir():
            raise FileNotFoundError(f"page not found: {page_id}")
        manifest = self._read_manifest(page_id)
        if manifest is None:
            raise FileNotFoundError(f"manifest missing for page: {page_id}")
        build = self._read_build_meta(page_id)
        source_files = sorted(
            str(path.relative_to(page_dir)).replace("\\", "/")
            for path in page_dir.rglob("*")
            if path.is_file() and "dist/" not in str(path.relative_to(page_dir)).replace("\\", "/")
        )
        return UserDefinedPageDetail(manifest=manifest, build=build, sourceFiles=source_files)

    def create_page(
        self,
        *,
        page_id: str,
        title: str,
        icon: str = "LayoutDashboard",
        order: int = 100,
    ) -> UserDefinedPageDetail:
        page_id = self.validate_page_id(page_id)
        page_dir = self.page_dir(page_id)
        if page_dir.exists():
            raise FileExistsError(f"page already exists: {page_id}")

        now_ms = int(time.time() * 1000)
        manifest = UserDefinedPageManifest(
            id=page_id,
            title=title.strip() or page_id,
            route=f"/user-defined-pages/{page_id}",
            icon=icon,
            order=order,
            enabled=True,
            placement="home.after",
            entry="src/index.tsx",
            updatedAt=now_ms,
        )

        page_dir.mkdir(parents=True, exist_ok=False)
        (page_dir / "src").mkdir(parents=True, exist_ok=True)
        (page_dir / "api").mkdir(parents=True, exist_ok=True)
        (page_dir / "assets").mkdir(parents=True, exist_ok=True)
        (page_dir / "dist").mkdir(parents=True, exist_ok=True)

        self._write_manifest(page_id, manifest)
        self._write_source_file(page_id, "src/Page.tsx", _default_page_tsx(manifest.title))
        self._write_source_file(page_id, "src/index.tsx", _DEFAULT_INDEX_TSX)
        self._write_build_meta(
            page_id,
            UserDefinedPageBuildMeta(status="idle", hash="", builtAt=0, error=None),
        )
        log.info("user_defined_pages.created", {"pageId": page_id})
        return self.get_page(page_id)

    def save_manifest(self, page_id: str, manifest_data: dict[str, Any]) -> UserDefinedPageManifest:
        page_id = self.validate_page_id(page_id)
        existing = self._read_manifest(page_id)
        if existing is None:
            raise FileNotFoundError(f"page not found: {page_id}")

        merged = existing.model_dump()
        merged.update(manifest_data)
        merged["id"] = page_id
        merged["route"] = f"/user-defined-pages/{page_id}"
        merged["updatedAt"] = int(time.time() * 1000)
        manifest = UserDefinedPageManifest.model_validate(merged)
        self._write_manifest(page_id, manifest)
        return manifest

    def save_source_file(self, page_id: str, relative_path: str, content: str) -> None:
        rel = self._assert_writable_relative(relative_path)
        rel_str = str(rel).replace("\\", "/")
        if rel_str.startswith("api/"):
            allowed_suffixes = _API_SUFFIXES
        else:
            allowed_suffixes = _SOURCE_SUFFIXES
        if rel.suffix not in allowed_suffixes:
            raise ValueError("unsupported source file type")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_SOURCE_FILE_BYTES:
            raise ValueError("source file is too large")
        self._write_source_file(page_id, rel_str, content)

    def read_source_file(self, page_id: str, relative_path: str) -> str:
        rel = self._assert_writable_relative(relative_path)
        path = self.page_dir(page_id) / rel
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")

    def bundle_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dist" / "page.js"

    def asset_path(self, page_id: str, relative_path: str) -> Path:
        rel = relative_path.replace("\\", "/").lstrip("/")
        if ".." in rel.split("/"):
            raise ValueError("path traversal is not allowed")
        path = (self.page_dir(page_id) / "assets" / rel).resolve()
        assets_root = (self.page_dir(page_id) / "assets").resolve()
        try:
            path.relative_to(assets_root)
        except ValueError:
            raise ValueError("invalid asset path")
        return path

    def write_build_meta(self, page_id: str, meta: UserDefinedPageBuildMeta) -> None:
        self._write_build_meta(page_id, meta)

    def read_build_meta(self, page_id: str) -> UserDefinedPageBuildMeta:
        return self._read_build_meta(page_id)

    def routes_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "api" / "routes.yaml"

    def api_handlers_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "api" / "handlers.py"

    def read_api_routes(self, page_id: str) -> Optional[str]:
        path = self.routes_path(page_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def write_api_meta(self, page_id: str, meta: UserDefinedPageApiMeta) -> None:
        path = self._api_meta_path(page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(meta.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_api_meta(self, page_id: str) -> UserDefinedPageApiMeta:
        path = self._api_meta_path(page_id)
        if not path.is_file():
            return UserDefinedPageApiMeta()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return UserDefinedPageApiMeta.model_validate(raw)
        except Exception:
            return UserDefinedPageApiMeta()

    def _manifest_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "manifest.json"

    def _build_meta_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dist" / "meta.json"

    def _api_meta_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dist" / "api-meta.json"

    def _read_manifest(self, page_id: str) -> Optional[UserDefinedPageManifest]:
        path = self._manifest_path(page_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return UserDefinedPageManifest.model_validate(raw)
        except Exception as exc:
            log.warning("user_defined_pages.manifest.invalid", {"pageId": page_id, "error": str(exc)})
            return None

    def _write_manifest(self, page_id: str, manifest: UserDefinedPageManifest) -> None:
        path = self._manifest_path(page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _read_build_meta(self, page_id: str) -> UserDefinedPageBuildMeta:
        path = self._build_meta_path(page_id)
        if not path.is_file():
            return UserDefinedPageBuildMeta()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return UserDefinedPageBuildMeta.model_validate(raw)
        except Exception:
            return UserDefinedPageBuildMeta()

    def _write_build_meta(self, page_id: str, meta: UserDefinedPageBuildMeta) -> None:
        path = self._build_meta_path(page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(meta.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _write_source_file(self, page_id: str, relative_path: str, content: str) -> None:
        rel = self._assert_writable_relative(relative_path)
        target = self.page_dir(page_id) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
