from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from flocks.cli.commands.skill import skill_app
from flocks.skill.installer import DepInstallResult
from flocks.skill.skill import SkillInfo


runner = CliRunner()


def test_status_lists_skills_without_requirements():
    skill = SkillInfo(
        name="cli-local-skill",
        description="CLI local install test",
        location="/tmp/cli-local-skill/SKILL.md",
    )

    with patch("flocks.cli.commands.skill.Skill.all", AsyncMock(return_value=[skill])):
        result = runner.invoke(skill_app, ["status"])

    assert result.exit_code == 0
    assert "cli-local-skill" in result.output
    assert "no requirements" in result.output


def test_install_deps_prints_result_message_when_no_command():
    result = DepInstallResult(
        success=True,
        message="Skill 'demo' has no install specs.",
    )

    with patch(
        "flocks.cli.commands.skill.SkillInstaller.install_deps",
        AsyncMock(return_value=[result]),
    ):
        cli_result = runner.invoke(skill_app, ["install-deps", "demo"])

    assert cli_result.exit_code == 0
    assert "has no install specs" in cli_result.output
