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
