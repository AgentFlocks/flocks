"""
Find routes for Flocks TUI compatibility

Provides /find/* endpoints that Flocks SDK expects.
"""

import os
import subprocess
from typing import Optional, List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from flocks.utils.log import Log


router = APIRouter()
log = Log.create(service="find-routes")


class FindResult(BaseModel):
    """Search result item"""
    file: str
    line: Optional[int] = None
    column: Optional[int] = None
    content: Optional[str] = None


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
    cwd = directory or os.getcwd()
    
    try:
        # Use ripgrep if available
        result = subprocess.run(
            ["rg", "--json", "--max-count", "100", pattern],
            cwd=cwd,
            capture_output=True,
            text=True,
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
        
        return results
    except FileNotFoundError:
        # ripgrep not available, use grep
        try:
            result = subprocess.run(
                ["grep", "-rn", pattern, "."],
                cwd=cwd,
                capture_output=True,
                text=True,
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
            
            return results
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
    limit: Optional[int] = Query(50, description="Max results"),
) -> List[str]:
    """Search for files by name"""
    cwd = directory or os.getcwd()
    
    try:
        # Use fd if available
        cmd = ["fd", "--max-results", str(limit or 50)]
        if type == "directory":
            cmd.extend(["--type", "d"])
        elif type == "file":
            cmd.extend(["--type", "f"])
        cmd.append(query)
        
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
        
        return result.stdout.strip().splitlines() if result.stdout else []
    except FileNotFoundError:
        # fd not available, use find
        try:
            cmd = ["find", ".", "-name", f"*{query}*", "-maxdepth", "10"]
            if type == "directory":
                cmd.extend(["-type", "d"])
            elif type == "file":
                cmd.extend(["-type", "f"])
            
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
            )
            
            files = result.stdout.strip().splitlines() if result.stdout else []
            return files[:limit or 50]
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
