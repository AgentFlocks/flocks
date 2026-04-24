"""
Targeted regression tests for the three issues raised in the PR review of
``fix(provider/google): robust Gemini 3 support``:

1. ``chat_stream()`` must not bundle ``reasoning`` together with ``delta`` /
   ``tool_calls`` in a single chunk – consumers that special-case
   reasoning-bearing chunks would otherwise drop text/tool calls.

2. ``chat()`` and ``chat_stream()`` must honour caller-provided
   ``thinkingConfig`` (built per-model by
   :func:`flocks.provider.options.build_provider_options`) and ``max_tokens``
   instead of hard-coding ``max_output_tokens=8192``.

3. ``_convert_messages()`` must accept an explicit ``session_id`` so the
   database-backed reasoning replay actually activates – it cannot rely on
   ``messages[0].sessionID`` because :class:`flocks.provider.provider.ChatMessage`
   does not carry that attribute and the runner does not set it.
"""

from __future__ import annotations

from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.provider.provider import ChatMessage, StreamChunk
from flocks.provider.sdk.google import GoogleProvider


# ---------------------------------------------------------------------------
# Helpers: minimal mocks that look like google-genai stream parts.
# ---------------------------------------------------------------------------


class _Part:
    """Mimic a google-genai response Part (text / thought / function_call)."""

    def __init__(self, text: str = "", thought: str = "", function_call=None):
        if text:
            self.text = text
        if thought:
            self.thought = thought
        if function_call is not None:
            self.function_call = function_call


class _FunctionCall:
    def __init__(self, name: str, args: Dict[str, Any]):
        self.name = name
        self.args = args


class _Candidate:
    def __init__(self, parts: List[_Part]):
        self.content = MagicMock()
        self.content.parts = parts


class _Usage:
    def __init__(self, p: int = 0, c: int = 0, t: int = 0):
        self.prompt_token_count = p
        self.candidates_token_count = c
        self.total_token_count = t


class _Chunk:
    def __init__(self, parts: List[_Part], usage=None):
        self.candidates = [_Candidate(parts)] if parts is not None else []
        self.usage_metadata = usage


async def _aiter(items):
    for it in items:
        yield it


def _make_provider_with_stream(stream_chunks):
    """Build a GoogleProvider whose client streams ``stream_chunks``."""
    provider = GoogleProvider()

    fake_client = MagicMock()
    fake_client.aio = MagicMock()
    fake_client.aio.models = MagicMock()
    fake_client.aio.models.generate_content_stream = AsyncMock(
        return_value=_aiter(stream_chunks)
    )
    fake_client.aio.models.generate_content = AsyncMock()

    provider._client = fake_client
    return provider, fake_client


# ---------------------------------------------------------------------------
# Issue #1 – reasoning must be emitted as its own chunk.
# ---------------------------------------------------------------------------


class TestReasoningSeparation:
    """Verify chat_stream() never bundles reasoning with text/tool_calls."""

    @pytest.mark.asyncio
    async def test_reasoning_and_text_emitted_in_separate_chunks(self):
        # Single SDK chunk that carries BOTH a thought and a text part – this
        # is the failure mode pointed out by the reviewer.
        sdk_chunks = [
            _Chunk(
                parts=[
                    _Part(thought="thinking step 1"),
                    _Part(text="hello world"),
                ],
                usage=_Usage(p=10, c=20, t=30),
            ),
        ]

        provider, _ = _make_provider_with_stream(sdk_chunks)

        emitted: List[StreamChunk] = []
        async for chunk in provider.chat_stream(
            "gemini-3-pro",
            [ChatMessage(role="user", content="hi")],
        ):
            emitted.append(chunk)

        # One reasoning chunk + one text chunk + final stop chunk.
        reasoning_chunks = [c for c in emitted if c.reasoning]
        text_chunks = [c for c in emitted if c.delta]

        assert reasoning_chunks, "expected at least one reasoning-only chunk"
        assert text_chunks, "expected at least one text chunk – text was dropped"

        # Crucial invariant: reasoning is never combined with non-empty delta
        # or tool_calls in the same chunk.
        for c in emitted:
            if c.reasoning:
                assert not c.delta, (
                    "reasoning chunk also carried text delta – consumer "
                    "would drop the text"
                )
                assert not c.tool_calls, (
                    "reasoning chunk also carried tool_calls – consumer "
                    "would drop the tool calls"
                )

    @pytest.mark.asyncio
    async def test_reasoning_and_tool_call_emitted_in_separate_chunks(self):
        sdk_chunks = [
            _Chunk(
                parts=[
                    _Part(thought="deciding to call search"),
                    _Part(function_call=_FunctionCall("search", {"q": "ai"})),
                ],
            ),
        ]

        provider, _ = _make_provider_with_stream(sdk_chunks)

        emitted: List[StreamChunk] = []
        async for chunk in provider.chat_stream(
            "gemini-3-pro",
            [ChatMessage(role="user", content="hi")],
        ):
            emitted.append(chunk)

        reasoning_chunks = [c for c in emitted if c.reasoning]
        tool_chunks = [c for c in emitted if c.tool_calls]

        assert reasoning_chunks, "reasoning was not surfaced"
        assert tool_chunks, "tool_calls were dropped alongside reasoning"

        for c in emitted:
            if c.reasoning:
                assert not c.tool_calls

    @pytest.mark.asyncio
    async def test_reasoning_only_chunk_marks_event_type(self):
        sdk_chunks = [
            _Chunk(parts=[_Part(thought="just thinking")]),
        ]

        provider, _ = _make_provider_with_stream(sdk_chunks)

        emitted = [c async for c in provider.chat_stream(
            "gemini-3-pro", [ChatMessage(role="user", content="hi")]
        )]

        reasoning = [c for c in emitted if c.reasoning]
        assert reasoning
        assert reasoning[0].event_type == "reasoning"
        assert reasoning[0].delta == ""


# ---------------------------------------------------------------------------
# Issue #2 – thinkingConfig and max_tokens must be forwarded to Gemini.
# ---------------------------------------------------------------------------


class TestThinkingConfigAndMaxTokens:
    @pytest.mark.asyncio
    async def test_chat_forwards_max_tokens_and_thinking_config(self):
        provider = GoogleProvider()

        captured: Dict[str, Any] = {}

        async def _fake_generate_content(*, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            resp = MagicMock()
            resp.candidates = []
            resp.usage_metadata = None
            resp.text = ""
            return resp

        fake_client = MagicMock()
        fake_client.aio = MagicMock()
        fake_client.aio.models = MagicMock()
        fake_client.aio.models.generate_content = _fake_generate_content
        provider._client = fake_client

        await provider.chat(
            "gemini-3-pro",
            [ChatMessage(role="user", content="hi")],
            max_tokens=32_000,
            thinkingConfig={"includeThoughts": True, "thinkingLevel": "high"},
            temperature=0.3,
        )

        cfg = captured["config"]
        assert cfg["max_output_tokens"] == 32_000, (
            "caller-provided max_tokens must override the 8192 default"
        )
        assert cfg["thinking_config"] == {
            "includeThoughts": True,
            "thinkingLevel": "high",
        }, "thinkingConfig must be forwarded to Gemini"
        assert cfg["temperature"] == 0.3

    @pytest.mark.asyncio
    async def test_chat_stream_forwards_max_tokens_and_thinking_config(self):
        captured: Dict[str, Any] = {}

        async def _fake_stream(*, model, contents, config):
            captured["model"] = model
            captured["config"] = config
            return _aiter([])

        provider = GoogleProvider()
        fake_client = MagicMock()
        fake_client.aio = MagicMock()
        fake_client.aio.models = MagicMock()
        fake_client.aio.models.generate_content_stream = _fake_stream
        provider._client = fake_client

        async for _ in provider.chat_stream(
            "gemini-2.5-pro",
            [ChatMessage(role="user", content="hi")],
            max_tokens=16_000,
            thinkingConfig={"includeThoughts": True, "thinkingBudget": 4000},
        ):
            pass

        cfg = captured["config"]
        assert cfg["max_output_tokens"] == 16_000
        assert cfg["thinking_config"] == {
            "includeThoughts": True,
            "thinkingBudget": 4000,
        }

    @pytest.mark.asyncio
    async def test_chat_stream_falls_back_to_default_when_no_max_tokens(self):
        captured: Dict[str, Any] = {}

        async def _fake_stream(*, model, contents, config):
            captured["config"] = config
            return _aiter([])

        provider = GoogleProvider()
        fake_client = MagicMock()
        fake_client.aio = MagicMock()
        fake_client.aio.models = MagicMock()
        fake_client.aio.models.generate_content_stream = _fake_stream
        provider._client = fake_client

        async for _ in provider.chat_stream(
            "gemini-2.0-pro",
            [ChatMessage(role="user", content="hi")],
        ):
            pass

        # No caller-provided value → safe default.
        assert captured["config"]["max_output_tokens"] == 8192
        # No thinking_config when none was provided.
        assert "thinking_config" not in captured["config"]


# ---------------------------------------------------------------------------
# Issue #3 – session_id must be honoured for DB-backed reasoning replay.
# ---------------------------------------------------------------------------


class TestSessionIdForwarding:
    """Verify the explicit session_id kwarg actually drives _convert_messages."""

    def test_convert_messages_accepts_explicit_session_id(self):
        provider = GoogleProvider()

        with patch("flocks.session.message.MessageSync") as mock_ms:
            mock_ms.list_with_parts.return_value = []

            system_msg, _msgs = provider._convert_messages(
                [ChatMessage(role="user", content="hi")],
                session_id="ses_explicit_42",
            )

            mock_ms.list_with_parts.assert_called_once_with("ses_explicit_42")
            assert "SecOps assistant" in system_msg

    def test_convert_messages_session_id_falls_back_to_legacy_attr(self):
        """Backward compat: still honour ``ChatMessage.session_id`` if set."""
        provider = GoogleProvider()

        msg = ChatMessage(role="user", content="hi")
        # Pydantic models accept arbitrary extra attrs at instance level via
        # __dict__ – we simulate a future caller stamping session_id on the
        # message itself.
        object.__setattr__(msg, "session_id", "ses_legacy_99")

        with patch("flocks.session.message.MessageSync") as mock_ms:
            mock_ms.list_with_parts.return_value = []

            provider._convert_messages([msg])

            mock_ms.list_with_parts.assert_called_once_with("ses_legacy_99")

    def test_convert_messages_no_session_id_skips_db_lookup(self):
        provider = GoogleProvider()

        with patch("flocks.session.message.MessageSync") as mock_ms:
            provider._convert_messages(
                [ChatMessage(role="user", content="hi")],
                session_id=None,
            )

            mock_ms.list_with_parts.assert_not_called()

    def test_convert_messages_empty_db_cache_falls_back_to_messages(self):
        """Cross-process / cold-start: ``MessageSync.list_with_parts`` may
        return ``[]`` even with a valid ``session_id``.  We must fall back
        to the in-memory ``messages`` argument or Gemini will get an empty
        ``contents`` list and reject the request.
        """
        provider = GoogleProvider()

        with patch("flocks.session.message.MessageSync") as mock_ms:
            mock_ms.list_with_parts.return_value = []

            _system, gemini_msgs = provider._convert_messages(
                [ChatMessage(role="user", content="hi from memory")],
                session_id="ses_cold_cache",
            )

            mock_ms.list_with_parts.assert_called_once_with("ses_cold_cache")
            # Fallback must produce at least one user turn so the API call
            # is not made with an empty contents list.
            assert gemini_msgs, "fallback to in-memory messages did not run"
            assert any(
                any(p.get("text") == "hi from memory" for p in m["parts"])
                for m in gemini_msgs
            ), "expected the in-memory user message to appear in fallback output"

    def test_convert_messages_db_with_only_system_does_not_double_accumulate(self):
        """When the DB snapshot contains only ``system`` rows and no
        user/assistant turns, the DB pass produces no gemini turns and we
        must fall back.  But the system text accumulated during the DB pass
        must NOT also appear in the final ``system_msg`` – otherwise any
        in-memory system message would be appended on top of the DB one,
        and the in-memory system would itself be added by fallback,
        producing a duplicated prompt.
        """
        provider = GoogleProvider()

        sys_mwp = MagicMock()
        sys_mwp.info = MagicMock(role="system")
        sys_part = MagicMock()
        sys_part.type = "text"
        sys_part.text = "DB-only system content"
        sys_mwp.parts = [sys_part]

        with patch("flocks.session.message.MessageSync") as mock_ms:
            mock_ms.list_with_parts.return_value = [sys_mwp]

            system_msg, gemini_msgs = provider._convert_messages(
                [
                    ChatMessage(role="system", content="memory system"),
                    ChatMessage(role="user", content="hello"),
                ],
                session_id="ses_only_system_in_db",
            )

        # Fallback ran, so the in-memory system message must be present
        # exactly once and the DB system content must NOT have leaked in.
        assert system_msg.count("memory system") == 1
        assert "DB-only system content" not in system_msg, (
            "DB-derived system text leaked into fallback path – this would "
            "cause a duplicated system prompt"
        )
        # Fallback successfully produced the user turn.
        assert any(
            any(p.get("text") == "hello" for p in m["parts"])
            for m in gemini_msgs
        )

    def test_convert_messages_db_exception_midloop_does_not_duplicate_history(self):
        """The DB iteration may throw partway through (corrupt cache row,
        attribute error on a part, etc.).  In that case the partially
        accumulated DB turns must be discarded so the fallback path doesn't
        produce a doubled conversation history.
        """
        provider = GoogleProvider()

        good_mwp = MagicMock()
        good_mwp.info = MagicMock(role="user")
        good_part = MagicMock()
        good_part.type = "text"
        good_part.text = "earlier user from db"
        good_mwp.parts = [good_part]

        # Trigger an exception by giving the second mwp a parts attribute
        # that explodes when iterated.
        bad_mwp = MagicMock()
        bad_mwp.info = MagicMock(role="assistant")
        bad_parts = MagicMock()
        bad_parts.__iter__ = MagicMock(side_effect=RuntimeError("boom"))
        bad_mwp.parts = bad_parts

        with patch("flocks.session.message.MessageSync") as mock_ms:
            mock_ms.list_with_parts.return_value = [good_mwp, bad_mwp]

            _system, gemini_msgs = provider._convert_messages(
                [ChatMessage(role="user", content="memory user")],
                session_id="ses_corrupt_db",
            )

        # The DB-derived "earlier user from db" turn must NOT appear in the
        # final result – we must have cleanly fallen back to the in-memory
        # message list with no doubling.
        all_text = " | ".join(
            p.get("text", "")
            for m in gemini_msgs
            for p in m["parts"]
        )
        assert "earlier user from db" not in all_text, (
            "partial DB state leaked into fallback output – conversation "
            "history would be duplicated"
        )
        assert "memory user" in all_text, "fallback did not emit in-memory message"

    @pytest.mark.asyncio
    async def test_chat_stream_passes_session_id_to_convert_messages(self):
        provider = GoogleProvider()
        fake_client = MagicMock()
        fake_client.aio = MagicMock()
        fake_client.aio.models = MagicMock()

        async def _empty_stream(**_kw):
            return _aiter([])

        fake_client.aio.models.generate_content_stream = _empty_stream
        provider._client = fake_client

        with patch.object(
            provider, "_convert_messages", wraps=provider._convert_messages
        ) as spy:
            async for _ in provider.chat_stream(
                "gemini-3-pro",
                [ChatMessage(role="user", content="hi")],
                session_id="ses_from_runner",
            ):
                pass

        # _convert_messages must receive session_id from kwargs, not None.
        _, kwargs = spy.call_args
        assert kwargs.get("session_id") == "ses_from_runner"
