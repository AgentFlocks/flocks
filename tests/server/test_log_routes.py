"""Log route helpers."""

from pathlib import Path
from datetime import date

import pytest
from fastapi import HTTPException

from flocks.server.routes import logs as log_routes


def test_read_log_file_tails_without_reading_entire_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")

    def fail_read_text(*args, **kwargs):
        raise AssertionError("read_text should not be used for tail reads")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    response = log_routes._read_log_file(log_path, tail=2)

    assert response.filename == "backend.log"
    assert response.content == "three\nfour"
    assert response.total_lines == 4
    assert response.truncated is True


def test_read_log_file_reports_untruncated_small_file(tmp_path: Path) -> None:
    log_path = tmp_path / "backend.log"
    log_path.write_text("one\ntwo\n", encoding="utf-8")

    response = log_routes._read_log_file(log_path, tail=5)

    assert response.content == "one\ntwo"
    assert response.total_lines == 2
    assert response.truncated is False


@pytest.mark.asyncio
async def test_list_logs_includes_root_and_date_log_files(tmp_path: Path, monkeypatch) -> None:
    today = date.today().isoformat()
    day_dir = tmp_path / today
    day_dir.mkdir()
    (tmp_path / "backend.log").write_text("backend\n", encoding="utf-8")
    (tmp_path / "webui.log").write_text("webui\n", encoding="utf-8")
    (day_dir / "flocks.log").write_text("main\n", encoding="utf-8")
    (day_dir / "errors.log").write_text("errors\n", encoding="utf-8")
    (tmp_path / "flocks.log.1").write_text("rotated\n", encoding="utf-8")
    (tmp_path / "not-a-log.txt").write_text("ignore\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    response = await log_routes.list_logs()

    names = {item.name for item in response.files}
    assert "backend.log" in names
    assert "webui.log" in names
    assert f"{today}/flocks.log" in names
    assert f"{today}/errors.log" in names
    assert "flocks.log.1" not in names
    assert "not-a-log.txt" not in names


@pytest.mark.asyncio
async def test_latest_log_prefers_main_flocks_log(tmp_path: Path, monkeypatch) -> None:
    today = date.today().isoformat()
    day_dir = tmp_path / today
    day_dir.mkdir()
    (tmp_path / "backend.log").write_text("backend\n", encoding="utf-8")
    (day_dir / "flocks.log").write_text("main\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    response = await log_routes.read_latest_log(tail=10)

    assert response.filename == f"{today}/flocks.log"
    assert response.content == "main"


@pytest.mark.asyncio
async def test_read_log_allows_daily_log_files(tmp_path: Path, monkeypatch) -> None:
    today = date.today().isoformat()
    day_dir = tmp_path / today
    day_dir.mkdir()
    (day_dir / "flocks.log").write_text("main\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    response = await log_routes.read_log(f"{today}/flocks.log", tail=10)

    assert response.filename == f"{today}/flocks.log"
    assert response.content == "main"


@pytest.mark.asyncio
async def test_read_log_rejects_rotated_suffix_files(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "backend.log.1").write_text("rotated\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        await log_routes.read_log("backend.log.1", tail=10)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_read_log_returns_same_nested_filename_as_list(tmp_path: Path, monkeypatch) -> None:
    today = date.today().isoformat()
    day_dir = tmp_path / today
    day_dir.mkdir()
    (day_dir / "errors.log").write_text("warn\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    listed = await log_routes.list_logs()
    listed_name = next(item.name for item in listed.files if item.name.endswith("errors.log"))
    response = await log_routes.read_log(listed_name, tail=10)

    assert listed_name == f"{today}/errors.log"
    assert response.filename == listed_name
    assert response.content == "warn"


def test_read_log_file_reads_only_the_tail_of_a_large_file(tmp_path: Path, monkeypatch) -> None:
    """The tail comes from block reads at the end of the file, so the bytes
    actually read for it stay bounded by the tail size, not the file size."""
    log_path = tmp_path / "backend.log"
    line = "x" * 99 + "\n"
    with log_path.open("w", encoding="utf-8") as handle:
        for _ in range(200_000):  # ~20 MB
            handle.write(line)
    log_path.with_name("marker").write_text("")

    read_bytes = {"n": 0}
    original_read = log_routes.Path.open

    class _CountingFile:
        def __init__(self, inner):
            self._inner = inner

        def read(self, n=-1):
            data = self._inner.read(n)
            read_bytes["n"] += len(data)
            return data

        def __getattr__(self, name):
            return getattr(self._inner, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._inner.__exit__(*exc)

    def counting_open(self, mode="r", *args, **kwargs):
        handle = original_read(self, mode, *args, **kwargs)
        return _CountingFile(handle) if mode == "rb" and self == log_path else handle

    monkeypatch.setattr(log_routes, "_count_lines", lambda path: 200_000)  # count is separately bounded
    monkeypatch.setattr(log_routes.Path, "open", counting_open)

    response = log_routes._read_log_file(log_path, tail=200)

    assert response.total_lines == 200_000 and response.truncated is True
    assert response.content.count("\n") == 199 and response.content.endswith("x" * 99)
    assert read_bytes["n"] < 4 * 64 * 1024, read_bytes


def test_read_log_file_matches_text_mode_line_semantics(tmp_path: Path) -> None:
    for text in ("", "\n", "a", "a\n", "a\nb", "a\nb\n", "one\r\ntwo\r\n"):
        log_path = tmp_path / "backend.log"
        log_path.write_bytes(text.encode())
        expected_lines = [line.rstrip("\r\n") for line in text.splitlines(keepends=True)]
        response = log_routes._read_log_file(log_path, tail=5)
        assert response.total_lines == len(expected_lines), text
        assert response.content == "\n".join(expected_lines), text


@pytest.mark.asyncio
async def test_read_log_rejects_non_zero_padded_date_dirs(tmp_path: Path, monkeypatch) -> None:
    """Only ``YYYY-MM-DD`` directories are log directories; ``2026-9-13`` is
    not one even though ``strptime`` would parse it."""
    (tmp_path / "2026-9-13").mkdir()
    (tmp_path / "2026-9-13" / "flocks.log").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(log_routes, "get_log_dir", lambda: tmp_path)

    listing = await log_routes.list_logs()
    assert [f.name for f in listing.files] == []
    with pytest.raises(HTTPException) as exc_info:
        await log_routes.read_log("2026-9-13/flocks.log", tail=10)
    assert exc_info.value.status_code == 404
