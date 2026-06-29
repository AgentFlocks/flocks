"""Build WebUI page TSX sources into browser-loadable ESM bundles."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from flocks.contracts.webui.models import WebUIPageBuildMeta
from flocks.contracts.webui.store import WEBUI_CONTRACT_SDK_IMPORT, WebUIPagesStore
from flocks.utils.log import Log

log = Log.create(service="webui-pages-builder")

MAX_OUTPUT_BYTES = 2_000_000
BUILD_TIMEOUT_SECONDS = 30
_SHIMS_DIR = Path(__file__).resolve().parent / "shims"
RUNTIME_NAME = "webui_page"
RUNTIME_VERSION = 1
SDK_IMPORT_NAME = WEBUI_CONTRACT_SDK_IMPORT


def _repo_root() -> Path:
    # flocks/contracts/webui/builder.py -> repo root is parents[3].
    return Path(__file__).resolve().parents[3]


def resolve_esbuild_bin() -> Optional[Path]:
    """Locate esbuild from the bundled webui toolchain."""
    webui_bin = _repo_root() / "webui" / "node_modules" / ".bin"
    if sys.platform == "win32":
        candidate = webui_bin / "esbuild.cmd"
        if candidate.is_file():
            return candidate
    candidate = webui_bin / "esbuild"
    if candidate.is_file():
        return candidate
    return None


class WebUIPageBuilder:
    """Compile a page entry file into dist/page.js."""

    def __init__(self, store: Optional[WebUIPagesStore] = None) -> None:
        self._store = store or WebUIPagesStore()

    def build(self, page_id: str) -> WebUIPageBuildMeta:
        page_id = self._store.validate_page_id(page_id)
        detail = self._store.get_page(page_id)
        page_dir = self._store.page_dir(page_id)
        entry = detail.manifest.entry.replace("\\", "/")
        entry_path = (page_dir / entry).resolve()
        try:
            entry_path.relative_to(page_dir.resolve())
        except ValueError:
            raise ValueError("invalid entry path")
        if not entry_path.is_file():
            raise FileNotFoundError(f"entry file not found: {entry}")

        esbuild = resolve_esbuild_bin()
        if esbuild is None:
            raise RuntimeError("esbuild is not available; install webui dependencies first")

        dist_dir = page_dir / "dist"
        dist_dir.mkdir(parents=True, exist_ok=True)
        outfile = dist_dir / "page.js"

        building = WebUIPageBuildMeta(status="building", hash="", builtAt=0, error=None)
        self._store.write_build_meta(page_id, building)

        cmd = [
            str(esbuild),
            str(entry_path),
            "--bundle",
            "--format=esm",
            f"--outfile={outfile}",
            "--platform=browser",
            "--target=es2020",
            "--jsx=automatic",
            f"--alias:react={_SHIMS_DIR / 'react.js'}",
            f"--alias:react/jsx-runtime={_SHIMS_DIR / 'jsx-runtime.js'}",
            f"--alias:{WEBUI_CONTRACT_SDK_IMPORT}={_SHIMS_DIR / 'sdk.js'}",
        ]

        env = os.environ.copy()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(page_dir),
                capture_output=True,
                text=True,
                timeout=BUILD_TIMEOUT_SECONDS,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            meta = WebUIPageBuildMeta(
                status="failed",
                hash="",
                builtAt=int(time.time() * 1000),
                error=f"build timed out after {BUILD_TIMEOUT_SECONDS}s",
                runtime=RUNTIME_NAME,
                runtimeVersion=RUNTIME_VERSION,
                sdkImport=SDK_IMPORT_NAME,
            )
            self._store.write_build_meta(page_id, meta)
            raise RuntimeError(meta.error) from exc

        if result.returncode != 0:
            stderr = (result.stderr or result.stdout or "esbuild failed").strip()
            meta = WebUIPageBuildMeta(
                status="failed",
                hash="",
                builtAt=int(time.time() * 1000),
                error=stderr[:4000],
                runtime=RUNTIME_NAME,
                runtimeVersion=RUNTIME_VERSION,
                sdkImport=SDK_IMPORT_NAME,
            )
            self._store.write_build_meta(page_id, meta)
            log.warning("webui_pages.build.failed", {"pageId": page_id, "error": stderr[:500]})
            return meta

        if not outfile.is_file():
            meta = WebUIPageBuildMeta(
                status="failed",
                hash="",
                builtAt=int(time.time() * 1000),
                error="build produced no output",
                runtime=RUNTIME_NAME,
                runtimeVersion=RUNTIME_VERSION,
                sdkImport=SDK_IMPORT_NAME,
            )
            self._store.write_build_meta(page_id, meta)
            return meta

        content = outfile.read_bytes()
        if len(content) > MAX_OUTPUT_BYTES:
            outfile.unlink(missing_ok=True)
            meta = WebUIPageBuildMeta(
                status="failed",
                hash="",
                builtAt=int(time.time() * 1000),
                error="build output is too large",
                runtime=RUNTIME_NAME,
                runtimeVersion=RUNTIME_VERSION,
                sdkImport=SDK_IMPORT_NAME,
            )
            self._store.write_build_meta(page_id, meta)
            return meta

        digest = hashlib.sha256(content).hexdigest()[:16]
        meta = WebUIPageBuildMeta(
            status="ready",
            hash=digest,
            builtAt=int(time.time() * 1000),
            error=None,
            runtime=RUNTIME_NAME,
            runtimeVersion=RUNTIME_VERSION,
            sdkImport=SDK_IMPORT_NAME,
        )
        self._store.write_build_meta(page_id, meta)
        log.info("webui_pages.build.ready", {"pageId": page_id, "hash": digest})
        return meta
