"""
Tests for session module
"""

import asyncio
import warnings
from unittest.mock import AsyncMock

import pytest
from flocks.auth.context import AuthUser, reset_current_auth_user, set_current_auth_user
from flocks.session.session import Session
from flocks.session.message import Message, MessageInfo, MessageRole, TokenUsage
from flocks.session.callable_state import add_session_callable_tools, get_session_callable_tools
from flocks.agent.registry import Agent
from flocks.storage.storage import Storage


@pytest.mark.asyncio
async def test_session_create():
    """Test session creation"""
    session = await Session.create(
        project_id="test_project",
        directory="/test/dir",
        title="Test Session",
    )
    
    assert session.id.startswith("ses_")  # Updated to match new ID format
    assert session.project_id == "test_project"
    assert session.directory == "/test/dir"
    assert session.title == "Test Session"
    assert session.status == "active"


@pytest.mark.asyncio
async def test_session_create_initializes_callable_tools_from_declared_agent_tools(
    monkeypatch: pytest.MonkeyPatch,
):
    initialize_mock = AsyncMock()
    monkeypatch.setattr(
        "flocks.session.callable_state.initialize_session_callable_tools",
        initialize_mock,
    )
    monkeypatch.setattr(
        "flocks.tool.catalog.get_always_load_tool_names",
        lambda: {"question", "tool_search"},
    )

    class _AgentInfo:
        tools = []

    monkeypatch.setattr(
        "flocks.agent.registry.Agent.get",
        AsyncMock(return_value=_AgentInfo()),
    )

    session = await Session.create(
        project_id="test_project_callable_tools",
        directory="/test/dir",
        title="Callable Tool Init",
        agent="rex",
    )

    initialize_mock.assert_awaited_once_with(
        session.id,
        [],
        always_load_tool_names={"question", "tool_search"},
    )


@pytest.mark.asyncio
async def test_session_get():
    """Test session retrieval"""
    # Create a session
    session = await Session.create(
        project_id="test_project",
        directory="/test/dir",
    )
    
    # Get the session
    retrieved = await Session.get("test_project", session.id)
    
    assert retrieved is not None
    assert retrieved.id == session.id
    assert retrieved.project_id == session.project_id


@pytest.mark.asyncio
async def test_session_list():
    """Test session listing"""
    # Create multiple sessions
    await Session.create(
        project_id="test_project_2",
        directory="/test/dir1",
        title="Session 1",
    )
    await Session.create(
        project_id="test_project_2",
        directory="/test/dir2",
        title="Session 2",
    )
    
    # List sessions
    sessions = await Session.list("test_project_2")
    
    assert len(sessions) >= 2
    assert all(s.project_id == "test_project_2" for s in sessions)


@pytest.mark.asyncio
async def test_session_list_all_uses_cache(monkeypatch: pytest.MonkeyPatch):
    """Repeated list_all calls should reuse the in-memory cache."""
    Session._all_sessions_cache = None

    session = await Session.create(
        project_id="test_project_cache",
        directory="/test/cache",
        title="Cached Session",
    )

    original_list_entries = Storage.list_entries
    call_count = 0

    async def counting_list_entries(cls, prefix=None, model=None):
        nonlocal call_count
        call_count += 1
        return await original_list_entries(prefix=prefix, model=model)

    monkeypatch.setattr(Storage, "list_entries", classmethod(counting_list_entries))

    first = await Session.list_all()
    second = await Session.list_all()

    assert any(s.id == session.id for s in first)
    assert any(s.id == session.id for s in second)
    assert call_count == 1


@pytest.mark.asyncio
async def test_session_cache_invalidated_after_storage_clear():
    """Clearing storage should drop stale session list caches."""
    await Session.create(
        project_id="test_project_clear_cache",
        directory="/test/cache-clear",
        title="Needs Invalidating",
    )

    first = await Session.list_all()
    assert any(s.project_id == "test_project_clear_cache" for s in first)

    await Storage.clear()

    second = await Session.list_all()
    assert all(s.project_id != "test_project_clear_cache" for s in second)


@pytest.mark.asyncio
async def test_session_update():
    """Test session update"""
    # Create a session
    session = await Session.create(
        project_id="test_project_3",
        directory="/test/dir",
        title="Original Title",
    )
    
    # Update the session
    updated = await Session.update(
        "test_project_3",
        session.id,
        title="Updated Title",
    )
    
    assert updated is not None
    assert updated.title == "Updated Title"


@pytest.mark.asyncio
async def test_session_accessible_to_recreated_user_with_same_username():
    session = await Session.create(
        project_id="test_project_username_owner",
        directory="/test/dir",
        title="Owned by Username",
        owner_user_id="usr_old_owner",
        owner_username="alice",
    )

    token = set_current_auth_user(
        AuthUser(
            id="usr_new_owner",
            username="alice",
            role="member",
            status="active",
        )
    )
    try:
        sessions = await Session.list_all()
        assert any(item.id == session.id for item in sessions)
    finally:
        reset_current_auth_user(token)


@pytest.mark.asyncio
async def test_retain_deleted_user_sessions_detaches_owner_id_and_preserves_username():
    session = await Session.create(
        project_id="test_project_deleted_owner",
        directory="/test/dir",
        title="Deleted Owner",
        owner_user_id="usr_deleted_owner",
        owner_username="alice",
    )

    migrated = await Session.retain_deleted_user_sessions("usr_deleted_owner", "alice")
    assert migrated == 1

    stored = await Session.get("test_project_deleted_owner", session.id)
    assert stored is not None
    assert stored.owner_user_id is None
    assert stored.owner_username == "alice"


@pytest.mark.asyncio
async def test_session_delete():
    """Test session deletion"""
    # Create a session
    session = await Session.create(
        project_id="test_project_4",
        directory="/test/dir",
    )
    
    # Delete the session
    deleted = await Session.delete("test_project_4", session.id)
    
    assert deleted is True
    
    # Verify it's marked as deleted (get returns None for deleted sessions)
    retrieved = await Session.get("test_project_4", session.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_session_delete_clears_callable_tool_state():
    """Deleting a session should remove persisted callable tool state."""
    session = await Session.create(
        project_id="test_project_callable_cleanup",
        directory="/test/dir",
    )
    await add_session_callable_tools(session.id, ["websearch"])

    callable_tools = await get_session_callable_tools(session.id)
    assert "websearch" in callable_tools
    assert len(callable_tools) > 0

    deleted = await Session.delete("test_project_callable_cleanup", session.id)

    assert deleted is True
    assert await get_session_callable_tools(session.id) == set()
    assert await Storage.get(f"session_callable_tools:{session.id}") is None


@pytest.mark.asyncio
async def test_message_create():
    """Test message creation"""
    message = await Message.create(
        session_id="test_session",
        role=MessageRole.USER,
        content="Hello, world!",
    )
    
    assert message.id.startswith("msg_")  # Updated to match new ID format
    assert message.sessionID == "test_session"  # Flocks uses sessionID
    assert message.role == "user"  # role is a string in Flocks


@pytest.mark.asyncio
async def test_message_list():
    """Test message listing"""
    session_id = "test_session_2"
    
    # Create messages
    await Message.create(session_id, MessageRole.USER, "Message 1")
    await Message.create(session_id, MessageRole.ASSISTANT, "Message 2")
    await Message.create(session_id, MessageRole.USER, "Message 3")
    
    # List messages
    messages = await Message.list(session_id)
    
    assert len(messages) >= 3


@pytest.mark.asyncio
async def test_message_get():
    """Test message retrieval"""
    session_id = "test_session_3"
    
    # Create a message
    message = await Message.create(session_id, MessageRole.USER, "Test message")
    
    # Get the message
    retrieved = await Message.get(session_id, message.id)
    
    assert retrieved is not None
    assert retrieved.id == message.id
    text_result = Message.get_text_content(retrieved)
    text = await text_result if asyncio.iscoroutine(text_result) else text_result
    assert text == "Test message"


@pytest.mark.asyncio
async def test_message_update_coerces_dict_tokens_to_token_usage():
    """Assistant token updates should stay aligned with the Pydantic schema."""
    session_id = "test_session_tokens_update"

    message = await Message.create(
        session_id=session_id,
        role=MessageRole.ASSISTANT,
        content="Token update test",
        parentID="msg_parent",
        modelID="test-model",
        providerID="test-provider",
        mode="rex",
        agent="rex",
    )

    updated = await Message.update(
        session_id,
        message.id,
        tokens={
            "input": 12,
            "output": 8,
            "reasoning": 3,
            "cache": {"read": 5, "write": 2},
        },
    )

    assert updated is not None
    assert isinstance(updated.tokens, TokenUsage)
    assert updated.tokens.input == 12
    assert updated.tokens.cache.read == 5

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dumped = updated.model_dump()

    assert dumped["tokens"]["output"] == 8
    assert not any("Pydantic serializer warnings" in str(w.message) for w in caught)


@pytest.mark.asyncio
async def test_message_delete():
    """Test message deletion"""
    session_id = "test_session_4"
    
    # Create a message
    message = await Message.create(session_id, MessageRole.USER, "Test message")
    
    # Delete the message
    deleted = await Message.delete(session_id, message.id)
    
    assert deleted is True
    
    # Verify it's gone
    retrieved = await Message.get(session_id, message.id)
    assert retrieved is None


@pytest.mark.asyncio
async def test_message_to_llm_format():
    """Test message conversion to LLM format"""
    session_id = "test_session_5"
    
    # Create messages
    await Message.create(session_id, MessageRole.USER, "Hello")
    await Message.create(session_id, MessageRole.ASSISTANT, "Hi there!")
    await Message.create(session_id, MessageRole.USER, "How are you?")
    
    # Convert to LLM format
    messages = await Message.list(session_id)
    llm_result = Message.to_llm_format(messages)
    llm_messages = await llm_result if asyncio.iscoroutine(llm_result) else llm_result
    
    assert len(llm_messages) >= 3
    assert llm_messages[0]["role"] == "user"
    assert llm_messages[0]["content"] == "Hello"


@pytest.mark.asyncio
async def test_agent_get():
    """Test agent retrieval"""
    agent = await Agent.get("rex")
    
    assert agent is not None
    assert agent.name == "rex"
    assert agent.native is True


@pytest.mark.asyncio
async def test_agent_list():
    """Test agent listing"""
    agents = await Agent.list()
    
    assert len(agents) >= 3
    assert any(a.name == "rex" for a in agents)
    assert any(a.name == "plan" for a in agents)


@pytest.mark.asyncio
async def test_agent_system_prompt():
    """Test agent system prompt"""
    # plan agent doesn't have a custom prompt (matches Flocks)
    plan_prompt = await Agent.get_system_prompt("plan")
    assert plan_prompt is None
    
    # explore agent has a custom prompt
    explore_prompt = await Agent.get_system_prompt("explore")
    assert explore_prompt is not None
    assert "file search" in explore_prompt.lower()


@pytest.mark.asyncio
async def test_agent_register():
    """Test custom agent registration"""
    from flocks.agent.agent import AgentInfo
    
    custom_agent = AgentInfo(
        name="custom",
        description="Custom test agent",
        native=False,
        prompt="You are a custom agent.",
    )
    
    Agent.register("custom", custom_agent)
    
    # Verify it's registered (reload to get custom agents)
    agents = await Agent._load_agents()
    agent = agents.get("custom")
    assert agent is not None
    assert agent.name == "custom"
    assert agent.native is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
