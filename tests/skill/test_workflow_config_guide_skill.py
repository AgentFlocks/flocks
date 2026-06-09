from pathlib import Path

import pytest

from flocks.skill.skill import Skill


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_parse_workflow_config_guide_project_skill() -> None:
    skill_file = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-config-guide"
        / "SKILL.md"
    )

    parsed = Skill._parse_skill_md(str(skill_file))

    assert parsed is not None
    assert parsed.name == "workflow-config-guide"
    assert parsed.category == "system"
    assert parsed.ui_hidden is True
    assert "配置现有 Flocks 工作流" in parsed.description


def test_workflow_config_guide_requires_free_text_question_input() -> None:
    skill_file = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-config-guide"
        / "SKILL.md"
    )

    content = skill_file.read_text(encoding="utf-8")

    assert "never make a configuration question choice-only" in content
    assert 'type: "text"' in content
    assert "Custom value or notes" in content


@pytest.mark.asyncio
async def test_discover_workflow_config_guide_project_skill() -> None:
    skills = await Skill.refresh()
    skill_names = {skill.name for skill in skills}

    assert "workflow-config-guide" in skill_names


def test_workflow_template_no_longer_ships_integration_guide() -> None:
    old_guide = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "workflows"
        / "workflow_template"
        / "integration_guide.md"
    )

    assert not old_guide.exists()
