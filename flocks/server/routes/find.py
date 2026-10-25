"""
Find routes for Flocks TUI compatibility

Provides /find/* endpoints that Flocks SDK expects.
"""

import asyncio
import os
import subprocess
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from flocks.config.config import Config
from flocks.utils.http_file_read_guard import assert_http_workspace_path_access, resolve_path_for_http_file_access
from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="find-routes")


class FindResult(BaseModel):
    """Search result item"""
    file: str
    line: Optional[int] = None
    column: Optional[int] = None
    content: Optional[str] = None


async def _resolve_search_directory(directory: Optional[str]) -> str:
    """Resolve a requested search root to an allowed readable directory."""
    cfg = await Config.get()
    requested = directory or os.getcwd()
    try:
        cwd = await resolve_path_for_http_file_access(requested, cfg)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc

    if not os.path.isdir(cwd):
        raise HTTPException(status_code=400, detail="Search directory must be a directory")
    return cwd


def _validate_search_input(value: str, *, label: str, max_length: int = 500) -> None:
    if not value or len(value) > max_length or "\x00" in value:
        raise HTTPException(status_code=400, detail=f"Invalid {label}")


def _readable_search_hits(cwd: str, results: list) -> list:
    """A readable search root does not authorize every descendant or alias."""
    from flocks.workspace.manager import WorkspaceManager

    root = Path(cwd).resolve()
    manager = WorkspaceManager.get_instance()
    workspace = manager.get_workspace_dir().resolve()
    decisions: dict[str, bool] = {}
    visible = []
    for result in results:
        relative = result.file if isinstance(result, FindResult) else result
        if relative not in decisions:
            try:
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise PermissionError("Search hit escapes its readable root")
                assert_http_workspace_path_access(str(target), manager=manager, workspace=workspace)
                decisions[relative] = True
            except (OSError, RuntimeError, ValueError):
                decisions[relative] = False
        if decisions[relative]:
            visible.append(result)
    return visible


@router.get(
    "",
    summary="Find text",
    description="Search for text patterns across files using ripgrep"
)
async def find_text(
    pattern: str = Query(..., description="Search pattern"),
    directory: Optional[str] = Query(None, description="Project directory"),
) -> List[FindResult]:
    """Search for text in files"""
    _validate_search_input(pattern, label="search pattern")
    cwd = await _resolve_search_directory(directory)
    
    try:
        # Use ripgrep if available
        result = await asyncio.to_thread(  # keep the event loop free while the search runs
            subprocess.run,
            ["rg", "--json", "--max-count", "100", "--", pattern],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        results = []
        for line in result.stdout.splitlines():
            try:
                import json
                data = json.loads(line)
                if data.get("type") == "match":
                    match_data = data.get("data", {})
                    path = match_data.get("path", {}).get("text", "")
                    line_num = match_data.get("line_number")
                    lines = match_data.get("lines", {})
                    content = lines.get("text", "").strip() if isinstance(lines, dict) else ""
                    
                    results.append(FindResult(
                        file=path,
                        line=line_num,
                        content=content[:200],  # Truncate
                    ))
            except Exception:
                continue
        
        return await asyncio.to_thread(_readable_search_hits, cwd, results)
    except FileNotFoundError:
        # ripgrep not available, use grep
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["grep", "-rn", "--", pattern, "."],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            results = []
            for line in result.stdout.splitlines()[:100]:
                parts = line.split(":", 2)
                if len(parts) >= 2:
                    results.append(FindResult(
                        file=parts[0],
                        line=int(parts[1]) if parts[1].isdigit() else None,
                        content=parts[2][:200] if len(parts) > 2 else None,
                    ))
            
            return await asyncio.to_thread(_readable_search_hits, cwd, results)
        except Exception:
            return []
    except Exception as e:
        log.warn("find.error", {"error": str(e)})
        return []


@router.get(
    "/file",
    summary="Find files",
    description="Search for files by name or pattern"
)
async def find_files(
    query: str = Query(..., description="File name or pattern"),
    directory: Optional[str] = Query(None, description="Project directory"),
    dirs: Optional[str] = Query(None, description="Include directories"),
    type: Optional[str] = Query(None, description="Filter type: file or directory"),
    limit: Optional[int] = Query(50, ge=1, le=200, description="Max results"),
) -> List[str]:
    """Search for files by name"""
    _validate_search_input(query, label="file query", max_length=200)
    cwd = await _resolve_search_directory(directory)
    
    try:
        # Use fd if available
        cmd = ["fd", "--max-results", str(limit or 50)]
        if type == "directory":
            cmd.extend(["--type", "d"])
        elif type == "file":
            cmd.extend(["--type", "f"])
        cmd.extend(["--", query])
        
        result = await asyncio.to_thread(  # keep the event loop free while the search runs
            subprocess.run,
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        
        files = result.stdout.strip().splitlines() if result.stdout else []
        return await asyncio.to_thread(_readable_search_hits, cwd, files)
    except FileNotFoundError:
        # fd not available, use find
        try:
            cmd = ["find", ".", "-maxdepth", "10", "-name", f"*{query}*"]
            if type == "directory":
                cmd.extend(["-type", "d"])
            elif type == "file":
                cmd.extend(["-type", "f"])
            
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            files = result.stdout.strip().splitlines() if result.stdout else []
            return await asyncio.to_thread(_readable_search_hits, cwd, files[:limit or 50])
        except Exception:
            return []
    except Exception as e:
        log.warn("find.file.error", {"error": str(e)})
        return []


@router.get(
    "/symbol",
    summary="Find symbols",
    description="Search for workspace symbols using LSP"
)
async def find_symbols(
    query: str = Query(..., description="Symbol name"),
    directory: Optional[str] = Query(None, description="Project directory"),
) -> List[dict]:
    """Search for symbols (placeholder)"""
    # TODO: Implement LSP symbol search
    return []
