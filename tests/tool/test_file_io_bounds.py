import asyncio
import time
import tracemalloc

import pytest

from flocks.tool.file import read
from flocks.tool.registry import ToolContext
from flocks.utils.file import File


def test_short_window_does_not_load_a_large_file(tmp_path):
    path = tmp_path / "large.txt"
    with path.open("wb") as stream:
        for _ in range(32):
            stream.write(b"line\n" * 200000)
    tracemalloc.start()
    try:
        lines, more, total, by_bytes, shortened = read._read_text_window(path, 0, 3, 1024, 2000)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert lines == ["line"] * 3 and more and total is None
    assert not by_bytes and not shortened
    assert peak < 2 * 1024 * 1024


def test_huge_single_line_has_a_bounded_input_budget(tmp_path, monkeypatch):
    path = tmp_path / "single-line.txt"
    path.write_text("x" * 20000)
    monkeypatch.setattr(read, "MAX_TEXT_SCAN_CHARS", 10000)
    with pytest.raises(ValueError, match="scan budget"):
        read._read_text_window(path, 0, 2, 1024, 100)


@pytest.mark.parametrize("text,offset,expected", [("", 0, []), ("a\n", 0, ["a"]), ("a\nb", 1, ["b"]), ("a\n", 4, [])])
def test_exact_eof_line_count(tmp_path, text, offset, expected):
    path = tmp_path / "eof.txt"
    path.write_text(text)
    lines, more, total, _, _ = read._read_text_window(path, offset, 10, 1024, 100)
    assert lines == expected and not more
    assert total == len(text.splitlines())


def test_utf8_byte_boundary_returns_valid_progress(tmp_path):
    path = tmp_path / "utf8.txt"
    path.write_text("中文内容\nnext")
    lines, more, _, by_bytes, _ = read._read_text_window(path, 0, 10, 7, 100)
    assert lines == ["中文"] and more and by_bytes


@pytest.mark.asyncio
async def test_read_and_search_leave_event_loop_running(tmp_path, monkeypatch):
    path = tmp_path / "file.txt"
    path.write_text("hello")
    real_read = read._read_resolved
    real_search = File._search_sync
    def slow_read(*args):
        time.sleep(0.12)
        return real_read(*args)
    def slow_search(*args):
        time.sleep(0.12)
        return real_search(*args)
    monkeypatch.setattr(read, "_read_resolved", slow_read)
    monkeypatch.setattr(File, "_search_sync", slow_search)
    monkeypatch.chdir(tmp_path)
    ticks = 0
    async def heartbeat():
        nonlocal ticks
        for _ in range(10):
            await asyncio.sleep(0.01)
            ticks += 1
    ctx = ToolContext(session_id="io-test", message_id="io-test", agent="test")
    read_result, search_result, _ = await asyncio.gather(read.read_tool(ctx, str(path)), File.search("file"), heartbeat())
    assert read_result.success and search_result == ["file.txt"] and ticks == 10


@pytest.mark.asyncio
async def test_search_prunes_ignored_directories_and_stops_at_limit(tmp_path, monkeypatch):
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "match.txt").write_text("ignored")
    (tmp_path / "match-dir").mkdir()
    for index in range(30):
        (tmp_path / f"match-{index}.txt").write_text("visible")
    monkeypatch.chdir(tmp_path)
    result = await File.search("match", limit=3, dirs=False)
    assert len(result) == 3 and all(name.endswith(".txt") for name in result)
    assert not any("node_modules" in name for name in result)
    assert await File.search("match", type="directory") == ["match-dir"]


@pytest.mark.asyncio
async def test_image_attachment_size_limit(tmp_path, monkeypatch):
    path = tmp_path / "oversized.png"
    path.write_bytes(b"x" * 1001)
    monkeypatch.setattr(read, "MAX_ATTACHMENT_BYTES", 1000)
    ctx = ToolContext(session_id="attachment", message_id="attachment", agent="test")
    result = await read.read_tool(ctx, str(path))
    assert not result.success and "exceeds 1000 bytes" in result.error
