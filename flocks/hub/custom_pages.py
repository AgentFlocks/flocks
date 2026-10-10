"""Carry the pages admins added to an installed WebUI package across updates.

The SOC workspace's 「+ 自定义页面」 session writes new pages into the
installed ``soc_ui`` directory and lists them in its ``workspace.json``. An
update swaps that directory for the official release, so the installer copies
those pages, and their places in the workspace sections, into the staged
release before the swap. Official pages are always replaced; the update
backup keeps any edits made to them.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from pydantic import ValidationError

from flocks.contracts.webui.models import WebUIWorkspaceManifest
from flocks.contracts.webui.store import WORKSPACE_MANIFEST_FILE, WebUIPagesStore
from flocks.hub.security import SKIP_NAMES
from flocks.utils.log import Log

log = Log.create(service="hub.custom_pages")

PAGE_MANIFEST = "manifest.json"
# Build output and installed dependencies never hold a page of their own.
_NON_PAGE_DIRS = SKIP_NAMES | {"dist", "node_modules", ".pytest_cache"}


def page_dirs(root: Path) -> dict[str, str]:
    """Map every page directory under *root* (relative POSIX path) to its page id.

    Like the page store, this skips dot-prefixed and symlinked directories and
    does not count the package's own ``manifest.json``; build output and
    installed dependencies are skipped as well.
    """
    pages: dict[str, str] = {}
    if root.is_symlink() or not root.is_dir():
        return pages
    for directory, names, files in os.walk(root, followlinks=False):
        names[:] = sorted(n for n in names if not n.startswith(".") and n not in _NON_PAGE_DIRS)
        current = Path(directory)
        if current == root or PAGE_MANIFEST not in files:
            continue
        page_id = _page_id(current / PAGE_MANIFEST)
        if page_id is not None:
            pages[current.relative_to(root).as_posix()] = page_id
    return pages


def classify_pages(
    installed: Path, official: Path, baseline: Optional[Mapping[str, str]] = None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split the pages admins added into those an update keeps and those it replaces.

    A page directory belongs to the release when the official package ships
    it, or shipped it at the last install: ``baseline`` holds that install's
    payload hashes keyed ``package/<path>``. Every other page, nested ones
    included, was added afterwards. It is kept unless the release now ships a
    page with the same id, something at its path, or the page directory it
    sits in.
    """
    installed_pages = page_dirs(installed)
    official_pages = page_dirs(official)
    official_ids = set(official_pages.values())
    shipped = set(official_pages) | _baseline_page_dirs(baseline)
    keep: list[dict[str, str]] = []
    replace: list[dict[str, str]] = []
    for path, page_id in sorted(installed_pages.items(), key=lambda item: (item[0].count("/"), item[0])):
        if path in shipped:
            continue
        entry = {"id": page_id, "title": _page_title(installed / path / PAGE_MANIFEST, page_id), "path": path}
        inside_shipped = any(parent.as_posix() in shipped for parent in list(PurePosixPath(path).parents)[:-1])
        if inside_shipped or page_id in official_ids or _clashes(official, path):
            replace.append(entry)
        else:
            keep.append(entry)
    return keep, replace


def carry_over(installed: Path, staged: Path, pages: Iterable[Mapping[str, Any]]) -> None:
    """Copy kept pages from the installed package into the staged release.

    A kept page travels with the nearest page around it when that one is kept
    too. Pages the release replaces are left out of every copy, so a kept page
    below one of them is copied on its own, into fresh parent directories.
    """
    kept = list(pages)
    if not kept:
        return
    kept_paths = {_safe_relative(str(page["path"])) for page in kept}
    page_paths = {Path(path) for path in page_dirs(installed)}
    left_out = {installed / path for path in page_paths - kept_paths}
    for relative in sorted(kept_paths, key=lambda path: len(path.parts)):
        enclosing = next((parent for parent in relative.parents if parent in page_paths), None)
        if enclosing in kept_paths:
            continue
        source = installed / relative
        target = staged / relative
        if source.is_symlink() or not source.is_dir():
            raise RuntimeError(f"Custom page directory is missing: {relative.as_posix()}; retry the update")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(
            source,
            target,
            symlinks=True,
            ignore=lambda directory, names: [
                name for name in names if name in SKIP_NAMES or Path(directory) / name in left_out
            ],
        )
    for relative in kept_paths:
        _mark_stale_build(staged / relative)
    _merge_workspace_file(installed, staged, [str(page["id"]) for page in kept])


def merge_workspace(official: dict[str, Any], installed: Any, kept_ids: Iterable[str]) -> dict[str, Any]:
    """Return the official workspace manifest with the kept pages back in their sections.

    Sections are read the way the page store reads them: ids trimmed and
    lowercased, and a section's default page counts as its first page even
    when ``pageIds`` leaves it out. A kept page joins the section that listed
    it before the update, after the page it followed there. A section only the
    installed manifest has comes along with just the kept pages; the official
    sections and their own pages stay as released.
    """
    kept = {page_id for page_id in map(_normalize_id, kept_ids) if page_id}
    if not kept or not isinstance(installed, dict):
        return official
    workspace_default = _normalize_id(installed.get("defaultPageId"))
    official_sections = official.get("sections")
    if not isinstance(official_sections, list) or not official_sections:
        # Without sections the workspace lists every page it contains.
        return {**official, "defaultPageId": workspace_default} if workspace_default in kept else official
    installed_sections = installed.get("sections")
    if not isinstance(installed_sections, list):
        return official

    merged = [dict(section) if isinstance(section, dict) else section for section in official_sections]
    placed: set[str] = set()
    anchor: Optional[dict[str, Any]] = None
    for installed_section in installed_sections:
        if not isinstance(installed_section, dict):
            continue
        raw_ids = installed_section.get("pageIds")
        listed = [page_id for page_id in map(_normalize_id, raw_ids if isinstance(raw_ids, list) else []) if page_id]
        default_page_id = _normalize_id(installed_section.get("defaultPageId"))
        if default_page_id and default_page_id not in listed:
            listed.insert(0, default_page_id)
        own = [page_id for page_id in dict.fromkeys(listed) if page_id in kept and page_id not in placed]
        section_id = _normalize_id(installed_section.get("id"))
        target = next(
            (s for s in merged if isinstance(s, dict) and section_id and _normalize_id(s.get("id")) == section_id),
            None,
        )
        if target is not None:
            page_ids = list(target.get("pageIds") or [])
            for page_id in own:
                present = [_normalize_id(existing) for existing in page_ids]
                before = listed[: listed.index(page_id)]
                position = next((present.index(p) + 1 for p in reversed(before) if p in present), 0)
                page_ids.insert(position, page_id)
            target["pageIds"] = page_ids
            if default_page_id in own:
                target["defaultPageId"] = default_page_id
            placed.update(own)
            anchor = target
            continue
        if not own:
            continue
        section = {
            **installed_section,
            "id": section_id,
            "pageIds": own,
            "defaultPageId": default_page_id if default_page_id in own else None,
        }
        if not _valid_section(section):
            continue
        position = next(i + 1 for i, existing in enumerate(merged) if existing is anchor) if anchor is not None else 0
        merged.insert(position, section)
        placed.update(own)
        anchor = section

    result = {**official, "sections": merged}
    if workspace_default in placed:
        result["defaultPageId"] = workspace_default
    try:
        WebUIWorkspaceManifest.model_validate(result)
    except ValidationError as exc:
        log.warning("hub.custom_pages.workspace_merge_invalid", {"error": str(exc)})
        return official
    return result


def _merge_workspace_file(installed: Path, staged: Path, kept_ids: list[str]) -> None:
    official_path = staged / WORKSPACE_MANIFEST_FILE
    installed_path = installed / WORKSPACE_MANIFEST_FILE
    if not official_path.is_file() or installed_path.is_symlink() or not installed_path.is_file():
        return
    official = json.loads(official_path.read_text(encoding="utf-8"))
    try:
        current = json.loads(installed_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        # The kept pages still arrive; they only miss their navigation entries.
        log.warning("hub.custom_pages.workspace_unreadable", {"path": str(installed_path), "error": str(exc)})
        return
    merged = merge_workspace(official, current, kept_ids)
    if merged != official:
        official_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _baseline_page_dirs(baseline: Optional[Mapping[str, str]]) -> set[str]:
    prefix, suffix = "package/", "/" + PAGE_MANIFEST
    return {
        key[len(prefix):-len(suffix)]
        for key in baseline or {}
        if key.startswith(prefix) and key.endswith(suffix) and len(key) > len(prefix) + len(suffix)
    }


def _mark_stale_build(root: Path) -> None:
    """Have the page store rebuild bundles copied while a rebuild was writing them.

    A ready build records the hash of its ``page.js``; any other mix means the
    copy caught a build half way.
    """
    for page in [root, *(root / path for path in page_dirs(root))]:
        meta_path = page / "dist" / "meta.json"
        # Copied symlinks keep pointing outside the staged release; leave them be.
        if (page / "dist").is_symlink() or meta_path.is_symlink():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            bundle = (page / "dist" / "page.js").read_bytes()
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("status") == "ready" and meta.get("hash") != hashlib.sha256(bundle).hexdigest()[:16]:
            meta_path.write_text(json.dumps({**meta, "status": "idle"}, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_id(value: Any) -> Optional[str]:
    return (value.strip().lower() or None) if isinstance(value, str) else None


def _page_id(manifest: Path) -> Optional[str]:
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        return WebUIPagesStore.validate_page_id(str(raw.get("id", "")))
    except Exception:
        return None


def _page_title(manifest: Path, fallback: str) -> str:
    try:
        title = json.loads(manifest.read_text(encoding="utf-8")).get("title")
    except Exception:
        return fallback
    return title.strip() if isinstance(title, str) and title.strip() else fallback


def _clashes(official: Path, path: str) -> bool:
    """True when the release has a file at *path* or a non-directory above it."""
    target = official / path
    if target.exists() or target.is_symlink():
        return True
    for parent in list(PurePosixPath(path).parents)[:-1]:
        node = official / parent.as_posix()
        if node.is_symlink() or (node.exists() and not node.is_dir()):
            return True
    return False


def _safe_relative(path: str) -> Path:
    relative = PurePosixPath(path)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError(f"Invalid custom page path: {path}")
    return Path(*relative.parts)


def _valid_section(section: dict[str, Any]) -> bool:
    try:
        WebUIPagesStore.validate_workspace_id(str(section.get("id", "")))
        WebUIWorkspaceManifest.model_validate({"id": "check", "title": "check", "sections": [section]})
    except (ValueError, ValidationError):
        return False
    return True
