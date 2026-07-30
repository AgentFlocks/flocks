"""Skill write guards shared by the self-improve Agent and file tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from flocks.memory.paths import path_is_within
from flocks.skill.skill import Skill


EVOLUTION_MANAGED_BY = "flocks"
SELF_IMPROVE_AGENT = "self-improve"


def user_skill_root() -> Path:
    """Return the only Skill root writable by self-improvement."""
    return Path.home() / ".flocks" / "plugins" / "skills"


def is_evolution_managed(content: str) -> bool:
    """Return whether a Skill opts into Flocks self-improvement."""
    data = Skill._parse_frontmatter(content)
    metadata = data.get("metadata")
    return bool(isinstance(metadata, dict) and metadata.get("managed_by") == EVOLUTION_MANAGED_BY)


def validate_skill_document(
    path: Path,
    content: str,
    *,
    root: Optional[Path] = None,
) -> Optional[str]:
    """Return an error when a self-improve-authored SKILL.md is invalid."""
    resolved_root = (root or user_skill_root()).resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    if not path_is_within(resolved_root, resolved_path):
        return f"Skill path is outside the self-improve user root: {path}"
    relative = resolved_path.relative_to(resolved_root)
    if len(relative.parts) != 2 or relative.name != "SKILL.md":
        return "Self-improve may write only <skill-name>/SKILL.md"

    data = Skill._parse_frontmatter(content)
    name = str(data.get("name") or "").strip()
    description = str(data.get("description") or "").strip()
    if not Skill._is_valid_name(name):
        return f"Invalid Skill name: {name!r}"
    if name != relative.parent.name:
        return "Skill frontmatter name must match its directory name"
    if not Skill._is_valid_description(description):
        return "Skill description must contain 1 to 1024 characters"
    if not is_evolution_managed(content):
        return "Self-improved Skills require metadata.managed_by: flocks"
    return None


async def validate_evolution_skill_write(
    path: Path,
    content: str,
    *,
    exists: bool,
) -> Optional[str]:
    """Enforce creation-only writes and prevent Skill name shadowing."""
    error = validate_skill_document(path, content)
    if error:
        return error
    if exists:
        return "Read the existing managed Skill and use edit instead of write"

    data = Skill._parse_frontmatter(content)
    name = str(data.get("name") or "").strip()
    if any(skill.name == name for skill in await Skill.all()):
        return f"Skill name already exists and cannot be shadowed: {name}"
    return None


def validate_evolution_skill_edit(
    path: Path,
    old_content: str,
    new_content: str,
) -> Optional[str]:
    """Allow edits only for existing self-improvement-managed Skills."""
    if not is_evolution_managed(old_content):
        return "Self-improve may edit only existing managed Skills"
    return validate_skill_document(path, new_content)


def skill_contents(root: Path) -> dict[str, bytes]:
    """Snapshot user SKILL.md files for post-run validation."""
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes() for path in sorted(root.glob("*/SKILL.md")) if path.is_file()
    }


def _restore_skill_contents(root: Path, before: dict[str, bytes]) -> None:
    after = skill_contents(root)
    for relative_path in after.keys() - before.keys():
        path = root / relative_path
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass
    for relative_path, content in before.items():
        path = root / relative_path
        if after.get(relative_path) != content:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def validate_skill_changes(
    root: Path,
    before: dict[str, bytes],
) -> bool:
    """Validate one managed Skill mutation or restore the pre-run state."""
    after = skill_contents(root)
    changed_paths = {path for path in before.keys() | after.keys() if before.get(path) != after.get(path)}
    if not changed_paths:
        return False

    error: Optional[str] = None
    if len(changed_paths) > 1:
        error = "Self-improve may create or update at most one Skill per run"
    else:
        relative_path = next(iter(changed_paths))
        new_content = after.get(relative_path)
        if new_content is None:
            error = "Self-improve may not delete Skills"
        else:
            try:
                decoded = new_content.decode("utf-8")
            except UnicodeDecodeError:
                error = "SKILL.md must be valid UTF-8"
            else:
                error = validate_skill_document(
                    root / relative_path,
                    decoded,
                    root=root,
                )
                old_content = before.get(relative_path)
                if (
                    error is None
                    and old_content is not None
                    and not is_evolution_managed(old_content.decode("utf-8", errors="replace"))
                ):
                    error = "Self-improve modified a Skill that is not Evolution-managed"
    if error:
        _restore_skill_contents(root, before)
        raise RuntimeError(error)
    return True


async def skill_catalog() -> list[dict[str, str]]:
    """Return compact discovery metadata for all available Skills."""
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "source": str(skill.source or ""),
            "managed_by": (skill.metadata.managed_by or "" if skill.metadata is not None else ""),
        }
        for skill in await Skill.all()
    ]


def serialize_skill_catalog(
    catalog: list[dict[str, str]],
    max_chars: int,
) -> str:
    """Serialize as many complete Skill entries as fit in the budget."""
    if max_chars < 2:
        return "[]"

    serialized_items = [json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in catalog]
    selected: list[str] = []
    used_chars = 2
    for item in serialized_items:
        item_chars = len(item) + (1 if selected else 0)
        if used_chars + item_chars > max_chars:
            continue
        selected.append(item)
        used_chars += item_chars
    return f"[{','.join(selected)}]"


def invalidate_skill_caches() -> None:
    """Make self-improved Skills visible to future Sessions."""
    Skill.clear_cache()
    from flocks.agent.registry import Agent

    Agent.invalidate_cache()
