from strix_chat_api.projection import EventProjector
from strix_chat_api.routing import chat_route


def test_chat_routes() -> None:
    assert chat_route("/api/v1/chat") == (None, "create")
    assert chat_route("/api/v1/chat/abc") == ("abc", None)
    assert chat_route("/api/v1/chat/abc/message") == ("abc", "message")
    assert chat_route("/api/v1/chat/abc/unknown") == (None, None)


def test_event_projector_emits_native_event_revisions() -> None:
    projector = EventProjector()
    native = [
        {
            "id": "chat_1",
            "version": 0,
            "type": "chat",
            "data": {"content": "hel", "metadata": {"streaming": True}},
        },
    ]

    first = projector.project(native)
    assert first[0]["id"] == 1
    assert first[0]["event_key"] == "chat_1"
    assert projector.project(native, after=1) == []

    native[0]["version"] = 1
    native[0]["data"]["content"] = "hello"
    revision = projector.project(native, after=1)
    assert revision[0]["id"] == 2
    assert revision[0]["event_key"] == "chat_1"
    assert revision[0]["data"]["content"] == "hello"
