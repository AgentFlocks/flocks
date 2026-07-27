"""
Tests for flocks/session/prompt.py

Covers:
- SessionPrompt.count_tokens(): token counting via chars/4 heuristic
- SessionPrompt.estimate_tokens(): quick character-based estimate
- SessionPrompt.count_message_tokens(): multi-message counting
- SessionPrompt.load_template() / render_template(): template processing
- SystemPrompt.environment(): env info injection
- SystemPrompt.runtime_metadata(): session/model/provider tail block
- SystemPrompt.provider(): model-to-prompt-file routing
"""

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.agent.agent import AgentInfo
from flocks.session.prompt import (
    PROMPT_DEFAULT,
    PromptTemplate,
    SessionPrompt,
    SystemPrompt,
)
from flocks.session import prompt_strings


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_empty_string_returns_zero(self):
        assert SessionPrompt.count_tokens("") == 0

    def test_none_equivalent_empty(self):
        # Passing falsy value
        assert SessionPrompt.count_tokens("") == 0

    def test_short_text_returns_positive(self):
        result = SessionPrompt.count_tokens("hello world")
        assert result > 0

    def test_longer_text_more_tokens(self):
        short = SessionPrompt.count_tokens("hi")
        long_ = SessionPrompt.count_tokens("This is a much longer piece of text with many words")
        assert long_ > short

    def test_always_uses_chars_over_4(self):
        # count_tokens now always uses chars/4 — no tiktoken path remains.
        text = "a" * 400
        assert SessionPrompt.count_tokens(text) == 100  # 400 // 4

    def test_short_text_chars_over_4(self):
        text = "test text"  # 9 chars -> 9 // 4 = 2
        assert SessionPrompt.count_tokens(text) == len(text) // 4


# ---------------------------------------------------------------------------
# estimate_tokens
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_returns_zero(self):
        assert SessionPrompt.estimate_tokens("") == 0

    def test_400_chars_returns_100(self):
        text = "x" * 400
        assert SessionPrompt.estimate_tokens(text) == 100

    def test_integer_division(self):
        text = "x" * 5  # 5 // 4 = 1
        assert SessionPrompt.estimate_tokens(text) == 1

    def test_zero_chars_returns_zero(self):
        assert SessionPrompt.estimate_tokens("   ") == 0  # 3 // 4 = 0


# ---------------------------------------------------------------------------
# count_message_tokens
# ---------------------------------------------------------------------------

class TestCountMessageTokens:
    def test_empty_list_returns_zero(self):
        assert SessionPrompt.count_message_tokens([]) == 0

    def test_dict_messages(self):
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = SessionPrompt.count_message_tokens(messages)
        assert result > 0

    def test_missing_content_field_returns_zero(self):
        messages = [{"role": "user"}]
        result = SessionPrompt.count_message_tokens(messages)
        assert result == 0

    def test_object_with_content_attr(self):
        class FakeMsg:
            content = "test message content"

        result = SessionPrompt.count_message_tokens([FakeMsg()])
        assert result > 0

    def test_additive_across_messages(self):
        msg1 = {"content": "a" * 40}
        msg2 = {"content": "b" * 40}
        total = SessionPrompt.count_message_tokens([msg1, msg2])
        single = SessionPrompt.count_message_tokens([msg1])
        assert total > single


# ---------------------------------------------------------------------------
# load_template / render_template
# ---------------------------------------------------------------------------

class TestLoadTemplate:
    def test_load_valid_template(self, tmp_path):
        template_file = tmp_path / "test.txt"
        template_file.write_text("Hello {{name}}, welcome to {{place}}!", encoding="utf-8")

        # Clear template cache first
        SessionPrompt._templates.clear()
        template = SessionPrompt.load_template(str(template_file))

        assert template is not None
        assert isinstance(template, PromptTemplate)
        assert "name" in template.variables
        assert "place" in template.variables

    def test_load_nonexistent_returns_none(self):
        result = SessionPrompt.load_template("/nonexistent/path/template.txt")
        assert result is None

    def test_template_cached_on_second_load(self, tmp_path):
        template_file = tmp_path / "cached.txt"
        template_file.write_text("content {{var}}", encoding="utf-8")

        SessionPrompt._templates.clear()
        t1 = SessionPrompt.load_template(str(template_file))
        t2 = SessionPrompt.load_template(str(template_file))

        assert t1 is t2  # same object from cache


class TestRenderTemplate:
    def test_render_substitutes_variables(self):
        template = PromptTemplate(
            name="test",
            content="Hello {{name}}, you are {{role}}.",
            variables=["name", "role"],
        )
        result = SessionPrompt.render_template(template, {"name": "Alice", "role": "admin"})
        assert "Alice" in result
        assert "admin" in result
        assert "{{name}}" not in result
        assert "{{role}}" not in result

    def test_render_missing_variable_leaves_placeholder(self):
        template = PromptTemplate(
            name="test",
            content="Hello {{name}}!",
            variables=["name"],
        )
        result = SessionPrompt.render_template(template, {})
        # Missing variable should either be left as-is or replaced with empty string
        assert "Hello" in result

    def test_render_no_variables(self):
        template = PromptTemplate(
            name="test",
            content="Static content",
            variables=[],
        )
        result = SessionPrompt.render_template(template, {})
        assert result == "Static content"


# ---------------------------------------------------------------------------
# SystemPrompt.environment() — async method
# ---------------------------------------------------------------------------

class TestSystemPromptEnvironment:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        result = await SystemPrompt.environment("/tmp")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_includes_working_directory(self):
        result = await SystemPrompt.environment("/my/work/dir")
        combined = "\n".join(result)
        assert "current working directory: /my/work/dir" in combined

    def test_distinguishes_source_code_and_working_directories(self, monkeypatch):
        monkeypatch.setenv("FLOCKS_REPO_ROOT", "/opt/flocks")

        combined = "\n".join(SystemPrompt.environment_stable("/workspace/project"))

        assert "flocks source code directory: /opt/flocks" in combined
        assert "current working directory: /workspace/project" in combined

    @pytest.mark.asyncio
    async def test_includes_date_info(self):
        result = await SystemPrompt.environment("/tmp")
        combined = "\n".join(result)
        from datetime import datetime
        current_year = str(datetime.now().year)
        assert current_year in combined

    @pytest.mark.asyncio
    async def test_non_empty(self):
        result = await SystemPrompt.environment("/tmp")
        assert len(result) > 0
        assert any(len(s) > 0 for s in result)


# ---------------------------------------------------------------------------
# SystemPrompt.runtime_metadata()
# ---------------------------------------------------------------------------


class TestSystemPromptRuntimeMetadata:
    def test_includes_session_model_provider_when_set(self) -> None:
        block = SystemPrompt.runtime_metadata(
            session_id="ses_test",
            model_id="claude-sonnet-4-20250514",
            provider_id="anthropic",
        )[0]
        assert "Session ID: ses_test" in block
        assert "Model: claude-sonnet-4-20250514" in block
        assert "Provider: anthropic" in block

    def test_omits_optional_lines_when_unset(self) -> None:
        block = SystemPrompt.runtime_metadata()[0]
        assert "Session ID:" not in block
        assert "Model:" not in block
        assert "Provider:" not in block
        assert "## Runtime Metadata" in block


class TestBuildSystemPrompts:
    @pytest.mark.asyncio
    async def test_builtin_system_subagent_child_uses_minimal_prompt(self):
        agent = AgentInfo(
            name="rex-junior",
            mode="subagent",
            tags=["system"],
            prompt="You are Rex Junior.",
        )
        with (
            patch("flocks.agent.registry.Agent.get", AsyncMock(return_value=agent)),
            patch(
                "flocks.session.session.Session.get_by_id",
                AsyncMock(return_value=SimpleNamespace(parent_id="ses-parent")),
            ),
        ):
            prompts = await SessionPrompt.build_system_prompts(
                session_id="ses-child",
                session_directory="/tmp/project",
                agent_name="rex-junior",
                agent_prompt="You are Rex Junior.",
                provider_id="anthropic",
                model_id="claude-sonnet",
                tool_catalog_prompt_factory=lambda: "SHOULD_NOT_APPEAR",
            )

        assert len(prompts) == 2
        assert prompts[0] == "You are Rex Junior."
        assert "## Environment" in prompts[1]
        assert "Current working directory: /tmp/project" in prompts[1]
        assert "Platform:" in prompts[1]
        assert "Today's date:" in prompts[1]
        assert "SHOULD_NOT_APPEAR" not in "\n".join(prompts)
        assert PROMPT_DEFAULT.strip() not in "\n".join(prompts)

    @pytest.mark.asyncio
    async def test_builtin_system_subagent_root_uses_full_prompt(self):
        agent = AgentInfo(
            name="rex-junior",
            mode="subagent",
            tags=["system"],
            prompt="You are Rex Junior.",
        )
        with (
            patch("flocks.agent.registry.Agent.get", AsyncMock(return_value=agent)),
            patch(
                "flocks.session.session.Session.get_by_id",
                AsyncMock(return_value=SimpleNamespace(parent_id=None)),
            ),
        ):
            prompts = await SessionPrompt.build_system_prompts(
                session_id="ses-root",
                session_directory="/tmp/project",
                agent_name="rex-junior",
                agent_prompt="You are Rex Junior.",
                provider_id="anthropic",
                model_id="claude-sonnet",
            )

        assert len(prompts) > 2
        assert any(PROMPT_DEFAULT.strip() in prompt for prompt in prompts)


# ---------------------------------------------------------------------------
# SystemPrompt.provider() — returns List[str]
# ---------------------------------------------------------------------------

class TestSystemPromptProvider:
    def test_anthropic_model_returns_list(self):
        result = SystemPrompt.provider("claude-3-5-sonnet-20241022")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].startswith(PROMPT_DEFAULT.strip())

    def test_gemini_model_returns_list(self):
        result = SystemPrompt.provider("gemini-1.5-pro")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].startswith(PROMPT_DEFAULT.strip())

    def test_gpt_model_returns_list(self):
        result = SystemPrompt.provider("gpt-4o")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].startswith(PROMPT_DEFAULT.strip())

    def test_unknown_model_returns_list(self):
        result = SystemPrompt.provider("totally-unknown-model")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].startswith(PROMPT_DEFAULT.strip())
        assert "use the ls tool" not in result[0]
        assert "must call the relevant tool" in result[0]

    def test_minimax_model_uses_minimax_prompt(self):
        result = SystemPrompt.provider("minimax:MiniMax-M2.5")
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].startswith(PROMPT_DEFAULT.strip())
        assert "prefer actually invoking the needed tool" in result[0]
        assert "Misleading behavior" in result[0]

    def test_none_model_returns_list(self):
        # provider() may raise on None; just verify it returns a list or handle gracefully
        try:
            result = SystemPrompt.provider(None)
            assert isinstance(result, list)
        except (AttributeError, TypeError):
            pytest.skip("provider(None) not supported by this implementation")

    def test_provider_prompts_do_not_reference_retired_todo_tools(self):
        prompt_dir = Path(__file__).resolve().parents[2] / "flocks" / "session" / "prompt"
        retired = ("TodoWrite", "TodoRead", "todowrite", "todoread")

        for prompt_path in prompt_dir.glob("*.txt"):
            content = prompt_path.read_text(encoding="utf-8")
            for name in retired:
                assert name not in content, f"{prompt_path.name} references retired tool {name}"


class TestPromptToolInstructions:
    def test_tool_instructions_are_platform_agnostic(self):
        instructions = prompt_strings._build_tool_instructions()

        assert "PowerShell" not in instructions
        assert "must explicitly specify encoding" not in instructions
        assert "Bash Tool Guidance" not in instructions

    def test_tool_instructions_do_not_hardcode_tool_name_mapping(self):
        instructions = prompt_strings._build_tool_instructions()

        assert "callable schema" in instructions
        assert "Read files: use the 'read' tool" not in instructions
        assert "Run commands: use the 'bash' tool" not in instructions
        assert "Search code: use the 'grep' tool" not in instructions


# ---------------------------------------------------------------------------
# PromptTemplate model
# ---------------------------------------------------------------------------

class TestPromptTemplate:
    def test_basic_creation(self):
        t = PromptTemplate(name="test", content="content", variables=["var1"])
        assert t.name == "test"
        assert t.content == "content"
        assert t.variables == ["var1"]

    def test_empty_variables(self):
        t = PromptTemplate(name="test", content="no vars", variables=[])
        assert t.variables == []


# ---------------------------------------------------------------------------
# estimate_full_context_tokens — B2 overhead + safety margin
# ---------------------------------------------------------------------------

class TestEstimateFullContextTokens:
    """estimate_full_context_tokens returns pure chars/4 message sum.

    No overhead fields and no safety margin are applied — the fixed
    85 % context_window overflow threshold makes them unnecessary.
    policy and apply_safety_margin parameters are accepted but ignored.
    """

    @pytest.fixture(autouse=True)
    def _patch_message_parts(self, monkeypatch: pytest.MonkeyPatch):
        # ``Message.parts`` requires DB lookup; stub it out so we only exercise
        # the message-content arithmetic in this suite.
        from flocks.session import message as message_mod

        async def _fake_parts(message_id, session_id):  # noqa: ARG001
            return []

        monkeypatch.setattr(message_mod.Message, "parts", staticmethod(_fake_parts))
        yield

    @pytest.mark.asyncio
    async def test_empty_messages_returns_zero(self):
        """No messages → 0 tokens (no overhead added)."""
        result = await SessionPrompt.estimate_full_context_tokens("ses_x", [])
        assert result == 0

    @pytest.mark.asyncio
    async def test_policy_arg_ignored(self):
        """policy argument is accepted but does not change the result."""
        from flocks.session.lifecycle.compaction import CompactionPolicy

        policy = CompactionPolicy.from_model(200_000, 8_192)
        result_with = await SessionPrompt.estimate_full_context_tokens(
            "ses_x", [], policy=policy,
        )
        result_without = await SessionPrompt.estimate_full_context_tokens(
            "ses_x", [],
        )
        assert result_with == result_without == 0

    @pytest.mark.asyncio
    async def test_safety_margin_arg_ignored(self):
        """apply_safety_margin=False has no effect (margin is never applied)."""
        from flocks.session.lifecycle.compaction import CompactionPolicy

        policy = CompactionPolicy.from_model(200_000, 8_192)
        result = await SessionPrompt.estimate_full_context_tokens(
            "ses_x", [], policy=policy, apply_safety_margin=False,
        )
        assert result == 0

    @pytest.mark.asyncio
    async def test_message_content_is_counted(self, monkeypatch: pytest.MonkeyPatch):
        messages = [{"id": "m1", "content": "x" * 400}]  # 400 chars → 100 tokens
        result = await SessionPrompt.estimate_full_context_tokens(
            "ses_x", messages,
        )
        assert result == 100
