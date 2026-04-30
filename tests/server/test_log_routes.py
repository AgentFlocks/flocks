"""Log route helpers."""

from pathlib import Path

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
