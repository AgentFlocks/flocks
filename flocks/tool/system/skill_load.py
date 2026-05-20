"""
Skill Tool - Load and execute skills.

The `skill_load` tool is the load-on-demand half of the skill system: keep the
tool schema short, and load the full SKILL.md only after the model has already
decided which skill applies.
"""

import os
from typing import List

from flocks.tool.registry import (
    ToolRegistry, ToolCategory, ToolParameter, ParameterType, ToolResult, ToolContext
)
from flocks.skill.skill import Skill, SkillInfo
from flocks.utils.log import Log


log = Log.create(service="tool.skill_load")


MAX_SKILL_DESCRIPTION_PREVIEW_CHARS = 500


SKILL_TOOL_DESCRIPTION = (
    "Load the full SKILL.md for one specific skill. "
    "Use this only after you have identified the correct skill name from the "
    "prompt's available-skills guidance or another discovery step. "
    "If a skills listing tool is available, use that first when unsure which "
    "skill applies. Once you know the name, you must call "
    "skill_load(name=\"<skill-name>\") before acting on the skill."
)


def _truncate_skill_description(description: str, name: str) -> str:
    """
    Backward-compatible helper kept for tests and any callers that still want a
    bounded skill preview outside the tool schema.

    Uses head + tail truncation so both the opening (scope/triggers) and the
    closing (hard constraints, "must load first") survive. Inserts a marker
    that tells the model how to fetch the full content via the `skill_load` tool.
    """
    max_chars = MAX_SKILL_DESCRIPTION_PREVIEW_CHARS
    if len(description) <= max_chars:
        return description

    marker = f' … [truncated; load full SKILL.md via skill_load(name="{name}") before acting] … '
    available = max_chars - len(marker)
    if available < 80:
        # Marker alone is unusually long (very long skill name); fall back to
        # plain head truncation so we still emit something useful.
        return description[: max_chars - 1] + "…"

    head_size = (available * 3) // 5  # ~60% head
    tail_size = available - head_size
    return description[:head_size] + marker + description[-tail_size:]


def build_description(skills: List[SkillInfo]) -> str:
    """Return the stable, token-light `skill_load` tool description."""
    _ = skills
    return SKILL_TOOL_DESCRIPTION


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
    # already handled it") to leave our payload alone. The `skill_load` tool is the
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


@ToolRegistry.register_function(
    name="skill_load",
    description=SKILL_TOOL_DESCRIPTION,
    category=ToolCategory.SYSTEM,
    native=True,
    parameters=[
        ToolParameter(
            name="name",
            type=ParameterType.STRING,
            description="The exact skill name to load",
            required=True
        ),
    ]
)
async def skill_tool(
    ctx: ToolContext,
    name: str,
) -> ToolResult:
    """Wrapper that keeps `skill_load` as a pure load-on-demand tool."""
    return await skill_tool_impl(ctx, name)
