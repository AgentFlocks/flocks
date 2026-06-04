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
