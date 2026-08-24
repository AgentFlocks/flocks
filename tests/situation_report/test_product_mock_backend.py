from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from scripts.situation_report_product_mock_backend import create_app


TOKEN = "unit-test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}", "X-Request-ID": "req-mock-001"}


@pytest.fixture
def contract_inputs(tmp_path: Path) -> tuple[Path, Path]:
    template = tmp_path / "contract-template.md"
    materials = tmp_path / "contract-materials.jsonl"
    template.write_text("# 契约测试模板\n\n## 摘要\n\n## 事件\n", encoding="utf-8")
    records = [
        {
            "pirs_id": "1",
            "source_type": "REPORT",
            "source_id": "contract-material-001",
            "title": {"zh": "契约素材一", "en": None, "original": "契约素材一"},
            "summary": {"zh": "仅用于 HTTP 契约单元测试", "en": None, "original": "仅用于 HTTP 契约单元测试"},
        },
        {
            "pirs_id": "1",
            "source_type": "VULN",
            "source_id": "contract-material-002",
            "title": {"zh": "契约素材二", "en": None, "original": "契约素材二"},
            "summary": {"zh": "不用于报告流程或效果结论", "en": None, "original": "不用于报告流程或效果结论"},
        },
    ]
    materials.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return template, materials


@pytest.mark.asyncio
async def test_mock_serves_seed_snapshots_and_tracks_versions(
    tmp_path: Path,
    contract_inputs: tuple[Path, Path],
) -> None:
    template, materials = contract_inputs
    app = create_app(
        state_dir=tmp_path / "state",
        template=template,
        materials=materials,
        token=TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as client:
        latest = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/state/latest",
            params={
                "knownReportVersion": 0,
                "knownTemplateVersion": 0,
                "knownMaterialVersion": 0,
            },
            headers=HEADERS,
        )
        assert latest.status_code == 200, latest.text
        body = latest.json()
        assert latest.headers["X-Request-ID"] == HEADERS["X-Request-ID"]
        assert body["sessionId"] == "ses_mock_flow"
        assert body["report"] == {"exists": False, "version": None, "changed": False}
        assert set(body["template"]) == {"exists", "version", "changed"}
        assert set(body["materials"]) == {"exists", "version", "changed"}
        assert body["template"]["changed"] is True
        assert body["materials"]["changed"] is True

        for resource, source in (("template", template), ("materials", materials)):
            downloaded = await client.get(
                f"/internal/flocks/v1/report-sessions/ses_mock_flow/{resource}/download",
                headers=HEADERS,
            )
            assert downloaded.status_code == 200
            assert downloaded.headers["X-Request-ID"] == HEADERS["X-Request-ID"]
            assert downloaded.content == source.read_bytes()

        unchanged = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/state/latest",
            params={
                "knownReportVersion": 0,
                "knownTemplateVersion": 1,
                "knownMaterialVersion": 1,
            },
            headers=HEADERS,
        )
        assert unchanged.status_code == 200
        assert unchanged.json()["template"]["changed"] is False

        report = "# Mock 契约测试结果\n\n## 摘要\n仅验证报告资源版本流转。\n".encode()
        imported = await client.put(
            "/__mock__/report-sessions/ses_mock_flow/resources/report",
            params={"version": 1},
            headers=HEADERS,
            content=report,
        )
        assert imported.status_code == 200, imported.text

        changed = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/state/latest",
            params={
                "knownReportVersion": 0,
                "knownTemplateVersion": 1,
                "knownMaterialVersion": 1,
            },
            headers=HEADERS,
        )
        assert changed.status_code == 200
        report_state = changed.json()["report"]
        assert report_state["changed"] is True
        assert report_state["version"] == 1
        downloaded = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/report/download",
            headers=HEADERS,
        )
        assert downloaded.headers["X-Report-Version"] == "1"
        assert downloaded.content == report


@pytest.mark.asyncio
async def test_mock_requires_auth_and_rejects_version_overwrite(
    tmp_path: Path,
    contract_inputs: tuple[Path, Path],
) -> None:
    template, materials = contract_inputs
    app = create_app(
        state_dir=tmp_path / "state",
        template=template,
        materials=materials,
        token=TOKEN,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock") as client:
        unauthenticated = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/state/latest",
            params={
                "knownReportVersion": 0,
                "knownTemplateVersion": 0,
                "knownMaterialVersion": 0,
            },
            headers={"X-Request-ID": "req-mock-002"},
        )
        assert unauthenticated.status_code == 401

        replacement = template.read_bytes() + b"\n<!-- mock template v2 -->\n"
        first = await client.put(
            "/__mock__/report-sessions/ses_mock_flow/resources/template",
            params={"version": 2},
            headers=HEADERS,
            content=replacement,
        )
        assert first.status_code == 200
        overwrite = await client.put(
            "/__mock__/report-sessions/ses_mock_flow/resources/template",
            params={"version": 2},
            headers=HEADERS,
            content=replacement,
        )
        assert overwrite.status_code == 409

        missing_request_id = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/template/download",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert missing_request_id.status_code == 400

        empty_materials = await client.put(
            "/__mock__/report-sessions/ses_mock_flow/resources/materials",
            params={"version": 2},
            headers=HEADERS,
            content=b"",
        )
        assert empty_materials.status_code == 200
        downloaded_empty = await client.get(
            "/internal/flocks/v1/report-sessions/ses_mock_flow/materials/download",
            headers=HEADERS,
        )
        assert downloaded_empty.status_code == 200
        assert downloaded_empty.headers["X-Material-Version"] == "2"
        assert downloaded_empty.content == b""
