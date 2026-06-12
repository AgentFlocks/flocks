"""
File content extraction utilities for session message processing.

Extracted from SessionRunner to keep file-handling concerns separate
from session execution logic.
"""

from __future__ import annotations

import base64
import io
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

_log = logging.getLogger(__name__)


_TEXT_EXTRACTABLE_MIMES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xml",
        "application/yaml",
        "application/x-yaml",
        "application/javascript",
        "application/x-sh",
        "application/x-shellscript",
        "text/markdown",
        "text/csv",
    }
)

_DEFAULT_MAX_CHARS = 12_000
_DEFAULT_MAX_PAGES = 20
_LOCAL_DOWNLOAD_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def file_url_to_path(url: str) -> str:
    """Convert a ``file://`` URL to a local filesystem path string."""
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if len(path) >= 3 and path[0] == "/" and path[2] == ":" and path[1].isalpha():
        path = path[1:]
    if parsed.netloc and parsed.netloc.lower() != "localhost":
        path = f"//{parsed.netloc}{path}"
    return path


def file_download_url_to_path(url: str) -> Optional[str]:
    """Extract the local file path from the WebUI file download URL."""
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname not in _LOCAL_DOWNLOAD_HOSTS:
        return None
    if parsed.path != "/api/file/download":
        return None
    path_values = parse_qs(parsed.query).get("path")
    if not path_values:
        return None
    return path_values[0]


def _read_local_path(path: str) -> Optional[bytes]:
    try:
        return Path(path).read_bytes()
    except Exception as e:
        _log.debug("read_file_part_bytes: file read failed: %s (path=%s)", e, path)
        return None


def read_file_part_bytes(url: str) -> Optional[bytes]:
    """Read raw bytes from a data URI, file:// URL, or local download URL.

    Returns None when the URL is empty, has an unsupported scheme, or the
    underlying read fails.
    """
    if not url:
        return None
    if url.startswith("data:"):
        try:
            _, encoded = url.split(",", 1)
            return base64.b64decode(encoded)
        except Exception as e:
            _log.debug("read_file_part_bytes: data URI decode failed: %s", e)
            return None
    if url.startswith("file://"):
        return _read_local_path(file_url_to_path(url))
    path = file_download_url_to_path(url)
    if path:
        return _read_local_path(path)
    return None


def is_text_extractable_mime(mime: str) -> bool:
    """Return True when the MIME type's content can be decoded as UTF-8 text."""
    return mime.startswith("text/") or mime in _TEXT_EXTRACTABLE_MIMES


def truncate_extracted_text(text: str, max_chars: int = _DEFAULT_MAX_CHARS) -> tuple[str, bool]:
    """Strip and truncate *text* to *max_chars*.

    Returns ``(truncated_text, was_truncated)``.
    """
    normalized = text.strip()
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars].rstrip(), True


def extract_pdf_text_from_bytes(
    data: bytes,
    *,
    max_pages: int = _DEFAULT_MAX_PAGES,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> Optional[str]:
    """Extract text from PDF bytes using pypdf.

    Returns None when pypdf is unavailable or extraction fails.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        texts: list[str] = []
        for page in reader.pages[:max_pages]:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                texts.append(page_text)
            if len("\n\n".join(texts)) >= max_chars:
                break
        if not texts:
            return None
        merged = "\n\n".join(texts)
        truncated, was_truncated = truncate_extracted_text(merged, max_chars=max_chars)
        suffix = "\n\n[PDF content truncated]" if was_truncated else ""
        return truncated + suffix
    except Exception as e:
        _log.debug("extract_pdf_text: failed: %s", e)
        return None


def extract_file_text(
    *,
    mime: str,
    filename: str,
    url: str,
) -> Optional[str]:
    """Read a file and return its text representation.

    Supports plain-text MIME types and PDF. Returns None when the file
    cannot be read or has no extractable text content.
    """
    data = read_file_part_bytes(url)
    if not data:
        return None

    extracted: Optional[str] = None
    if is_text_extractable_mime(mime):
        decoded = data.decode("utf-8", errors="replace")
        truncated, was_truncated = truncate_extracted_text(decoded)
        if truncated:
            suffix = "\n\n[Text content truncated]" if was_truncated else ""
            extracted = truncated + suffix
    elif mime == "application/pdf":
        extracted = extract_pdf_text_from_bytes(data)

    if not extracted:
        return None

    return "\n".join([f"[Attached file: {filename}]", "", extracted])


__all__ = [
    "extract_pdf_text_from_bytes",
    "extract_file_text",
    "file_download_url_to_path",
    "file_url_to_path",
    "is_text_extractable_mime",
    "read_file_part_bytes",
    "truncate_extracted_text",
]
