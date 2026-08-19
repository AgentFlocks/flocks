from __future__ import annotations

import asyncio
import hashlib
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
from flocks.situation_report.product.orchestrator import persist_terminal_status_message


def _product_prompt(*, operation: str = "generate", instruction: str | None = None) -> dict:
    action_value: dict[str, object] = {
        "name": f"situation_report.{operation}",
        "version": "1",
        "requestID": f"req-route-{operation}",
        "generationID": f"gen-route-{operation}",
    }
    if operation == "generate":
        action_value["language"] = "zh-CN"
    else:
        action_value["baseBackendReportVersion"] = 1
    action = ReportAction.model_validate(action_value)
    return {
        "agent": "situation-report-product",
        "parts": [
            {
                "type": "text",
                "text": build_report_prompt_text(
                    action=action,
                    user_instruction=(
                        instruction
                        or "使用后端当前模板和素材生成报告。"
                    ),
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
    assert runner.await_args.kwargs["generic_runner"].__name__ == "_run_managed_product_event_chain"


@pytest.mark.asyncio
async def test_conversation_rewrite_text_dispatches_as_modify(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post(
        "/api/session",
        json={"title": "对话修改边界", "category": "situation-report"},
    )
    assert created.status_code == 200
    session_id = created.json()["id"]

    runner = AsyncMock()
    from flocks.situation_report.product import dispatch

    monkeypatch.setattr(dispatch, "run_managed_report_turn", runner)
    monkeypatch.setattr("flocks.agent.registry.Agent.get", AsyncMock(return_value=object()))

    response = await client.post(
        f"/api/session/{session_id}/prompt_async",
        json=_product_prompt(operation="modify", instruction="请重新生成整份报告。"),
    )

    assert response.status_code == 202, response.text
    for _ in range(10):
        if runner.await_count:
            break
        await asyncio.sleep(0)
    runner.assert_awaited_once()
    decision = runner.await_args.kwargs["decision"]
    assert decision.kind == "execute"
    assert decision.prompt.action.operation == "modify"


@pytest.mark.asyncio
async def test_hidden_product_agent_is_only_allowed_by_internal_runner(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import session as session_routes

    hidden_agent = SimpleNamespace(hidden=True, tags=[], mode="primary", delegatable=False)
    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=hidden_agent),
    )

    with pytest.raises(HTTPException) as exc_info:
        await session_routes._require_agent_usable_for_chat("situation-report-product")
    assert exc_info.value.status_code == 400

    await session_routes._require_agent_usable_for_chat(
        "situation-report-product",
        internal_agent_name="situation-report-product",
    )


@pytest.mark.asyncio
async def test_managed_product_runner_rejects_unprepared_internal_events(
    monkeypatch: pytest.MonkeyPatch,
):
    from flocks.server.routes import session as session_routes

    inner = AsyncMock()
    monkeypatch.setattr(session_routes, "_run_prompt_event_chain", inner)
    prepared = SimpleNamespace(
        agent="situation-report-product",
        metadata={"situationReport": {"generationID": "gen-internal", "operation": "generate"}},
    )
    await session_routes._run_managed_product_event_chain("ses-internal", object(), prepared, "/tmp")
    assert inner.await_args.kwargs["internal_agent_name"] == "situation-report-product"

    unprepared = SimpleNamespace(agent="situation-report-product", metadata={})
    with pytest.raises(HTTPException) as exc_info:
        await session_routes._run_managed_product_event_chain("ses-internal", object(), unprepared, "/tmp")
    assert exc_info.value.status_code == 403


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
async def test_terminal_report_status_is_available_from_message_api(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
):
    created = await client.post(
        "/api/session",
        json={"title": "终态消息查询", "category": "situation-report"},
    )
    assert created.status_code == 200
    session = await Session.get_by_id(created.json()["id"])
    assert session is not None
    monkeypatch.setattr(
        "flocks.situation_report.product.orchestrator.publish_event",
        AsyncMock(),
    )
    output_path = session_root(session.id) / "output/versions/frv_001/report.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_content = "# 终态消息查询报告\n".encode()
    output_path.write_bytes(output_content)

    linked = await persist_terminal_status_message(
        session=session,
        generation_id="gen-route-terminal",
        parent_message_id="req-route-terminal",
        payload={
            "requestID": "req-route-terminal",
            "operation": "generate",
            "status": "succeeded",
            "stage": "output_ready",
            "progress": 100,
            "flocksReportVersion": "frv_001",
            "output": {
                "path": str(output_path),
                "format": "markdown",
                "sizeBytes": len(output_content),
                "sha256": hashlib.sha256(output_content).hexdigest(),
                "downloadAPI": "/api/file/download",
            },
            "error": None,
        },
        expected_generation=Session.lifecycle_generation(session.id),
    )

    response = await client.get(
        f"/api/session/{session.id}/message/{linked['messageID']}"
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["info"]["modelID"] == "situation-report-runtime"
    assert body["parts"] == [
        {
            "id": linked["messagePartID"],
            "messageID": linked["messageID"],
            "sessionID": session.id,
            "type": "text",
            "text": "报告已生成并发布（版本：frv_001）。",
            "time": None,
            "synthetic": None,
            "tool": None,
            "state": None,
            "callID": None,
            "metadata": {
                "situationReport": {
                    "kind": "terminal_status",
                    **linked,
                }
            },
            "url": None,
            "mime": None,
            "filename": None,
        }
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "error", "expected_text"),
    [
        ("failed", {"code": "RuntimeError", "message": "模型调用失败"}, "报告生成失败：模型调用失败"),
        ("cancelled", None, "报告生成已取消。"),
    ],
)
async def test_non_success_terminal_report_status_is_persisted(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    error: dict[str, str] | None,
    expected_text: str,
):
    created = await client.post(
        "/api/session",
        json={"title": f"{status} 终态消息", "category": "situation-report"},
    )
    assert created.status_code == 200
    session = await Session.get_by_id(created.json()["id"])
    assert session is not None
    monkeypatch.setattr(
        "flocks.situation_report.product.orchestrator.publish_event",
        AsyncMock(),
    )

    linked = await persist_terminal_status_message(
        session=session,
        generation_id=f"gen-route-{status}",
        parent_message_id=f"req-route-{status}",
        payload={
            "requestID": f"req-route-{status}",
            "operation": "generate",
            "status": status,
            "stage": status,
            "progress": 100,
            "flocksReportVersion": None,
            "output": None,
            "error": error,
        },
        expected_generation=Session.lifecycle_generation(session.id),
    )

    response = await client.get(
        f"/api/session/{session.id}/message/{linked['messageID']}"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["parts"][0]["text"] == expected_text
    report_metadata = body["parts"][0]["metadata"]["situationReport"]
    assert report_metadata["status"] == status
    assert report_metadata["error"] == error


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
