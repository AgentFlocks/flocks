"""
L2 SkillAuthor - persist successful experience as reusable SKILL.md skills.

Mirrors the 6-action surface of hermes-agent's skill_manager_tool:
  create / edit / patch / delete / write_file / remove_file.

The default ``BuiltinSkillAuthor`` writes under
``~/.flocks/plugins/skills/<name>/`` (user scope) or
``<cwd>/.flocks/plugins/skills/<name>/`` (project scope). Every
``create()`` also appends a record to
``~/.flocks/data/evolution/authored.jsonl`` so L3 tracker and L4 curator
can distinguish agent-created skills from hub/installed ones.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from flocks.evolution.types import SkillDraft, SkillRef


class SkillAuthor(ABC):
    """Abstract base for L2 skill authoring strategies."""

    name: str = ""
    is_noop: bool = False

    @abstractmethod
    async def create(self, draft: SkillDraft) -> SkillRef:
        """Create a new SKILL.md and supporting directory layout."""
        ...

    @abstractmethod
    async def edit(self, name: str, content: str) -> SkillRef:
        """Replace the entire SKILL.md content (full rewrite)."""
        ...

    @abstractmethod
    async def patch(
        self,
        name: str,
        find: str,
        replace: str,
        file: str = "SKILL.md",
    ) -> SkillRef:
        """Targeted find-and-replace within SKILL.md or any supporting file."""
        ...

    @abstractmethod
    async def delete(self, name: str) -> bool:
        """Remove a user/agent-created skill entirely."""
        ...

    @abstractmethod
    async def write_file(self, name: str, rel_path: str, content: str) -> SkillRef:
        """Add or overwrite a supporting file (references/, templates/, scripts/, assets/)."""
        ...

    @abstractmethod
    async def remove_file(self, name: str, rel_path: str) -> bool:
        """Remove a supporting file from a user/agent-created skill."""
        ...


class NoOpAuthor(SkillAuthor):
    """Default no-op author; raises on any mutating call so misconfig is loud."""

    name = "_noop"
    is_noop = True

    async def create(self, draft: SkillDraft) -> SkillRef:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot create skills")

    async def edit(self, name: str, content: str) -> SkillRef:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot edit skills")

    async def patch(self, name: str, find: str, replace: str, file: str = "SKILL.md") -> SkillRef:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot patch skills")

    async def delete(self, name: str) -> bool:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot delete skills")

    async def write_file(self, name: str, rel_path: str, content: str) -> SkillRef:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot write skill files")

    async def remove_file(self, name: str, rel_path: str) -> bool:
        raise RuntimeError("evolution.author is disabled (NoOp); cannot remove skill files")
