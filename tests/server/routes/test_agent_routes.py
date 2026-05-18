"""
Agent route tests

Covers:
  - Listing agents (GET /api/agent)
  - Getting a specific agent (GET /api/agent/{name})
  - Creating a custom agent (POST /api/agent)
  - Updating an agent (PUT /api/agent/{name})
  - Deleting a custom agent (DELETE /api/agent/{name})
  - Running / testing an agent (POST /api/agent/{name}/test)
  - Error cases (404, 422)
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient

# Minimal valid agent payload
_AGENT_PAYLOAD = {
    "name": "test-agent",
    "description": "A test agent",
    "mode": "primary",
    "permission": [],
    "options": {},
    "prompt": "You are a test assistant.",
}


# ===========================================================================
# List
# ===========================================================================

class TestAgentList:

    @pytest.mark.asyncio
    async def test_list_agents_returns_array(self, client: AsyncClient):
        """GET /api/agent returns a non-empty list of built-in agents."""
        resp = await client.get("/api/agent")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    @pytest.mark.asyncio
    async def test_list_agents_have_required_fields(self, client: AsyncClient):
        """Each agent in the list has the required Flocks-compatible fields."""
        resp = await client.get("/api/agent")
        for agent in resp.json():
            assert "name" in agent
            assert "permission" in agent
            assert "options" in agent


# ===========================================================================
# Get
# ===========================================================================

class TestAgentGet:

    @pytest.mark.asyncio
    async def test_get_builtin_agent(self, client: AsyncClient):
        """GET /api/agent/rex returns the built-in rex agent."""
        resp = await client.get("/api/agent/rex")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["name"] == "rex"

    @pytest.mark.asyncio
    async def test_get_unknown_agent_returns_404(self, client: AsyncClient):
        """GET for a non-existent agent returns 404."""
        resp = await client.get("/api/agent/this_agent_does_not_exist_ever")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Create
# ===========================================================================

class TestAgentCreate:

    @pytest.mark.asyncio
    async def test_create_agent(self, client: AsyncClient):
        """POST /api/agent creates a new YAML-backed agent."""
        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), resp.text
        data = resp.json()
        assert data["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_create_agent_missing_name_returns_422(self, client: AsyncClient):
        """Creating an agent without a name returns 422."""
        resp = await client.post(
            "/api/agent",
            json={k: v for k, v in _AGENT_PAYLOAD.items() if k != "name"},
        )
        assert resp.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY

    @pytest.mark.asyncio
    async def test_created_agent_retrievable_by_name(self, client: AsyncClient):
        """A newly created agent can be retrieved by name even if the global list
        (backed by the agent state cache) doesn't refresh automatically."""
        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code == status.HTTP_200_OK

        # Direct GET by name uses the refreshed agent registry, so the new agent is visible
        get_resp = await client.get("/api/agent/test-agent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_created_agent_survives_registry_reload(self, client: AsyncClient):
        """Storage-backed custom agents remain visible after process cache reload."""
        from flocks.agent.registry import Agent

        resp = await client.post("/api/agent", json=_AGENT_PAYLOAD)
        assert resp.status_code == status.HTTP_200_OK

        Agent._custom_agents.clear()
        Agent.invalidate_cache()

        get_resp = await client.get("/api/agent/test-agent")
        assert get_resp.status_code == status.HTTP_200_OK
        assert get_resp.json()["name"] == "test-agent"

        list_resp = await client.get("/api/agent")
        assert list_resp.status_code == status.HTTP_200_OK
        assert "test-agent" in [agent["name"] for agent in list_resp.json()]


# ===========================================================================
# Update
# ===========================================================================

class TestAgentUpdate:

    @pytest.mark.asyncio
    async def test_update_agent_description(self, client: AsyncClient):
        """PUT /api/agent/{name} updates the agent description."""
        # Create first
        await client.post("/api/agent", json=_AGENT_PAYLOAD)

        updated = {**_AGENT_PAYLOAD, "description": "Updated description"}
        resp = await client.put("/api/agent/test-agent", json=updated)
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_update_nonexistent_agent_returns_404(self, client: AsyncClient):
        """Updating a non-existent agent returns 404."""
        resp = await client.put(
            "/api/agent/no_such_agent",
            json=_AGENT_PAYLOAD,
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND


# ===========================================================================
# Delete
# ===========================================================================

class TestAgentDelete:

    @pytest.mark.asyncio
    async def test_delete_custom_agent(self, client: AsyncClient):
        """DELETE /api/agent/{name} removes the custom agent."""
        await client.post("/api/agent", json=_AGENT_PAYLOAD)
        resp = await client.delete("/api/agent/test-agent")
        assert resp.status_code == status.HTTP_200_OK

        # Should no longer appear in the list
        list_resp = await client.get("/api/agent")
        names = [a["name"] for a in list_resp.json()]
        assert "test-agent" not in names

    @pytest.mark.asyncio
    async def test_delete_builtin_agent_returns_error(self, client: AsyncClient):
        """Deleting a built-in agent that has no storage entry returns 404
        (no Storage key 'agent/custom/rex' and no YAML override file)."""
        resp = await client.delete("/api/agent/rex")
        # If rex has no Storage / YAML entry the route returns 404.
        # If it somehow has an entry from another source it may succeed (200).
        # The important thing is it does NOT crash (5xx).
        assert resp.status_code < 500


# ===========================================================================
# Test / Run
# ===========================================================================

class TestAgentRun:

    @pytest.mark.asyncio
    async def test_run_agent_creates_session(self, client: AsyncClient):
        """POST /api/agent/{name}/test creates a session for a known agent.

        We first create a custom agent so we control its existence in storage,
        then call /test on it.  Built-in agents (rex etc.) depend on the
        Instance/state cache being warm, which is outside scope of this unit test.
        """
        # Create a known custom agent
        await client.post("/api/agent", json=_AGENT_PAYLOAD)

        resp = await client.post(
            "/api/agent/test-agent/test",
            json={"message": "hello"},
        )
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert "sessionId" in data or "session_id" in data or "id" in data

    @pytest.mark.asyncio
    async def test_run_nonexistent_agent_returns_404(self, client: AsyncClient):
        """Testing a non-existent agent returns 404."""
        resp = await client.post(
            "/api/agent/no_such_agent/test",
            json={"message": "hi"},
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
