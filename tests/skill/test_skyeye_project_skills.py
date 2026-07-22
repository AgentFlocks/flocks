from pathlib import Path

import pytest

from flocks.skill.skill import Skill


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_parse_skyeye_project_skill_files() -> None:
    skill_files = [
        PROJECT_ROOT / ".flocks" / "plugins" / "skills" / "skyeye-use" / "SKILL.md",
        PROJECT_ROOT / ".flocks" / "plugins" / "skills" / "skyeye-sensor-data-fetch" / "SKILL.md",
    ]

    parsed = [Skill._parse_skill_md(str(skill_file)) for skill_file in skill_files]

    assert parsed[0] is not None
    assert parsed[0].name == "skyeye-use"
    assert "SkyEye" in parsed[0].description

    assert parsed[1] is not None
    assert parsed[1].name == "skyeye-sensor-data-fetch"
    assert "Sensor" in parsed[1].description


@pytest.mark.asyncio
async def test_discover_skyeye_project_skills() -> None:
    skills = await Skill.refresh()
    skill_names = {skill.name for skill in skills}

    assert "skyeye-use" in skill_names
    assert "skyeye-sensor-data-fetch" in skill_names
