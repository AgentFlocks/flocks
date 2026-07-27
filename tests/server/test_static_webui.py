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
        response = await client.get("/session/abc", headers={"Accept": "text/html"})

    assert response.status_code == 200
    assert "Flocks WebUI" in response.text


@pytest.mark.asyncio
async def test_static_webui_falls_back_before_full_app_auth(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FLOCKS_WEBUI_DIST_DIR", str(_write_dist(tmp_path)))
    from flocks.server.app import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/session/abc",
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
