from strix.core.agents import AgentCoordinator
from strix.interface.tui.live_view import TuiLiveView

from strix_chat_api.manager import ChatManager, ChatSession


def test_get_chat_serializes_agent_projection() -> None:
    manager = ChatManager()
    coordinator = AgentCoordinator()
    coordinator.statuses["root-agent"] = "waiting"
    coordinator.parent_of["root-agent"] = None
    coordinator.names["root-agent"] = "strix"
    live_view = TuiLiveView()
    live_view.record_user_message("root-agent", "hello")
    session = ChatSession(
        id="chat-test",
        message="hello",
        targets=[],
        scan_mode="standard",
        coordinator=coordinator,
        live_view=live_view,
    )
    manager._sessions[session.id] = session

    try:
        response = manager.get_chat(session.id)
    finally:
        manager._sessions.clear()
        manager.close()

    assert response["root_agent_id"] == "root-agent"
    assert response["agents"] == [
        {
            "id": "root-agent",
            "name": "strix",
            "parent_id": None,
            "status": "waiting",
        },
    ]
    assert response["events"][0]["data"]["content"] == "hello"


def test_list_chats_retains_existing_sessions() -> None:
    manager = ChatManager()
    older = ChatSession(
        id="chat-older",
        message="Review the authentication flow",
        targets=[],
        scan_mode="standard",
        coordinator=AgentCoordinator(),
        live_view=TuiLiveView(),
        updated_at=10,
    )
    newer = ChatSession(
        id="chat-newer",
        message="Map the API attack surface",
        targets=["https://example.test"],
        scan_mode="quick",
        coordinator=AgentCoordinator(),
        live_view=TuiLiveView(),
        updated_at=20,
    )
    manager._sessions = {older.id: older, newer.id: newer}

    try:
        response = manager.list_chats()
        assert set(manager._sessions) == {"chat-older", "chat-newer"}
    finally:
        manager._sessions.clear()
        manager.close()

    assert [chat["id"] for chat in response["chats"]] == ["chat-newer", "chat-older"]
    assert response["chats"][0]["title"] == "Map the API attack surface"
    assert response["chats"][0]["targets"] == ["https://example.test"]
