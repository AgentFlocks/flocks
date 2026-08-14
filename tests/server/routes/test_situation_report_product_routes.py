from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from httpx import AsyncClient

from flocks.auth.context import AuthUser
from flocks.config.config import Config
from flocks.project.project import Project
from flocks.server.routes.file import download_file
from flocks.server.routes.session import SessionCreateRequest
from flocks.session.session import Session
from flocks.situation_report.product.contracts import ReportAction, build_report_prompt_text
from flocks.situation_report.product.files import session_root


def _product_prompt() -> dict:
    action = ReportAction.model_validate(
        {
            "name": "situation_report.generate",
            "version": "1",
            "requestID": "req-route-001",
            "generationID": "gen-route-001",
            "language": "zh-CN",
        }
    )
    return {
        "agent": "situation-report-product",
        "parts": [
            {
                "type": "text",
                "text": build_report_prompt_text(
                    action=action,
                    user_instruction="使用后端当前模板和素材生成报告。",
                ),
            }
        ],
    }


@pytest.mark.asyncio
async def test_original_session_create_contract_is_used(client: AsyncClient):
    response = await client.post("/api/session", json={"title": "一期态势报告"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"].startswith("ses_")
    assert body["title"] == "一期态势报告"
    assert "projectID" in body
    assert set(SessionCreateRequest.model_fields).isdisjoint({"workspace", "projectKey", "sourceSessionID"})


@pytest.mark.asyncio
async def test_report_session_create_hides_internal_one_to_one_project(client: AsyncClient):
    response = await client.post(
        "/api/session",
        json={"title": "一期态势报告", "category": "situation-report"},
    )

    assert response.status_code == 200, response.text
    session = await Session.get_by_id(response.json()["id"])
    assert session is not None
    project = await Project.get(session.project_id, owner_id="api-token-service")
    assert project is not None
    assert session.category == "situation-report"
    assert session.project_id == project.id
    assert session.directory == project.worktree == str(session_root(session.id))


@pytest.mark.asyncio
async def test_prompt_async_dispatches_product_agent_on_a_managed_report_session(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post(
        "/api/session",
        json={"title": "一期态势报告", "category": "situation-report"},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    runner = AsyncMock()
    from flocks.situation_report.product import dispatch

    monkeypatch.setattr(
        dispatch,
        "run_managed_report_turn",
        runner,
    )
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=object()),
    )

    response = await client.post(
        f"/api/session/{session_id}/prompt_async",
        json=_product_prompt(),
    )
    assert response.status_code == 202, response.text
    assert response.json() == {"status": "accepted", "sessionID": session_id}
    for _ in range(10):
        if runner.await_count:
            break
        await asyncio.sleep(0)
    runner.assert_awaited_once()
    assert runner.await_args.kwargs["session"].id == session_id
    assert runner.await_args.kwargs["decision"].kind == "execute"


@pytest.mark.asyncio
async def test_product_agent_rejects_an_ordinary_session(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post("/api/session", json={"title": "普通会话"})
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=object()))

    response = await client.post(
        f"/api/session/{created.json()['id']}/prompt_async",
        json=_product_prompt(),
    )

    assert response.status_code == 409
    assert "Session is not a managed situation-report Session" in response.text


@pytest.mark.asyncio
async def test_product_agent_does_not_require_delegated_user_header(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post(
        "/api/session",
        json={"title": "无用户委托头", "category": "situation-report"},
    )
    session_id = created.json()["id"]
    runner = AsyncMock()
    from flocks.situation_report.product import dispatch

    monkeypatch.setattr(
        dispatch,
        "run_managed_report_turn",
        runner,
    )
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=object()))

    response = await client.post(
        f"/api/session/{session_id}/prompt_async",
        json=_product_prompt(),
    )

    assert response.status_code == 202, response.text
    assert "X-Flocks-User-Key" not in client.headers


@pytest.mark.asyncio
async def test_product_dispatch_rejects_non_service_flocks_identity():
    from flocks.situation_report.product.dispatch import dispatch_product_prompt

    with pytest.raises(HTTPException) as exc_info:
        await dispatch_product_prompt(
            session=SimpleNamespace(id="ses_member_denied"),
            request=SimpleNamespace(),
            event=None,
            current_user=AuthUser(id="member-1", username="member-1", role="member"),
            working_directory="/tmp",
            is_running=lambda _session_id: False,
            is_chain_active=lambda _session_id: False,
            set_chain_active=lambda _session_id, _active: None,
            schedule_background=lambda *_args, **_kwargs: None,
            generic_runner=AsyncMock(),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_original_download_endpoint_reads_product_output_by_path(client: AsyncClient):
    output = session_root("ses_download_test") / "output/versions/frv_001/report.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("# 下载测试\n", encoding="utf-8")
    assert output.is_relative_to(Config.get_data_path())

    response = await client.get("/api/file/download", params={"path": str(output)})

    assert response.status_code == 200, response.text
    assert response.content == output.read_bytes()
    assert set(inspect.signature(download_file).parameters) == {"path"}


@pytest.mark.asyncio
async def test_generic_session_prompt_path_is_unchanged(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post("/api/session", json={"title": "普通会话"})
    session_id = created.json()["id"]
    scheduled: list[object] = []

    def capture(coro, **_kwargs):
        scheduled.append(coro)

    monkeypatch.setattr("flocks.server.routes.session._schedule_background_coro", capture)
    monkeypatch.setattr(
        "flocks.server.routes.session._require_agent_usable_for_chat",
        AsyncMock(),
    )
    response = await client.post(
        f"/api/session/{session_id}/prompt_async",
        json={"agent": "rex", "parts": [{"type": "text", "text": "普通消息"}]},
    )

    assert response.status_code == 202
    assert response.json()["status"] == "accepted"
    assert len(scheduled) == 1
    scheduled[0].close()
