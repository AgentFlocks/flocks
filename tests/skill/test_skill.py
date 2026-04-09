"""
Tests for Skill system

Validates skill discovery, parsing, and loading.
"""

import pytest
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from flocks.skill.skill import Skill, SkillInfo, SkillRequires, SkillInstallSpec


@pytest.fixture
def temp_skill_dir():
    """Create temporary skill directory"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "test-skill"
        skill_dir.mkdir()
        
        # Create SKILL.md with required frontmatter
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: test-skill
description: This is a test skill for validation.
---

# Test Skill

Follow these steps to test the skill.
""")
        
        yield tmpdir


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear skill cache before each test"""
    Skill.clear_cache()
    yield
    Skill.clear_cache()


def test_parse_skill_md(temp_skill_dir):
    """Test parsing SKILL.md file"""
    skill_file = os.path.join(temp_skill_dir, "test-skill", "SKILL.md")
    
    skill_info = Skill._parse_skill_md(skill_file)
    
    assert skill_info is not None
    assert skill_info.name == "test-skill"
    assert "test skill" in skill_info.description.lower()
    assert skill_info.location == skill_file


def test_parse_skill_md_without_frontmatter():
    """Test that SKILL.md without frontmatter returns None"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "no-frontmatter"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# No Frontmatter\n\nJust content.\n")
        result = Skill._parse_skill_md(str(skill_file))
        assert result is None


def test_parse_skill_with_frontmatter():
    """Test parsing SKILL.md with YAML frontmatter"""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "frontmatter-skill"
        skill_dir.mkdir()
        
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: frontmatter-skill
description: "Skill with frontmatter"
---

# Frontmatter Skill

Additional content here.
""")
        
        skill_info = Skill._parse_skill_md(str(skill_file))
        
        assert skill_info is not None
        assert skill_info.name == "frontmatter-skill"
        assert skill_info.description == "Skill with frontmatter"


def test_parse_skill_with_localized_description():
    """description_cn in frontmatter should be parsed and exposed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        skill_dir = Path(tmpdir) / "localized-skill"
        skill_dir.mkdir()

        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("""---
name: localized-skill
description: "English description"
description_cn: "中文描述"
---

# Localized Skill
""")

        skill_info = Skill._parse_skill_md(str(skill_file))

        assert skill_info is not None
        assert skill_info.description == "English description"
        assert skill_info.description_cn == "中文描述"


@pytest.mark.asyncio
async def test_discover_skills():
    """Test skill discovery"""
    # This will discover skills from default locations
    skills = await Skill.all()
    
    # Should be a list (might be empty if no skills installed)
    assert isinstance(skills, list)
    
    # If example skill exists, verify it
    if skills:
        skill_names = [s.name for s in skills]
        assert all(isinstance(name, str) for name in skill_names)


@pytest.mark.asyncio
async def test_get_skill_by_name():
    """Test getting specific skill"""
    # First, get all skills
    all_skills = await Skill.all()
    
    if all_skills:
        # Get first skill by name
        first_skill = all_skills[0]
        retrieved = await Skill.get(first_skill.name)
        
        assert retrieved is not None
        assert retrieved.name == first_skill.name
        assert retrieved.description == first_skill.description
        assert retrieved.location == first_skill.location


@pytest.mark.asyncio
async def test_get_nonexistent_skill():
    """Test getting non-existent skill"""
    skill = await Skill.get("nonexistent-skill-12345")
    assert skill is None


@pytest.mark.asyncio
async def test_refresh_skills():
    """Skill.refresh() clears the cache and re-discovers skills."""
    # Populate cache with a sentinel entry
    Skill._cache = {"sentinel": SkillInfo(name="sentinel", description="x", location="/x")}

    # refresh() must clear the cache and run discovery
    with patch.object(Skill, "_discover", return_value={}) as mock_discover:
        result = await Skill.refresh()
        mock_discover.assert_called_once()

    # Cache should reflect the fresh discovery result (empty dict here)
    assert result == []
    assert Skill._cache == {}


def test_clear_cache():
    """Test cache clearing"""
    # Set cache
    Skill._cache = {"test": SkillInfo(
        name="test",
        description="Test",
        location="/tmp/test"
    )}
    
    # Clear
    Skill.clear_cache()
    
    # Should be None
    assert Skill._cache is None


@pytest.mark.asyncio
async def test_skill_info_model():
    """Test SkillInfo model"""
    info = SkillInfo(
        name="test-skill",
        description="A test skill",
        location="/path/to/skill.md"
    )
    
    assert info.name == "test-skill"
    assert info.description == "A test skill"
    assert info.location == "/path/to/skill.md"
    assert info.description_cn is None
    
    # Test serialization
    data = info.model_dump()
    assert data["name"] == "test-skill"
    
    # Test deserialization
    info2 = SkillInfo(**data)
    assert info2.name == info.name


# =============================================================================
# check_eligibility — any_bins and env branches
# =============================================================================

def _make_skill(requires: SkillRequires | None = None) -> SkillInfo:
    return SkillInfo(
        name="test-skill",
        description="A test skill",
        location="/tmp/test/SKILL.md",
        requires=requires,
    )


def test_eligibility_any_bins_one_present():
    """any_bins: at least one binary is found → eligible."""
    # python3 is guaranteed to exist; __ghost__ is not
    skill = _make_skill(SkillRequires(any_bins=["__ghost__", "python3"]))
    result = Skill.check_eligibility(skill)
    assert result.eligible is True
    assert not result.missing


def test_eligibility_any_bins_all_missing():
    """any_bins: none of the listed binaries exist → not eligible."""
    skill = _make_skill(SkillRequires(any_bins=["__ghost1__", "__ghost2__"]))
    result = Skill.check_eligibility(skill)
    assert result.eligible is False
    assert any("any_bin:" in m for m in (result.missing or []))


def test_eligibility_env_present():
    """requires.env: env var set → eligible."""
    os.environ["_FLOCKS_TEST_VAR"] = "1"
    try:
        skill = _make_skill(SkillRequires(env=["_FLOCKS_TEST_VAR"]))
        result = Skill.check_eligibility(skill)
        assert result.eligible is True
    finally:
        del os.environ["_FLOCKS_TEST_VAR"]


def test_eligibility_env_missing():
    """requires.env: env var absent → not eligible."""
    skill = _make_skill(SkillRequires(env=["__MISSING_ENV_1234__"]))
    result = Skill.check_eligibility(skill)
    assert result.eligible is False
    assert any("env:__MISSING_ENV_1234__" in m for m in (result.missing or []))


# =============================================================================
# _parse_skill_md — metadata block extraction
# =============================================================================

def test_parse_skill_md_with_metadata(tmp_path):
    """SKILL.md with nested metadata.flocks → install_specs and requires populated."""
    skill_dir = tmp_path / "meta-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: meta-skill\n"
        "description: Skill with metadata\n"
        "metadata:\n"
        "  flocks:\n"
        "    requires:\n"
        "      bins: [gh]\n"
        "    install:\n"
        "      - kind: brew\n"
        "        formula: gh\n"
        "---\n"
        "# Meta Skill\n"
    )

    skill_info = Skill._parse_skill_md(str(skill_file))

    assert skill_info is not None
    assert skill_info.name == "meta-skill"
    assert skill_info.requires is not None
    assert skill_info.requires.bins == ["gh"]
    assert skill_info.install_specs is not None
    assert len(skill_info.install_specs) == 1
    assert skill_info.install_specs[0].kind == "brew"
    assert skill_info.install_specs[0].formula == "gh"


def test_parse_skill_md_openclaw_metadata(tmp_path):
    """SKILL.md with metadata.openclaw → same fields populated via openclaw key."""
    skill_dir = tmp_path / "openclaw-skill"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(
        "---\n"
        "name: openclaw-skill\n"
        "description: OpenClaw compatible skill\n"
        "metadata:\n"
        "  openclaw:\n"
        "    requires:\n"
        "      bins: [node]\n"
        "---\n"
    )

    skill_info = Skill._parse_skill_md(str(skill_file))

    assert skill_info is not None
    assert skill_info.requires is not None
    assert skill_info.requires.bins == ["node"]


# =============================================================================
# _discover — source label assignment
# =============================================================================

def _write_skill_md(path: Path, name: str, description: str = "A test skill") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# Body\n"
    )


@pytest.mark.asyncio
async def test_discover_user_source_for_global_plugins(tmp_path):
    """Skills under ~/.flocks/plugins/skills/ must get source='user'."""
    home = tmp_path
    global_plugins = home / ".flocks" / "plugins" / "skills" / "my-user-skill"
    _write_skill_md(global_plugins / "SKILL.md", "my-user-skill")

    with (
        patch("os.path.expanduser", return_value=str(home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(tmp_path)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(tmp_path)),
    ):
        Skill.clear_cache()
        skills = await Skill.all()

    skill = next((s for s in skills if s.name == "my-user-skill"), None)
    assert skill is not None, "my-user-skill not discovered"
    assert skill.source == "user", f"expected 'user', got {skill.source!r}"


@pytest.mark.asyncio
async def test_discover_project_source_for_project_plugins(tmp_path):
    """Skills under <project>/.flocks/plugins/skills/ must get source='project'."""
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    project_plugins = project_dir / ".flocks" / "plugins" / "skills" / "my-project-skill"
    _write_skill_md(project_plugins / "SKILL.md", "my-project-skill")

    # Use a separate home to avoid ~/.flocks interference
    fake_home = tmp_path / "home"
    fake_home.mkdir()

    with (
        patch("os.path.expanduser", return_value=str(fake_home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(project_dir)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(project_dir)),
    ):
        Skill.clear_cache()
        skills = await Skill.all()

    skill = next((s for s in skills if s.name == "my-project-skill"), None)
    assert skill is not None, "my-project-skill not discovered"
    assert skill.source == "project", f"expected 'project', got {skill.source!r}"


@pytest.mark.asyncio
async def test_discover_flocks_source_for_builtin_skills(tmp_path):
    """Skills under .flocks/skills/ (not plugins/) must get source='flocks'."""
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()
    builtin_skill = project_dir / ".flocks" / "skills" / "my-builtin-skill"
    _write_skill_md(builtin_skill / "SKILL.md", "my-builtin-skill")

    fake_home = tmp_path / "home"
    fake_home.mkdir()

    with (
        patch("os.path.expanduser", return_value=str(fake_home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(project_dir)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(project_dir)),
    ):
        Skill.clear_cache()
        skills = await Skill.all()

    skill = next((s for s in skills if s.name == "my-builtin-skill"), None)
    assert skill is not None, "my-builtin-skill not discovered"
    assert skill.source == "flocks", f"expected 'flocks', got {skill.source!r}"


@pytest.mark.asyncio
async def test_discover_project_overrides_global(tmp_path):
    """Project-level plugin skill must override global skill of the same name."""
    fake_home = tmp_path / "home"
    project_dir = tmp_path / "myproject"
    project_dir.mkdir()

    # Same name in both global and project plugins
    global_skill = fake_home / ".flocks" / "plugins" / "skills" / "shared-skill"
    _write_skill_md(global_skill / "SKILL.md", "shared-skill", "Global version")

    project_skill = project_dir / ".flocks" / "plugins" / "skills" / "shared-skill"
    _write_skill_md(project_skill / "SKILL.md", "shared-skill", "Project version")

    with (
        patch("os.path.expanduser", return_value=str(fake_home)),
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(project_dir)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(project_dir)),
    ):
        Skill.clear_cache()
        skills = await Skill.all()

    skill = next((s for s in skills if s.name == "shared-skill"), None)
    assert skill is not None
    assert skill.source == "project", "project-level skill should override global"
    assert "Project version" in skill.description


# =============================================================================
# SkillFileWatcher — basic lifecycle
# =============================================================================

def test_watcher_start_stop_no_crash(tmp_path):
    """SkillFileWatcher can start and stop without errors."""
    from flocks.skill.skill import SkillFileWatcher

    # Create a fake skill directory so the watcher has something to watch
    skill_dir = tmp_path / ".flocks"
    skill_dir.mkdir()

    with (
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(tmp_path)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(tmp_path)),
        patch("os.path.expanduser", return_value=str(tmp_path)),
    ):
        watcher = SkillFileWatcher(Skill)
        watcher.start()
        watcher.stop()

    # No exception → pass
    assert watcher._observer is None


def test_watcher_debounce_clears_cache():
    """SkillFileWatcher._do_clear() triggers cache invalidation synchronously."""
    from flocks.skill.skill import SkillFileWatcher

    Skill._cache = {"dummy": SkillInfo(name="dummy", description="x", location="/x")}

    watcher = SkillFileWatcher(Skill)
    # Call the debounce callback directly — no timing dependency.
    watcher._do_clear()

    assert Skill._cache is None, "_do_clear should have cleared the cache"


def test_start_stop_watcher_class_methods(tmp_path):
    """Skill.start_watcher() / stop_watcher() manage the singleton watcher."""
    from flocks.skill.skill import SkillFileWatcher

    # Ensure clean state
    Skill._watcher = None

    with (
        patch("flocks.skill.skill.Instance.get_directory", return_value=str(tmp_path)),
        patch("flocks.skill.skill.Instance.get_worktree", return_value=str(tmp_path)),
        patch("os.path.expanduser", return_value=str(tmp_path)),
    ):
        Skill.start_watcher()
        assert Skill._watcher is not None

        # Calling start_watcher again is a no-op (guard branch)
        first = Skill._watcher
        Skill.start_watcher()
        assert Skill._watcher is first

        Skill.stop_watcher()
        assert Skill._watcher is None

        # Calling stop_watcher when already None is a no-op
        Skill.stop_watcher()  # should not raise


# =============================================================================
# _parse_frontmatter — edge cases
# =============================================================================

def test_parse_frontmatter_unclosed_returns_empty():
    """Frontmatter with opening --- but no closing --- returns empty dict."""
    content = "---\nname: broken\ndescription: No closing\n"
    data = Skill._parse_frontmatter(content)
    assert data == {}


def test_parse_frontmatter_yaml_failure_fallback():
    """When yaml.safe_load fails, falls back to simple key:value parser."""
    # Force yaml.safe_load to raise an exception
    with patch("yaml.safe_load", side_effect=Exception("yaml error")):
        content = "---\nname: fallback-skill\ndescription: Fallback test\n---\n"
        data = Skill._parse_frontmatter(content)

    assert data.get("name") == "fallback-skill"
    assert data.get("description") == "Fallback test"


# =============================================================================
# _parse_skill_md — error branches
# =============================================================================

def test_parse_skill_md_invalid_name_returns_none(tmp_path):
    """SKILL.md with invalid name (uppercase / spaces) → _parse_skill_md returns None."""
    skill_dir = tmp_path / "bad-name"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("---\nname: Invalid Name!\ndescription: Bad name\n---\n")

    result = Skill._parse_skill_md(str(skill_file))
    assert result is None


def test_parse_skill_md_invalid_metadata_still_parses(tmp_path):
    """Malformed metadata.flocks block logs a warning but still returns SkillInfo."""
    skill_dir = tmp_path / "bad-meta"
    skill_dir.mkdir()
    skill_file = skill_dir / "SKILL.md"
    # 'requires' has an invalid value type to trigger SkillMetadata.model_validate failure
    skill_file.write_text(
        "---\n"
        "name: bad-meta\n"
        "description: Metadata parse will fail\n"
        "metadata:\n"
        "  flocks:\n"
        "    requires: not-a-dict\n"  # invalid — should be a mapping
        "---\n"
    )

    result = Skill._parse_skill_md(str(skill_file))
    # Should still return SkillInfo even if metadata parsing fails
    assert result is not None
    assert result.name == "bad-meta"
    assert result.requires is None
    assert result.install_specs is None


def test_parse_skill_md_nonexistent_file_returns_none():
    """Passing a path that doesn't exist returns None gracefully."""
    result = Skill._parse_skill_md("/nonexistent/path/SKILL.md")
    assert result is None


# =============================================================================
# Skill write path tests
# =============================================================================

@pytest.mark.asyncio
async def test_create_skill_writes_to_plugins_path(tmp_path, monkeypatch):
    """POST /skills should write to ~/.flocks/plugins/skills/, not ~/.flocks/skills/."""
    from httpx import AsyncClient, ASGITransport
    from flocks.server.app import app

    # Redirect home directory to tmp_path so we don't pollute real ~/.flocks
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p.replace("~", str(tmp_path)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/skills", json={
            "name": "write-path-test",
            "description": "Testing write path is plugins/skills",
            "content": "# Test\n\nContent here.",
        })

    assert resp.status_code == 201
    data = resp.json()
    # Write path must be under plugins/skills/, NOT skills/
    assert "plugins/skills" in data["location"]
    assert "/skills/write-path-test/SKILL.md" in data["location"]
    # Canonical path check: must NOT be directly under .flocks/skills/
    assert "/.flocks/skills/" not in data["location"].replace("plugins/skills", "")


@pytest.mark.asyncio
async def test_create_skill_preserves_description_cn(tmp_path, monkeypatch):
    """POST /skills should persist and return description_cn when provided."""
    from httpx import AsyncClient, ASGITransport
    from flocks.server.app import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p.replace("~", str(tmp_path)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/skills", json={
            "name": "localized-create-test",
            "description": "English description",
            "description_cn": "中文描述",
            "content": "# Test\n\nContent here.",
        })

    assert resp.status_code == 201
    data = resp.json()
    assert data["description"] == "English description"
    assert data["description_cn"] == "中文描述"

    skill_path = tmp_path / ".flocks" / "plugins" / "skills" / "localized-create-test" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert "description: English description" in content
    assert "description_cn: 中文描述" in content


@pytest.mark.asyncio
async def test_update_skill_preserves_description_cn(tmp_path, monkeypatch):
    """PUT /skills/{name} should keep localized descriptions instead of dropping them."""
    from httpx import AsyncClient, ASGITransport
    from flocks.server.app import app

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path) if p == "~" else p.replace("~", str(tmp_path)))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        create_resp = await client.post("/api/skills", json={
            "name": "localized-update-test",
            "description": "Original English",
            "description_cn": "原始中文",
            "content": "# Original\n\nBody",
        })
        assert create_resp.status_code == 201

        update_resp = await client.put("/api/skills/localized-update-test", json={
            "name": "localized-update-test",
            "description": "Updated English",
            "description_cn": "更新后的中文",
            "content": "# Updated\n\nBody",
        })

    assert update_resp.status_code == 200
    data = update_resp.json()
    assert data["description"] == "Updated English"
    assert data["description_cn"] == "更新后的中文"

    skill_path = tmp_path / ".flocks" / "plugins" / "skills" / "localized-update-test" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert "description: Updated English" in content
    assert "description_cn: 更新后的中文" in content
