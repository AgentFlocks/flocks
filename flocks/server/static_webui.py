"""Static WebUI hosting helpers for the FastAPI server."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from fastapi import Request, Response
from fastapi.responses import FileResponse, PlainTextResponse

_INDEX_CACHE_CONTROL = "no-store"
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_STATIC_CACHE_CONTROL = "no-cache"
_FINGERPRINT_RE = re.compile(r"(?:^|[.-])[0-9a-f]{8,}(?:[.-]|$)", re.IGNORECASE)
_STATIC_MEDIA_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".wasm": "application/wasm",
}
_PROTECTED_PREFIXES = (
    "/api",
    "/event",
    "/global",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/health",
)


class WebUIDistMissingError(RuntimeError):
    """Raised when the production WebUI build output is unavailable."""


def source_webui_dist_dir() -> Path:
    """Return the source-tree WebUI dist directory."""
    return Path(__file__).resolve().parents[2] / "webui" / "dist"


def packaged_webui_dist_dir() -> Path:
    """Return the packaged WebUI static directory."""
    return Path(__file__).resolve().parents[1] / "webui_static"


def resolve_webui_dist_dir() -> Path | None:
    """Return the first usable WebUI dist directory."""
    candidates: list[Path] = []
    override = os.getenv("FLOCKS_WEBUI_DIST_DIR")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend([source_webui_dist_dir(), packaged_webui_dist_dir()])
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate.resolve()
    return None


def ensure_webui_dist_dir() -> Path:
    """Return the WebUI dist directory or raise a clear startup error."""
    dist_dir = resolve_webui_dist_dir()
    if dist_dir is None:
        raise WebUIDistMissingError(
            "WebUI build output is missing. Run `cd webui && npm run build`, "
            "or start without `--skip-webui-build` so Flocks can build it."
        )
    return dist_dir


async def maybe_serve_static_webui(request: Request) -> Response | None:
    """Serve SPA static files for browser navigations.

    API and TUI-compatible requests continue through the existing routers.  Only
    real static files and browser HTML navigation requests are handled here.
    """
    if request.method not in {"GET", "HEAD"}:
        return None

    path = request.url.path or "/"
    dist_dir = resolve_webui_dist_dir()
    if dist_dir is None:
        return None

    file_path = _resolve_existing_static_file(dist_dir, path)
    if file_path is not None:
        return _file_response(file_path, cache_control=_cache_control_for_file(path, file_path))

    if path.startswith("/assets/"):
        return PlainTextResponse("Not found", status_code=404)
    if _is_protected_backend_path(path):
        return None
    if not _accepts_html(request):
        return None

    return _file_response(dist_dir / "index.html", cache_control=_INDEX_CACHE_CONTROL)


def _resolve_existing_static_file(dist_dir: Path, path: str) -> Path | None:
    if path == "/":
        return None
    relative = unquote(path.lstrip("/"))
    candidate = (dist_dir / relative).resolve()
    try:
        candidate.relative_to(dist_dir)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def _file_response(path: Path, *, cache_control: str) -> FileResponse:
    headers = {"Cache-Control": cache_control}
    return FileResponse(path, headers=headers, media_type=_STATIC_MEDIA_TYPES.get(path.suffix.lower()))


def _cache_control_for_file(path: str, file_path: Path) -> str:
    if file_path.name == "index.html":
        return _INDEX_CACHE_CONTROL
    if path.startswith("/assets/") or _FINGERPRINT_RE.search(file_path.name):
        return _ASSET_CACHE_CONTROL
    return _STATIC_CACHE_CONTROL


def _is_protected_backend_path(path: str) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in _PROTECTED_PREFIXES)


def _accepts_html(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    if not accept or accept == "*/*":
        return False
    return "text/html" in accept or "application/xhtml+xml" in accept
