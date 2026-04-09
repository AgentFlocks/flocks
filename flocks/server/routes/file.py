"""
File operation routes

Routes for file reading, searching, and listing
"""

from typing import List, Optional, Dict
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from flocks.config.config import Config
from flocks.utils.file import File, FileNode, FileContent, FileInfo
from flocks.utils.http_file_read_guard import resolve_path_for_http_file_access
from flocks.utils.log import Log

router = APIRouter()
log = Log.create(service="routes.file")


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
        return nodes
    except PermissionError:
        log.warning("http_file.list.denied")
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        log.error("file.list.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/content", response_model=FileContent, summary="Read file")
async def read_file(path: str = Query(..., description="File path")):
    """
    Read file
    
    Read the content of a specified file.
    """
    try:
        cfg = await Config.get()
        safe_path = await resolve_path_for_http_file_access(path, cfg)
        content = await File.read(safe_path)
        return content
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError:
        log.warning("http_file.read.denied")
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        log.error("file.read.error", {"error": str(e), "path": path})
        raise HTTPException(status_code=500, detail=str(e))


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
    try:
        results = await File.search(query=query, limit=limit, dirs=dirs, type=type)
        return results
    except Exception as e:
        log.error("file.search.error", {"error": str(e), "query": query})
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=List[FileInfo], summary="Get file status")
async def get_file_status():
    """
    Get file status
    
    Get the git status of all files in the project.
    """
    try:
        status = await File.status()
        return status
    except Exception as e:
        log.error("file.status.error", {"error": str(e)})
        raise HTTPException(status_code=500, detail=str(e))


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
    """
    try:
        import subprocess
        import os
        
        cwd = os.getcwd()
        
        # Use grep for text search
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
            pattern,
            ".",
        ]
        
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        matches = []
        
        if result.returncode == 0:
            for line in result.stdout.split("\n")[:100]:  # Limit to 100 matches
                if not line.strip():
                    continue
                
                parts = line.split(":", 2)
                if len(parts) >= 3:
                    file_path = parts[0].lstrip("./")
                    line_num = parts[1]
                    text = parts[2]
                    
                    matches.append({
                        "file": file_path,
                        "line": int(line_num) if line_num.isdigit() else 0,
                        "text": text,
                    })
        
        return matches
    except Exception as e:
        log.error("text.search.error", {"error": str(e), "pattern": pattern})
        raise HTTPException(status_code=500, detail=str(e))
