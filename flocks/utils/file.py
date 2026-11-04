"""
File operations module

Handles file reading, writing, searching, and listing
"""

import asyncio
import os
import fnmatch
import time
import subprocess
import base64
import mimetypes
from typing import List, Dict, Any, Optional
from pathlib import Path
from pydantic import BaseModel

from flocks.utils.log import Log

log = Log.create(service="file")


class FileNode(BaseModel):
    """File or directory node"""
    name: str
    path: str
    absolute: str
    type: str  # "file" or "directory"
    ignored: bool = False


class FileContent(BaseModel):
    """File content"""
    type: str = "text"
    content: str
    encoding: Optional[str] = None
    mimeType: Optional[str] = None


class FileInfo(BaseModel):
    """File status information"""
    path: str
    added: int = 0
    removed: int = 0
    status: str  # "added", "deleted", "modified"


class File:
    """File operations namespace"""
    
    @classmethod
    async def list(cls, directory: str) -> List[FileNode]:
        """
        List files and directories
        
        Args:
            directory: Directory path
            
        Returns:
            List of file nodes
        """
        try:
            abs_path = os.path.abspath(directory)
            
            if not os.path.exists(abs_path):
                log.warn("directory.not_found", {"path": abs_path})
                return []
            
            if not os.path.isdir(abs_path):
                log.warn("not_a_directory", {"path": abs_path})
                return []
            
            nodes = []
            
            try:
                entries = os.listdir(abs_path)
            except PermissionError:
                log.warn("permission_denied", {"path": abs_path})
                return []
            
            for entry in sorted(entries):
                entry_path = os.path.join(abs_path, entry)
                rel_path = os.path.relpath(entry_path, abs_path)
                
                is_dir = os.path.isdir(entry_path)
                is_ignored = entry.startswith(".")
                
                node = FileNode(
                    name=entry,
                    path=rel_path,
                    absolute=entry_path,
                    type="directory" if is_dir else "file",
                    ignored=is_ignored,
                )
                
                nodes.append(node)
            
            return nodes
        except Exception as e:
            log.error("file.list.error", {"error": str(e), "path": directory})
            return []
    
    @classmethod
    async def read(cls, file_path: str) -> FileContent:
        """
        Read file content
        
        Args:
            file_path: File path
            
        Returns:
            File content
        """
        try:
            abs_path = os.path.abspath(file_path)
            
            if not os.path.exists(abs_path):
                raise FileNotFoundError(f"File not found: {file_path}")
            
            if not os.path.isfile(abs_path):
                raise ValueError(f"Not a file: {file_path}")
            
            # Get MIME type
            mime_type, _ = mimetypes.guess_type(abs_path)
            
            # Check if binary
            should_encode = cls._should_encode(mime_type)
            
            if should_encode:
                # Read as binary and encode
                with open(abs_path, "rb") as f:
                    content_bytes = f.read()
                    content = base64.b64encode(content_bytes).decode("utf-8")
                
                return FileContent(
                    type="text",
                    content=content,
                    encoding="base64",
                    mimeType=mime_type or "application/octet-stream",
                )
            else:
                # Read as text
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read()
                except UnicodeDecodeError:
                    # Fallback to binary encoding
                    with open(abs_path, "rb") as f:
                        content_bytes = f.read()
                        content = base64.b64encode(content_bytes).decode("utf-8")
                    
                    return FileContent(
                        type="text",
                        content=content,
                        encoding="base64",
                        mimeType="application/octet-stream",
                    )
                
                return FileContent(
                    type="text",
                    content=content,
                    mimeType=mime_type or "text/plain",
                )
        except Exception as e:
            log.error("file.read.error", {"error": str(e), "path": file_path})
            raise
    
    @classmethod
    def _should_encode(cls, mime_type: Optional[str]) -> bool:
        """
        Check if file should be base64 encoded
        
        Args:
            mime_type: MIME type
            
        Returns:
            True if should encode
        """
        if not mime_type:
            return False
        
        mime_lower = mime_type.lower()
        
        # Text types don't need encoding
        if mime_lower.startswith("text/"):
            return False
        
        # Application types that are text
        text_apps = [
            "application/json",
            "application/xml",
            "application/javascript",
            "application/typescript",
        ]
        
        if any(mime_lower.startswith(t) for t in text_apps):
            return False
        
        # Binary types
        binary_types = [
            "image/",
            "audio/",
            "video/",
            "font/",
            "application/pdf",
            "application/zip",
            "application/x-",
        ]
        
        return any(mime_lower.startswith(t) for t in binary_types)
    
    @classmethod
    async def search(cls, query: str, limit: int = 10, dirs: bool = True, 
                    type: Optional[str] = None) -> List[str]:
        return await asyncio.to_thread(cls._search_sync, os.getcwd(), query, limit, dirs, type)

    @staticmethod
    def _search_sync(cwd: str, query: str, limit: int, dirs: bool, type: Optional[str]) -> List[str]:
        """
        Search for files by name
        
        Args:
            query: Search query
            limit: Maximum results
            dirs: Include directories
            type: Filter by type ("file" or "directory")
            
        Returns:
            List of file paths
        """
        matches = []
        limit = max(0, min(limit, 200))
        if not limit:
            return matches
        excluded = {".git", "node_modules", "__pycache__", ".venv", "venv"}
        deadline = time.monotonic() + 5.0
        pattern = f"*{query}*"
        try:
            for directory, children, files in os.walk(cwd, followlinks=False):
                children[:] = [name for name in children if name not in excluded]
                candidates = []
                if type != "file" and (dirs or type == "directory"):
                    candidates.extend(children)
                if type != "directory":
                    candidates.extend(files)
                for name in candidates:
                    if time.monotonic() >= deadline:
                        return matches
                    if fnmatch.fnmatchcase(name, pattern):
                        matches.append(os.path.relpath(os.path.join(directory, name), cwd))
                        if len(matches) >= limit:
                            return matches
                if time.monotonic() >= deadline:
                    return matches
        except OSError as exc:
            log.error("file.search.error", {"error": str(exc), "query": query})
        return matches

    @classmethod
    async def status(cls) -> List[FileInfo]:
        """Read Git status without blocking the server's event loop."""
        return await asyncio.to_thread(cls._status_sync, os.getcwd())

    @staticmethod
    def _status_sync(cwd: str) -> List[FileInfo]:
        """Use one status and one batched diff, regardless of file count."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
                cwd=cwd,
                capture_output=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout:
                return []

            stats: Dict[str, tuple[int, int]] = {}
            try:
                diff = subprocess.run(
                    ["git", "diff", "--numstat", "-z", "HEAD", "--"],
                    cwd=cwd, capture_output=True, timeout=5,
                )
                if diff.returncode != 0:
                    # An unborn repository has no HEAD. Its staged files are
                    # still useful, and --cached compares them to an empty tree.
                    diff = subprocess.run(
                        ["git", "diff", "--numstat", "-z", "--cached", "--"],
                        cwd=cwd, capture_output=True, timeout=5,
                    )
                if diff.returncode == 0:
                    records = iter(diff.stdout.split(b"\0"))
                    for record in records:
                        if not record:
                            continue
                        added, removed, path = record.split(b"\t", 2)
                        if not path:
                            # Renames/copies have separate old and new names.
                            next(records)
                            path = next(records)
                        stats[os.fsdecode(path)] = (
                            int(added) if added.isdigit() else 0,
                            int(removed) if removed.isdigit() else 0,
                        )
            except (OSError, subprocess.TimeoutExpired, ValueError, StopIteration) as exc:
                log.warn("file.status.diff_unavailable", {"error": str(exc)})

            files = []
            records = iter(result.stdout.split(b"\0"))
            for record in records:
                if not record:
                    continue
                code, path = record[:2], os.fsdecode(record[3:])
                if b"R" in code or b"C" in code:
                    next(records, None)
                if b"D" in code:
                    status = "deleted"
                elif b"A" in code or code == b"??":
                    status = "added"
                else:
                    status = "modified"
                added, removed = stats.get(path, (0, 0))
                files.append(FileInfo(path=path, added=added, removed=removed, status=status))
            return files
        except Exception as e:
            log.error("file.status.error", {"error": str(e)})
            return []
