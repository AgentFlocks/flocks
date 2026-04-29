"""
BuiltinSkillAuthor: full action surface + safety rails.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from flocks.evolution import EvolutionEngine, SkillDraft
from flocks.evolution.manifest import AuthorManifest


_BODY = """Body content with steps.

## Steps
1. step a
2. step b
"""


@pytest.fixture
def author():
    engine = EvolutionEngine.get()
    engine.bootstrap({"enabled": True, "author": {"use": "builtin"}})
    return engine.author


@pytest.mark.asyncio
async def test_create_writes_skill_md_and_manifest(author):
    ref = await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    assert Path(ref.location).exists()
    text = Path(ref.location).read_text(encoding="utf-8")
    assert text.startswith("---")
    assert "alpha" in text
    assert AuthorManifest.get().is_authored("alpha")


@pytest.mark.asyncio
async def test_duplicate_create_raises(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    with pytest.raises(FileExistsError):
        await author.create(SkillDraft(name="alpha", description="d", content=_BODY))


@pytest.mark.asyncio
async def test_edit_full_rewrite_validates_frontmatter(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    with pytest.raises(ValueError):
        await author.edit("alpha", "no frontmatter at all")


@pytest.mark.asyncio
async def test_patch_requires_unique_match(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    # 'step' appears in 'step a' and 'step b' — should reject ambiguous patch
    with pytest.raises(ValueError):
        await author.patch("alpha", "step", "STEP")


@pytest.mark.asyncio
async def test_patch_unique_match_succeeds(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    await author.patch("alpha", "step a", "step ALPHA")
    text = Path(
        author._locate_skill_dir("alpha", "user") / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "step ALPHA" in text


@pytest.mark.asyncio
async def test_write_and_remove_supporting_file(author):
    ref = await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    await author.write_file("alpha", "references/notes.md", "# notes")
    target = Path(ref.skill_dir) / "references" / "notes.md"
    assert target.exists()
    assert (await author.remove_file("alpha", "references/notes.md")) is True
    assert not target.exists()


@pytest.mark.asyncio
async def test_supporting_file_subdir_whitelist_enforced(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    with pytest.raises(ValueError):
        await author.write_file("alpha", "evil/escape.txt", "bad")


@pytest.mark.asyncio
async def test_delete_refuses_skills_not_in_manifest(author):
    # Manually create a skill dir without manifest entry and ensure delete refuses.
    user_root = Path(os.environ["FLOCKS_ROOT"]) / "plugins" / "skills" / "external"
    user_root.mkdir(parents=True)
    (user_root / "SKILL.md").write_text(
        "---\nname: external\ndescription: x\n---\n\nbody\n", encoding="utf-8"
    )
    with pytest.raises(PermissionError):
        await author.delete("external")


@pytest.mark.asyncio
async def test_delete_tombstones_manifest(author):
    await author.create(SkillDraft(name="alpha", description="d", content=_BODY))
    assert AuthorManifest.get().is_authored("alpha")
    assert (await author.delete("alpha")) is True
    assert AuthorManifest.get().is_authored("alpha") is False
