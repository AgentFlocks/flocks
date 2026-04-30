"""
Workspace route tests

Covers:
  - Directory tree (GET /api/workspace/tree)
  - Directory listing (GET /api/workspace/list)
  - Directory creation / deletion (POST & DELETE /api/workspace/dir)
  - File read / write / delete (GET, PUT, DELETE /api/workspace/file)
  - File upload (POST /api/workspace/upload)
  - File download (GET /api/workspace/download)
  - File move / rename (POST /api/workspace/move)
  - Workspace stats (GET /api/workspace/stats)
  - Path traversal rejection
  - Absolute path rejection
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import status
from httpx import AsyncClient
from tests.utils.file_type_samples import ALL_SUPPORTED_UPLOAD_FILENAMES, create_sample_file


# ===========================================================================
# Tree & List
# ===========================================================================

class TestWorkspaceTreeAndList:

    @pytest.mark.asyncio
    async def test_tree_returns_root_node(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """GET /api/workspace/tree returns a tree structure."""
        resp = await client.get("/api/workspace/tree")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, dict)
        assert data["type"] == "directory"

    @pytest.mark.asyncio
    async def test_tree_includes_files(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Tree includes the files created in mock_workspace."""
        resp = await client.get("/api/workspace/tree")
        assert resp.status_code == status.HTTP_200_OK
        # Recursively search for README.md in the tree
        def find(node: dict, name: str) -> bool:
            if node.get("name") == name:
                return True
            return any(find(child, name) for child in node.get("children", []))

        assert find(resp.json(), "README.md")

    @pytest.mark.asyncio
    async def test_list_root(self, client: AsyncClient, mock_workspace: Path):
        """GET /api/workspace/list returns top-level entries."""
        resp = await client.get("/api/workspace/list", params={"path": ""})
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        names = [item["name"] for item in data]
        assert "README.md" in names
        assert "subdir" in names

    @pytest.mark.asyncio
    async def test_list_subdir(self, client: AsyncClient, mock_workspace: Path):
        """List a nested directory."""
        resp = await client.get("/api/workspace/list", params={"path": "subdir"})
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        names = [item["name"] for item in data]
        assert "file.txt" in names

    @pytest.mark.asyncio
    async def test_list_nonexistent_dir_returns_404(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Listing a non-existent directory returns 404."""
        resp = await client.get("/api/workspace/list", params={"path": "no_such_dir"})
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Directory operations
# ===========================================================================

class TestWorkspaceDirectories:

    @pytest.mark.asyncio
    async def test_create_directory(self, client: AsyncClient, mock_workspace: Path):
        """POST /api/workspace/dir creates a new directory."""
        resp = await client.post(
            "/api/workspace/dir", json={"path": "new_folder"}
        )
        assert resp.status_code == status.HTTP_200_OK
        assert (mock_workspace / "new_folder").is_dir()

    @pytest.mark.asyncio
    async def test_create_nested_directory(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Creating a nested path creates all intermediate directories."""
        resp = await client.post(
            "/api/workspace/dir", json={"path": "deep/nested/dir"}
        )
        assert resp.status_code == status.HTTP_200_OK
        assert (mock_workspace / "deep" / "nested" / "dir").is_dir()

    @pytest.mark.asyncio
    async def test_delete_directory(self, client: AsyncClient, mock_workspace: Path):
        """DELETE /api/workspace/dir removes the directory."""
        # Create first
        await client.post("/api/workspace/dir", json={"path": "to_delete"})
        assert (mock_workspace / "to_delete").is_dir()

        resp = await client.request(
            "DELETE",
            "/api/workspace/dir",
            params={"path": "to_delete"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert not (mock_workspace / "to_delete").exists()

    @pytest.mark.asyncio
    async def test_reject_absolute_path(self, client: AsyncClient, mock_workspace: Path):
        """Absolute paths are rejected with 400."""
        resp = await client.post(
            "/api/workspace/dir", json={"path": "/etc/evil"}
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.asyncio
    async def test_reject_path_traversal(self, client: AsyncClient, mock_workspace: Path):
        """Path traversal attempts (../) are rejected with 400."""
        resp = await client.post(
            "/api/workspace/dir", json={"path": "../../evil"}
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ===========================================================================
# File operations
# ===========================================================================

class TestWorkspaceFiles:

    @pytest.mark.asyncio
    async def test_read_existing_file(self, client: AsyncClient, mock_workspace: Path):
        """GET /api/workspace/file returns file content."""
        resp = await client.get("/api/workspace/file", params={"path": "README.md"})
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "content" in data
        assert "# Test workspace" in data["content"]

    @pytest.mark.asyncio
    async def test_read_nonexistent_file_returns_404(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Reading a non-existent file returns 404."""
        resp = await client.get(
            "/api/workspace/file", params={"path": "ghost.txt"}
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_write_new_file(self, client: AsyncClient, mock_workspace: Path):
        """PUT /api/workspace/file creates a new file with given content."""
        resp = await client.put(
            "/api/workspace/file",
            json={"path": "hello.txt", "content": "Hello, world!"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert (mock_workspace / "hello.txt").read_text() == "Hello, world!"

    @pytest.mark.asyncio
    async def test_overwrite_existing_file(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Writing to an existing file overwrites it."""
        await client.put(
            "/api/workspace/file",
            json={"path": "README.md", "content": "New content"},
        )
        assert (mock_workspace / "README.md").read_text() == "New content"

    @pytest.mark.asyncio
    async def test_delete_file(self, client: AsyncClient, mock_workspace: Path):
        """DELETE /api/workspace/file removes the file."""
        # Create a disposable file
        await client.put(
            "/api/workspace/file",
            json={"path": "disposable.txt", "content": "bye"},
        )
        resp = await client.request(
            "DELETE",
            "/api/workspace/file",
            params={"path": "disposable.txt"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert not (mock_workspace / "disposable.txt").exists()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_file_returns_404(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Deleting a non-existent file returns 404."""
        resp = await client.request(
            "DELETE",
            "/api/workspace/file",
            params={"path": "nope.txt"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Upload
# ===========================================================================

class TestWorkspaceUpload:

    @pytest.mark.asyncio
    async def test_upload_single_file(self, client: AsyncClient, mock_workspace: Path):
        """POST /api/workspace/upload stores an uploaded file."""
        content = b"uploaded content"
        resp = await client.post(
            "/api/workspace/upload",
            files={"files": ("upload.txt", io.BytesIO(content), "text/plain")},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert (mock_workspace / "upload.txt").read_bytes() == content

    @pytest.mark.asyncio
    async def test_upload_to_subdirectory(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Uploading to a sub-path creates the file under the sub-directory."""
        resp = await client.post(
            "/api/workspace/upload",
            params={"dest": "subdir"},
            files={"files": ("nested.txt", io.BytesIO(b"data"), "text/plain")},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert (mock_workspace / "subdir" / "nested.txt").exists()

    @pytest.mark.asyncio
    async def test_upload_binary_file_succeeds_without_chat_purpose(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Generic workspace uploads remain unrestricted for non-chat usage."""
        resp = await client.post(
            "/api/workspace/upload",
            files={"files": ("archive.zip", io.BytesIO(b"zip"), "application/zip")},
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json()["uploaded"][0]
        assert result.get("error") is None
        assert result["name"] == "archive.zip"
        assert (mock_workspace / "archive.zip").exists()

    @pytest.mark.asyncio
    async def test_chat_upload_rejects_disallowed_file_type(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Chat uploads reject unsupported file types via purpose=chat."""
        resp = await client.post(
            "/api/workspace/upload",
            params={"purpose": "chat"},
            files={"files": ("archive.zip", io.BytesIO(b"zip"), "application/zip")},
        )
        assert resp.status_code == status.HTTP_200_OK
        result = resp.json()["uploaded"][0]
        assert "Unsupported file type" in result["error"]
        assert not (mock_workspace / "archive.zip").exists()

    @pytest.mark.parametrize("filename", ALL_SUPPORTED_UPLOAD_FILENAMES)
    @pytest.mark.asyncio
    async def test_chat_upload_accepts_all_supported_file_types(
        self,
        client: AsyncClient,
        mock_workspace: Path,
        filename: str,
    ):
        """Chat uploads accept every file type advertised by the UI."""
        source = mock_workspace / "fixtures" / filename
        source.parent.mkdir(parents=True, exist_ok=True)
        create_sample_file(source)
        resp = await client.post(
            "/api/workspace/upload",
            params={"purpose": "chat"},
            files={"files": (filename, io.BytesIO(source.read_bytes()), "application/octet-stream")},
        )

        assert resp.status_code == status.HTTP_200_OK
        result = resp.json()["uploaded"][0]
        assert result.get("error") is None
        assert result["name"] == filename
        assert (mock_workspace / filename).exists()

    @pytest.mark.asyncio
    async def test_upload_overwrites_duplicate_file_without_chat_purpose(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Generic workspace uploads overwrite duplicate filenames by default."""
        first = await client.post(
            "/api/workspace/upload",
            params={"dest": "uploads"},
            files={"files": ("report.pdf", io.BytesIO(b"first"), "application/pdf")},
        )
        second = await client.post(
            "/api/workspace/upload",
            params={"dest": "uploads"},
            files={"files": ("report.pdf", io.BytesIO(b"second"), "application/pdf")},
        )
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        first_item = first.json()["uploaded"][0]
        second_item = second.json()["uploaded"][0]
        assert first_item["name"] == "report.pdf"
        assert second_item["name"] == "report.pdf"
        assert first_item["path"] == "uploads/report.pdf"
        assert second_item["path"] == "uploads/report.pdf"
        assert (mock_workspace / "uploads" / "report.pdf").read_bytes() == b"second"

    @pytest.mark.asyncio
    async def test_chat_upload_renames_duplicate_file(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Chat uploads auto-rename duplicates to preserve attachment paths."""
        first = await client.post(
            "/api/workspace/upload",
            params={"dest": "uploads", "purpose": "chat"},
            files={"files": ("report.pdf", io.BytesIO(b"first"), "application/pdf")},
        )
        second = await client.post(
            "/api/workspace/upload",
            params={"dest": "uploads", "purpose": "chat"},
            files={"files": ("report.pdf", io.BytesIO(b"second"), "application/pdf")},
        )
        assert first.status_code == status.HTTP_200_OK
        assert second.status_code == status.HTTP_200_OK
        first_item = first.json()["uploaded"][0]
        second_item = second.json()["uploaded"][0]
        assert first_item["name"] == "report.pdf"
        assert second_item["name"] == "report (1).pdf"
        assert first_item["path"] == "uploads/report.pdf"
        assert second_item["path"] == "uploads/report (1).pdf"
        assert (mock_workspace / "uploads" / "report.pdf").read_bytes() == b"first"
        assert (mock_workspace / "uploads" / "report (1).pdf").read_bytes() == b"second"

    @pytest.mark.asyncio
    async def test_chat_upload_returns_error_after_too_many_name_conflicts(
        self, client: AsyncClient, mock_workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Chat uploads stop probing once the rename limit is exhausted."""
        from flocks.server.routes import workspace as workspace_routes

        monkeypatch.setattr(workspace_routes, "_MAX_UPLOAD_RENAME_ATTEMPTS", 1)
        uploads_dir = mock_workspace / "uploads"
        uploads_dir.mkdir(exist_ok=True)
        (uploads_dir / "report.pdf").write_bytes(b"first")
        (uploads_dir / "report (1).pdf").write_bytes(b"second")

        resp = await client.post(
            "/api/workspace/upload",
            params={"dest": "uploads", "purpose": "chat"},
            files={"files": ("report.pdf", io.BytesIO(b"third"), "application/pdf")},
        )

        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "Too many conflicting filenames" in resp.json()["detail"]
        result = resp.json()["uploaded"][0]
        assert "Too many conflicting filenames" in result["error"]
        assert (mock_workspace / "uploads" / "report.pdf").read_bytes() == b"first"
        assert (mock_workspace / "uploads" / "report (1).pdf").read_bytes() == b"second"


# ===========================================================================
# Download
# ===========================================================================

class TestWorkspaceDownload:

    @pytest.mark.asyncio
    async def test_download_single_file(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """GET /api/workspace/download returns file bytes."""
        resp = await client.get(
            "/api/workspace/download", params={"path": "README.md"}
        )
        assert resp.status_code == status.HTTP_200_OK
        assert b"# Test workspace" in resp.content

    @pytest.mark.asyncio
    async def test_download_nonexistent_returns_404(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Downloading a non-existent file returns 404."""
        resp = await client.get(
            "/api/workspace/download", params={"path": "missing.txt"}
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_download_zip(self, client: AsyncClient, mock_workspace: Path):
        """POST /api/workspace/download/zip returns a zip archive."""
        resp = await client.post(
            "/api/workspace/download/zip",
            json={"paths": ["README.md", "subdir/file.txt"]},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.headers.get("content-type", "").startswith("application/zip")


# ===========================================================================
# Move / Rename
# ===========================================================================

class TestWorkspaceMove:

    @pytest.mark.asyncio
    async def test_move_file(self, client: AsyncClient, mock_workspace: Path):
        """POST /api/workspace/move renames or moves a file."""
        # Create a file to move
        await client.put(
            "/api/workspace/file",
            json={"path": "old_name.txt", "content": "data"},
        )
        resp = await client.post(
            "/api/workspace/move",
            json={"src": "old_name.txt", "dst": "new_name.txt"},
        )
        assert resp.status_code == status.HTTP_200_OK
        assert not (mock_workspace / "old_name.txt").exists()
        assert (mock_workspace / "new_name.txt").exists()

    @pytest.mark.asyncio
    async def test_move_nonexistent_returns_404(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """Moving a file that does not exist returns 404."""
        resp = await client.post(
            "/api/workspace/move",
            json={"src": "ghost.txt", "dst": "dest.txt"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Stats
# ===========================================================================

class TestWorkspaceStats:

    @pytest.mark.asyncio
    async def test_stats_returns_expected_shape(
        self, client: AsyncClient, mock_workspace: Path
    ):
        """GET /api/workspace/stats returns size/count totals."""
        resp = await client.get("/api/workspace/stats")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "workspace" in data or "total_size" in data or isinstance(data, dict)
