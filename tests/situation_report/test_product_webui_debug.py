from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from flocks.session.session import SessionInfo
from flocks.situation_report.product.backend_sync import BackendReportSynchronizer
from flocks.situation_report.product.webui_debug import (
    is_webui_debug_session,
    publish_webui_debug_report,
    webui_debug_enabled,
    webui_debug_metadata,
)
from scripts.situation_report_product_mock_backend import create_app


TOKEN = "debug-unit-token"


def _mock_app(tmp_path: Path):
    template = tmp_path / "template.md"
    materials = tmp_path / "materials.jsonl"
    template.write_text("# 调试模板\n\n## 摘要\n", encoding="utf-8")
    materials.write_text(
        json.dumps(
            {
                "source_type": "REPORT",
                "source_id": "debug-source-1",
                "title": {"zh": "调试素材"},
                "summary": {"zh": "用于 WebUI 调试边界测试"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return create_app(
        state_dir=tmp_path / "state",
        template=template,
        materials=materials,
        token=TOKEN,
    )


def _client_factory(app):
    return lambda: httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://debug-mock",
    )


def test_webui_debug_marker_requires_flag_and_exact_session_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SITUATION_REPORT_WEBUI_DEBUG_ENABLED", raising=False)
    session = SessionInfo(
        id="ses_debug_marker",
        projectID="prj_debug_marker",
        directory="/tmp/debug-marker",
        category="situation-report",
        metadata=webui_debug_metadata(),
    )
    assert webui_debug_enabled() is False
    assert is_webui_debug_session(session) is True

    monkeypatch.setenv("SITUATION_REPORT_WEBUI_DEBUG_ENABLED", "true")
    assert webui_debug_enabled() is True
    assert is_webui_debug_session(session.model_copy(update={"category": "user"})) is False
    assert is_webui_debug_session(session.model_copy(update={"metadata": {}})) is False


@pytest.mark.asyncio
async def test_explicit_debug_sync_and_generated_report_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _mock_app(tmp_path)
    factory = _client_factory(app)
    sync = BackendReportSynchronizer(
        factory,
        base_url="http://debug-mock",
        token=TOKEN,
    )
    latest = await sync.get_latest(
        session_id="ses_debug_flow",
        known_report_version=0,
        known_template_version=0,
        known_material_version=0,
        request_id="req-debug-state",
    )
    assert latest.report.exists is False
    assert latest.template.version == 1
    assert latest.materials.version == 1

    monkeypatch.setenv("SITUATION_REPORT_WEBUI_DEBUG_ENABLED", "true")
    monkeypatch.setenv("SITUATION_REPORT_WEBUI_DEBUG_BACKEND_BASE_URL", "http://debug-mock")
    monkeypatch.setenv("SITUATION_REPORT_WEBUI_DEBUG_BACKEND_TOKEN", TOKEN)
    report_path = tmp_path / "report.md"
    report_path.write_text("# 调试报告\n\n## 摘要\n已完成。\n", encoding="utf-8")
    version = await publish_webui_debug_report(
        session_id="ses_debug_flow",
        report_path=report_path,
        client_factory=factory,
    )
    assert version == 1

    changed = await sync.get_latest(
        session_id="ses_debug_flow",
        known_report_version=0,
        known_template_version=1,
        known_material_version=1,
        request_id="req-debug-changed",
    )
    assert changed.report.exists is True
    assert changed.report.version == 1
    assert changed.report.changed is True
