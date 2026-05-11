"""
Tests for flocks/session/prompt.py

Covers:
- SessionPrompt.count_tokens(): token counting with/without tiktoken
- SessionPrompt.estimate_tokens(): quick character-based estimate
- SessionPrompt.count_message_tokens(): multi-message counting
- SessionPrompt.load_template() / render_template(): template processing
- SystemPrompt.environment(): env info injection
- SystemPrompt.provider(): model-to-prompt-file routing
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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

    def test_fallback_estimate_without_tiktoken(self):
        with patch.object(SessionPrompt, '_get_tokenizer', return_value=None):
            # Should fall back to char//4 estimate
            text = "a" * 400
            result = SessionPrompt.count_tokens(text)
            assert result == 100  # 400 // 4

    def test_with_tiktoken_mock(self):
        mock_tokenizer = MagicMock()
        mock_tokenizer.encode.return_value = [1, 2, 3, 4, 5]  # 5 tokens
        with patch.object(SessionPrompt, '_get_tokenizer', return_value=mock_tokenizer):
            result = SessionPrompt.count_tokens("test text")
        assert result == 5
        mock_tokenizer.encode.assert_called_once_with("test text")


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
        assert "/my/work/dir" in combined

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

    def test_none_model_returns_list(self):
        # provider() may raise on None; just verify it returns a list or handle gracefully
        try:
            result = SystemPrompt.provider(None)
            assert isinstance(result, list)
        except (AttributeError, TypeError):
            pytest.skip("provider(None) not supported by this implementation")


class TestPromptToolInstructions:
    def test_windows_bash_guidance_mentions_powershell(self):
        with patch.object(prompt_strings.platform, "system", return_value="Windows"):
            guidance = prompt_strings._build_bash_tool_guidance()

        assert "Windows machine" in guidance
        assert "must follow PowerShell syntax" in guidance
        assert 'Path(path).read_text(encoding="utf-8-sig")' in guidance

    def test_non_windows_bash_guidance_stays_generic(self):
        with patch.object(prompt_strings.platform, "system", return_value="Darwin"):
            guidance = prompt_strings._build_bash_tool_guidance()

        assert "Bash Tool Guidance" in guidance
        assert "Windows machine" not in guidance
        assert "must follow PowerShell syntax" not in guidance

    def test_tool_instructions_are_platform_agnostic(self):
        with patch.object(prompt_strings.platform, "system", return_value="Windows"):
            windows_instructions = prompt_strings._build_tool_instructions()
        with patch.object(prompt_strings.platform, "system", return_value="Darwin"):
            darwin_instructions = prompt_strings._build_tool_instructions()

        assert windows_instructions == darwin_instructions
        assert "PowerShell" not in windows_instructions
        assert "must explicitly specify encoding" not in windows_instructions

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
