"""
File operation routes

Routes for file reading, searching, and listing
"""

import asyncio
import mimetypes
import os
from pathlib import Path
from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

from flocks.config.config import Config
from flocks.utils.file import File, FileNode, FileContent, FileInfo
from flocks.utils.http_file_read_guard import (
    assert_http_workspace_path_access,
    resolve_path_for_http_file_access,
)
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.file")

# ``/api/file/content`` returns the whole file in one JSON body (base64 for
# binary, +33%), so it is capped; larger files go through ``/download``.
_CONTENT_MAX_BYTES = int(os.environ.get("FLOCKS_HTTP_FILE_CONTENT_MAX_BYTES", str(50 * 1024 * 1024)))


def _workspace_hit_checker():
    """Return a per-request readability check for file and search hits.

    The workspace root is resolved once per request and each path is decided
    once (``find`` reports many lines of the same file). A hit that cannot be
    resolved -- a symlink loop, an entry removed meanwhile -- is skipped
    instead of failing the whole listing.
    """
    from flocks.workspace.manager import WorkspaceManager

    manager = WorkspaceManager.get_instance()
    workspace = manager.get_workspace_dir().resolve()
    decided: Dict[str, bool] = {}

    def readable(path: str) -> bool:
        if path not in decided:
            try:
                assert_http_workspace_path_access(path, manager=manager, workspace=workspace)
                decided[path] = True
            except (OSError, RuntimeError, ValueError):  # PermissionError is an OSError
                decided[path] = False
        return decided[path]

    return readable


@router.get("/list", response_model=List[FileNode], summary="List files")
async def list_files(path: str = Query(..., description="Directory path")):
    """
    List files
    
    List files and directories in a specified path.
    """
    try:
        cfg = await Config.get()
        safe_path = await resolve_path_for_http_file_access(path, cfg)
        nodes = await File.list(safe_path)
        readable = _workspace_hit_checker()
        # One policy check per entry: keep large directories off the event loop.
        return await asyncio.to_thread(lambda: [node for node in nodes if readable(node.absolute)])
    except HTTPException:
        raise
    except PermissionError:
        log.warning("http_file.list.denied", {"path": path})
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        # Internal error text (paths, driver messages) stays in the log.
        log.error("file.list.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/content", response_model=FileContent, summary="Read file")
async def read_file(path: str = Query(..., description="File path")):
    """
    Read file
    
    Read the content of a specified file.
    """
    try:
        cfg = await Config.get()
        safe_path = await resolve_path_for_http_file_access(path, cfg)
        try:
            size = os.stat(safe_path).st_size
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"File not found: {path}")
        if size > _CONTENT_MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File too large for inline read ({size} bytes > {_CONTENT_MAX_BYTES}); use /api/file/download",
            )
        content = await File.read(safe_path)
        return content
    except HTTPException:
        raise
    except FileNotFoundError:
        # Echo the path as the caller wrote it, not the resolved server path.
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except IsADirectoryError:
        raise HTTPException(status_code=400, detail=f"Not a file: {path}")
    except ValueError as e:
        if "Not a file" in str(e):
            raise HTTPException(status_code=400, detail=f"Not a file: {path}")
        log.error("file.read.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail="Internal server error")
    except PermissionError:
        log.warning("http_file.read.denied", {"path": path})
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        log.error("file.read.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/download", summary="Download file")
async def download_file(path: str = Query(..., description="File path")):
    try:
        # Channel media another instance received is fetched from the
        # database first, under this instance's data directory whatever path
        # the other one wrote into the URL; the access check below still decides.
        from flocks.session.files import local_channel_media_path, restore_channel_media

        media = local_channel_media_path(Path(path).expanduser())
        if media is not None:
            path = str(media)
            try:
                await restore_channel_media([media])
            except Exception as exc:
                log.warning("http_file.download.restore_failed", {"error_type": type(exc).__name__})
        cfg = await Config.get()
        safe_path = await resolve_path_for_http_file_access(path, cfg)
        target = Path(safe_path)
        if not target.is_file():
            raise HTTPException(status_code=400, detail=f"Not a file: {path}")
        return FileResponse(
            path=str(target),
            filename=target.name,
            media_type=mimetypes.guess_type(str(target))[0] or "application/octet-stream",
        )
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    except PermissionError:
        log.warning("http_file.download.denied", {"path": path})
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        log.error("file.download.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/search", response_model=List[str], summary="Search files")
async def search_files(
    query: str = Query(..., description="Search query"),
    limit: int = Query(10, ge=1, le=200, description="Maximum results"),
    dirs: bool = Query(True, description="Include directories"),
    type: Optional[str] = Query(None, description="Filter by type (file or directory)"),
):
    """
    Search files
    
    Search for files or directories by name or pattern in the project directory.
    """
    # Only enforce basic resource limits here.  The underlying ``File.search``
    # invokes ``subprocess.run`` with ``shell=False`` and an argv list, so the
    # query cannot reach a shell — there is no injection vector that warrants
    # rejecting legitimate filenames containing ``;`` ``|`` ``$`` ``` ``` etc.
    # The null byte is still refused because many POSIX APIs treat it as a
    # string terminator and will silently truncate the argument.
    if not query or len(query) > 200 or "\x00" in query:
        raise HTTPException(status_code=400, detail="Invalid search query")
    try:
        results = await File.search(query=query, limit=limit, dirs=dirs, type=type)
        readable = _workspace_hit_checker()
        return await asyncio.to_thread(
            lambda: [path for path in results if readable(os.path.abspath(path))]
        )
    except Exception as e:
        log.error("file.search.error", {"error": str(e), "query": query})
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/status", response_model=List[FileInfo], summary="Get file status")
async def get_file_status():
    """
    Get file status
    
    Get the git status of all files in the project.
    """
    try:
        status = await File.status()
        readable = _workspace_hit_checker()
        return await asyncio.to_thread(
            lambda: [item for item in status if readable(os.path.abspath(item.path))]
        )
    except Exception as e:
        log.error("file.status.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail="Internal server error")


# Additional route for text search (ripgrep-like functionality)
class TextSearchRequest(BaseModel):
    """Text search request"""
    pattern: str
    limit: int = 10


class TextSearchMatch(BaseModel):
    """Text search match"""
    file: str
    line: int
    column: int
    text: str


@router.get("/find/text", response_model=List[Dict], summary="Find text")
async def find_text(pattern: str = Query(..., description="Search pattern")):
    """
    Find text
    
    Search for text patterns across files in the project using grep.
    Only searches within the Flocks project root directory.
    """
    try:
        import subprocess
        import os
        from flocks.utils.paths import find_flocks_project_root

        project_root = find_flocks_project_root()
        if project_root is None:
            raise HTTPException(status_code=403, detail="No Flocks project root found")
        cwd = str(project_root)

        if not pattern or len(pattern) > 500 or "\x00" in pattern:
            raise HTTPException(status_code=400, detail="Invalid search pattern")

        # ``grep`` runs as its own argv (no shell); the ``--`` sentinel stops
        # ``grep`` from interpreting a leading ``-`` in ``pattern`` as an
        # option.  We intentionally keep regex semantics (the historical
        # contract) and rely on ``subprocess.run(shell=False)`` plus the
        # bounded cwd (``project_root``) to contain the command.
        cmd = [
            "grep",
            "-rn",  # recursive, line numbers
            "--include=*.py",
            "--include=*.js",
            "--include=*.ts",
            "--include=*.tsx",
            "--include=*.jsx",
            "--include=*.java",
            "--include=*.go",
            "--include=*.rs",
            "--include=*.c",
            "--include=*.cpp",
            "--include=*.h",
            "--include=*.txt",
            "--include=*.md",
            "--exclude-dir=.git",
            "--exclude-dir=node_modules",
            "--exclude-dir=__pycache__",
            "--exclude-dir=.venv",
            "--exclude-dir=venv",
            "--",
            pattern,
            ".",
        ]
        
        try:
            # Off the event loop: grep over a project tree can run for seconds.
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired:
            # Same contract as ``/find``: a search that does not finish in
            # time is an empty result, not a server error.
            log.warning("text.search.timeout", {"pattern": pattern, "cwd": cwd})
            return []
        
        matches = []

        if result.returncode == 0:
            readable = _workspace_hit_checker()
            for line in result.stdout.split("\n")[:100]:
                if not line.strip():
                    continue

                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path = parts[0].removeprefix("./")
                    if not readable(str(project_root / file_path)):
                        continue
                    line_num = parts[1]
                    text = parts[2]
                    
                    matches.append({
                        "file": file_path,
                        "line": int(line_num) if line_num.isdigit() else 0,
                        "text": text,
                    })
        
        return matches
    except HTTPException:
        raise
    except Exception as e:
        log.error("text.search.error", {"error": str(e), "pattern": pattern})
        raise HTTPException(status_code=500, detail="Internal server error")
