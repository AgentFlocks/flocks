"""
Log viewing routes for WebUI.

Provides endpoints to list and read log files from ~/.flocks/logs.
"""

from collections import deque
from datetime import date, datetime
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
    for p in sorted(_iter_log_files(log_dir), key=lambda f: f.stat().st_mtime, reverse=True):
        stat = p.stat()
        files.append(LogFileInfo(name=p.relative_to(log_dir).as_posix(), size=stat.st_size, modified=stat.st_mtime))
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

    today_log = log_dir / date.today().isoformat() / "flocks.log"
    if today_log.is_file():
        return _read_log_file(today_log, tail, filename=_relative_log_name(log_dir, today_log))

    for day_dir in sorted(_iter_date_dirs(log_dir), reverse=True):
        main_log = day_dir / "flocks.log"
        if main_log.is_file():
            return _read_log_file(main_log, tail, filename=_relative_log_name(log_dir, main_log))

    log_files = sorted(_iter_log_files(log_dir), key=lambda f: f.stat().st_mtime, reverse=True)
    if not log_files:
        raise HTTPException(status_code=404, detail="No log files found")

    return _read_log_file(log_files[0], tail, filename=_relative_log_name(log_dir, log_files[0]))


@router.get(
    "/{filename:path}",
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

    if not log_path.is_file() or not _is_allowed_log_path(log_dir, log_path):
        raise HTTPException(status_code=404, detail=f"Log file not found: {filename}")

    if not log_path.resolve().is_relative_to(log_dir.resolve()):
        raise HTTPException(status_code=403, detail="Access denied")

    return _read_log_file(log_path, tail, filename=filename)


def _is_date_dir(path: Path) -> bool:
    try:
        datetime.strptime(path.name, "%Y-%m-%d")
    except ValueError:
        return False
    return path.is_dir()


def _iter_date_dirs(log_dir: Path) -> List[Path]:
    return [p for p in log_dir.iterdir() if _is_date_dir(p)]


def _is_allowed_log_path(log_dir: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(log_dir)
    except ValueError:
        return False
    parts = relative.parts
    if len(parts) == 1:
        return parts[0] in {"backend.log", "webui.log"}
    if len(parts) == 2:
        day, filename = parts
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            return False
        return filename in {"flocks.log", "errors.log"}
    return False


def _iter_log_files(log_dir: Path) -> List[Path]:
    files = []
    for name in ("backend.log", "webui.log"):
        path = log_dir / name
        if path.is_file():
            files.append(path)
    for day_dir in _iter_date_dirs(log_dir):
        for name in ("flocks.log", "errors.log"):
            path = day_dir / name
            if path.is_file():
                files.append(path)
    return files


def _relative_log_name(log_dir: Path, path: Path) -> str:
    return path.relative_to(log_dir).as_posix()


def _read_log_file(path: Path, tail: int, filename: str | None = None) -> LogContentResponse:
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
        filename=filename or path.name,
        content=content,
        total_lines=total,
        truncated=truncated,
    )
