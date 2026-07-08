import json
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


def test_workflow_config_guide_requires_workflow_config_manage() -> None:
    skill_file = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-config-guide"
        / "SKILL.md"
    )

    content = skill_file.read_text(encoding="utf-8")

    assert 'workflow_config_manage(action="get"' in content
    assert 'workflow_config_manage(action="diff"' in content
    assert 'workflow_config_manage(action="put"' in content
    assert 'config_type="poller"' in content
    assert 'config_type="syslog"' in content
    assert 'config_type="kafka"' in content
    assert "/api/workflow/<workflow_id>/config" not in content
    assert "GET /api/workflow" not in content
    assert "PUT /api/workflow" not in content
    assert "use the runtime endpoint after template confirmation" not in content


def test_workflow_builder_references_template_inside_skill() -> None:
    skill_file = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-builder"
        / "SKILL.md"
    )

    content = skill_file.read_text(encoding="utf-8")

    assert "references/workflow_template/" in content
    assert ".flocks/plugins/workflows/workflow_template" not in content


def test_workflow_builder_template_requires_workflow_config_manage() -> None:
    guide_file = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-builder"
        / "references"
        / "workflow_template"
        / "guide.md"
    )

    content = guide_file.read_text(encoding="utf-8")

    assert 'workflow_config_manage(action="get"' in content
    assert 'workflow_config_manage(action="diff"' in content
    assert 'workflow_config_manage(action="put"' in content
    assert 'config_type="poller"' in content
    assert 'config_type="syslog"' in content
    assert 'config_type="kafka"' in content
    assert "/api/workflow/<id>/config" not in content
    assert "config/sync" not in content


@pytest.mark.asyncio
async def test_discover_workflow_config_guide_project_skill() -> None:
    skills = await Skill.refresh()
    skill_names = {skill.name for skill in skills}

    assert "workflow-config-guide" in skill_names


def test_stream_alert_guides_require_workflow_config_manage() -> None:
    for workflow_id in ("stream_alert_denoise", "stream_alert_triage"):
        guide_file = (
            PROJECT_ROOT
            / ".flocks"
            / "flockshub"
            / "plugins"
            / "workflows"
            / workflow_id
            / "guide.md"
        )

        content = guide_file.read_text(encoding="utf-8")

        assert f'workflow_config_manage(action="get", workflow_id="{workflow_id}")' in content
        assert f'workflow_config_manage(action="diff", workflow_id="{workflow_id}"' in content
        assert f'workflow_config_manage(action="put", workflow_id="{workflow_id}"' in content
        assert f"/api/workflow/{workflow_id}/config" not in content
        assert "config/sync" not in content
        if workflow_id == "stream_alert_denoise":
            assert 'config_type="syslog"' in content
            assert f"/api/workflow/{workflow_id}/syslog-config" in content
        if workflow_id == "stream_alert_triage":
            assert 'config_type="poller"' in content
            assert f"/api/workflow/{workflow_id}/poller-config" in content


def test_workflow_template_no_longer_ships_integration_guide() -> None:
    old_workflow_template = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "workflows"
        / "workflow_template"
    )
    workflow_builder_template = (
        PROJECT_ROOT
        / ".flocks"
        / "plugins"
        / "skills"
        / "workflow-builder"
        / "references"
        / "workflow_template"
    )

    assert not old_workflow_template.exists()
    assert (workflow_builder_template / "workflow.md").exists()
    assert (workflow_builder_template / "workflow.json").exists()
    assert (workflow_builder_template / "config.json").exists()
    assert (workflow_builder_template / "guide.md").exists()

    config = json.loads((workflow_builder_template / "config.json").read_text(encoding="utf-8"))
    assert config["kind"] == "workflow.integration-config"
    assert isinstance(config["publish"], dict)
    assert isinstance(config["triggers"], list)
    assert "publishTemplates" not in config
