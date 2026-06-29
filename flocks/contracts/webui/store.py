"""Filesystem store for WebUI pages."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any, Optional

from flocks.contracts.webui.models import (
    WebUIPageApiMeta,
    WebUIPageBuildMeta,
    WebUIPageDetail,
    WebUIPageListItem,
    WebUIPageManifest,
)
from flocks.utils.log import Log

log = Log.create(service="webui-pages-store")

PAGE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_SOURCE_FILE_BYTES = 512_000
ALLOWED_WRITE_PREFIXES = ("src/", "assets/", "api/")
ALLOWED_WRITE_FILES = frozenset({"manifest.json"})
_SOURCE_SUFFIXES = {".tsx", ".ts", ".jsx", ".js", ".css", ".json"}
_API_SUFFIXES = {".py", ".yaml", ".yml"}
_MIGRATION_TEXT_SUFFIXES = _SOURCE_SUFFIXES | _API_SUFFIXES
_PROJECT_ROOT_UNSET = object()
_LEGACY_ROOT_UNSET = object()
WEBUI_CONTRACT_ROUTE_PREFIX = "/contracts/webui"
WEBUI_CONTRACT_SDK_IMPORT = "@flocks/webui-contract-sdk"
LEGACY_WEBUI_PAGE_ROUTE_PREFIX = "/user-defined-pages"
LEGACY_WEBUI_PAGE_SDK_IMPORT = "@flocks/user-defined-page-sdk"
LEGACY_WEBUI_PAGE_SDK_GLOBAL = "__FLOCKS_USER_DEFINED_PAGE_SDK__"
WEBUI_CONTRACT_SDK_GLOBAL = "__FLOCKS_WEBUI_CONTRACT_SDK__"


def _default_page_tsx(title: str) -> str:
    safe_title = title.replace("\\", "\\\\").replace('"', '\\"')
    return f"""import {{ useEffect, useState }} from 'react';
import {{ Card }} from '{WEBUI_CONTRACT_SDK_IMPORT}';

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


def webui_contract_page_route(page_id: str) -> str:
    return f"{WEBUI_CONTRACT_ROUTE_PREFIX}/{page_id}"


def get_webui_pages_root() -> Path:
    """Return the canonical user-space write root for WebUI pages."""
    override = os.environ.get("FLOCKS_CONTRACTS_WEBUI_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".flocks" / "plugins" / "contracts" / "webui").resolve()


def get_legacy_webui_pages_root() -> Path:
    """Return the legacy user-space root used before WebUI contracts."""
    override = os.environ.get("FLOCKS_USER_DEFINED_PAGES_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / ".flocks" / "plugins" / "user_defined_pages").resolve()


def get_project_webui_pages_root(project_dir: Optional[Path] = None) -> Path:
    """Return the project-space read root for checked-in WebUI pages."""
    base = project_dir or Path.cwd()
    return (base / ".flocks" / "plugins" / "contracts" / "webui").resolve()


class WebUIPagesStore:
    """CRUD and scan helpers for user-space WebUI pages."""

    def __init__(
        self,
        root: Optional[Path] = None,
        *,
        project_root: Optional[Path] | object = _PROJECT_ROOT_UNSET,
        legacy_root: Optional[Path] | object = _LEGACY_ROOT_UNSET,
        project_dir: Optional[Path] = None,
    ) -> None:
        env_override = os.environ.get("FLOCKS_CONTRACTS_WEBUI_ROOT")
        self._root = (root or get_webui_pages_root()).resolve()
        if project_root is _PROJECT_ROOT_UNSET:
            project_root = None if root is not None or env_override else get_project_webui_pages_root(project_dir)
        if legacy_root is _LEGACY_ROOT_UNSET:
            legacy_env = os.environ.get("FLOCKS_USER_DEFINED_PAGES_ROOT")
            legacy_root = get_legacy_webui_pages_root() if legacy_env or (root is None and not env_override) else None
        self._project_root = project_root.resolve() if isinstance(project_root, Path) else None
        self._legacy_root = legacy_root.resolve() if isinstance(legacy_root, Path) else None
        self._read_roots = self._dedupe_roots(self._root, self._legacy_root, self._project_root)
        self._legacy_migration_done = False

    @property
    def root(self) -> Path:
        return self._root

    @property
    def read_roots(self) -> tuple[Path, ...]:
        return self._read_roots

    def ensure_root(self) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_pages()
        return self._root

    @staticmethod
    def validate_page_id(page_id: str) -> str:
        normalized = (page_id or "").strip().lower()
        if not PAGE_ID_RE.fullmatch(normalized):
            raise ValueError("invalid page id: use lowercase letters, numbers, and hyphens")
        return normalized

    def page_dir(self, page_id: str) -> Path:
        page_id = self.validate_page_id(page_id)
        existing = self._find_page_dir(page_id)
        if existing is not None:
            return existing
        return self._page_dir_in_root(self._root, page_id)

    def root_page_dir(self, page_id: str) -> Path:
        """Return the canonical user-root path for a page without copying."""
        return self._page_dir_in_root(self._root, self.validate_page_id(page_id))

    def writable_page_dir(self, page_id: str) -> Path:
        """Return a writable page directory, copying read-only pages on write."""
        page_id = self.validate_page_id(page_id)
        self.ensure_root()
        target = self._page_dir_in_root(self._root, page_id)
        if target.is_dir():
            return target

        source = self._find_page_dir(page_id)
        if source is not None and source != target:
            shutil.copytree(source, target)
            self._normalize_migrated_page(target, page_id)
            log.info("webui_pages.materialized", {"pageId": page_id, "source": str(source), "target": str(target)})
        return target

    def page_exists(self, page_id: str) -> bool:
        return self._find_page_dir(self.validate_page_id(page_id)) is not None

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

    def list_pages(self, *, enabled_only: bool = False) -> list[WebUIPageListItem]:
        self.ensure_root()
        items: list[WebUIPageListItem] = []
        seen_keys: set[str] = set()
        for root in self._read_roots:
            if not root.is_dir():
                continue
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if child.name in seen_keys:
                    continue
                manifest = self._read_manifest(child.name)
                if manifest is None:
                    continue
                if child.name in seen_keys or manifest.id in seen_keys:
                    continue
                seen_keys.update({child.name, manifest.id})
                if enabled_only and not manifest.enabled:
                    continue
                build = self._read_build_meta(child.name)
                items.append(
                    WebUIPageListItem(
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

    def get_page(self, page_id: str) -> WebUIPageDetail:
        self.ensure_root()
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
        return WebUIPageDetail(manifest=manifest, build=build, sourceFiles=source_files)

    def create_page(
        self,
        *,
        page_id: str,
        title: str,
        icon: str = "LayoutDashboard",
        order: int = 100,
    ) -> WebUIPageDetail:
        page_id = self.validate_page_id(page_id)
        if self.page_exists(page_id):
            raise FileExistsError(f"page already exists: {page_id}")
        page_dir = self._page_dir_in_root(self._root, page_id)

        now_ms = int(time.time() * 1000)
        manifest = WebUIPageManifest(
            id=page_id,
            title=title.strip() or page_id,
            route=webui_contract_page_route(page_id),
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
            WebUIPageBuildMeta(status="idle", hash="", builtAt=0, error=None),
        )
        log.info("webui_pages.created", {"pageId": page_id})
        return self.get_page(page_id)

    def save_manifest(self, page_id: str, manifest_data: dict[str, Any]) -> WebUIPageManifest:
        page_id = self.validate_page_id(page_id)
        existing = self._read_manifest(page_id)
        if existing is None:
            raise FileNotFoundError(f"page not found: {page_id}")

        merged = existing.model_dump()
        merged.update(manifest_data)
        merged["id"] = page_id
        merged["route"] = webui_contract_page_route(page_id)
        merged["updatedAt"] = int(time.time() * 1000)
        manifest = WebUIPageManifest.model_validate(merged)
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
        self.ensure_root()
        rel = self._assert_writable_relative(relative_path)
        path = self.page_dir(page_id) / rel
        if not path.is_file():
            raise FileNotFoundError(relative_path)
        return path.read_text(encoding="utf-8")

    def bundle_path(self, page_id: str) -> Path:
        self.ensure_root()
        return self.page_dir(page_id) / "dist" / "page.js"

    def asset_path(self, page_id: str, relative_path: str) -> Path:
        self.ensure_root()
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

    def write_build_meta(self, page_id: str, meta: WebUIPageBuildMeta) -> None:
        self._write_build_meta(page_id, meta)

    def read_build_meta(self, page_id: str) -> WebUIPageBuildMeta:
        return self._read_build_meta(page_id)

    def routes_path(self, page_id: str) -> Path:
        self.ensure_root()
        return self.page_dir(page_id) / "api" / "routes.yaml"

    def api_handlers_path(self, page_id: str) -> Path:
        self.ensure_root()
        return self.page_dir(page_id) / "api" / "handlers.py"

    def read_api_routes(self, page_id: str) -> Optional[str]:
        path = self.routes_path(page_id)
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")

    def write_api_meta(self, page_id: str, meta: WebUIPageApiMeta) -> None:
        path = self._api_meta_path(page_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(meta.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_api_meta(self, page_id: str) -> WebUIPageApiMeta:
        path = self._api_meta_path(page_id)
        if not path.is_file():
            return WebUIPageApiMeta()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return WebUIPageApiMeta.model_validate(raw)
        except Exception:
            return WebUIPageApiMeta()

    def _manifest_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "manifest.json"

    def _build_meta_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dist" / "meta.json"

    def _api_meta_path(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dist" / "api-meta.json"

    def _read_manifest(self, page_id: str) -> Optional[WebUIPageManifest]:
        path = self._manifest_path(page_id)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            manifest = WebUIPageManifest.model_validate(raw)
            expected_route = webui_contract_page_route(page_id)
            if manifest.id != page_id or manifest.route != expected_route:
                return manifest.model_copy(update={"id": page_id, "route": expected_route})
            return manifest
        except Exception as exc:
            log.warning("webui_pages.manifest.invalid", {"pageId": page_id, "error": str(exc)})
            return None

    def _write_manifest(self, page_id: str, manifest: WebUIPageManifest) -> None:
        self._write_manifest_at(self.writable_page_dir(page_id), manifest)

    def _read_build_meta(self, page_id: str) -> WebUIPageBuildMeta:
        path = self._build_meta_path(page_id)
        if not path.is_file():
            return WebUIPageBuildMeta()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return WebUIPageBuildMeta.model_validate(raw)
        except Exception:
            return WebUIPageBuildMeta()

    def _write_build_meta(self, page_id: str, meta: WebUIPageBuildMeta) -> None:
        self._write_build_meta_at(self.writable_page_dir(page_id), meta)

    @staticmethod
    def _write_manifest_at(page_dir: Path, manifest: WebUIPageManifest) -> None:
        path = page_dir / "manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(manifest.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _write_build_meta_at(page_dir: Path, meta: WebUIPageBuildMeta) -> None:
        path = page_dir / "dist" / "meta.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(meta.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_source_file(self, page_id: str, relative_path: str, content: str) -> None:
        rel = self._assert_writable_relative(relative_path)
        target = self.writable_page_dir(page_id) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def _migrate_legacy_pages(self) -> None:
        if self._legacy_migration_done:
            return
        self._legacy_migration_done = True
        legacy_root = self._legacy_root
        if legacy_root is None or not legacy_root.is_dir() or legacy_root == self._root:
            return

        for child in sorted(legacy_root.iterdir()):
            if not child.is_dir():
                continue
            try:
                page_id = self.validate_page_id(child.name)
                target = self._page_dir_in_root(self._root, page_id)
            except ValueError:
                continue
            if target.exists():
                continue
            try:
                shutil.copytree(child, target)
                self._normalize_migrated_page(target, page_id)
                log.info("webui_pages.legacy_migrated", {"pageId": page_id, "source": str(child), "target": str(target)})
            except Exception as exc:
                log.warning("webui_pages.legacy_migration_failed", {"pageId": child.name, "error": str(exc)})

    def _normalize_migrated_page(self, page_dir: Path, page_id: str) -> None:
        manifest_path = page_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest = WebUIPageManifest.model_validate(raw).model_copy(
                    update={
                        "id": page_id,
                        "route": webui_contract_page_route(page_id),
                        "updatedAt": int(time.time() * 1000),
                    }
                )
                self._write_manifest_at(page_dir, manifest)
            except Exception as exc:
                log.warning("webui_pages.legacy_manifest_normalize_failed", {"pageId": page_id, "error": str(exc)})

        replacements = {
            LEGACY_WEBUI_PAGE_SDK_IMPORT: WEBUI_CONTRACT_SDK_IMPORT,
            LEGACY_WEBUI_PAGE_SDK_GLOBAL: WEBUI_CONTRACT_SDK_GLOBAL,
            f"{LEGACY_WEBUI_PAGE_ROUTE_PREFIX}/": f"{WEBUI_CONTRACT_ROUTE_PREFIX}/",
            "/api/user-defined-pages/": "/api/contracts/webui/pages/",
        }
        for path in page_dir.rglob("*"):
            if not path.is_file() or path.suffix not in _MIGRATION_TEXT_SUFFIXES:
                continue
            rel = str(path.relative_to(page_dir)).replace("\\", "/")
            if not (rel.startswith("src/") or rel.startswith("api/") or rel == "dist/page.js"):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            updated = content
            for old, new in replacements.items():
                updated = updated.replace(old, new)
            if updated != content:
                path.write_text(updated, encoding="utf-8")

        self._write_build_meta_at(page_dir, WebUIPageBuildMeta(status="idle", hash="", builtAt=0, error=None))

    @staticmethod
    def _dedupe_roots(*roots: Optional[Path]) -> tuple[Path, ...]:
        result: list[Path] = []
        seen: set[Path] = set()
        for root in roots:
            if root is None:
                continue
            resolved = root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append(resolved)
        return tuple(result)

    def _find_page_dir(self, page_id: str) -> Optional[Path]:
        for root in self._read_roots:
            candidate = self._page_dir_in_root(root, page_id)
            if candidate.is_dir():
                return candidate
        return None

    def _page_dir_in_root(self, root: Path, page_id: str) -> Path:
        page_path = (root / page_id).resolve()
        self._assert_inside_root(page_path, root)
        return page_path

    @staticmethod
    def _assert_inside_root(path: Path, root: Path) -> None:
        try:
            path.relative_to(root)
        except ValueError:
            raise ValueError("invalid page path")
