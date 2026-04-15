"""
Tests for server module
"""

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import status

from flocks.server.app import app
from flocks.task.manager import TaskManager
from flocks.task.models import DeliveryStatus, SchedulerMode, TaskStatus, TaskTrigger


@pytest.fixture
async def client():
    """Create test client"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_root_endpoint(client):
    """Test root endpoint - returns HTML (webui) or JSON API info"""
    response = await client.get("/")
    assert response.status_code == status.HTTP_200_OK
    # When webui dist exists, returns HTML; otherwise returns JSON API info
    content_type = response.headers.get("content-type", "")
    if "application/json" in content_type:
        data = response.json()
        assert data["name"] == "Flocks API"
        assert data["status"] == "running"
    else:
        # webui HTML response
        assert "html" in content_type or len(response.content) > 0


@pytest.mark.asyncio
async def test_health_check(client):
    """Test health check endpoint"""
    response = await client.get("/api/health")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["status"] == "healthy"
    assert isinstance(data["version"], str) and data["version"]
    assert "timestamp" in data
    assert "task_manager_started" in data
    assert "task_scheduler_running" in data
    assert "task_scheduler_available" in data
    assert "task_queue_running" in data
    assert "task_queue_queued" in data
    assert "task_stale_running" in data


@pytest.mark.asyncio
async def test_task_queue_status_includes_diagnostics(client):
    response = await client.get("/api/task-system/queue/status")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["max_concurrent"] == 4
    assert "running" in data
    assert "queued" in data
    assert "stale_running" in data
    assert "oldest_running_seconds" in data


@pytest.mark.asyncio
async def test_ping(client):
    """Test ping endpoint"""
    response = await client.get("/api/ping")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["message"] == "pong"


@pytest.mark.asyncio
async def test_queue_items_endpoint(client):
    response = await client.get("/api/task-executions")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "items" in data
    assert "total" in data


@pytest.mark.asyncio
async def test_task_schedulers_scheduled_only_excludes_immediate_queue_templates(client):
    await TaskManager.create_scheduler(
        title="立即任务",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
    )
    scheduled = await TaskManager.create_scheduler(
        title="单次计划",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=False),
    )

    response = await client.get("/api/task-schedulers", params={"scheduledOnly": "true"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    ids = {item["id"] for item in data["items"]}

    assert scheduled.id in ids
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_task_schedulers_list_excludes_archived_builtin_after_delete(client):
    scheduler = await TaskManager.create_scheduler(
        title="内置计划任务",
        mode=SchedulerMode.CRON,
        trigger=TaskTrigger(cron="*/5 * * * *", timezone="Asia/Shanghai"),
        dedup_key="builtin:test-scheduled-task",
    )

    response = await client.delete(f"/api/task-schedulers/{scheduler.id}")
    assert response.status_code == status.HTTP_200_OK

    response = await client.get("/api/task-schedulers", params={"scheduledOnly": "true"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    ids = {item["id"] for item in data["items"]}

    assert scheduler.id not in ids


@pytest.mark.asyncio
async def test_mark_execution_viewed_endpoint_updates_delivery_status(client):
    from flocks.task.store import TaskStore

    scheduler = await TaskManager.create_scheduler(
        title="标记已读",
        mode=SchedulerMode.ONCE,
        trigger=TaskTrigger(run_immediately=True),
    )
    execution = (await TaskManager.list_scheduler_executions(scheduler.id, limit=1))[0][0]
    execution.status = TaskStatus.COMPLETED
    execution.delivery_status = DeliveryStatus.UNREAD
    await TaskStore.update_execution(execution)

    response = await client.post(f"/api/task-executions/{execution.id}/viewed")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["deliveryStatus"] == "viewed"


@pytest.mark.asyncio
async def test_create_session(client):
    """Test session creation - TypeScript compatible"""
    response = await client.post(
        "/api/session",
        json={
            "projectID": "proj_123",  # camelCase
            "directory": "/test/dir",
            "title": "Test Session",
            "agent": "rex",
        }
    )
    assert response.status_code == status.HTTP_200_OK  # TypeScript returns 200
    data = response.json()
    assert data["title"] == "Test Session"
    assert "projectID" in data  # auto-computed from directory hash
    assert "directory" in data
    assert "id" in data
    assert data["id"].startswith("ses_")


@pytest.mark.asyncio
async def test_list_sessions(client):
    """Test session listing - TypeScript compatible"""
    # Create a session first
    create_response = await client.post(
        "/api/session",
        json={
            "projectID": "proj_123",  # camelCase
            "directory": "/test/dir",
        }
    )
    assert create_response.status_code == status.HTTP_200_OK  # TypeScript returns 200
    
    # List sessions - TypeScript returns array directly
    response = await client.get("/api/session")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list), "Session list should return array"
    assert len(data) >= 1


@pytest.mark.asyncio
async def test_get_session(client):
    """Test getting a session by ID - TypeScript compatible"""
    # Create a session
    create_response = await client.post(
        "/api/session",
        json={
            "projectID": "proj_123",  # camelCase
            "directory": "/test/dir",
            "title": "Test Session",
        }
    )
    session_id = create_response.json()["id"]
    
    # Get the session - TypeScript path: /{sessionID}
    response = await client.get(f"/api/session/{session_id}")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == session_id
    assert data["title"] == "Test Session"


@pytest.mark.asyncio
async def test_update_session(client):
    """Test updating a session - TypeScript compatible"""
    # Create a session
    create_response = await client.post(
        "/api/session",
        json={
            "projectID": "proj_123",  # camelCase
            "directory": "/test/dir",
        }
    )
    session_id = create_response.json()["id"]
    
    # Update the session - TypeScript path: /{sessionID}, body with title
    response = await client.patch(
        f"/api/session/{session_id}",
        json={
            "title": "Updated Title",
        }
    )
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["title"] == "Updated Title"


@pytest.mark.asyncio
async def test_delete_session(client):
    """Test deleting a session - TypeScript compatible"""
    # Create a session
    create_response = await client.post(
        "/api/session",
        json={
            "projectID": "proj_123",  # camelCase
            "directory": "/test/dir",
        }
    )
    session_id = create_response.json()["id"]
    
    # Delete the session - TypeScript path: /{sessionID}, returns 200 with true
    response = await client.delete(f"/api/session/{session_id}")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() is True
    
    # Verify it's deleted
    get_response = await client.get(f"/api/session/{session_id}")
    assert get_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_list_providers(client):
    """Test listing providers"""
    response = await client.get("/api/provider")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert "all" in data
    assert "default" in data
    assert "connected" in data
    assert isinstance(data["all"], list)
    
    if data["all"]:
        provider = data["all"][0]
        assert "id" in provider
        assert "name" in provider
        assert "models" in provider


@pytest.mark.asyncio
async def test_get_provider(client):
    """Test getting a specific provider"""
    response = await client.get("/api/provider/anthropic")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["id"] == "anthropic"
    assert data["name"] == "Anthropic"
    assert isinstance(data["models"], (list, dict))


@pytest.mark.asyncio
async def test_list_models_for_provider(client):
    """Test listing models for a provider"""
    response = await client.get("/api/provider/openai/models")
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert isinstance(data, list)
    # In a test environment without API keys, the list may be empty.
    if data:
        model = data[0]
        assert "id" in model
        assert "name" in model
        assert "providerID" in model


@pytest.mark.asyncio
async def test_provider_and_model_lists_are_empty_without_connected_providers(
    client, monkeypatch: pytest.MonkeyPatch
):
    """Fresh installs should not show built-in providers/models before connection."""
    from flocks.config.config_writer import ConfigWriter

    monkeypatch.setattr(ConfigWriter, "list_provider_ids", classmethod(lambda cls: []))

    provider_resp = await client.get("/api/provider")
    assert provider_resp.status_code == status.HTTP_200_OK
    provider_data = provider_resp.json()
    assert provider_data["all"] == []
    assert provider_data["default"] == {}
    assert provider_data["connected"] == []

    model_resp = await client.get("/api/model/v2/definitions")
    assert model_resp.status_code == status.HTTP_200_OK
    model_data = model_resp.json()
    assert model_data["models"] == []
    assert model_data["total"] == 0


@pytest.mark.asyncio
async def test_404_for_unknown_session(client):
    """Test 404 for unknown session"""
    response = await client.get("/api/session/session_UNKNOWN123456789012345678")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_404_for_unknown_provider(client):
    """Test 404 for unknown provider"""
    response = await client.get("/api/provider/unknown_provider")
    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_question_routes_available_with_and_without_api_prefix(client):
    """Question routes should work for both /api/question and /question prefixes."""
    unknown_request_id = "question_nonexistent_request"

    response_api = await client.post(
        f"/api/question/{unknown_request_id}/reply",
        json={"answers": [["a"]]},
    )
    assert response_api.status_code == status.HTTP_404_NOT_FOUND
    assert response_api.json().get("message") == "Question request not found"

    response_legacy = await client.post(
        f"/question/{unknown_request_id}/reply",
        json={"answers": [["a"]]},
    )
    assert response_legacy.status_code == status.HTTP_404_NOT_FOUND
    assert response_legacy.json().get("message") == "Question request not found"


@pytest.mark.asyncio
async def test_question_pending_route_lists_session_requests(client):
    """Pending question list should return only the current session's requests."""
    from flocks.server.routes.question import clear_request_state, store_question_request

    req1 = {
        "id": "question_req_1",
        "sessionID": "session_a",
        "questions": [{"question": "A?"}],
        "tool": {"callID": "call_a", "messageID": "msg_a"},
    }
    req2 = {
        "id": "question_req_2",
        "sessionID": "session_b",
        "questions": [{"question": "B?"}],
        "tool": {"callID": "call_b", "messageID": "msg_b"},
    }
    store_question_request(req1["id"], req1)
    store_question_request(req2["id"], req2)

    try:
        response = await client.get("/api/question/session/session_a/pending")
        assert response.status_code == status.HTTP_200_OK
        assert response.json() == [req1]
    finally:
        clear_request_state(req1["id"])
        clear_request_state(req2["id"])


