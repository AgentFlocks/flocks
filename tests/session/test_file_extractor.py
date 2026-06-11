"""
Tests for flocks/session/utils/file_extractor.py

Covers pure-function utilities:
- read_file_part_bytes: data URI and file:// URL reading
- is_text_extractable_mime: MIME type classification
- truncate_extracted_text: text truncation
- extract_file_text: unified extraction entry point
"""

import base64
import tempfile
from pathlib import Path

import pytest

from flocks.session.utils.file_extractor import (
    extract_file_text,
    file_url_to_path,
    is_text_extractable_mime,
    read_file_part_bytes,
    truncate_extracted_text,
)


# ---------------------------------------------------------------------------
# read_file_part_bytes
# ---------------------------------------------------------------------------


class TestReadFilePartBytes:
    def test_empty_string_returns_none(self):
        assert read_file_part_bytes("") is None

    def test_none_like_empty_returns_none(self):
        # non-supported scheme
        assert read_file_part_bytes("http://example.com/file.txt") is None

    def test_data_uri_base64(self):
        content = b"hello world"
        encoded = base64.b64encode(content).decode()
        url = f"data:text/plain;base64,{encoded}"
        result = read_file_part_bytes(url)
        assert result == content

    def test_data_uri_invalid_base64_returns_none(self):
        result = read_file_part_bytes("data:text/plain;base64,!!!invalid!!!")
        assert result is None

    def test_data_uri_no_comma_returns_none(self):
        result = read_file_part_bytes("data:text/plain;base64")
        assert result is None

    def test_file_url_reads_file(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"file content")
        url = test_file.as_uri()
        result = read_file_part_bytes(url)
        assert result == b"file content"

    def test_file_url_nonexistent_returns_none(self):
        result = read_file_part_bytes("file:///nonexistent/path/file.txt")
        assert result is None

    def test_file_url_with_spaces_in_path(self, tmp_path):
        test_dir = tmp_path / "my dir"
        test_dir.mkdir()
        test_file = test_dir / "my file.txt"
        test_file.write_bytes(b"spaced content")
        url = test_file.as_uri()
        result = read_file_part_bytes(url)
        assert result == b"spaced content"

    def test_windows_drive_file_url_path_does_not_keep_posix_prefix(self):
        path = file_url_to_path("file:///C:/Users/demo/Pictures/channel%20image.png")
        assert path == "C:/Users/demo/Pictures/channel image.png"

    def test_unc_file_url_path_preserves_host(self):
        path = file_url_to_path("file://server/share/channel%20image.png")
        assert path == "//server/share/channel image.png"


# ---------------------------------------------------------------------------
# is_text_extractable_mime
# ---------------------------------------------------------------------------


class TestIsTextExtractableMime:
    @pytest.mark.parametrize(
        "mime",
        [
            "text/plain",
            "text/html",
            "text/css",
            "text/javascript",
            "text/markdown",
            "text/csv",
            "text/xml",
        ],
    )
    def test_text_prefix_is_extractable(self, mime):
        assert is_text_extractable_mime(mime) is True

    @pytest.mark.parametrize(
        "mime",
        [
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/yaml",
            "application/x-yaml",
            "application/javascript",
            "application/x-sh",
            "application/x-shellscript",
        ],
    )
    def test_special_application_mimes_are_extractable(self, mime):
        assert is_text_extractable_mime(mime) is True

    @pytest.mark.parametrize(
        "mime",
        [
            "image/png",
            "image/jpeg",
            "video/mp4",
            "audio/mpeg",
            "application/octet-stream",
            "application/zip",
            "application/pdf",  # PDF handled separately
        ],
    )
    def test_binary_mimes_are_not_extractable(self, mime):
        assert is_text_extractable_mime(mime) is False

    def test_empty_string_is_not_extractable(self):
        assert is_text_extractable_mime("") is False


# ---------------------------------------------------------------------------
# truncate_extracted_text
# ---------------------------------------------------------------------------


class TestTruncateExtractedText:
    def test_short_text_not_truncated(self):
        text = "hello world"
        result, was_truncated = truncate_extracted_text(text, max_chars=100)
        assert result == "hello world"
        assert was_truncated is False

    def test_long_text_is_truncated(self):
        text = "a" * 200
        result, was_truncated = truncate_extracted_text(text, max_chars=100)
        assert len(result) <= 100
        assert was_truncated is True

    def test_exact_length_not_truncated(self):
        text = "x" * 50
        result, was_truncated = truncate_extracted_text(text, max_chars=50)
        assert was_truncated is False

    def test_leading_trailing_whitespace_stripped(self):
        text = "  hello  "
        result, was_truncated = truncate_extracted_text(text, max_chars=100)
        assert result == "hello"
        assert was_truncated is False

    def test_trailing_whitespace_removed_after_truncation(self):
        # Ensure rstrip is called on the truncated portion
        text = "hello " + "x" * 200
        result, was_truncated = truncate_extracted_text(text, max_chars=10)
        assert was_truncated is True
        assert not result.endswith(" ")

    def test_empty_string_not_truncated(self):
        result, was_truncated = truncate_extracted_text("", max_chars=10)
        assert result == ""
        assert was_truncated is False

    def test_default_max_chars(self):
        text = "a" * 15000
        result, was_truncated = truncate_extracted_text(text)
        assert was_truncated is True
        assert len(result) <= 12000


# ---------------------------------------------------------------------------
# extract_file_text
# ---------------------------------------------------------------------------


class TestExtractFileText:
    def test_plain_text_file(self, tmp_path):
        f = tmp_path / "readme.txt"
        f.write_text("Hello from file", encoding="utf-8")
        result = extract_file_text(mime="text/plain", filename="readme.txt", url=f.as_uri())
        assert result is not None
        assert "Hello from file" in result
        assert "[Attached file: readme.txt]" in result

    def test_json_file_extracted(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}', encoding="utf-8")
        result = extract_file_text(mime="application/json", filename="data.json", url=f.as_uri())
        assert result is not None
        assert '"key"' in result

    def test_markdown_file_extracted(self, tmp_path):
        f = tmp_path / "notes.md"
        f.write_text("# Title\nSome content", encoding="utf-8")
        result = extract_file_text(mime="text/markdown", filename="notes.md", url=f.as_uri())
        assert result is not None
        assert "# Title" in result

    def test_image_file_returns_none(self, tmp_path):
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG\r\n")
        result = extract_file_text(mime="image/png", filename="image.png", url=f.as_uri())
        assert result is None

    def test_nonexistent_file_returns_none(self):
        result = extract_file_text(
            mime="text/plain",
            filename="missing.txt",
            url="file:///nonexistent/missing.txt",
        )
        assert result is None

    def test_large_text_file_truncated(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 20000, encoding="utf-8")
        result = extract_file_text(mime="text/plain", filename="big.txt", url=f.as_uri())
        assert result is not None
        assert "[Text content truncated]" in result

    def test_filename_in_output_header(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("print('hello')", encoding="utf-8")
        result = extract_file_text(mime="text/x-python", filename="script.py", url=f.as_uri())
        assert result is not None
        assert "[Attached file: script.py]" in result

    def test_empty_file_returns_none(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")
        result = extract_file_text(mime="text/plain", filename="empty.txt", url=f.as_uri())
        assert result is None

    def test_data_uri_text_extracted(self):
        content = "inline content from data uri"
        encoded = base64.b64encode(content.encode()).decode()
        url = f"data:text/plain;base64,{encoded}"
        result = extract_file_text(mime="text/plain", filename="inline.txt", url=url)
        assert result is not None
        assert "inline content from data uri" in result
