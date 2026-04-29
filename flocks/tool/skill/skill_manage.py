"""
skill_manage tool - L2 evolution skill author surface for the agent.

A single multi-action tool that lets Rex (or any primary agent) persist
successful experience as reusable skills. Routes every action through
``EvolutionEngine.author`` so the actual write strategy is pluggable —
the bundled implementation writes to ``~/.flocks/plugins/skills/``;
plugins can substitute a remote registry, encrypted storage, etc.

Actions
-------
- ``create``       Create a new skill (name + description + body content).
- ``edit``         Replace an existing skill's full SKILL.md.
- ``patch``        Targeted find/replace within SKILL.md or a supporting file.
- ``delete``       Remove a previously agent-created skill.
- ``write_file``   Add or overwrite a supporting file under
                   references/ templates/ scripts/ assets/.
- ``remove_file``  Remove a supporting file.

Permission
----------
Gated by ``permission.skill_manage`` (defaults to "ask"). The action
patterns let users allowlist non-destructive ops (``create``,
``write_file``) while keeping destructive ops on prompt.
"""

from __future__ import annotations

from typing import Optional

from flocks.evolution import EvolutionEngine, SkillDraft
from flocks.tool.registry import (
    ParameterType,
    ToolCategory,
    ToolContext,
    ToolParameter,
    ToolRegistry,
    ToolResult,
)
from flocks.utils.log import Log

log = Log.create(service="tool.skill_manage")


_VALID_ACTIONS = {"create", "edit", "patch", "delete", "write_file", "remove_file"}


_DESCRIPTION = """\
Persist successful experience as reusable skills (procedural memory).
Use after solving a non-trivial task that the user is likely to ask again.

Available actions:
  create       Create a new skill from a name + description + step-by-step body.
  edit         Replace an existing agent-created skill's full SKILL.md.
  patch        Targeted find/replace inside SKILL.md (or a supporting file via 'file').
  delete       Remove a previously agent-created skill.
  write_file   Add a supporting file under references/, templates/, scripts/, or assets/.
  remove_file  Remove such a supporting file.

Required parameters per action:
  create       name, description, content [, scope, category, tags]
  edit         name, content
  patch        name, find, replace [, file]
  delete       name
  write_file   name, rel_path, content
  remove_file  name, rel_path

Skills go to ~/.flocks/plugins/skills/<name>/SKILL.md (scope='user', the
default) or <project>/.flocks/plugins/skills/<name>/ (scope='project').
Only skills created here can be later edited / deleted via this tool;
hub-installed and bundled skills are immutable.
"""


def _ensure_author_enabled() -> Optional[ToolResult]:
    engine = EvolutionEngine.get()
    if engine.author.is_noop:
        return ToolResult(
            success=False,
            error=(
                "skill_manage requires evolution.author to be enabled. "
                "Set evolution.enabled=true and evolution.author.use=\"builtin\" "
                "(or another author plugin) in flocks.json."
            ),
        )
    return None


@ToolRegistry.register_function(
    name="skill_manage",
    description=_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="action",
            type=ParameterType.STRING,
            description=f"One of: {', '.join(sorted(_VALID_ACTIONS))}",
            required=True,
        ),
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description="Skill name (lowercase, hyphen-separated). Required by all actions.",
            required=True,
        ),
        ToolParameter(
            name="description",
            type=ParameterType.STRING,
            description="Frontmatter description (1-1024 chars). Required for create.",
            required=False,
        ),
        ToolParameter(
            name="content",
            type=ParameterType.STRING,
            description=(
                "SKILL.md body for 'create' (without frontmatter), full SKILL.md "
                "for 'edit', or new file content for 'write_file'."
            ),
            required=False,
        ),
        ToolParameter(
            name="scope",
            type=ParameterType.STRING,
            description="'user' (~/.flocks) or 'project' (<cwd>/.flocks). Default: user. Used by 'create' only.",
            required=False,
        ),
        ToolParameter(
            name="category",
            type=ParameterType.STRING,
            description="Optional category subdirectory for nested skills (create only).",
            required=False,
        ),
        ToolParameter(
            name="tags",
            type=ParameterType.ARRAY,
            description="Optional tags for discoverability (create only).",
            required=False,
        ),
        ToolParameter(
            name="find",
            type=ParameterType.STRING,
            description="String to locate within the target file (patch action). Must match exactly once.",
            required=False,
        ),
        ToolParameter(
            name="replace",
            type=ParameterType.STRING,
            description="Replacement string for 'patch' (use empty string to delete the matched range).",
            required=False,
        ),
        ToolParameter(
            name="file",
            type=ParameterType.STRING,
            description="Target file relative to the skill dir (patch action). Defaults to 'SKILL.md'.",
            required=False,
        ),
        ToolParameter(
            name="rel_path",
            type=ParameterType.STRING,
            description=(
                "Path under references/ templates/ scripts/ assets/ for "
                "write_file / remove_file. E.g. 'references/example.md'."
            ),
            required=False,
        ),
    ],
)
async def skill_manage(
    ctx: ToolContext,
    action: str,
    name: str,
    description: Optional[str] = None,
    content: Optional[str] = None,
    scope: Optional[str] = None,
    category: Optional[str] = None,
    tags: Optional[list] = None,
    find: Optional[str] = None,
    replace: Optional[str] = None,
    file: Optional[str] = None,
    rel_path: Optional[str] = None,
) -> ToolResult:
    """Multi-action skill author surface backed by EvolutionEngine.author."""
    if action not in _VALID_ACTIONS:
        return ToolResult(
            success=False,
            error=f"Unknown action '{action}'. Valid: {', '.join(sorted(_VALID_ACTIONS))}",
        )

    not_ready = _ensure_author_enabled()
    if not_ready is not None:
        return not_ready

    await ctx.ask(
        permission="skill_manage",
        patterns=[action, name],
        always=[],
        metadata={"action": action, "name": name},
    )

    author = EvolutionEngine.get().author

    try:
        if action == "create":
            if not description or content is None:
                return ToolResult(
                    success=False,
                    error="'description' and 'content' are required for 'create'.",
                )
            draft = SkillDraft(
                name=name,
                description=description,
                content=content,
                scope=scope or "user",  # type: ignore[arg-type]
                category=category,
                tags=tags or [],
            )
            ref = await author.create(draft)
            return ToolResult(
                success=True,
                output=f"Created skill '{ref.name}' at {ref.location}",
                title=f"create: {ref.name}",
                metadata={
                    "name": ref.name,
                    "scope": ref.scope,
                    "skill_dir": ref.skill_dir,
                    "location": ref.location,
                },
            )

        if action == "edit":
            if content is None:
                return ToolResult(success=False, error="'content' is required for 'edit'.")
            ref = await author.edit(name, content)
            return ToolResult(
                success=True,
                output=f"Updated skill '{ref.name}' at {ref.location}",
                title=f"edit: {ref.name}",
                metadata={"name": ref.name, "scope": ref.scope, "skill_dir": ref.skill_dir},
            )

        if action == "patch":
            if find is None or replace is None:
                return ToolResult(
                    success=False,
                    error="'find' and 'replace' are required for 'patch' (use empty replace to delete).",
                )
            ref = await author.patch(name, find, replace, file=file or "SKILL.md")
            return ToolResult(
                success=True,
                output=f"Patched {file or 'SKILL.md'} in skill '{ref.name}'",
                title=f"patch: {ref.name}",
                metadata={"name": ref.name, "scope": ref.scope, "skill_dir": ref.skill_dir, "file": file or "SKILL.md"},
            )

        if action == "delete":
            ok = await author.delete(name)
            return ToolResult(
                success=ok,
                output=(f"Deleted skill '{name}'." if ok else f"Skill '{name}' was already absent on disk; manifest tombstoned."),
                title=f"delete: {name}",
                metadata={"name": name, "deleted": ok},
            )

        if action == "write_file":
            if not rel_path or content is None:
                return ToolResult(
                    success=False,
                    error="'rel_path' and 'content' are required for 'write_file'.",
                )
            ref = await author.write_file(name, rel_path, content)
            return ToolResult(
                success=True,
                output=f"Wrote {rel_path} in skill '{ref.name}'",
                title=f"write_file: {ref.name}",
                metadata={"name": ref.name, "scope": ref.scope, "skill_dir": ref.skill_dir, "rel_path": rel_path},
            )

        # remove_file
        if not rel_path:
            return ToolResult(success=False, error="'rel_path' is required for 'remove_file'.")
        ok = await author.remove_file(name, rel_path)
        return ToolResult(
            success=ok,
            output=(
                f"Removed {rel_path} from skill '{name}'."
                if ok
                else f"{rel_path} not found in skill '{name}'."
            ),
            title=f"remove_file: {name}",
            metadata={"name": name, "rel_path": rel_path, "removed": ok},
        )

    except (ValueError, FileNotFoundError, FileExistsError, PermissionError) as exc:
        return ToolResult(success=False, error=str(exc), title=f"{action}: {name}")
    except Exception as exc:
        log.error("skill_manage.error", {"action": action, "name": name, "error": str(exc)})
        return ToolResult(success=False, error=f"{type(exc).__name__}: {exc}", title=f"{action}: {name}")
