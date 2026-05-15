"""
Log viewing routes for WebUI.

Provides endpoints to list and read log files from ~/.flocks/logs.
"""

from collections import deque
from pathlib import Path
from typing import List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from flocks.utils.log import get_log_dir

router = APIRouter()


class LogFileInfo(BaseModel):
    name: str
    size: int
    modified: float


class LogListResponse(BaseModel):
    files: List[LogFileInfo]
    log_dir: str


class LogContentResponse(BaseModel):
    filename: str
    content: str
    total_lines: int
    truncated: bool


@router.get(
    "",
    response_model=LogListResponse,
    summary="List log files",
)
async def list_logs():
    """List all log files sorted by modification time (newest first)."""
    log_dir = get_log_dir()
    if not log_dir.is_dir():
        return LogListResponse(files=[], log_dir=str(log_dir))

    files: List[LogFileInfo] = []
    for p in sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        files.append(LogFileInfo(name=p.name, size=stat.st_size, modified=stat.st_mtime))
    return LogListResponse(files=files, log_dir=str(log_dir))


@router.get(
    "/latest",
    response_model=LogContentResponse,
    summary="Read latest log file",
)
async def read_latest_log(
    tail: int = Query(200, ge=1, le=5000, description="Number of lines from the end"),
):
    """Read the last N lines of the most recent log file."""
    log_dir = get_log_dir()
    if not log_dir.is_dir():
        raise HTTPException(status_code=404, detail="Log directory not found")

    log_files = sorted(log_dir.glob("*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        raise HTTPException(status_code=404, detail="No log files found")

    return _read_log_file(log_files[0], tail)


@router.get(
    "/{filename}",
    response_model=LogContentResponse,
    summary="Read a specific log file",
)
async def read_log(
    filename: str,
    tail: int = Query(200, ge=1, le=5000, description="Number of lines from the end"),
):
    """Read the last N lines of a specific log file."""
    log_dir = get_log_dir()
    log_path = log_dir / filename

    if not log_path.is_file() or not log_path.suffix == ".log":
        raise HTTPException(status_code=404, detail=f"Log file not found: {filename}")

    if not log_path.resolve().is_relative_to(log_dir.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")

    return _read_log_file(log_path, tail)


def _read_log_file(path: Path, tail: int) -> LogContentResponse:
    try:
        lines: deque[str] = deque(maxlen=tail)
        total = 0
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                total += 1
                lines.append(line.rstrip("\n"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read log: {e}")

    truncated = total > tail
    content = "\n".join(lines)

    return LogContentResponse(
        filename=path.name,
        content=content,
        total_lines=total,
        truncated=truncated,
    )
