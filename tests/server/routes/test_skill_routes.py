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


@pytest.fixture
def native_group_skills(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from unittest.mock import AsyncMock
    from flocks.server.routes import skill as routes

    home = tmp_path / "home"
    home.mkdir()
    source = tmp_path / "source"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Skill, "_source_root", lambda: source)
    prompt_refresh = AsyncMock()
    monkeypatch.setattr(routes, "_refresh_agents_for_skill_change", prompt_refresh)
    user_path = home / ".flocks" / "plugins" / "skills" / "user-skill" / "SKILL.md"
    builtin_path = source / ".flocks" / "plugins" / "skills" / "core-skill" / "SKILL.md"
    legacy_path = source / ".flocks" / "skills" / "legacy-core" / "SKILL.md"
    for path, name in ((user_path, "user-skill"), (builtin_path, "core-skill"), (legacy_path, "legacy-core")):
        path.parent.mkdir(parents=True)
        path.write_bytes((
            f"---\r\nname: {name}\r\ndescription: Analyze: alerts\r\ngroup: Original\r\n"
            "category: system\r\nx-vendor:\r\n  nested: [one, two]\r\n"
            "metadata:\r\n  flocks:\r\n    requires:\r\n      env: [TEST_SKILL_DEP]\r\n"
            "---\r\n\r\n# Skill body\r\n  whitespace stays  \r\n\r\n"
        ).encode("utf-8"))
    Skill.clear_cache()
    yield user_path, builtin_path, prompt_refresh
    Skill.clear_cache()


@pytest.mark.asyncio
async def test_native_group_skill_patch_preserves_body_metadata_and_prompts(client, native_group_skills):
    import yaml

    path, _, prompt_refresh = native_group_skills
    before = path.read_bytes()
    body = before.split(b"---\r\n", 2)[2]
    for value, expected in (("  Team A  ", "Team A"), (None, ""), ("", "")):
        response = await client.patch("/api/skills/user-skill", json={"group": value})
        assert response.status_code == 200, response.text
        assert response.json()["group"] == expected
        assert response.json()["group_readonly"] is False
        assert response.json()["description"] == "Analyze: alerts"
        after = path.read_bytes()
        assert after.split(b"---\r\n", 2)[2] == body
        metadata = yaml.safe_load(after.decode().split("---", 2)[1])
        assert metadata["x-vendor"] == {"nested": ["one", "two"]}
        assert metadata["metadata"]["flocks"]["requires"]["env"] == ["TEST_SKILL_DEP"]
        assert metadata["group"] == expected
        assert (await client.get("/api/skills/user-skill")).json()["group"] == expected
        for endpoint in ("/api/skills", "/api/skills/status"):
            listed = (await client.get(endpoint)).json()
            assert next(item for item in listed if item["name"] == "user-skill")["group"] == expected
    unchanged = path.read_bytes()
    assert (await client.patch("/api/skills/user-skill", json={})).status_code == 200
    assert path.read_bytes() == unchanged
    prompt_refresh.assert_not_awaited()
    assert not Skill.settings_path().exists()


@pytest.mark.asyncio
async def test_native_group_skill_ordinary_edit_rename_keeps_unknown_frontmatter(client, native_group_skills):
    path, _, _ = native_group_skills
    payload = {"name": "user-skill", "description": "Edited: description", "content": "# New body\n"}
    response = await client.put("/api/skills/user-skill", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["group"] == "Original"
    renamed = await client.put("/api/skills/user-skill", json={**payload, "name": "renamed-skill"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["group"] == "Original"
    renamed_path = Path(renamed.json()["location"])
    assert not path.exists()
    data = Skill._parse_frontmatter(renamed_path.read_text())
    assert data["group"] == "Original"
    assert data["x-vendor"] == {"nested": ["one", "two"]}
    assert data["category"] == "system"
    assert (await client.get("/api/skills/renamed-skill")).json()["group"] == "Original"
    cleared = await client.put("/api/skills/renamed-skill", json={**payload, "name": "renamed-skill", "group": None})
    assert cleared.json()["group"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [
    ("patch", {"group": "Team"}),
    ("patch", {"group": None}),
    ("patch", {"group": ""}),
    ("put", {"name": "core-skill", "description": "Changed", "content": "# Changed"}),
    ("put", {"name": "renamed-core", "description": "Changed", "content": "# Changed", "group": "Team"}),
    ("put", {"name": "core-skill", "description": "Changed", "content": "# Changed", "group": None}),
])
async def test_native_group_builtin_skill_immutable_even_for_admin(client, native_group_skills, method, payload):
    user_path, builtin_path, prompt_refresh = native_group_skills
    before = builtin_path.read_bytes()
    response = await getattr(client, method)("/api/skills/core-skill", json=payload)
    assert response.status_code == 403, response.text
    assert "read-only" in response.text
    assert builtin_path.read_bytes() == before
    assert not (user_path.parent.parent / "core-skill").exists()
    assert not (user_path.parent.parent / "renamed-core").exists()
    prompt_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method,payload", [
    ("patch", {"group": "Team"}),
    ("patch", {"group": None}),
    ("patch", {"group": ""}),
    ("put", {"name": "legacy-core", "description": "Changed", "content": "Changed"}),
    ("put", {"name": "legacy-core", "description": "Changed", "content": "Changed", "group": "Team"}),
    ("put", {"name": "renamed-legacy", "description": "Changed", "content": "Changed", "group": None}),
])
async def test_native_group_legacy_flocks_builtin_is_readonly(client, native_group_skills, method, payload):
    user_path, _, prompt_refresh = native_group_skills
    skill = await Skill.get("legacy-core")
    assert skill.source == "flocks"
    assert skill.native is False  # The discovery flag does not override built-in ownership.
    path = Path(skill.location)
    before = path.read_bytes()
    response = await getattr(client, method)("/api/skills/legacy-core", json=payload)
    assert response.status_code == 403, response.text
    assert "read-only" in response.text
    assert path.read_bytes() == before
    assert not (user_path.parent.parent / "legacy-core").exists()
    assert not (user_path.parent.parent / "renamed-legacy").exists()
    prompt_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["core-skill", "legacy-core"])
async def test_native_group_builtin_skill_cannot_be_deleted(client, native_group_skills, name):
    skill = await Skill.get(name)
    path = Path(skill.location)
    before = path.read_bytes()
    response = await client.delete(f"/api/skills/{name}")
    assert response.status_code == 403
    assert path.read_bytes() == before


@pytest.mark.asyncio
async def test_native_group_builtin_skill_immutable_for_regular_user(native_group_skills):
    from fastapi import HTTPException
    from flocks.server.routes import skill as routes

    with pytest.raises(HTTPException) as patch_error:
        await routes.update_skill_metadata("core-skill", routes.SkillMetadataUpdateRequest(group="Team"), _user={"role": "user"})
    assert patch_error.value.status_code == 403
    with pytest.raises(HTTPException) as put_error:
        await routes.update_skill("core-skill", routes.SkillCreateRequest(name="core-skill", description="Changed", content="Body"), _user={"role": "user"})
    assert put_error.value.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, False, [], {}, "x" * 33, "a\x00b", "a\nb", "a\x7fb"])
async def test_native_group_skill_invalid_values_do_not_write(client, native_group_skills, value):
    path, _, _ = native_group_skills
    before = path.read_bytes()
    response = await client.patch("/api/skills/user-skill", json={"group": value})
    assert response.status_code == 422
    assert path.read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["core-skill", "legacy-core"])
@pytest.mark.parametrize("method", ["post", "put"])
async def test_native_group_skill_cannot_shadow_builtin_name(client, native_group_skills, target, method):
    user_path, _, prompt_refresh = native_group_skills
    builtin = await Skill.get(target)
    builtin_path = Path(builtin.location)
    builtin_before, user_before = builtin_path.read_bytes(), user_path.read_bytes()
    endpoint = "/api/skills" if method == "post" else "/api/skills/user-skill"
    response = await getattr(client, method)(endpoint, json={
        "name": target, "description": "Replacement", "content": "Replacement body", "group": "Replacement",
    })
    assert response.status_code == 403, response.text
    assert builtin_path.read_bytes() == builtin_before
    assert user_path.read_bytes() == user_before
    assert not (user_path.parent.parent / target).exists()
    assert (await Skill.get(target)).location == str(builtin_path)
    prompt_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["post", "put"])
async def test_native_group_skill_name_collision_preserves_custom_definition(client, native_group_skills, method):
    path, _, prompt_refresh = native_group_skills
    other = path.parent.parent / "other-skill" / "SKILL.md"
    other.parent.mkdir()
    original = "---\nname: other-skill\ndescription: Other skill\ngroup: Other\n---\nOther body\n"
    other.write_text(original, encoding="utf-8")
    Skill.clear_cache()
    before = path.read_bytes()
    endpoint = "/api/skills" if method == "post" else "/api/skills/user-skill"
    response = await getattr(client, method)(endpoint, json={
        "name": "other-skill", "description": "Replacement", "content": "Replacement body",
    })
    assert response.status_code == 409, response.text
    assert other.read_text(encoding="utf-8") == original
    assert path.read_bytes() == before
    prompt_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../core-skill", "absolute-path", "", "user-skill/../../core-skill"])
async def test_native_group_skill_rejects_noncanonical_write_names(client, native_group_skills, name):
    path, _, prompt_refresh = native_group_skills
    before = path.read_bytes()
    if name == "absolute-path":
        name = str(path.parents[4] / "outside-skill")
    response = await client.put("/api/skills/user-skill", json={
        "name": name, "description": "Changed", "content": "Changed",
    })
    assert response.status_code == 422, response.text
    assert path.read_bytes() == before
    prompt_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_group_skill_unknown_lookup_and_full_put_requirements(client, native_group_skills):
    assert (await client.patch("/api/skills/unknown", json={"group": "Team"})).status_code == 404
    assert (await client.put("/api/skills/user-skill", json={"group": "Team"})).status_code == 422


async def test_skill_group_readonly_is_computed_and_keeps_project_definition_rule(
    client, native_group_skills, tmp_path,
):
    custom_project = tmp_path / ".flocks/plugins/skills/custom-project/SKILL.md"
    custom_project.parent.mkdir(parents=True)
    original = "---\nname: custom-project\ndescription: Project\ngroup: Project group\ngroup_readonly: false\n---\nBody\n"
    custom_project.write_text(original)
    Skill.clear_cache()
    assert not Skill.is_system_shipped(custom_project)
    for endpoint in ("/api/skills", "/api/skills/status"):
        rows = {row["name"]: row for row in (await client.get(endpoint)).json()}
        assert rows["user-skill"]["group_readonly"] is False
        for name in ("core-skill", "legacy-core", "custom-project"):
            assert rows[name]["group_readonly"] is True
            detail = await client.get(f"/api/skills/{name}")
            assert detail.json()["group_readonly"] is True
    blocked = await client.patch("/api/skills/custom-project", json={"group": "Changed"})
    assert blocked.status_code == 403
    assert custom_project.read_text() == original


async def test_install_api_cannot_shadow_shipped_skill(client, native_group_skills, tmp_path):
    user_path, builtin_path, _ = native_group_skills
    original = builtin_path.read_bytes()
    incoming = tmp_path / "replacement.md"
    incoming.write_text("---\nname: core-skill\ndescription: Replacement\ngroup: Changed\n---\nReplacement\n")
    result = await client.post("/api/skills/install", json={"source": str(incoming)})
    assert result.status_code == 422 and "read-only" in result.text
    assert builtin_path.read_bytes() == original
    assert not (user_path.parent.parent / "core-skill").exists()
