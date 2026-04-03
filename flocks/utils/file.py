"""
File operations module

Handles file reading, writing, searching, and listing
"""

import os
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
            
            # Security: restrict file access to project workspace only
            from flocks.utils.paths import find_project_root
            from flocks.sandbox.paths import resolve_sandbox_path
            project_root = find_project_root()
            try:
                resolve_sandbox_path(abs_path, str(project_root), str(project_root))
            except ValueError as e:
                raise PermissionError(f"Access denied: {e}")
            
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
        try:
            # Use find command for file search
            cwd = os.getcwd()
            
            # Build find command
            cmd = ["find", ".", "-name", f"*{query}*"]
            
            if type == "file":
                cmd.extend(["-type", "f"])
            elif type == "directory":
                cmd.extend(["-type", "d"])
            
            # Exclude common directories
            excludes = [".git", "node_modules", "__pycache__", ".venv", "venv"]
            for exclude in excludes:
                cmd.extend(["-not", "-path", f"*/{exclude}/*"])
            
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode == 0:
                lines = [line.strip() for line in result.stdout.split("\n") if line.strip()]
                # Remove leading "./"
                lines = [line[2:] if line.startswith("./") else line for line in lines]
                return lines[:limit]
            
            return []
        except Exception as e:
            log.error("file.search.error", {"error": str(e), "query": query})
            return []
    
    @classmethod
    async def status(cls) -> List[FileInfo]:
        """
        Get git status of files
        
        Returns:
            List of file status info
        """
        try:
            cwd = os.getcwd()
            
            # Check if in git repo
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode != 0:
                return []
            
            # Get git status
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            )
            
            if result.returncode != 0:
                return []
            
            files = []
            
            for line in result.stdout.split("\n"):
                if not line.strip():
                    continue
                
                # Parse git status format
                status_code = line[:2]
                file_path = line[3:].strip()
                
                # Determine status
                if status_code.strip() == "A":
                    status = "added"
                elif status_code.strip() == "D":
                    status = "deleted"
                else:
                    status = "modified"
                
                # Get diff stats
                added, removed = 0, 0
                
                if status != "added":
                    try:
                        diff_result = subprocess.run(
                            ["git", "diff", "--numstat", "HEAD", "--", file_path],
                            cwd=cwd,
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        
                        if diff_result.returncode == 0 and diff_result.stdout.strip():
                            parts = diff_result.stdout.strip().split()
                            if len(parts) >= 2:
                                try:
                                    added = int(parts[0])
                                    removed = int(parts[1])
                                except ValueError:
                                    pass
                    except Exception:
                        pass
                
                file_info = FileInfo(
                    path=file_path,
                    added=added,
                    removed=removed,
                    status=status,
                )
                
                files.append(file_info)
            
            return files
        except Exception as e:
            log.error("file.status.error", {"error": str(e)})
            return []
