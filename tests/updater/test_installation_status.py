import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from flocks.updater.installation_status import MAX_RECORD_BYTES, read_installation_result


@pytest.fixture
def receipt(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    path = tmp_path / "run" / "installation-result.json"
    path.parent.mkdir()
    record = {
        "version": 1, "id": "attempt-1", "phase": "failed", "recovery": "restored",
        "messages": {
            "zh-CN": {"title": "升级未完成", "message": "原安装已恢复，可以继续使用。", "retry_hint": "修复后重新运行安装命令。"},
            "en-US": {"title": "Upgrade failed", "message": "The original installation was restored."},
        },
        "detail": "connection postgresql://user:private-password@localhost/example failed",  # secret-guard: allow - synthetic DSN redaction fixture
        "private_metadata": "must never reach the browser",
    }
    path.write_text(json.dumps(record))
    return path, record


def test_read_recovery_preserves_file_and_exposes_only_display_fields(receipt):
    path, _ = receipt
    before = path.read_bytes()
    result = read_installation_result()
    assert result["message"] == "原安装已恢复，可以继续使用。"
    assert result["recovery"] == "restored"
    assert "private-password" not in result["detail"]
    assert "private_metadata" not in result
    assert read_installation_result("en-US")["title"] == "Upgrade failed"
    assert path.read_bytes() == before


@pytest.mark.parametrize("change", [{"phase": "done"}, {"phase": "running"}, {"version": 2}, {"version": True}, {"id": None}, {"messages": None}, {"recovery": []}, {"recovery": {}}, {"recovery": 3}])
def test_completed_or_invalid_records_do_not_show_an_old_failure(receipt, change):
    path, record = receipt
    path.write_text(json.dumps({**record, **change}))
    assert read_installation_result() is None


@pytest.mark.parametrize("detail", [
    "postgresql://localhost/db?password=synthetic-pass&sslmode=disable",  # secret-guard: allow - synthetic redaction fixture
    "host=localhost password=synthetic-pass dbname=example",  # secret-guard: allow - synthetic redaction fixture
    "host=localhost password='synthetic pass' dbname=example",  # secret-guard: allow - synthetic redaction fixture
])
def test_every_display_field_redacts_connection_credentials(receipt, detail):
    path, record = receipt
    record["detail"] = detail
    record["messages"]["zh-CN"] = {key: detail for key in ["title", "message", "retry_hint"]}
    path.write_text(json.dumps(record))
    result = read_installation_result()
    assert result is not None
    for key in ["title", "message", "retry_hint", "detail"]:
        assert "synthetic-pass" not in result[key]
        assert "synthetic pass" not in result[key]
        assert "***" in result[key]


def test_absent_record_does_not_change_an_open_install(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOCKS_ROOT", str(tmp_path))
    assert read_installation_result() is None
    assert not (tmp_path / "run").exists()


def test_oversized_record_is_ignored(receipt):
    path, _ = receipt
    path.write_text(" " * (MAX_RECORD_BYTES + 1))
    assert read_installation_result() is None


def test_a_symlink_cannot_publish_an_unrelated_file(receipt):
    path, _ = receipt
    retained = path.with_suffix(".retained")
    path.rename(retained)
    path.symlink_to(retained)
    assert read_installation_result() is None


async def test_installation_result_requires_admin_before_reading(monkeypatch):
    from flocks.server.routes import update
    from flocks.updater import installation_status

    def deny(_request):
        raise HTTPException(status_code=403)

    monkeypatch.setattr(update, "require_admin", deny)
    monkeypatch.setattr(installation_status, "read_installation_result", lambda _locale: pytest.fail("must authenticate before reading"))
    with pytest.raises(HTTPException) as error:
        await update.installation_result(Request({"type": "http", "headers": []}))
    assert error.value.status_code == 403
