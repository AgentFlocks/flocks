"""
Skill Tool - Load and execute skills

Loads skill files that provide specialized instructions for specific tasks.
Skills are markdown files with structured content.
Ported from original skill tool.
"""

import os
from typing import List

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.skill.skill import Skill, SkillInfo
from flocks.utils.log import Log


log = Log.create(service="tool.skill")


# Maximum characters of a skill's description shown in the `skill` tool's
# meta-description (the tool index that ships with the system prompt).
#
# Why a limit at all?
#   The `skill` tool's description is injected into every LLM call as part of
#   the tool schema. Listing the full SKILL.md frontmatter description (allowed
#   up to 1024 chars by `Skill._is_valid_description`) for every skill makes
#   the prompt grow linearly with the number of skills — and most of that text
#   is "how to use" detail that the model only needs *after* it decides to
#   load the skill.
#
# Why 500?
#   Empirically, the descriptions in `flocks/.flocks/plugins/skills/*/SKILL.md`
#   cluster between 60 and 614 characters; 500 chars preserves ~96% of the
#   total content (only one outlier needs trimming) while keeping the worst-
#   case cost of the index bounded. Critically, threat-intel/EDR skills tend
#   to put their hard constraints ("must load this skill before any X tool")
#   at the *end* of the description, so we keep both head and tail.
MAX_SKILL_DESCRIPTION_PREVIEW_CHARS = 500


def _truncate_skill_description(description: str, name: str) -> str:
    """
    Cap a single skill's description at MAX_SKILL_DESCRIPTION_PREVIEW_CHARS.

    Uses head + tail truncation so both the opening (scope/triggers) and the
    closing (hard constraints, "must load first") survive. Inserts a marker
    that tells the model how to fetch the full content via the `skill` tool.
    """
    if len(description) <= MAX_SKILL_DESCRIPTION_PREVIEW_CHARS:
        return description

    marker = f' … [truncated; load full SKILL.md via skill(name="{name}") before acting] … '
    available = MAX_SKILL_DESCRIPTION_PREVIEW_CHARS - len(marker)
    if available < 80:
        # Marker alone is unusually long (very long skill name); fall back to
        # plain head truncation so we still emit something useful.
        return description[: MAX_SKILL_DESCRIPTION_PREVIEW_CHARS - 1] + "…"

    head_size = (available * 3) // 5  # ~60% head
    tail_size = available - head_size
    return description[:head_size] + marker + description[-tail_size:]


def build_description(skills: List[SkillInfo]) -> str:
    """Build tool description with available skills.

    Each skill's description is capped at MAX_SKILL_DESCRIPTION_PREVIEW_CHARS
    (head + tail). The model is instructed to call `skill(name=...)` to
    obtain the full SKILL.md when it decides to act on a skill.
    """
    if not skills:
        return "Load a skill to get detailed instructions for a specific task. No skills are currently available."

    # Match Flocks's format: space-separated, no newlines
    parts = [
        "Load a skill to get detailed instructions for a specific task.",
        "Skills provide specialized knowledge and step-by-step guidance.",
        "Use this when a task matches an available skill's description.",
        # Strong, explicit guidance: the descriptions below are PREVIEWS only.
        # The model must call this tool to get the full SKILL.md before
        # actually executing the skill's workflow.
        (
            "IMPORTANT: each <description> below is a preview that may be "
            f"truncated to {MAX_SKILL_DESCRIPTION_PREVIEW_CHARS} chars. "
            "It is enough to decide WHETHER a skill applies, but NOT enough "
            "to execute it. Once you pick a skill, you MUST call "
            "skill(name=\"<skill-name>\") to load the full SKILL.md before "
            "running its steps or calling any tool the skill governs."
        ),
        "<available_skills>",
    ]

    for skill in skills:
        preview = _truncate_skill_description(skill.description, skill.name)
        parts.extend([
            "  <skill>",
            f"    <name>{skill.name}</name>",
            f"    <description>{preview}</description>",
            "  </skill>",
        ])

    parts.append("</available_skills>")

    # Join with space like Flocks does: .join(" ")
    return " ".join(parts)


async def skill_tool_impl(
    ctx: ToolContext,
    name: str,
) -> ToolResult:
    """
    Load a skill
    
    Args:
        ctx: Tool context
        name: Skill name to load
        
    Returns:
        ToolResult with skill content
    """
    if not name:
        return ToolResult(
            success=False,
            error="Skill name is required"
        )
    
    # Get skill
    skill = await Skill.get(name)
    
    if not skill:
        all_skills = await Skill.all()
        available = ", ".join(s.name for s in all_skills) or "none"
        return ToolResult(
            success=False,
            error=f'Skill "{name}" not found. Available skills: {available}'
        )
    
    # Request permission
    await ctx.ask(
        permission="skill",
        patterns=[name],
        always=[name],
        metadata={}
    )
    
    # Load skill content
    location = skill.location
    
    try:
        with open(location, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception as e:
        return ToolResult(
            success=False,
            error=f"Failed to load skill: {str(e)}"
        )
    
    # Get base directory
    skill_dir = os.path.dirname(location)

    # Format output
    output = f"""## Skill: {skill.name}

**Base directory**: {skill_dir}

{content.strip()}"""

    # ``truncated=True`` here is intentional: it tells ToolRegistry's
    # auto-truncate path (registry.py: "Auto-truncate output unless the tool
    # already handled it") to leave our payload alone. The `skill` tool is the
    # *load-on-demand* counterpart of the tiny preview that ships in the system
    # prompt -- if the model just decided to load this skill, it needs the
    # FULL SKILL.md to act on. Cropping it at 100 KB / 1000 lines (the
    # registry's defaults) silently drops the workflow steps, references, and
    # constraints that authors typically place at the *end* of the file, which
    # is the exact bug users were hitting (skill.md tail "感觉就完全丢失了").
    # Mirrors hermes-agent's `skill_view`, which also returns content in full.
    log.info("skill.load.full_content", {
        "name": skill.name,
        "bytes": len(output.encode("utf-8")),
        "lines": output.count("\n") + 1,
    })

    # Evolution L3: record this load as a use. NoOp tracker silently
    # swallows the call so this branch is safe even when evolution is
    # disabled. Tracker exceptions must NEVER affect skill loading —
    # the call is wrapped so a corrupted usage file can't break the agent.
    try:
        from flocks.evolution import EvolutionEngine
        scope = "project" if skill.source == "project" else "user"
        EvolutionEngine.get().tracker.bump_use(skill.name, scope=scope)
    except Exception as e:  # pragma: no cover - defensive
        log.debug("skill.tracker_bump_failed", {"name": skill.name, "error": str(e)})

    return ToolResult(
        success=True,
        output=output,
        title=f"Loaded skill: {skill.name}",
        truncated=True,
        metadata={
            "name": skill.name,
            "dir": skill_dir,
            "auto_truncate_bypassed": True,
        }
    )


async def get_all_skills() -> List[dict]:
    """
    Get all available skills as dictionaries
    
    Wrapper function for API routes compatibility.
    
    Returns:
        List of skill dictionaries with name, description, location
    """
    skills = await Skill.all()
    return [
        {
            "name": skill.name,
            "description": skill.description,
            "location": skill.location,
        }
        for skill in skills
    ]


async def get_skill(name: str) -> dict | None:
    """
    Get a specific skill by name as a dictionary
    
    Wrapper function for API routes compatibility.
    
    Args:
        name: Skill name to get
        
    Returns:
        Skill dictionary or None if not found
    """
    skill = await Skill.get(name)
    if not skill:
        return None
    
    # Also read the content
    try:
        with open(skill.location, 'r', encoding='utf-8') as f:
            content = f.read()
    except Exception:
        content = ""
    
    return {
        "name": skill.name,
        "description": skill.description,
        "location": skill.location,
        "content": content,
    }


# Register the tool (description will be updated dynamically on first call)
@ToolRegistry.register_function(
    name="skill",
    description="Load a skill to get detailed instructions for a specific task. Available skills are listed in the description.",
    category=ToolCategory.SYSTEM,
    parameters=[
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description="The skill identifier from available_skills",
            required=True
        ),
    ]
)
async def skill_tool(
    ctx: ToolContext,
    name: str,
) -> ToolResult:
    """Wrapper that updates description and calls implementation"""
    # Update tool description with available skills on first call
    tool = ToolRegistry.get("skill")
    if tool:
        skills = await Skill.all()
        tool.info.description = build_description(skills)
    
    return await skill_tool_impl(ctx, name)
