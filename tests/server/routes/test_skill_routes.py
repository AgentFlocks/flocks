from pathlib import Path

import pytest
from httpx import AsyncClient

from flocks.skill.skill import Skill


@pytest.mark.asyncio
async def test_ordinary_request_keeps_bundled_skills_visible(
    client: AsyncClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bundled skills stay visible when an ordinary request starts elsewhere."""
    ordinary_cwd = tmp_path / "ordinary-cwd"
    ordinary_cwd.mkdir()
    monkeypatch.chdir(ordinary_cwd)
    Skill.clear_cache()

    response = await client.get("/api/skills")

    assert response.status_code == 200
    source_skills_root = Path(Skill._source_root()) / ".flocks"
    bundled_skills = [
        skill
        for skill in response.json()
        if Path(skill["location"]).is_relative_to(source_skills_root)
    ]
    assert bundled_skills
    assert any(skill["source"] == "project" for skill in bundled_skills)
