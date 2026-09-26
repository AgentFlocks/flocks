from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from flocks.server.auth import require_user
from flocks.server.routes.knowledgebase import create_router, file_content_response


class _Files:
    def __init__(self, body: bytes, media: str):
        self.body = body
        self.media = media

    async def content(self, file_id: str):
        assert file_id == "file-1"
        return self.body, self.media


def test_html_download_is_not_served_as_a_page():
    response = file_content_response(b"<script>alert(1)</script>", "text/html", inline=False)
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_inline_html_stays_a_download():
    response = file_content_response(b"<html></html>", "text/html; charset=utf-8", inline=True)
    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"] == "attachment"


def test_inline_preview_keeps_workspace_media_types():
    pdf = file_content_response(b"%PDF", "application/pdf", inline=True)
    assert pdf.media_type == "application/pdf"
    assert pdf.headers["content-disposition"] == "inline"
    svg = file_content_response(b"<svg></svg>", "image/svg+xml", inline=True)
    assert svg.media_type == "image/svg+xml"
    assert "script-src 'none'" in svg.headers["content-security-policy"]


async def test_content_route_uses_the_safe_download_response(monkeypatch):
    monkeypatch.setattr(
        "flocks.server.routes.knowledgebase.get_client",
        lambda: _Files(b"<script>alert(1)</script>", "text/html"),
    )
    app = FastAPI()
    app.include_router(create_router(), prefix="/api")
    app.dependency_overrides[require_user] = lambda: object()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        download = await client.get("/api/knowledgebase/files/file-1/content")
        preview = await client.get("/api/knowledgebase/files/file-1/content", params={"inline": "1"})
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/octet-stream")
    assert download.headers["content-disposition"] == "attachment"
    assert preview.headers["content-disposition"] == "attachment"
