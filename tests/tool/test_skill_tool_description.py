"""Tests for flocks.tool.system.skill.

Two complementary aspects of the skill tool's progressive-disclosure design
are exercised here:

1. ``build_description`` — the meta-description that ships in every system
   prompt. Each skill's description is capped at
   ``MAX_SKILL_DESCRIPTION_PREVIEW_CHARS`` using head + tail truncation, so
   both the opening (scope/triggers) and the closing (hard constraints, "must
   load this skill before X") survive. The model is explicitly told to call
   ``skill(name=...)`` to load the full SKILL.md before acting.

2. ``skill_tool`` (load on demand) — when the model actually calls the tool,
   the FULL SKILL.md must come back unredacted. This is the load-on-demand
   counterpart of the truncated preview, mirroring hermes-agent's
   ``skill_view``. Without the explicit opt-out the registry would silently
   crop SKILL.md at 10 KB / 200 lines (head-only), dropping the workflow
   steps and references that authors put at the file's tail.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from flocks.skill.skill import Skill, SkillInfo
from flocks.tool.registry import ToolContext, ToolRegistry
from flocks.tool.system.skill import (
    MAX_SKILL_DESCRIPTION_PREVIEW_CHARS,
    _truncate_skill_description,
    build_description,
    skill_tool_impl,
)


def _skill(name: str, description: str) -> SkillInfo:
    return SkillInfo(name=name, description=description, location=f"/tmp/{name}/SKILL.md")


def _make_ctx() -> ToolContext:
    ctx = MagicMock(spec=ToolContext)
    ctx.ask = AsyncMock(return_value=None)
    ctx.metadata = MagicMock()
    ctx.aborted = False
    ctx.extra = {}
    ctx.agent = "rex"
    ctx.session_id = "ses_test_skill"
    return ctx


# ---------------------------------------------------------------------------
# _truncate_skill_description
# ---------------------------------------------------------------------------


class TestTruncateSkillDescription:
    def test_short_description_is_unchanged(self):
        desc = "Short skill description in two sentences. Use it for X."
        assert _truncate_skill_description(desc, "x-skill") == desc

    def test_description_at_cap_is_unchanged(self):
        desc = "a" * MAX_SKILL_DESCRIPTION_PREVIEW_CHARS
        assert _truncate_skill_description(desc, "x-skill") == desc

    def test_long_description_is_truncated_under_cap(self):
        desc = "a" * 1000
        out = _truncate_skill_description(desc, "x-skill")
        assert len(out) <= MAX_SKILL_DESCRIPTION_PREVIEW_CHARS

    def test_truncation_keeps_head_and_tail(self):
        head = "HEAD_MARKER " + ("a" * 600)
        tail = ("z" * 200) + " TAIL_MARKER"
        desc = head + tail
        out = _truncate_skill_description(desc, "demo")
        assert out.startswith("HEAD_MARKER")
        assert out.endswith("TAIL_MARKER")

    def test_truncation_inserts_skill_load_hint(self):
        desc = "a" * 2000
        out = _truncate_skill_description(desc, "onesec-use")
        # The truncation marker must point the model at the right tool call,
        # otherwise progressive disclosure breaks.
        assert 'skill(name="onesec-use")' in out
        assert "truncated" in out

    def test_chinese_threat_intel_skill_keeps_trailing_constraint(self):
        # Real-world shape: scope ... ability list ... HARD CONSTRAINT at end.
        # The trailing "必须先加载本 skill" is the most decision-critical part
        # for these skills.
        head = "用于处理 OneSEC 终端安全平台相关任务，" + ("，能力列表" * 200)
        tail = "本 skill 是 OneSEC 平台操作的唯一决策入口：必须先加载本 skill。"
        desc = head + tail
        out = _truncate_skill_description(desc, "onesec-use")
        assert len(out) <= MAX_SKILL_DESCRIPTION_PREVIEW_CHARS
        assert out.startswith("用于处理 OneSEC 终端安全平台相关任务")
        assert out.endswith("必须先加载本 skill。")


# ---------------------------------------------------------------------------
# build_description
# ---------------------------------------------------------------------------


class TestBuildDescription:
    def test_empty_skills_returns_no_skills_message(self):
        out = build_description([])
        assert "No skills are currently available" in out
        assert "<available_skills>" not in out

    def test_each_skill_emits_xml_block(self):
        out = build_description(
            [
                _skill("alpha", "First skill description."),
                _skill("beta", "Second skill description."),
            ]
        )
        assert "<available_skills>" in out
        assert "</available_skills>" in out
        assert "<name>alpha</name>" in out
        assert "<name>beta</name>" in out
        assert "<description>First skill description.</description>" in out
        assert "<description>Second skill description.</description>" in out

    def test_includes_progressive_disclosure_instruction(self):
        out = build_description([_skill("alpha", "demo")])
        # Must direct the model at the load-on-demand tool call pattern,
        # otherwise the model will try to act on the (possibly truncated)
        # preview without first reading the full SKILL.md.
        assert "skill(name=" in out
        assert "preview" in out.lower() or "truncated" in out.lower()
        assert "MUST" in out or "must" in out.lower()

    def test_long_description_is_truncated_in_output(self):
        long_desc = "a" * 2000
        out = build_description([_skill("big", long_desc)])
        # The full 2000-char string should never appear verbatim because it
        # exceeds the per-skill preview cap.
        assert long_desc not in out
        assert 'skill(name="big")' in out

    def test_short_descriptions_are_emitted_verbatim(self):
        desc = "Short, single-sentence description that fits."
        out = build_description([_skill("tiny", desc)])
        # No truncation marker should appear for descriptions under the cap.
        assert desc in out
        assert "truncated" not in out.split("<available_skills>")[1]

    def test_api_surface_returns_string(self):
        out = build_description([_skill("alpha", "desc")])
        assert isinstance(out, str)
        assert len(out) > 0


# ---------------------------------------------------------------------------
# skill_tool_impl: load-on-demand must NOT truncate the SKILL.md content
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_skill_dir():
    """Create a temp directory with a SKILL.md that exceeds default truncation
    limits (>10 KB AND >200 lines) so we can prove the registry's auto-truncate
    pass leaves it alone.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "huge-skill"
        skill_dir.mkdir()

        body_lines = [
            f"## Step {i}: do something important and clearly identifiable" for i in range(1, 401)
        ]
        # Tail sentinel proves we kept the END of the file (where authors
        # typically put the operational steps and references).
        body_lines.append("# TAIL_SENTINEL: this line MUST survive end-to-end")
        body = "\n".join(body_lines)
        # Pad with extra bytes so total comfortably exceeds the 10 KB cap.
        padding = "x" * (15 * 1024)

        (skill_dir / "SKILL.md").write_text(
            f"""---
name: huge-skill
description: Test skill that exceeds default truncation limits.
---

# Huge Skill

{padding}

{body}
""",
            encoding="utf-8",
        )
        yield skill_dir


class TestSkillLoadNoTruncation:
    @pytest.mark.asyncio
    async def test_skill_tool_returns_full_content(self, fake_skill_dir):
        """skill_tool_impl must return the entire SKILL.md content, including
        the tail, regardless of file size."""
        skill_md = fake_skill_dir / "SKILL.md"
        original = skill_md.read_text(encoding="utf-8")
        # Sanity-check the fixture: it MUST exceed the registry defaults,
        # otherwise the test isn't actually exercising the bypass.
        assert len(original.encode("utf-8")) > 10 * 1024
        assert original.count("\n") > 200

        skill_info = SkillInfo(
            name="huge-skill",
            description="Test skill",
            location=str(skill_md),
        )

        with patch.object(Skill, "get", AsyncMock(return_value=skill_info)):
            result = await skill_tool_impl(_make_ctx(), name="huge-skill")

        assert result.success is True
        assert "TAIL_SENTINEL" in result.output, (
            "tail of SKILL.md was dropped — load-on-demand must deliver "
            "the full file, not the head-truncated version"
        )
        # The content should appear verbatim within the formatted output.
        assert original.strip() in result.output

    @pytest.mark.asyncio
    async def test_skill_tool_sets_truncated_flag_to_bypass_registry(self, fake_skill_dir):
        """skill_tool_impl must set ``truncated=True`` so ToolRegistry's
        auto-truncate pass (registry.py: 'unless the tool already handled it')
        leaves our payload alone."""
        skill_info = SkillInfo(
            name="huge-skill",
            description="Test skill",
            location=str(fake_skill_dir / "SKILL.md"),
        )

        with patch.object(Skill, "get", AsyncMock(return_value=skill_info)):
            result = await skill_tool_impl(_make_ctx(), name="huge-skill")

        assert result.truncated is True
        assert result.metadata.get("auto_truncate_bypassed") is True

    @pytest.mark.asyncio
    async def test_registry_execute_does_not_truncate_skill_output(self, fake_skill_dir):
        """End-to-end: when the `skill` tool is executed via ToolRegistry
        (which is what the real session loop does), the auto-truncate path
        must NOT fire and the tail must survive."""
        skill_info = SkillInfo(
            name="huge-skill",
            description="Test skill",
            location=str(fake_skill_dir / "SKILL.md"),
        )

        skill_tool = ToolRegistry.get("skill")
        assert skill_tool is not None, "skill tool must be registered"

        with patch.object(Skill, "all", AsyncMock(return_value=[skill_info])), \
             patch.object(Skill, "get", AsyncMock(return_value=skill_info)):
            result = await skill_tool.execute(_make_ctx(), name="huge-skill")

        assert result.success is True
        assert result.truncated is True  # explicit bypass, not registry-imposed
        assert "TAIL_SENTINEL" in result.output
        # The registry's truncation hint must NOT appear — that would mean
        # the bypass failed.
        assert "Use Grep to search the full content" not in result.output
        assert "lines truncated" not in result.output
        assert "bytes truncated" not in result.output
