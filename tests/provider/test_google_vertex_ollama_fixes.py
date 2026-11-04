"""Adapter regressions found during the 2026-09 model/provider test pass.

* Google: parallel function calls get distinct ``index`` values; a prompt
  blocked by the safety filter raises a classifiable error instead of
  streaming nothing; ``anyOf`` in tool schemas is unwrapped (case-insensitive).
* Vertex Anthropic: a non-200 streaming response raises with the status code
  instead of parsing the error body as SSE; missing ADC + missing gcloud
  surfaces one ValueError.
* Ollama: the shared openai_base client (retry cap + capped http client) is
  used with the ``ollama`` placeholder key when no key is configured.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.provider.provider import ChatMessage, ProviderConfig


class _AIter:
    def __init__(self, items):
        self._items = list(items)

    def __aiter__(self):
        self._it = iter(self._items)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _google():
    from flocks.provider.sdk.google import GoogleProvider

    p = GoogleProvider()
    p.configure(ProviderConfig(provider_id="google", api_key="k"))
    return p


def _gchunk(parts=None, candidates=True, block=None):
    cand = [SimpleNamespace(content=SimpleNamespace(parts=parts or []))] if candidates else []
    return SimpleNamespace(candidates=cand, usage_metadata=None, prompt_feedback=SimpleNamespace(block_reason=block) if block else None)


def _gpart(text=None, thought=None, fc=None):
    return SimpleNamespace(text=text, thought=thought, function_call=fc)


def _run_google(p, chunks):
    p._client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content_stream=AsyncMock(return_value=_AIter(chunks)))))
    return p.chat_stream("gemini-2.5-pro", [ChatMessage(role="user", content="hi")])


@pytest.mark.asyncio
async def test_google_parallel_function_calls_get_distinct_indexes():
    fcs = [_gpart(fc=SimpleNamespace(name="a", args={"x": 1})), _gpart(fc=SimpleNamespace(name="b", args={"y": 2}))]
    chunks = [c async for c in _run_google(_google(), [_gchunk(fcs)])]
    calls = chunks[0].tool_calls
    assert [t["index"] for t in calls] == [0, 1]
    assert [t["function"]["name"] for t in calls] == ["a", "b"]
    assert chunks[-1].finish_reason == "tool-calls"


@pytest.mark.asyncio
async def test_google_safety_block_raises_content_policy_error():
    from flocks.session.runner import SessionRunner

    with pytest.raises(RuntimeError) as exc:
        [c async for c in _run_google(_google(), [_gchunk(candidates=False, block=SimpleNamespace(name="SAFETY"))])]
    assert "SAFETY" in str(exc.value)
    decision = SessionRunner.classify_failover_error({"name": "RuntimeError", "message": str(exc.value), "data": {"message": str(exc.value)}})
    assert decision.reason == "content_policy"


def test_google_tool_schema_unwraps_anyof_case_insensitively():
    p = _google()
    decl = p._convert_tools([{
        "type": "function",
        "function": {
            "name": "f",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "x": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "y": {"anyof": [{"type": "integer"}, {"type": "null"}]},
                },
            },
        },
    }])
    params = decl[0]["function_declarations"][0]["parameters"]
    assert "additionalProperties" not in json.dumps(params)
    assert params["properties"]["x"] == {"type": "string"}
    assert params["properties"]["y"] == {"type": "integer"}


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self.text = body
        self._body = body.encode()

    def json(self):
        return json.loads(self.text)

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self.text.splitlines():
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Client:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        return self._resp

    def stream(self, *a, **kw):
        return self._resp


@pytest.mark.asyncio
async def test_vertex_stream_non_200_raises_with_status():
    from flocks.provider.sdk.vertex_anthropic import VertexAnthropicProvider

    p = VertexAnthropicProvider()
    p.project = "proj"
    with patch.object(p, "_get_access_token", AsyncMock(return_value="tok")), patch("httpx.AsyncClient", lambda *a, **kw: _Client(_Resp(401, '{"error": {"message": "unauthorized"}}'))):
        with pytest.raises(Exception) as exc:
            [c async for c in p.chat_stream("claude", [ChatMessage(role="user", content="hi")])]
    assert "Vertex Anthropic API error: 401" in str(exc.value)


@pytest.mark.asyncio
async def test_vertex_missing_adc_and_gcloud_is_one_value_error():
    import google.auth as gauth

    from flocks.provider.sdk.vertex_anthropic import VertexAnthropicProvider

    p = VertexAnthropicProvider()
    p.project = "proj"
    with patch.object(gauth, "default", side_effect=Exception("no adc")), patch("subprocess.run", side_effect=FileNotFoundError("gcloud")):
        with pytest.raises(ValueError) as exc:
            await p._get_access_token()
    assert "Could not get Google Cloud credentials" in str(exc.value)


def test_ollama_uses_shared_client_with_placeholder_key():
    from flocks.provider.sdk.ollama import OllamaProvider

    p = OllamaProvider()
    p.configure(ProviderConfig(provider_id="ollama", base_url="http://localhost:11434/v1"))
    with patch("openai.AsyncOpenAI") as cls, patch("flocks.provider.sdk.openai_base.create_openai_http_client") as hc:
        hc.return_value = MagicMock(name="capped")
        p._get_client()
    kw = cls.call_args.kwargs
    assert kw["api_key"] == "ollama"
    assert kw["max_retries"] == 0
    assert kw["http_client"] is hc.return_value
    assert OllamaProvider().is_configured() is True
