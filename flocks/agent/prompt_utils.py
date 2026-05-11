"""
Prompt builder utilities for dynamic agent prompts.

These functions construct sections of the delegation-aware system prompts used
by Rex, Hephaestus, and similar orchestrator agents.  Each function takes typed
context objects from :mod:`flocks.agent.agent` and returns a Markdown string.

Previously located at flocks.agent.prompts.builder.dynamic.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from flocks.agent.agent import AvailableAgent, AvailableCategory, AvailableSkill, AvailableTool, AvailableWorkflow


# ---------------------------------------------------------------------------
# Tool categorisation helper
# ---------------------------------------------------------------------------

def categorize_tools(tool_names: List[str]) -> List[AvailableTool]:
    """Build AvailableTool list using real ToolRegistry categories.

    Falls back to name-based heuristics when ToolRegistry is unavailable
    (e.g. during unit tests that don't initialise the registry).
    """
    try:
        from flocks.tool.registry import ToolRegistry
        ToolRegistry.init()
        tools: List[AvailableTool] = []
        for name in tool_names:
            tool_entry = ToolRegistry.get(name)
            category = tool_entry.info.category.value if tool_entry else "system"
            tools.append(AvailableTool(name=name, category=category))
        return tools
    except Exception:
        # Fallback: minimal heuristic so prompt-building never crashes
        return [AvailableTool(name=name, category="system") for name in tool_names]


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

# Human-readable labels for ToolCategory values shown in the prompt
_CATEGORY_LABELS: Dict[str, str] = {
    "file":     "File",
    "code":     "Code / Shell",
    "search":   "Search",
    "browser":  "Browser",
    "terminal": "Terminal",
    "system":   "System / Agent",
    "custom":   "Custom / Plugin",
}

# Display order for categories in the prompt
_CATEGORY_ORDER = ["file", "code", "search", "browser", "terminal", "system", "custom"]


def _format_tools_for_prompt(tools: List[AvailableTool]) -> str:
    """Group tools by ToolCategory and render all of them.

    Returns a compact multi-line block where every registered tool is visible,
    grouped under a human-readable category label.
    """
    # Group by category, preserving insertion order within each group
    groups: Dict[str, List[str]] = {}
    for t in tools:
        groups.setdefault(t.category, []).append(t.name)

    if not groups:
        return ""

    lines: List[str] = []
    # Emit ordered categories first, then any remaining ones
    seen: set = set()
    for cat in _CATEGORY_ORDER:
        if cat in groups:
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            names = ", ".join(f"`{n}`" for n in groups[cat])
            lines.append(f"**{label}**: {names}")
            seen.add(cat)
    for cat, names_list in groups.items():
        if cat not in seen:
            label = _CATEGORY_LABELS.get(cat, cat.capitalize())
            names = ", ".join(f"`{n}`" for n in names_list)
            lines.append(f"**{label}**: {names}")

    return "\n".join(lines)


def build_key_triggers_section(
    agents: List[AvailableAgent],
    _skills: Optional[List[AvailableSkill]] = None,
) -> str:
    key_triggers = [f"- {a.metadata.key_trigger}" for a in agents if a.metadata.key_trigger]
    if not key_triggers:
        return ""
    return (
        "### Key Triggers (check BEFORE classification):\n\n"
        + "\n".join(key_triggers)
        + '\n- **"Look into" + "create PR"** → Not just research. Full implementation cycle expected.'
    )


def build_tool_selection_table(
    _agents: List[AvailableAgent],
    tools: Optional[List[AvailableTool]] = None,
    _skills: Optional[List[AvailableSkill]] = None,
) -> str:
    tools = tools or []
    rows: List[str] = ["### Available Tools:"]

    if tools:
        tools_block = _format_tools_for_prompt(tools)
        if tools_block:
            rows += ["", tools_block]
    return "\n".join(rows)


def build_agent_selection_table(agents: List[AvailableAgent]) -> str:
    cost_order = {"FREE": 0, "CHEAP": 1, "EXPENSIVE": 2}
    sorted_agents = [a for a in agents if a.metadata.category != "utility"]
    sorted_agents.sort(key=lambda a: cost_order.get(a.metadata.cost, 99))

    rows: List[str] = ["### Available Agents:"]
    if sorted_agents:
        rows += [
            "",
            "| Agent | Cost | When to Use | Trigger Signals |",
            "|-------|------|-------------|-----------------|",
        ]
        for agent in sorted_agents:
            short_desc = agent.description.split(".")[0] or agent.description
            trigger_text = ", ".join(t.trigger for t in (agent.metadata.triggers or [])[:2])
            if not trigger_text and agent.metadata.use_when:
                trigger_text = ", ".join(agent.metadata.use_when[:2])
            trigger_text = trigger_text or "-"
            rows.append(
                f"| `{agent.name}` | {agent.metadata.cost} | {short_desc} | {trigger_text} |"
            )

    rows.append("")
    rows.append("**Default flow**: direct tools first → `explore` / `librarian` for research → `oracle` if required")
    return "\n".join(rows)


def build_explore_section(agents: List[AvailableAgent]) -> str:
    explore_agent = next((a for a in agents if a.name == "explore"), None)
    if not explore_agent:
        return ""
    use_when = explore_agent.metadata.use_when or []
    avoid_when = explore_agent.metadata.avoid_when or []
    left = [f"| {w} |  |" for w in avoid_when]
    right = [f"|  | {w} |" for w in use_when]
    return (
        "### Explore Agent = Contextual Grep\n\n"
        "Use it as a **peer tool**, not a fallback. Fire liberally.\n\n"
        "| Use Direct Tools | Use Explore Agent |\n"
        "|------------------|-------------------|\n"
        + "\n".join(left + right)
    )


def build_librarian_section(agents: List[AvailableAgent]) -> str:
    librarian_agent = next((a for a in agents if a.name == "librarian"), None)
    if not librarian_agent:
        return ""
    use_when = librarian_agent.metadata.use_when or []
    triggers = "\n".join([f'- "{w}"' for w in use_when])
    return (
        "### Librarian Agent = Reference Grep\n\n"
        "Search **external references** (docs, OSS, web). Fire proactively when unfamiliar libraries are involved.\n\n"
        "| Contextual Grep (Internal) | Reference Grep (External) |\n"
        "|----------------------------|---------------------------|\n"
        "| Search OUR codebase | Search EXTERNAL resources |\n"
        "| Find patterns in THIS repo | Find examples in OTHER repos |\n"
        "| How does our code work? | How does this library work? |\n"
        "| Project-specific logic | Official API documentation |\n"
        "| | Library best practices & quirks |\n"
        "| | OSS implementation examples |\n\n"
        "**Trigger phrases** (fire librarian immediately):\n"
        + triggers
    )


def build_delegation_table(agents: List[AvailableAgent]) -> str:
    rows: List[str] = [
        "### Delegation Table:",
        "",
        "| Domain | Delegate To | Trigger |",
        "|--------|-------------|---------|",
    ]
    for agent in agents:
        for trigger in agent.metadata.triggers:
            rows.append(f"| {trigger.domain} | `{agent.name}` | {trigger.trigger} |")
    return "\n".join(rows)


def build_category_skills_delegation_guide(
    categories: List[AvailableCategory],
    skills: List[AvailableSkill],
) -> str:
    if not categories and not skills:
        return ""

    category_rows = [f"| `{c.name}` | {c.description or c.name} |" for c in categories]
    skill_rows = [
        f"| `{s.name}` | {s.description.split('.')[0] or s.description} |"
        for s in skills
    ]

    return (
        "### Category + Skills Delegation System\n\n"
        "**delegate_task() combines categories and skills for optimal task execution.**\n\n"
        "#### Available Categories (Domain-Optimized Models)\n\n"
        "Each category is configured with a model optimized for that domain. Read the description to understand when to use it.\n\n"
        "| Category | Domain / Best For |\n"
        "|----------|-------------------|\n"
        + "\n".join(category_rows)
        + "\n\n#### Available Skills (Domain Expertise Injection)\n\n"
        "Skills inject specialized instructions into the subagent. Read the description to understand when each skill applies.\n\n"
        "| Skill | Expertise Domain |\n"
        "|-------|------------------|\n"
        + "\n".join(skill_rows)
        + "\n\n---\n\n"
        "### MANDATORY: Category + Skill Selection Protocol\n\n"
        "**STEP 1: Select Category**\n"
        "- Read each category's description\n"
        "- Match task requirements to category domain\n"
        "- Select the category whose domain BEST fits the task\n\n"
        "**STEP 2: Evaluate ALL Skills**\n"
        "For EVERY skill listed above, ask yourself:\n"
        '> "Does this skill\'s expertise domain overlap with my task?"\n\n'
        "- If YES → INCLUDE in `load_skills=[...]`\n"
        "- If NO → You MUST justify why (see below)\n\n"
        "**STEP 3: Justify Omissions**\n\n"
        "If you choose NOT to include a skill that MIGHT be relevant, you MUST provide:\n\n"
        "```\n"
        'SKILL EVALUATION for "[skill-name]":\n'
        "- Skill domain: [what the skill description says]\n"
        "- Task domain: [what your task is about]\n"
        "- Decision: OMIT\n"
        "- Reason: [specific explanation of why domains don't overlap]\n"
        "```\n\n"
        "**WHY JUSTIFICATION IS MANDATORY:**\n"
        "- Forces you to actually READ skill descriptions\n"
        "- Prevents lazy omission of potentially useful skills\n"
        "- Subagents are STATELESS - they only know what you tell them\n"
        "- Missing a relevant skill = suboptimal output\n\n"
        "---\n\n"
        "### Delegation Pattern\n\n"
        "```typescript\n"
        "delegate_task(\n"
        '  category="[selected-category]",\n'
        '  load_skills=["skill-1", "skill-2"],  // Include ALL relevant skills\n'
        '  prompt="..."\n'
        ")\n"
        "```\n\n"
        "**ANTI-PATTERN (will produce poor results):**\n"
        "```typescript\n"
        'delegate_task(category="...", load_skills=[], run_in_background=false, prompt="...")  // Empty load_skills without justification\n'
        "```"
    )


def build_oracle_section(agents: List[AvailableAgent]) -> str:
    oracle_agent = next((a for a in agents if a.name == "oracle"), None)
    if not oracle_agent:
        return ""
    use_when = oracle_agent.metadata.use_when or []
    avoid_when = oracle_agent.metadata.avoid_when or []

    return (
        "<Oracle_Usage>\n"
        "## Oracle — Read-Only High-IQ Consultant\n\n"
        "Oracle is a read-only, expensive, high-quality reasoning model for debugging and architecture. Consultation only.\n\n"
        "### WHEN to Consult:\n\n"
        "| Trigger | Action |\n"
        "|---------|--------|\n"
        + "\n".join([f"| {w} | Oracle FIRST, then implement |" for w in use_when])
        + "\n\n### WHEN NOT to Consult:\n\n"
        + "\n".join([f"- {w}" for w in avoid_when])
        + "\n\n### Usage Pattern:\n"
        'Briefly announce "Consulting Oracle for [reason]" before invocation.\n\n'
        "**Exception**: This is the ONLY case where you announce before acting. For all other work, start immediately without status updates.\n"
        "</Oracle_Usage>"
    )


def build_hard_blocks_section() -> str:
    blocks = [
        "| Type error suppression (`as any`, `@ts-ignore`) | Never |",
        "| Commit without explicit request | Never |",
        "| Speculate about unread code | Never |",
        "| Leave code in broken state after failures | Never |",
    ]
    return (
        "## Hard Blocks (NEVER violate)\n\n"
        "| Constraint | No Exceptions |\n"
        "|------------|---------------|\n"
        + "\n".join(blocks)
    )


def build_anti_patterns_section() -> str:
    patterns = [
        "| **Type Safety** | `as any`, `@ts-ignore`, `@ts-expect-error` |",
        "| **Error Handling** | Empty catch blocks `catch(e) {}` |",
        "| **Testing** | Deleting failing tests to \"pass\" |",
        "| **Search** | Firing agents for single-line typos or obvious syntax errors |",
        "| **Debugging** | Shotgun debugging, random changes |",
    ]
    return (
        "## Anti-Patterns (BLOCKING violations)\n\n"
        "| Category | Forbidden |\n"
        "|----------|-----------|\n"
        + "\n".join(patterns)
    )


def build_ultrawork_section(
    agents: List[AvailableAgent],
    categories: List[AvailableCategory],
    skills: List[AvailableSkill],
) -> str:
    lines: List[str] = []

    if categories:
        lines.append("**Categories** (for implementation tasks):")
        for cat in categories:
            short_desc = cat.description or cat.name
            lines.append(f"- `{cat.name}`: {short_desc}")
        lines.append("")

    if skills:
        lines.append("**Skills** (combine with categories - EVALUATE ALL for relevance):")
        for skill in skills:
            short_desc = skill.description.split(".")[0] or skill.description
            lines.append(f"- `{skill.name}`: {short_desc}")
        lines.append("")

    if agents:
        ultrawork_agent_priority = ["explore", "librarian", "plan", "oracle"]
        sorted_agents = list(agents)
        sorted_agents.sort(
            key=lambda a: ultrawork_agent_priority.index(a.name)
            if a.name in ultrawork_agent_priority
            else 999
        )
        lines.append("**Agents** (for specialized consultation/exploration):")
        for agent in sorted_agents:
            short_desc = agent.description.split(".")[0] or agent.description
            suffix = " (multiple)" if agent.name in ("explore", "librarian") else ""
            lines.append(f"- `{agent.name}{suffix}`: {short_desc}")

    return "\n".join(lines)


def build_workflows_section(workflows: List[AvailableWorkflow]) -> str:
    """Render the available workflows section for injection into system prompts.

    Mirrors the pattern used by build_category_skills_delegation_guide() for
    skills, so agents know which workflows exist before calling run_workflow.
    """
    if not workflows:
        return ""

    project_wfs = [w for w in workflows if w.source == "project"]
    global_wfs = [w for w in workflows if w.source != "project"]

    rows: List[str] = [
        "### Available Workflows",
        "",
        "| Workflow | Description | Path | Scope |",
        "|----------|-------------|------|-------|",
    ]
    for w in project_wfs:
        short_desc = w.description.split("\n")[0] if w.description else ""
        rows.append(f"| `{w.name}` | {short_desc} | `{w.path}` | project |")
    for w in global_wfs:
        short_desc = w.description.split("\n")[0] if w.description else ""
        rows.append(f"| `{w.name}` | {short_desc} | `{w.path}` | global |")

    rows += [
        "",
        '**Usage**: `run_workflow(workflow="<path>", inputs={...})`',
    ]
    return "\n".join(rows)
