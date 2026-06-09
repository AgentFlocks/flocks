from flocks.tool.channel.channel_message import _normalize_channel_type
from flocks.tool.registry import ToolRegistry


def test_channel_message_normalizes_weixin_aliases() -> None:
    assert _normalize_channel_type("weixin") == "weixin"
    assert _normalize_channel_type("微信") == "weixin"
    assert _normalize_channel_type("wechat") == "weixin"
    assert _normalize_channel_type("wx") == "weixin"


def test_channel_message_schema_includes_weixin() -> None:
    schema = ToolRegistry.get_schema("channel_message")

    assert schema is not None
    assert "weixin" in schema.properties["channel_type"]["enum"]
    assert "微信" in schema.properties["channel_type"]["enum"]
