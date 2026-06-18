"""
Tests for CLI session title generation.

Verifies that session titles are generated correctly when sessions are
initiated from the CLI, covering both the happy path and edge cases
(LLM failures, single-run mode where fire_and_forget may be cancelled).
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(title="New session - 2026-01-01T00:00:00", project_id="proj-1"):
    s = MagicMock()
    s.title = title
    s.project_id = project_id
    return s


def _make_user_msg(text: str, msg_id="msg-1"):
    msg = MagicMock()
    msg.role = "user"
    msg.id = msg_id
    part = MagicMock()
    part.type = "text"
    part.text = text
    return msg, part


def _patch_title_deps(mock_session, user_msgs, parts, mock_provider, mock_update=None):
    """Return a list of patch context managers for generate_title_after_first_message."""
    if mock_update is None:
        mock_update = AsyncMock()
    return [
        patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=mock_session)),
        patch("flocks.session.session.Session.is_default_title", return_value=True),
        patch("flocks.session.session.Session.update", new=mock_update),
        patch("flocks.session.message.Message.list", new=AsyncMock(return_value=user_msgs)),
        patch("flocks.session.message.Message.parts", new=AsyncMock(return_value=parts)),
        patch("flocks.provider.provider.Provider._ensure_initialized"),
        patch("flocks.provider.provider.Provider.get", return_value=mock_provider),
    ]


# ---------------------------------------------------------------------------
# Unit tests: generate_title_after_first_message
# ---------------------------------------------------------------------------

class TestGenerateTitleAfterFirstMessage:
    """Unit tests for SessionTitle.generate_title_after_first_message."""

    @pytest.mark.asyncio
    async def test_generates_title_and_saves_to_db(self):
        """Title is generated for the first user message and persisted to DB."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session()
        msg, part = _make_user_msg("Help me debug this Python script")
        mock_provider = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield MagicMock(delta="Debug Python")

        mock_provider.chat_stream = fake_stream
        mock_update = AsyncMock()

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider, mock_update)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
            )

        assert title == "Debug Python"
        mock_update.assert_awaited_once_with("proj-1", "sess-1", title="Debug Python")

    @pytest.mark.asyncio
    async def test_rejects_tool_call_payload_title_and_falls_back(self):
        """Tool-call payloads from the title model are not persisted as titles."""
        from flocks.session.lifecycle.title import SessionTitle

        question = (
            "based on ThreatBook Threat Intelligence, please give me reports for "
            "cyber news related to Hong Kong for the past 7 days"
        )
        mock_session = _make_session()
        msg, part = _make_user_msg(question)
        mock_provider = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield MagicMock(delta='[TOOL_CALL]\n{tool => "news", args => {\n  --query: "Hong Kong"\n}}')

        mock_provider.chat_stream = fake_stream
        mock_update = AsyncMock()

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider, mock_update)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="minimax-m2.7",
                provider_id="threatbook-cn-llm",
            )

        expected = SessionTitle._generate_simple_title(question)
        assert title == expected
        assert "TOOL_CALL" not in title
        mock_update.assert_awaited_once_with("proj-1", "sess-1", title=expected)

    def test_rejects_json_function_call_title_candidate(self):
        """Structured function-call shaped JSON is invalid as a thread title."""
        from flocks.session.lifecycle.title import SessionTitle

        title = SessionTitle._sanitize_generated_title(
            '{"name": "news", "arguments": {"query": "Hong Kong"}}'
        )

        assert title == ""

    @pytest.mark.asyncio
    async def test_skips_when_more_than_one_user_message(self):
        """Returns None when there are 2+ user messages (not the first turn)."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session()
        msg1, msg2 = MagicMock(), MagicMock()
        msg1.role = msg2.role = "user"

        with (
            patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=mock_session)),
            patch("flocks.session.session.Session.is_default_title", return_value=True),
            patch("flocks.session.message.Message.list", new=AsyncMock(return_value=[msg1, msg2])),
        ):
            result = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_skips_when_title_already_set(self):
        """Returns existing title immediately when session has a meaningful title."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session(title="Debug Python Script")

        with (
            patch("flocks.session.session.Session.get_by_id", new=AsyncMock(return_value=mock_session)),
            patch("flocks.session.session.Session.is_default_title", return_value=False),
        ):
            result = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
            )

        assert result == "Debug Python Script"

    @pytest.mark.asyncio
    async def test_falls_back_to_simple_title_when_llm_fails(self):
        """Uses _generate_simple_title and still saves to DB when LLM raises."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session()
        msg, part = _make_user_msg("Help me with something")
        mock_provider = MagicMock()

        async def failing_stream(*args, **kwargs):
            raise ConnectionError("LLM unreachable")
            yield  # make it an async generator

        mock_provider.chat_stream = failing_stream
        mock_update = AsyncMock()

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider, mock_update)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
            )

        assert title == "Help me with something"
        mock_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_publishes_sse_event_when_callback_provided(self):
        """SSE event is published when event_publish_callback is given (Web path)."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session()
        msg, part = _make_user_msg("Hello world")
        mock_provider = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield MagicMock(delta="Hello World")

        mock_provider.chat_stream = fake_stream
        event_cb = AsyncMock()

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
                event_publish_callback=event_cb,
            )

        event_cb.assert_awaited_once_with(
            "session.updated",
            {"id": "sess-1", "title": "Hello World"},
        )

    @pytest.mark.asyncio
    async def test_no_sse_event_in_cli_path(self):
        """No SSE event when event_publish_callback is None (CLI path)."""
        from flocks.session.lifecycle.title import SessionTitle

        mock_session = _make_session()
        msg, part = _make_user_msg("CLI test message")
        mock_provider = MagicMock()

        async def fake_stream(*args, **kwargs):
            yield MagicMock(delta="CLI Test")

        mock_provider.chat_stream = fake_stream
        mock_update = AsyncMock()

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider, mock_update)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
                event_publish_callback=None,
            )

        assert title == "CLI Test"
        mock_update.assert_awaited_once_with("proj-1", "sess-1", title="CLI Test")


# ---------------------------------------------------------------------------
# Unit tests: PROMPT_TITLE language rule
# ---------------------------------------------------------------------------

class TestPromptTitleLanguageRule:
    """Verify the LANGUAGE rule and Chinese examples are present in PROMPT_TITLE."""

    def test_prompt_contains_language_rule(self):
        from flocks.session.prompt_strings import PROMPT_TITLE

        assert "LANGUAGE" in PROMPT_TITLE or "same language" in PROMPT_TITLE.lower(), (
            "PROMPT_TITLE must contain a language-matching rule"
        )

    def test_prompt_contains_chinese_examples(self):
        from flocks.session.prompt_strings import PROMPT_TITLE

        has_chinese = any(
            char in PROMPT_TITLE for char in ["分析", "重构", "调试", "你好"]
        )
        assert has_chinese, "PROMPT_TITLE should include at least one Chinese example"

    @pytest.mark.asyncio
    async def test_language_rule_is_sent_to_llm(self):
        """The system prompt passed to the LLM contains the LANGUAGE rule."""
        from flocks.session.lifecycle.title import SessionTitle, _CANONICAL_TITLE_PROMPT

        assert (
            "LANGUAGE" in _CANONICAL_TITLE_PROMPT
            or "same language" in _CANONICAL_TITLE_PROMPT.lower()
        ), "_CANONICAL_TITLE_PROMPT must include the language-matching rule"

        mock_session = _make_session()
        msg, part = _make_user_msg("帮我分析这个IP地址的威胁情报")
        mock_provider = MagicMock()
        captured_messages = []

        async def fake_stream(model_id, messages, **kwargs):
            captured_messages.extend(messages)
            yield MagicMock(delta="IP威胁情报分析")

        mock_provider.chat_stream = fake_stream

        patches = _patch_title_deps(mock_session, [msg], [part], mock_provider)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6]:
            title = await SessionTitle.generate_title_after_first_message(
                session_id="sess-1",
                model_id="claude-3",
                provider_id="anthropic",
            )

        assert title == "IP威胁情报分析"

        system_msg = next((m for m in captured_messages if m.role == "system"), None)
        assert system_msg is not None
        assert "LANGUAGE" in system_msg.content or "same language" in system_msg.content.lower()

        user_msg = next((m for m in captured_messages if m.role == "user"), None)
        assert user_msg is not None
        assert "帮我分析" in user_msg.content


# ---------------------------------------------------------------------------
# Integration: _process_message calls title generation after the loop
# ---------------------------------------------------------------------------

class TestProcessMessageTitleGeneration:
    """Tests that _process_message explicitly generates title after SessionLoop.run()."""

    @pytest.mark.asyncio
    async def test_title_generated_after_successful_loop(self):
        """_process_message calls generate_title_after_first_message after a
        successful loop, ensuring the title is saved even in single-run mode
        where the fire_and_forget task may be cancelled by asyncio cleanup."""
        from flocks.cli.session_runner import CLISessionRunner
        from flocks.session.session_loop import LoopResult
        import flocks.session.session_loop as session_loop_mod
        import flocks.session.message as message_mod
        import flocks.agent.registry as agent_mod
        import flocks.provider.provider as provider_mod
        import flocks.session.lifecycle.title as title_mod
        import flocks.config.config as config_mod

        runner = CLISessionRunner(console=MagicMock(), directory=Path("/tmp"))
        runner._session = MagicMock()
        runner._session.id = "sess-test"
        runner._live = None
        runner._content_buffer = []
        runner._reasoning_buffer = []
        runner._has_content = False
        runner._has_reasoning = False
        runner._accumulated_text = ""
        runner._last_display_time = 0

        mock_agent = MagicMock()
        mock_agent.name = "rex"
        mock_provider_inst = MagicMock()
        mock_provider_inst.is_configured = MagicMock(return_value=True)
        title_generate_mock = AsyncMock(return_value="Test Title")

        mock_live_ctx = MagicMock()
        mock_live_ctx.__enter__ = MagicMock(return_value=mock_live_ctx)
        mock_live_ctx.__exit__ = MagicMock(return_value=False)
        mock_live_ctx.update = MagicMock()

        with (
            patch.object(message_mod.Message, "create", new=AsyncMock()),
            patch.object(session_loop_mod.SessionLoop, "run",
                         new=AsyncMock(return_value=LoopResult(action="done"))),
            patch.object(agent_mod.Agent, "default_agent", new=AsyncMock(return_value="rex")),
            patch.object(agent_mod.Agent, "get", new=AsyncMock(return_value=mock_agent)),
            patch.object(provider_mod.Provider, "get", return_value=mock_provider_inst),
            patch.object(title_mod.SessionTitle, "generate_title_after_first_message",
                         new=title_generate_mock),
            patch.object(config_mod.Config, "resolve_default_llm",
                         new=AsyncMock(return_value={"provider_id": "anthropic", "model_id": "claude-3"})),
            patch("flocks.cli.session_runner.Live", return_value=mock_live_ctx),
            patch("flocks.cli.session_runner.Spinner"),
        ):
            await runner._process_message("test message")

        title_generate_mock.assert_awaited_once()
        kwargs = title_generate_mock.call_args.kwargs
        assert kwargs["session_id"] == "sess-test"
        assert kwargs["provider_id"] == "anthropic"
        assert kwargs["model_id"] == "claude-3"

    @pytest.mark.asyncio
    async def test_title_not_generated_when_loop_errors(self):
        """_process_message skips title generation when the loop returns an error."""
        from flocks.cli.session_runner import CLISessionRunner
        from flocks.session.session_loop import LoopResult
        import flocks.session.session_loop as session_loop_mod
        import flocks.session.message as message_mod
        import flocks.agent.registry as agent_mod
        import flocks.provider.provider as provider_mod
        import flocks.session.lifecycle.title as title_mod
        import flocks.config.config as config_mod

        runner = CLISessionRunner(console=MagicMock(), directory=Path("/tmp"))
        runner._session = MagicMock()
        runner._session.id = "sess-test"
        runner._live = None
        runner._content_buffer = []
        runner._reasoning_buffer = []
        runner._has_content = False
        runner._has_reasoning = False
        runner._accumulated_text = ""
        runner._last_display_time = 0

        mock_agent = MagicMock()
        mock_agent.name = "rex"
        mock_provider_inst = MagicMock()
        mock_provider_inst.is_configured = MagicMock(return_value=True)
        title_generate_mock = AsyncMock(return_value=None)

        mock_live_ctx = MagicMock()
        mock_live_ctx.__enter__ = MagicMock(return_value=mock_live_ctx)
        mock_live_ctx.__exit__ = MagicMock(return_value=False)
        mock_live_ctx.update = MagicMock()

        with (
            patch.object(message_mod.Message, "create", new=AsyncMock()),
            patch.object(session_loop_mod.SessionLoop, "run",
                         new=AsyncMock(return_value=LoopResult(action="error", error="provider failed"))),
            patch.object(agent_mod.Agent, "default_agent", new=AsyncMock(return_value="rex")),
            patch.object(agent_mod.Agent, "get", new=AsyncMock(return_value=mock_agent)),
            patch.object(provider_mod.Provider, "get", return_value=mock_provider_inst),
            patch.object(title_mod.SessionTitle, "generate_title_after_first_message",
                         new=title_generate_mock),
            patch.object(config_mod.Config, "resolve_default_llm",
                         new=AsyncMock(return_value={"provider_id": "anthropic", "model_id": "claude-3"})),
            patch("flocks.cli.session_runner.Live", return_value=mock_live_ctx),
            patch("flocks.cli.session_runner.Spinner"),
        ):
            await runner._process_message("test message")

        title_generate_mock.assert_not_awaited()
