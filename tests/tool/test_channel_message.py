from flocks.tool.channel.channel_message import _normalize_channel_type
from flocks.tool.registry import ToolRegistry


def test_channel_message_normalizes_weixin_aliases() -> None:
    assert _normalize_channel_type("weixin") == "weixin"
    assert _normalize_channel_type("微信") == "weixin"
    assert _normalize_channel_type("wechat") == "weixin"
    assert _normalize_channel_type("wx") == "weixin"


def test_channel_message_normalizes_wecom_aliases() -> None:
    assert _normalize_channel_type("wecom") == "wecom"
    assert _normalize_channel_type("企业微信") == "wecom"
    assert _normalize_channel_type("企微") == "wecom"
    assert _normalize_channel_type("wechat_work") == "wecom"
    assert _normalize_channel_type("wxwork") == "wecom"


def test_channel_message_schema_includes_weixin() -> None:
    schema = ToolRegistry.get_schema("channel_message")

    assert schema is not None
    assert "wecom" in schema.properties["channel_type"]["enum"]
    assert "企业微信" in schema.properties["channel_type"]["enum"]
    assert "weixin" in schema.properties["channel_type"]["enum"]
    assert "微信" in schema.properties["channel_type"]["enum"]
