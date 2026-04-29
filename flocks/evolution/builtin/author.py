"""
Built-in L2 SkillAuthor.

Implements the 6-action surface of hermes-agent's skill_manager_tool
adapted to Flocks' skill discovery layout (``~/.flocks/plugins/skills/``
for user scope, ``<cwd>/.flocks/plugins/skills/`` for project scope).

After every successful create/edit/patch/write_file/remove_file the
author:
  - Atomically writes the file via tempfile + os.replace.
  - Calls ``Skill.clear_cache()`` so the next discovery sees the change.
  - Records or invalidates an entry in ``AuthorManifest`` so L3 tracker
    and L4 curator know the skill is agent-managed.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from flocks.evolution.manifest import AuthorManifest
from flocks.evolution.strategies import SkillAuthor
from flocks.evolution.types import SkillDraft, SkillRef, SkillScope
from flocks.project.instance import Instance
from flocks.utils.log import Log

log = Log.create(service="evolution.author")


MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576

VALID_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_SUBDIRS = {"references", "templates", "scripts", "assets"}


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _user_skills_root() -> Path:
    raw = os.getenv("FLOCKS_ROOT")
    base = Path(raw) if raw else (Path.home() / ".flocks")
    return base / "plugins" / "skills"


def _project_skills_root() -> Path:
    project_dir = Instance.get_directory() or os.getcwd()
    return Path(project_dir) / ".flocks" / "plugins" / "skills"


def _scope_root(scope: SkillScope) -> Path:
    return _project_skills_root() if scope == "project" else _user_skills_root()


def _resolve_skill_dir(scope: SkillScope, name: str, category: Optional[str] = None) -> Path:
    root = _scope_root(scope)
    return root / category / name if category else root / name


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate_name(name: str) -> Optional[str]:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_NAME_LENGTH:
        return f"Skill name exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores; must start with a letter or digit."
        )
    return None


def _validate_category(category: Optional[str]) -> Optional[str]:
    if not category:
        return None
    if not isinstance(category, str):
        return "Category must be a string."
    category = category.strip()
    if not category:
        return None
    if "/" in category or "\\" in category:
        return f"Invalid category '{category}'. Categories must be a single directory name."
    if len(category) > MAX_NAME_LENGTH:
        return f"Category exceeds {MAX_NAME_LENGTH} characters."
    if not VALID_NAME_RE.match(category):
        return f"Invalid category '{category}'."
    return None


def _validate_frontmatter(content: str) -> Optional[str]:
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---"):
        return "SKILL.md must start with YAML frontmatter (---)."
    end_match = re.search(r"\n---\s*\n", content[3:])
    if not end_match:
        return "SKILL.md frontmatter is not closed; add a closing '---' line."
    yaml_body = content[3:end_match.start() + 3]
    try:
        parsed = yaml.safe_load(yaml_body)
    except yaml.YAMLError as exc:
        return f"YAML frontmatter parse error: {exc}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping."
    if "name" not in parsed:
        return "Frontmatter must include 'name'."
    if "description" not in parsed:
        return "Frontmatter must include 'description'."
    if len(str(parsed["description"])) > MAX_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_DESCRIPTION_LENGTH} characters."
    body = content[end_match.end() + 3:].strip()
    if not body:
        return "SKILL.md must have content after the frontmatter."
    return None


def _validate_content_size(content: str, label: str = "SKILL.md") -> Optional[str]:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,}); split into supporting files."
        )
    return None


def _validate_supporting_path(rel_path: str) -> Optional[str]:
    if not rel_path:
        return "rel_path is required."
    if ".." in rel_path.replace("\\", "/").split("/"):
        return "Path traversal ('..') is not allowed."
    parts = Path(rel_path).parts
    if not parts or parts[0] not in ALLOWED_SUBDIRS:
        return f"File must live under one of: {', '.join(sorted(ALLOWED_SUBDIRS))}."
    if len(parts) < 2:
        return "Provide a file path, not just a directory."
    return None


# ---------------------------------------------------------------------------
# Atomic IO
# ---------------------------------------------------------------------------


def _atomic_write_text(file_path: Path, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.tmp.",
        suffix="",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp_path, file_path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Frontmatter assembly
# ---------------------------------------------------------------------------


def _build_skill_md(draft: SkillDraft) -> str:
    """Render a SkillDraft into a full SKILL.md (frontmatter + body)."""
    fm = {
        "name": draft.name,
        "description": draft.description,
    }
    if draft.tags:
        fm["tags"] = list(draft.tags)
    if draft.category:
        fm["category"] = draft.category
    fm.update(draft.extra_frontmatter or {})

    yaml_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = draft.content.strip()
    return f"---\n{yaml_text}\n---\n\n{body}\n"


# ---------------------------------------------------------------------------
# BuiltinSkillAuthor
# ---------------------------------------------------------------------------


class BuiltinSkillAuthor(SkillAuthor):
    """Default L2 author. Writes under flocks' plugins/skills layout."""

    name = "builtin"
    is_noop = False

    # ------------------------------------------------------------------
    # Internal lookup (only operates on agent-created skills)
    # ------------------------------------------------------------------

    def _locate_skill_dir(self, name: str, scope: Optional[SkillScope] = None) -> Optional[Path]:
        """Return the existing on-disk skill dir (if any) for ``name``.

        Searches both scopes by default. The caller usually pins a scope
        via the manifest record so we don't accidentally edit a
        same-named skill across scopes.
        """
        scopes: list = [scope] if scope else ["project", "user"]
        for s in scopes:
            candidate = _scope_root(s) / name
            if (candidate / "SKILL.md").exists():
                return candidate
            # Also walk one level for category-nested skills
            root = _scope_root(s)
            if root.exists():
                for sub in root.iterdir():
                    if sub.is_dir():
                        nested = sub / name / "SKILL.md"
                        if nested.exists():
                            return nested.parent
        return None

    def _invalidate_skill_cache(self) -> None:
        try:
            from flocks.skill.skill import Skill
            Skill.clear_cache()
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("skill_cache.invalidate_failed", {"error": str(exc)})

    # ------------------------------------------------------------------
    # SkillAuthor protocol
    # ------------------------------------------------------------------

    async def create(self, draft: SkillDraft) -> SkillRef:
        for err in (
            _validate_name(draft.name),
            _validate_category(draft.category),
        ):
            if err:
                raise ValueError(err)

        rendered = _build_skill_md(draft)
        for err in (
            _validate_frontmatter(rendered),
            _validate_content_size(rendered),
        ):
            if err:
                raise ValueError(err)

        existing = self._locate_skill_dir(draft.name)
        if existing is not None:
            raise FileExistsError(f"Skill '{draft.name}' already exists at {existing}")

        skill_dir = _resolve_skill_dir(draft.scope, draft.name, draft.category)
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_md = skill_dir / "SKILL.md"
        _atomic_write_text(skill_md, rendered)

        AuthorManifest.get().append({
            "name": draft.name,
            "scope": draft.scope,
            "skill_dir": str(skill_dir),
            "category": draft.category,
            "tags": list(draft.tags),
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        self._invalidate_skill_cache()
        log.info("skill.created", {"name": draft.name, "scope": draft.scope, "dir": str(skill_dir)})

        return SkillRef(
            name=draft.name,
            scope=draft.scope,
            location=str(skill_md),
            skill_dir=str(skill_dir),
        )

    async def edit(self, name: str, content: str) -> SkillRef:
        for err in (
            _validate_frontmatter(content),
            _validate_content_size(content),
        ):
            if err:
                raise ValueError(err)

        record = AuthorManifest.get().get_record(name)
        scope: SkillScope = (record or {}).get("scope", "user")
        skill_dir = self._locate_skill_dir(name, scope)
        if skill_dir is None:
            raise FileNotFoundError(f"Skill '{name}' not found in agent-managed locations.")

        skill_md = skill_dir / "SKILL.md"
        _atomic_write_text(skill_md, content)
        self._invalidate_skill_cache()
        log.info("skill.edited", {"name": name, "dir": str(skill_dir)})

        return SkillRef(
            name=name,
            scope=scope,
            location=str(skill_md),
            skill_dir=str(skill_dir),
        )

    async def patch(self, name: str, find: str, replace: str, file: str = "SKILL.md") -> SkillRef:
        if not find:
            raise ValueError("'find' string is required for patch.")
        if replace is None:
            raise ValueError("'replace' is required for patch (use empty string to delete).")

        record = AuthorManifest.get().get_record(name)
        scope: SkillScope = (record or {}).get("scope", "user")
        skill_dir = self._locate_skill_dir(name, scope)
        if skill_dir is None:
            raise FileNotFoundError(f"Skill '{name}' not found.")

        if file == "SKILL.md":
            target = skill_dir / "SKILL.md"
        else:
            err = _validate_supporting_path(file)
            if err:
                raise ValueError(err)
            target = skill_dir / file
            if not target.resolve().is_relative_to(skill_dir.resolve()):
                raise ValueError(f"Patch target {file} escapes the skill directory.")

        if not target.exists():
            raise FileNotFoundError(f"File not found: {target.relative_to(skill_dir)}")

        original = target.read_text(encoding="utf-8")
        count = original.count(find)
        if count == 0:
            raise ValueError(f"'find' string not present in {target.relative_to(skill_dir)}.")
        if count > 1:
            raise ValueError(
                f"'find' string matches {count} times in {target.relative_to(skill_dir)}; "
                "make it unique or split into multiple patches."
            )
        new_content = original.replace(find, replace, 1)

        err = _validate_content_size(new_content, label=str(target.relative_to(skill_dir)))
        if err:
            raise ValueError(err)
        if file == "SKILL.md":
            err = _validate_frontmatter(new_content)
            if err:
                raise ValueError(f"Patch would break SKILL.md structure: {err}")

        _atomic_write_text(target, new_content)
        self._invalidate_skill_cache()
        log.info("skill.patched", {"name": name, "file": file})

        return SkillRef(
            name=name,
            scope=scope,
            location=str(skill_dir / "SKILL.md"),
            skill_dir=str(skill_dir),
        )

    async def delete(self, name: str) -> bool:
        record = AuthorManifest.get().get_record(name)
        if record is None:
            raise PermissionError(
                f"Refusing to delete '{name}': not in agent-author manifest "
                "(only agent-created skills can be deleted via this API)."
            )
        scope: SkillScope = record.get("scope", "user")
        skill_dir = self._locate_skill_dir(name, scope)
        if skill_dir is None:
            # Already gone on disk; just append a tombstone for consistency.
            AuthorManifest.get().forget(name, scope=scope)
            self._invalidate_skill_cache()
            return False

        shutil.rmtree(skill_dir, ignore_errors=False)
        AuthorManifest.get().forget(name, scope=scope)
        self._invalidate_skill_cache()
        log.info("skill.deleted", {"name": name, "dir": str(skill_dir)})
        return True

    async def write_file(self, name: str, rel_path: str, content: str) -> SkillRef:
        err = _validate_supporting_path(rel_path)
        if err:
            raise ValueError(err)
        if len(content.encode("utf-8")) > MAX_SKILL_FILE_BYTES:
            raise ValueError(
                f"File exceeds {MAX_SKILL_FILE_BYTES // 1024} KiB limit."
            )

        record = AuthorManifest.get().get_record(name)
        scope: SkillScope = (record or {}).get("scope", "user")
        skill_dir = self._locate_skill_dir(name, scope)
        if skill_dir is None:
            raise FileNotFoundError(f"Skill '{name}' not found.")

        target = skill_dir / rel_path
        if not target.resolve().is_relative_to(skill_dir.resolve()):
            raise ValueError(f"{rel_path} escapes the skill directory.")
        _atomic_write_text(target, content)
        self._invalidate_skill_cache()
        log.info("skill.file_written", {"name": name, "rel": rel_path})

        return SkillRef(
            name=name,
            scope=scope,
            location=str(skill_dir / "SKILL.md"),
            skill_dir=str(skill_dir),
        )

    async def remove_file(self, name: str, rel_path: str) -> bool:
        err = _validate_supporting_path(rel_path)
        if err:
            raise ValueError(err)

        record = AuthorManifest.get().get_record(name)
        scope: SkillScope = (record or {}).get("scope", "user")
        skill_dir = self._locate_skill_dir(name, scope)
        if skill_dir is None:
            raise FileNotFoundError(f"Skill '{name}' not found.")

        target = skill_dir / rel_path
        if not target.resolve().is_relative_to(skill_dir.resolve()):
            raise ValueError(f"{rel_path} escapes the skill directory.")
        if not target.exists():
            return False
        target.unlink()
        self._invalidate_skill_cache()
        log.info("skill.file_removed", {"name": name, "rel": rel_path})
        return True


__all__ = ["BuiltinSkillAuthor"]
