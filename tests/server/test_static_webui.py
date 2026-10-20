import mimetypes
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.server.static_webui import maybe_serve_static_webui


def _write_dist(root: Path) -> Path:
    dist = root / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>Flocks WebUI</body></html>", encoding="utf-8")
    (assets / "app.12345678.js").write_text("console.log('flocks');", encoding="utf-8")
    return dist


def _app() -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def static_webui(request, call_next):
        response = await maybe_serve_static_webui(request)
        if response is not None:
            return response
        return await call_next(request)

    @app.get("/api/health")
    async def health():
        return {"status": "healthy"}

    return app


@pytest.mark.asyncio
async def test_static_webui_serves_browser_root(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert "Flocks WebUI" in response.text
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.asyncio
async def test_static_webui_serves_assets_with_immutable_cache(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    monkeypatch.setattr(mimetypes, "guess_type", lambda *_args, **_kwargs: ("text/plain", None))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/assets/app.12345678.js")

    assert response.status_code == 200
    assert response.headers["Content-Type"].startswith("application/javascript")
    assert "console.log" in response.text
    assert response.headers["Cache-Control"] == "public, max-age=31536000, immutable"


@pytest.mark.asyncio
async def test_static_webui_falls_back_for_browser_deep_link(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/sessions/ses_abc", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert "Flocks WebUI" in response.text


@pytest.mark.asyncio
async def test_static_webui_falls_back_before_full_app_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    from flocks.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/sessions/ses_abc",
            headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0",
            },
        )

    assert response.status_code == 200
    assert "Flocks WebUI" in response.text


@pytest.mark.asyncio
async def test_static_webui_does_not_bypass_full_app_api_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    from flocks.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/session/abc",
            headers={
                "Accept": "text/html",
                "User-Agent": "Mozilla/5.0",
            },
        )

    assert response.status_code == 401
    assert "Flocks WebUI" not in response.text


@pytest.mark.asyncio
async def test_static_webui_does_not_intercept_api(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.get("/api/health", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_static_webui_does_not_fallback_for_non_get(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    async with AsyncClient(transport=ASGITransport(app=_app()), base_url="http://test") as client:
        response = await client.post("/session/abc", headers={"Accept": "text/html"})

    assert response.status_code == 404


# --- a deployment that pins its UI directory -------------------------------

def test_pinned_ui_directory_is_served_and_nothing_else(monkeypatch, tmp_path: Path) -> None:
    from flocks.server.static_webui import enforce_pinned_webui_dist, resolve_webui_dist_dir

    pinned = _write_dist(tmp_path / "pinned")
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(pinned))
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_STRICT", "1")

    assert resolve_webui_dist_dir() == pinned.resolve()
    enforce_pinned_webui_dist()  # present: no error


def test_pinned_ui_directory_missing_is_a_failed_start(monkeypatch, tmp_path: Path) -> None:
    from flocks.server import static_webui

    # Another build is available; a pinned deployment must not fall back to it.
    fallback = _write_dist(tmp_path / "fallback")
    monkeypatch.setattr(static_webui, "source_webui_dist_dir", lambda: fallback)
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(tmp_path / "missing"))
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_STRICT", "true")

    assert static_webui.resolve_webui_dist_dir() is None
    with pytest.raises(static_webui.WebUIDistMissingError, match="FLOCKS_WEBUI_DIST_STRICT"):
        static_webui.enforce_pinned_webui_dist()


def test_unpinned_resolution_still_falls_back(monkeypatch, tmp_path: Path) -> None:
    from flocks.server import static_webui

    fallback = _write_dist(tmp_path / "fallback")
    monkeypatch.setattr(static_webui, "source_webui_dist_dir", lambda: fallback)
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(tmp_path / "missing"))
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_STRICT", raising=False)

    assert static_webui.resolve_webui_dist_dir() == fallback.resolve()
    static_webui.enforce_pinned_webui_dist()  # nothing pinned: never raises


@pytest.mark.parametrize("declared", ["env", "descriptor"])
def test_a_managed_install_pins_its_ui_directory_without_being_told(monkeypatch, tmp_path: Path, declared) -> None:
    """G06: the supervisor checked one directory; no other build may stand in for it."""
    import json

    from flocks.server import static_webui

    fallback = _write_dist(tmp_path / "fallback")
    monkeypatch.setattr(static_webui, "source_webui_dist_dir", lambda: fallback)
    monkeypatch.setattr(static_webui, "packaged_webui_dist_dir", lambda: fallback)
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_STRICT", raising=False)
    root = tmp_path / "root"
    monkeypatch.setenv("FLOCKS_ROOT", str(root))
    if declared == "env":
        monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "managed")
    else:
        monkeypatch.delenv("FLOCKS_DEPLOY_MODE", raising=False)
        (root / "config").mkdir(parents=True)
        (root / "config" / "deployment.json").write_text(json.dumps({"mode": "managed"}), encoding="utf-8")
    static_webui._declared_managed.cache_clear()

    # No directory named, or one without its entry page: a failed start.
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_DIR", raising=False)
    assert static_webui.resolve_webui_dist_dir() is None
    with pytest.raises(static_webui.WebUIDistMissingError, match="managed installation"):
        static_webui.enforce_pinned_webui_dist()
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(empty))
    with pytest.raises(static_webui.WebUIDistMissingError):
        static_webui.enforce_pinned_webui_dist()

    pinned = _write_dist(tmp_path / "pinned")
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(pinned))
    assert static_webui.resolve_webui_dist_dir() == pinned.resolve()
    static_webui.enforce_pinned_webui_dist()
    static_webui._declared_managed.cache_clear()


def test_a_source_install_keeps_its_search_order(monkeypatch, tmp_path: Path) -> None:
    from flocks.server import static_webui

    fallback = _write_dist(tmp_path / "fallback")
    monkeypatch.setattr(static_webui, "source_webui_dist_dir", lambda: fallback)
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_STRICT", raising=False)
    monkeypatch.setenv("FLOCKS_DEPLOY_MODE", "source")
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path / "root"))
    monkeypatch.delenv("FLOCKS_WEBUI_DIST_DIR", raising=False)
    static_webui._declared_managed.cache_clear()

    assert static_webui.resolve_webui_dist_dir() == fallback.resolve()
    static_webui.enforce_pinned_webui_dist()
    static_webui._declared_managed.cache_clear()
