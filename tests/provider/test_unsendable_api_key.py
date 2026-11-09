"""An API key that cannot go in an HTTP header: how it is recognised and refused."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from openai import AsyncOpenAI

from flocks.provider.credential import (
    UNSENDABLE_API_KEY_MESSAGE,
    api_key_is_sendable,
    is_unsendable_header_error,
)
from flocks.tool.system.model_config import add_provider_tool


@pytest.mark.parametrize(
    "api_key",
    ["", "sk-AbC_123", "not-needed", "key|email@example.test", "with space", "~!@#$%^&*()"],
)
def test_ascii_keys_are_sendable(api_key):
    assert api_key_is_sendable(api_key)


@pytest.mark.parametrize(
    "api_key",
    ["请在这里填写密钥", "sk-全角ａｂｃ", "••••••••", "sk​-abc", "sk-abc\n", "sk-a\tb"],
)
def test_other_keys_are_not_sendable(api_key):
    assert not api_key_is_sendable(api_key)


def test_every_key_the_check_passes_goes_through_the_http_client():
    httpx.Headers({"Authorization": "Bearer ~!@#$%^&*() with space"})


@pytest.mark.asyncio
async def test_real_client_refusal_is_recognised_and_sends_nothing():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})

    client = AsyncOpenAI(
        api_key="请在这里填写您的模型接口访问密钥吧",
        base_url="https://gateway.test/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    with pytest.raises(UnicodeEncodeError) as caught:
        await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "nihao1"}])

    # The position the page showed: right after "Bearer ".
    assert "position 7-23" in str(caught.value)
    assert is_unsendable_header_error(caught.value)
    assert requests == []


def test_other_unicode_errors_are_not_blamed_on_the_key():
    with pytest.raises(UnicodeEncodeError) as ascii_error:
        "中文".encode("ascii")
    assert not is_unsendable_header_error(ascii_error.value)

    with pytest.raises(UnicodeEncodeError) as latin_error:
        httpx.Headers({"x": "中文"}, encoding="latin-1")
    assert not is_unsendable_header_error(latin_error.value)

    assert not is_unsendable_header_error(ValueError("boom"))


@pytest.mark.asyncio
async def test_agent_tool_cannot_save_a_key_that_cannot_be_sent(monkeypatch):
    from flocks.config.config_writer import ConfigWriter

    add_provider = MagicMock(side_effect=AssertionError("nothing may be written"))
    monkeypatch.setattr(ConfigWriter, "add_provider", add_provider)
    monkeypatch.setattr(ConfigWriter, "get_provider_raw", classmethod(lambda cls, pid: None))

    result = await add_provider_tool(
        MagicMock(),
        name="cn key",
        base_url="https://api.example.test/v1",
        api_key="请在这里填写密钥",
    )

    assert result.success is False
    assert result.error == UNSENDABLE_API_KEY_MESSAGE
    add_provider.assert_not_called()
