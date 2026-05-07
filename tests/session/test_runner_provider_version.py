"""
Tests for ``flocks.session.runner._annotate_with_provider_version``.

Ensures that when a tool's ``ToolInfo`` carries a ``provider_version`` (sourced
from ``_provider.yaml``), the description handed to the LLM in the function
schema is augmented with a ``[Provider: ... | Version: ...]`` annotation.

Without this annotation the model has no way to distinguish e.g. Sangfor SIP
v9.2 vs v9.3 when picking parameter values, since the same Python tool name
can be backed by different upstream API versions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from flocks.session.runner import _annotate_with_provider_version


@dataclass
class _FakeToolInfo:
    """Minimal stand-in for ``ToolInfo`` exposing only the attrs we need."""

    description: str = ""
    provider: Optional[str] = None
    provider_version: Optional[str] = None


class TestAnnotateWithProviderVersion:
    def test_appends_version_when_present(self):
        info = _FakeToolInfo(
            description="Query asset data.",
            provider="sangfor_sip",
            provider_version="9.2",
        )
        result = _annotate_with_provider_version(info, info.description)

        assert result.startswith("Query asset data.")
        assert result.endswith("[Provider: sangfor_sip | Version: 9.2]")
        # Annotation is separated from the body by a blank line so the LLM
        # treats it as a distinct hint rather than part of the last sentence.
        assert "\n\n[Provider:" in result

    def test_returns_description_unchanged_when_no_version(self):
        info = _FakeToolInfo(description="Plain tool.", provider="foo")
        assert _annotate_with_provider_version(info, info.description) == "Plain tool."

    def test_handles_none_description(self):
        info = _FakeToolInfo(
            description="",
            provider="sangfor_sip",
            provider_version="9.2",
        )
        # Pass None explicitly to mirror what runner does when ToolInfo had
        # no description.
        result = _annotate_with_provider_version(info, None)
        # Without a body we should NOT emit a leading blank line — the model
        # would otherwise see a stray newline before the hint.
        assert result == "[Provider: sangfor_sip | Version: 9.2]"

    def test_falls_back_to_service_label_when_provider_missing(self):
        info = _FakeToolInfo(
            description="Generic tool.",
            provider=None,
            provider_version="1.0",
        )
        result = _annotate_with_provider_version(info, info.description)
        assert "[Provider: service | Version: 1.0]" in result

    def test_does_not_mutate_original_description(self):
        info = _FakeToolInfo(
            description="Original.",
            provider="x",
            provider_version="2",
        )
        original = info.description
        _ = _annotate_with_provider_version(info, info.description)
        # ToolInfo objects are reused across turns; the helper must not mutate
        # them in place.
        assert info.description == original

    def test_strips_trailing_whitespace_before_annotation(self):
        info = _FakeToolInfo(
            description="Body with trailing spaces.   \n\n",
            provider="x",
            provider_version="3",
        )
        result = _annotate_with_provider_version(info, info.description)
        # Exactly one blank line (\n\n) between body and annotation.
        assert "Body with trailing spaces.\n\n[Provider: x | Version: 3]" == result

    def test_tool_info_without_provider_version_attr(self):
        """Builtin tools / MCP tools may not declare provider_version at all."""

        class _Bare:
            description = "I have nothing extra."

        result = _annotate_with_provider_version(_Bare(), _Bare.description)
        assert result == "I have nothing extra."
