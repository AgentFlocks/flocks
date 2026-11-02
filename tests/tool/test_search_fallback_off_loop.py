"""Without ripgrep, glob and grep walk the tree in Python -- off the event loop.

The walk used to run on the event loop: glob over a home directory held up
the whole server, every request and every session, for minutes, so the
backend looked dead and had to be restarted (INC-45A58F4B).
"""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from flocks.tool import nofollow, search_walk
from flocks.tool.code import grep as grep_module
from flocks.tool.file import glob as glob_module
from flocks.tool.registry import ToolContext

DIRECTORIES = 40
SCAN_SECONDS = 0.025


def _ctx(root: Path) -> ToolContext:
    async def approve(_request) -> None:
        return None

    return ToolContext(
        session_id="ses_search_walk", message_id="msg_search_walk",
        permission_callback=approve, extra={"workspace_dir": str(root)},
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    for index in range(DIRECTORIES):
        directory = tmp_path / f"d{index:02}"
        directory.mkdir()
        (directory / "report.txt").write_text("mine\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def slow_scan(monkeypatch: pytest.MonkeyPatch) -> list:
    """Every directory listing takes a while: a big tree, in small. Returns the directories listed."""
    listed: list = []
    real_scan = nofollow.Directory.scan

    def scan(self):
        time.sleep(SCAN_SECONDS)
        listed.append(self.path)
        return real_scan(self)

    monkeypatch.setattr(nofollow.Directory, "scan", scan)
    return listed


def _without_ripgrep(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    monkeypatch.setattr(grep_module if tool == "grep" else glob_module, "find_ripgrep", lambda: None)


async def _search(tool: str, root: Path):
    if tool == "grep":
        return await grep_module.grep_tool(_ctx(root), pattern="never-there", path=str(root))
    return await glob_module.glob_tool(_ctx(root), pattern="**/never-there.md", path=str(root))


@pytest.mark.parametrize("tool", ["glob", "grep"])
async def test_fallback_walk_leaves_the_event_loop_free(
    tree: Path, slow_scan: list, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    _without_ripgrep(monkeypatch, tool)
    gaps: list = []
    done = asyncio.Event()

    async def heartbeat() -> None:
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    started = time.monotonic()
    result = await _search(tool, tree)
    elapsed = time.monotonic() - started
    done.set()
    await beat

    assert result.success, result.error
    assert "No files found" in result.output
    assert len(slow_scan) == DIRECTORIES + 1
    assert elapsed >= DIRECTORIES * SCAN_SECONDS
    # The walk took about a second; the loop was never held up for long.
    assert max(gaps) < 0.4


@pytest.mark.parametrize("tool", ["glob", "grep"])
async def test_fallback_walk_stops_at_the_time_limit(
    tree: Path, slow_scan: list, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    _without_ripgrep(monkeypatch, tool)
    monkeypatch.setattr(search_walk, "TIME_LIMIT", 0.2)

    started = time.monotonic()
    result = await _search(tool, tree)

    assert time.monotonic() - started < 0.8
    assert result.success, result.error
    assert result.truncated is True
    assert result.metadata["timed_out"] is True
    assert "No files found" in result.output
    assert "Search stopped after 0.2 seconds" in result.output
    assert len(slow_scan) < DIRECTORIES / 2


@pytest.mark.parametrize("tool", ["glob", "grep"])
async def test_fallback_walk_that_finishes_is_not_marked_stopped(
    tree: Path, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    _without_ripgrep(monkeypatch, tool)

    if tool == "grep":
        result = await grep_module.grep_tool(_ctx(tree), pattern="mine", path=str(tree))
    else:
        result = await glob_module.glob_tool(_ctx(tree), pattern="**/report.txt", path=str(tree))

    assert result.success, result.error
    assert result.metadata["timed_out"] is False
    assert "Search stopped" not in result.output
    assert str(tree / "d07" / "report.txt") in result.output


async def test_given_up_fallback_walk_stops_walking(
    tree: Path, slow_scan: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_ripgrep(monkeypatch, "glob")
    search = asyncio.create_task(_search("glob", tree))
    await asyncio.sleep(0.15)
    search.cancel()
    with pytest.raises(asyncio.CancelledError):
        await search
    listed = len(slow_scan)
    await asyncio.sleep(0.3)

    assert listed < DIRECTORIES
    # The listing under way when it was given up ends; nothing after it.
    assert len(slow_scan) <= listed + 1


async def test_glob_fallback_walk_ends_past_the_result_limit(
    tree: Path, slow_scan: list, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_ripgrep(monkeypatch, "glob")
    monkeypatch.setattr(glob_module, "MAX_FILES", 5)

    result = await glob_module.glob_tool(_ctx(tree), pattern="**/*.txt", path=str(tree))

    assert result.success, result.error
    assert result.truncated is True and result.metadata["timed_out"] is False
    assert result.metadata["count"] == 5
    # The sixth match ends the walk: the root and six directories are listed.
    assert len(slow_scan) == 7


async def test_glob_fallback_counts_only_files_still_there(
    tree: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matches gone (or turned into links) by the time they are checked do not count toward the limit."""
    _without_ripgrep(monkeypatch, "glob")
    monkeypatch.setattr(glob_module, "MAX_FILES", 5)
    gone = {str(Path("d00", "report.txt")), str(Path("d01", "report.txt"))}
    real_file_status = nofollow.file_status
    monkeypatch.setattr(
        nofollow, "file_status",
        lambda root, relative: None if relative in gone else real_file_status(root, relative),
    )

    result = await glob_module.glob_tool(_ctx(tree), pattern="**/*.txt", path=str(tree))

    assert result.success, result.error
    assert result.metadata["count"] == 5 and result.truncated is True
    shown = {line for line in result.output.splitlines() if line.endswith("report.txt")}
    assert shown == {str(tree / f"d{index:02}" / "report.txt") for index in range(2, 7)}


async def test_glob_fallback_checks_found_files_off_the_event_loop(
    tree: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _without_ripgrep(monkeypatch, "glob")
    real_file_status = nofollow.file_status

    def slow_file_status(root, relative):
        time.sleep(SCAN_SECONDS)
        return real_file_status(root, relative)

    monkeypatch.setattr(nofollow, "file_status", slow_file_status)
    gaps: list = []
    done = asyncio.Event()

    async def heartbeat() -> None:
        last = time.monotonic()
        while not done.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.05)
    result = await glob_module.glob_tool(_ctx(tree), pattern="**/*.txt", path=str(tree))
    done.set()
    await beat

    assert result.success, result.error
    assert result.metadata["count"] == DIRECTORIES
    assert max(gaps) < 0.4


async def test_glob_fallback_walks_only_the_directory_the_tool_opened(
    tree: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker thread opens the search directory again; one swapped in meanwhile is not walked."""
    _without_ripgrep(monkeypatch, "glob")
    other = tmp_path_factory.mktemp("someone_else")
    (other / "their-private-name.txt").write_text("theirs\n", encoding="utf-8")
    real_directory = nofollow.directory

    def directory(path, **kwargs):
        if threading.current_thread() is not threading.main_thread() and path == str(tree):
            return real_directory(str(other), **kwargs)
        return real_directory(path, **kwargs)

    monkeypatch.setattr(nofollow, "directory", directory)

    result = await glob_module.glob_tool(_ctx(tree), pattern="**/*.txt", path=str(tree))

    assert result.success is False
    assert "changed" in result.error
    assert "their-private-name" not in f"{result.output}{result.error}"


def test_walk_files_lists_nothing_more_once_stopped(tree: Path) -> None:
    seen: list = []
    with nofollow.directory(str(tree)) as root:
        for relative, _holder, _name in nofollow.walk_files(root, stop=lambda: len(seen) >= 2):
            seen.append(relative)

    assert seen == [str(Path("d00", "report.txt")), str(Path("d01", "report.txt"))]
