from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from flocks.input.events import UserInputEvent
from flocks.session.message import Message
from flocks.session.session import Session, SessionInfo
from flocks.situation_report.product.backend_sync import (
    BackendReportSyncError,
    BackendReportSynchronizer,
    LatestResource,
    initialize_report_action,
)
from flocks.situation_report.product.contracts import (
    ReportAction,
    build_report_prompt_text,
    parse_report_prompt_parts,
)
from flocks.situation_report.product.events import publish_report_status
from flocks.situation_report.product.files import session_root
from flocks.situation_report.product.output import publish_validated_candidate
from flocks.situation_report.product.orchestrator import PRODUCTION_AGENT, run_managed_report_turn
from flocks.situation_report.product.policy import ReportPolicyDecision
from flocks.situation_report.product.session_state import load_session_state
from flocks.situation_report.product.workspace import (
    _template_h2,
    read_generation_context,
    read_material_page,
    validate_candidate_report,
    write_candidate_report,
)


SESSION_ID = "ses_product_runtime"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _action(operation: str, generation_id: str, *, base_version: int | None = None) -> ReportAction:
    value: dict[str, object] = {
        "name": f"situation_report.{operation}",
        "version": "1",
        "requestID": f"req-{generation_id}",
        "generationID": generation_id,
    }
    if operation == "generate":
        value["language"] = "zh-CN"
    else:
        value["baseBackendReportVersion"] = base_version
    return ReportAction.model_validate(value)


def _prompt(operation: str, generation_id: str, *, base_version: int | None = None):
    text = build_report_prompt_text(
        action=_action(operation, generation_id, base_version=base_version),
        user_instruction={
            "generate": "使用当前配置生成中文态势报告。",
            "modify": "修改当前报告的行动建议。",
            "regenerate": "请重新生成当前报告。",
        }[operation],
    )
    return parse_report_prompt_parts([{"type": "text", "text": text}])


def _missing() -> dict:
    return {"exists": False, "version": None, "changed": False}


def _missing_with_zero_version() -> dict:
    return {"exists": False, "version": 0, "changed": False}


def test_missing_resource_normalizes_zero_version_sentinel() -> None:
    assert LatestResource.model_validate(_missing()).model_dump() == _missing()
    assert LatestResource.model_validate(_missing_with_zero_version()).model_dump() == _missing()


@pytest.mark.parametrize(
    "value",
    [
        {"exists": False, "version": 1, "changed": False},
        {"exists": False, "version": 0, "changed": True},
    ],
)
def test_missing_resource_rejects_conflicting_state(value: dict) -> None:
    with pytest.raises(ValidationError):
        LatestResource.model_validate(value)


def _changed(
    *,
    version: int,
    content: bytes,
    url: str,
    snapshot_key: str | None = None,
    snapshot_id: str | None = None,
    format: str | None = None,
) -> dict:
    del content, url, snapshot_key, snapshot_id, format
    return {"exists": True, "changed": True, "version": version}


def _unchanged(
    *,
    version: int,
    content: bytes,
    snapshot_key: str | None = None,
    snapshot_id: str | None = None,
    format: str | None = None,
) -> dict:
    del content, snapshot_key, snapshot_id, format
    return {"exists": True, "changed": False, "version": version}


def _state_response(
    request: httpx.Request,
    *,
    report: dict,
    template: dict,
    materials: dict,
) -> httpx.Response:
    return httpx.Response(
        200,
        headers={"X-Request-ID": request.headers["X-Request-ID"]},
        json={
            "sessionId": SESSION_ID,
            "report": report,
            "template": template,
            "materials": materials,
        },
    )


def _download_response(
    request: httpx.Request,
    *,
    resource: str,
    version: int,
    content: bytes,
) -> httpx.Response:
    header = {
        "report": "X-Report-Version",
        "template": "X-Template-Version",
        "materials": "X-Material-Version",
    }[resource]
    media_type = "application/x-ndjson" if resource == "materials" else "text/markdown"
    return httpx.Response(
        200,
        headers={
            "X-Request-ID": request.headers["X-Request-ID"],
            header: str(version),
            "Content-Type": media_type,
        },
        content=content,
    )


def _contract_materials(version: int) -> bytes:
    rows = [
        {
            "pirs_id": "contract-pirs",
            "source_type": "REPORT",
            "source_id": f"contract-report-v{version}",
            "score": 80,
            "relevance_percent": 80,
            "tier": "TIER1",
            "matched_factors": [],
            "source_updated_at": 1787280000000,
            "title": {"zh": f"契约报告素材 V{version}", "en": None, "original": f"契约报告素材 V{version}"},
            "summary": {
                "zh": "仅验证产品运行时，不代表报告效果。",
                "en": None,
                "original": "仅验证产品运行时，不代表报告效果。",
            },
            "published_at": 1787280000000,
            "content_updated_at": 1787280000000,
            "report": {"threatbook_lab": True, "severity": "high", "tags": ["contract"]},
            "vulnerability": None,
            "darkweb": None,
            "telegram": None,
            "saved": False,
        },
        {
            "pirs_id": "contract-pirs",
            "source_type": "VULN",
            "source_id": f"CVE-2026-CONTRACT-{version}",
            "score": 70,
            "relevance_percent": 70,
            "tier": "TIER2",
            "matched_factors": [],
            "source_updated_at": 1787280000000,
            "title": {"zh": f"契约漏洞素材 V{version}", "en": None, "original": f"契约漏洞素材 V{version}"},
            "summary": {"zh": "仅验证版本切换和素材引用。", "en": None, "original": "仅验证版本切换和素材引用。"},
            "published_at": 1787280000000,
            "content_updated_at": 1787280000000,
            "report": None,
            "vulnerability": {"score": 9.8, "risk_level": "HIGH", "tags": ["contract"]},
            "darkweb": None,
            "telegram": None,
            "saved": False,
        },
    ]
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


def _material_id(value: dict) -> str:
    return f"{value['source_type']}:{value['source_id']}"


def _valid_report(template: bytes, materials: bytes) -> str:
    material_ids = [_material_id(json.loads(line)) for line in materials.decode("utf-8").splitlines() if line.strip()]
    lines = ["全部已校验素材：" + "、".join(material_ids)]
    for heading in _template_h2(template.decode("utf-8")):
        lines.extend([f"## {heading}", "本节根据已校验素材生成。"])
    return "\n".join(lines)


@pytest.fixture
def real_inputs() -> tuple[bytes, bytes, bytes]:
    template = "# {{report_title}}\n\n## 摘要\n\n## 重点事件\n\n## 建议\n".encode("utf-8")
    materials_v1 = _contract_materials(1)
    materials_v2 = _contract_materials(2)
    return template, materials_v1, materials_v2


@pytest.fixture
def product_session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionInfo:
    monkeypatch.setenv("SITUATION_REPORT_PRODUCT_ROOT", str(tmp_path / "product"))
    monkeypatch.setenv("SITUATION_REPORT_BACKEND_BASE_URL", "https://backend.example")
    monkeypatch.setenv("SITUATION_REPORT_BACKEND_TOKEN", "backend-token")
    session = SessionInfo(
        id=SESSION_ID,
        projectID="prj_product_runtime",
        directory=str(session_root(SESSION_ID)),
        title="态势报告流程测试",
        ownerUserID="api-token-service",
        ownerUsername="api-token-service",
        category="situation-report",
    )
    monkeypatch.setattr(Session, "get_by_id", AsyncMock(return_value=session))
    return session


@pytest.mark.asyncio
async def test_generate_uses_original_session_id_and_backend_latest(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials, _ = real_inputs
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert "X-Flocks-User-Key" not in request.headers
        assert request.headers["Authorization"] == "Bearer backend-token"
        if request.url.path.endswith("/state/latest"):
            assert dict(request.url.params) == {
                "knownReportVersion": "0",
                "knownTemplateVersion": "0",
                "knownMaterialVersion": "0",
            }
            return _state_response(
                request,
                report=_missing_with_zero_version(),
                template=_changed(
                    version=1,
                    content=template,
                    url="/downloads/template-v1",
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-001",
                    format="markdown",
                ),
                materials=_changed(
                    version=1,
                    content=materials,
                    url="/downloads/materials-v1",
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-001",
                    format="jsonl",
                ),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    prompt = _prompt("generate", "gen-001")
    context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=prompt,
        synchronizer=sync,
    )

    root = session_root(product_session.id)
    assert context_path.is_relative_to(root)
    assert root.parent.name == "projects"
    assert root.name == product_session.id
    assert not (root.parent.parent / "users").exists()
    state = load_session_state(product_session.id)
    assert state.session_id == product_session.id
    assert state.report_state is not None
    assert state.report_state["templateVersion"] == 1
    assert state.report_state["materialVersion"] == 1
    assert state.report_state["observedBackendReportVersion"] == 0

    context = await read_generation_context(
        session_id=product_session.id,
        generation_id="gen-001",
    )
    first_page = await read_material_page(
        session_id=product_session.id,
        generation_id="gen-001",
        limit=50,
    )
    assert context["language"] == "zh-CN"
    assert "reportTitle" not in context
    assert context["validationPolicy"]["reportTitleAllowed"] is False
    assert context["validationPolicy"]["h1Count"] == 0
    assert first_page["total"] == 2
    expected_materials = [
        {**json.loads(line), "material_id": _material_id(json.loads(line))} for line in materials.splitlines()
    ]
    assert first_page["materials"] == expected_materials

    request_count = len(requests)
    replay = await initialize_report_action(
        session_id=product_session.id,
        prompt=prompt,
        synchronizer=sync,
    )
    assert replay == context_path
    assert len(requests) == request_count

    report = _valid_report(template, materials)
    titled_write = await write_candidate_report(
        session_id=product_session.id,
        generation_id="gen-001",
        content="# 不应出现在报告正文中的标题\n\n" + report,
    )
    titled_validation = await validate_candidate_report(
        session_id=product_session.id,
        generation_id="gen-001",
    )
    assert titled_validation["status"] == "needs_revision"
    assert titled_validation["issues"] == [
        {
            "code": "report_title_forbidden",
            "detail": "Report-level H1 headings are not allowed",
        }
    ]
    await write_candidate_report(
        session_id=product_session.id,
        generation_id="gen-001",
        content=report,
        expected_sha256=titled_write["sha256"],
    )
    assert (
        await validate_candidate_report(
            session_id=product_session.id,
            generation_id="gen-001",
        )
    )["status"] == "passed"
    published = await publish_validated_candidate(
        session_id=product_session.id,
        generation_id="gen-001",
    )
    output = root / published["output"]["path"]
    assert output.is_file()
    published_report = output.read_text(encoding="utf-8")
    assert published_report.startswith("全部已校验素材：")
    assert not any(line.startswith("# ") for line in published_report.splitlines())


@pytest.mark.asyncio
async def test_validator_only_rejects_the_actual_internal_candidate_path(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    """A normal URL segment such as network/ is not an internal path leak."""
    template, materials, _ = real_inputs

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            return _state_response(
                request,
                report=_missing(),
                template=_changed(version=1, content=template, url="/template-v1"),
                materials=_changed(version=1, content=materials, url="/materials-v1"),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    generation_id = "gen-validator-path"
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", generation_id),
        synchronizer=sync,
    )

    valid_with_network_url = (
        _valid_report(template, materials)
        + "\n\n参考链接：https://security.example/network/advisory\n"
    )
    initial_write = await write_candidate_report(
        session_id=product_session.id,
        generation_id=generation_id,
        content=valid_with_network_url,
    )
    first_validation = await validate_candidate_report(
        session_id=product_session.id,
        generation_id=generation_id,
    )
    assert first_validation["status"] == "passed"

    leaked_path = f"work/{generation_id}/report.md"
    await write_candidate_report(
        session_id=product_session.id,
        generation_id=generation_id,
        content=valid_with_network_url + f"\n内部路径：{leaked_path}\n",
        expected_sha256=initial_write["sha256"],
    )
    second_validation = await validate_candidate_report(
        session_id=product_session.id,
        generation_id=generation_id,
    )
    assert second_validation["status"] == "needs_revision"
    assert second_validation["issues"] == [
        {"code": "internal_path_leakage", "markers": [leaked_path]}
    ]


@pytest.mark.asyncio
async def test_generate_allows_new_attempt_until_initial_report_is_published(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials, _ = real_inputs
    latest_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            latest_requests.append(request)
            known_template_version = int(request.url.params["knownTemplateVersion"])
            known_material_version = int(request.url.params["knownMaterialVersion"])
            return _state_response(
                request,
                report=_missing(),
                template=(
                    _changed(
                        version=1,
                        content=template,
                        url="/template-v1",
                        snapshot_key="templateSnapshotID",
                        snapshot_id="ts-001",
                        format="markdown",
                    )
                    if known_template_version == 0
                    else _unchanged(
                        version=1,
                        content=template,
                        snapshot_key="templateSnapshotID",
                        snapshot_id="ts-001",
                        format="markdown",
                    )
                ),
                materials=(
                    _changed(
                        version=1,
                        content=materials,
                        url="/materials-v1",
                        snapshot_key="materialSnapshotID",
                        snapshot_id="ms-001",
                        format="jsonl",
                    )
                    if known_material_version == 0
                    else _unchanged(
                        version=1,
                        content=materials,
                        snapshot_key="materialSnapshotID",
                        snapshot_id="ms-001",
                        format="jsonl",
                    )
                ),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-failed-001"),
        synchronizer=sync,
    )

    changed_instruction = parse_report_prompt_parts(
        [
            {
                "type": "text",
                "text": build_report_prompt_text(
                    action=_action("generate", "gen-retry-002"),
                    user_instruction="重新生成，并使报告更加简洁。",
                ),
            }
        ]
    )
    retry_context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=changed_instruction,
        synchronizer=sync,
    )
    retry_context = json.loads(retry_context_path.read_text(encoding="utf-8"))
    state_before_publish = load_session_state(product_session.id).report_state

    assert retry_context["generationID"] == "gen-retry-002"
    assert retry_context["userInstruction"] == "重新生成，并使报告更加简洁。"
    assert state_before_publish is not None
    assert state_before_publish["firstGenerationID"] == "gen-failed-001"
    assert "currentFlocksReportVersion" not in state_before_publish
    assert len(latest_requests) == 2

    await write_candidate_report(
        session_id=product_session.id,
        generation_id="gen-retry-002",
        content=_valid_report(template, materials),
    )
    validation = await validate_candidate_report(
        session_id=product_session.id,
        generation_id="gen-retry-002",
    )
    assert validation["status"] == "passed"
    await publish_validated_candidate(
        session_id=product_session.id,
        generation_id="gen-retry-002",
    )

    with pytest.raises(
        BackendReportSyncError,
        match="generate is not valid after the Session's initial report was published",
    ):
        await initialize_report_action(
            session_id=product_session.id,
            prompt=_prompt("generate", "gen-after-success-003"),
            synchronizer=sync,
        )

    assert len(latest_requests) == 2


@pytest.mark.asyncio
async def test_a1_orchestrator_runs_preflight_publish_and_event_end_to_end(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
):
    template, materials, _ = real_inputs
    wire_text = build_report_prompt_text(
        action=_action("generate", "gen-e2e"),
        user_instruction="使用当前配置生成中文态势报告。",
    )
    parsed = parse_report_prompt_parts([{"type": "text", "text": wire_text}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            return _state_response(
                request,
                report=_missing(),
                template=_changed(
                    version=1,
                    content=template,
                    url="/template-e2e",
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-e2e",
                    format="markdown",
                ),
                materials=_changed(
                    version=1,
                    content=materials,
                    url="/materials-e2e",
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-e2e",
                    format="jsonl",
                ),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    published_events: list[tuple[str, dict]] = []

    async def capture_event(event_type: str, properties: dict) -> None:
        published_events.append((event_type, properties))

    monkeypatch.setattr("flocks.situation_report.product.events.publish_event", capture_event)
    monkeypatch.setattr("flocks.situation_report.product.orchestrator.publish_event", capture_event)
    monkeypatch.setattr(
        "flocks.situation_report.product.orchestrator._raise_persisted_agent_error",
        AsyncMock(),
    )

    async def deterministic_a1_boundary(
        session_id: str,
        _session: SessionInfo,
        prepared: UserInputEvent,
        _working_directory: str,
    ) -> None:
        assert prepared.agent == PRODUCTION_AGENT
        assert prepared.metadata["situationReport"] == {
            "generationID": "gen-e2e",
            "operation": "generate",
        }
        await write_candidate_report(
            session_id=session_id,
            generation_id="gen-e2e",
            content=_valid_report(template, materials),
        )
        validation = await validate_candidate_report(
            session_id=session_id,
            generation_id="gen-e2e",
        )
        assert validation["status"] == "passed"

    await run_managed_report_turn(
        session=product_session,
        event=UserInputEvent(
            source_type="webui",
            sessionID=product_session.id,
            text=wire_text,
            parts=[{"type": "text", "text": wire_text}],
            agent=PRODUCTION_AGENT,
            metadata={},
        ),
        decision=ReportPolicyDecision(kind="execute", prompt=parsed),
        working_directory=product_session.directory,
        generic_runner=deterministic_a1_boundary,
        backend_synchronizer=sync,
    )

    statuses = [value for event_type, value in published_events if event_type == "situation.report.status"]
    assert [value["status"] for value in statuses] == [
        "running",
        "running",
        "running",
        "succeeded",
    ]
    assert [value["stage"] for value in statuses] == [
        "downloading_resources",
        "generating",
        "validating",
        "output_ready",
    ]
    assert [value["progress"] for value in statuses] == [5, 20, 90, 100]
    output = statuses[-1]["output"]
    assert Path(output["path"]).is_absolute()
    assert Path(output["path"]).read_bytes()
    assert _sha256(Path(output["path"]).read_bytes()) == output["sha256"]
    terminal = statuses[-1]
    assert terminal["messageID"].startswith("msg_")
    assert terminal["messagePartID"].startswith("prt_")
    result_message = await Message.get_with_parts(product_session.id, terminal["messageID"])
    assert result_message is not None
    assert result_message.info.parentID == "req-gen-e2e"
    assert result_message.info.modelID == "situation-report-runtime"
    assert len(result_message.parts) == 1
    result_part = result_message.parts[0]
    assert result_part.id == terminal["messagePartID"]
    assert result_part.ignored is True
    terminal_without_delivery_sequence = {
        key: value for key, value in terminal.items() if key not in {"eventID", "eventSequence"}
    }
    assert result_part.metadata == {
        "situationReport": {
            "kind": "terminal_status",
            **terminal_without_delivery_sequence,
        }
    }
    assert result_part.text == f"报告已生成并发布（版本：{terminal['flocksReportVersion']}）。"
    assert Message.to_model_message([result_message]) == []
    terminal_part_events = [
        value
        for event_type, value in published_events
        if event_type == "message.part.updated" and value.get("part", {}).get("id") == terminal["messagePartID"]
    ]
    assert len(terminal_part_events) == 1
    assert terminal_part_events[0]["part"]["messageID"] == terminal["messageID"]
    assert terminal_part_events[0]["part"]["metadata"] == result_part.metadata
    assert published_events.index(("message.part.updated", terminal_part_events[0])) < published_events.index(
        ("situation.report.status", terminal)
    )


@pytest.mark.asyncio
async def test_abort_stops_report_before_recovery_write_and_validation(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
    monkeypatch: pytest.MonkeyPatch,
):
    """The public Abort path must persist cancelled as the only terminal state."""
    from flocks.server.routes import session as session_routes
    from flocks.session.background_tasks import track_background_task

    template, materials, _ = real_inputs
    generation_id = "gen-aborted"
    wire_text = build_report_prompt_text(
        action=_action("generate", generation_id),
        user_instruction="使用当前配置生成中文态势报告。",
    )
    parsed = parse_report_prompt_parts([{"type": "text", "text": wire_text}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            return _state_response(
                request,
                report=_missing(),
                template=_changed(version=1, content=template, url="/template-abort"),
                materials=_changed(version=1, content=materials, url="/materials-abort"),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    published_events: list[tuple[str, dict]] = []
    runner_started = asyncio.Event()
    runner_stopped = asyncio.Event()

    async def capture_event(event_type: str, properties: dict) -> None:
        published_events.append((event_type, properties))

    async def blocking_agent(*_args, **_kwargs) -> None:
        runner_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            runner_stopped.set()

    monkeypatch.setattr("flocks.situation_report.product.events.publish_event", capture_event)
    monkeypatch.setattr("flocks.situation_report.product.orchestrator.publish_event", capture_event)
    monkeypatch.setattr("flocks.server.routes.event.publish_event", capture_event)
    monkeypatch.setattr(
        "flocks.server.routes.question.reject_session_questions",
        AsyncMock(return_value=0),
    )

    task = asyncio.create_task(
        run_managed_report_turn(
            session=product_session,
            event=UserInputEvent(
                source_type="webui",
                sessionID=product_session.id,
                text=wire_text,
                parts=[{"type": "text", "text": wire_text}],
                agent=PRODUCTION_AGENT,
                metadata={},
            ),
            decision=ReportPolicyDecision(kind="execute", prompt=parsed),
            working_directory=product_session.directory,
            generic_runner=blocking_agent,
            backend_synchronizer=sync,
        )
    )
    track_background_task(task, session_id=product_session.id)
    await asyncio.wait_for(runner_started.wait(), timeout=1)

    assert await session_routes._abort_session_processing(product_session.id) is True
    with pytest.raises(asyncio.CancelledError):
        await task

    statuses = [
        value
        for event_type, value in published_events
        if event_type == "situation.report.status"
    ]
    assert [value["status"] for value in statuses] == ["running", "running", "cancelled"]
    assert [value["stage"] for value in statuses] == [
        "downloading_resources",
        "generating",
        "generating",
    ]
    assert runner_stopped.is_set() is True
    workspace = session_root(product_session.id)
    assert not (workspace / "work" / generation_id / "report.md").exists()
    assert not (workspace / "runs" / generation_id / "validation.json").exists()
    assert json.loads((workspace / "output" / "status.json").read_text(encoding="utf-8"))["status"] == "cancelled"
    terminal = statuses[-1]
    result_message = await Message.get_with_parts(product_session.id, terminal["messageID"])
    assert result_message is not None
    assert result_message.parts[0].metadata["situationReport"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_preflight_failure_persists_failed_terminal_message(
    product_session: SessionInfo,
    monkeypatch: pytest.MonkeyPatch,
):
    wire_text = build_report_prompt_text(
        action=_action("generate", "gen-failed"),
        user_instruction="使用当前配置生成中文态势报告。",
    )
    parsed = parse_report_prompt_parts([{"type": "text", "text": wire_text}])

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="backend unavailable")

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    published_events: list[tuple[str, dict]] = []

    async def capture_event(event_type: str, properties: dict) -> None:
        published_events.append((event_type, properties))

    monkeypatch.setattr("flocks.situation_report.product.events.publish_event", capture_event)
    monkeypatch.setattr("flocks.situation_report.product.orchestrator.publish_event", capture_event)

    with pytest.raises(BackendReportSyncError):
        await run_managed_report_turn(
            session=product_session,
            event=UserInputEvent(
                source_type="webui",
                sessionID=product_session.id,
                text=wire_text,
                parts=[{"type": "text", "text": wire_text}],
                agent=PRODUCTION_AGENT,
                metadata={},
            ),
            decision=ReportPolicyDecision(kind="execute", prompt=parsed),
            working_directory=product_session.directory,
            generic_runner=AsyncMock(),
            backend_synchronizer=sync,
        )

    statuses = [value for event_type, value in published_events if event_type == "situation.report.status"]
    assert [value["status"] for value in statuses] == ["running", "failed"]
    terminal = statuses[-1]
    assert terminal["stage"] == "downloading_resources"
    assert terminal["error"]["code"] == "BackendReportSyncError"
    result_message = await Message.get_with_parts(product_session.id, terminal["messageID"])
    assert result_message is not None
    assert result_message.info.parentID == "req-gen-failed"
    result_part = result_message.parts[0]
    assert result_part.id == terminal["messagePartID"]
    assert result_part.ignored is True
    terminal_without_delivery_sequence = {
        key: value for key, value in terminal.items() if key not in {"eventID", "eventSequence"}
    }
    assert result_part.metadata["situationReport"] == {
        "kind": "terminal_status",
        **terminal_without_delivery_sequence,
    }
    assert result_part.text.startswith("报告生成失败：")


@pytest.mark.asyncio
async def test_modify_syncs_changed_report_template_and_materials_atomically(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template_v1, materials_v1, materials_v2 = real_inputs
    template_v2 = template_v1 + b"\n<!-- saved template version 2 -->\n"
    report_v1 = _valid_report(template_v1, materials_v1).encode("utf-8")
    phase = "generate"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        if request.url.path.endswith("/state/latest"):
            if phase == "generate":
                return _state_response(
                    request,
                    report=_missing(),
                    template=_changed(
                        version=1,
                        content=template_v1,
                        url="/template-v1",
                        snapshot_key="templateSnapshotID",
                        snapshot_id="ts-001",
                        format="markdown",
                    ),
                    materials=_changed(
                        version=1,
                        content=materials_v1,
                        url="/materials-v1",
                        snapshot_key="materialSnapshotID",
                        snapshot_id="ms-001",
                        format="jsonl",
                    ),
                )
            return _state_response(
                request,
                report=_changed(version=1, content=report_v1, url="/report-v1"),
                template=_changed(
                    version=2,
                    content=template_v2,
                    url="/template-v2",
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-002",
                    format="markdown",
                ),
                materials=_changed(
                    version=2,
                    content=materials_v2,
                    url="/materials-v2",
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-002",
                    format="jsonl",
                ),
            )
        if request.url.path.endswith("/report/download"):
            return _download_response(request, resource="report", version=1, content=report_v1)
        if request.url.path.endswith("/template/download"):
            version, content = (1, template_v1) if phase == "generate" else (2, template_v2)
            return _download_response(request, resource="template", version=version, content=content)
        if request.url.path.endswith("/materials/download"):
            version, content = (1, materials_v1) if phase == "generate" else (2, materials_v2)
            return _download_response(request, resource="materials", version=version, content=content)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-001"),
        synchronizer=sync,
    )
    phase = "modify"
    context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("modify", "gen-002", base_version=1),
        synchronizer=sync,
    )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    state = load_session_state(product_session.id).report_state
    assert state is not None
    assert (state["syncedBackendReportVersion"], state["templateVersion"], state["materialVersion"]) == (1, 2, 2)
    assert context["baseReport"]["sha256"] == _sha256(report_v1)
    assert context["template"]["snapshotID"].startswith("template-v2-")
    assert context["materials"]["snapshotID"].startswith("materials-v2-")


@pytest.mark.asyncio
async def test_regenerate_checks_report_version_without_downloading_report_body(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials, _ = real_inputs
    report = _valid_report(template, materials).encode("utf-8")
    phase = "generate"
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path.endswith("/state/latest"):
            if phase == "generate":
                return _state_response(
                    request,
                    report=_missing(),
                    template=_changed(
                        version=1,
                        content=template,
                        url="/template-v1",
                        snapshot_key="templateSnapshotID",
                        snapshot_id="ts-001",
                        format="markdown",
                    ),
                    materials=_changed(
                        version=1,
                        content=materials,
                        url="/materials-v1",
                        snapshot_key="materialSnapshotID",
                        snapshot_id="ms-001",
                        format="jsonl",
                    ),
                )
            return _state_response(
                request,
                report=_changed(version=1, content=report, url="/report-v1"),
                template=_unchanged(
                    version=1,
                    content=template,
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-001",
                    format="markdown",
                ),
                materials=_unchanged(
                    version=1,
                    content=materials,
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-001",
                    format="jsonl",
                ),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-001"),
        synchronizer=sync,
    )
    phase = "regenerate"
    context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("regenerate", "gen-002", base_version=0),
        synchronizer=sync,
    )

    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["effectiveBackendReportVersion"] == 1
    assert "baseReport" not in context
    assert not any(path.endswith("/report/download") for path in requested_paths)


@pytest.mark.asyncio
async def test_partial_changed_resource_failure_does_not_switch_active_versions(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials_v1, materials_v2 = real_inputs
    phase = "generate"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            if phase == "generate":
                template_value = _changed(
                    version=1,
                    content=template,
                    url="/template-v1",
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-001",
                    format="markdown",
                )
                material_value = _changed(
                    version=1,
                    content=materials_v1,
                    url="/materials-v1",
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-001",
                    format="jsonl",
                )
                return _state_response(
                    request,
                    report=_missing(),
                    template=template_value,
                    materials=material_value,
                )
            return _state_response(
                request,
                report=_changed(version=1, content=b"# backend report\n", url="/report-v1"),
                template=_changed(
                    version=2,
                    content=template + b"\n<!-- v2 -->\n",
                    url="/template-v2",
                    snapshot_key="templateSnapshotID",
                    snapshot_id="ts-002",
                    format="markdown",
                ),
                materials=_changed(
                    version=2,
                    content=materials_v2,
                    url="/materials-v2",
                    snapshot_key="materialSnapshotID",
                    snapshot_id="ms-002",
                    format="jsonl",
                ),
            )
        if request.url.path.endswith("/report/download"):
            return _download_response(request, resource="report", version=1, content=b"# backend report\n")
        if request.url.path.endswith("/template/download"):
            version, content = (1, template) if phase == "generate" else (2, template + b"\n<!-- v2 -->\n")
            return _download_response(request, resource="template", version=version, content=content)
        if request.url.path.endswith("/materials/download"):
            version, content = (1, materials_v1) if phase == "generate" else (2, b"corrupt")
            return _download_response(request, resource="materials", version=version, content=content)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-001"),
        synchronizer=sync,
    )
    before = load_session_state(product_session.id).model_dump(by_alias=True)
    phase = "failed-sync"
    with pytest.raises(BackendReportSyncError):
        await initialize_report_action(
            session_id=product_session.id,
            prompt=_prompt("modify", "gen-002", base_version=1),
            synchronizer=sync,
        )
    after = load_session_state(product_session.id).model_dump(by_alias=True)
    assert after["reportState"] == before["reportState"]


@pytest.mark.asyncio
async def test_same_material_version_with_new_live_content_creates_new_local_snapshot(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials_v1, materials_v2 = real_inputs
    phase = "first"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            known = int(request.url.params["knownMaterialVersion"])
            return _state_response(
                request,
                report=_missing(),
                template=_changed(version=1, content=template, url="unused")
                if known == 0
                else _unchanged(version=1, content=template),
                materials=_changed(version=1, content=materials_v1, url="unused")
                if known == 0
                else _unchanged(version=1, content=materials_v1),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            content = materials_v1 if phase == "first" else materials_v2
            return _download_response(request, resource="materials", version=1, content=content)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    first_context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-live-001"),
        synchronizer=sync,
    )
    first_context = json.loads(first_context_path.read_text(encoding="utf-8"))
    phase = "second"
    second_context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-live-002"),
        synchronizer=sync,
    )
    second_context = json.loads(second_context_path.read_text(encoding="utf-8"))

    assert first_context["materialVersion"] == second_context["materialVersion"] == 1
    assert first_context["materials"]["sha256"] != second_context["materials"]["sha256"]
    assert first_context["materials"]["snapshotID"] != second_context["materials"]["snapshotID"]


@pytest.mark.asyncio
async def test_modify_rejects_report_download_version_race(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, materials, _ = real_inputs
    report = _valid_report(template, materials).encode("utf-8")
    phase = "generate"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            if phase == "generate":
                return _state_response(
                    request,
                    report=_missing(),
                    template=_changed(version=1, content=template, url="unused"),
                    materials=_changed(version=1, content=materials, url="unused"),
                )
            return _state_response(
                request,
                report=_changed(version=1, content=report, url="unused"),
                template=_unchanged(version=1, content=template),
                materials=_unchanged(version=1, content=materials),
            )
        if request.url.path.endswith("/report/download"):
            return _download_response(request, resource="report", version=2, content=report)
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=materials)
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-race-001"),
        synchronizer=sync,
    )
    before = load_session_state(product_session.id).model_dump(by_alias=True)
    phase = "modify"
    with pytest.raises(BackendReportSyncError, match="Downloaded report version changed"):
        await initialize_report_action(
            session_id=product_session.id,
            prompt=_prompt("modify", "gen-race-002", base_version=1),
            synchronizer=sync,
        )
    after = load_session_state(product_session.id).model_dump(by_alias=True)
    assert after["reportState"] == before["reportState"]


@pytest.mark.asyncio
async def test_generate_accepts_empty_material_download(
    product_session: SessionInfo,
    real_inputs: tuple[bytes, bytes, bytes],
):
    template, _, _ = real_inputs

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/state/latest"):
            return _state_response(
                request,
                report=_missing(),
                template=_changed(version=1, content=template, url="unused"),
                materials=_changed(version=1, content=b"", url="unused"),
            )
        if request.url.path.endswith("/template/download"):
            return _download_response(request, resource="template", version=1, content=template)
        if request.url.path.endswith("/materials/download"):
            return _download_response(request, resource="materials", version=1, content=b"")
        return httpx.Response(404)

    sync = BackendReportSynchronizer(lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    context_path = await initialize_report_action(
        session_id=product_session.id,
        prompt=_prompt("generate", "gen-empty-001"),
        synchronizer=sync,
    )
    context = json.loads(context_path.read_text(encoding="utf-8"))
    assert context["materials"]["recordCount"] == 0
    page = await read_material_page(
        session_id=product_session.id,
        generation_id="gen-empty-001",
        limit=20,
    )
    assert page == {
        "offset": 0,
        "limit": 20,
        "total": 0,
        "hasMore": False,
        "nextOffset": 0,
        "materials": [],
    }


@pytest.mark.asyncio
async def test_backend_resource_calls_require_configured_service_token(
    product_session: SessionInfo,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("SITUATION_REPORT_BACKEND_TOKEN")
    synchronizer = BackendReportSynchronizer()
    with pytest.raises(BackendReportSyncError, match="BACKEND_TOKEN is not configured"):
        await synchronizer.get_latest(
            session_id=product_session.id,
            known_report_version=0,
            known_template_version=0,
            known_material_version=0,
            request_id="req-missing-token",
        )


@pytest.mark.asyncio
async def test_report_status_event_contains_session_identity_only(
    product_session: SessionInfo,
    monkeypatch: pytest.MonkeyPatch,
):
    published = AsyncMock()
    monkeypatch.setattr("flocks.situation_report.product.events.publish_event", published)
    first = await publish_report_status(
        session_id=product_session.id,
        generation_id="gen-event",
        payload={"status": "running", "stage": "checking_resources"},
    )
    second = await publish_report_status(
        session_id=product_session.id,
        generation_id="gen-event",
        payload={"status": "succeeded", "stage": "output_ready"},
    )
    assert first["eventSequence"] == 1
    assert second["eventSequence"] == 2
    assert second["sessionID"] == product_session.id
    assert {"projectKey", "userKey", "userKeyHash"}.isdisjoint(second)


def test_text_contract_has_no_project_user_or_resource_snapshot_fields():
    action = _action("generate", "gen-contract")
    text = build_report_prompt_text(action=action, user_instruction="生成报告")
    assert "projectKey" not in text
    assert "userKey" not in text
    assert "templateSnapshotID" not in text
    assert "materialSnapshotID" not in text
    with pytest.raises(ValidationError):
        ReportAction.model_validate(
            {
                **action.model_dump(by_alias=True),
                "projectKey": "prj_not_allowed",
            }
        )
